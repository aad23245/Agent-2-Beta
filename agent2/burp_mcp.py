# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/burp_mcp.py
──────────────────
Burp Suite MCP bridge.

Connects Agent2 to the Burp Suite **MCP server** (the official PortSwigger
"MCP Server" BApp, which exposes an SSE endpoint — default
http://127.0.0.1:9876/sse). Once connected, EVERY tool Burp exposes
(proxy history, Repeater, Intruder, Scanner, site map, active-scan issues,
send raw HTTP request, etc.) becomes available to the Gemini agent as a
normal function call — so the user can drive all of Burp from Agent2.

Design
------
The `mcp` SDK is async, but both Agent2 agent loops (web + CLI) are
synchronous. We therefore run a single long-lived asyncio event loop on a
background daemon thread. The SSE connection + MCP `ClientSession` are opened
once and kept alive for the life of the process; synchronous callers schedule
coroutines onto that loop with `run_coroutine_threadsafe` and block on the
returned future.

Public surface (all synchronous, safe to call from any thread):
    burp.enabled                      -> bool   (config toggle)
    burp.connect(timeout=)            -> (ok, message)
    burp.disconnect()                 -> None
    burp.is_connected()               -> bool
    burp.list_tools()                 -> list[dict]   (cached MCP tool schemas)
    burp.gemini_declarations()        -> list[FunctionDeclaration]
    burp.is_burp_tool(name)           -> bool
    burp.call_tool(name, args)        -> dict   (normalised result)
    burp.status()                     -> dict   (for UI / /burp command)
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from agent2.config import BURP_MCP_URL, BURP_MCP_ENABLED

# `google.genai` is always installed; `mcp` may be absent on very old installs.
try:
    from google.genai import types as _gtypes
except Exception:  # pragma: no cover - genai is a hard dependency elsewhere
    _gtypes = None

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    _MCP_AVAILABLE = True
    _MCP_IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover
    ClientSession = None       # type: ignore
    sse_client = None          # type: ignore
    _MCP_AVAILABLE = False
    _MCP_IMPORT_ERROR = str(_exc)


# ── JSON-Schema → Gemini Schema conversion ──────────────────────────────────────

def _json_schema_to_gemini(schema: dict | None):
    """
    Convert a JSON Schema (as returned by MCP tool.inputSchema) into a
    google.genai types.Schema. Best-effort: unknown/again-nested constructs
    degrade gracefully to STRING so a tool is never dropped entirely.
    """
    if _gtypes is None:
        return None
    S, T = _gtypes.Schema, _gtypes.Type
    schema = schema or {}

    type_map = {
        "string":  T.STRING,
        "integer": T.INTEGER,
        "number":  T.NUMBER,
        "boolean": T.BOOLEAN,
        "array":   T.ARRAY,
        "object":  T.OBJECT,
    }

    jtype = schema.get("type")
    # JSON Schema allows a list of types (e.g. ["string", "null"]); pick the
    # first concrete, non-null one.
    if isinstance(jtype, list):
        jtype = next((t for t in jtype if t != "null"), "string")

    gtype = type_map.get(jtype, T.STRING)

    kwargs: dict[str, Any] = {"type": gtype}
    if schema.get("description"):
        kwargs["description"] = str(schema["description"])[:1024]
    # Enums (Gemini only supports string enums)
    if schema.get("enum") and gtype == T.STRING:
        kwargs["enum"] = [str(e) for e in schema["enum"]]

    if gtype == T.OBJECT:
        props = schema.get("properties") or {}
        g_props = {}
        for pname, pschema in props.items():
            child = _json_schema_to_gemini(pschema if isinstance(pschema, dict) else {})
            if child is not None:
                g_props[pname] = child
        if g_props:
            kwargs["properties"] = g_props
        req = [r for r in (schema.get("required") or []) if r in g_props]
        if req:
            kwargs["required"] = req

    elif gtype == T.ARRAY:
        items = schema.get("items")
        if isinstance(items, dict):
            child = _json_schema_to_gemini(items)
            if child is not None:
                kwargs["items"] = child
        else:
            kwargs["items"] = S(type=T.STRING)

    return S(**kwargs)


def _root_cause(exc: BaseException) -> str:
    """Unwrap ExceptionGroup / __cause__ chains into a short readable message."""
    seen = 0
    cur: BaseException | None = exc
    while cur is not None and seen < 6:
        subs = getattr(cur, "exceptions", None)  # ExceptionGroup
        if subs:
            cur = subs[0]
        elif cur.__cause__ is not None:
            cur = cur.__cause__
        else:
            break
        seen += 1
    msg = str(cur) or cur.__class__.__name__
    return msg[:200]


def _sanitize_name(name: str) -> str:
    """
    Normalise an MCP tool name into one that is valid across every backend:
    Gemini allows ^[a-zA-Z0-9_.-]+, but OpenAI/Anthropic reject '.', so we map
    everything except alphanumerics, '_' and '-' to '_' and namespace under burp_.
    """
    safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in name)
    if not safe.startswith("burp_"):
        safe = "burp_" + safe
    return safe[:63]


def _candidate_urls(url: str) -> list[str]:
    """
    Build an ordered, de-duplicated list of SSE endpoints to try.

    The PortSwigger MCP server exposes its SSE stream at `/sse`. Accept either a
    bare base URL (http://127.0.0.1:9876 or …/) or the full …/sse URL from the
    user and always end up trying the correct endpoint.
    """
    url = (url or "").strip()
    if not url:
        return []
    base = url.rstrip("/")
    candidates = [url]
    if not base.endswith("/sse"):
        candidates.append(base + "/sse")
    # De-dupe while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for u in candidates:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ── The bridge ──────────────────────────────────────────────────────────────────

class BurpMCP:
    """Singleton bridge to the Burp Suite MCP server (thread-safe, sync API)."""

    def __init__(self) -> None:
        self.url: str = BURP_MCP_URL
        # `enabled` == "auto-connect on each agent turn". Persisted in the shared
        # settings table so the web toggle survives restarts and stays in sync
        # with the CLI. Defaults to the config/env value only if never set.
        self.enabled: bool = self._load_auto_connect()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: "ClientSession | None" = None
        self._connected = threading.Event()
        self._closing: asyncio.Event | None = None
        self._lock = threading.Lock()

        self._tools: list[dict] = []        # cached MCP tool schemas
        self._tool_names: set[str] = set()  # sanitized gemini names
        self._name_map: dict[str, str] = {} # gemini name -> real MCP tool name
        self._last_error: str = ""
        self._connected_url: str = ""       # the URL that actually connected

    @staticmethod
    def _load_auto_connect() -> bool:
        """Read the persisted auto-connect toggle; fall back to config default."""
        try:
            from agent2.database import get_setting
            val = get_setting("burp_auto_connect")
            if val is not None:
                return str(val).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            pass
        return BURP_MCP_ENABLED

    def set_auto_connect(self, on: bool) -> None:
        """Enable/disable auto-connect and persist it (shared web + CLI)."""
        self.enabled = bool(on)
        try:
            from agent2.database import set_setting
            set_setting("burp_auto_connect", "1" if on else "0")
        except Exception:
            pass

    # ── connection lifecycle ────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Thread target: owns the asyncio loop for the whole connection."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        finally:
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None

    async def _serve(self) -> None:
        """Open SSE + MCP session, list tools, then idle until close requested."""
        self._closing = asyncio.Event()
        candidates = _candidate_urls(self.url)
        last_exc: BaseException | None = None
        for candidate in candidates:
            try:
                async with sse_client(candidate) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self._session = session
                        self._connected_url = candidate
                        await self._refresh_tools_async(session)
                        self._connected.set()
                        await self._closing.wait()   # keep the session open
                        return
            except Exception as exc:
                last_exc = exc
                # Try the next candidate URL (e.g. base -> base/sse).
                continue
        if last_exc is not None:
            self._last_error = _root_cause(last_exc)
        self._session = None
        self._connected.set()  # unblock any waiter; is_connected() re-checks

    async def _refresh_tools_async(self, session: "ClientSession") -> None:
        resp = await session.list_tools()
        tools: list[dict] = []
        names: set[str] = set()
        name_map: dict[str, str] = {}
        for t in resp.tools:
            gname = _sanitize_name(t.name)
            schema = getattr(t, "inputSchema", None) or {}
            tools.append({
                "real_name": t.name,
                "name": gname,
                "description": (t.description or t.name),
                "schema": schema,
            })
            names.add(gname)
            name_map[gname] = t.name
        self._tools = tools
        self._tool_names = names
        self._name_map = name_map

    def connect(self, timeout: float = 12.0) -> tuple[bool, str]:
        """Establish the connection (idempotent). Returns (ok, message)."""
        if not _MCP_AVAILABLE:
            return False, (
                "The `mcp` package is not installed. Run "
                "`python run.py --reset` (or `pip install mcp`) and retry."
            )
        with self._lock:
            if self.is_connected():
                return True, f"Already connected to Burp MCP at {self.url}"

            # Clean up a dead thread if any
            if self._thread and not self._thread.is_alive():
                self._thread = None

            self._last_error = ""
            self._connected.clear()

            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run_loop, name="burp-mcp", daemon=True
                )
                self._thread.start()

        # Wait (outside the lock) for _serve to signal ready or fail
        ok = self._connected.wait(timeout=timeout)
        if not ok:
            return False, (
                f"Timed out connecting to Burp MCP at {self.url}. "
                "Is Burp running with the MCP server BApp enabled?"
            )
        if self._session is not None:
            return True, (
                f"Connected to Burp MCP at {self.url} — "
                f"{len(self._tools)} tool(s) available."
            )
        err = self._last_error or "unknown error"
        return False, (
            f"Could not connect to Burp MCP at {self.url}: {err}\n"
            "Open Burp → Extensions → MCP tab → tick 'Enabled', then retry."
        )

    def disconnect(self) -> None:
        with self._lock:
            loop, closing = self._loop, self._closing
            if loop and closing and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(closing.set)
                except Exception:
                    pass
            self._session = None
            self._connected.clear()
            self._tools, self._tool_names, self._name_map = [], set(), {}
            self._connected_url = ""
            self._thread = None

    def is_connected(self) -> bool:
        return self._session is not None and self._loop is not None

    # ── tool discovery ──────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        return list(self._tools)

    def is_burp_tool(self, name: str) -> bool:
        return name in self._tool_names

    def gemini_declarations(self) -> list:
        """Build google.genai FunctionDeclaration objects for every Burp tool."""
        if _gtypes is None or not self._tools:
            return []
        decls = []
        for t in self._tools:
            try:
                params = _json_schema_to_gemini(t["schema"])
                # Gemini rejects an OBJECT schema with no properties; give it a stub.
                if params is not None and getattr(params, "properties", None) in (None, {}):
                    if getattr(params, "type", None) == _gtypes.Type.OBJECT:
                        params = None
                decls.append(_gtypes.FunctionDeclaration(
                    name=t["name"],
                    description=f"[Burp Suite] {t['description']}"[:1024],
                    parameters=params,
                ))
            except Exception as exc:
                self._last_error = f"decl {t['name']}: {exc}"
        return decls

    def provider_tool_schemas(self) -> list[dict]:
        """
        Provider-agnostic tool schemas (name / description / JSON-Schema params)
        for custom OpenAI- or Anthropic-compatible providers. Mirrors the Gemini
        declarations so a custom model gets the same Burp tools.
        """
        out: list[dict] = []
        for t in self._tools:
            schema = t.get("schema") or {}
            if not isinstance(schema, dict) or schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            out.append({
                "name": t["name"],
                "description": f"[Burp Suite] {t['description']}"[:1024],
                "parameters": schema,
            })
        return out

    # ── tool invocation ─────────────────────────────────────────────────────────

    def call_tool(self, name: str, args: dict, timeout: float = 60.0) -> dict:
        """Invoke a Burp MCP tool synchronously. Returns a normalised dict."""
        if not self.is_connected():
            return {"error": "Not connected to Burp MCP. Ask the agent to connect first."}
        real = self._name_map.get(name, name)
        loop = self._loop
        if loop is None:
            return {"error": "Burp MCP loop is not running."}
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self._call_async(real, args or {}), loop
            )
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            fut.cancel()
            return {"error": (
                f"Burp tool '{real}' timed out after {timeout:.0f}s. "
                "Burp may be waiting on the target or the request is slow."
            )}
        except Exception as exc:
            detail = _root_cause(exc) or exc.__class__.__name__
            return {"error": f"Burp tool '{real}' failed: {detail}"}

    async def _call_async(self, real_name: str, args: dict) -> dict:
        session = self._session
        if session is None:
            return {"error": "Burp MCP session closed."}
        result = await session.call_tool(real_name, args)
        # Flatten MCP content blocks into plain text for the model.
        chunks: list[str] = []
        for block in (getattr(result, "content", None) or []):
            text = getattr(block, "text", None)
            if text is not None:
                chunks.append(text)
            else:
                chunks.append(str(block))
        out = "\n".join(chunks) if chunks else ""
        if getattr(result, "isError", False):
            return {"error": out or "Burp reported an error", "tool": real_name}
        return {"output": out, "tool": real_name, "success": True}

    # ── status (for UI / CLI) ───────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "connected": self.is_connected(),
            "url": self._connected_url or self.url,
            "tool_count": len(self._tools),
            "tools": [t["real_name"] for t in self._tools],
            "mcp_installed": _MCP_AVAILABLE,
            "last_error": self._last_error,
        }


# Module-level singleton — import this everywhere.
burp = BurpMCP()
