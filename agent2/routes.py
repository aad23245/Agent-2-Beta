# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

"""
agent2/routes.py
────────────────
All REST API endpoints registered on the Flask app.
Call register_routes(app) from main.py after creating the app instance.
"""

import uuid
from flask import request, jsonify

from agent2.config import (
    OS_NAME, SHELL_BIN, SHELL_LABEL,
    MODELS, MODES, DEFAULT_MODEL, DEFAULT_MODE,
)
from agent2.database import qall, qone, exe
from agent2.keys import rotator
from agent2 import providers


def register_routes(app) -> None:
    """Attach all /api/* routes to *app*."""

    # ── Chats ──────────────────────────────────────────────────────────────────

    @app.route("/api/chats", methods=["GET"])
    def api_list_chats():
        return jsonify(qall("SELECT * FROM chats ORDER BY updated_at DESC"))

    @app.route("/api/chats", methods=["POST"])
    def api_new_chat():
        d   = request.json or {}
        cid = str(uuid.uuid4())
        exe(
            "INSERT INTO chats(id, model, mode) VALUES(?, ?, ?)",
            (cid, d.get("model", DEFAULT_MODEL), d.get("mode", DEFAULT_MODE)),
        )
        return jsonify(qone("SELECT * FROM chats WHERE id=?", (cid,)))

    @app.route("/api/chats/<cid>", methods=["GET"])
    def api_get_chat(cid):
        chat = qone("SELECT * FROM chats WHERE id=?", (cid,))
        if not chat:
            return jsonify({"error": "not found"}), 404
        chat["messages"] = qall(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY created_at", (cid,)
        )
        return jsonify(chat)

    @app.route("/api/chats/<cid>", methods=["PUT"])
    def api_update_chat(cid):
        d = request.json or {}
        for col in ("title", "model", "mode"):
            v = d.get(col, "").strip()
            if v:
                exe(f"UPDATE chats SET {col}=? WHERE id=?", (v, cid))
        return jsonify(qone("SELECT * FROM chats WHERE id=?", (cid,)))

    @app.route("/api/chats/<cid>", methods=["DELETE"])
    def api_del_chat(cid):
        exe("DELETE FROM chats WHERE id=?", (cid,))
        return jsonify({"ok": True})

    # ── Memories ───────────────────────────────────────────────────────────────

    @app.route("/api/memories", methods=["GET"])
    def api_get_mems():
        return jsonify(qall("SELECT * FROM memories ORDER BY created_at DESC"))

    @app.route("/api/memories", methods=["POST"])
    def api_add_mem():
        content = (request.json or {}).get("content", "").strip()
        if not content:
            return jsonify({"error": "empty"}), 400
        mid = str(uuid.uuid4())
        exe("INSERT INTO memories(id, content) VALUES(?, ?)", (mid, content))
        return jsonify(qone("SELECT * FROM memories WHERE id=?", (mid,)))

    @app.route("/api/memories/<mid>", methods=["DELETE"])
    def api_del_mem(mid):
        exe("DELETE FROM memories WHERE id=?", (mid,))
        return jsonify({"ok": True})

    # ── Rules ──────────────────────────────────────────────────────────────────

    @app.route("/api/rules", methods=["GET"])
    def api_get_rules():
        return jsonify(qall("SELECT * FROM rules ORDER BY created_at DESC"))

    @app.route("/api/rules", methods=["POST"])
    def api_add_rule():
        content = (request.json or {}).get("content", "").strip()
        if not content:
            return jsonify({"error": "empty"}), 400
        rid = str(uuid.uuid4())
        exe("INSERT INTO rules(id, content) VALUES(?, ?)", (rid, content))
        return jsonify(qone("SELECT * FROM rules WHERE id=?", (rid,)))

    @app.route("/api/rules/<rid>", methods=["PUT"])
    def api_toggle_rule(rid):
        exe("UPDATE rules SET active=1-active WHERE id=?", (rid,))
        return jsonify(qone("SELECT * FROM rules WHERE id=?", (rid,)))

    @app.route("/api/rules/<rid>", methods=["DELETE"])
    def api_del_rule(rid):
        exe("DELETE FROM rules WHERE id=?", (rid,))
        return jsonify({"ok": True})

    # ── API Keys ───────────────────────────────────────────────────────────────

    @app.route("/api/keys", methods=["GET"])
    def api_get_keys():
        return jsonify(rotator.status())

    @app.route("/api/keys", methods=["POST"])
    def api_add_key():
        d    = request.json or {}
        key  = d.get("key", "").strip().replace(" ", "").replace("\n", "")
        name = d.get("name", "").strip()
        if not key or len(key) < 15:
            return jsonify({"error": "invalid key"}), 400
        if any(e["key"] == key for e in rotator.entries):
            return jsonify({"ok": False, "error": "already_exists"}), 409
        ok, label = rotator.add(key, name or None)
        return jsonify({"ok": ok, "label": label, "keys": rotator.status()})

    @app.route("/api/keys/<label>", methods=["PUT"])
    def api_update_key(label):
        d = request.json or {}
        if "name" in d:
            rotator.set_name(label, d["name"])
        return jsonify({"ok": True, "keys": rotator.status()})

    @app.route("/api/keys/<label>", methods=["DELETE"])
    def api_del_key(label):
        rotator.remove(label)
        return jsonify({"ok": True, "keys": rotator.status()})

    @app.route("/api/keys/<label>/reset", methods=["POST"])
    def api_reset_key(label):
        rotator.reset_key(label)
        return jsonify({"ok": True, "keys": rotator.status()})

    @app.route("/api/keys/<label>/pin", methods=["POST"])
    def api_pin_key(label):
        d = request.json or {}
        rotator.pin(label if d.get("pin") else None)
        return jsonify({"ok": True, "keys": rotator.status()})

    # ── Custom providers (bring your own API) ──────────────────────────────────

    @app.route("/api/providers", methods=["GET"])
    def api_get_providers():
        return jsonify(providers.list_providers(safe=True))

    @app.route("/api/providers", methods=["POST"])
    def api_add_provider():
        d = request.json or {}
        base_url = (d.get("base_url") or "").strip()
        api_key  = (d.get("api_key")  or "").strip()
        model_id = (d.get("model_id") or "").strip()
        name     = (d.get("name")     or "").strip()
        fmt      = (d.get("format")   or "openai").strip().lower()
        user_agent = (d.get("user_agent") or "").strip()
        if not base_url or not api_key or not model_id:
            return jsonify({"error": "base_url, api_key and model_id are required"}), 400
        if not base_url.startswith("http"):
            return jsonify({"error": "base_url must start with http(s)://"}), 400
        p = providers.add_provider(name, base_url, api_key, model_id, fmt, user_agent)
        return jsonify({"ok": True, "provider": p,
                        "providers": providers.list_providers(safe=True)})

    @app.route("/api/providers/<pid>", methods=["DELETE"])
    def api_del_provider(pid):
        providers.remove_provider(pid)
        return jsonify({"ok": True, "providers": providers.list_providers(safe=True)})

    @app.route("/api/providers/<pid>/test", methods=["POST"])
    def api_test_provider(pid):
        prov = providers.get_provider(pid)
        if not prov:
            return jsonify({"ok": False, "error": "not found"}), 404
        try:
            r = providers.chat(prov, [{"role": "user", "content": "Reply with the single word: OK"}],
                               "You are a connection test. Reply concisely.")
            return jsonify({"ok": True, "text": (r.get("text") or "")[:200],
                            "tokens": r.get("tokens", 0)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)[:400]}), 200

    # ── Burp Suite MCP bridge ──────────────────────────────────────────────────

    @app.route("/api/burp", methods=["GET"])
    def api_burp_status():
        from agent2.burp_mcp import burp
        return jsonify(burp.status())

    @app.route("/api/burp/connect", methods=["POST"])
    def api_burp_connect():
        from agent2.burp_mcp import burp
        d = request.json or {}
        url = (d.get("url") or "").strip()
        if url:
            burp.url = url
        ok, msg = burp.connect()
        return jsonify({"ok": ok, "message": msg, "status": burp.status()})

    @app.route("/api/burp/disconnect", methods=["POST"])
    def api_burp_disconnect():
        from agent2.burp_mcp import burp
        burp.disconnect()
        return jsonify({"ok": True, "message": "Disconnected.", "status": burp.status()})

    @app.route("/api/burp/auto", methods=["POST"])
    def api_burp_auto():
        """Toggle auto-connect (connect on every agent turn)."""
        from agent2.burp_mcp import burp
        d = request.json or {}
        burp.set_auto_connect(bool(d.get("enabled")))
        return jsonify({"ok": True, "status": burp.status()})

    # ── Platform info ──────────────────────────────────────────────────────────

    @app.route("/api/platform", methods=["GET"])
    def api_platform():
        return jsonify({
            "os":            OS_NAME,
            "shell":         SHELL_LABEL,
            "shell_bin":     SHELL_BIN,
            "models":        MODELS,
            "modes":         MODES,
            "providers":     providers.list_providers(safe=True),
            "default_model": DEFAULT_MODEL,
            "default_mode":  DEFAULT_MODE,
        })
