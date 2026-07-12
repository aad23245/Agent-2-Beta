# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/provider_agent.py
────────────────────────
Autonomous agent loop for CUSTOM providers (OpenAI- or Anthropic-compatible).

Mirrors agent2/agent.py's run_agent() behaviour (same tools, same DB, same
Socket.IO events) but talks to a user-registered endpoint via agent2.providers
instead of the Gemini SDK. Tool execution is shared (agent2.tools.dispatch_tool
for local tools, terminal.stream_command for shell, burp for Burp tools), so a
custom model gets the exact same capabilities as Gemini.
"""

from __future__ import annotations

import json

from agent2.config import SHELL_LABEL, MAX_AGENT_ITERS, MAX_TOOL_OUTPUT
from agent2.database import qall, qone, exe
from agent2 import providers
from agent2.tools import dispatch_tool
from agent2.terminal import stream_command, make_stop, clear_stop
from agent2.burp_mcp import burp
from agent2.agent import (
    system_prompt, save_msg, _LOCAL_TOOLS, _tool_label, _tool_result_summary,
)

SHELL_TOOL = "run_command"


def _tool_meta(name: str, args: dict) -> dict:
    """Metadata for a saved tool_call/result row so the Web UI renders it right."""
    if name == SHELL_TOOL:
        return {"args": args, "cmd": args.get("command", "")}
    if burp.is_burp_tool(name):
        return {"args": args, "burp": name}
    return {"args": args, "local": name}


def _tool_desc(name: str, args: dict) -> str:
    """Human-readable description for a saved tool_call row."""
    if name == SHELL_TOOL:
        return args.get("description", "Running…")
    return _tool_label(name, args)


def _exec_tool(name: str, args: dict, sid: str, term_id: str, socketio) -> tuple[str, bool]:
    """Run any tool by name; return (result_text, ok)."""
    if name == SHELL_TOOL:
        cmd = args.get("command", "")
        desc = args.get("description", "Running…")
        socketio.emit("chat_tool_call",
                      {"command": cmd, "description": desc,
                       "shell": SHELL_LABEL, "tool": "run_command"}, room=sid)
        out, rc = stream_command(cmd, sid, term_id, socketio)
        # Shell output already streams into the terminal pane; don't duplicate it
        # as a chat result block.
        return out[:MAX_TOOL_OUTPUT], rc == 0

    if burp.is_burp_tool(name):
        socketio.emit("chat_tool_call",
                      {"command": f"{name}(…)", "description": f"Burp: {name}",
                       "shell": "Burp MCP", "tool": name}, room=sid)
        r = burp.call_tool(name, args)
        out = (r.get("output") or r.get("error") or "")
        ok = "error" not in r
        socketio.emit("chat_tool_result",
                      {"tool": name, "summary": str(out)[:2000], "ok": ok}, room=sid)
        return str(out)[:MAX_TOOL_OUTPUT], ok

    if name in _LOCAL_TOOLS:
        socketio.emit("chat_tool_call",
                      {"command": f"{name}(…)", "description": _tool_label(name, args),
                       "shell": "tool", "tool": name}, room=sid)
        r = dispatch_tool(name, args)
        summary = _tool_result_summary(name, r)
        ok = "error" not in r
        socketio.emit("chat_tool_result",
                      {"tool": name, "summary": summary[:2000], "ok": ok}, room=sid)
        return summary[:MAX_TOOL_OUTPUT], ok

    return f"Unknown tool: {name}", False


def run_provider_agent(chat_id, user_message, sid, term_id, pid, socketio,
                       attachments=None) -> None:
    """Main loop for a custom provider. `pid` is the provider id (after 'custom:')."""
    stop = make_stop(sid)
    save_msg(chat_id, "user", user_message,
             {"attachments": [a["name"] for a in (attachments or [])]})

    chat = qone("SELECT title FROM chats WHERE id=?", (chat_id,))
    if chat and chat["title"] == "New Chat":
        title = user_message.strip().replace("\n", " ")[:50]
        exe("UPDATE chats SET title=? WHERE id=?", (title, chat_id))
        socketio.emit("chat_titled", {"chat_id": chat_id, "title": title}, room=sid)

    prov = providers.get_provider(pid)
    if not prov:
        msg = "**Custom provider not found.** It may have been removed."
        save_msg(chat_id, "assistant", msg)
        socketio.emit("chat_response", {"text": msg, "done": True, "tokens": 0}, room=sid)
        clear_stop(sid)
        return

    fmt = prov.get("format", "openai")

    # Lazily bring up the Burp MCP bridge so its tools are offered to the
    # custom provider too (parity with the Gemini loop).
    if burp.enabled and not burp.is_connected():
        ok, bmsg = burp.connect(timeout=8.0)
        socketio.emit("toast", {"msg": bmsg, "type": "success" if ok else "warning"}, room=sid)

    system = system_prompt(burp_connected=burp.is_connected(),
                           burp_tool_count=len(burp.list_tools()))

    # Build message history from DB (plain user/assistant text is enough to seed;
    # tool round-trips within THIS turn are kept in the provider-native format).
    history = qall(
        "SELECT role, content FROM messages WHERE chat_id=? "
        "AND role IN ('user','assistant') ORDER BY created_at DESC LIMIT 20",
        (chat_id,))
    history.reverse()
    messages: list[dict] = [{"role": h["role"] if h["role"] == "user" else "assistant",
                             "content": h["content"]} for h in history]

    total_tokens = 0
    for _ in range(MAX_AGENT_ITERS):
        if stop.is_set():
            socketio.emit("chat_response", {"text": "_Stopped by user._", "done": True,
                                            "tokens": total_tokens}, room=sid)
            clear_stop(sid)
            return
        try:
            result = providers.chat(prov, messages, system)
        except Exception as exc:
            msg = f"**Provider error ({prov.get('model_id')}):** {exc}"
            save_msg(chat_id, "assistant", msg)
            socketio.emit("chat_response", {"text": msg, "done": True, "tokens": total_tokens}, room=sid)
            clear_stop(sid)
            return

        total_tokens += result.get("tokens", 0) or 0
        socketio.emit("token_update", {"chat_id": chat_id, "tokens": total_tokens}, room=sid)
        tool_calls = result.get("tool_calls") or []

        if not tool_calls:
            final = result.get("text") or "Done."
            save_msg(chat_id, "assistant", final)
            socketio.emit("chat_response", {"text": final, "done": True, "tokens": total_tokens}, room=sid)
            clear_stop(sid)
            return

        # Append the assistant turn (native format) then execute each tool.
        if fmt == "anthropic":
            messages.append({"role": "assistant", "content": result.get("raw_content", [])})
            tool_results = []
            for tc in tool_calls:
                out, ok = _exec_tool(tc["name"], tc["args"], sid, term_id, socketio)
                save_msg(chat_id, "tool_call", _tool_desc(tc["name"], tc["args"]),
                         _tool_meta(tc["name"], tc["args"]))
                save_msg(chat_id, "tool_result", out[:10_000],
                         {**_tool_meta(tc["name"], tc["args"]), "ok": ok})
                tool_results.append({"type": "tool_result", "tool_use_id": tc["id"],
                                     "content": out or "(no output)"})
            messages.append({"role": "user", "content": tool_results})
        else:  # openai
            messages.append(result.get("raw_assistant") or
                            {"role": "assistant", "content": result.get("text", ""),
                             "tool_calls": [
                                 {"id": tc["id"], "type": "function",
                                  "function": {"name": tc["name"],
                                               "arguments": json.dumps(tc["args"])}}
                                 for tc in tool_calls]})
            for tc in tool_calls:
                out, ok = _exec_tool(tc["name"], tc["args"], sid, term_id, socketio)
                save_msg(chat_id, "tool_call", _tool_desc(tc["name"], tc["args"]),
                         _tool_meta(tc["name"], tc["args"]))
                save_msg(chat_id, "tool_result", out[:10_000],
                         {**_tool_meta(tc["name"], tc["args"]), "ok": ok})
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": out or "(no output)"})

    msg = f"Agent reached max iterations ({MAX_AGENT_ITERS})."
    save_msg(chat_id, "assistant", msg)
    socketio.emit("chat_response", {"text": msg, "done": True, "tokens": total_tokens}, room=sid)
    clear_stop(sid)
