#!/usr/bin/env python3
# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2web.py — Agent2 entry point
────────────────────────────
Wires together the agent2 package and starts the Flask-SocketIO server.

Project layout
──────────────
  agent2web.py         ← you are here  (python run.py  or  python agent2web.py)
  agent2cli.py         ← agent2 CLI entry point (python agent2cli.py)
  run.py               ← cross-platform setup launcher (creates venv, installs deps)
  agent2.db            ← SQLite database — API keys, memories, rules, providers
  agent2/
    __init__.py
    config.py          ← platform detection, models, modes, constants, root paths
    database.py        ← SQLite helpers (qall / qone / exe) + schema + migrations
    keys.py            ← KeyRotator: multi-key, rotation, pinning, usage tracking
    terminal.py        ← stream_command, stdin injection, kill, stop events
    agent.py           ← system_prompt, build_context, run_agent agentic loop
    routes.py          ← all /api/* REST endpoints
    sockets.py         ← all Socket.IO event handlers
    ui.py              ← full HTML / CSS / JS single-page frontend
"""

#!/usr/bin/env python3
"""
agent2web.py — Agent2 entry point
"""

# ─────────────────────────────────────────────────────────────────────────────
# 🔒 VENV ENFORCEMENT (MUST RUN FIRST)
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"
IS_WIN = os.name == "nt"


def in_venv():
    """Detect if currently inside a virtual environment."""
    return (
        hasattr(sys, "real_prefix") or
        (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    )


def get_venv_python():
    """Return platform-specific venv python path."""
    if IS_WIN:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv():
    """Ensure execution inside venv, else restart using it."""
    if in_venv():
        return

    venv_python = get_venv_python()

    if not venv_python.exists():
        print("\n  [ERR] Virtual environment not found or not initialized.")
        print("       Please run one of the following:\n")
        print("         python run.py")
        print("         python run.py --reset\n")
        sys.exit(1)

    print("\n  [INFO] Switching to virtual environment...\n")

    try:
        os.execv(
            str(venv_python),
            [str(venv_python)] + sys.argv
        )
    except Exception as e:
        print(f"\n  [ERR] Failed to switch to venv: {e}")
        print("\n  Suggested fix:")
        print("     python run.py")
        print("     python run.py --reset\n")
        sys.exit(1)


# 🔥 Enforce venv BEFORE any imports that rely on dependencies
ensure_venv()


# ─────────────────────────────────────────────────────────────────────────────
# 📦 Imports (safe after venv enforcement)
# ─────────────────────────────────────────────────────────────────────────────

from flask import Flask, Response
from flask_socketio import SocketIO
from flask import send_from_directory

# All API keys (Gemini + custom providers) live in agent2.db — no .env.
from agent2.config   import OS_NAME, SHELL_LABEL
from agent2.database import init_db
init_db()
from agent2.providers import init_providers_table
init_providers_table()
from agent2.keys     import rotator
from agent2.routes   import register_routes
from agent2.sockets  import register_sockets
from agent2.ui       import get_html



# ─────────────────────────────────────────────────────────────────────────────
# 🌐 App Setup
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or os.urandom(32)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading"
)


# ─────────────────────────────────────────────────────────────────────────────
# 🔌 Register Components
# ─────────────────────────────────────────────────────────────────────────────

register_routes(app)
register_sockets(socketio)


# ─────────────────────────────────────────────────────────────────────────────
# 🖥️ Frontend
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return Response(get_html(), mimetype="text/html")

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'public'), 
        'favicon.ico', 
        mimetype='image/vnd.microsoft.icon'
    )
@app.route('/style.css')
def style():
    return send_from_directory(
        os.path.join(app.root_path, 'public'), 
        'style.css', 
        mimetype='text/css'
    )

@app.route('/script.js')
def script():
    return send_from_directory(
        os.path.join(app.root_path, 'public'), 
        'script.js', 
        mimetype='application/javascript'
    )

# ─────────────────────────────────────────────────────────────────────────────
# 🚀 Main Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        init_db()

        print(f"\n  Agent2  [{OS_NAME} / {SHELL_LABEL}]")
        print("  " + "=" * 46)

        keys = rotator.status()

        if not keys:
            print("  [!]  No API keys configured")
            print("       Add one via:  python run.py --addapi")
            print("       Or open Settings in the web UI after starting.")
        else:
            for k in keys:
                st  = "active" if k["active"] else "exhausted"
                tok = f"{k['tokens']:,}" if k.get("tokens") else "0"
                pin = " [pinned]" if k.get("pinned") else ""

                print(
                    f"  [{'OK' if k['active'] else '!'}]  "
                    f"#{k['label']} {k['name']}: {k['preview']}  "
                    f"[{st}]{pin}  {tok} tokens"
                )

        print(f"  [>>] http://localhost:1311")
        print("  " + "=" * 46 + "\n")

        socketio.run(
            app,
            host="0.0.0.0",
            port=1311,
            debug=False,
            allow_unsafe_werkzeug=True,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 🛑 Graceful Shutdown
    # ─────────────────────────────────────────────────────────────────────────
    except KeyboardInterrupt:
        print("\n\n  [INFO] Shutting down Agent2 gracefully...")
        print("  [OK] Cleanup complete. Bye.\n")
        sys.exit(0)

    # ─────────────────────────────────────────────────────────────────────────
    # 💥 Fail-Safe Error Handler
    # ─────────────────────────────────────────────────────────────────────────
    except Exception as e:
        print("\n  [FATAL] Runtime error occurred:")
        print(f"          {str(e)[:300]}\n")

        print("  Suggested fix:")
        print("     python run.py")
        print("     python run.py --reset\n")

        sys.exit(1)