"""Describe a DuckDB database compactly so the model only sees what really exists."""
from __future__ import annotations

from dataclasses import dataclass

import duckdb


@dataclass
class Column:
    name: str
    type: str
    examples: list


@dataclass
class Table:
    name: str
    rows: int
    columns: list[Column]


def introspect(con: duckdb.DuckDBPyConnection, max_examples: int = 4, low_cardinality: int = 12) -> list[Table]:
    out = []
    names = [r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY 1").fetchall()]
    for t in names:
        rows = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        cols = []
        for name, typ, *_ in con.execute(f'DESCRIBE "{t}"').fetchall():
            examples = []
            if typ.upper() in ("VARCHAR", "TEXT"):
                distinct = con.execute(f'SELECT count(DISTINCT "{name}") FROM "{t}"').fetchone()[0]
                if distinct <= low_cardinality:  # enumerations: show every value so the model spells them right
                    examples = [r[0] for r in con.execute(f'SELECT DISTINCT "{name}" FROM "{t}" WHERE "{name}" IS NOT NULL ORDER BY 1').fetchall()]
                else:
                    examples = [r[0] for r in con.execute(f'SELECT "{name}" FROM "{t}" WHERE "{name}" IS NOT NULL LIMIT {max_examples}').fetchall()]
            cols.append(Column(name, typ, examples))
        out.append(Table(t, rows, cols))
    return out


def schema_prompt(tables: list[Table]) -> str:
    lines = []
    for t in tables:
        lines.append(f"TABLE {t.name} ({t.rows:,} rows)")
        for c in t.columns:
            ex = f"  -- e.g. {', '.join(repr(e) for e in c.examples)}" if c.examples else ""
            lines.append(f"  {c.name} {c.type}{ex}")
    return "\n".join(lines)
