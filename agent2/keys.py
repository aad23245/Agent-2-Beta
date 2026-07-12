# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/keys.py
──────────────
KeyRotator: manages multiple Gemini API keys with:
  - auto-rotation on quota exhaustion
  - manual pinning (always use a specific key)
  - per-key usage tracking (tokens + requests) persisted to SQLite
  - label / friendly-name support
  - thread-safe access

Keys are stored ENTIRELY in agent2.db (table `api_keys`) — there is no .env.
Every DB access is wrapped so a locked/corrupt DB degrades gracefully instead
of crashing the agent.
"""

import time
import threading

try:
    import google.genai as genai
except Exception:  # google-genai not installed yet
    genai = None

from agent2.database import (
    qone, exe,
    list_api_keys, add_api_key, remove_api_key, set_api_key_name,
)


class KeyRotator:
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.entries: list[dict] = []   # {key, label, name, active, errs, tokens, requests, last_used}
        self._active_label: str | None = None   # pinned label (None = auto-rotate)
        self.reload()

    # ── Load keys from the database ────────────────────────────────────────────

    def reload(self) -> None:
        seen: set[str] = set()
        new_entries: list[dict] = []
        existing = {e["label"]: e for e in self.entries}

        for rec in list_api_keys():
            v = (rec.get("api_key") or "").strip()
            label = str(rec.get("label") or "")
            if not v or v in seen or len(v) < 10 or not label:
                continue
            ex = existing.get(label, {})
            try:
                row = qone("SELECT * FROM key_usage WHERE key_label=?", (label,))
            except Exception:
                row = None
            new_entries.append({
                "key":      v,
                "label":    label,
                "name":     rec.get("name") or ex.get("name", f"Key {label}"),
                "active":   ex.get("active", bool(rec.get("active", 1))),
                "errs":     ex.get("errs", 0),
                "tokens":   row["total_tokens"]   if row else 0,
                "requests": row["total_requests"] if row else 0,
                "last_used": row["last_used"]     if row else None,
            })
            seen.add(v)

        with self._lock:
            self.entries = new_entries

    # ── Get a usable client ────────────────────────────────────────────────────

    def get(self) -> tuple["genai.Client | None", str | None, str | None]:
        """Return (client, raw_key, label) for the next active key."""
        if genai is None:
            return None, None, None
        with self._lock:
            # Pinned key has priority
            if self._active_label:
                for e in self.entries:
                    if e["label"] == self._active_label and e["active"]:
                        return self._client(e["key"]), e["key"], e["label"]

            good = [e for e in self.entries if e["active"]]
            if not good:
                # All exhausted — reset and retry once
                for e in self.entries:
                    e["active"] = True
                    e["errs"]   = 0
                good = self.entries

            if not good:
                return None, None, None

            e = good[0]
            return self._client(e["key"]), e["key"], e["label"]

    @staticmethod
    def _client(key: str):
        try:
            return genai.Client(api_key=key)
        except Exception:
            return None

    # ── Mark failures ──────────────────────────────────────────────────────────

    def fail(self, key: str, quota: bool = False) -> None:
        with self._lock:
            for e in self.entries:
                if e["key"] == key:
                    e["errs"] += 1
                    if quota or e["errs"] >= 3:
                        e["active"] = False
                    break

    # ── Record successful usage ────────────────────────────────────────────────

    def record_usage(self, label: str, tokens: int) -> None:
        with self._lock:
            for e in self.entries:
                if e["label"] == label:
                    e["tokens"]   += tokens
                    e["requests"] += 1
                    e["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    break
        try:
            exe(
                """INSERT INTO key_usage(key_label, total_tokens, total_requests, last_used)
                   VALUES(?, ?, 1, datetime('now'))
                   ON CONFLICT(key_label) DO UPDATE SET
                     total_tokens   = total_tokens + ?,
                     total_requests = total_requests + 1,
                     last_used      = datetime('now')""",
                (label, tokens, tokens),
            )
        except Exception:
            pass

    # ── Manage keys ───────────────────────────────────────────────────────────

    def reset_key(self, label: str) -> None:
        with self._lock:
            for e in self.entries:
                if e["label"] == label:
                    e["active"] = True
                    e["errs"]   = 0

    def set_name(self, label: str, name: str) -> None:
        set_api_key_name(label, name)
        self.reload()

    def pin(self, label: str | None) -> None:
        """Pin to a specific key, or None to re-enable auto-rotate."""
        with self._lock:
            self._active_label = label

    def add(self, key: str, name: str | None = None) -> tuple[bool, str]:
        ok, label = add_api_key(key, name)
        if ok:
            self.reload()
        return ok, label

    def remove(self, label: str) -> None:
        remove_api_key(label)
        with self._lock:
            if self._active_label == label:
                self._active_label = None
        self.reload()

    # ── Status snapshot (safe for JSON serialisation) ─────────────────────────

    def status(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "label":    e["label"],
                    "name":     e["name"],
                    "preview":  e["key"][:14] + "…",
                    "active":   e["active"],
                    "errs":     e["errs"],
                    "tokens":   e["tokens"],
                    "requests": e["requests"],
                    "last_used": e["last_used"],
                    "pinned":   self._active_label == e["label"],
                }
                for e in self.entries
            ]


# ── Singleton ──────────────────────────────────────────────────────────────────
try:
    rotator = KeyRotator()
except Exception:
    # Never let key loading crash import of the whole app.
    rotator = KeyRotator.__new__(KeyRotator)
    rotator.entries = []
    rotator._active_label = None
