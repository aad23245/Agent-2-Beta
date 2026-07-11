# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/terminal.py
──────────────────
Interactive terminal process management:
  - stream_command(): spawn a shell command, stream output via WebSocket
  - stdin injection: send input to a waiting process
  - kill: terminate a running process
  - stop events: allow the agent loop to be cancelled mid-flight
"""

import os
import threading
import subprocess

from agent2.config import ROOT, SHELL_LABEL, shell_argv

# ── Shared state (module-level singletons, imported where needed) ──────────────

# sid -> { term_id -> {"proc": Popen, "lock": Lock} }
_procs: dict[str, dict] = {}

# sid -> threading.Event  (set = stop requested)
_stop_events: dict[str, threading.Event] = {}

_procs_lock = threading.Lock()


# ── Process store helpers ──────────────────────────────────────────────────────

def store_proc(sid: str, term_id: str, proc: subprocess.Popen) -> None:
    with _procs_lock:
        if sid not in _procs:
            _procs[sid] = {}
        _procs[sid][term_id] = {"proc": proc, "lock": threading.Lock()}


def get_proc(sid: str, term_id: str) -> dict | None:
    return _procs.get(sid, {}).get(term_id)


def del_proc(sid: str, term_id: str) -> None:
    with _procs_lock:
        _procs.get(sid, {}).pop(term_id, None)


def cleanup_sid(sid: str) -> None:
    """Remove all proc entries for a disconnected session."""
    with _procs_lock:
        _procs.pop(sid, None)


# ── Stop-event helpers (used by agent.py) ─────────────────────────────────────

def make_stop(sid: str) -> threading.Event:
    ev = threading.Event()
    _stop_events[sid] = ev
    return ev


def stop_agent(sid: str) -> None:
    ev = _stop_events.get(sid)
    if ev:
        ev.set()


def clear_stop(sid: str) -> None:
    _stop_events.pop(sid, None)


# ── Main command runner ────────────────────────────────────────────────────────

def stream_command(
    command: str,
    sid: str,
    term_id: str,
    socketio,           # passed in to avoid circular import
) -> tuple[str, int]:
    """
    Run *command* in the platform shell.
    Stream each output line via WebSocket to the given sid.
    Returns (full_output, returncode).
    """
    output: list[str] = []
    argv = shell_argv(command)

    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=os.environ.copy(),
            cwd=str(ROOT),
        )
        store_proc(sid, term_id, proc)

        socketio.emit("terminal_start", {
            "command": command,
            "shell":   SHELL_LABEL,
            "term_id": term_id,
        }, room=sid)
        socketio.emit("proc_started", {"term_id": term_id}, room=sid)

        for line in proc.stdout:
            socketio.emit("terminal_line", {
                "data":    line.rstrip("\n"),
                "term_id": term_id,
            }, room=sid)
            output.append(line)

        proc.wait()
        del_proc(sid, term_id)

        socketio.emit("terminal_done", {
            "returncode": proc.returncode,
            "term_id":    term_id,
        }, room=sid)
        socketio.emit("proc_ended", {"term_id": term_id}, room=sid)

        return "".join(output), proc.returncode

    except Exception as exc:
        err = str(exc)
        del_proc(sid, term_id)
        socketio.emit("terminal_line", {"data": f"[error] {err}", "term_id": term_id}, room=sid)
        socketio.emit("terminal_done", {"returncode": -1, "term_id": term_id}, room=sid)
        socketio.emit("proc_ended", {"term_id": term_id}, room=sid)
        return err, -1


# ── Stdin injection ────────────────────────────────────────────────────────────

def send_stdin(sid: str, term_id: str, text: str, socketio) -> None:
    entry = get_proc(sid, term_id)
    if not entry:
        socketio.emit("terminal_line", {
            "data": "[no active process to send input to]",
            "term_id": term_id,
        }, room=sid)
        return
    try:
        with entry["lock"]:
            entry["proc"].stdin.write(text + "\n")
            entry["proc"].stdin.flush()
        socketio.emit("terminal_line", {
            "data": f"[stdin] {text}",
            "term_id": term_id,
        }, room=sid)
    except Exception as exc:
        socketio.emit("terminal_line", {
            "data": f"[stdin error] {exc}",
            "term_id": term_id,
        }, room=sid)


# ── Kill ───────────────────────────────────────────────────────────────────────

def kill_proc(sid: str, term_id: str) -> None:
    entry = get_proc(sid, term_id)
    if entry:
        try:
            entry["proc"].terminate()
        except Exception:
            pass
