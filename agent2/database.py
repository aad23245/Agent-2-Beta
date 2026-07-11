# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/database.py
──────────────────
SQLite connection helpers, schema creation, and migrations.
All DB access goes through qall / qone / exe so the rest of the app
never touches sqlite3 directly.
"""

import sqlite3
from agent2.config import DB


# ── Connection ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def qall(sql: str, p: tuple = ()) -> list[dict]:
    """Execute SELECT and return all rows as plain dicts."""
    c = _conn()
    rows = [dict(r) for r in c.execute(sql, p).fetchall()]
    c.close()
    return rows


def qone(sql: str, p: tuple = ()) -> dict | None:
    """Execute SELECT and return the first row as a dict, or None."""
    c = _conn()
    r = c.execute(sql, p).fetchone()
    c.close()
    return dict(r) if r else None


def exe(sql: str, p: tuple = ()) -> None:
    """Execute a write statement (INSERT / UPDATE / DELETE)."""
    c = _conn()
    c.execute(sql, p)
    c.commit()
    c.close()


# ── Schema ─────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables and run any pending migrations."""
    c = _conn()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS chats (
            id         TEXT PRIMARY KEY,
            title      TEXT DEFAULT 'New Chat',
            model      TEXT DEFAULT 'gemini-2.5-flash',
            mode       TEXT DEFAULT 'pro',
            created_at TEXT DEFAULT(datetime('now')),
            updated_at TEXT DEFAULT(datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS messages (
            id         TEXT PRIMARY KEY,
            chat_id    TEXT,
            role       TEXT,      -- user | assistant | tool_call | tool_result
            content    TEXT,
            meta       TEXT DEFAULT '{}',
            created_at TEXT DEFAULT(datetime('now')),
            FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memories (
            id         TEXT PRIMARY KEY,
            content    TEXT,
            created_at TEXT DEFAULT(datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS rules (
            id         TEXT PRIMARY KEY,
            content    TEXT,
            active     INTEGER DEFAULT 1,
            created_at TEXT DEFAULT(datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS key_usage (
            key_label       TEXT PRIMARY KEY,
            total_tokens    INTEGER DEFAULT 0,
            total_requests  INTEGER DEFAULT 0,
            last_used       TEXT DEFAULT(datetime('now'))
        );
    """)

    # ── Migrations: add columns that may not exist on older DBs ───────────────
    _migrations = [
        ("chats",    "model", "TEXT DEFAULT 'gemini-2.5-flash'"),
        ("chats",    "mode",  "TEXT DEFAULT 'pro'"),
    ]
    for table, col, typedef in _migrations:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # column already exists

    c.commit()
    c.close()
