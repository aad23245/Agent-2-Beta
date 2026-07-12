#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2cli.py  —  Agent 2 CLI
────────────────────────────
Run via:  python run.py --cli
      or: venv/bin/python agent2cli.py   (after run.py setup)

Keys are stored in agent2.db (shared with the Web UI). No .env is used.
Key rotation: if one key hits quota, the next is tried automatically.

Commands:
  /help          show all commands
  /addapi        add an API key to the database
  /model [name]  switch model
  /mode  [name]  switch mode (fast | pro | thinking)
  /clear         clear conversation (start fresh)
  /shrink        summarize & shrink history manually
  /scan <path>   scan and analyze entire project
  /history       show recent messages
  /clearhistory  clear message history
  /memory        list saved memories
  /addmem <txt>  add a memory
  /run <cmd>     run a shell command directly
  /read <file>   read a file
  /search <q>    web search
  /exit          quit
"""

import os, sys, re, json, shutil, threading, time, platform
import subprocess, urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime

# ── Locate project root ────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.resolve()
DATA_DIR = Path.home() / ".agent2"
HST_FILE = DATA_DIR / "history.json"
MEM_FILE = DATA_DIR / "memories.json"
PT_HISTORY = DATA_DIR / "cli_history.txt"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Windows console fixes ──────────────────────────────────────────────────────
OS_NAME = platform.system()
IS_WIN  = OS_NAME == "Windows"
IS_MAC  = OS_NAME == "Darwin"

if IS_WIN:
    os.system("chcp 65001 >nul 2>&1")
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception: pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

# ── Rich (installed by run.py) ─────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich import box as rbox
    _RICH = True
    _con  = Console(highlight=False)
except ImportError:
    _RICH = False
    _con  = None

# ── prompt_toolkit ─────────────────────────────────────────────────────────────
# Imported lazily/optionally so commands like `--help` and `/addapi` can still
# work on a fresh machine before optional CLI dependencies are installed.
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.styles import Style
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    _PTK = True
    _PTK_IMPORT_ERROR = None
except ImportError as ex:
    PromptSession = FileHistory = HTML = Style = None
    Application = KeyBindings = Layout = HSplit = Window = FormattedTextControl = None
    Completer = Completion = AutoSuggestFromHistory = None
    _PTK = False
    _PTK_IMPORT_ERROR = ex

# ── Gemini ─────────────────────────────────────────────────────────────────────
# Do not exit at import time. This lets `python agent2cli.py --help` work even
# when google-genai has not been installed yet. Agent calls validate this later.
try:
    import google.genai as genai
    from google.genai import types as gtypes
    _GENAI = True
    _GENAI_IMPORT_ERROR = None
except ImportError as ex:
    genai = None
    gtypes = None
    _GENAI = False
    _GENAI_IMPORT_ERROR = ex

# ── Burp Suite MCP bridge (optional) ───────────────────────────────────────────
try:
    from agent2.burp_mcp import burp as _burp
    _BURP_OK = True
except Exception as _bex:
    _burp = None
    _BURP_OK = False
    _BURP_IMPORT_ERROR = _bex

# ── Shared SQLite store (memories + rules are shared with the Web UI) ───────────
# The CLI and Web UI use the SAME agent2.db so memories/rules stay in sync across
# both modes. Falls back to a local JSON file only if the DB is unavailable.
try:
    from agent2.database import qall as _db_qall, exe as _db_exe, init_db as _db_init
    from agent2.database import (
        list_api_keys as _db_list_keys,
        add_api_key   as _db_add_key,
        remove_api_key as _db_remove_key,
    )
    _db_init()
    _DB_OK = True
except Exception as _dbex:
    _db_qall = _db_exe = _db_init = None
    _db_list_keys = _db_add_key = _db_remove_key = None
    _DB_OK = False
    _DB_IMPORT_ERROR = _dbex

# Shared agent iteration cap (keeps CLI provider loop consistent with the Web UI).
try:
    from agent2.config import MAX_AGENT_ITERS
except Exception:
    MAX_AGENT_ITERS = 40

# ── ANSI colour helpers (theme-driven) ─────────────────────────────────────────
R  = "\033[0m"; B  = "\033[1m"; D  = "\033[2m"

# The palette below is MUTABLE — /theme and /color rewrite these module globals
# at runtime, and every helper reads them lazily, so a theme change is instant.
# 256-colour codes keep it portable across terminals.
PU = "\033[38;5;135m"; CY = "\033[38;5;81m";  GR = "\033[38;5;83m"
YW = "\033[38;5;221m"; RD = "\033[38;5;203m"; WH = "\033[38;5;255m"
MG = "\033[38;5;177m"

# Hex accent used by the Rich-rendered paths and the prompt_toolkit prompt.
ACCENT  = "#7c6af7"
ACCENT2 = "#60b8ff"

# ── Theme presets ───────────────────────────────────────────────────────────────
# Each preset defines the primary accent (PU), secondary (CY/MG) and matching
# hex accents for Rich + the prompt line. GR/YW/RD/WH stay semantic (success /
# warning / error / bright) across themes for consistent status cues.
THEMES: dict[str, dict] = {
    "purple": {"label": "Purple (default)", "PU": "135", "CY": "81",  "MG": "177",
               "accent": "#7c6af7", "accent2": "#60b8ff"},
    "emerald":{"label": "Emerald",          "PU": "48",  "CY": "43",  "MG": "84",
               "accent": "#2ee6a6", "accent2": "#3ddc84"},
    "ocean":  {"label": "Ocean",            "PU": "39",  "CY": "45",  "MG": "81",
               "accent": "#3aa0ff", "accent2": "#60d0ff"},
    "amber":  {"label": "Amber",            "PU": "214", "CY": "221", "MG": "215",
               "accent": "#ff9f43", "accent2": "#f0c060"},
    "rose":   {"label": "Rose",             "PU": "205", "CY": "211", "MG": "218",
               "accent": "#ff5c8a", "accent2": "#ff8fb0"},
    "mono":   {"label": "Monochrome",       "PU": "250", "CY": "244", "MG": "252",
               "accent": "#cccccc", "accent2": "#999999"},
}
DEFAULT_THEME = "purple"

# A few named accent colours for /color (overrides just the primary accent).
ACCENT_CHOICES = {
    "purple": ("135", "#7c6af7"), "blue":  ("39",  "#3aa0ff"),
    "green":  ("48",  "#2ee6a6"), "cyan":  ("45",  "#22d3ee"),
    "amber":  ("214", "#ff9f43"), "orange":("208", "#ff7a1a"),
    "red":    ("203", "#ff5555"), "pink":  ("205", "#ff5c8a"),
    "teal":   ("43",  "#14b8a6"), "white": ("255", "#ffffff"),
}

_CURRENT_THEME = DEFAULT_THEME


def apply_theme(name: str, persist: bool = True) -> bool:
    """Repaint the palette from a preset. Returns True if applied."""
    global PU, CY, MG, ACCENT, ACCENT2, _CURRENT_THEME
    t = THEMES.get(name)
    if not t:
        return False
    PU = f"\033[38;5;{t['PU']}m"
    CY = f"\033[38;5;{t['CY']}m"
    MG = f"\033[38;5;{t['MG']}m"
    ACCENT  = t["accent"]
    ACCENT2 = t["accent2"]
    _CURRENT_THEME = name
    if persist and _DB_OK:
        try:
            from agent2.database import set_setting
            set_setting("cli_theme", name)
            set_setting("cli_accent", "")   # theme wins → clear standalone accent
        except Exception:
            pass
    return True


def apply_accent(name_or_code: str, persist: bool = True) -> bool:
    """Override just the primary accent colour (PU + hex). Returns True if applied."""
    global PU, ACCENT, MG
    key = (name_or_code or "").strip().lower()
    if key in ACCENT_CHOICES:
        code, hexv = ACCENT_CHOICES[key]
    elif key.isdigit() and 0 <= int(key) <= 255:
        code, hexv = key, ACCENT   # keep hex accent; recolour ANSI primary
    else:
        return False
    PU = f"\033[38;5;{code}m"
    MG = f"\033[38;5;{code}m"
    ACCENT = hexv
    if persist and _DB_OK:
        try:
            from agent2.database import set_setting
            set_setting("cli_accent", key)
        except Exception:
            pass
    return True


def load_theme_from_settings() -> None:
    """Restore the saved theme + accent override at startup (best-effort)."""
    if not _DB_OK:
        return
    try:
        from agent2.database import get_setting
        theme = (get_setting("cli_theme") or "").strip()
        accent = (get_setting("cli_accent") or "").strip()
        if theme in THEMES:
            apply_theme(theme, persist=False)
        if accent:
            apply_accent(accent, persist=False)
    except Exception:
        pass


def _p(col, text): return f"{col}{text}{R}"
def ok(t):   return _p(GR, t)
def warn(t): return _p(YW, t)
def err(t):  return _p(RD, t)
def dim(t):  return _p(D,  t)
def pu(t):   return _p(PU, t)
def cy(t):   return _p(CY, t)

# ── Platform ───────────────────────────────────────────────────────────────────
def detect_shell():
    if IS_WIN:
        ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if ps: return ps, "PowerShell", "-Command"
        return "cmd.exe", "CMD", "/c"
    sh = os.environ.get("SHELL", "")
    for s in [sh, "/bin/bash", "/bin/zsh", "/bin/sh"]:
        if s and shutil.which(s):
            return s, Path(s).name.upper(), "-c"
    return "/bin/sh", "SH", "-c"

SHELL_BIN, SHELL_LABEL, SHELL_FLAG = detect_shell()

def shell_argv(cmd: str) -> list:
    if IS_WIN and SHELL_BIN.lower().endswith("cmd.exe"):
        return ["cmd.exe", "/c", cmd]
    return [SHELL_BIN, SHELL_FLAG, cmd]

# ── Models & modes ─────────────────────────────────────────────────────────────
MODELS = {
        "2.5-flash":      "gemini-2.5-flash",
    "2.5-pro":        "gemini-2.5-pro",
    "3.1-flash":      "gemini-3.1-flash",
    "3.1-pro":        "gemini-3.1-pro",
}
DEFAULT_MODEL = "2.5-flash"

MODES = {
    "fast":     {"icon": "⚡", "max_tokens": 2048,  "thinking": False},
    "pro":      {"icon": "★",  "max_tokens": 8192,  "thinking": False},
    "thinking": {"icon": "🧠", "max_tokens": 16384, "thinking": True, "thinking_budget": 8000},
}
DEFAULT_MODE = "pro"

# ── API key management (stored in agent2.db — no .env) ─────────────────────────
def load_keys() -> list[dict]:
    """Return list of {key, label, active, errs} from the database."""
    if not (_DB_OK and _db_list_keys):
        return []
    keys, seen = [], set()
    placeholder = "your_gemini_api_key_here"
    try:
        for rec in _db_list_keys():
            v = (rec.get("api_key") or "").strip()
            if v and v != placeholder and len(v) > 10 and v not in seen:
                keys.append({"key": v, "label": str(rec.get("label") or ""),
                             "active": bool(rec.get("active", 1)), "errs": 0})
                seen.add(v)
    except Exception:
        pass
    return keys

def save_key_to_env(new_key: str) -> tuple[bool, str]:
    """Add a new key to the database. Returns (success, label_or_reason)."""
    if not (_DB_OK and _db_add_key):
        return False, "database unavailable"
    reason_map = {"too_short": "key too short", "already_exists": "already exists"}
    ok, info = _db_add_key(new_key)
    return (ok, info if ok else reason_map.get(info, info))

# ── Key rotator (in-memory, seeded from .env) ──────────────────────────────────
class KeyRotator:
    _lock = threading.Lock()

    def __init__(self):
        self._entries: list[dict] = []
        self._pinned: str | None = None   # label pinned as the preferred key
        self.reload()
        self.load_pin()

    def reload(self):
        with self._lock:
            self._entries = load_keys()

    def pin(self, label: str | None):
        """Pin a key label as preferred (None = auto-rotate). Persisted."""
        with self._lock:
            self._pinned = label
            if label:
                for e in self._entries:
                    if e["label"] == label:
                        e["active"] = True
                        e["errs"] = 0
        if _DB_OK:
            try:
                from agent2.database import set_setting
                set_setting("cli_pinned_key", label or "")
            except Exception:
                pass

    def load_pin(self):
        """Restore a previously pinned label from settings (call once at start)."""
        if not _DB_OK:
            return
        try:
            from agent2.database import get_setting
            lbl = (get_setting("cli_pinned_key") or "").strip()
        except Exception:
            lbl = ""
        with self._lock:
            self._pinned = lbl or None

    def get(self) -> tuple:
        """Return (client, raw_key, label) — pinned key first, else first active."""
        if not _GENAI:
            return None, None, None
        with self._lock:
            if self._pinned:
                for e in self._entries:
                    if e["label"] == self._pinned and e["active"]:
                        return genai.Client(api_key=e["key"]), e["key"], e["label"]
            active = [e for e in self._entries if e["active"]]
            if not active:
                # reset all and retry once
                for e in self._entries:
                    e["active"] = True
                    e["errs"] = 0
                active = self._entries
            if not active:
                return None, None, None
            e = active[0]
            return genai.Client(api_key=e["key"]), e["key"], e["label"]

    def fail(self, key: str, quota: bool = False):
        with self._lock:
            for e in self._entries:
                if e["key"] == key:
                    e["errs"] += 1
                    if quota or e["errs"] >= 3:
                        e["active"] = False
                    break

    def next_active(self, current_key: str) -> tuple:
        """After a failure, get the next different active key."""
        if not _GENAI:
            return None, None, None
        with self._lock:
            active = [e for e in self._entries if e["active"] and e["key"] != current_key]
            if not active:
                return None, None, None
            e = active[0]
            return genai.Client(api_key=e["key"]), e["key"], e["label"]

    def status(self) -> list[dict]:
        with self._lock:
            return [{"label": e["label"], "preview": e["key"][:14] + "…",
                     "active": e["active"],
                     "pinned": e["label"] == self._pinned} for e in self._entries]

_rotator = KeyRotator()

# ── Memories (shared with Web UI via agent2.db; JSON fallback) ─────────────────
def load_mems() -> list:
    if _DB_OK:
        try:
            rows = _db_qall("SELECT content, created_at FROM memories ORDER BY created_at")
            return [{"id": str(i), "content": r["content"], "importance": 5,
                     "tags": [], "created": r.get("created_at", "")}
                    for i, r in enumerate(rows)]
        except Exception:
            pass
    if MEM_FILE.exists():
        try: return json.loads(MEM_FILE.read_text(encoding="utf-8"))
        except: pass
    return []

def save_mems(mems: list):
    MEM_FILE.write_text(json.dumps(mems, indent=2, ensure_ascii=False), encoding="utf-8")

def add_mem(content: str, importance: int = 5, tags: list = None):
    content = content.strip()
    if not content:
        return
    if _DB_OK:
        try:
            import uuid as _uuid
            _db_exe("INSERT INTO memories(id, content) VALUES(?, ?)",
                    (str(_uuid.uuid4()), content))
            return
        except Exception:
            pass
    mems = load_mems()
    mems.append({"id": f"{time.time():.0f}", "content": content,
                 "importance": importance, "tags": tags or [],
                 "created": datetime.now().isoformat()})
    save_mems(mems)

def load_rules() -> list:
    """Active custom rules from the shared DB (empty if unavailable)."""
    if _DB_OK:
        try:
            return _db_qall("SELECT content FROM rules WHERE active=1 ORDER BY created_at")
        except Exception:
            pass
    return []

# ── History ────────────────────────────────────────────────────────────────────
def load_history() -> list:
    if HST_FILE.exists():
        try: return json.loads(HST_FILE.read_text(encoding="utf-8"))[-60:]
        except: pass
    return []

def save_history(h: list):
    HST_FILE.write_text(json.dumps(h[-100:], indent=2, ensure_ascii=False), encoding="utf-8")

# ── Terminal width ─────────────────────────────────────────────────────────────
def tw() -> int:
    return min(shutil.get_terminal_size((100, 30)).columns, 120)

# ── Print helpers ──────────────────────────────────────────────────────────────
def hr(char="─", col=D):
    print(f"{col}{char * (tw() - 2)}{R}")

def status_line(msg: str, kind: str = "info"):
    sym  = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "✗"}.get(kind, "•")
    _col = {"info": CY, "success": GR, "warning": YW, "error": RD}.get(kind, D)
    if _RICH:
        style = {"info":"#60b8ff","success":"#3ddc84","warning":"#f0c060","error":"#ff5555"}.get(kind,"dim")
        _con.print(f"  [{style}]{sym}[/] {msg}")
    else:
        print(f"  {_col}{sym}{R} {msg}")

def print_banner():
    os.system("cls" if IS_WIN else "clear")
    if _RICH:
        title = Text()
        title.append("  ⚡ ", style="bold yellow")
        title.append("Agent 2 CLI", style=f"bold {ACCENT}")
        title.append(f"  {OS_NAME}/{SHELL_LABEL}", style="dim")
        _con.print(Panel(title, border_style="#1e1e30", padding=(0, 1)))
    else:
        w = min(tw(), 56)
        print(f"{PU}{'═' * w}{R}")
        print(f"{PU}{B}  ⚡ Agent 2 CLI{R}  {D}{OS_NAME}/{SHELL_LABEL}{R}")
        print(f"{PU}{'═' * w}{R}")
    print()

# ── Slash-command registry (single source for help + autocomplete) ─────────────
# (command_token, base_command, description). base_command is what the completer
# inserts and what the dispatcher matches; command_token is the display form.
SLASH_COMMANDS: list[tuple[str, str, str]] = [
    ("/help",            "/help",         "Show this help"),
    ("/addapi",          "/addapi",       "Add a Gemini API key (saved to agent2.db)"),
    ("/keys",            "/keys",         "Activate a key/provider with ↑/↓ (Gemini + custom; shows model id)"),
    ("/model [name]",    "/model",        "Pick a model with ↑/↓ (built-ins + custom providers)"),
    ("/mode [name]",     "/mode",         "Pick a mode with ↑/↓ (fast ⚡ | pro ★ | thinking 🧠)"),
    ("/theme",           "/theme",        "Pick a colour theme with ↑/↓ (purple, emerald, ocean, …)"),
    ("/color",           "/color",        "Set the accent colour with ↑/↓"),
    ("/provider …",      "/provider",     "Manage custom API providers (add | list | use | del | test)"),
    ("/burp …",          "/burp",         "Connect to Burp Suite MCP (connect | list | status)"),
    ("/scan <path>",     "/scan",         "Scan and analyze an entire project directory"),
    ("/run <cmd>",       "/run",          "Run a shell command directly"),
    ("/read <file>",     "/read",         "Read a file's contents"),
    ("/search <query>",  "/search",       "Web search (DuckDuckGo)"),
    ("/memory",          "/memory",       "List all saved memories"),
    ("/addmem <text>",   "/addmem",       "Save a memory manually"),
    ("/history",         "/history",      "Show last 10 messages"),
    ("/shrink",          "/shrink",       "Summarize & shrink history to save tokens"),
    ("/clear",           "/clear",        "Clear the screen (keeps history)"),
    ("/clearhistory",    "/clearhistory", "Clear the conversation history"),
    ("/exit",            "/exit",         "Quit  (also Ctrl+C)"),
]


def print_help():
    cmds = [(tok, desc) for tok, _base, desc in SLASH_COMMANDS]
    if _RICH:
        t = Table(show_header=True, header_style=f"bold {ACCENT}",
                  box=rbox.SIMPLE_HEAD, border_style="dim")
        t.add_column("Command",     style=ACCENT2, no_wrap=True)
        t.add_column("Description", style="#c4c4dc")
        for cmd, desc in cmds:
            t.add_row(cmd, desc)
        _con.print(t)
        _con.print(f"  [dim]Tip: type [/][{ACCENT2}]/[/][dim] to see suggestions; keep typing to filter. "
                   f"While the agent works, type a message + Enter to queue it.[/]")
    else:
        print(f"\n{PU}{B}  Commands:{R}")
        for cmd, desc in cmds:
            print(f"  {CY}{cmd:<28}{R}{D}{desc}{R}")
        print(f"  {D}Tip: type / for suggestions; keep typing to filter. "
              f"Type during a turn to queue the next message.{R}")
        print()

def print_agent_reply(text: str):
    """Render agent markdown reply."""
    if _RICH:
        hr_style = "#1e1e30"
        _con.rule(style=hr_style)
        _con.print(f"  [bold {ACCENT}]⚡ Agent 2[/]  [dim]{datetime.now().strftime('%H:%M')}[/]")
        _con.print()
        _con.print(Markdown(text), style="#c4c4dc")
        _con.print()
    else:
        hr()
        print(f"  {PU}{B}⚡ Agent 2{R}  {D}{datetime.now().strftime('%H:%M')}{R}")
        print()
        _render_markdown_plain(text)
        print()

def _render_markdown_plain(text: str):
    in_code = False
    lang    = ""
    for line in text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
            lang = line[3:].strip() if in_code else ""
            if in_code:  print(f"  {D}┌{'─' * 50}{R}")
            else:        print(f"  {D}└{'─' * 50}{R}")
            continue
        if in_code:
            print(f"  {YW}│ {line}{R}"); continue
        if   line.startswith("# "):   print(f"\n  {WH}{B}{line[2:]}{R}")
        elif line.startswith("## "):  print(f"\n  {CY}{B}{line[3:]}{R}")
        elif line.startswith("### "): print(f"  {PU}{line[4:]}{R}")
        elif re.match(r"^[-*] ", line): print(f"  {D}•{R} {line[2:]}")
        elif re.match(r"^\d+\. ", line):
            n, rest = line.split(". ", 1); print(f"  {PU}{n}.{R} {rest}")
        else:
            line = re.sub(r"\*\*(.+?)\*\*", f"{WH}{B}\\1{R}", line)
            line = re.sub(r"`(.+?)`",        f"{YW}\\1{R}", line)
            print(f"  {line}")

def print_tool_call(name: str, desc: str, detail: str = ""):
    icons = {"run_command":"⚙️","read_file":"📄","write_file":"✏️",
             "scan_project":"🔍","multi_edit_files":"✂️",
             "web_search":"🌐","save_memory":"🧠","emit_plan":"📋"}
    icon = icons.get(name, "🔧")
    if _RICH:
        body = Text()
        body.append(f" {icon} ", style="bold")
        body.append(name, style="bold #f0c060")
        body.append(f"  {desc}", style="dim")
        if detail: body.append(f"\n   $ {detail}", style="#f0c060")
        _con.print(Panel(body, border_style="#2a2a40", padding=(0, 1)))
    else:
        print(f"\n  {YW}▶ {name}{R}  {D}{desc}{R}")
        if detail: print(f"  {YW}$ {detail}{R}")

def print_plan(title: str, steps: list):
    if _RICH:
        body = Text()
        body.append(f"{title}\n\n", style="bold white")
        for i, s in enumerate(steps, 1):
            body.append(f"  {i}. ", style="bold #7c6af7")
            body.append(f"{s}\n",   style="#c4c4dc")
        _con.print(Panel(body, title="[bold #7c6af7]📋 Plan[/]",
                         border_style="#3a2a70", padding=(0, 1)))
    else:
        print(f"\n  {PU}{B}📋 {title}{R}")
        for i, s in enumerate(steps, 1):
            print(f"  {PU}{i}.{R} {s}")
        print()

# ── Esc Interrupter ────────────────────────────────────────────────────────────
import _thread

class EscInterrupter:
    """Listens for ESC key in the background to interrupt processing."""
    def __init__(self):
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._listen, daemon=True)

    def start(self):
        self._t.start()

    def stop(self):
        self._stop.set()

    def _listen(self):
        if not IS_WIN: return
        import msvcrt
        while not self._stop.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch in (b'\x1b', b'\x03'):  # ESC or Ctrl+C
                    _thread.interrupt_main()
                    break
            time.sleep(0.05)


class InputController:
    """Runs while the agent is working. It lets the user:
      • press ESC to interrupt the current turn, AND
      • TYPE A MESSAGE + Enter to QUEUE it — it runs as soon as the
        current turn (and any earlier queued messages) finish.

    Queued messages are collected in a thread-safe list drained by the main
    loop. Fully cross-platform: msvcrt on Windows, select() on POSIX. Degrades
    to a no-op if stdin isn't an interactive TTY.
    """
    def __init__(self):
        self._stop  = threading.Event()
        self._queue: list[str] = []
        self._qlock = threading.Lock()
        self._t = threading.Thread(target=self._listen, daemon=True)
        try:
            self._tty = sys.stdin.isatty()
        except Exception:
            self._tty = False

    def start(self):
        if self._tty:
            self._t.start()

    def stop(self):
        self._stop.set()

    def drain(self) -> list[str]:
        with self._qlock:
            msgs = self._queue[:]
            self._queue.clear()
        return msgs

    def _enqueue(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        with self._qlock:
            self._queue.append(text)
        try:
            status_line(f"queued — will run after the current turn: {text[:60]}", "info")
        except Exception:
            pass

    def _listen(self):
        try:
            if IS_WIN:
                self._listen_win()
            else:
                self._listen_posix()
        except Exception:
            pass

    def _listen_win(self):
        import msvcrt
        buf: list[str] = []
        while not self._stop.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):            # arrow/fn key → swallow the code char
                    if msvcrt.kbhit():
                        msvcrt.getwch()
                    continue
                if ch in ("\x1b", "\x03"):            # ESC / Ctrl+C → interrupt
                    _thread.interrupt_main(); break
                if ch in ("\r", "\n"):                # Enter → queue the line
                    self._enqueue("".join(buf)); buf = []
                elif ch in ("\x08", "\x7f"):          # Backspace
                    if buf: buf.pop()
                elif ch >= " ":
                    buf.append(ch)
            else:
                time.sleep(0.03)

    def _listen_posix(self):
        # Put the TTY in cbreak mode so we get keystrokes immediately (bare ESC
        # included) and can read char-by-char WITHOUT blocking — mirroring the
        # Windows path. termios state is always restored on exit.
        import select
        try:
            import termios, tty
        except Exception:
            termios = tty = None

        fd = None
        saved = None
        try:
            fd = sys.stdin.fileno()
            if tty is not None:
                saved = termios.tcgetattr(fd)
                tty.setcbreak(fd)
        except Exception:
            saved = None

        buf: list[str] = []
        try:
            while not self._stop.is_set():
                try:
                    r, _, _ = select.select([sys.stdin], [], [], 0.2)
                except Exception:
                    return
                if not r:
                    continue
                ch = sys.stdin.read(1)
                if ch == "":                          # EOF
                    return
                if ch in ("\x1b", "\x03"):            # ESC / Ctrl+C → interrupt
                    _thread.interrupt_main(); break
                if ch in ("\r", "\n"):                # Enter → queue the line
                    self._enqueue("".join(buf)); buf = []
                elif ch in ("\x08", "\x7f"):          # Backspace / DEL
                    if buf: buf.pop()
                elif ch >= " ":
                    buf.append(ch)
        finally:
            if fd is not None and saved is not None and termios is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, saved)
                except Exception:
                    pass

# ── Spinner ────────────────────────────────────────────────────────────────────
class Spinner:
    _frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

    def __init__(self, msg: str = "Thinking"):
        self._msg  = msg
        self._stop = threading.Event()
        self._t    = None
        self._prog = None

    def start(self):
        if _RICH:
            self._prog = Progress(SpinnerColumn(), TextColumn("[dim]{task.description}"),
                                  transient=True, console=_con)
            self._prog.start()
            self._prog.add_task(self._msg)
        else:
            self._t = threading.Thread(target=self._spin, daemon=True)
            self._t.start()

    def stop(self):
        self._stop.set()
        if _RICH and self._prog:
            self._prog.stop()
        if self._t:
            self._t.join(timeout=0.5)
        if not _RICH:
            print(f"\r{' ' * (tw() - 2)}\r", end="", flush=True)

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            print(f"\r  {PU}{self._frames[i % len(self._frames)]}{R} {D}{self._msg}…{R}",
                  end="", flush=True)
            i += 1
            time.sleep(0.08)

# ── Run command (streaming) ────────────────────────────────────────────────────
def run_cmd_stream(cmd: str, cwd: str | None = None) -> tuple[str, int]:
    work_dir = str(Path(cwd).expanduser()) if cwd else str(Path.cwd())
    output   = []
    if _RICH:
        _con.print(f"  [dim]$ {cmd}[/]")
    else:
        print(f"  {D}$ {cmd}{R}")
    try:
        proc = subprocess.Popen(
            shell_argv(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, bufsize=1, universal_newlines=True,
            env=os.environ.copy(), cwd=work_dir,
        )
        for line in proc.stdout:
            output.append(line)
            stripped = line.rstrip("\n")
            if _RICH: _con.print(f"  [dim]│[/] {stripped}")
            else:     print(f"  {D}│{R} {stripped}")
        proc.wait()
        rc  = proc.returncode
        sym = "✓" if rc == 0 else "✗"
        col_r = GR if rc == 0 else RD
        if _RICH:
            style = "bold #3ddc84" if rc == 0 else "bold #ff5555"
            _con.print(f"  [{style}]{sym} exit {rc}[/]")
        else:
            print(f"  {col_r}{B}{sym} exit {rc}{R}")
        return "".join(output), rc
    except Exception as ex:
        msg = str(ex)
        if _RICH: _con.print(f"  [bold #ff5555]✗ {msg}[/]")
        else:     print(f"  {RD}✗ {msg}{R}")
        return msg, -1

# ── Tool implementations (same logic as web app) ───────────────────────────────
MAX_FILE = 64_000
_SKIP    = {"__pycache__", ".git", "node_modules", ".venv", "venv", "env",
            "dist", "build", ".next", "target", ".DS_Store"}

def _impl_read(args: dict) -> dict:
    p = Path(args["path"]).expanduser()
    s = args.get("start_line"); e = args.get("end_line")
    try:
        if not p.exists(): return {"error": f"Not found: {p}"}
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        sl, el = (s - 1 if s else 0), (e if e else total)
        content = "".join(lines[sl:el])
        if len(content) > MAX_FILE:
            content = content[:MAX_FILE] + "\n…[truncated]"
        return {"content": content, "total_lines": total, "path": str(p)}
    except Exception as ex: return {"error": str(ex)}

def _impl_write(args: dict) -> dict:
    p = Path(args.get("path", "")).expanduser()
    content = args.get("content", "")
    if not content: return {"error": "content is required"}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        lines = content.count("\n") + 1
        return {"success": True, "path": str(p), "lines": lines}
    except Exception as ex: return {"error": str(ex)}



def _impl_search(args: dict) -> dict:
    q = args.get("query", "")
    try:
        url = "https://api.duckduckgo.com/?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "no_html": "1", "skip_disambig": "1"})
        req = urllib.request.Request(url, headers={"User-Agent": "Agent 2CLI/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        results = []
        if data.get("AbstractText"):
            results.append({"title": data.get("Heading",""), "snippet": data["AbstractText"][:400]})
        for t in data.get("RelatedTopics", [])[:4]:
            if isinstance(t, dict) and t.get("Text"):
                results.append({"title": t["Text"][:80], "snippet": t["Text"][:300]})
        return {"query": q, "results": results[:5]} if results else {"query": q, "results": [], "note": "No results"}
    except Exception as ex:
        return {"error": str(ex), "query": q}

def _impl_save_mem(args: dict) -> dict:
    c = args.get("content", "").strip()
    if not c: return {"error": "content required"}
    imp  = min(10, max(1, int(args.get("importance", 5))))
    tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()]
    add_mem(c, imp, tags)
    return {"saved": True}

def _impl_plan(args: dict) -> dict:
    title = args.get("title", "Plan")
    try:    steps = json.loads(args.get("steps", "[]"))
    except: steps = [args.get("steps", "")]
    print_plan(title, steps)
    return {"plan_emitted": True}


def _impl_scan_project(args: dict) -> dict:
    raw = args.get("path", ".")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    p = p.resolve()
    if not p.exists() or not p.is_dir(): return {"error": f"Invalid directory: {p}"}
    
    important_exts = {".py", ".js", ".html", ".css", ".json", ".md", ".txt", ".ts", ".tsx",
                      ".jsx", ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".rb",
                      ".php", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".env", ".sql",
                      ".sh", ".bat", ".ps1", ".xml", ".svg", ".lock"}
    skip_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
                 ".next", "target", ".DS_Store", ".idea", ".vscode", "coverage", ".cache"}
    
    # Build a file tree first
    tree_lines = [f"Project root: {p}"]
    contents = []
    total_size = 0
    
    import os as _os
    for root, dirs, files in _os.walk(p):
        dirs[:] = sorted([d for d in dirs if d not in skip_dirs])
        level = len(Path(root).relative_to(p).parts)
        indent = "  " * level
        tree_lines.append(f"{indent}{Path(root).name}/")
        for fname in sorted(files):
            fp = Path(root) / fname
            if fp.suffix in important_exts:
                tree_lines.append(f"{indent}  {fname}  ({fp.stat().st_size} bytes)")
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    if len(text) > 50000: text = text[:50000] + "\n...[truncated]"
                    contents.append(f"\n{'='*60}\n FILE: {fp.relative_to(p)}\n{'='*60}\n{text}")
                    total_size += len(text)
                    if total_size > 300000:
                        contents.append("\n--- [TRUNCATED: Project too large, remaining files skipped] ---")
                        break
                except Exception:
                    pass
        if total_size > 300000:
            break
    
    file_tree = "\n".join(tree_lines)
    file_contents = "\n".join(contents) if contents else "No important text files found."
    return {"file_tree": file_tree, "file_count": len(contents), "project_contents": file_contents}

def _impl_multi_edit(args: dict) -> dict:
    edits = args.get("edits", [])
    results = []
    for edit in edits:
        p = Path(edit.get("path", "")).expanduser()
        old_text = edit.get("old_text", "")
        new_text = edit.get("new_text", "")
        if not p.exists():
            results.append(f"{p}: File not found")
            continue
        try:
            c = p.read_text(encoding="utf-8")
            if old_text not in c:
                results.append(f"{p}: old_text not found")
            else:
                p.write_text(c.replace(old_text, new_text), encoding="utf-8")
                results.append(f"{p}: Successfully edited")
        except Exception as e:
            results.append(f"{p}: Error {e}")
    return {"results": "\n".join(results)}

def dispatch_tool(name: str, args: dict) -> dict:
    if name == "read_file":        return _impl_read(args)
    if name == "write_file":       return _impl_write(args)
    if name == "web_search":       return _impl_search(args)
    if name == "save_memory":      return _impl_save_mem(args)
    if name == "emit_plan":        return _impl_plan(args)
    if name == "scan_project":     return _impl_scan_project(args)
    if name == "multi_edit_files": return _impl_multi_edit(args)
    if name in ("list_dir", "delete_file", "grep_search", "update_todo"):
        from agent2.tools import dispatch_tool as _shared_dispatch
        return _shared_dispatch(name, args)
    if _BURP_OK and _burp and _burp.is_burp_tool(name):
        return _burp.call_tool(name, args)
    return {"error": f"Unknown tool: {name}"}

# ── Gemini tool declarations ────────────────────────────────────────────────────
def _build_tools():
    S = gtypes.Schema; T = gtypes.Type
    return gtypes.Tool(function_declarations=[
        gtypes.FunctionDeclaration(name="run_command",
            description=f"Execute a shell command on {OS_NAME} ({SHELL_LABEL}). Use for running scripts, installs, scans, builds.",
            parameters=S(type=T.OBJECT, properties={
                "command":     S(type=T.STRING),
                "description": S(type=T.STRING),
                "cwd":         S(type=T.STRING),
            }, required=["command","description"])),
        gtypes.FunctionDeclaration(name="read_file",
            description="Read a file's contents. Always read before editing.",
            parameters=S(type=T.OBJECT, properties={
                "path":       S(type=T.STRING),
                "start_line": S(type=T.INTEGER),
                "end_line":   S(type=T.INTEGER),
            }, required=["path"])),
        gtypes.FunctionDeclaration(name="write_file",
            description="Create or overwrite a file with the given content. Use for creating new files. Parent directories are created automatically.",
            parameters=S(type=T.OBJECT, properties={
                "path":    S(type=T.STRING),
                "content": S(type=T.STRING),
            }, required=["path","content"])),
        gtypes.FunctionDeclaration(name="web_search",
            description="Search the web for CVEs, docs, error messages, latest info.",
            parameters=S(type=T.OBJECT, properties={
                "query":       S(type=T.STRING),
                "max_results": S(type=T.INTEGER),
            }, required=["query"])),
        gtypes.FunctionDeclaration(name="save_memory",
            description="Save an important fact to long-term memory (persists across sessions).",
            parameters=S(type=T.OBJECT, properties={
                "content":    S(type=T.STRING),
                "importance": S(type=T.INTEGER),
                "tags":       S(type=T.STRING),
            }, required=["content"])),
        gtypes.FunctionDeclaration(name="emit_plan",
            description="Show a step-by-step plan before a complex multi-step task.",
            parameters=S(type=T.OBJECT, properties={
                "title": S(type=T.STRING),
                "steps": S(type=T.STRING),
            }, required=["title","steps"])),
        gtypes.FunctionDeclaration(name="scan_project",
            description="Recursively scan a project directory and return a file tree + content of ALL code/config files. Use this AUTOMATICALLY whenever the user mentions a project, asks to check code, add features, or fix bugs. Pass the project path.",
            parameters=S(type=T.OBJECT, properties={
                "path": S(type=T.STRING),
            }, required=["path"])),
        gtypes.FunctionDeclaration(name="multi_edit_files",
            description="Edit multiple files at once by replacing exact text snippets. Each edit has path, old_text (exact match), new_text (replacement). Use for renaming, refactoring, or patching across files.",
            parameters=S(type=T.OBJECT, properties={
                "edits": S(type=T.ARRAY, items=S(type=T.OBJECT, properties={
                    "path": S(type=T.STRING),
                    "old_text": S(type=T.STRING),
                    "new_text": S(type=T.STRING)
                }))
            }, required=["edits"])),
        gtypes.FunctionDeclaration(name="list_dir",
            description="List a directory's files and subfolders. Use to explore project structure before reading/editing.",
            parameters=S(type=T.OBJECT, properties={
                "path": S(type=T.STRING),
            }, required=["path"])),
        gtypes.FunctionDeclaration(name="delete_file",
            description="Delete a file or directory (recursively). Use when refactoring or removing artifacts.",
            parameters=S(type=T.OBJECT, properties={
                "path": S(type=T.STRING),
            }, required=["path"])),
        gtypes.FunctionDeclaration(name="grep_search",
            description="Regex-search file contents across a directory tree. Returns file:line: matches. Use to locate symbols/usages.",
            parameters=S(type=T.OBJECT, properties={
                "pattern": S(type=T.STRING),
                "path":    S(type=T.STRING),
                "glob":    S(type=T.STRING),
            }, required=["pattern"])),
        gtypes.FunctionDeclaration(name="update_todo",
            description="Create/update a live TODO checklist for a multi-step build. Pass the full list each time with each item's status (pending|in_progress|completed). Call FIRST for big tasks, then update as you finish steps.",
            parameters=S(type=T.OBJECT, properties={
                "todos": S(type=T.ARRAY, items=S(type=T.OBJECT, properties={
                    "task":   S(type=T.STRING),
                    "status": S(type=T.STRING),
                }))
            }, required=["todos"])),
    ])

# ── System prompt ──────────────────────────────────────────────────────────────
def build_sys_prompt(burp_tool_count: int = 0) -> str:
    if IS_WIN:
        plat = ("PLATFORM: Windows / CMD+PowerShell\n"
                "ipconfig | dir | type | python | pip | ping -n 4 | winget/choco for packages")
    elif IS_MAC:
        plat = "PLATFORM: macOS / zsh\nifconfig | ls | python3 | pip3 | brew install"
    else:
        plat = "PLATFORM: Linux / bash\nip addr | ls | python3 | pip3 | apt/dnf/pacman"

    mems = load_mems()
    mem_block = ""
    if mems:
        top = sorted(mems, key=lambda x: -x.get("importance", 5))[:20]
        mem_block = "\n\n## MEMORIES:\n" + "\n".join(
            f"- [{m['importance']}/10] {m['content']}" for m in top)

    rules = load_rules()
    rules_block = ""
    if rules:
        rules_block = "\n\n## CUSTOM RULES (follow strictly):\n" + "\n".join(
            f"- {r['content']}" for r in rules)

    burp_block = ""
    if burp_tool_count:
        burp_block = (
            "\n\n## BURP SUITE (live — via MCP)\n"
            f"You are connected to a running Burp Suite instance with {burp_tool_count} Burp "
            "tools available, all prefixed `burp_` (proxy HTTP history, Repeater, Intruder, "
            "active/passive Scanner, site map, send raw HTTP request, scan issues, etc.).\n"
            "- For anything about intercepted traffic, replaying/modifying requests, scanning a "
            "web target, or the user's Burp session, CALL the relevant `burp_*` tool — do not "
            "guess or fall back to run_command.\n"
            "- Use Burp tools for HTTP/web testing; use run_command for OS tools (nmap, sqlmap…).\n"
            "- Summarise Burp results clearly: endpoints, parameters, and any issues found."
        )

    return f"""You are Agent 2 — an elite autonomous AI development and security agent running in a terminal.

{plat}

## YOUR TOOLS (use these — do NOT just print code)
1. **run_command** — Execute any shell command. Translate user intent to platform commands automatically:
   - User says "ls" or "ls -a" on Windows → run `dir` or `dir /a`
   - User says "cat file" on Windows → run `type file`
   - User says "mkdir" → use the correct platform command
   - ALWAYS translate Linux/Mac commands to Windows equivalents and vice versa. NEVER tell the user to "use dir instead" — just DO it.
2. **read_file** — Read a file's contents (optionally specific line range)
3. **write_file** — Create or overwrite a file with content. Use this to ACTUALLY write code to disk. Do NOT just show code in chat — call write_file to create the file.
4. **scan_project** — Recursively scan a project directory. Returns file tree + all source code. Use this AUTOMATICALLY when the user:
   - Says "check my project", "look at my code", "scan this", "add a feature to my project"
   - Mentions any project or codebase by name or path
   - Asks to fix bugs, refactor, or add functionality to existing code
   - You do NOT need the user to type /scan — just call it yourself
5. **multi_edit_files** — Precisely edit multiple files at once using find-and-replace. Each edit: {{path, old_text, new_text}}. Use for renaming, refactoring, or patching across files.
6. **web_search** — Search the web for docs, errors, CVEs, latest info
7. **save_memory** — Persist important facts across sessions
8. **emit_plan** — Show a step-by-step plan before complex tasks (3+ steps)

## CRITICAL RULES
- **NEVER just show code in chat and expect the user to copy-paste it.** Always use `write_file` to create files and `multi_edit_files` to edit existing files. You are an AGENT — you DO things, not just suggest things.
- **When creating a project** (e.g. "make an e-commerce site"), use `emit_plan` first, then `write_file` for EVERY file. Create proper directory structure. Write ALL the code to disk.
- **When editing a project**, use `scan_project` first to understand the full codebase (language, framework, DB, structure), then use `multi_edit_files` or `write_file` to make changes.
- **When fixing bugs or testing**, `scan_project` first, deeply analyze all files for logic errors, security vulnerabilities (XSS, SQLi, CSRF, etc.), and edge cases, then fix them using `multi_edit_files`. You have full cybersecurity analysis capabilities.
- **When asked to perform security testing**, use `run_command` to execute tools like nmap, sqlmap, nikto, or write custom testing scripts to verify vulnerabilities.
- **When user asks to "shrink memory/history"**, that is handled by the /shrink command — tell them to use `/shrink`.
- **Translate commands automatically.** If user says `ls`, run `dir`. If user says `cat`, run `type`. NEVER refuse or say "you should use X instead" — just run the right command.
- **Task with 3+ steps** → call `update_todo` FIRST to lay out the checklist, then update each item's status as you finish it so the user sees live progress.

## WORKING LIKE A SENIOR ENGINEER (Claude-Code discipline)
- **Plan → act → verify.** For any non-trivial task: (1) `update_todo` with the steps, (2) do the work, (3) VERIFY it actually works by running it — do not claim success without evidence.
- **Explore before you edit.** Use `list_dir`, `grep_search`, and `read_file` to understand conventions (naming, structure, libraries already in use) and MATCH them. Never introduce a new framework when the repo already uses one.
- **Read before you write.** Always `read_file` before `multi_edit_files` so your `old_text` matches exactly. Make the smallest change that fully solves the problem.
- **Never leave it broken.** After edits, run the build/lint/tests. If something fails, read the actual error output and fix the root cause — don't guess, don't paper over it, don't disable the check.
- **Report honestly.** If tests fail, say so and show the output. If you skipped a step, say that. State "done" only for work you verified.
- **Be surgical.** Don't reformat unrelated code, rename things gratuitously, or delete code you didn't write without checking what it does first.

## TESTING MASTERY
- After writing or changing code, ALWAYS exercise it: run the script/server, run the test suite, or write a quick harness with `run_command`. Observe real output.
- Write real tests when building features: use the project's framework (pytest / jest / vitest / go test / JUnit …). Cover the happy path, edge cases, and error handling. Prefer small, fast, deterministic tests.
- If no test framework exists in a project you're building, set one up (e.g. `pytest`, `npm i -D vitest`) and add a runnable `test` command.
- Reproduce a bug with a failing test FIRST, then fix it, then show the test passing.
- For web/security work with Burp connected, drive requests through the `burp_*` tools and confirm findings before reporting them.

## LARGE / MULTI-FILE PROJECTS
- Start with `emit_plan` + `update_todo`, then build in coherent slices (data model → backend → API → frontend → tests), keeping each slice runnable.
- Create a sane layout and the supporting files: README, dependency manifest (`requirements.txt`/`package.json`), `.gitignore`, env example, and a run/start script.
- Keep files focused and modular; split large files by responsibility. Wire modules together and verify imports resolve by running the entry point.
- Track progress with `update_todo` and give a short status after each slice. For very big builds, checkpoint by running what exists so far before moving on.
- Persist durable decisions (stack, conventions, ports) with `save_memory` so later turns stay consistent.

## RESPONSE STYLE
- Use markdown: headers, **bold**, `code`, tables
- Always include language tag on code blocks: ```python, ```bash
- Summarize command output clearly — surface the important lines, not walls of text
- After finishing: confirm what was done, what you verified, and suggest next steps{burp_block}{rules_block}{mem_block}
"""


# ── Shrink History ─────────────────────────────────────────────────────────────
def shrink_history_agent(history: list, model_key: str, keep: int = 10, manual: bool = False) -> list:
    if not manual and len(history) < 100:
        return history
    
    if manual:
        status_line("Manually shrinking history...", "info")
    else:
        status_line("History reached 100 messages. Summarizing to save tokens...", "info")
        
    client, key, label = _rotator.get()
    if not client: return history[-50:] # fallback
    
    api_model = MODELS.get(model_key, MODELS[DEFAULT_MODEL])
    
    text_to_summarize = ""
    for h in history[:-keep]:
        role = "User" if h["role"] == "user" else "Agent"
        text_to_summarize += f"{role}: {h['content']}\n\n"
        
    prompt = f"Please provide a concise but comprehensive summary of the following conversation history. Retain key facts, decisions, and context.\n\n{text_to_summarize}"
    
    try:
        resp = client.models.generate_content(model=api_model, contents=prompt)
        summary = resp.text
        
        new_history = [{"role": "assistant", "content": f"**[System: History Summary]**\n{summary}", "ts": datetime.now().isoformat()}]
        new_history.extend(history[-keep:])
        return new_history
    except Exception as ex:
        status_line(f"Failed to shrink history: {ex}", "warning")
        return history[-50:] # fallback

# ── Model fallback + last-model persistence ────────────────────────────────────
class ModelUnavailable(Exception):
    """Raised by an agent loop when a model can't serve the turn and the caller
    should fall back to another model. `reason` ∈ {unauthenticated, exhausted,
    invalid_model}."""
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _classify_model_error(msg: str) -> str | None:
    """Map an error message to a fallback reason, or None if not fallback-worthy."""
    m = (msg or "").lower()
    if any(k in m for k in ("401", "403", "unauthenticated", "unauthorized",
                            "invalid api key", "api key not valid", "permission",
                            "unauthorized_client")):
        return "unauthenticated"
    if any(k in m for k in ("429", "quota", "exhausted", "resource_exhausted",
                            "rate limit", "rate-limit", "overloaded")):
        return "exhausted"
    if any(k in m for k in ("not found", "does not exist", "no such model",
                            "unsupported", "model_not_found", "invalid model")):
        return "invalid_model"
    return None


def load_last_model() -> str | None:
    """The model the user last used (persisted, shared settings table)."""
    if _DB_OK:
        try:
            from agent2.database import get_setting
            return get_setting("cli_last_model")
        except Exception:
            pass
    return None


def save_last_model(model_key: str) -> None:
    if _DB_OK and model_key:
        try:
            from agent2.database import set_setting
            set_setting("cli_last_model", model_key)
            # Remember the last built-in Gemini model separately so /keys can
            # restore it when un-pinning away from a custom provider.
            if model_key in MODELS:
                set_setting("cli_last_gemini_model", model_key)
        except Exception:
            pass


# ── Agent loop ─────────────────────────────────────────────────────────────────
def run_agent(
    user_msg:  str,
    history:   list,
    model_key: str,
    mode_key:  str,
) -> list:
    """One full agentic turn. Returns updated history."""

    if not _GENAI:
        status_line("google-genai is not installed. Run: pip install google-genai", "error")
        return history

    client, key, label = _rotator.get()
    if not client:
        status_line("No API keys found. Run:  python run.py --addapi  or type /addapi", "error")
        return history

    api_model = MODELS.get(model_key, MODELS[DEFAULT_MODEL])
    mode_cfg  = MODES.get(mode_key,  MODES[DEFAULT_MODE])

    # Burp tools are only offered when the user has explicitly connected with
    # `/burp connect`. We NEVER auto-connect here — connecting on every turn was
    # slow and surprising. If a live session exists, expose its tools.
    burp_decls = []
    if _BURP_OK and _burp and _burp.is_connected():
        burp_decls = _burp.gemini_declarations()

    agent_tools = [_build_tools()]
    if burp_decls:
        agent_tools.append(gtypes.Tool(function_declarations=burp_decls))

    # Generation config
    cfg_kw: dict = dict(
        system_instruction=build_sys_prompt(burp_tool_count=len(burp_decls)),
        tools=agent_tools,
        tool_config=gtypes.ToolConfig(
            function_calling_config=gtypes.FunctionCallingConfig(mode="AUTO")
        ),
        max_output_tokens=mode_cfg["max_tokens"],
    )
    if mode_cfg.get("thinking") and model_key in ("2.5-pro", "3.1-flash", "3.1-pro", "2.5-flash"):
        try:
            cfg_kw["thinking_config"] = gtypes.ThinkingConfig(
                thinking_budget=mode_cfg.get("thinking_budget", 8000))
        except Exception: pass

    gen_cfg = gtypes.GenerateContentConfig(**cfg_kw)

    # Build context from history (last 20 turns)
    context = []
    for h in history[-20:]:
        if   h["role"] == "user":      context.append(gtypes.Content(role="user",  parts=[gtypes.Part(text=h["content"])]))
        elif h["role"] == "assistant": context.append(gtypes.Content(role="model", parts=[gtypes.Part(text=h["content"])]))

    context.append(gtypes.Content(role="user", parts=[gtypes.Part(text=user_msg)]))
    history.append({"role": "user", "content": user_msg, "ts": datetime.now().isoformat()})

    total_tokens = 0

    for _iteration in range(MAX_AGENT_ITERS):
        mode_icon = mode_cfg["icon"]
        spin_msg  = f"Agent 2  [{model_key} / {mode_key} {mode_icon}]  key #{label}"
        spin = Spinner(spin_msg)
        spin.start()

        try:
            resp = client.models.generate_content(model=api_model, contents=context, config=gen_cfg)
        except KeyboardInterrupt:
            spin.stop()
            print()
            status_line("Interrupted.", "warning")
            return history
        except Exception as exc:
            spin.stop()
            es = str(exc)
            reason    = _classify_model_error(es)
            is_quota  = reason == "exhausted"
            is_auth   = reason == "unauthenticated"
            _rotator.fail(key, quota=is_quota)

            if is_quota or is_auth:
                # Try the next API key on the SAME model first.
                c2, k2, l2 = _rotator.next_active(key)
                if c2:
                    tag = "Quota" if is_quota else "Auth"
                    status_line(f"{tag} issue on key #{label} — switching to key #{l2}", "warning")
                    client, key, label = c2, k2, l2
                    continue
                # No keys left → let the caller fall back to another model.
                raise ModelUnavailable(reason, es)
            if reason == "invalid_model":
                raise ModelUnavailable("invalid_model", es)
            status_line(f"API Error ({model_key}): {es}", "error")
            return history
        finally:
            spin.stop()

        # Parse response
        try:
            candidate = resp.candidates[0] if resp.candidates else None
            if not candidate or not candidate.content:
                fr = getattr(candidate, "finish_reason", "?") if candidate else "none"
                status_line(f"Empty response (finish_reason={fr}). Try /model 2.5-flash", "warning")
                return history
            parts = candidate.content.parts or []
        except Exception as ex:
            status_line(f"Parse error: {ex}", "error")
            return history

        func_calls: list = []
        texts:      list = []
        for p in parts:
            try:
                if p.function_call and p.function_call.name: func_calls.append(p.function_call)
                elif p.text: texts.append(p.text)
            except Exception: pass

        # Tokens
        try:    tok = getattr(resp.usage_metadata, "total_token_count", 0) or 0
        except: tok = 0
        total_tokens += tok
        if tok:
            if _RICH: _con.print(f"  [dim]tokens: {total_tokens:,}[/]")
            else:     print(f"  {D}tokens: {total_tokens:,}{R}")

        # Interim text (before tool calls)
        if texts and func_calls:
            print()
            for t in texts: print(f"  {D}{t[:200]}{R}")

        # Tool calls
        if func_calls:
            context.append(gtypes.Content(role="model",
                parts=[gtypes.Part(function_call=fc) for fc in func_calls]))
            tool_result_parts = []

            for fc in func_calls:
                name = fc.name
                args = dict(fc.args)
                print()

                if name == "run_command":
                    cmd  = args.get("command", "")
                    desc = args.get("description", "Running…")
                    cwd  = args.get("cwd", None)
                    print_tool_call(name, desc, cmd)
                    out, rc = run_cmd_stream(cmd, cwd)
                    result  = {"output": out[:3000], "returncode": rc, "success": rc == 0}
                else:
                    labels = {
                        "read_file":        f"Reading {args.get('path','?')}",
                        "write_file":       f"Writing {args.get('path','?')}",
                        "scan_project":     f"Scanning {args.get('path','?')}",
                        "multi_edit_files": f"Editing {len(args.get('edits',[])) if isinstance(args.get('edits'), list) else '?'} file(s)",
                        "web_search":       f"Searching: {args.get('query','?')}",
                        "save_memory":      f"Saving memory",
                        "emit_plan":        f"Planning: {args.get('title','?')}",
                    }
                    print_tool_call(name, labels.get(name, name))
                    result = dispatch_tool(name, args)

                    # Pretty display for Burp MCP tools
                    if name.startswith("burp_"):
                        if result.get("success"):
                            preview = str(result.get("output", ""))[:1000]
                            status_line(f"Burp → {name}", "success")
                            if _RICH: _con.print(f"[dim]{preview}[/]")
                            else:     print(f"{D}{preview}{R}")
                        elif "error" in result:
                            status_line(f"Burp error: {result['error']}", "error")
                    elif name == "read_file" and "content" in result:
                        preview = result["content"][:600]
                        lang    = Path(args.get("path","")).suffix.lstrip(".")
                        if _RICH:
                            try:   _con.print(Syntax(preview, lang or "text", theme="monokai", line_numbers=True))
                            except: _con.print(f"[dim]{preview}[/]")
                        else: print(f"{YW}{preview}{R}")
                    elif name == "write_file" and result.get("success"):
                        status_line(f"Written \u2192 {result.get('path','?')}  ({result.get('lines',0)} lines)", "success")
                    elif name == "scan_project" and "file_tree" in result:
                        tree = result["file_tree"][:2000]
                        cnt  = result.get("file_count", 0)
                        status_line(f"Scanned {cnt} files", "success")
                        if _RICH: _con.print(f"[dim]{tree}[/]")
                        else:     print(f"{D}{tree}{R}")
                    elif name == "multi_edit_files" and "results" in result:
                        for line in result["results"].split("\n"):
                            if "Successfully" in line:
                                status_line(line, "success")
                            elif "not found" in line or "Error" in line:
                                status_line(line, "error")
                            else:
                                status_line(line, "info")
                    elif name == "web_search" and "results" in result:
                        for res in result["results"][:3]:
                            if _RICH: _con.print(f"  [bold #60b8ff]{res.get('title','')[:70]}[/]\n  [dim]{res.get('snippet','')[:220]}[/]\n")
                            else:     print(f"  {CY}{res.get('title','')[:70]}{R}\n  {D}{res.get('snippet','')[:220]}{R}\n")
                    elif name == "save_memory" and result.get("saved"):
                        status_line("Memory saved", "success")
                    elif "error" in result:
                        status_line(f"Tool error: {result['error']}", "error")

                # Cap large string values so a big scan can't blow up context
                # (mirrors the Web UI's MAX_TOOL_OUTPUT truncation).
                safe_result = {
                    k: (v[:6000] + "\n…[truncated]" if isinstance(v, str) and len(v) > 6000 else v)
                    for k, v in result.items()
                }
                tool_result_parts.append(gtypes.Part(function_response=gtypes.FunctionResponse(
                    name=name, response=safe_result)))

            context.append(gtypes.Content(role="user", parts=tool_result_parts))

        else:
            # Final text response
            final = "\n".join(texts) or "Done."
            print_agent_reply(final)
            history.append({"role": "assistant", "content": final,
                            "ts": datetime.now().isoformat()})
            return history

    status_line(f"Reached max iterations ({MAX_AGENT_ITERS}).", "warning")
    return history

# ── /burp command (manage Burp Suite MCP bridge) ───────────────────────────────
def cmd_burp(user_input: str):
    """/burp [connect|disconnect|status|list] — manage the Burp Suite MCP bridge."""
    if not _BURP_OK or _burp is None:
        status_line("Burp MCP bridge unavailable (mcp package missing). Run: pip install mcp", "error")
        return
    parts = user_input.split(maxsplit=1)
    sub = (parts[1].strip().lower() if len(parts) > 1 else "status")

    if sub in ("connect", "on", ""):
        _burp.enabled = True
        okc, msg = _burp.connect()
        status_line(msg, "success" if okc else "error")
    elif sub in ("disconnect", "off"):
        _burp.disconnect()
        status_line("Disconnected from Burp MCP.", "info")
    elif sub in ("list", "tools"):
        tools = _burp.list_tools()
        if not tools:
            status_line("No Burp tools (not connected?). Try: /burp connect", "warning")
        else:
            status_line(f"{len(tools)} Burp tools available:", "success")
            for t in tools:
                print(f"  {CY}{t['name']}{R}  {D}{(t['description'] or '')[:70]}{R}")
    else:  # status
        s = _burp.status()
        state = ok("connected") if s["connected"] else err("disconnected")
        print(f"  Burp MCP: {state}  {D}({s['url']}){R}")
        print(f"  Tools: {s['tool_count']}   mcp installed: {s['mcp_installed']}")
        if s["last_error"]:
            print(f"  {D}last error: {s['last_error'][:120]}{R}")
        print(f"  {D}Usage: /burp connect | disconnect | list | status{R}")


# ── /addapi command (interactive, writes to agent2.db) ─────────────────────────
def cmd_addapi():
    keys = load_keys()
    print()
    if _RICH:
        _con.print(Panel("[bold #7c6af7]Add Gemini API Key[/]\nFree: [link=https://aistudio.google.com/app/apikey]aistudio.google.com/app/apikey[/link]",
                         border_style="#3a2a70"))
    else:
        print(f"  {PU}{B}Add Gemini API Key{R}")
        print(f"  Free key: https://aistudio.google.com/app/apikey\n")

    status_line(f"Keys currently stored: {len(keys)}", "info")
    for k in keys:
        col = GR if k["active"] else RD
        print(f"    {col}●{R}  #{k['label']}: {D}{k['key'][:14]}…{R}")
    print()

    while True:
        try:
            raw = input(f"  {PU}paste key (or Enter to cancel):{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return
        if not raw:
            return
        raw = raw.replace(" ", "").replace("\n", "")
        ok_save, msg = save_key_to_env(raw)
        if ok_save:
            _rotator.reload()
            status_line(f"Key saved as #{msg}", "success")
            status_line(f"Total keys stored: {len(load_keys())}", "info")
            ans = input(f"  Add another? [y/N]: ").strip().lower()
            if ans != "y":
                break
        else:
            status_line(f"Could not save key: {msg}", "error")

# ── Read multi-line input helper ───────────────────────────────────────────────
def read_input(prompt_str: str) -> str:
    """Read one line, stripping leading/trailing whitespace."""
    try:
        if _RICH:
            return _con.input(prompt_str)
        else:
            return input(prompt_str)
    except (EOFError, KeyboardInterrupt):
        raise KeyboardInterrupt


def get_prompt_style(mode: str):
    # Mode tints the prompt, but the active theme accent is the base colour so
    # /theme and /color visibly change the input line too.
    mode_color = {
        "fast": "#00ff9c",     # neon green
        "pro": ACCENT,         # theme accent
        "thinking": "#ff9f43"  # orange
    }.get(mode, ACCENT)

    return Style.from_dict({
        "user": f"{mode_color} bold",
        "meta": "#888888",
        "arrow": f"{mode_color} bold",
        # Autocomplete dropdown styling.
        "completion-menu.completion":         "bg:#1e1e30 #c4c4dc",
        "completion-menu.completion.current": f"bg:{mode_color} #10101a bold",
        "completion-menu.meta.completion":         "bg:#15151f #8888aa",
        "completion-menu.meta.completion.current": f"bg:{mode_color} #10101a",
    })


# ── Slash-command autocomplete (type "/" → suggestions; "/m" → filtered) ───────
if _PTK and Completer is not None:
    class SlashCompleter(Completer):
        """Suggests slash commands. Only fires when the buffer starts with '/'
        and is on the first token, so normal chat isn't interrupted."""
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor.lstrip()
            if not text.startswith("/") or " " in text:
                return
            word = text                      # includes leading '/'
            for _tok, base, desc in SLASH_COMMANDS:
                if base.startswith(word.lower()):
                    yield Completion(
                        base,
                        start_position=-len(word),
                        display=base,
                        display_meta=desc,
                    )
else:
    SlashCompleter = None


# ── Interactive arrow-key selector (like Claude Code's /model picker) ───────────
def select_from_list(title: str, options: list[dict], current_value=None):
    """
    Show a full-screen-ish inline menu; navigate with ↑/↓ (or j/k), Enter to
    pick, Esc/q to cancel. Each option is {"value","label","hint"}.
    Returns the chosen option's "value", or None if cancelled.

    Falls back to a numbered prompt when prompt_toolkit's Application isn't
    available (e.g. minimal installs).
    """
    if not options:
        return None

    # Fallback: plain numbered list.
    if not _PTK or Application is None:
        for i, o in enumerate(options, 1):
            mark = "  ←" if o["value"] == current_value else ""
            print(f"  {i}. {o['label']}  {o.get('hint','')}{mark}")
        try:
            raw = input("  Pick number (Enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]["value"]
        return None

    # Start the cursor on the current value if present.
    idx = next((i for i, o in enumerate(options) if o["value"] == current_value), 0)
    state = {"idx": idx, "chosen": None}

    def render():
        lines = [("class:title", f"  {title}\n")]
        for i, o in enumerate(options):
            sel = i == state["idx"]
            cur = o["value"] == current_value
            pointer = "❯ " if sel else "  "
            style = "class:sel" if sel else "class:opt"
            tag = " (current)" if cur else ""
            lines.append((style, f"  {pointer}{o['label']}{tag}"))
            hint = o.get("hint", "")
            if hint:
                lines.append(("class:hint", f"   {hint}"))
            lines.append(("", "\n"))
        lines.append(("class:footer", "\n  ↑/↓ move · Enter select · Esc cancel"))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("k")
    def _up(event):
        state["idx"] = (state["idx"] - 1) % len(options)

    @kb.add("down")
    @kb.add("j")
    def _down(event):
        state["idx"] = (state["idx"] + 1) % len(options)

    @kb.add("enter")
    def _enter(event):
        state["chosen"] = options[state["idx"]]["value"]
        event.app.exit()

    @kb.add("escape")
    @kb.add("q")
    @kb.add("c-c")
    def _cancel(event):
        state["chosen"] = None
        event.app.exit()

    style = Style.from_dict({
        "title":  "#7c6af7 bold",
        "sel":    "#00ff9c bold",
        "opt":    "#c4c4dc",
        "hint":   "#888888 italic",
        "footer": "#666666",
    })
    app = Application(
        layout=Layout(HSplit([Window(FormattedTextControl(render), wrap_lines=True)])),
        key_bindings=kb, style=style, full_screen=False, mouse_support=False,
    )
    app.run()
    return state["chosen"]


def model_label(model_key: str) -> str:
    """Short, friendly name for the prompt line (custom providers → their name)."""
    if isinstance(model_key, str) and model_key.startswith("custom:"):
        try:
            from agent2 import providers as P
            prov = P.get_provider(model_key.split(":", 1)[1])
            if prov:
                return prov.get("name") or prov.get("model_id") or "custom"
        except Exception:
            pass
        return "custom"
    return model_key


def build_model_choices() -> list[dict]:
    """Built-in Gemini models + custom providers, as selector options."""
    opts: list[dict] = []
    for k, api in MODELS.items():
        opts.append({"value": k, "label": f"⚡ {k}",
                     "hint": f"Gemini · {api}"})
    try:
        from agent2 import providers as P
        P.init_providers_table()
        for p in P.list_providers(safe=True):
            opts.append({"value": p["key"],
                         "label": f"🔌 {p['name']}",
                         "hint": f"{p['format']} · {p['model_id']}"})
    except Exception:
        pass
    return opts


def build_mode_choices() -> list[dict]:
    """Modes (fast / pro / thinking) as arrow-selector options."""
    hints = {
        "fast":     "Fastest replies, lowest token use",
        "pro":      "Balanced — recommended for most tasks",
        "thinking": "Deep reasoning via extended thinking",
    }
    return [{"value": k, "label": f"{v['icon']}  {k}",
             "hint": f"{v['max_tokens']} tokens · {hints.get(k, '')}"}
            for k, v in MODES.items()]

# ── Main interactive loop ──────────────────────────────────────────────────────
def run_provider_agent_cli(user_msg: str, history: list, pid: str) -> list:
    """CLI agentic loop for a custom provider (OpenAI/Anthropic compatible)."""
    from agent2 import providers as P
    prov = P.get_provider(pid)
    if not prov:
        status_line("Custom provider not found. Use /provider list", "error")
        return history

    history.append({"role": "user", "content": user_msg,
                    "ts": datetime.now().isoformat()})
    # Burp tools are only offered if the user already ran `/burp connect`.
    burp_live = _BURP_OK and _burp and _burp.is_connected()
    system = build_sys_prompt(burp_tool_count=len(_burp.list_tools()) if burp_live else 0)
    fmt = prov.get("format", "openai")

    # Seed provider messages from text history
    messages = [{"role": ("user" if h["role"] == "user" else "assistant"),
                 "content": h["content"]}
                for h in history[-20:] if h["role"] in ("user", "assistant")]

    for _ in range(MAX_AGENT_ITERS):
        spin = Spinner(f"{prov.get('model_id','model')} thinking…")
        spin.start()
        try:
            result = P.chat(prov, messages, system)
        except Exception as exc:
            spin.stop()
            reason = _classify_model_error(str(exc))
            if reason:
                # Let agent_turn fall back to another model.
                raise ModelUnavailable(reason, str(exc))
            status_line(f"Provider error: {exc}", "error")
            return history
        finally:
            spin.stop()

        total = result.get("tokens", 0)
        if total:
            print(f"  {D}tokens: {total:,}{R}")
        tool_calls = result.get("tool_calls") or []

        if not tool_calls:
            final = result.get("text") or "Done."
            print_agent_reply(final)
            history.append({"role": "assistant", "content": final,
                            "ts": datetime.now().isoformat()})
            return history

        if fmt == "anthropic":
            messages.append({"role": "assistant", "content": result.get("raw_content", [])})
            tr = []
            for tc in tool_calls:
                out, ok = _run_tool_cli(tc["name"], tc["args"])
                tr.append({"type": "tool_result", "tool_use_id": tc["id"],
                           "content": out or "(no output)"})
            messages.append({"role": "user", "content": tr})
        else:
            messages.append(result.get("raw_assistant") or {
                "role": "assistant", "content": result.get("text", ""),
                "tool_calls": [{"id": tc["id"], "type": "function",
                                "function": {"name": tc["name"],
                                             "arguments": json.dumps(tc["args"])}}
                               for tc in tool_calls]})
            for tc in tool_calls:
                out, ok = _run_tool_cli(tc["name"], tc["args"])
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": out or "(no output)"})

    status_line("Reached max iterations.", "warning")
    return history


def _run_tool_cli(name: str, args: dict) -> tuple[str, bool]:
    """Execute a tool for the CLI provider loop, echoing to the terminal."""
    if name == "run_command":
        cmd = args.get("command", "")
        print_tool_call(name, args.get("description", "Running…"), cmd)
        out, rc = run_cmd_stream(cmd, args.get("cwd"))
        return out[:6000], rc == 0
    print_tool_call(name, name)
    result = dispatch_tool(name, args)
    if "error" in result:
        status_line(f"Tool error: {result['error']}", "error")
        return f"Error: {result['error']}", False
    txt = result.get("output") or result.get("content") or json.dumps(result)[:2000]
    return str(txt)[:6000], True


def _one_model_turn(user_input: str, history: list, model: str, mode: str) -> list:
    """Run a single turn on exactly one model (may raise ModelUnavailable)."""
    if isinstance(model, str) and model.startswith("custom:"):
        return run_provider_agent_cli(user_input, history, model.split(":", 1)[1])
    return run_agent(user_input, history, model, mode)


def _fallback_order(current: str) -> list:
    """Ordered models to try: current first, then Gemini built-ins, then other
    custom providers. Prefers free Gemini tiers before other paid gateways."""
    order = [current]
    for k in MODELS:                       # built-in Gemini models
        if k not in order:
            order.append(k)
    try:                                    # custom providers
        from agent2 import providers as P
        P.init_providers_table()
        for p in P.list_providers(safe=True):
            if p["key"] not in order:
                order.append(p["key"])
    except Exception:
        pass
    return order


def agent_turn(user_input: str, history: list, model: str, mode: str) -> tuple[list, str]:
    """Run a turn with automatic model fallback on auth/quota failures.

    Returns (history, model_that_succeeded). If every model fails, prints a clear
    'all exhausted/failed' error and returns the history unchanged.
    """
    order = _fallback_order(model)
    base_len = len(history)
    failures: list[tuple[str, str]] = []    # (model, reason)

    for i, m in enumerate(order):
        try:
            new_hist = _one_model_turn(user_input, history, m, mode)
            if m != model:
                status_line(f"Now using {model_label(m)} (auto-switched).", "success")
            return new_hist, m
        except ModelUnavailable as mu:
            failures.append((m, mu.reason))
            # Roll back any partial user/assistant rows this attempt appended so
            # the next model starts clean.
            del history[base_len:]
            nice = {"unauthenticated": "unauthenticated",
                    "exhausted": "resource exhausted",
                    "invalid_model": "unavailable"}.get(mu.reason, mu.reason)
            nxt = order[i + 1] if i + 1 < len(order) else None
            if nxt:
                status_line(f"{model_label(m)} → {nice}. Trying {model_label(nxt)}…", "warning")
            else:
                status_line(f"{model_label(m)} → {nice}.", "error")
        except KeyboardInterrupt:
            print(); status_line("Interrupted.", "warning")
            return history, model

    # Every model failed.
    all_exhausted = all(r == "exhausted" for _, r in failures) and failures
    detail = ", ".join(f"{model_label(mm)}: {rr}" for mm, rr in failures)
    if all_exhausted:
        status_line(f"All resources exhausted — every model hit its quota. ({detail})", "error")
    else:
        status_line(f"All models failed. ({detail})", "error")
    status_line("Add/rotate keys with /addapi, add a provider with /provider add, "
                "or try again later.", "info")
    return history, model


# ── /provider command ──────────────────────────────────────────────────────────
def cmd_provider(user_input: str, current_model: str) -> str:
    """Manage custom providers. Returns a (possibly new) model key to use."""
    from agent2 import providers as P
    P.init_providers_table()
    parts = user_input.split(maxsplit=2)
    sub = parts[1].strip().lower() if len(parts) > 1 else "list"

    if sub == "list":
        provs = P.list_providers(safe=True)
        if not provs:
            status_line("No custom providers. Add one: /provider add", "info")
        else:
            for p in provs:
                mark = "  ← active" if current_model == p["key"] else ""
                print(f"  {CY}{p['key']}{R}  {p['name']}  {D}[{p['format']}] {p['model_id']}{mark}{R}")
        print(f"  {D}Usage: /provider add | use <custom:id> | del <id> | test <id>{R}")
        return current_model

    if sub == "add":
        print(f"  {PU}{B}Add custom provider{R}  (OpenAI- or Anthropic-compatible)")
        print(f"  {D}Base URL examples:{R}")
        print(f"  {D}  OpenRouter : https://openrouter.ai/api/v1{R}")
        print(f"  {D}  OpenAI     : https://api.openai.com/v1{R}")
        print(f"  {D}  Anthropic  : https://api.anthropic.com   (format: anthropic){R}")
        print(f"  {D}  Groq       : https://api.groq.com/openai/v1{R}")
        print(f"  {D}  Ollama     : http://localhost:11434/v1{R}")
        try:
            name  = input("  Name: ").strip()
            burl  = input("  Base URL (usually ends in /v1): ").strip()
            mid   = input("  Model ID (e.g. deepseek/deepseek-chat): ").strip()
            fmt   = (input("  Format [openai/anthropic] (default openai): ").strip().lower() or "openai")
            akey  = input("  API key: ").strip()
            print(f"  {D}  User-Agent: optional. Some gateways (e.g. AgentRouter) only accept an{R}")
            print(f"  {D}  allowlisted client UA like  opencode/0.4.0  — leave blank for default.{R}")
            uagent = input("  User-Agent (optional): ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return current_model
        if not (burl and mid and akey):
            status_line("base_url, model_id and api_key are all required.", "error")
            return current_model
        p = P.add_provider(name, burl, akey, mid, fmt, uagent)
        ua_note = f", UA {uagent}" if uagent else ""
        status_line(f"Added {p['key']} ({p['format']}{ua_note}).", "success")

        # Test the connection immediately so the user knows it works NOW.
        prov = P.get_provider(p["id"])
        status_line("Testing connection…", "info")
        try:
            r = P.chat(prov, [{"role": "user", "content": "Reply with the single word OK"}],
                       "Connection test.")
            status_line(f"Connection OK — {(r.get('text') or 'OK')[:50]}", "success")
            try:
                ans = input(f"  Switch to this model now? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans in ("", "y", "yes"):
                status_line(f"Model → {p['key']}", "success")
                return p["key"]
        except Exception as exc:
            status_line(f"Connection test FAILED: {exc}", "error")
            status_line("Fix the Base URL/key/format, then: /provider test " + p["id"], "warning")
        return current_model

    if sub == "use" and len(parts) > 2:
        key = parts[2].strip()
        pid = key.split(":", 1)[1] if key.startswith("custom:") else key
        if P.get_provider(pid):
            status_line(f"Model → custom:{pid}", "success")
            return "custom:" + pid
        status_line("Provider not found. /provider list", "error")
        return current_model

    if sub == "del" and len(parts) > 2:
        pid = parts[2].strip().replace("custom:", "")
        P.remove_provider(pid)
        status_line(f"Removed provider {pid}", "success")
        return DEFAULT_MODEL if current_model == "custom:" + pid else current_model

    if sub == "test" and len(parts) > 2:
        pid = parts[2].strip().replace("custom:", "")
        prov = P.get_provider(pid)
        if not prov:
            status_line("Provider not found.", "error"); return current_model
        try:
            r = P.chat(prov, [{"role": "user", "content": "Reply with the single word OK"}],
                       "Connection test.")
            status_line(f"OK — {(r.get('text') or '')[:60]}", "success")
        except Exception as exc:
            status_line(f"Test failed: {exc}", "error")
        return current_model

    status_line("Usage: /provider list | add | use <custom:id> | del <id> | test <id>", "info")
    return current_model


# ── /keys command (interactive activator with ↑/↓ selector) ────────────────────
def cmd_keys(current_model: str) -> str:
    """Show Gemini keys + custom providers and let the user ACTIVATE one with
    ↑/↓. Activating a Gemini key pins it as preferred (and, if a custom provider
    was active, switches the model back to Gemini). Activating a custom provider
    switches the model to it. Returns the (possibly new) model key."""
    gem = _rotator.status()
    try:
        from agent2 import providers as P
        P.init_providers_table()
        provs = P.list_providers(safe=True)
    except Exception:
        provs = []

    if not gem and not provs:
        status_line("No keys yet. Add a Gemini key with /addapi or a provider with /provider add.", "warning")
        return current_model

    using_custom = isinstance(current_model, str) and current_model.startswith("custom:")

    options: list[dict] = []
    # Gemini keys — value gem:<label>
    for k in gem:
        state = []
        if k.get("pinned"):
            state.append("pinned")
        if not k["active"]:
            state.append("exhausted")
        hint = "Gemini · " + (", ".join(state) if state else ("active" if not using_custom else "available"))
        options.append({"value": f"gem:{k['label']}",
                        "label": f"⚡ Gemini key #{k['label']}  {k['preview']}",
                        "hint": hint})
    # Custom providers — value = their model key (custom:<id>). Show model id
    # beside the key so two providers sharing the same API key are distinct.
    for p in provs:
        options.append({"value": p["key"],
                        "label": f"🔌 {p['name']}  {p['api_key']}  ·  {p['model_id']}",
                        "hint": f"{p['format']} provider · model {p['model_id']}"})
    # Auto-rotate option (unpin) when a key is currently pinned.
    if any(k.get("pinned") for k in gem):
        options.append({"value": "gem:__auto__",
                        "label": "↻ Auto-rotate Gemini keys (unpin)",
                        "hint": "Use whichever key is available; rotate on quota"})

    # Where the cursor starts.
    if using_custom:
        current_value = current_model
    else:
        pinned = next((k["label"] for k in gem if k.get("pinned")), None)
        current_value = f"gem:{pinned}" if pinned else None

    picked = select_from_list("Activate a key / provider", options, current_value=current_value)
    if picked is None:
        status_line("No change.", "info")
        return current_model

    # ── Custom provider chosen → switch the model to it ────────────────────────
    if picked.startswith("custom:"):
        status_line(f"Activated provider → {model_label(picked)}  ({picked})", "success")
        return picked

    # ── Gemini branch ──────────────────────────────────────────────────────────
    if picked == "gem:__auto__":
        _rotator.pin(None)
        status_line("Gemini keys set to auto-rotate.", "success")
    else:
        label = picked.split(":", 1)[1]
        _rotator.pin(label)
        status_line(f"Activated Gemini key #{label} (pinned).", "success")

    # If a custom provider was active, drop back to a Gemini model so the pinned
    # key is actually used.
    if using_custom:
        new_model = load_last_gemini_model() or DEFAULT_MODEL
        status_line(f"Model → {new_model}", "success")
        return new_model
    return current_model


def load_last_gemini_model() -> str | None:
    """Best-effort recall of the last built-in Gemini model the user was on."""
    if _DB_OK:
        try:
            from agent2.database import get_setting
            m = (get_setting("cli_last_gemini_model") or "").strip()
            if m in MODELS:
                return m
        except Exception:
            pass
    return None


# ── /theme and /color (UI settings) ────────────────────────────────────────────
def cmd_theme(user_input: str):
    """/theme [name] — pick a colour theme with ↑/↓ (or set directly by name)."""
    parts = user_input.split(maxsplit=1)
    if len(parts) > 1:
        name = parts[1].strip().lower()
        if apply_theme(name):
            status_line(f"Theme → {THEMES[name]['label']}", "success")
        else:
            status_line(f"Unknown theme. Options: {', '.join(THEMES)}", "error")
        return
    choices = [{"value": k, "label": f"{v['label']}",
                "hint": f"accent {v['accent']}"} for k, v in THEMES.items()]
    picked = select_from_list("Select a theme", choices, current_value=_CURRENT_THEME)
    if picked and apply_theme(picked):
        status_line(f"Theme → {THEMES[picked]['label']}", "success")
        # Redraw so the new palette is immediately visible.
        print_banner()
    elif picked is None:
        status_line("Theme unchanged.", "info")


def cmd_color(user_input: str):
    """/color [name] — set just the accent colour with ↑/↓ (or by name)."""
    parts = user_input.split(maxsplit=1)
    if len(parts) > 1:
        name = parts[1].strip().lower()
        if apply_accent(name):
            status_line(f"Accent → {name}", "success")
        else:
            status_line(f"Unknown colour. Options: {', '.join(ACCENT_CHOICES)} (or a 0–255 code)", "error")
        return
    choices = [{"value": k, "label": f"{k}", "hint": hexv}
               for k, (code, hexv) in ACCENT_CHOICES.items()]
    picked = select_from_list("Select an accent colour", choices)
    if picked and apply_accent(picked):
        status_line(f"Accent → {picked}", "success")
        print_banner()
    elif picked is None:
        status_line("Accent unchanged.", "info")


def process_turn(user_input: str, history: list, model: str, mode: str):
    """Run one agent turn while letting the user interrupt (ESC) or QUEUE a
    follow-up message by typing during execution. Returns
    (history, model, queued_messages)."""
    ctrl = InputController()
    ctrl.start()
    queued: list[str] = []
    try:
        history, model = agent_turn(user_input, history, model, mode)
        save_last_model(model)
        history = shrink_history_agent(history, model)
        save_history(history)
        queued = ctrl.drain()
    except KeyboardInterrupt:
        print()
        status_line("Interrupted.", "warning")
        # A late ESC may land here (interrupt_main only *schedules* the raise).
        try:
            queued = ctrl.drain()
        except Exception:
            pass
    finally:
        ctrl.stop()
    return history, model, queued


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Agent 2 CLI — autonomous dev agent")
    ap.add_argument("message", nargs="?", help="One-shot message (no REPL)")
    ap.add_argument("--model", default=None, choices=list(MODELS.keys()))
    ap.add_argument("--mode",  default=None, choices=list(MODES.keys()))
    ap.add_argument("--clear", action="store_true", help="Start with fresh chats")
    args = ap.parse_args()

    # Session state — restore the last-used model unless overridden by --model.
    def _valid_model(mk: str | None) -> bool:
        if not mk:
            return False
        if mk in MODELS:
            return True
        if mk.startswith("custom:"):
            try:
                from agent2 import providers as P
                P.init_providers_table()
                return P.get_provider(mk.split(":", 1)[1]) is not None
            except Exception:
                return False
        return False

    _saved = load_last_model()
    model     = args.model or (_saved if _valid_model(_saved) else DEFAULT_MODEL)
    mode      = args.mode  or DEFAULT_MODE
    history   = [] if args.clear else load_history()

    # Restore the saved UI theme / accent before anything is drawn.
    load_theme_from_settings()

    # One-shot mode (like `gemini -m flash "hello"`)
    if args.message:
        _rotator.reload()
        history, model = agent_turn(args.message, history, model, mode)
        save_last_model(model)
        save_history(history)
        return

    # Interactive REPL
    if not _PTK:
        print("\n  [ERR]  prompt-toolkit not installed.")
        print("         Run:  pip install prompt-toolkit\n")
        return

    print_banner()
    _restored_note = "  (restored)" if (_saved and model == _saved and not args.model) else ""
    status_line(f"Model: {model_label(model)}{_restored_note}  Mode: {mode}  Shell: {SHELL_LABEL}", "info")
    status_line("Type /help for commands.  Ctrl+C or /exit to quit.", "info")
    if not _GENAI:
        status_line("google-genai is missing — install it before chatting: pip install google-genai", "warning")
    if not load_keys():
        status_line("No API keys — type /addapi to add one.", "warning")
    if history:
        status_line(f"Restored {len(history)} messages from last session.  /clearhistory to start fresh.", "info")

    completer = SlashCompleter() if SlashCompleter else None
    session = PromptSession(
        history=FileHistory(str(PT_HISTORY)),
        completer=completer,
        complete_while_typing=True,          # suggest as you type "/…"
        auto_suggest=AutoSuggestFromHistory() if AutoSuggestFromHistory else None,
    )

    pending: list[str] = []   # messages queued while the agent was working

    while True:
        # If the agent queued follow-up messages, run those before prompting.
        if pending:
            user_input = pending.pop(0).strip()
            if user_input:
                status_line(f"↳ running queued message: {user_input[:70]}", "info")
        else:
            # Build prompt line
            mo_icon   = MODES[mode]["icon"]
            style = get_prompt_style(mode)

            prompt = HTML(
                f'<user>you</user> '
                f'<meta>[{SHELL_LABEL}|{__import__("html").escape(model_label(model))}|{mo_icon} ]</meta>'
                f'<arrow>></arrow> '
            )

            try:
                print()
                user_input = session.prompt(prompt, style=style).strip()
            except KeyboardInterrupt:
                print()
                status_line("Goodbye.", "info")
                save_history(history)
                break

        if not user_input:
            continue

        low = user_input.lower()

        # ── Slash commands ─────────────────────────────────────────────────────
        if low in ("/exit", "/quit", "exit", "quit"):
            status_line("Goodbye.", "info")
            save_history(history)
            break

        elif low == "/help":
            print_help()

        elif low == "/addapi":
            cmd_addapi()

        elif low == "/keys":
            model = cmd_keys(model)
            save_last_model(model)

        elif low.startswith("/burp"):
            cmd_burp(user_input)

        elif low.startswith("/provider"):
            model = cmd_provider(user_input, model)
            save_last_model(model)

        elif low.startswith("/model"):   # keep BEFORE /mode — /mode must not swallow /model
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1:
                # Interactive arrow-key picker (built-ins + custom providers).
                choices = build_model_choices()
                picked = select_from_list("Select a model", choices, current_value=model)
                if picked and picked != model:
                    model = picked
                    save_last_model(model)
                    _lbl = next((c["label"] for c in choices if c["value"] == picked), picked)
                    status_line(f"Model → {_lbl}  ({picked})", "success")
                elif picked is None:
                    status_line("Model unchanged.", "info")
            else:
                m = parts[1].strip()
                # Accept a built-in key, a custom:<id>, or a bare provider id.
                if m in MODELS:
                    model = m
                    save_last_model(model)
                    status_line(f"Model → {m}", "success")
                else:
                    from agent2 import providers as P
                    P.init_providers_table()
                    pid = m.split(":", 1)[1] if m.startswith("custom:") else m
                    if P.get_provider(pid):
                        model = "custom:" + pid
                        save_last_model(model)
                        status_line(f"Model → custom:{pid}", "success")
                    else:
                        opts = ", ".join(list(MODELS) + ["custom:<id>"])
                        status_line(f"Unknown model. Options: {opts}", "error")

        elif low.startswith("/mode"):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1:
                # Interactive arrow-key picker (same UX as /model).
                choices = build_mode_choices()
                picked = select_from_list("Select a mode", choices, current_value=mode)
                if picked and picked != mode:
                    mode = picked
                    status_line(f"Mode → {mode}  {MODES[mode]['icon']}", "success")
                elif picked is None:
                    status_line("Mode unchanged.", "info")
            else:
                m = parts[1].strip()
                if m in MODES:
                    mode = m
                    status_line(f"Mode → {m}  {MODES[m]['icon']}", "success")
                else:
                    status_line(f"Unknown mode. Options: {', '.join(MODES)}", "error")

        elif low.startswith("/theme"):
            cmd_theme(user_input)

        elif low.startswith("/color") or low.startswith("/colour"):
            cmd_color(user_input)

        elif low == "/clear":
            # Clear the SCREEN only — history is preserved (use /clearhistory to wipe).
            os.system("cls" if IS_WIN else "clear")
            print_banner()
            status_line("Screen cleared. History kept — use /clearhistory to wipe it.", "success")
        elif low == "/clearhistory":
            history = []
            save_history(history)
            status_line("Conversation history cleared.", "success")

        elif low == "/shrink":
            history = shrink_history_agent(history, model, keep=5, manual=True)
            save_history(history)
            status_line("History shrunk.", "success")

        elif low == "/history":
            if not history:
                status_line("No history.", "info")
            else:
                for h in history[-10:]:
                    col = CY if h["role"] == "user" else PU
                    sym = "you" if h["role"] == "user" else " a2"
                    ts  = h.get("ts","")[-8:][:5]
                    print(f"  {col}{sym}{R}  {D}{ts}{R}  {h['content'][:90]}")

        elif low == "/memory":
            mems = load_mems()
            if not mems:
                status_line("No memories saved yet.", "info")
            else:
                if _RICH:
                    t = Table(show_header=True, header_style="bold #7c6af7", box=rbox.SIMPLE_HEAD)
                    t.add_column("#", width=3, style="dim")
                    t.add_column("Imp", width=5)
                    t.add_column("Content")
                    t.add_column("Tags", style="dim")
                    for i, m in enumerate(sorted(mems, key=lambda x: -x.get("importance", 5)), 1):
                        t.add_row(str(i), f"{m.get('importance',5)}/10",
                                  m["content"][:80],
                                  ", ".join(m.get("tags", [])))
                    _con.print(t)
                else:
                    for i, m in enumerate(sorted(mems, key=lambda x: -x.get("importance", 5)), 1):
                        print(f"  {D}{i}.{R}  {YW}[{m.get('importance',5)}/10]{R}  {m['content'][:80]}")

        elif low.startswith("/addmem"):
            parts = user_input.split(maxsplit=1)
            if len(parts) > 1:
                add_mem(parts[1].strip())
                status_line("Memory saved.", "success")
            else:
                status_line("Usage: /addmem <text>", "warning")

        elif low == "/run" or low.startswith("/run "):
            cmd = user_input[4:].strip()
            if cmd:
                run_cmd_stream(cmd)
            else:
                status_line("Usage: /run <shell command>", "warning")

        elif low == "/read" or low.startswith("/read "):
            path = user_input[5:].strip()
            if not path:
                status_line("Usage: /read <file path>", "warning")
            else:
                result = _impl_read({"path": path})
                if "error" in result:
                    status_line(result["error"], "error")
                else:
                    lang = Path(path).suffix.lstrip(".")
                    if _RICH:
                        try:   _con.print(Syntax(result["content"][:3000], lang or "text", theme="monokai", line_numbers=True))
                        except: _con.print(result["content"][:3000])
                    else:
                        print(f"{YW}{result['content'][:3000]}{R}")

        elif low == "/search" or low.startswith("/search "):
            q = user_input[7:].strip()
            if not q:
                status_line("Usage: /search <query>", "warning")
            else:
                result = _impl_search({"query": q})
                for res in result.get("results", [])[:5]:
                    print()
                    if _RICH:
                        _con.print(f"  [bold {ACCENT2}]{res.get('title','')[:70]}[/]\n  [dim]{res.get('snippet','')[:250]}[/]\n")
                    else:
                        print(f"  {CY}{res.get('title','')[:70]}{R}\n  {D}{res.get('snippet','')[:250]}{R}\n")
                if not result.get("results"):
                    status_line("No results.", "info")

        elif low.startswith("/scan"):
            # Extract path and feed to agent as an explicit scan request
            scan_path = user_input[5:].strip() or "."
            scan_msg = f"Scan the project at path: {scan_path} — use the scan_project tool on that path. Show the file tree and analyze the tech stack (languages, frameworks, DB, etc)."
            try:
                history, model, _queued = process_turn(scan_msg, history, model, mode)
                pending.extend(_queued)
            except KeyboardInterrupt:
                print(); status_line("Interrupted.", "warning")

        elif low.startswith("/"):
            status_line(f"Unknown command: {user_input}  →  /help", "warning")

        # ── Agent call ─────────────────────────────────────────────────────────
        else:
            try:
                history, model, _queued = process_turn(user_input, history, model, mode)
                pending.extend(_queued)
            except KeyboardInterrupt:
                print(); status_line("Interrupted.", "warning")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        try:
            status_line("Goodbye.", "info")
        except Exception:
            pass
    except Exception as _fatal:
        # Failsafe: never dump a raw traceback at the user; suggest recovery.
        try:
            status_line(f"Unexpected error: {str(_fatal)[:300]}", "error")
            status_line("Try:  python run.py  or  python run.py --reset", "info")
        except Exception:
            print(f"\n  [FATAL] {str(_fatal)[:300]}")
            print("  Try:  python run.py  or  python run.py --reset")
        sys.exit(1)
