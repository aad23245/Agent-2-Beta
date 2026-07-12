# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/agent.py
───────────────
Core agent logic:
  - system_prompt(): builds the full system instruction (platform rules +
    memories + user rules)
  - build_context(): assembles the last N messages into Gemini Content objects
  - run_agent(): the main agentic loop (LLM → tool call → stream → loop)
"""

import base64
import threading
import uuid

import google.genai as genai
from google.genai import types

from agent2.config import (
    OS_NAME, SHELL_LABEL,
    MODELS, MODES, DEFAULT_MODEL, DEFAULT_MODE,
    MAX_CTX_MESSAGES, MAX_TOOL_OUTPUT, MAX_AGENT_ITERS,
)
from agent2.database import qall, qone, exe
from agent2.keys import rotator
from agent2.terminal import stream_command, make_stop, clear_stop
from agent2.burp_mcp import burp
from agent2.tools import dispatch_tool, _build_tools

# Local (non-shell, non-Burp) tools dispatched via agent2.tools.dispatch_tool
_LOCAL_TOOLS = {
    "read_file", "write_file", "web_search", "save_memory", "emit_plan",
    "scan_project", "multi_edit_files", "list_dir", "delete_file",
    "grep_search", "update_todo",
}


def _tool_label(name: str, args: dict) -> str:
    if name == "read_file":        return f"Reading {args.get('path','?')}"
    if name == "write_file":       return f"Writing {args.get('path','?')}"
    if name == "scan_project":     return f"Scanning {args.get('path','?')}"
    if name == "list_dir":         return f"Listing {args.get('path','?')}"
    if name == "delete_file":      return f"Deleting {args.get('path','?')}"
    if name == "grep_search":      return f"Searching /{args.get('pattern','?')}/"
    if name == "web_search":       return f"Web search: {args.get('query','?')}"
    if name == "save_memory":      return "Saving memory"
    if name == "emit_plan":        return f"Planning: {args.get('title','?')}"
    if name == "update_todo":      return "Updating task list"
    if name == "multi_edit_files":
        edits = args.get("edits", [])
        n = len(edits) if isinstance(edits, list) else "?"
        return f"Editing {n} file(s)"
    return name


def _tool_result_summary(name: str, result: dict) -> str:
    if "error" in result:
        return f"Error: {result['error']}"
    if name == "write_file" and result.get("success"):
        return f"Wrote {result.get('path','?')} ({result.get('lines',0)} lines)"
    if name == "read_file":
        return result.get("content", "")
    if name == "list_dir":
        return f"{result.get('count',0)} entries:\n" + "\n".join(result.get("entries", []))
    if name == "grep_search":
        return f"{result.get('match_count',0)} matches:\n" + "\n".join(result.get("matches", []))
    if name == "scan_project":
        return f"Scanned {result.get('file_count',0)} files\n{result.get('file_tree','')}"
    if name == "update_todo":
        rows = "\n".join(f"[{'x' if t['status']=='completed' else ' '}] {t['task']}"
                         for t in result.get("todos", []))
        return f"Progress {result.get('progress','')}\n{rows}"
    if name == "multi_edit_files":
        return result.get("results", "done")
    if name == "delete_file":
        return f"Deleted {result.get('deleted','?')}"
    return str(result)[:2000]

# ── Tool declaration ───────────────────────────────────────────────────────────

_TOOL = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="run_command",
        description=(
            f"Execute a shell command on the user's {OS_NAME} machine ({SHELL_LABEL}). "
            "Use for: running scripts, port scanning, network recon, file operations, "
            "installing packages, building/compiling, testing, launching programs. "
            f"Triggers: run, execute, scan, install, build, test, compile, launch, save, start."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "command": types.Schema(
                    type=types.Type.STRING,
                    description=f"Exact {SHELL_LABEL} command for {OS_NAME}",
                ),
                "description": types.Schema(
                    type=types.Type.STRING,
                    description="One-line human-readable description of what this command does",
                ),
            },
            required=["command", "description"],
        ),
    )
])


# ── System prompt ──────────────────────────────────────────────────────────────

def _platform_rules() -> str:
    if OS_NAME == "Windows":
        return (
            "PLATFORM: Windows.\n"
            "- Shell: CMD / PowerShell. Use Windows syntax only.\n"
            "- ipconfig (not ifconfig) | dir (not ls) | type (not cat)\n"
            "- python (not python3) | pip (not pip3)\n"
            "- ping -n 4 (not ping -c 4) | Paths use backslash: C:\\Users\\\n"
            "- nmap.exe if installed; winget or choco for packages"
        )
    if OS_NAME == "Darwin":
        return (
            "PLATFORM: macOS / zsh.\n"
            "- ifconfig for network | brew for packages\n"
            "- python3, pip3 | open <file> to launch"
        )
    return (
        "PLATFORM: Linux / bash.\n"
        "- ip addr or ifconfig | apt/dnf/pacman for packages\n"
        "- python3, pip3"
    )


def system_prompt(burp_connected: bool = False, burp_tool_count: int = 0) -> str:
    """Build the full system prompt including platform rules, memories and rules."""
    sp = f"""You are Agent2 — an elite AUTONOMOUS AI software engineer and security agent, on par with Claude Code. You do not just advise — you BUILD, EDIT, RUN, and VERIFY, using tools, until the task is fully done.

{_platform_rules()}

## AUTONOMY — finish the whole task in one go
- For any non-trivial request, FIRST call `update_todo` with the full step list, then work through it, marking each item `in_progress` → `completed` as you go. Keep the list current.
- Do NOT stop to ask permission between steps. Chain tool calls: explore → plan → create files → run → fix errors → verify. Only return your final text when the task is genuinely complete.
- Build ENTIRE projects from a single prompt: create the full directory structure and EVERY file with `write_file`, install deps and run the project with `run_command`, then confirm it works.
- If a command fails, read the error, fix the cause, and re-run — autonomously. Iterate until green.

## TOOLS (use them — never just print code and stop)
- `update_todo` — live task checklist; call first for multi-step work, update as you progress
- `scan_project` / `list_dir` / `grep_search` / `read_file` — explore before editing
- `write_file` — create/overwrite files (ACTUALLY write code to disk)
- `multi_edit_files` — precise find/replace across many files
- `delete_file` — remove files/dirs when refactoring
- `run_command` — execute shell commands: installs, builds, tests, scans, launches
- `web_search` — docs, CVEs, errors, latest info
- `save_memory` — persist important facts
- `emit_plan` — show a plan for a complex task

## CODE QUALITY
- Production-quality, complete, runnable code — no TODO stubs or placeholders
- Correct project structure, dependency files, and a README when building projects
- Fenced code blocks with language tags in explanations

## SECURITY WORK
- Full pentest workflows via run_command (nmap, sqlmap, nikto, gobuster, etc.)
- When Burp is connected, drive it via the `burp_*` tools

## RESPONSE STYLE
- Markdown: headers, **bold**, tables, code blocks
- Concise but complete — no filler. Summarise what you built and how to run it."""

    mems  = qall("SELECT content FROM memories ORDER BY created_at")
    rules = qall("SELECT content FROM rules WHERE active=1 ORDER BY created_at")
    if burp_connected:
        sp += (
            "\n\n## BURP SUITE (live — via MCP)\n"
            f"You are connected to a running Burp Suite instance and have {burp_tool_count} "
            "Burp tools available, all prefixed `burp_` (e.g. proxy history, Repeater, "
            "Intruder, active/passive Scanner, site map, send raw HTTP request, issues).\n"
            "- When the user asks anything about intercepted traffic, requests/responses, "
            "scanning a target, replaying/modifying a request, or their Burp session, CALL the "
            "relevant `burp_*` tool instead of run_command or guessing.\n"
            "- Prefer Burp tools over shell tools for HTTP interception, request replay and web "
            "vulnerability scanning; use run_command for OS-level tools (nmap, sqlmap, etc.).\n"
            "- After a Burp tool returns, summarise findings clearly (endpoints, params, issues)."
        )
    if mems:
        sp += "\n\n## MEMORIES (always apply):\n" + "\n".join(f"- {m['content']}" for m in mems)
    if rules:
        sp += "\n\n## CUSTOM RULES (follow strictly):\n" + "\n".join(f"- {r['content']}" for r in rules)
    return sp


# ── Context builder ────────────────────────────────────────────────────────────

def build_context(chat_id: str) -> list[types.Content]:
    """Fetch the last MAX_CTX_MESSAGES rows and convert to Gemini Content objects."""
    rows = qall(
        "SELECT id, role, content, meta FROM messages "
        "WHERE chat_id=? ORDER BY created_at DESC LIMIT ?",
        (chat_id, MAX_CTX_MESSAGES),
    )
    rows.reverse()

    import json
    ctx: list[types.Content] = []
    for i, r in enumerate(rows):
        role, content = r["role"], r["content"]
        meta = json.loads(r.get("meta") or "{}")

        if role == "user":
            ctx.append(types.Content(role="user", parts=[types.Part(text=content)]))

        elif role == "assistant":
            ctx.append(types.Content(role="model", parts=[types.Part(text=content)]))

        elif role == "tool_call":
            # Only include if the next row is its result (keeps the pair intact)
            if i + 1 < len(rows) and rows[i + 1]["role"] == "tool_result":
                burp_name = meta.get("burp")
                local_name = meta.get("local")
                if burp_name:
                    ctx.append(types.Content(
                        role="model",
                        parts=[types.Part(function_call=types.FunctionCall(
                            name=burp_name, args=meta.get("args", {})))],
                    ))
                elif local_name:
                    ctx.append(types.Content(
                        role="model",
                        parts=[types.Part(function_call=types.FunctionCall(
                            name=local_name, args=meta.get("args", {})))],
                    ))
                else:
                    args = meta.get("args", {"command": meta.get("cmd", ""), "description": content})
                    ctx.append(types.Content(
                        role="model",
                        parts=[types.Part(function_call=types.FunctionCall(name="run_command", args=args))],
                    ))

        elif role == "tool_result":
            burp_name = meta.get("burp")
            local_name = meta.get("local")
            if burp_name:
                ctx.append(types.Content(
                    role="user",
                    parts=[types.Part(function_response=types.FunctionResponse(
                        name=burp_name,
                        response={"output": content[:MAX_TOOL_OUTPUT],
                                  "success": meta.get("rc", 0) == 0},
                    ))],
                ))
            elif local_name:
                ctx.append(types.Content(
                    role="user",
                    parts=[types.Part(function_response=types.FunctionResponse(
                        name=local_name,
                        response={"output": content[:MAX_TOOL_OUTPUT],
                                  "success": meta.get("ok", True)},
                    ))],
                ))
            else:
                ctx.append(types.Content(
                    role="user",
                    parts=[types.Part(function_response=types.FunctionResponse(
                        name="run_command",
                        response={
                            "output":     content[:MAX_TOOL_OUTPUT],
                            "returncode": meta.get("rc", 0),
                            "success":    meta.get("rc", 0) == 0,
                        },
                    ))],
                ))
    return ctx


# ── DB helpers ─────────────────────────────────────────────────────────────────

def save_msg(chat_id: str, role: str, content: str, meta: dict | None = None) -> None:
    import json
    exe(
        "INSERT INTO messages(id, chat_id, role, content, meta) VALUES(?,?,?,?,?)",
        (str(uuid.uuid4()), chat_id, role, content, json.dumps(meta or {})),
    )
    exe("UPDATE chats SET updated_at=datetime('now') WHERE id=?", (chat_id,))


# ── Agent loop ─────────────────────────────────────────────────────────────────

def run_agent(
    chat_id:     str,
    user_message: str,
    sid:         str,
    term_id:     str,
    model_key:   str,
    mode_key:    str,
    socketio,                    # passed in to avoid circular import
    attachments: list | None = None,
) -> None:
    """
    Main agentic loop.
    1. Saves the user message.
    2. Calls Gemini with the full context.
    3. If the response contains a tool call → run the command, feed result back.
    4. If the response is text → emit to client and return.
    Supports stop events and message editing.
    """
    stop = make_stop(sid)

    # ── Persist user message ─────────────────────────────────────────────────
    save_msg(chat_id, "user", user_message,
             {"attachments": [a["name"] for a in (attachments or [])]})

    # Auto-title on first message
    chat = qone("SELECT title FROM chats WHERE id=?", (chat_id,))
    if chat and chat["title"] == "New Chat":
        title = user_message.strip().replace("\n", " ")[:50]
        if len(user_message) > 50:
            title += "…"
        exe("UPDATE chats SET title=? WHERE id=?", (title, chat_id))
        socketio.emit("chat_titled", {"chat_id": chat_id, "title": title}, room=sid)

    exe("UPDATE chats SET model=?, mode=? WHERE id=?", (model_key, mode_key, chat_id))

    # ── Get API client ────────────────────────────────────────────────────────
    client, key, key_label = rotator.get()
    if not client:
        msg = (
            "**No API keys configured.**\n\n"
            "Open **Keys** in the sidebar to add a Gemini API key.\n"
            "Free key: https://aistudio.google.com/app/apikey"
        )
        save_msg(chat_id, "assistant", msg)
        socketio.emit("chat_response", {"text": msg, "done": True, "tokens": 0}, room=sid)
        clear_stop(sid)
        return

    # ── Resolve model / mode ──────────────────────────────────────────────────
    mode_cfg  = MODES.get(mode_key, MODES[DEFAULT_MODE])
    model_cfg = MODELS.get(model_key) or MODELS.get(DEFAULT_MODEL) or list(MODELS.values())[0]
    api_model = model_cfg["api"]
    model_group = model_cfg.get("group", "")

    # ── Build generation config ───────────────────────────────────────────────
    # Lazily bring up the Burp MCP bridge so its tools can be offered to Gemini.
    # Auto-connect only when the user enabled it; but ALWAYS offer Burp tools if
    # a session is live (e.g. connected manually via the Burp settings tab).
    burp_decls: list = []
    if burp.enabled and not burp.is_connected():
        ok, bmsg = burp.connect(timeout=8.0)
        socketio.emit("toast",
                      {"msg": bmsg, "type": "success" if ok else "warning"},
                      room=sid)
    if burp.is_connected():
        burp_decls = burp.gemini_declarations()

    agent_tools = [_build_tools()]
    if burp_decls:
        agent_tools.append(types.Tool(function_declarations=burp_decls))

    cfg_kwargs: dict = dict(
        system_instruction=system_prompt(burp_connected=bool(burp_decls),
                                         burp_tool_count=len(burp_decls)),
        tools=agent_tools,
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="AUTO")
        ),
    )
    if mode_cfg.get("max_tokens", 0) > 0:
        cfg_kwargs["max_output_tokens"] = mode_cfg["max_tokens"]

    if mode_cfg.get("thinking") and mode_cfg.get("thinking_budget", 0) > 0:
        if model_group in ("2.5", "3.1"):
            try:
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=mode_cfg["thinking_budget"]
                )
            except Exception:
                pass   # model doesn't support thinking → skip silently

    cfg = types.GenerateContentConfig(**cfg_kwargs)

    # ── Build context from DB ─────────────────────────────────────────────────
    context = build_context(chat_id)

    # ── Inject file attachments into the last user turn ───────────────────────
    if attachments:
        file_parts: list[types.Part] = []
        for att in attachments:
            try:
                raw = base64.b64decode(att["data"])
                mt  = att.get("mime_type", "text/plain")
                is_text = mt.startswith("text/") or mt in (
                    "application/json", "application/xml",
                    "application/javascript", "application/yaml",
                )
                if is_text:
                    file_parts.append(types.Part(
                        text=f"\n\n[Attached: {att['name']}]\n```\n"
                             f"{raw.decode('utf-8', 'replace')[:8000]}\n```"
                    ))
                else:
                    file_parts.append(types.Part(
                        inline_data=types.Blob(mime_type=mt, data=raw)
                    ))
            except Exception as exc:
                file_parts.append(types.Part(text=f"[Attachment error: {att['name']}: {exc}]"))

        if context and context[-1].role == "user":
            context[-1] = types.Content(
                role="user",
                parts=list(context[-1].parts) + file_parts,
            )
        else:
            context.append(types.Content(role="user", parts=file_parts))

    total_tokens = 0

    # ── Main loop ─────────────────────────────────────────────────────────────
    for iteration in range(MAX_AGENT_ITERS):

        # Check if user requested stop
        if stop.is_set():
            socketio.emit("chat_response",
                          {"text": "_Stopped by user._", "done": True, "tokens": total_tokens},
                          room=sid)
            clear_stop(sid)
            return

        # ── Call Gemini ───────────────────────────────────────────────────────
        try:
            resp = client.models.generate_content(
                model=api_model, contents=context, config=cfg
            )
        except Exception as exc:
            es = str(exc)
            is_quota    = "429" in es or "quota" in es.lower() or "exhausted" in es.lower()
            is_model_err = any(k in es.lower() for k in ("not found", "invalid", "unsupported"))
            rotator.fail(key, quota=is_quota)

            if is_quota:
                c2, k2, l2 = rotator.get()
                if c2 and k2 != key:
                    client, key, key_label = c2, k2, l2
                    socketio.emit("toast",
                                  {"msg": f"Quota hit — switched to key #{l2}", "type": "warning"},
                                  room=sid)
                    continue   # retry with new key

            hint = (
                f"\n\n> Model `{api_model}` may not be available on your key tier. "
                "Try **2.5 Flash**."
                if is_model_err else ""
            )
            msg = f"**API Error ({api_model}):** {es}{hint}"
            save_msg(chat_id, "assistant", msg)
            socketio.emit("chat_response", {"text": msg, "done": True, "tokens": total_tokens}, room=sid)
            clear_stop(sid)
            return

        # ── Parse response safely ─────────────────────────────────────────────
        try:
            candidate = resp.candidates[0] if resp.candidates else None
            if candidate is None or candidate.content is None:
                finish = getattr(candidate, "finish_reason", "UNKNOWN") if candidate else "NO_CANDIDATE"
                msg = (
                    f"**Empty response** (finish_reason: `{finish}`).\n\n"
                    "The model returned no content. Try rephrasing your message "
                    "or switching to a different model."
                )
                save_msg(chat_id, "assistant", msg)
                socketio.emit("chat_response", {"text": msg, "done": True, "tokens": total_tokens}, room=sid)
                clear_stop(sid)
                return
            parts = candidate.content.parts or []
        except (IndexError, AttributeError) as exc:
            msg = f"**Response parse error:** {exc}. Try again or switch models."
            save_msg(chat_id, "assistant", msg)
            socketio.emit("chat_response", {"text": msg, "done": True, "tokens": total_tokens}, room=sid)
            clear_stop(sid)
            return

        # ── Extract text + function call ──────────────────────────────────────
        func_call: types.FunctionCall | None = None
        texts: list[str] = []
        for p in parts:
            try:
                if p.function_call and p.function_call.name:
                    func_call = p.function_call
                elif p.text:
                    texts.append(p.text)
            except Exception:
                pass  # skip malformed parts

        # ── Token accounting ──────────────────────────────────────────────────
        try:
            tok = getattr(resp.usage_metadata, "total_token_count", 0) or 0
        except Exception:
            tok = 0
        total_tokens += tok
        if tok and key_label:
            rotator.record_usage(key_label, tok)
            socketio.emit("key_usage_update",
                          {"label": key_label, "tokens": tok, "keys": rotator.status()},
                          room=sid)
        socketio.emit("token_update", {"chat_id": chat_id, "tokens": total_tokens}, room=sid)

        # ── Handle tool call ──────────────────────────────────────────────────
        if func_call and func_call.name == "run_command":
            if stop.is_set():
                socketio.emit("chat_response",
                              {"text": "_Stopped by user._", "done": True, "tokens": total_tokens},
                              room=sid)
                clear_stop(sid)
                return

            args = dict(func_call.args)
            cmd  = args.get("command", "")
            desc = args.get("description", "Running…")

            save_msg(chat_id, "tool_call", desc, {"args": args, "cmd": cmd})
            socketio.emit("chat_tool_call",
                          {"command": cmd, "description": desc,
                           "shell": SHELL_LABEL, "tool": "run_command"},
                          room=sid)

            output, rc = stream_command(cmd, sid, term_id, socketio)
            save_msg(chat_id, "tool_result", output[:10_000], {"rc": rc, "cmd": cmd})
            # Shell output already streams live into the terminal pane; no chat
            # result block needed (that would duplicate it).

            context.append(types.Content(
                role="model",
                parts=[types.Part(function_call=types.FunctionCall(name="run_command", args=args))],
            ))
            context.append(types.Content(
                role="user",
                parts=[types.Part(function_response=types.FunctionResponse(
                    name="run_command",
                    response={
                        "output":     output[:MAX_TOOL_OUTPUT],
                        "returncode": rc,
                        "success":    rc == 0,
                    },
                ))],
            ))
            # Continue loop → Gemini will now summarise the output

        # ── Handle local file / utility tools (read, write, list, grep, todo…) ─
        elif func_call and func_call.name in _LOCAL_TOOLS:
            if stop.is_set():
                socketio.emit("chat_response",
                              {"text": "_Stopped by user._", "done": True, "tokens": total_tokens},
                              room=sid)
                clear_stop(sid)
                return

            tname = func_call.name
            targs = dict(func_call.args)
            desc  = _tool_label(tname, targs)

            save_msg(chat_id, "tool_call", desc, {"args": targs, "local": tname})
            socketio.emit("chat_tool_call",
                          {"command": f"{tname}(…)", "description": desc,
                           "shell": "tool", "tool": tname},
                          room=sid)

            result = dispatch_tool(tname, targs)
            summary = _tool_result_summary(tname, result)
            save_msg(chat_id, "tool_result", summary[:10_000],
                     {"ok": "error" not in result, "local": tname})
            socketio.emit("chat_tool_result",
                          {"tool": tname, "summary": summary[:2000],
                           "ok": "error" not in result}, room=sid)

            context.append(types.Content(
                role="model",
                parts=[types.Part(function_call=types.FunctionCall(name=tname, args=targs))],
            ))
            context.append(types.Content(
                role="user",
                parts=[types.Part(function_response=types.FunctionResponse(
                    name=tname,
                    response={k: (str(v)[:MAX_TOOL_OUTPUT] if isinstance(v, str) else v)
                              for k, v in result.items()},
                ))],
            ))
            # Continue loop → model uses the tool result

        # ── Handle Burp Suite MCP tool call ───────────────────────────────────
        elif func_call and burp.is_burp_tool(func_call.name):
            if stop.is_set():
                socketio.emit("chat_response",
                              {"text": "_Stopped by user._", "done": True, "tokens": total_tokens},
                              room=sid)
                clear_stop(sid)
                return

            bname = func_call.name
            bargs = dict(func_call.args)
            desc  = f"Burp: {bname}"

            save_msg(chat_id, "tool_call", desc, {"args": bargs, "burp": bname})
            socketio.emit("chat_tool_call",
                          {"command": f"{bname}({', '.join(f'{k}={v}' for k, v in bargs.items())[:200]})",
                           "description": desc, "shell": "Burp MCP", "tool": bname},
                          room=sid)

            b_result = burp.call_tool(bname, bargs)
            b_out    = b_result.get("output") or b_result.get("error") or "(no output)"
            save_msg(chat_id, "tool_result", str(b_out)[:10_000],
                     {"rc": 0 if b_result.get("success") else 1, "burp": bname})
            socketio.emit("chat_tool_result",
                          {"tool": bname, "summary": str(b_out)[:2000],
                           "ok": bool(b_result.get("success"))}, room=sid)

            context.append(types.Content(
                role="model",
                parts=[types.Part(function_call=types.FunctionCall(name=bname, args=bargs))],
            ))
            context.append(types.Content(
                role="user",
                parts=[types.Part(function_response=types.FunctionResponse(
                    name=bname,
                    response={k: str(v)[:MAX_TOOL_OUTPUT] if isinstance(v, str) else v
                              for k, v in b_result.items()},
                ))],
            ))
            # Continue loop → Gemini will now use the Burp result

        # ── Final text response ───────────────────────────────────────────────
        else:
            final = "\n".join(texts) or "Done."
            save_msg(chat_id, "assistant", final)
            socketio.emit("chat_response", {"text": final, "done": True, "tokens": total_tokens}, room=sid)
            clear_stop(sid)
            return

    # Exceeded max iterations
    msg = f"Agent reached max iterations ({MAX_AGENT_ITERS})."
    save_msg(chat_id, "assistant", msg)
    socketio.emit("chat_response", {"text": msg, "done": True, "tokens": total_tokens}, room=sid)
    clear_stop(sid)
