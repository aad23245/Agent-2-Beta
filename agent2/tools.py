# Author: Aarav Shah
# Portfolio: aaravshah1311.is-great.net
# github: github.com/aaravshah1311

import os
import json
import uuid
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from google.genai import types
from agent2.config import IS_WIN, IS_MAC, OS_NAME, SHELL_LABEL
from agent2.database import exe

MAX_FILE = 100_000   # max chars returned by read_file

def status_line(msg, typ=None):
    pass


def add_mem(content: str, importance: int = 5, tags=None) -> None:
    """Persist a memory into the web app's memories table (id, content)."""
    try:
        exe("INSERT INTO memories(id, content) VALUES(?, ?)",
            (str(uuid.uuid4()), content))
    except Exception:
        pass


def print_plan(title, steps) -> None:
    """No-op in the web context; the plan is surfaced via the tool result."""
    pass

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

def _impl_list_dir(args: dict) -> dict:
    raw = args.get("path", ".")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    p = p.resolve()
    if not p.exists():
        return {"error": f"Not found: {p}"}
    if p.is_file():
        return {"path": str(p), "is_file": True, "size": p.stat().st_size}
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
    entries = []
    try:
        for item in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if item.name in skip:
                continue
            if item.is_dir():
                entries.append(f"{item.name}/")
            else:
                entries.append(f"{item.name}  ({item.stat().st_size} B)")
        return {"path": str(p), "count": len(entries), "entries": entries}
    except Exception as ex:
        return {"error": str(ex)}


def _impl_delete_file(args: dict) -> dict:
    import shutil as _sh
    p = Path(args.get("path", "")).expanduser()
    if not p.exists():
        return {"error": f"Not found: {p}"}
    try:
        if p.is_dir():
            _sh.rmtree(p)
            return {"success": True, "deleted": str(p), "type": "dir"}
        p.unlink()
        return {"success": True, "deleted": str(p), "type": "file"}
    except Exception as ex:
        return {"error": str(ex)}


def _impl_grep(args: dict) -> dict:
    import re as _re
    pattern = args.get("pattern", "")
    if not pattern:
        return {"error": "pattern required"}
    raw = args.get("path", ".")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        root = Path(os.getcwd()) / root
    root = root.resolve()
    glob = args.get("glob", "")
    try:
        rx = _re.compile(pattern)
    except Exception as ex:
        return {"error": f"bad regex: {ex}"}
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
    hits, scanned = [], 0
    targets = [root] if root.is_file() else root.rglob(glob or "*")
    for fp in targets:
        if not fp.is_file():
            continue
        if any(part in skip for part in fp.parts):
            continue
        try:
            scanned += 1
            for i, line in enumerate(fp.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{fp}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 200:
                        return {"pattern": pattern, "matches": hits, "truncated": True, "files_scanned": scanned}
        except Exception:
            pass
    return {"pattern": pattern, "matches": hits, "match_count": len(hits), "files_scanned": scanned}


# In-memory todo store (per process) for the plan/todo tracker.
_TODO_STATE: list[dict] = []

def _impl_update_todo(args: dict) -> dict:
    """Maintain a live task list so the agent can plan and track a big build."""
    global _TODO_STATE
    todos = args.get("todos")
    if isinstance(todos, list):
        _TODO_STATE = []
        for t in todos:
            if isinstance(t, dict):
                _TODO_STATE.append({
                    "task":   str(t.get("task", ""))[:200],
                    "status": t.get("status", "pending"),
                })
            else:
                _TODO_STATE.append({"task": str(t)[:200], "status": "pending"})
    done = sum(1 for t in _TODO_STATE if t["status"] == "completed")
    return {
        "todos": _TODO_STATE,
        "total": len(_TODO_STATE),
        "completed": done,
        "progress": f"{done}/{len(_TODO_STATE)}",
    }


def dispatch_tool(name: str, args: dict) -> dict:
    if name == "read_file":        return _impl_read(args)
    if name == "write_file":       return _impl_write(args)
    if name == "web_search":       return _impl_search(args)
    if name == "save_memory":      return _impl_save_mem(args)
    if name == "emit_plan":        return _impl_plan(args)
    if name == "scan_project":     return _impl_scan_project(args)
    if name == "multi_edit_files": return _impl_multi_edit(args)
    if name == "list_dir":         return _impl_list_dir(args)
    if name == "delete_file":      return _impl_delete_file(args)
    if name == "grep_search":      return _impl_grep(args)
    if name == "update_todo":      return _impl_update_todo(args)
    return {"error": f"Unknown tool: {name}"}

# ── Gemini tool declarations ────────────────────────────────────────────────────
def _build_tools() -> types.Tool:
    S = types.Schema; T = types.Type
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(name="run_command",
            description=f"Execute a shell command on {OS_NAME} ({SHELL_LABEL}). Use for running scripts, installs, scans, builds.",
            parameters=S(type=T.OBJECT, properties={
                "command":     S(type=T.STRING),
                "description": S(type=T.STRING),
                "cwd":         S(type=T.STRING),
            }, required=["command","description"])),
        types.FunctionDeclaration(name="read_file",
            description="Read a file's contents. Always read before editing.",
            parameters=S(type=T.OBJECT, properties={
                "path":       S(type=T.STRING),
                "start_line": S(type=T.INTEGER),
                "end_line":   S(type=T.INTEGER),
            }, required=["path"])),
        types.FunctionDeclaration(name="write_file",
            description="Create or overwrite a file with the given content. Use for creating new files. Parent directories are created automatically.",
            parameters=S(type=T.OBJECT, properties={
                "path":    S(type=T.STRING),
                "content": S(type=T.STRING),
            }, required=["path","content"])),
        types.FunctionDeclaration(name="web_search",
            description="Search the web for CVEs, docs, error messages, latest info.",
            parameters=S(type=T.OBJECT, properties={
                "query":       S(type=T.STRING),
                "max_results": S(type=T.INTEGER),
            }, required=["query"])),
        types.FunctionDeclaration(name="save_memory",
            description="Save an important fact to long-term memory (persists across sessions).",
            parameters=S(type=T.OBJECT, properties={
                "content":    S(type=T.STRING),
                "importance": S(type=T.INTEGER),
                "tags":       S(type=T.STRING),
            }, required=["content"])),
        types.FunctionDeclaration(name="emit_plan",
            description="Show a step-by-step plan before a complex multi-step task.",
            parameters=S(type=T.OBJECT, properties={
                "title": S(type=T.STRING),
                "steps": S(type=T.STRING),
            }, required=["title","steps"])),
        types.FunctionDeclaration(name="scan_project",
            description="Recursively scan a project directory and return a file tree + content of ALL code/config files. Use this AUTOMATICALLY whenever the user mentions a project, asks to check code, add features, or fix bugs. Pass the project path.",
            parameters=S(type=T.OBJECT, properties={
                "path": S(type=T.STRING),
            }, required=["path"])),
        types.FunctionDeclaration(name="multi_edit_files",
            description="Edit multiple files at once by replacing exact text snippets. Each edit has path, old_text (exact match), new_text (replacement). Use for renaming, refactoring, or patching across files.",
            parameters=S(type=T.OBJECT, properties={
                "edits": S(type=T.ARRAY, items=S(type=T.OBJECT, properties={
                    "path": S(type=T.STRING),
                    "old_text": S(type=T.STRING),
                    "new_text": S(type=T.STRING)
                }))
            }, required=["edits"])),
        types.FunctionDeclaration(name="list_dir",
            description="List the contents of a directory (files + subfolders). Use to explore a project's structure before reading or editing.",
            parameters=S(type=T.OBJECT, properties={
                "path": S(type=T.STRING),
            }, required=["path"])),
        types.FunctionDeclaration(name="delete_file",
            description="Delete a file or directory (recursively). Use when refactoring or removing generated artifacts.",
            parameters=S(type=T.OBJECT, properties={
                "path": S(type=T.STRING),
            }, required=["path"])),
        types.FunctionDeclaration(name="grep_search",
            description="Search file contents by regex across a directory tree. Returns file:line: matches. Use to locate symbols, functions, or usages in a codebase.",
            parameters=S(type=T.OBJECT, properties={
                "pattern": S(type=T.STRING),
                "path":    S(type=T.STRING),
                "glob":    S(type=T.STRING),
            }, required=["pattern"])),
        types.FunctionDeclaration(name="update_todo",
            description="Create/update a live TODO checklist for a multi-step build. Pass the full list each time with each item's status (pending|in_progress|completed). Call this FIRST for any big task, then update statuses as you finish steps so the user sees progress.",
            parameters=S(type=T.OBJECT, properties={
                "todos": S(type=T.ARRAY, items=S(type=T.OBJECT, properties={
                    "task":   S(type=T.STRING),
                    "status": S(type=T.STRING),
                }))
            }, required=["todos"])),
    ])

