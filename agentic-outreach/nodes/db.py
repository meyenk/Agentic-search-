"""
nodes/db.py — SQLite tracker so we never re-contact the same professor
or re-surface the same job posting across runs.
"""

import sqlite3
import os
from config import DB_FILE


def get_conn():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS contacted (
            id          TEXT PRIMARY KEY,
            kind        TEXT,
            name        TEXT,
            org         TEXT,
            score       REAL,
            drafted_on  TEXT DEFAULT (date('now'))
        );
    """)
    conn.commit()
    conn.close()


def already_contacted(candidate_id: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT id FROM contacted WHERE id=?", (candidate_id,)).fetchone()
    conn.close()
    return row is not None


def mark_contacted(candidate: dict):
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO contacted (id, kind, name, org, score)
        VALUES (?, ?, ?, ?, ?)
    """, (candidate["id"], candidate["kind"], candidate["name"], candidate["org"], candidate.get("score", 0)))
    conn.commit()
    conn.close()
