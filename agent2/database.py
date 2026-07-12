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

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        -- Gemini API keys live here now (replaces the old .env storage). All
        -- API credentials — Gemini keys AND custom-provider keys — are kept in
        -- this single SQLite DB so the Web UI and CLI share one source of truth.
        CREATE TABLE IF NOT EXISTS api_keys (
            label      TEXT PRIMARY KEY,
            api_key    TEXT UNIQUE,
            name       TEXT,
            active     INTEGER DEFAULT 1,
            created_at TEXT DEFAULT(datetime('now'))
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

    # One-time migration of any legacy .env Gemini keys into the DB.
    try:
        migrate_env_keys()
    except Exception:
        pass


# ── Settings (small key-value store, shared by web + CLI) ───────────────────────

def get_setting(key: str, default: str | None = None) -> str | None:
    """Return a persisted setting value, or *default* if unset."""
    try:
        row = qone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default
    except Exception:
        return default


def set_setting(key: str, value: str) -> None:
    """Persist a setting (upsert)."""
    exe("INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)))


# ── Gemini API keys (stored in DB — no more .env) ───────────────────────────────
# The whole app now keeps Gemini keys in the `api_keys` table. These helpers are
# the single, failsafe gateway; every one swallows errors and returns a sane
# default so a corrupt/locked DB can never crash the agent.

def _next_key_label() -> str:
    """Smallest positive integer label not already taken."""
    try:
        used = {r["label"] for r in qall("SELECT label FROM api_keys")}
    except Exception:
        used = set()
    n = 1
    while str(n) in used:
        n += 1
    return str(n)


def list_api_keys() -> list[dict]:
    """All stored Gemini keys, ordered by creation, or [] on any failure."""
    try:
        return qall("SELECT * FROM api_keys ORDER BY created_at, label")
    except Exception:
        return []


def add_api_key(api_key: str, name: str | None = None) -> tuple[bool, str]:
    """Insert a Gemini key. Returns (ok, label_or_reason)."""
    api_key = (api_key or "").strip().replace(" ", "").replace("\n", "")
    if len(api_key) < 15:
        return False, "too_short"
    try:
        if qone("SELECT label FROM api_keys WHERE api_key=?", (api_key,)):
            return False, "already_exists"
        label = _next_key_label()
        exe("INSERT INTO api_keys(label, api_key, name, active) VALUES(?,?,?,1)",
            (label, api_key, name or f"Key {label}"))
        return True, label
    except Exception as ex:
        return False, str(ex)[:120]


def remove_api_key(label: str) -> None:
    try:
        exe("DELETE FROM api_keys WHERE label=?", (str(label),))
    except Exception:
        pass


def set_api_key_name(label: str, name: str) -> None:
    try:
        exe("UPDATE api_keys SET name=? WHERE label=?", (name, str(label)))
    except Exception:
        pass


def migrate_env_keys() -> int:
    """One-time import of any GEMINI_API_KEY* values from a legacy .env file
    (or the environment) into the DB, then neutralise the .env file. Returns the
    number of keys migrated. Safe to call on every startup — it no-ops once done."""
    migrated = 0
    try:
        from agent2.config import ENV  # local import to avoid cycles
    except Exception:
        ENV = None

    # Collect candidate keys from both the process env and the .env file.
    candidates: list[str] = []
    for i in ([""] + [f"_{n}" for n in range(2, 10)]):
        import os as _os
        v = _os.environ.get(f"GEMINI_API_KEY{i}", "").strip()
        if v:
            candidates.append(v)
    if ENV is not None:
        try:
            if ENV.exists():
                for line in ENV.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip().startswith("GEMINI_API_KEY"):
                            candidates.append(v.strip().strip('"').strip("'"))
        except Exception:
            pass

    placeholder = "your_gemini_api_key_here"
    seen: set[str] = set()
    for v in candidates:
        v = v.strip()
        if not v or v == placeholder or len(v) < 15 or v in seen:
            continue
        seen.add(v)
        ok, _ = add_api_key(v)
        if ok:
            migrated += 1

    # Retire the legacy .env so it is never read again.
    try:
        if ENV is not None and ENV.exists():
            ENV.rename(ENV.with_suffix(".env.migrated"))
    except Exception:
        pass

    return migrated
