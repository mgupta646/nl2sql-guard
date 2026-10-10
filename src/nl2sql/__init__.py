"""nl2sql-guard: plain-English questions over DuckDB, with SQL guardrails."""
from .agent import Agent, Answer
from .guard import GuardError, Verdict, check_sql
from .llm import Recorded, extract_sql, get_llm

__all__ = ["Agent", "Answer", "GuardError", "Recorded", "Verdict", "check_sql", "extract_sql", "get_llm"]
__version__ = "0.1.0"
