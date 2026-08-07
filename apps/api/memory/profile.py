"""
Tier 2 — PROFILE memory: durable structured facts about a student.

Deliberately stored in its own SQLite file (data/memory.db), not in
campus.db: scripts/seed.py drops and rebuilds campus.db, and re-seeding the
demo data must not erase what the system has learned about a student.
Survives process restarts by virtue of being on disk — that is exactly what
the cross-session acceptance test checks.
"""
import sqlite3
import time
from pathlib import Path

MEMORY_DB = Path(__file__).resolve().parents[3] / "data" / "memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_facts (
    student_id    TEXT NOT NULL,
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    confidence    REAL NOT NULL,
    evidence_turn TEXT,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (student_id, key)
);

CREATE TABLE IF NOT EXISTS profile_fact_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id    TEXT NOT NULL,
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    confidence    REAL NOT NULL,
    evidence_turn TEXT,
    recorded_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_summaries (
    id          TEXT PRIMARY KEY,
    student_id  TEXT NOT NULL,
    thread_id   TEXT,
    summary     TEXT NOT NULL,
    ts          REAL NOT NULL
);
"""


def _connect():
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MEMORY_DB)
    conn.executescript(SCHEMA)
    return conn


def upsert_fact(student_id: str, key: str, value: str, confidence: float, evidence_turn: str = "") -> bool:
    """Insert or update one durable fact. On conflict the higher-confidence
    value wins; the previous value is always appended to history so a
    confident-but-wrong extraction can be audited later.

    Returns True if the live row was written/updated, False if an existing
    higher-confidence value was kept.
    """
    now = time.time()
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT value, confidence FROM profile_facts WHERE student_id=? AND key=?",
            (student_id, key),
        ).fetchone()

        conn.execute(
            "INSERT INTO profile_fact_history (student_id, key, value, confidence, evidence_turn, recorded_at) "
            "VALUES (?,?,?,?,?,?)",
            (student_id, key, value, confidence, evidence_turn, now),
        )

        if existing and existing[1] > confidence:
            conn.commit()
            return False

        conn.execute(
            "INSERT INTO profile_facts (student_id, key, value, confidence, evidence_turn, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(student_id, key) DO UPDATE SET "
            "value=excluded.value, confidence=excluded.confidence, "
            "evidence_turn=excluded.evidence_turn, updated_at=excluded.updated_at",
            (student_id, key, value, confidence, evidence_turn, now),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_facts(student_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT key, value, confidence, evidence_turn, updated_at FROM profile_facts "
            "WHERE student_id=? ORDER BY confidence DESC, updated_at DESC",
            (student_id,),
        ).fetchall()
        return [
            {"key": r[0], "value": r[1], "confidence": r[2], "evidence_turn": r[3], "updated_at": r[4]}
            for r in rows
        ]
    finally:
        conn.close()


def save_turn_summary(summary_id: str, student_id: str, thread_id: str, summary: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO turn_summaries (id, student_id, thread_id, summary, ts) VALUES (?,?,?,?,?)",
            (summary_id, student_id, thread_id, summary, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def get_turn_summary(summary_id: str) -> dict | None:
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT id, student_id, thread_id, summary, ts FROM turn_summaries WHERE id=?", (summary_id,)
        ).fetchone()
        if not r:
            return None
        return {"id": r[0], "student_id": r[1], "thread_id": r[2], "summary": r[3], "ts": r[4]}
    finally:
        conn.close()


def clear_all() -> None:
    """Used by scripts/reset_demo.sh and tests."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM profile_facts")
        conn.execute("DELETE FROM profile_fact_history")
        conn.execute("DELETE FROM turn_summaries")
        conn.commit()
    finally:
        conn.close()
