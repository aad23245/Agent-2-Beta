# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/providers.py
───────────────────
Custom model providers — "bring your own API".

Lets the user register any model endpoint by supplying:
    - base_url   (e.g. https://openrouter.ai/api/v1  or  https://api.anthropic.com)
    - api_key
    - model id   (e.g. deepseek/deepseek-chat, claude-3-5-sonnet-20241022)
    - format     ("openai" | "anthropic")

Providers are persisted in SQLite (table `providers`) and each becomes a
selectable model in the UI/CLI (key = "custom:<id>"). Requests are made with
the standard library only (urllib) so no extra dependency is required, and the
same Agent2 tool schema is exposed to the model in whichever wire format it
expects. The agent loop for custom providers lives in `provider_agent.py`.
"""

from __future__ import annotations

import json
import uuid
import urllib.request
import urllib.error

from agent2.database import qall, qone, exe


# ── Persistence ─────────────────────────────────────────────────────────────────

def init_providers_table() -> None:
    exe("""
        CREATE TABLE IF NOT EXISTS providers (
            id         TEXT PRIMARY KEY,
            name       TEXT,
            base_url   TEXT,
            api_key    TEXT,
            model_id   TEXT,
            format     TEXT DEFAULT 'openai',   -- openai | anthropic
            user_agent TEXT DEFAULT '',         -- optional custom User-Agent (some gateways allowlist clients)
            created_at TEXT DEFAULT(datetime('now'))
        )
    """)
    # Migration for DBs created before user_agent existed.
    try:
        exe("ALTER TABLE providers ADD COLUMN user_agent TEXT DEFAULT ''")
    except Exception:
        pass  # column already exists


# Default User-Agent Agent 2 sends to custom providers. Kept identifiable and
# honest. Some gateways (e.g. AgentRouter) only accept an allowlisted coding-agent
# UA in name/version form — set a per-provider user_agent to satisfy those.
DEFAULT_USER_AGENT = "Agent2/2.0"


def list_providers(safe: bool = True) -> list[dict]:
    rows = qall("SELECT * FROM providers ORDER BY created_at")
    if safe:
        for r in rows:
            k = r.get("api_key") or ""
            r["api_key"] = (k[:6] + "…" + k[-4:]) if len(k) > 12 else "•••"
            r["key"] = "custom:" + r["id"]
    return rows


def get_provider(pid: str) -> dict | None:
    return qone("SELECT * FROM providers WHERE id=?", (pid,))


def add_provider(name: str, base_url: str, api_key: str,
                 model_id: str, fmt: str = "openai",
                 user_agent: str = "") -> dict:
    pid = uuid.uuid4().hex[:8]
    fmt = fmt if fmt in ("openai", "anthropic") else "openai"
    exe("""INSERT INTO providers(id, name, base_url, api_key, model_id, format, user_agent)
           VALUES(?,?,?,?,?,?,?)""",
        (pid, name or model_id, base_url.rstrip("/"), api_key, model_id, fmt,
         (user_agent or "").strip()))
    return {"id": pid, "key": "custom:" + pid, "name": name or model_id,
            "model_id": model_id, "format": fmt}


def remove_provider(pid: str) -> None:
    exe("DELETE FROM providers WHERE id=?", (pid,))


# ── HTTP helper ─────────────────────────────────────────────────────────────────

def _openai_chat_url(base_url: str) -> str:
    """
    Build the /chat/completions URL from whatever the user pasted as base_url.

    Accepts a bare host (https://api.example.com), a versioned root
    (…/v1), an OpenAI-style root (…/openai/v1) or the full completions URL —
    and always returns a valid endpoint. This is the #1 cause of "provider
    won't connect": users paste https://host with no /v1 and the old code
    POSTed to https://host/chat/completions which returns an HTML 404.
    """
    b = (base_url or "").strip().rstrip("/")
    if not b:
        return b
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/completions"):            # already a completions path
        return b
    if b.endswith("/v1") or b.endswith("/v3") or b.endswith("/openai") \
       or "/v1/" in b or "/v2/" in b:
        return b + "/chat/completions"
    # Bare host or custom root → assume OpenAI-style versioned API.
    return b + "/v1/chat/completions"


def _anthropic_messages_url(base_url: str) -> str:
    """Build the Anthropic /v1/messages URL, tolerating a trailing /v1."""
    b = (base_url or "").strip().rstrip("/")
    if b.endswith("/messages"):
        return b
    if b.endswith("/v1"):
        return b + "/messages"
    return b + "/v1/messages"


def _http_post(url: str, headers: dict, payload: dict, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code} from {url}: {body[:400]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach {url}: {getattr(e, 'reason', e)}")
    except Exception as e:
        raise RuntimeError(str(e))
    try:
        return json.loads(raw)
    except Exception:
        snippet = raw.strip().replace("\n", " ")[:220] or "(empty response)"
        raise RuntimeError(
            f"Non-JSON response from {url}. Check the Base URL is an "
            f"OpenAI-/Anthropic-compatible API root (it usually ends in /v1). "
            f"Server said: {snippet}")


# ── Tool-schema translation ─────────────────────────────────────────────────────
# Agent2's canonical tool schema (name, description, JSON-schema params) is
# defined here once and rendered into each provider's wire format.

def agent_tool_schema() -> list[dict]:
    """Canonical tool list as plain JSON-Schema dicts (provider-agnostic)."""
    obj = "object"
    return [
        {"name": "run_command",
         "description": "Execute a shell command on the user's machine (installs, builds, tests, scans, launches).",
         "parameters": {"type": obj, "properties": {
             "command": {"type": "string"}, "description": {"type": "string"}},
             "required": ["command", "description"]}},
        {"name": "read_file",
         "description": "Read a file's contents. Read before editing.",
         "parameters": {"type": obj, "properties": {
             "path": {"type": "string"},
             "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
             "required": ["path"]}},
        {"name": "write_file",
         "description": "Create or overwrite a file with content. Parent dirs auto-created.",
         "parameters": {"type": obj, "properties": {
             "path": {"type": "string"}, "content": {"type": "string"}},
             "required": ["path", "content"]}},
        {"name": "multi_edit_files",
         "description": "Find/replace exact text across multiple files.",
         "parameters": {"type": obj, "properties": {
             "edits": {"type": "array", "items": {"type": obj, "properties": {
                 "path": {"type": "string"}, "old_text": {"type": "string"},
                 "new_text": {"type": "string"}}}}},
             "required": ["edits"]}},
        {"name": "list_dir",
         "description": "List a directory's files and subfolders.",
         "parameters": {"type": obj, "properties": {"path": {"type": "string"}},
                        "required": ["path"]}},
        {"name": "grep_search",
         "description": "Regex-search file contents across a directory tree.",
         "parameters": {"type": obj, "properties": {
             "pattern": {"type": "string"}, "path": {"type": "string"},
             "glob": {"type": "string"}}, "required": ["pattern"]}},
        {"name": "delete_file",
         "description": "Delete a file or directory recursively.",
         "parameters": {"type": obj, "properties": {"path": {"type": "string"}},
                        "required": ["path"]}},
        {"name": "scan_project",
         "description": "Recursively scan a project: file tree + all source contents.",
         "parameters": {"type": obj, "properties": {"path": {"type": "string"}},
                        "required": ["path"]}},
        {"name": "web_search",
         "description": "Search the web for docs, errors, CVEs.",
         "parameters": {"type": obj, "properties": {"query": {"type": "string"}},
                        "required": ["query"]}},
        {"name": "update_todo",
         "description": "Create/update a live TODO checklist for a multi-step build. Pass the full list each time with each item's status (pending|in_progress|completed).",
         "parameters": {"type": obj, "properties": {
             "todos": {"type": "array", "items": {"type": obj, "properties": {
                 "task": {"type": "string"}, "status": {"type": "string"}}}}},
             "required": ["todos"]}},
        {"name": "save_memory",
         "description": "Persist an important fact across sessions.",
         "parameters": {"type": obj, "properties": {"content": {"type": "string"}},
                        "required": ["content"]}},
    ]


def _burp_tool_schemas() -> list[dict]:
    """Live Burp MCP tools as provider-agnostic schemas (empty if not connected)."""
    try:
        from agent2.burp_mcp import burp
        if burp.is_connected():
            return burp.provider_tool_schemas()
    except Exception:
        pass
    return []


def _openai_tools() -> list[dict]:
    tools = agent_tool_schema() + _burp_tool_schemas()
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["parameters"]}}
            for t in tools]


def _anthropic_tools() -> list[dict]:
    tools = agent_tool_schema() + _burp_tool_schemas()
    return [{"name": t["name"], "description": t["description"],
             "input_schema": t["parameters"]}
            for t in tools]


# ── Chat call — returns a normalised result ─────────────────────────────────────
#
# Normalised result shape:
#   {"text": str, "tool_calls": [{"id","name","args"}], "tokens": int}

def call_openai(prov: dict, messages: list[dict], system: str) -> dict:
    url = _openai_chat_url(prov["base_url"])
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {prov['api_key']}",
               "User-Agent": (prov.get("user_agent") or "").strip() or DEFAULT_USER_AGENT,
               # Some gateways (OpenRouter etc.) want these; harmless elsewhere.
               "HTTP-Referer": "https://github.com/aaravshah1311",
               "X-Title": "Agent 2"}
    msgs = [{"role": "system", "content": system}] + messages
    payload = {"model": prov["model_id"], "messages": msgs,
               "tools": _openai_tools(), "tool_choice": "auto"}
    data = _http_post(url, headers, payload)

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    tool_calls = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function", {}) or {}
        try:
            a = json.loads(fn.get("arguments") or "{}")
        except Exception:
            a = {}
        tool_calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "args": a})
    usage = data.get("usage", {}) or {}
    return {"text": msg.get("content") or "",
            "tool_calls": tool_calls,
            "tokens": usage.get("total_tokens", 0),
            "raw_assistant": msg}


def call_anthropic(prov: dict, messages: list[dict], system: str) -> dict:
    url = _anthropic_messages_url(prov["base_url"])
    headers = {"Content-Type": "application/json",
               "x-api-key": prov["api_key"],
               "User-Agent": (prov.get("user_agent") or "").strip() or DEFAULT_USER_AGENT,
               "anthropic-version": "2023-06-01"}
    payload = {"model": prov["model_id"], "system": system,
               "messages": messages, "tools": _anthropic_tools(),
               "max_tokens": 8192}
    data = _http_post(url, headers, payload)

    text, tool_calls = "", []
    for block in (data.get("content") or []):
        if block.get("type") == "text":
            text += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append({"id": block.get("id", ""),
                               "name": block.get("name", ""),
                               "args": block.get("input", {}) or {}})
    usage = data.get("usage", {}) or {}
    tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    return {"text": text, "tool_calls": tool_calls, "tokens": tokens,
            "raw_content": data.get("content", [])}


def chat(prov: dict, messages: list[dict], system: str) -> dict:
    """Dispatch to the right wire format. `prov` is a full DB row (with api_key)."""
    if prov.get("format") == "anthropic":
        return call_anthropic(prov, messages, system)
    return call_openai(prov, messages, system)
