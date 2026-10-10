"""Question → SQL → guard → execute, with one self-correction loop."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import duckdb

from .guard import GuardError, check_sql
from .llm import LLM, extract_sql
from .schema import introspect, schema_prompt

SYSTEM = """You translate questions into a single DuckDB SQL query.
Rules:
- Use ONLY the tables and columns listed in the schema. Never invent columns.
- Write one read-only SELECT (CTEs are fine). Never modify data.
- If the question asks to change, delete or export data, or cannot be answered from the schema, reply exactly: CANNOT_ANSWER
- Prefer readable column aliases. Round averages to 1–2 decimals.
- Reply with the SQL in a ```sql fenced block and nothing else."""


@dataclass
class Attempt:
    raw: str
    sql: str | None = None
    error: str | None = None
    stage: str = ""          # guard | execute | ok | refused


@dataclass
class Answer:
    question: str
    ok: bool
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    message: str = ""
    seconds: float = 0.0


class Agent:
    def __init__(self, con: duckdb.DuckDBPyConnection, llm: LLM, max_rows: int = 200, max_attempts: int = 2):
        self.con, self.llm, self.max_rows, self.max_attempts = con, llm, max_rows, max_attempts
        self.tables = introspect(con)
        self.allowed = {t.name for t in self.tables}
        self.schema = schema_prompt(self.tables)

    def _prompt(self, question: str, feedback: str | None) -> str:
        p = f"SCHEMA:\n{self.schema}\n\nQUESTION: {question}\n"
        if feedback:
            p += f"\nYour previous SQL failed:\n{feedback}\nFix it and reply with corrected SQL only.\n"
        return p

    def ask(self, question: str) -> Answer:
        t0 = time.perf_counter()
        ans, feedback = Answer(question, ok=False), None
        for _ in range(self.max_attempts):
            raw = self.llm.generate(SYSTEM, self._prompt(question, feedback))
            att = Attempt(raw)
            ans.attempts.append(att)
            if "CANNOT_ANSWER" in raw.upper():
                att.stage, ans.message = "refused", "The model declined: the question can't be answered read-only from this schema."
                break
            try:
                verdict = check_sql(extract_sql(raw), self.allowed, self.max_rows)
            except GuardError as e:
                # Guard violations are never retried: a model that tried to write or escape
                # the sandbox once should not get a second attempt at the same request.
                att.stage, att.error = "guard", str(e)
                ans.message = f"Blocked by guardrails: {e}"
                break
            att.sql = verdict.sql
            try:
                cur = self.con.execute(verdict.sql)
                ans.columns = [d[0] for d in cur.description]
                ans.rows = cur.fetchall()
                att.stage, ans.ok, ans.sql = "ok", True, verdict.sql
                truncated = verdict.limit_added and len(ans.rows) >= self.max_rows
                ans.message = f"{len(ans.rows)} row(s)" + (f" · capped at {self.max_rows} by the guard" if truncated else "")
                break
            except duckdb.Error as e:
                att.stage, att.error = "execute", str(e).splitlines()[0]
                feedback = f"{verdict.sql}\nERROR: {att.error}"
                ans.message = f"Query failed: {att.error}"
        ans.seconds = round(time.perf_counter() - t0, 3)
        return ans
