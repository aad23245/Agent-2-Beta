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

    # ── Platform info ──────────────────────────────────────────────────────────

    @app.route("/api/platform", methods=["GET"])
    def api_platform():
        return jsonify({
            "os":            OS_NAME,
            "shell":         SHELL_LABEL,
            "shell_bin":     SHELL_BIN,
            "models":        MODELS,
            "modes":         MODES,
            "default_model": DEFAULT_MODEL,
            "default_mode":  DEFAULT_MODE,
        })
