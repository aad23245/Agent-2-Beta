# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/config.py
────────────────
All constants: platform detection, model definitions, mode definitions,
tuneable limits, and shared root paths.
"""

import os
import shutil
import platform as _plat
from pathlib import Path

# ── Root paths ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent   # project root (where run.py lives)
DB   = ROOT / "agent2.db"
ENV  = ROOT / ".env"

# ── Auto-load .env (must run before anything imports os.environ values) ────────
if ENV.exists():
    for _line in ENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── Platform ───────────────────────────────────────────────────────────────────
OS_NAME = _plat.system()   # "Windows" | "Darwin" | "Linux"
IS_WIN  = OS_NAME == "Windows"
IS_MAC  = OS_NAME == "Darwin"


def detect_shell() -> tuple[str, str, str]:
    """Return (shell_bin, shell_label, shell_flag)."""
    if IS_WIN:
        ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if ps:
            return ps, "PowerShell", "-Command"
        return "cmd.exe", "CMD", "/c"
    user_shell = os.environ.get("SHELL", "")
    for sh in [user_shell, "/bin/bash", "/bin/zsh", "/bin/sh"]:
        if sh and shutil.which(sh):
            return sh, Path(sh).name.upper(), "-c"
    return "/bin/sh", "SH", "-c"


SHELL_BIN, SHELL_LABEL, SHELL_FLAG = detect_shell()


def shell_argv(cmd: str) -> list[str]:
    """Build subprocess argv for the current platform."""
    if IS_WIN and SHELL_BIN.lower().endswith("cmd.exe"):
        return ["cmd.exe", "/c", cmd]
    return [SHELL_BIN, SHELL_FLAG, cmd]


# ── Models ─────────────────────────────────────────────────────────────────────
MODELS: dict[str, dict] = {
        "2.5-flash":      {"api": "gemini-2.5-flash",      "label": "2.5 Flash",      "group": "2.5"},
    "2.5-pro":        {"api": "gemini-2.5-pro",        "label": "2.5 Pro",        "group": "2.5"},
    "3.1-flash":      {"api": "gemini-3.1-flash",      "label": "3.1 Flash",      "group": "3.1"},
    "3.1-pro":        {"api": "gemini-3.1-pro",        "label": "3.1 Pro",        "group": "3.1"},
}
DEFAULT_MODEL = "2.5-flash"

# ── Modes ──────────────────────────────────────────────────────────────────────
MODES: dict[str, dict] = {
    "fast": {
        "label": "Fast", "icon": "⚡",
        "desc": "Fastest responses, lowest token usage",
        "max_tokens": 2048, "thinking": False, "thinking_budget": 0,
    },
    "pro": {
        "label": "Pro", "icon": "★",
        "desc": "Balanced — recommended for most tasks",
        "max_tokens": 8192, "thinking": False, "thinking_budget": 0,
    },
    "thinking": {
        "label": "Thinking", "icon": "🧠",
        "desc": "Deep reasoning via extended thinking (2.5/3.1 models only)",
        "max_tokens": 16384, "thinking": True, "thinking_budget": 8000,
    },
}
DEFAULT_MODE = "pro"

# ── Agent limits ───────────────────────────────────────────────────────────────
MAX_CTX_MESSAGES = 20       # max messages sent to Gemini per turn
MAX_TOOL_OUTPUT  = 3_000    # max chars from a command that go back into context
MAX_AGENT_ITERS  = 10       # max tool-call iterations per user message
