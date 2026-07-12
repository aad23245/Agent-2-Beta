#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
Agent2 — one-line network installer
───────────────────────────────────
Designed to be run straight from a pipe:

    curl -fsSL https://raw.githubusercontent.com/aaravshah1311/Agent-2-Beta/main/install.py | python3 -

    # Windows PowerShell
    irm https://raw.githubusercontent.com/aaravshah1311/Agent-2-Beta/main/install.py | python -

What it does (stdlib only — nothing to pre-install):
  1. Verifies Python 3.9+.
  2. Locates git (prints a download link if missing).
  3. Shallow-clones the repo into ./Agent-2-Beta  (override with AGENT2_DIR).
  4. Hands off to run.py, which builds the .venv, installs deps, and starts Agent2.

IMPORTANT — why this file exists separately from run.py:
  When a script is delivered over a pipe, Python reads it from STDIN. That means
  `__file__` is unreliable and, crucially, STDIN is *consumed by the script*, so
  any interactive input() the launcher does will hit EOF. This installer therefore
  avoids `__file__` entirely and never prompts — it only clones + delegates. You
  add your Gemini API key afterwards (in the web Settings panel, or `agent2 --addapi`).

Environment overrides:
  AGENT2_DIR    target directory                (default: ./Agent-2-Beta)
  AGENT2_MODE   launch mode after install       web | cli | none   (default: web)
  AGENT2_REPO   git URL to clone                (default: official repo)
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

REPO_URL = os.environ.get(
    "AGENT2_REPO", "https://github.com/aaravshah1311/Agent-2-Beta"
)
TARGET = Path(os.environ.get("AGENT2_DIR", "Agent-2-Beta")).expanduser().resolve()
MODE = os.environ.get("AGENT2_MODE", "web").strip().lower()

IS_WIN = os.name == "nt"

# ── Console setup ───────────────────────────────────────────────────────────────
# When delivered over a pipe on Windows, stdout defaults to a legacy code page
# (cp1252) that cannot encode arrows/box characters and would crash mid-print.
# Force UTF-8 and enable ANSI colours where possible; degrade gracefully.
if IS_WIN:
    os.system("")  # enable ANSI on modern Windows terminals
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Colours (best-effort; degrade to plain text where ANSI is unsupported) ──────
R = "\033[0m"; B = "\033[1m"; D = "\033[2m"
GR = "\033[1;32m"; CY = "\033[1;36m"; YL = "\033[1;33m"; RD = "\033[1;31m"; MG = "\033[1;35m"


def say(sym, msg, col=CY):
    print(f"  {col}{sym}{R}  {msg}")


def die(msg, hint=""):
    print(f"\n  {RD}[ERR]{R}  {msg}")
    if hint:
        print(f"        {D}{hint}{R}")
    sys.exit(1)


def banner():
    print(f"""{MG}
    _                    _   ____
   / \\   __ _  ___ _ __ | |_|___ \\
  / _ \\ / _` |/ _ \\ '_ \\| __| __) |
 / ___ \\ (_| |  __/ | | | |_ / __/
/_/   \\_\\__, |\\___|_| |_|\\__|_____|
        |___/{R}
  {D}One-line installer  ·  github.com/aaravshah1311{R}
""")


def find_git():
    exe = shutil.which("git")
    if exe:
        return exe
    if IS_WIN:
        for c in (
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/cmd/git.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git/cmd/git.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/cmd/git.exe",
        ):
            if c.exists():
                return str(c)
    else:
        for c in ("/usr/bin/git", "/usr/local/bin/git", "/opt/homebrew/bin/git"):
            if Path(c).exists():
                return c
    return None


def main():
    banner()

    # 1) Python version -------------------------------------------------------
    if sys.version_info < (3, 9):
        die(f"Python 3.9+ required (you have {sys.version.split()[0]}).",
            "Install a newer Python from https://python.org/downloads")
    say("[OK]", f"Python {sys.version.split()[0]}")

    # 2) git ------------------------------------------------------------------
    git = find_git()
    if not git:
        link = ("https://git-scm.com/download/win" if IS_WIN
                else "https://git-scm.com/downloads")
        die("git is required but was not found on PATH.",
            f"Install git from {link}, reopen your terminal, and re-run this command.")
    say("[OK]", f"git found  {D}{git}{R}")

    # 3) Target directory -----------------------------------------------------
    if TARGET.exists() and any(TARGET.iterdir()):
        if (TARGET / "run.py").exists():
            say("[!]", f"{TARGET} already contains Agent2 — skipping clone, updating instead.", YL)
            _handoff(update=True)
            return
        die(f"Target directory is not empty: {TARGET}",
            "Move it aside or set AGENT2_DIR to an empty path, then re-run.")

    say("[..]", f"Cloning {CY}{REPO_URL}{R}  ->  {D}{TARGET}{R}")
    res = subprocess.run(
        [git, "clone", "--depth", "1", REPO_URL, str(TARGET)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if res.returncode != 0:
        die(f"Clone failed:\n{(res.stdout or '').strip()[:500]}",
            "Check your internet connection and that the repo URL is reachable.")
    say("[OK]", "Repository downloaded")

    _handoff(update=False)


def _handoff(update: bool):
    """Delegate to run.py so it creates the venv, installs deps, and launches."""
    run_py = TARGET / "run.py"
    if not run_py.exists():
        die(f"run.py not found in {TARGET} — the download looks incomplete.")

    if MODE == "none":
        print(f"""
  {GR}{B}Agent2 is installed.{R}

  Next steps:
    {CY}cd {TARGET}{R}
    {CY}python run.py{R}            {D}# sets up .venv, installs deps, starts the web UI{R}

  {D}Add your free Gemini key at https://aistudio.google.com/app/apikey{R}
  {D}then paste it in the web Settings panel, or run:  python run.py --addapi{R}
""")
        return

    flag = "--cli" if MODE == "cli" else "--web"
    # If Agent2 is already present, genuinely pull the latest code (run.py --update
    # preserves agent2.db) instead of just relaunching — matches the message above.
    launch_args = ["--update"] if update else [flag]
    action = "Updating" if update else "Setting up"
    say(">>>", f"{action} Agent2 ...", GR)
    print(f"  {D}(This builds a virtual environment and installs dependencies — first run takes a minute.){R}\n")

    # run.py is stdin-safe on its own (its prompts fall back to defaults on EOF),
    # so it can finish setup even though our own stdin was the install pipe.
    try:
        subprocess.run([sys.executable, str(run_py), *launch_args], cwd=str(TARGET))
    except KeyboardInterrupt:
        print(f"\n  {D}Stopped.{R}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n  {D}Cancelled by user.{R}")
        sys.exit(0)
