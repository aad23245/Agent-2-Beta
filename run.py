#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
Agent2 — Universal Launcher
Platforms: Windows (CMD/PowerShell) | macOS | Linux

Usage:
  python run.py               — setup + start
  python run.py --web         — setup + start Web Agent
  python run.py --cli         — setup + start CLI agent
  python run.py --addapi      — add another API key
  python run.py --update      — update to latest code (-up / -update also work)
  python run.py --reset       — wipe venv and reinstall
  python run.py --uninstall   — remove venv, keys, DB and global command
  python run.py -h            — Show this help menu
"""

import os, sys, re, subprocess, platform, shutil, time, threading, itertools
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.resolve()
VENV      = ROOT / ".venv"
ENV_FILE  = ROOT / ".env"          # legacy — migrated into agent2.db, then retired
DB_FILE   = ROOT / "agent2.db"
APP_WEB   = ROOT / "agent2web.py"
APP_CLI   = ROOT / "agent2cli.py"

# ── Self-update / bootstrap source ─────────────────────────────────────────────
REPO_URL  = "https://github.com/aaravshah1311/Agent-2-Beta"
# Files/dirs that must SURVIVE an update (never wiped, never overwritten).
PRESERVE  = {
    "agent2.db", "agent2.db-wal", "agent2.db-shm", "agent2.db-journal",
    ".env", ".env.migrated",
}

OS_NAME = platform.system()   # Windows | Darwin | Linux
IS_WIN  = OS_NAME == "Windows"
IS_MAC  = OS_NAME == "Darwin"

VENV_PY  = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
VENV_PIP = VENV / ("Scripts/pip.exe"    if IS_WIN else "bin/pip")

# (import_name, pip_name, display_label)
COMMON_PACKAGES = [
    ("google.genai",   "google-genai",   "google-genai"),
    ("mcp",            "mcp",            "MCP (Burp Suite bridge)"),
]

WEB_PACKAGES = [
    ("flask",          "flask",          "Flask"),
    ("flask_socketio", "flask-socketio", "Flask-SocketIO")
]

CLI_PACKAGES = [
    ("rich",           "rich",           "Rich (terminal UI)"),
    ("prompt_toolkit", "prompt_toolkit", "Prompt Toolkit (terminal input)"),
]

# ── Windows console fixes ──────────────────────────────────────────────────────
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

# ── ANSI helpers ───────────────────────────────────────────────────────────────
R  = "\033[0m";   B  = "\033[1m";  D  = "\033[2m"
GR = "\033[1;32m"; CY = "\033[1;36m"; YL = "\033[1;33m"
RD = "\033[1;31m"; MG = "\033[1;35m"; WH = "\033[1;37m"

g   = lambda t: f"{GR}{t}{R}"
y   = lambda t: f"{YL}{t}{R}"
r   = lambda t: f"{RD}{t}{R}"
c   = lambda t: f"{CY}{t}{R}"
w   = lambda t: f"{WH}{B}{t}{R}"
dim = lambda t: f"{D}{t}{R}"

# ── Banner ─────────────────────────────────────────────────────────────────────
def banner():
    os.system("cls" if IS_WIN else "clear")
    print(f"{MG}")
    print(r"    _                    _   ____  ")
    print(r"   / \   __ _  ___ _ __ | |_|___ \ ")
    print(r"  / _ \ / _` |/ _ \ '_ \| __| __) |")
    print(r" / ___ \ (_| |  __/ | | | |_ / __/ ")
    print(r"/_/   \_\__, |\___|_| |_|\__|_____|")
    print(r"        |___/                      ")
    print(R)
    print(f"  {CY}{'═' * 46}{R}")
    print(f"  {w('Autonomous Terminal Agent')}  {dim('v2.1')}")
    print(f"  {dim(OS_NAME + ' ' + platform.machine() + '  |  Python ' + sys.version.split()[0])}")
    print(f"  {CY}{'═' * 46}{R}\n")

# ── Spinner ────────────────────────────────────────────────────────────────────
SPIN = ["-", "\\", "|", "/"]

def spin_run(label: str, cmd: list) -> subprocess.CompletedProcess:
    done, box = threading.Event(), [None]
    def work():
        try:
            box[0] = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace")
        except Exception as e:
            box[0] = subprocess.CompletedProcess(cmd, 1, "", str(e))
        finally:
            done.set()
    threading.Thread(target=work, daemon=True).start()
    for f in itertools.cycle(SPIN):
        if done.is_set(): break
        print(f"\r  [{GR}{f}{R}]  {label} ...", end="", flush=True)
        time.sleep(0.10)
    print(f"\r  {g('[OK]')}  {label}        ", flush=True)
    return box[0]

# ── Step 1: Python ─────────────────────────────────────────────────────────────
def check_python():
    print(f"  {w('[ System ]')}\n")
    if sys.version_info < (3, 9):
        print(f"  {r('[ERR]')}  Python 3.9+ required (you have {sys.version.split()[0]})")
        sys.exit(1)
    print(f"  {g('[OK]')}  Python {sys.version.split()[0]}")
    print(f"  {g('[OK]')}  Platform: {OS_NAME} {platform.machine()}")

# ── Step 2: Venv ───────────────────────────────────────────────────────────────
def ensure_venv(reset=False):
    # ── 🔒 Venv Reset Guard ──────────────────────────────────────────────────
    is_running_from_venv = str(VENV).lower() in sys.executable.lower()

    if reset:
        if is_running_from_venv:
            print(f"\n  {r('[ERR]')}  It couldn't be reset in env mode.")
            print(f"         Deactivate env or try in other terminal.\n")
            sys.exit(1)
            
        if VENV.exists():
            # Function to handle Windows read-only file locks
            def remove_readonly(func, path, excinfo):
                import stat
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception: pass

            try:
                # We print the label manually since we aren't using spin_run here
                print(f"  {y('[-]')}  Removing old venv ...", end="", flush=True)
                
                shutil.rmtree(VENV, onerror=remove_readonly)
                
                time.sleep(1) # Wait for Windows file handles
                print(f"\r  {g('[OK]')}  Environment wiped.        ")
            except Exception as e:
                print(f"\n  {r('[ERR]')}  Reset failed: {e}")
                sys.exit(1)
            
            for cache in ROOT.rglob("__pycache__"):
                shutil.rmtree(cache, ignore_errors=True)

    # ── 🛠️ Venv Creation ─────────────────────────────────────────────────────
    print(f"\n  {w('[ Virtual Environment ]')}\n")
    if not VENV.exists():
        # Creation DOES use spin_run because it calls an external process
        res = spin_run("Creating fresh virtual environment",
                       [sys.executable, "-m", "venv", str(VENV)])
        if res.returncode != 0:
            print(r(f"\n  [ERR]  {res.stderr[:400]}")); sys.exit(1)
    else:
        print(f"  {g('[OK]')}  Virtual environment ready  {dim(str(VENV))}")

# ── Step 3: Packages ───────────────────────────────────────────────────────────
def pkg_ok(name: str) -> bool:
    """Explicitly check if a package can be imported in the venv."""
    return subprocess.run(
        [str(VENV_PY), "-c", f"import {name}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0

def install_deps(mode="web"):
    print(f"\n  {w('[ Dependencies ]')}\n")

    subprocess.run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    packages = COMMON_PACKAGES[:]

    if mode == "web":
        packages += WEB_PACKAGES
    elif mode == "cli":
        packages += CLI_PACKAGES
    else:
        packages += WEB_PACKAGES + CLI_PACKAGES  # fallback (default run)

    for imp, pip_name, label in packages:
        if pkg_ok(imp):
            print(f"  {g('[OK]')}  {label} {dim('(verified)')}")
        else:
            print(f"  {y('[..]')}  Installing {label}...")
            res = spin_run(f"Installing {label}", 
                           [str(VENV_PY), "-m", "pip", "install", pip_name, "--quiet"])
            
            if res.returncode != 0:
                print(r(f"  [ERR] Failed to install {label}"))
                sys.exit(1)
            
            if pkg_ok(imp):
                print(f"  {g('[OK]')}  {label} verified in venv")
            else:
                print(r(f"  [ERR] {label} installed but not accessible"))
                sys.exit(1)

# ── Step 4: API-key storage (agent2.db — no .env) ──────────────────────────────
# run.py runs on the *system* Python before the venv exists, so it talks to the
# SQLite DB directly with the stdlib. The schema matches agent2/database.py.
import sqlite3


def _db_conn():
    c = sqlite3.connect(str(DB_FILE))
    c.row_factory = sqlite3.Row
    return c


def _ensure_keys_table():
    try:
        c = _db_conn()
        c.execute("""CREATE TABLE IF NOT EXISTS api_keys (
                        label      TEXT PRIMARY KEY,
                        api_key    TEXT UNIQUE,
                        name       TEXT,
                        active     INTEGER DEFAULT 1,
                        created_at TEXT DEFAULT(datetime('now'))
                     )""")
        c.commit(); c.close()
    except Exception as e:
        print(f"  {y('[!]')}  Could not open agent2.db: {e}")


def _migrate_env_once():
    """Import any legacy .env GEMINI_API_KEY* into the DB, then retire the file."""
    if not ENV_FILE.exists():
        return
    try:
        found = []
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip().startswith("GEMINI_API_KEY"):
                    found.append(v.strip().strip('"').strip("'"))
        for k in found:
            _db_add_key(k)
        ENV_FILE.rename(ENV_FILE.with_suffix(".env.migrated"))
        if found:
            print(f"  {g('[OK]')}  Migrated {len(found)} key(s) from .env into agent2.db")
    except Exception:
        pass


def _load_keys() -> list[str]:
    _ensure_keys_table()
    placeholder = "your_gemini_api_key_here"
    keys, seen = [], set()
    try:
        c = _db_conn()
        for row in c.execute("SELECT api_key FROM api_keys ORDER BY created_at, label"):
            v = (row["api_key"] or "").strip()
            if v and v != placeholder and len(v) > 10 and v not in seen:
                keys.append(v); seen.add(v)
        c.close()
    except Exception:
        pass
    return keys


def _db_add_key(key: str) -> bool:
    """Insert one key into the DB (dedup + smallest free integer label)."""
    key = (key or "").strip().replace(" ", "").replace("\n", "")
    if len(key) < 15:
        return False
    _ensure_keys_table()
    try:
        c = _db_conn()
        exists = c.execute("SELECT 1 FROM api_keys WHERE api_key=?", (key,)).fetchone()
        if exists:
            c.close(); return False
        used = {r["label"] for r in c.execute("SELECT label FROM api_keys")}
        n = 1
        while str(n) in used:
            n += 1
        c.execute("INSERT INTO api_keys(label, api_key, name, active) VALUES(?,?,?,1)",
                  (str(n), key, f"Key {n}"))
        c.commit(); c.close()
        return True
    except Exception:
        return False


def _prompt_key(num: int) -> str:
    while True:
        try:
            key = input(f"  {CY}>>>{R} API key #{num}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); return ""
        if not key:
            return ""
        key = key.replace(" ", "").replace("\n", "")
        if len(key) < 15:
            print(f"  {y('[!]')}  Key too short — check and retry\n")
            continue
        return key

# ── Step 4a: Setup (first run, no keys) ───────────────────────────────────────
def ensure_keys(force_add=False):
    print(f"\n  {w('[ Gemini API Keys ]')}\n")
    _ensure_keys_table()
    _migrate_env_once()
    keys = _load_keys()

    if keys and not force_add:
        for i, k in enumerate(keys, 1):
            print(f"  {g('[OK]')}  Key #{i}: {c(k[:14]+'...')}")
        print()
        # EOFError guard: when launched from the piped one-line installer there is
        # no keyboard on stdin — treat that as "no, keep existing keys" and move on.
        try:
            ans = input(f"  {CY}>>>{R} Add more keys for failover? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if ans != "y":
            return

    if not keys:
        print(f"  {y('[!]')}  No keys configured.")
        print(f"  {dim('  Free key:')} {c('https://aistudio.google.com/app/apikey')}\n")

    print(f"  {dim('  Enter keys one by one. Press Enter with nothing to finish.')}\n")
    num = len(keys) + 1
    while num <= 9:
        key = _prompt_key(num)
        if not key:
            break
        if key in keys:
            print(f"  {y('[!]')}  Duplicate — skipping\n"); continue
        if _db_add_key(key):
            keys.append(key)
            print(f"  {g('[OK]')}  Key #{num} saved\n")
            num += 1
        else:
            print(f"  {y('[!]')}  Could not save key (duplicate or too short)\n")
            continue
        if num > 9:
            print(f"  {dim('  Maximum 9 keys.')}"); break
        ans = input(f"  {CY}>>>{R} Add another for failover? [y/N]: ").strip().lower()
        if ans != "y":
            break

    if not keys:
        print(f"  {y('[!]')}  No keys — the app will show setup instructions at runtime.")
        return
    print(f"\n  {g('[OK]')}  {len(_load_keys())} key(s) saved to agent2.db")

# ── Step 4b: /addapi  — add key interactively, persist to agent2.db ────────────
def add_api():
    banner()
    print(f"  {w('[ Add Gemini API Key ]')}\n")
    _ensure_keys_table()
    _migrate_env_once()
    keys = _load_keys()
    print(f"  {dim('Currently stored keys:')} {len(keys)}")
    for i, k in enumerate(keys, 1):
        print(f"  {g('[OK]')}  Key #{i}: {c(k[:14]+'...')}")
    print(f"\n  {dim('Free key:  https://aistudio.google.com/app/apikey')}\n")

    num = len(keys) + 1
    while num <= 9:
        key = _prompt_key(num)
        if not key:
            break
        key = key.replace(" ", "")
        if len(key) < 15:
            print(f"  {y('[!]')}  Too short\n"); continue
        if key in keys:
            print(f"  {y('[!]')}  Already saved\n"); continue
        if _db_add_key(key):
            keys.append(key)
            print(f"  {g('[OK]')}  Key #{num} saved to agent2.db\n")
            num += 1
        else:
            print(f"  {y('[!]')}  Could not save key\n"); continue
        if num > 9:
            print(f"  {dim('  Maximum 9 keys reached.')}"); break
        ans = input(f"  {CY}>>>{R} Add another? [y/N]: ").strip().lower()
        if ans != "y":
            break

    print(f"\n  {g('[DONE]')}  Total keys in agent2.db: {len(_load_keys())}")

def uninstall():
    """Wipe everything: .venv, .env, agent2.db, __pycache__, and global command"""
    banner()
    print(f"  {r('[ WARNING ]')}  {w('This will delete all keys, data, the environment, and global commands.')}")
    try:
        ans = input(f"  {CY}>>>{R} Are you absolutely sure? [y/N]: ").strip().lower()
    except KeyboardInterrupt:
        print(f"\n  {g('[OK]')}  Uninstall aborted.")
        return

    if ans != 'y':
        print(f"\n  {g('[OK]')}  Uninstall aborted.")
        return

    # Files to wipe
    to_delete = [VENV, ENV_FILE, ENV_FILE.with_suffix(".env.migrated"), DB_FILE]

    for path in to_delete:
        if path.exists():
            try:
                if path.is_dir():
                    shutil.rmtree(path, onerror=lambda func, p, _: (os.chmod(p, 0o777), func(p)))
                else:
                    path.unlink()
                print(f"  {g('[OK]')}  Deleted: {dim(path.name)}")
            except Exception as e:
                print(f"  {y('[!]')}  Could not delete {path.name}: {e}")

    # Clean up global command everywhere it may live (primary + legacy + PATH),
    # so a stale wrapper can't linger after an uninstall.
    for bin_dir in _candidate_bin_dirs():
        for cmd in ["agent2", "agent2.bat", "agent2.cmd"]:
            cmd_path = bin_dir / cmd
            if cmd_path.exists():
                try:
                    cmd_path.unlink()
                    print(f"  {g('[OK]')}  Deleted global command: {dim(str(cmd_path))}")
                except Exception:
                    pass

    # Clean up python caches
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    print(f"\n  {MG}{B}Agent2 has been fully uninstalled.{R}\n")
    sys.exit(0)

# ── Self-update / bootstrap ────────────────────────────────────────────────────
def _rm_path(path: Path):
    """Delete a file or directory, defeating Windows read-only locks."""
    def _onerror(func, p, _exc):
        import stat
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onerror=_onerror)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except PermissionError:
            import stat
            os.chmod(path, stat.S_IWRITE)
            path.unlink()


def git_exe():
    """Locate a usable git executable, checking PATH then common install dirs."""
    found = shutil.which("git")
    if found:
        return found
    if IS_WIN:
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "cmd" / "git.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git" / "cmd" / "git.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe",
        ]
    else:
        candidates = [Path("/usr/bin/git"), Path("/usr/local/bin/git"), Path("/opt/homebrew/bin/git")]
    for cnd in candidates:
        if cnd and cnd.exists():
            return str(cnd)
    return None


def ensure_git() -> str:
    """Return a path to git, installing it first if it is missing. Exits on failure."""
    print(f"\n  {w('[ Git ]')}\n")
    exe = git_exe()
    if exe:
        print(f"  {g('[OK]')}  git found  {dim(exe)}")
        return exe

    print(f"  {y('[!]')}  git not found — attempting automatic install ...")
    if IS_WIN:
        installer = None
        if shutil.which("winget"):
            installer = ["winget", "install", "--id", "Git.Git", "-e",
                         "--source", "winget", "--accept-source-agreements",
                         "--accept-package-agreements"]
        elif shutil.which("choco"):
            installer = ["choco", "install", "git", "-y"]
    elif IS_MAC:
        installer = ["brew", "install", "git"] if shutil.which("brew") else None
    else:
        if shutil.which("apt-get"):
            installer = ["sudo", "apt-get", "install", "-y", "git"]
        elif shutil.which("dnf"):
            installer = ["sudo", "dnf", "install", "-y", "git"]
        elif shutil.which("yum"):
            installer = ["sudo", "yum", "install", "-y", "git"]
        elif shutil.which("pacman"):
            installer = ["sudo", "pacman", "-S", "--noconfirm", "git"]
        else:
            installer = None

    if not installer:
        print(f"  {r('[ERR]')}  Could not find a package manager to install git.")
        if IS_WIN:
            print(f"         {dim('Install git manually:')} {c('https://git-scm.com/download/win')}")
        elif IS_MAC:
            print(f"         {dim('Install Homebrew first:')} {c('https://brew.sh')}")
        else:
            print(f"         {dim('Install git with your distro package manager, then retry.')}")
        sys.exit(1)

    res = spin_run("Installing git", installer)
    if res.returncode != 0:
        print(f"  {r('[ERR]')}  git install failed: {dim((res.stderr or '')[:300])}")
        print(f"         {dim('Install git manually and retry:')} {c('https://git-scm.com/downloads')}")
        sys.exit(1)

    exe = git_exe()
    if not exe:
        print(f"  {g('[OK]')}  git installed — but not visible in this session.")
        print(f"         {dim('Open a NEW terminal and run the command again.')}")
        sys.exit(0)
    print(f"  {g('[OK]')}  git installed  {dim(exe)}")
    return exe


def _clone_repo(git: str, dest: Path):
    """Shallow-clone REPO_URL into *dest*. Returns CompletedProcess."""
    return spin_run("Downloading latest code",
                    [git, "clone", "--depth", "1", REPO_URL, str(dest)])


def _validate_snapshot(src: Path) -> bool:
    """A fresh checkout is only trusted if it carries the launcher itself."""
    return (src / "run.py").exists()


def _skip_top_level(name: str) -> bool:
    """Items in ROOT that an update must never touch."""
    return name in PRESERVE or name in {".git", ".venv"}


def self_update():
    """Replace all local code with the latest from REPO_URL, preserving the DB.

    Strategy is fail-safe: download + validate a complete new copy FIRST, then
    overlay it on top of the current install, and only afterwards prune stale
    files. The app is never left in a half-deleted state — if the download step
    fails, nothing on disk has changed yet.
    """
    banner()
    print(f"  {w('[ Update Agent2 ]')}")
    print(f"  {dim('Source:')} {c(REPO_URL)}")
    print(f"  {dim('Target:')} {dim(str(ROOT))}")
    print(f"  {dim('Preserved:')} {g('agent2.db')} {dim('(your keys, data & settings)')}\n")

    git = ensure_git()

    # 1) Download into a temp staging dir (sibling of ROOT so os.replace is cheap).
    import tempfile
    staging = Path(tempfile.mkdtemp(prefix="agent2_update_", dir=str(ROOT.parent)))
    snapshot = staging / "snapshot"
    try:
        res = _clone_repo(git, snapshot)
        if res.returncode != 0:
            print(f"  {r('[ERR]')}  Download failed: {dim((res.stderr or '')[:300])}")
            print(f"  {g('[OK]')}  Nothing was changed. Your current install is intact.")
            return
        if not _validate_snapshot(snapshot):
            print(f"  {r('[ERR]')}  Downloaded code looks incomplete (run.py missing).")
            print(f"  {g('[OK]')}  Nothing was changed. Your current install is intact.")
            return

        # Drop the cloned .git — we don't want to convert the user's install
        # into a checkout of the beta repo.
        clone_git = snapshot / ".git"
        if clone_git.exists():
            _rm_path(clone_git)

        new_names = {p.name for p in snapshot.iterdir()}

        # 2) Overlay: copy every new item over the current install. The DB and
        #    other preserved files are never overwritten.
        print(f"\n  {w('[ Applying update ]')}\n")
        for item in snapshot.iterdir():
            if item.name in PRESERVE:
                continue
            target = ROOT / item.name
            try:
                if target.exists():
                    _rm_path(target)
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
            except Exception as e:
                print(f"  {r('[ERR]')}  Failed writing {item.name}: {e}")
                print(f"  {y('[!]')}  Update aborted mid-apply — re-run {c('run.py --update')} to retry.")
                return
        print(f"  {g('[OK]')}  New code written")

        # 3) Prune: remove old top-level files that no longer exist upstream,
        #    but never touch the DB, .git, or .venv.
        removed = 0
        for item in ROOT.iterdir():
            if _skip_top_level(item.name):
                continue
            if item.name not in new_names:
                try:
                    _rm_path(item)
                    removed += 1
                except Exception as e:
                    print(f"  {y('[!]')}  Could not remove stale {item.name}: {e}")
        if removed:
            print(f"  {g('[OK]')}  Removed {removed} stale item(s)")

        # 4) Clear caches so the new code isn't shadowed by old bytecode.
        for cache in ROOT.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)
        print(f"  {g('[OK]')}  Cleared bytecode caches")

    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print(f"\n  {MG}{B}Agent2 updated successfully.{R}")
    print(f"  {dim('Finalizing setup with the new version ...')}\n")
    time.sleep(1)

    # 5) Hand off to the freshly-downloaded launcher so it configures the venv
    #    and installs ITS dependencies (the in-memory functions are now stale).
    env = os.environ.copy()
    env["AGENT2_UPDATED"] = "1"          # marker for the new launcher, if it cares
    try:
        subprocess.run([sys.executable, str(ROOT / "run.py")], env=env)
    except KeyboardInterrupt:
        pass
    sys.exit(0)


def bootstrap_if_needed():
    """First-run bootstrap: if only run.py is present (no app code), download the
    full project into this same directory before setup continues."""
    if APP_CLI.exists() or APP_WEB.exists() or (ROOT / "agent2").is_dir():
        return  # already a full install — nothing to do

    banner()
    print(f"  {w('[ First-Run Bootstrap ]')}")
    print(f"  {dim('Only run.py detected — fetching the full Agent2 project.')}")
    print(f"  {dim('Source:')} {c(REPO_URL)}")
    print(f"  {dim('Target:')} {dim(str(ROOT))}\n")

    git = ensure_git()

    import tempfile
    staging = Path(tempfile.mkdtemp(prefix="agent2_boot_", dir=str(ROOT.parent)))
    snapshot = staging / "snapshot"
    try:
        res = _clone_repo(git, snapshot)
        if res.returncode != 0 or not _validate_snapshot(snapshot):
            print(f"  {r('[ERR]')}  Could not download the project: "
                  f"{dim((res.stderr or 'incomplete checkout')[:300])}")
            print(f"         {dim('Check your connection and retry:')} {c('python run.py')}")
            sys.exit(1)

        clone_git = snapshot / ".git"
        if clone_git.exists():
            _rm_path(clone_git)

        print(f"\n  {w('[ Installing project ]')}\n")
        for item in snapshot.iterdir():
            # Never clobber a pre-existing run.py (the one we're running from) or
            # any preserved data file.
            if item.name == "run.py" or item.name in PRESERVE:
                continue
            target = ROOT / item.name
            try:
                if target.exists():
                    _rm_path(target)
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
            except Exception as e:
                print(f"  {r('[ERR]')}  Failed installing {item.name}: {e}")
                sys.exit(1)
        print(f"  {g('[OK]')}  Project files installed")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print(f"  {g('[OK]')}  Bootstrap complete — continuing setup ...\n")
    time.sleep(1)

# ── Step 5: Optional security tools ───────────────────────────────────────────
def check_tools():
    print(f"\n  {w('[ Optional Security Tools ]')}\n")
    tool_hints = {
        "nmap":    ("nmap",                 "brew install nmap",        "sudo apt install nmap"),
        "nikto":   ("nikto",               "brew install nikto",       "sudo apt install nikto"),
        "gobuster":("gobuster",            "brew install gobuster",    "sudo apt install gobuster"),
        "sqlmap":  ("sqlmap",              "pip install sqlmap",       "pip install sqlmap"),
        "hydra":   ("hydra",               "brew install hydra",       "sudo apt install hydra"),
    }
    for tool, (win, mac, linux) in tool_hints.items():
        if shutil.which(tool):
            print(f"  {g('[OK]')}  {tool}")
        else:
            hint = win if IS_WIN else (mac if IS_MAC else linux)
            print(f"  {y('[!]')}  {tool} {dim('not found')}  →  {dim(hint)}")

# ── Step 5b: Global Command ───────────────────────────────────────────────────
def _global_bin_dir() -> Path:
    """A stable, per-user directory to hold the `agent2` launcher on any OS."""
    if IS_WIN:
        return Path.home() / ".agent2" / "bin"
    return Path.home() / ".local" / "bin"


def _dir_on_path(d: Path) -> bool:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    dl = str(d).rstrip("\\/").lower()
    return any(p.rstrip("\\/").lower() == dl for p in parts if p)


def _add_to_path_windows(d: Path) -> str:
    """Append *d* to the persistent per-user PATH (via PowerShell). Returns
    'added' | 'exists' | 'failed'."""
    ds = str(d)
    ps = (
        "$ErrorActionPreference='Stop';"
        "$d=[Environment]::ExpandEnvironmentVariables($env:A2DIR);"
        "$p=[Environment]::GetEnvironmentVariable('PATH','User');"
        "if(-not $p){$p=''};"
        "$parts=$p.Split(';');"
        "if($parts -notcontains $d){"
        "  $new= if($p){$p.TrimEnd(';')+';'+$d}else{$d};"
        "  [Environment]::SetEnvironmentVariable('PATH',$new,'User');"
        "  'added' } else { 'exists' }"
    )
    try:
        env = os.environ.copy()
        env["A2DIR"] = ds
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, env=env, timeout=20)
        out = (res.stdout or "").strip().lower()
        return out if out in ("added", "exists") else "failed"
    except Exception:
        return "failed"


def _add_to_path_unix(d: Path) -> str:
    """Ensure *d* is exported on PATH from the user's shell rc files."""
    line = f'\n# Added by Agent2\nexport PATH="{d}:$PATH"\n'
    marker = "# Added by Agent2"
    touched = False
    for rc in (".bashrc", ".zshrc", ".profile"):
        p = Path.home() / rc
        try:
            existing = p.read_text(encoding="utf-8") if p.exists() else ""
            if marker in existing or str(d) in existing:
                continue
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
            touched = True
        except Exception:
            pass
    return "added" if touched else "exists"


def _wrapper_names() -> list[str]:
    """Filenames the agent2 launcher may use on this platform."""
    return ["agent2.bat", "agent2.cmd"] if IS_WIN else ["agent2"]


def _candidate_bin_dirs() -> list:
    """Every dir an agent2 wrapper might live in: the primary, both legacy
    locations, and every directory currently on PATH. De-duplicated, order-stable.
    A stale wrapper in ANY of these can shadow the real one, so we rewrite them all."""
    dirs = [
        _global_bin_dir(),
        Path.home() / ".local" / "bin",     # legacy (older installs used this on all OSes)
        Path.home() / ".agent2" / "bin",
    ]
    for p in os.environ.get("PATH", "").split(os.pathsep):
        p = p.strip().strip('"')
        if p:
            dirs.append(Path(p))
    seen, out = set(), []
    for d in dirs:
        try:
            key = str(d).rstrip("\\/").lower()
        except Exception:
            continue
        if key and key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _refresh_stale_wrappers(content: str, primary: Path) -> int:
    """Overwrite every EXISTING agent2 wrapper found outside *primary* so an old
    copy (e.g. one left in ~/.local/bin by a previous install, pointing at a moved
    or deleted run.py) can never shadow the freshly-installed command. Returns the
    count refreshed."""
    fixed = 0
    primary_key = str(primary).rstrip("\\/").lower()
    for d in _candidate_bin_dirs():
        if str(d).rstrip("\\/").lower() == primary_key:
            continue
        for name in _wrapper_names():
            wrapper = d / name
            try:
                if not wrapper.exists():
                    continue
                # Compare newline-insensitively so a wrapper we already wrote isn't
                # flagged stale just because of CRLF/LF translation.
                cur = wrapper.read_text(encoding="utf-8", errors="replace")
                if cur.replace("\r", "") == content.replace("\r", ""):
                    continue  # already correct
                wrapper.write_bytes(content.encode("utf-8"))  # exact bytes, no NL translation
                if not IS_WIN:
                    try: wrapper.chmod(0o755)
                    except Exception: pass
                print(f"  {g('[OK]')}  Refreshed stale launcher: {dim(str(wrapper))}")
                fixed += 1
            except Exception:
                pass
    return fixed


def install_global_command():
    print(f"\n  {w('[ Global Command ]')}\n")
    bin_dir = _global_bin_dir()

    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
        run_path = ROOT / "run.py"
        # Prefer the venv python if it exists (so the command works even if the
        # system python changes); fall back to whatever python is running setup.
        py = str(VENV_PY) if VENV_PY.exists() else sys.executable

        if IS_WIN:
            cmd_path = bin_dir / "agent2.bat"
            content = (
                "@echo off\r\n"
                f'if "%~1"=="" (\r\n'
                f'    "{py}" "{run_path}" --cli\r\n'
                ") else (\r\n"
                f'    "{py}" "{run_path}" %*\r\n'
                ")\r\n"
            )
            cmd_path.write_bytes(content.encode("utf-8"))  # exact bytes, no NL translation
        else:
            cmd_path = bin_dir / "agent2"
            content = (
                "#!/usr/bin/env bash\n"
                "if [ $# -eq 0 ]; then\n"
                f'    exec "{py}" "{run_path}" --cli\n'
                "else\n"
                f'    exec "{py}" "{run_path}" "$@"\n'
                "fi\n"
            )
            cmd_path.write_bytes(content.encode("utf-8"))
            try:
                cmd_path.chmod(0o755)
            except Exception:
                pass

        print(f"  {g('[OK]')}  Command installed: {c('agent2')} {dim('(' + str(cmd_path) + ')')}")

        # Self-heal: rewrite any OTHER agent2 wrapper already on PATH (e.g. a stale
        # one in ~/.local/bin from an older install) so it can't shadow this one
        # by pointing at an old or deleted run.py.
        _refresh_stale_wrappers(content, primary=bin_dir)

        # Make sure the directory is actually on PATH so `agent2` works anywhere.
        if _dir_on_path(bin_dir):
            print(f"  {g('[OK]')}  {dim(str(bin_dir) + ' already on PATH')}")
        else:
            state = _add_to_path_windows(bin_dir) if IS_WIN else _add_to_path_unix(bin_dir)
            if state == "added":
                print(f"  {g('[OK]')}  Added to PATH: {dim(str(bin_dir))}")
                if IS_WIN:
                    print(f"         {dim('Open a NEW terminal, then just type:')} {c('agent2')}")
                else:
                    print(f"         {dim('Run:')} {c('source ~/.bashrc')}  {dim('(or open a new terminal), then type:')} {c('agent2')}")
            elif state == "exists":
                print(f"  {g('[OK]')}  {dim('PATH already configured — open a new terminal, then type:')} {c('agent2')}")
            else:
                print(f"  {y('[!]')}  Couldn't auto-update PATH. Add this dir manually:")
                print(f"         {dim(str(bin_dir))}")
    except Exception as e:
        print(f"  {y('[!]')}  Failed to install global command: {e}")

# ── Step 6: Launch ─────────────────────────────────────────────────────────────
def launch_web():
    print(f"\n  {CY}{'═' * 46}{R}")
    print(f"  {g('>>>')}  Starting Agent2 Web UI ...")
    print(f"  {c('>>>')}  {w('http://localhost:1311')}")
    print(f"  {dim('       Ctrl+C to stop')}")
    print(f"  {CY}{'═' * 46}{R}\n")
    try:
        subprocess.run([str(VENV_PY), str(APP_WEB)])
    except KeyboardInterrupt:
        print(f"\n  {dim('Stopped.')}")

def launch_cli():
    if not APP_CLI.exists():
        print(r(f"\n  [ERR]  agent2cli.py not found at {APP_CLI}"))
        print(f"  {dim('  Place agent2cli.py in the same folder as run.py.')}")
        sys.exit(1)
    print(f"\n  {CY}{'═' * 46}{R}")
    print(f"  {g('>>>')}  Starting Agent2 CLI ...")
    print(f"  {dim('       Type /help for commands  |  Ctrl+C to exit')}")
    print(f"  {CY}{'═' * 46}{R}\n")
    try:
        subprocess.run([str(VENV_PY), str(APP_CLI)])
    except KeyboardInterrupt:
        print(f"\n  {dim('Stopped.')}")

# ── Main ───────────────────────────────────────────────────────────────────────
def show_help():
    banner()
    print(f"  {w('Usage:')}  python run.py [options]\n")
    print(f"  {c('--web')}       Setup + Start Web UI (default)")
    print(f"  {c('--cli')}       Setup + Start CLI Agent")
    print(f"  {c('--addapi')}    Add a new Gemini API key")
    print(f"  {c('--update, -up')} Update to the latest code (keeps your agent2.db)")
    print(f"  {c('--reset')}     Wipe .venv and reinstall packages")
    print(f"  {c('--uninstall')} Full cleanup (deletes DB, .env, and .venv)")
    print(f"  {c('--help, -h')}  Show this help menu\n")
    sys.exit(0)

def main():
    args = sys.argv[1:]
    
    # ── Command Handlers ─────────────────────────────────────────────────────
    if "--help" in args or "-h" in args:
        show_help()

    if "--uninstall" in args:
        uninstall()

    # Self-update: -up / -update / --up / --update
    if any(a in ("-up", "-update", "--up", "--update") for a in args):
        self_update()

    # First-run bootstrap: if only run.py exists, fetch the full project first.
    bootstrap_if_needed()

    do_reset  = "--reset"  in args
    do_addapi = "--addapi" in args
    do_cli    = "--cli"    in args
    do_web    = "--web"    in args

    # ── Setup Sequence ───────────────────────────────────────────────────────
    banner()
    check_python()
    ensure_venv(do_reset)
    mode = "all"
    if do_cli:
        mode = "cli"
    elif do_web:
        mode = "web"

    install_deps(mode)
    
    if do_addapi:
        ensure_keys(force_add=True)
        return

    ensure_keys()
    check_tools()
    install_global_command()

    # ── Mode Selection ───────────────────────────────────────────────────────
    if do_cli:
        launch_cli()
    elif do_web:
        launch_web()
    else:
        # Normal mode: Interactive selection with 10s timeout
        print(f"\n  {w('[ Select Mode ]')}  {dim('(Default: Web UI in 10s)')}")
        print(f"  {g('1.')}  Agent2 Web UI  {c('[Default]')}")
        print(f"  {g('2.')}  Agent2 CLI")
        print(f"  {g('3.')}  Exit")
        
        user_choice = [None]
        def get_input():
            try:
                user_choice[0] = input(f"\n  {CY}>>>{R} Choice [1-3]: ").strip()
            except EOFError: pass

        # Start input thread
        input_thread = threading.Thread(target=get_input, daemon=True)
        input_thread.start()
        
        # Wait 10 seconds
        input_thread.join(timeout=10.0)

        choice = user_choice[0]
        
        if choice is None:
            print(f"\n  {y('[timeout]')}  No response. Launching {g('Web UI')}...")
            time.sleep(1)
            launch_web()
        elif choice == '1':
            launch_web()
        elif choice == '2':
            launch_cli()
        elif choice == '3':
            print(f"\n  {dim('Goodbye.')}")
            sys.exit(0)
        else:
            print(f"\n  {y('[!]')}  Invalid choice. Defaulting to {g('Web UI')}...")
            launch_web()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n  \033[2mStopped by user (Ctrl+C).\033[0m")
        sys.exit(0)
