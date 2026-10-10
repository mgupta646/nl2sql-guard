"""SQL guardrails: the model may *propose* SQL; only validated, read-only SQL runs.

Checks, in order:
  1. parses as exactly one statement (no `; DROP TABLE …` piggybacking)
  2. is a read-only query (SELECT / WITH … SELECT / set operations)
  3. contains no DDL, DML, PRAGMA, ATTACH, COPY, INSTALL, … anywhere in the tree
  4. calls no file/network/system functions (read_csv, glob, httpfs, …)
  5. references only tables in the allowlist (CTE names are fine)
  6. has a LIMIT no larger than max_rows (added if missing)
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Merge,
    exp.Command, exp.Copy, exp.Attach, exp.Detach, exp.Pragma, exp.Set, exp.Use, exp.TruncateTable,
)
FORBIDDEN_FUNCS = {
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text", "read_blob",
    "glob", "parquet_scan", "csv_scan", "sniff_csv", "httpfs", "query", "query_table", "getenv",
    "current_setting", "duckdb_secrets", "duckdb_settings", "pg_read_file",
}


class GuardError(ValueError):
    """Raised when proposed SQL is not allowed to run."""


@dataclass(frozen=True)
class Verdict:
    sql: str            # the SQL that will actually run (normalised, LIMIT enforced)
    tables: tuple[str, ...]
    limit_added: bool


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return (node.sql_name() or "").lower()


def check_sql(sql: str, allowed_tables: set[str], max_rows: int = 200, dialect: str = "duckdb") -> Verdict:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise GuardError("empty query")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise GuardError(f"could not parse SQL: {str(e).splitlines()[0]}") from e
    if len(statements) != 1:
        raise GuardError(f"exactly one statement allowed, got {len(statements)}")
    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        raise GuardError(f"only read-only SELECT queries are allowed, got {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise GuardError(f"{node.key.upper()} is not allowed")
        if isinstance(node, exp.Func) and _func_name(node) in FORBIDDEN_FUNCS:
            raise GuardError(f"function {_func_name(node)}() is not allowed")

    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    tables = []
    for t in tree.find_all(exp.Table):
        if not t.name:  # table functions, already vetted above
            raise GuardError("table functions are not allowed")
        name = t.name.lower()
        if t.args.get("db") or t.args.get("catalog"):
            raise GuardError(f"qualified table {t.sql()} is not allowed")
        if name not in allowed and name not in ctes:
            raise GuardError(f"unknown table '{t.name}'; allowed: {sorted(allowed)}")
        if name not in ctes:
            tables.append(name)

    limit_added = False
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)
        limit_added = True
    else:
        try:
            n = int(limit.expression.name)
        except (AttributeError, ValueError):
            raise GuardError("LIMIT must be a number") from None
        if n > max_rows:
            tree = tree.limit(max_rows)
            limit_added = True
    return Verdict(tree.sql(dialect=dialect, pretty=True), tuple(sorted(set(tables))), limit_added)
