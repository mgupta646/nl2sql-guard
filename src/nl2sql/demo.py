"""Build the static demo page: every demo question run through the real pipeline."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .agent import Agent, Answer
from .llm import Recorded, extract_sql
from .sample_data import connect

CSS = """
:root{--ink:#18202e;--mute:#667085;--line:#e4e7ee;--bg:#f6f8fb;--blue:#2563eb;--ok:#15803d;--bad:#c2410c;--warn:#b45309}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:34px 20px 60px}h1{font-size:30px;margin:0 0 6px}h2{font-size:20px;margin:34px 0 12px}
.sub{color:var(--mute);margin:0 0 18px}.pipe{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:18px 0}
.pipe div{background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px 12px;font-size:14px}.pipe b{display:block;font-size:14.5px}.pipe span{color:var(--mute);font-size:12.5px}
.pipe i{color:var(--mute);font-style:normal}.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin:16px 0}
.q{font-size:18px;font-weight:700;margin:0 0 8px}.badge{display:inline-block;font-size:12.5px;font-weight:800;padding:2px 9px;border-radius:999px;margin-right:6px}
.ok{background:#dcfce7;color:var(--ok)}.blocked{background:#ffedd5;color:var(--bad)}.refused{background:#e0e7ff;color:#3730a3}.fixed{background:#fef3c7;color:var(--warn)}
pre{background:#0f172a;color:#e2e8f0;border-radius:10px;padding:12px 14px;overflow:auto;font-size:13.5px;line-height:1.45;margin:10px 0}
.note{color:var(--mute);font-size:14px;margin:4px 0 8px}.err{color:var(--bad);font-size:14px;margin:4px 0}
table{border-collapse:collapse;width:100%;font-size:14px;margin-top:8px}th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line)}th{background:#f1f4f9}
td.n{text-align:right;font-variant-numeric:tabular-nums}.bars{margin-top:10px}.bar{display:grid;grid-template-columns:220px 1fr 70px;gap:8px;align-items:center;font-size:13.5px;margin:3px 0}
.bar div{background:#dbeafe;border-radius:4px;height:14px}.bar div i{display:block;height:100%;background:var(--blue);border-radius:4px}
details summary{cursor:pointer;color:var(--mute);font-size:14px}footer{margin-top:40px;color:var(--mute);font-size:13.5px}
"""


def _bars(ans: Answer) -> str:
    if not ans.rows or len(ans.rows) > 12 or len(ans.columns) != 2:
        return ""
    vals = [r[-1] for r in ans.rows]
    if not all(isinstance(v, (int, float)) for v in vals):
        return ""
    top = max(vals) or 1
    return '<div class="bars">' + "".join(
        f'<div class="bar"><span>{escape(str(r[0]))}</span><div><i style="width:{100 * v / top:.1f}%"></i></div><span>{v:,}</span></div>'
        for r, v in zip(ans.rows, vals)) + "</div>"


def _table(ans: Answer, limit: int = 8) -> str:
    if not ans.rows:
        return ""
    head = "".join(f"<th>{escape(c)}</th>" for c in ans.columns)
    body = "".join("<tr>" + "".join(f"<td class='{'n' if isinstance(v, (int, float)) else ''}'>{escape(str(v))}</td>" for v in r) + "</tr>"
                   for r in ans.rows[:limit])
    more = f"<p class='note'>…and {len(ans.rows) - limit} more rows</p>" if len(ans.rows) > limit else ""
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{more}"


def _card(ans: Answer, note: str | None) -> str:
    stages = [a.stage for a in ans.attempts]
    if ans.ok and len(ans.attempts) > 1:
        badge = '<span class="badge fixed">self-corrected</span><span class="badge ok">answered</span>'
    elif ans.ok:
        badge = '<span class="badge ok">answered</span>'
    elif "refused" in stages:
        badge = '<span class="badge refused">model refused</span>'
    elif "guard" in stages:
        badge = '<span class="badge blocked">blocked by guardrails</span>'
    else:
        badge = '<span class="badge blocked">failed</span>'
    parts = [f'<div class="card"><p class="q">“{escape(ans.question)}”</p>{badge} <span class="note">{escape(ans.message)}</span>']
    if note:
        parts.append(f'<p class="note">{escape(note)}</p>')
    for i, a in enumerate(ans.attempts, 1):
        label = f"Attempt {i}: model output" if len(ans.attempts) > 1 else "Model output"
        parts.append(f'<p class="note"><b>{label}</b></p><pre>{escape(extract_sql(a.raw))}</pre>')
        if a.error:
            parts.append(f'<p class="err">{"Guardrail" if a.stage == "guard" else "Database"}: {escape(a.error)}</p>')
    if ans.ok and ans.sql and ans.sql.strip() != extract_sql(ans.attempts[-1].raw).strip():
        parts.append(f'<details><summary>SQL that actually ran (validated, LIMIT enforced)</summary><pre>{escape(ans.sql)}</pre></details>')
    parts.append(_bars(ans) or _table(ans))
    parts.append("</div>")
    return "".join(parts)


def build(questions_path: str | Path, data_dir: str | Path, out: str | Path) -> list[Answer]:
    spec = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    llm = Recorded({q["question"]: q["replies"] for q in spec["questions"]}, name="recorded")
    agent = Agent(connect(data_dir), llm)
    answers = [agent.ask(q["question"]) for q in spec["questions"]]
    notes = [q.get("note") for q in spec["questions"]]
    ok = sum(a.ok for a in answers)
    blocked = sum(any(x.stage == "guard" for x in a.attempts) for a in answers)
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>nl2sql-guard demo</title><style>{CSS}</style></head><body><main>
<h1>nl2sql-guard</h1><p class="sub">Plain-English questions over a bike-share database. The model proposes SQL; only validated, read-only SQL runs.
This page replays recorded model replies through the real pipeline: {ok} answered, {blocked} blocked, {len(answers) - ok - blocked} refused.</p>
<div class="pipe"><div><b>Question</b><span>plain English</span></div><i>→</i><div><b>Schema grounding</b><span>real tables, columns, enum values</span></div><i>→</i>
<div><b>LLM</b><span>Groq · OpenAI · Claude</span></div><i>→</i><div><b>Guardrails</b><span>parse · read-only · allowlist · LIMIT</span></div><i>→</i>
<div><b>DuckDB</b><span>execute</span></div><i>→</i><div><b>Self-correct</b><span>error fed back once</span></div></div>
<details><summary>Schema the model sees</summary><pre>{escape(agent.schema)}</pre></details>
<h2>Questions</h2>{''.join(_card(a, n) for a, n in zip(answers, notes))}
<footer>Data is synthetic. Source: <a href="https://github.com/mgupta646/nl2sql-guard">github.com/mgupta646/nl2sql-guard</a></footer>
</main></body></html>"""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html, encoding="utf-8")
    return answers
