import json
from pathlib import Path

import pytest

from nl2sql import Agent, GuardError, Recorded, check_sql, extract_sql
from nl2sql.demo import build
from nl2sql.sample_data import connect

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data"
TABLES = {"trips", "stations"}


# ---------------------------------------------------------------- guardrails
@pytest.mark.parametrize("sql, reason", [
    ("DROP TABLE trips", "read-only"),
    ("DELETE FROM trips", "read-only"),
    ("INSERT INTO trips VALUES (1)", "read-only"),
    ("UPDATE trips SET duration_min = 0", "read-only"),
    ("SELECT 1; DROP TABLE trips", "one statement"),
    ("SELECT * FROM read_csv('/etc/passwd')", "read_csv"),
    ("SELECT * FROM read_parquet('s3://bucket/x.parquet')", "read_parquet"),
    ("SELECT getenv('HOME')", "getenv"),
    ("SELECT * FROM users", "unknown table"),
    ("SELECT * FROM main.trips", "qualified"),
    ("ATTACH 'other.db'", "read-only"),
    ("COPY trips TO 'out.csv'", "read-only"),
    ("PRAGMA show_tables", "read-only"),
    ("", "empty"),
    ("SELEC * FRM trips", "parse|read-only"),
])
def test_guard_blocks(sql, reason):
    with pytest.raises(GuardError, match=reason):
        check_sql(sql, TABLES)


def test_guard_adds_limit():
    v = check_sql("SELECT * FROM trips", TABLES, max_rows=50)
    assert v.limit_added and "LIMIT 50" in v.sql


def test_guard_caps_large_limit():
    assert "LIMIT 20" in check_sql("SELECT * FROM trips LIMIT 9999", TABLES, max_rows=20).sql


def test_guard_keeps_small_limit():
    v = check_sql("SELECT * FROM trips LIMIT 5", TABLES)
    assert not v.limit_added and "LIMIT 5" in v.sql


def test_guard_allows_ctes_joins_and_unions():
    v = check_sql("WITH m AS (SELECT * FROM trips WHERE rider_type='member') "
                  "SELECT s.name, count(*) FROM m JOIN stations s ON s.station_id = m.start_station_id GROUP BY 1", TABLES)
    assert v.tables == ("stations", "trips")


def test_extract_sql_from_fenced_reply():
    assert extract_sql("Sure!\n```sql\nSELECT 1\n```\nHope that helps") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"


# ---------------------------------------------------------------- agent
@pytest.fixture(scope="module")
def con():
    return connect(DATA)


def test_schema_prompt_lists_real_columns_and_enum_values(con):
    schema = Agent(con, llm=None).schema
    assert "TABLE trips" in schema and "distance_km" in schema and "'member'" in schema


def test_agent_answers(con):
    llm = Recorded({"How many trips?": ["```sql\nSELECT count(*) AS n FROM trips\n```"]})
    ans = Agent(con, llm).ask("How many trips?")
    assert ans.ok and ans.rows[0][0] > 10000 and len(ans.attempts) == 1


def test_agent_self_corrects_after_database_error(con):
    llm = Recorded({"q": ["SELECT avg(distance_miles) FROM trips", "SELECT avg(distance_km) FROM trips"]})
    ans = Agent(con, llm).ask("q")
    assert ans.ok and [a.stage for a in ans.attempts] == ["execute", "ok"]
    assert "distance_miles" in ans.attempts[0].error


def test_agent_never_retries_guard_violations(con):
    llm = Recorded({"q": ["DROP TABLE trips", "SELECT 1"]})
    ans = Agent(con, llm).ask("q")
    assert not ans.ok and len(ans.attempts) == 1 and ans.attempts[0].stage == "guard"
    assert con.execute("SELECT count(*) FROM trips").fetchone()[0] > 0  # table still there


def test_agent_respects_refusal(con):
    ans = Agent(con, Recorded({"q": ["CANNOT_ANSWER"]})).ask("q")
    assert not ans.ok and ans.attempts[0].stage == "refused"


def test_agent_gives_up_after_max_attempts(con):
    ans = Agent(con, Recorded({"q": ["SELECT nope FROM trips"]}), max_attempts=2).ask("q")
    assert not ans.ok and len(ans.attempts) == 2


# ---------------------------------------------------------------- demo
def test_demo_page_builds(tmp_path):
    answers = build(ROOT / "examples" / "demo_questions.json", DATA, tmp_path / "index.html")
    spec = json.loads((ROOT / "examples" / "demo_questions.json").read_text(encoding="utf-8"))
    assert len(answers) == len(spec["questions"])
    assert sum(a.ok for a in answers) == 7
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "blocked by guardrails" in html and "self-corrected" in html
