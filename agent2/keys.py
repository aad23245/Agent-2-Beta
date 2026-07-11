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
"""

import os
import re
import time
import threading

import google.genai as genai

from agent2.config import ENV
from agent2.database import qone, exe


class KeyRotator:
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.entries: list[dict] = []   # {key, label, name, active, errs, tokens, requests, last_used}
        self._active_label: str | None = None   # pinned label (None = auto-rotate)
        self.reload()

    # ── Load keys from environment ─────────────────────────────────────────────

    def reload(self) -> None:
        seen: set[str] = set()
        new_entries: list[dict] = []
        existing = {e["label"]: e for e in self.entries}

        env_names = ["GEMINI_API_KEY"] + [f"GEMINI_API_KEY_{i}" for i in range(2, 10)]
        for env_name in env_names:
            v = os.environ.get(env_name, "").strip()
            if not v or v in seen or len(v) < 10:
                continue
            label = "1" if env_name == "GEMINI_API_KEY" else env_name.split("_")[-1]
            ex  = existing.get(label, {})
            row = qone("SELECT * FROM key_usage WHERE key_label=?", (label,))
            new_entries.append({
                "key":      v,
                "label":    label,
                "name":     ex.get("name", f"Key {label}"),
                "active":   ex.get("active", True),
                "errs":     ex.get("errs", 0),
                "tokens":   row["total_tokens"]   if row else 0,
                "requests": row["total_requests"] if row else 0,
                "last_used": row["last_used"]     if row else None,
            })
            seen.add(v)

        with self._lock:
            self.entries = new_entries

    # ── Get a usable client ────────────────────────────────────────────────────

    def get(self) -> tuple[genai.Client | None, str | None, str | None]:
        """Return (client, raw_key, label) for the next active key."""
        with self._lock:
            # Pinned key has priority
            if self._active_label:
                for e in self.entries:
                    if e["label"] == self._active_label and e["active"]:
                        return genai.Client(api_key=e["key"]), e["key"], e["label"]

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
            return genai.Client(api_key=e["key"]), e["key"], e["label"]

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
        exe(
            """INSERT INTO key_usage(key_label, total_tokens, total_requests, last_used)
               VALUES(?, ?, 1, datetime('now'))
               ON CONFLICT(key_label) DO UPDATE SET
                 total_tokens   = total_tokens + ?,
                 total_requests = total_requests + 1,
                 last_used      = datetime('now')""",
            (label, tokens, tokens),
        )

    # ── Manage keys ───────────────────────────────────────────────────────────

    def reset_key(self, label: str) -> None:
        with self._lock:
            for e in self.entries:
                if e["label"] == label:
                    e["active"] = True
                    e["errs"]   = 0

    def set_name(self, label: str, name: str) -> None:
        with self._lock:
            for e in self.entries:
                if e["label"] == label:
                    e["name"] = name
                    break
        self._save()

    def pin(self, label: str | None) -> None:
        """Pin to a specific key, or None to re-enable auto-rotate."""
        with self._lock:
            self._active_label = label

    def add(self, key: str, name: str | None = None) -> tuple[bool, str]:
        key = key.strip()
        if any(e["key"] == key for e in self.entries):
            return False, "already_exists"
        used = {int(e["label"]) for e in self.entries if e["label"].isdigit()}
        n = 1
        while n in used:
            n += 1
        label = str(n)
        with self._lock:
            self.entries.append({
                "key": key, "label": label,
                "name": name or f"Key {label}",
                "active": True, "errs": 0,
                "tokens": 0, "requests": 0, "last_used": None,
            })
        self._save()
        self.reload()
        return True, label

    def remove(self, label: str) -> None:
        with self._lock:
            self.entries = [e for e in self.entries if e["label"] != label]
            if self._active_label == label:
                self._active_label = None
        self._save()
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

    # ── Persist keys to .env ──────────────────────────────────────────────────

    def _save(self) -> None:
        env: dict[str, str] = {}
        if ENV.exists():
            for line in ENV.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if not re.match(r"^GEMINI_API_KEY", k.strip()):
                        env[k.strip()] = v.strip()

        with self._lock:
            entries = list(self.entries)

        for i, e in enumerate(entries):
            env_name = "GEMINI_API_KEY" if i == 0 else f"GEMINI_API_KEY_{e['label']}"
            env[env_name] = e["key"]

        ENV.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n", encoding="utf-8")
        for k, v in env.items():
            os.environ[k] = v


# ── Singleton ──────────────────────────────────────────────────────────────────
rotator = KeyRotator()
