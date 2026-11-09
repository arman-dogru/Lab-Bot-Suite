#!/usr/bin/env python3
"""Project Hub — personal project manager. Python stdlib only."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(os.environ.get("PROJECT_HUB_ROOT", Path(__file__).resolve().parent))
DATA = Path(os.environ.get("PROJECT_HUB_DATA", ROOT / "data"))
DB_PATH = DATA / "hub.sqlite"
UPLOADS = DATA / "uploads"
SECRET_PATH = Path(os.environ.get("PROJECT_HUB_SECRET", ROOT / "secrets" / "webhook.token"))
HOST = os.environ.get("PROJECT_HUB_HOST", "127.0.0.1")
PORT = int(os.environ.get("PROJECT_HUB_PORT", "8787"))
MAX_BODY = 8 * 1024 * 1024
MAX_UPLOAD = 5 * 1024 * 1024
MAX_JSON = 256 * 1024
TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{7,127}")
STATUSES = ("todo", "doing", "done")
PRIORITIES = ("low", "medium", "high", "urgent")
STATUS_LABEL = {"todo": "To do", "doing": "Doing", "done": "Done"}

_db_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with _db_lock:
        conn = connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              archived INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              details TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'todo',
              priority TEXT NOT NULL DEFAULT 'medium',
              due_date TEXT,
              position INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              revision INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS note_revisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
              revision INTEGER NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
              filename TEXT NOT NULL,
              stored_name TEXT NOT NULL,
              content_type TEXT NOT NULL,
              size INTEGER NOT NULL,
              sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, status, position);
            CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project_id);
            CREATE INDEX IF NOT EXISTS idx_docs_project ON documents(project_id);
            """
        )
        conn.commit()
        conn.close()


def seed_demo(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT id FROM projects WHERE id = ?", ("jetson-lab",)).fetchone()
    if row:
        return
    ts = now_iso()
    conn.execute(
        "INSERT INTO projects (id, name, description, archived, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (
            "jetson-lab",
            "Jetson Lab",
            "Personal lab notes for the Jetson Nano workstation: thermal, power, and quiet-hours experiments.",
            0,
            ts,
            ts,
        ),
    )
    tasks = [
        ("lab-fan-curve", "Map fan curve vs SoC temperature", "Record idle, compile, and inference temps.", "doing", "high", "2026-11-06", 0),
        ("lab-power-budget", "Confirm 5V barrel supply headroom", "Measure draw under full CPU load.", "todo", "urgent", "2026-11-03", 1),
        ("lab-quiet-hours", "Document quiet-hours cron window", "Keep nightly jobs out of the chat channel.", "todo", "medium", "2026-11-10", 2),
        ("lab-snapshot", "Practice workspace snapshot restore", "Verify SQLite and uploads survive a restart.", "done", "low", "2026-10-28", 3),
    ]
    for tid, title, details, status, priority, due, pos in tasks:
        conn.execute(
            """INSERT INTO tasks (id, project_id, title, details, status, priority, due_date, position, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (tid, "jetson-lab", title, details, status, priority, due, pos, ts, ts),
        )
    notes = [
        (
            "lab-bench",
            "Bench setup",
            "# Bench setup\n\n- Board: Jetson Nano\n- Power: official barrel supply\n- Network: Ethernet only\n- Workspace bind: `/srv/jetson-bot/workspace`\n\nKeep experiments local. Do not touch the bot or budget services.",
        ),
        (
            "lab-thermal",
            "Thermal notes",
            "# Thermal notes\n\nIdle sits near 42 C with the stock fan. A full compile pushes the SoC past 70 C if the fan curve is left on the default step.\n\nNext check: log `tegrastats` for ten minutes during a clean build.",
        ),
    ]
    for nid, title, body in notes:
        conn.execute(
            "INSERT INTO notes (id, project_id, title, body, revision, created_at, updated_at) VALUES (?,?,?,?,1,?,?)",
            (nid, "jetson-lab", title, body, ts, ts),
        )
        conn.execute(
            "INSERT INTO note_revisions (note_id, revision, title, body, created_at) VALUES (?,?,?,?,?)",
            (nid, 1, title, body, ts),
        )
    conn.commit()


def load_secret() -> str:
    if not SECRET_PATH.is_file():
        raise SystemExit(f"webhook secret file missing: {SECRET_PATH}")
    secret = SECRET_PATH.read_text(encoding="utf-8").strip()
    if not secret or secret.startswith("#"):
        raise SystemExit("webhook secret file is empty")
    return secret


def safe_filename(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not base or base in {".", ".."}:
        base = "file"
    return base[:120]


def row_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def project_or_404(conn, pid: str):
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return row


class App:
    def __init__(self, secret: str):
        self.secret = secret

    def handle(self, method: str, path: str, query: dict, headers, body: bytes):
        parsed = urlparse(path)
        parts = [unquote(p) for p in parsed.path.split("/") if p]
        if method == "GET" and parts == ["health"]:
            return 200, {"ok": True, "service": "project-hub"}, "json"
        if method == "POST" and parts == ["webhook", "tasks"]:
            return self.webhook_create(headers, body)
        if method == "GET" and not parts:
            return 200, self.render_home(query.get("q", [""])[0]), "html"
        if method == "GET" and parts == ["static", "app.css"]:
            return 200, CSS, "css"
        if method == "GET" and parts == ["static", "app.js"]:
            return 200, JS, "js"
        if method == "GET" and len(parts) == 2 and parts[0] == "projects" and parts[1] != "new":
            return 200, self.render_project(parts[1], query), "html"
        if method == "POST" and parts == ["projects"]:
            return self.create_project(body, headers)
        if len(parts) >= 2 and parts[0] == "projects":
            return self.project_routes(method, parts, query, headers, body)
        if method == "GET" and parts == ["search"]:
            return 200, self.search(query.get("q", [""])[0]), "json"
        return 404, {"error": "not found"}, "json"

    def project_routes(self, method, parts, query, headers, body):
        pid = parts[1]
        with _db_lock:
            conn = connect()
            try:
                project = project_or_404(conn, pid)
                if not project:
                    return 404, {"error": "project not found"}, "json"
                if method == "POST" and parts[2:] == ["archive"]:
                    return self._set_archived(conn, project, 1)
                if method == "POST" and parts[2:] == ["reopen"]:
                    return self._set_archived(conn, project, 0)
                if method == "POST" and parts[2:] == ["update"]:
                    return self._update_project(conn, project, body)
                if method == "GET" and parts[2:] == ["export.json"]:
                    return 200, self._export(conn, project), "json-download"
                if method == "GET" and parts[2:] == ["export.md"]:
                    return 200, self._export_md(conn, project), "md-download"
                if method == "POST" and parts[2:] == ["tasks"]:
                    return self._create_task(conn, project, body)
                if method == "POST" and len(parts) == 5 and parts[2] == "tasks" and parts[4] == "update":
                    return self._update_task(conn, project, parts[3], body)
                if method == "POST" and len(parts) == 5 and parts[2] == "tasks" and parts[4] == "delete":
                    return self._delete_task(conn, project, parts[3])
                if method == "POST" and parts[2:] == ["notes"]:
                    return self._create_note(conn, project, body)
                if method == "POST" and len(parts) == 5 and parts[2] == "notes" and parts[4] == "update":
                    return self._update_note(conn, project, parts[3], body)
                if method == "GET" and len(parts) == 5 and parts[2] == "notes" and parts[4] == "revisions":
                    return 200, self._note_revisions(conn, project, parts[3]), "json"
                if method == "POST" and parts[2:] == ["documents"]:
                    return self._upload(conn, project, headers, body)
                if method == "GET" and len(parts) == 4 and parts[2] == "documents":
                    return self._download(conn, project, parts[3])
                return 404, {"error": "not found"}, "json"
            finally:
                conn.close()

    def _form(self, body: bytes, headers) -> dict:
        ctype = headers.get("Content-Type", "")
        if "application/json" in ctype:
            if len(body) > MAX_JSON:
                raise ValueError("JSON body too large")
            data = json.loads(body.decode("utf-8") or "{}")
            if not isinstance(data, dict):
                raise ValueError("JSON object required")
            return data
        parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        return {k: v[-1] if v else "" for k, v in parsed.items()}

    def _loopback_authorities(self) -> set[str]:
        """Exact loopback host:port pairs for this live server, not prefix matches."""
        ports = {PORT}
        server = getattr(self, "server", None)
        if server is None:
            server = getattr(self, "_bound_server", None)
        if server is not None:
            addr = getattr(server, "server_address", None)
            if addr and len(addr) >= 2 and addr[1]:
                ports.add(int(addr[1]))
        hosts = {"127.0.0.1", "localhost", HOST}
        return {f"{host}:{port}" for host in hosts for port in ports if host}

    def _request_authority(self, value: str) -> str | None:
        if not value:
            return None
        parsed = urlparse(value)
        if parsed.scheme != "http" or not parsed.hostname:
            return None
        try:
            port = parsed.port or 80
        except ValueError:
            return None
        return f"{parsed.hostname}:{port}"

    def _cookies(self, headers) -> dict:
        raw = headers.get("Cookie") or ""
        out = {}
        for part in raw.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            out[name.strip()] = value.strip()
        return out

    def _expected_session(self) -> str:
        return hashlib.sha256(b"project-hub-session:" + self.secret.encode()).hexdigest()

    def _session_cookie_header(self) -> str:
        return f"project_hub_session={self._expected_session()}; HttpOnly; SameSite=Strict; Path=/"

    def _browser_form(self, headers) -> bool:
        """Accept a real browser form POST only with the page cookie plus same-origin Origin or Referer."""
        cookie = self._cookies(headers).get("project_hub_session", "")
        if not cookie or not hmac.compare_digest(cookie, self._expected_session()):
            return False
        origin = headers.get("Origin") or ""
        referer = headers.get("Referer") or ""
        source = origin or referer
        if not source:
            return False
        return self._request_authority(source) in self._loopback_authorities()

    def _require_origin(self, headers) -> str | None:
        origin = headers.get("Origin") or ""
        referer = headers.get("Referer") or ""
        source = origin or referer
        if source:
            authority = self._request_authority(source)
            if authority not in self._loopback_authorities():
                return "cross-origin state change refused"
        token = headers.get("X-Requested-With", "")
        if token == "ProjectHub":
            return None
        if self._browser_form(headers):
            return None
        return "missing X-Requested-With"

    def create_project(self, body, headers):
        err = self._require_origin(headers)
        if err:
            return 403, {"error": err}, "json"
        try:
            data = self._form(body, headers)
        except ValueError as exc:
            return 400, {"error": str(exc)}, "json"
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 120:
            return 400, {"error": "name is required (1-120 chars)"}, "json"
        description = str(data.get("description", "")).strip()[:4000]
        ts = now_iso()
        pid = new_id()
        with _db_lock:
            conn = connect()
            conn.execute(
                "INSERT INTO projects (id, name, description, archived, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (pid, name, description, 0, ts, ts),
            )
            conn.commit()
            conn.close()
        return 303, f"/projects/{pid}", "redirect"

    def _set_archived(self, conn, project, archived: int):
        ts = now_iso()
        conn.execute("UPDATE projects SET archived = ?, updated_at = ? WHERE id = ?", (archived, ts, project["id"]))
        conn.commit()
        return 303, f"/projects/{project['id']}", "redirect"

    def _update_project(self, conn, project, body):
        try:
            data = self._form(body, {"Content-Type": "application/x-www-form-urlencoded"})
        except ValueError as exc:
            return 400, {"error": str(exc)}, "json"
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 120:
            return 400, {"error": "name is required (1-120 chars)"}, "json"
        description = str(data.get("description", "")).strip()[:4000]
        conn.execute(
            "UPDATE projects SET name = ?, description = ?, updated_at = ? WHERE id = ?",
            (name, description, now_iso(), project["id"]),
        )
        conn.commit()
        return 303, f"/projects/{project['id']}", "redirect"

    def _create_task(self, conn, project, body):
        try:
            data = self._form(body, {"Content-Type": "application/x-www-form-urlencoded"})
        except ValueError as exc:
            return 400, {"error": str(exc)}, "json"
        return self._insert_task(conn, project["id"], data, redirect=True)

    def _insert_task(self, conn, project_id, data, redirect=False):
        title = str(data.get("title", "")).strip()
        if not title or len(title) > 200:
            return 400, {"error": "title is required (1-200 chars)"}, "json"
        status = str(data.get("status", "todo")).strip()
        priority = str(data.get("priority", "medium")).strip()
        if status not in STATUSES:
            return 400, {"error": "status must be todo, doing, or done"}, "json"
        if priority not in PRIORITIES:
            return 400, {"error": "priority must be low, medium, high, or urgent"}, "json"
        due = str(data.get("due_date") or "").strip() or None
        if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            return 400, {"error": "due_date must be YYYY-MM-DD"}, "json"
        details = str(data.get("details", "")).strip()[:4000]
        pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS n FROM tasks WHERE project_id = ? AND status = ?",
            (project_id, status),
        ).fetchone()["n"]
        ts = now_iso()
        tid = new_id()
        conn.execute(
            """INSERT INTO tasks (id, project_id, title, details, status, priority, due_date, position, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (tid, project_id, title, details, status, priority, due, pos, ts, ts),
        )
        conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (ts, project_id))
        conn.commit()
        if redirect:
            return 303, f"/projects/{project_id}", "redirect"
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        return 201, row_dict(row), "json"

    def _update_task(self, conn, project, tid, body):
        task = conn.execute("SELECT * FROM tasks WHERE id = ? AND project_id = ?", (tid, project["id"])).fetchone()
        if not task:
            return 404, {"error": "task not found"}, "json"
        try:
            data = self._form(body, {"Content-Type": "application/x-www-form-urlencoded"})
        except ValueError as exc:
            return 400, {"error": str(exc)}, "json"
        title = str(data.get("title", task["title"])).strip()
        if not title or len(title) > 200:
            return 400, {"error": "title is required (1-200 chars)"}, "json"
        status = str(data.get("status", task["status"])).strip()
        priority = str(data.get("priority", task["priority"])).strip()
        if status not in STATUSES or priority not in PRIORITIES:
            return 400, {"error": "invalid status or priority"}, "json"
        due = str(data.get("due_date") or "").strip() or None
        if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            return 400, {"error": "due_date must be YYYY-MM-DD"}, "json"
        details = str(data.get("details", task["details"])).strip()[:4000]
        conn.execute(
            """UPDATE tasks SET title=?, details=?, status=?, priority=?, due_date=?, updated_at=?
               WHERE id=? AND project_id=?""",
            (title, details, status, priority, due, now_iso(), tid, project["id"]),
        )
        conn.commit()
        return 303, f"/projects/{project['id']}", "redirect"

    def _delete_task(self, conn, project, tid):
        cur = conn.execute("DELETE FROM tasks WHERE id = ? AND project_id = ?", (tid, project["id"]))
        conn.commit()
        if cur.rowcount == 0:
            return 404, {"error": "task not found"}, "json"
        return 303, f"/projects/{project['id']}", "redirect"

    def _create_note(self, conn, project, body):
        try:
            data = self._form(body, {"Content-Type": "application/x-www-form-urlencoded"})
        except ValueError as exc:
            return 400, {"error": str(exc)}, "json"
        title = str(data.get("title", "")).strip()
        if not title or len(title) > 160:
            return 400, {"error": "title is required (1-160 chars)"}, "json"
        text = str(data.get("body", "")).strip()[:20000]
        ts = now_iso()
        nid = new_id()
        conn.execute(
            "INSERT INTO notes (id, project_id, title, body, revision, created_at, updated_at) VALUES (?,?,?,?,1,?,?)",
            (nid, project["id"], title, text, ts, ts),
        )
        conn.execute(
            "INSERT INTO note_revisions (note_id, revision, title, body, created_at) VALUES (?,?,?,?,?)",
            (nid, 1, title, text, ts),
        )
        conn.commit()
        return 303, f"/projects/{project['id']}#notes", "redirect"

    def _update_note(self, conn, project, nid, body):
        note = conn.execute("SELECT * FROM notes WHERE id = ? AND project_id = ?", (nid, project["id"])).fetchone()
        if not note:
            return 404, {"error": "note not found"}, "json"
        try:
            data = self._form(body, {"Content-Type": "application/x-www-form-urlencoded"})
        except ValueError as exc:
            return 400, {"error": str(exc)}, "json"
        title = str(data.get("title", note["title"])).strip()
        if not title or len(title) > 160:
            return 400, {"error": "title is required (1-160 chars)"}, "json"
        text = str(data.get("body", "")).strip()[:20000]
        if title == note["title"] and text == note["body"]:
            return 303, f"/projects/{project['id']}#notes", "redirect"
        rev = note["revision"] + 1
        ts = now_iso()
        conn.execute(
            "UPDATE notes SET title=?, body=?, revision=?, updated_at=? WHERE id=? AND project_id=?",
            (title, text, rev, ts, nid, project["id"]),
        )
        conn.execute(
            "INSERT INTO note_revisions (note_id, revision, title, body, created_at) VALUES (?,?,?,?,?)",
            (nid, rev, title, text, ts),
        )
        conn.commit()
        return 303, f"/projects/{project['id']}#notes", "redirect"

    def _note_revisions(self, conn, project, nid):
        note = conn.execute("SELECT id FROM notes WHERE id = ? AND project_id = ?", (nid, project["id"])).fetchone()
        if not note:
            return {"error": "note not found"}
        rows = conn.execute(
            "SELECT revision, title, body, created_at FROM note_revisions WHERE note_id = ? ORDER BY revision DESC",
            (nid,),
        ).fetchall()
        return [row_dict(r) for r in rows]

    def _upload(self, conn, project, headers, body):
        ctype = headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return 400, {"error": "multipart upload required"}, "json"
        if len(body) > MAX_UPLOAD + 64 * 1024:
            return 413, {"error": "upload too large"}, "json"
        try:
            filename, content, file_type = parse_multipart(ctype, body)
        except ValueError as exc:
            return 400, {"error": str(exc)}, "json"
        if len(content) > MAX_UPLOAD:
            return 413, {"error": "upload too large"}, "json"
        if not content:
            return 400, {"error": "empty file"}, "json"
        safe = safe_filename(filename)
        doc_id = new_id()
        stored = f"{doc_id}_{safe}"
        if ".." in stored or "/" in stored or "\\" in stored:
            return 400, {"error": "invalid filename"}, "json"
        dest = (UPLOADS / stored).resolve()
        if UPLOADS.resolve() not in dest.parents:
            return 400, {"error": "invalid path"}, "json"
        digest = hashlib.sha256(content).hexdigest()
        dest.write_bytes(content)
        conn.execute(
            """INSERT INTO documents (id, project_id, filename, stored_name, content_type, size, sha256, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (doc_id, project["id"], safe, stored, file_type or "application/octet-stream", len(content), digest, now_iso()),
        )
        conn.commit()
        return 303, f"/projects/{project['id']}#documents", "redirect"

    def _download(self, conn, project, doc_id):
        if not TOKEN_RE.fullmatch(doc_id):
            return 400, {"error": "invalid id"}, "json"
        doc = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND project_id = ?", (doc_id, project["id"])
        ).fetchone()
        if not doc:
            return 404, {"error": "document not found"}, "json"
        stored = doc["stored_name"]
        if not re.fullmatch(r"[A-Za-z0-9._-]{8,160}", stored) or ".." in stored:
            return 400, {"error": "invalid stored name"}, "json"
        path = (UPLOADS / stored).resolve()
        if UPLOADS.resolve() not in path.parents or not path.is_file():
            return 404, {"error": "file missing"}, "json"
        data = path.read_bytes()
        return 200, (doc["filename"], doc["content_type"], data), "file"

    def _export(self, conn, project):
        return {
            "project": row_dict(project),
            "tasks": [row_dict(r) for r in conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY status, position", (project["id"],))],
            "notes": [row_dict(r) for r in conn.execute("SELECT * FROM notes WHERE project_id = ? ORDER BY updated_at DESC", (project["id"],))],
            "note_revisions": [
                row_dict(r)
                for r in conn.execute(
                    """SELECT r.* FROM note_revisions r JOIN notes n ON n.id = r.note_id
                       WHERE n.project_id = ? ORDER BY r.note_id, r.revision""",
                    (project["id"],),
                )
            ],
            "documents": [
                {k: row[k] for k in ("id", "filename", "content_type", "size", "sha256", "created_at")}
                for row in conn.execute("SELECT * FROM documents WHERE project_id = ?", (project["id"],))
            ],
        }

    def _export_md(self, conn, project):
        lines = [f"# {project['name']}", "", project["description"] or "_No description._", "", f"Status: {'Archived' if project['archived'] else 'Active'}", ""]
        lines.append("## Tasks")
        lines.append("")
        tasks = conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY status, position", (project["id"],)).fetchall()
        if not tasks:
            lines.append("_No tasks._")
        for status in STATUSES:
            group = [t for t in tasks if t["status"] == status]
            if not group:
                continue
            lines.append(f"### {STATUS_LABEL[status]}")
            lines.append("")
            for t in group:
                due = f" due {t['due_date']}" if t["due_date"] else ""
                lines.append(f"- **{t['title']}** ({t['priority']}{due})")
                if t["details"]:
                    lines.append(f"  - {t['details']}")
            lines.append("")
        lines.append("## Notes")
        lines.append("")
        notes = conn.execute("SELECT * FROM notes WHERE project_id = ? ORDER BY updated_at DESC", (project["id"],)).fetchall()
        if not notes:
            lines.append("_No notes._")
        for n in notes:
            lines.append(f"### {n['title']} (revision {n['revision']})")
            lines.append("")
            lines.append(n["body"])
            lines.append("")
        lines.append("## Documents")
        lines.append("")
        docs = conn.execute("SELECT filename, size, sha256 FROM documents WHERE project_id = ?", (project["id"],)).fetchall()
        if not docs:
            lines.append("_No documents._")
        for d in docs:
            lines.append(f"- {d['filename']} ({d['size']} bytes, sha256 {d['sha256']})")
        lines.append("")
        return "\n".join(lines)

    def webhook_create(self, headers, body):
        auth = headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:].strip(), self.secret):
            return 401, {"error": "unauthorized"}, "json"
        if len(body) > MAX_JSON:
            return 413, {"error": "body too large"}, "json"
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return 400, {"error": "invalid JSON"}, "json"
        if not isinstance(data, dict):
            return 400, {"error": "JSON object required"}, "json"
        project_id = str(data.get("project_id", "")).strip()
        with _db_lock:
            conn = connect()
            try:
                project = project_or_404(conn, project_id)
                if not project:
                    return 404, {"error": "project not found"}, "json"
                if project["archived"]:
                    return 409, {"error": "project is archived"}, "json"
                return self._insert_task(conn, project_id, data, redirect=False)
            finally:
                conn.close()

    def search(self, q: str):
        q = q.strip()
        if not q:
            return {"query": "", "results": []}
        like = f"%{q}%"
        with _db_lock:
            conn = connect()
            try:
                results = []
                for row in conn.execute(
                    "SELECT id, name, description FROM projects WHERE name LIKE ? OR description LIKE ? ORDER BY updated_at DESC LIMIT 20",
                    (like, like),
                ):
                    results.append({"type": "project", "id": row["id"], "title": row["name"], "snippet": row["description"][:180], "href": f"/projects/{row['id']}"})
                for row in conn.execute(
                    """SELECT t.id, t.title, t.details, t.project_id, p.name AS project
                       FROM tasks t JOIN projects p ON p.id = t.project_id
                       WHERE t.title LIKE ? OR t.details LIKE ? OR p.name LIKE ? OR p.description LIKE ? LIMIT 30""",
                    (like, like, like, like),
                ):
                    results.append({"type": "task", "id": row["id"], "title": row["title"], "snippet": row["details"][:180], "project": row["project"], "href": f"/projects/{row['project_id']}"})
                for row in conn.execute(
                    """SELECT n.id, n.title, n.body, n.project_id, p.name AS project
                       FROM notes n JOIN projects p ON p.id = n.project_id
                       WHERE n.title LIKE ? OR n.body LIKE ? OR p.name LIKE ? OR p.description LIKE ? LIMIT 30""",
                    (like, like, like, like),
                ):
                    results.append({"type": "note", "id": row["id"], "title": row["title"], "snippet": row["body"][:180], "project": row["project"], "href": f"/projects/{row['project_id']}#notes"})
                for row in conn.execute(
                    """SELECT d.id, d.filename, d.project_id, p.name AS project
                       FROM documents d JOIN projects p ON p.id = d.project_id
                       WHERE d.filename LIKE ? OR p.name LIKE ? OR p.description LIKE ? LIMIT 30""",
                    (like, like, like),
                ):
                    results.append({"type": "document", "id": row["id"], "title": row["filename"], "snippet": row["filename"], "project": row["project"], "href": f"/projects/{row['project_id']}/documents/{row['id']}"})
                return {"query": q, "results": results}
            finally:
                conn.close()

    def render_home(self, q: str = "") -> str:
        q = (q or "").strip()
        found = self.search(q)["results"] if q else []
        with _db_lock:
            conn = connect()
            active = conn.execute("SELECT * FROM projects WHERE archived = 0 ORDER BY updated_at DESC").fetchall()
            archived = conn.execute("SELECT * FROM projects WHERE archived = 1 ORDER BY updated_at DESC").fetchall()
            conn.close()
        def cards(rows, empty):
            if not rows:
                return f'<p class="empty">{html.escape(empty)}</p>'
            bits = []
            for p in rows:
                bits.append(
                    f'<a class="card" href="/projects/{html.escape(p["id"])}">'
                    f'<strong>{html.escape(p["name"])}</strong>'
                    f'<span>{html.escape((p["description"] or "No description")[:140])}</span></a>'
                )
            return "".join(bits)
        body = f"""
        <header class="top">
          <div>
            <p class="kicker">Personal</p>
            <h1>Project Hub</h1>
          </div>
          <form class="search" action="/" method="get" role="search">
            <label class="sr" for="q">Search</label>
            <input id="q" name="q" type="search" value="{html.escape(q, quote=True)}" placeholder="Search projects, tasks, notes" autocomplete="off">
          </form>
        </header>
        {self._search_hits(found) if q else ""}
        <section class="panel">
          <div class="panel-head"><h2>Active</h2></div>
          <div class="cards">{cards(active, "No active projects yet. Create one below.")}</div>
        </section>
        <section class="panel">
          <h2>New project</h2>
          <form class="stack" method="post" action="/projects" data-state="1">
            <label>Name<input name="name" required maxlength="120" autofocus></label>
            <label>Description<textarea name="description" maxlength="4000" rows="3"></textarea></label>
            <button type="submit">Create project</button>
          </form>
        </section>
        <section class="panel">
          <h2>Archived</h2>
          <div class="cards">{cards(archived, "Nothing archived.")}</div>
        </section>
        <div id="search-results" class="search-results" hidden></div>
        """
        return page("Project Hub", body)

    def _search_hits(self, found) -> str:
        if not found:
            return '<section class="panel"><h2>Search</h2><p class="empty">No matches.</p></section>'
        items = []
        for row in found:
            title = html.escape(row.get("title") or "")
            kind = html.escape(row.get("type") or "")
            snippet = html.escape((row.get("snippet") or "")[:180])
            href = html.escape(row.get("href") or "/", quote=True)
            project = html.escape(row.get("project") or "")
            extra = f" · {project}" if project else ""
            items.append(f'<a href="{href}"><strong>{title}</strong> <span class="meta">{kind}{extra}</span><br>{snippet}</a>')
        return '<section class="panel" id="search-hits"><h2>Search</h2>' + "".join(items) + "</section>"

    def render_project(self, pid: str, query: dict) -> str:
        with _db_lock:
            conn = connect()
            project = project_or_404(conn, pid)
            if not project:
                conn.close()
                return page("Not found", '<p class="empty">That project does not exist. <a href="/">Back home</a></p>', status_note="Missing")
            tasks = conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY position, created_at", (pid,)).fetchall()
            notes = conn.execute("SELECT * FROM notes WHERE project_id = ? ORDER BY updated_at DESC", (pid,)).fetchall()
            docs = conn.execute("SELECT * FROM documents WHERE project_id = ? ORDER BY created_at DESC", (pid,)).fetchall()
            conn.close()
        board = []
        for status in STATUSES:
            cards = []
            group = [t for t in tasks if t["status"] == status]
            if not group:
                cards.append('<p class="empty">Nothing here.</p>')
            for t in group:
                due = html.escape(t["due_date"] or "No due date")
                cards.append(
                    f'<article class="task" id="task-{html.escape(t["id"])}">'
                    f'<header><strong>{html.escape(t["title"])}</strong>'
                    f'<span class="pri pri-{html.escape(t["priority"])}">{html.escape(t["priority"])}</span></header>'
                    f'<p>{html.escape(t["details"] or "")}</p>'
                    f'<p class="meta">Due {due}</p>'
                    f'<form method="post" action="/projects/{html.escape(pid)}/tasks/{html.escape(t["id"])}/update" data-state="1" class="inline">'
                    f'<input type="hidden" name="title" value="{html.escape(t["title"], quote=True)}">'
                    f'<input type="hidden" name="details" value="{html.escape(t["details"] or "", quote=True)}">'
                    f'<input type="hidden" name="priority" value="{html.escape(t["priority"])}">'
                    f'<input type="hidden" name="due_date" value="{html.escape(t["due_date"] or "")}">'
                    f'<label class="sr">Move<select name="status">'
                    + "".join(f'<option value="{s}"{" selected" if s == t["status"] else ""}>{STATUS_LABEL[s]}</option>' for s in STATUSES)
                    + f'</select></label><button type="submit">Move</button></form>'
                    f'<details><summary>Edit</summary>'
                    f'<form method="post" action="/projects/{html.escape(pid)}/tasks/{html.escape(t["id"])}/update" data-state="1" class="stack">'
                    f'<label>Title<input name="title" required maxlength="200" value="{html.escape(t["title"], quote=True)}"></label>'
                    f'<label>Details<textarea name="details" maxlength="4000" rows="3">{html.escape(t["details"] or "")}</textarea></label>'
                    f'<label>Priority<select name="priority">'
                    + "".join(f'<option value="{p}"{" selected" if p == t["priority"] else ""}>{p}</option>' for p in PRIORITIES)
                    + '</select></label>'
                    f'<label>Due<input type="date" name="due_date" value="{html.escape(t["due_date"] or "")}"></label>'
                    f'<input type="hidden" name="status" value="{html.escape(t["status"])}">'
                    f'<button type="submit">Save task</button></form>'
                    f'<form method="post" action="/projects/{html.escape(pid)}/tasks/{html.escape(t["id"])}/delete" data-state="1">'
                    f'<button type="submit" class="danger">Delete task</button></form></details></article>'
                )
            board.append(f'<section class="column" aria-label="{STATUS_LABEL[status]}"><h3>{STATUS_LABEL[status]}</h3>{"".join(cards)}</section>')
        note_html = []
        if not notes:
            note_html.append('<p class="empty">No notes yet.</p>')
        for n in notes:
            note_html.append(
                f'<article class="note" id="note-{html.escape(n["id"])}">'
                f'<header><strong>{html.escape(n["title"])}</strong><span class="meta">rev {n["revision"]}</span></header>'
                f'<pre class="md">{html.escape(n["body"])}</pre>'
                f'<form method="post" action="/projects/{html.escape(pid)}/notes/{html.escape(n["id"])}/update" data-state="1" class="stack">'
                f'<label>Title<input name="title" required maxlength="160" value="{html.escape(n["title"], quote=True)}"></label>'
                f'<label>Markdown<textarea name="body" maxlength="20000" rows="8">{html.escape(n["body"])}</textarea></label>'
                f'<button type="submit">Save revision</button></form>'
                f'<button type="button" class="ghost" data-revisions="/projects/{html.escape(pid)}/notes/{html.escape(n["id"])}/revisions">Revision history</button>'
                f'<div class="revisions" hidden></div></article>'
            )
        doc_html = []
        if not docs:
            doc_html.append('<p class="empty">No documents uploaded.</p>')
        for d in docs:
            doc_html.append(
                f'<li><a href="/projects/{html.escape(pid)}/documents/{html.escape(d["id"])}">{html.escape(d["filename"])}</a>'
                f' <span class="meta">{d["size"]} bytes</span></li>'
            )
        archived = bool(project["archived"])
        action = "reopen" if archived else "archive"
        label = "Reopen" if archived else "Archive"
        banner = '<p class="banner">This project is archived. Reopen it to keep working.</p>' if archived else ""
        body = f"""
        <header class="top">
          <div>
            <p class="kicker"><a href="/">Project Hub</a></p>
            <h1>{html.escape(project["name"])}</h1>
            <p class="lede">{html.escape(project["description"] or "")}</p>
          </div>
          <div class="actions">
            <a class="button" href="/projects/{html.escape(pid)}/export.md">Export Markdown</a>
            <a class="button" href="/projects/{html.escape(pid)}/export.json">Export JSON</a>
            <form method="post" action="/projects/{html.escape(pid)}/{action}" data-state="1"><button type="submit">{label}</button></form>
          </div>
        </header>
        {banner}
        <section class="panel">
          <h2>Board</h2>
          <div class="board">{"".join(board)}</div>
          <form class="stack add" method="post" action="/projects/{html.escape(pid)}/tasks" data-state="1">
            <h3>Add task</h3>
            <label>Title<input name="title" required maxlength="200"></label>
            <label>Details<textarea name="details" maxlength="4000" rows="2"></textarea></label>
            <div class="row">
              <label>Status<select name="status">{"".join(f'<option value="{s}">{STATUS_LABEL[s]}</option>' for s in STATUSES)}</select></label>
              <label>Priority<select name="priority">{"".join(f'<option value="{p}"{" selected" if p=="medium" else ""}>{p}</option>' for p in PRIORITIES)}</select></label>
              <label>Due<input type="date" name="due_date"></label>
            </div>
            <button type="submit">Add task</button>
          </form>
        </section>
        <section class="panel" id="notes">
          <h2>Notes</h2>
          {"".join(note_html)}
          <form class="stack" method="post" action="/projects/{html.escape(pid)}/notes" data-state="1">
            <h3>New note</h3>
            <label>Title<input name="title" required maxlength="160"></label>
            <label>Markdown<textarea name="body" maxlength="20000" rows="6" placeholder="# Heading"></textarea></label>
            <button type="submit">Save note</button>
          </form>
        </section>
        <section class="panel" id="documents">
          <h2>Documents</h2>
          <ul class="docs">{"".join(doc_html)}</ul>
          <form class="stack" method="post" action="/projects/{html.escape(pid)}/documents" enctype="multipart/form-data" data-state="1">
            <label>Upload<input type="file" name="file" required></label>
            <button type="submit">Upload</button>
          </form>
        </section>
        """
        return page(project["name"], body)


def parse_multipart(content_type: str, body: bytes):
    match = re.search(r'boundary=([^;]+)', content_type)
    if not match:
        raise ValueError("missing boundary")
    boundary = match.group(1).strip().strip('"')
    if len(boundary) > 120:
        raise ValueError("bad boundary")
    marker = ("--" + boundary).encode()
    chunks = body.split(marker)
    for chunk in chunks:
        if not chunk or chunk in (b"--", b"--\r\n") or chunk.startswith(b"--"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        elif chunk.startswith(b"\n"):
            chunk = chunk[1:]
        if b"\r\n\r\n" not in chunk:
            continue
        header_blob, content = chunk.split(b"\r\n\r\n", 1)
        # Only the CRLF that introduces the next boundary is not file content.
        if content.endswith(b"\r\n"):
            content = content[:-2]
        headers = header_blob.decode("utf-8", errors="replace")
        if 'name="file"' not in headers and "name='file'" not in headers and "filename=" not in headers:
            continue
        fname = "upload.bin"
        fm = re.search(r'filename="([^"]*)"', headers) or re.search(r"filename=([^;\r\n]+)", headers)
        if fm:
            fname = fm.group(1).strip()
        tm = re.search(r"Content-Type:\s*([^\r\n]+)", headers, re.I)
        file_type = tm.group(1).strip() if tm else mimetypes.guess_type(fname)[0] or "application/octet-stream"
        return fname, content, file_type
    raise ValueError("file field missing")


CSS = """
:root { color-scheme: light dark; --bg:#f4f1ea; --ink:#1c1915; --muted:#6d645b; --card:#fffdf8; --line:#e4dccf; --accent:#0f6e56; --accent-ink:#fff; --danger:#9b2335; --shadow:0 10px 30px rgba(40,30,10,.06); }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161411; --ink:#f3efe6; --muted:#b3a89b; --card:#221e19; --line:#3a332b; --accent:#3cba9a; --accent-ink:#10211c; --danger:#ff8b98; --shadow:none; }
}
* { box-sizing: border-box; }
body { margin:0; font:16px/1.5 "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif; background:var(--bg); color:var(--ink); }
a { color: var(--accent); }
.wrap { max-width: 1100px; margin: 0 auto; padding: 1.25rem; }
.top { display:flex; justify-content:space-between; gap:1rem; align-items:flex-end; flex-wrap:wrap; margin-bottom:1rem; }
h1 { font-size: 2rem; line-height:1.1; margin:.1rem 0; letter-spacing:-.02em; }
h2 { margin:0 0 .75rem; font-size:1.15rem; }
h3 { margin:.2rem 0 .4rem; font-size:1rem; }
.kicker { margin:0; text-transform:uppercase; letter-spacing:.14em; font:600 .72rem/1.2 ui-sans-serif, system-ui, sans-serif; color:var(--muted); }
.lede { margin:.3rem 0 0; max-width: 46rem; color:var(--muted); }
.panel { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:1rem; margin:0 0 1rem; box-shadow:var(--shadow); }
.cards { display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:.75rem; }
.card { display:flex; flex-direction:column; gap:.35rem; text-decoration:none; color:inherit; border:1px solid var(--line); border-radius:12px; padding:.85rem; background:transparent; }
.card:focus, button:focus, input:focus, textarea:focus, select:focus, a.button:focus { outline: 2px solid var(--accent); outline-offset: 2px; }
.card span { color:var(--muted); font-size:.92rem; }
.board { display:grid; grid-template-columns: repeat(3, 1fr); gap:.75rem; }
.column { background: color-mix(in srgb, var(--bg) 70%, var(--card)); border-radius:12px; padding:.6rem; min-height:8rem; }
.task, .note { border:1px solid var(--line); border-radius:12px; padding:.7rem; margin:0 0 .6rem; background:var(--card); }
.task header, .note header, .actions, .row, .inline { display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; }
.task p { margin:.35rem 0; white-space:pre-wrap; }
.pri { font:600 .72rem/1 ui-sans-serif, system-ui, sans-serif; text-transform:uppercase; letter-spacing:.04em; border-radius:999px; padding:.2rem .45rem; }
.pri-low { background:#e7eef2; color:#245; } .pri-medium { background:#efe7d2; color:#543; } .pri-high { background:#f6dfd2; color:#732; } .pri-urgent { background:#f6d4d8; color:#722; }
@media (prefers-color-scheme: dark) {
  .pri-low,.pri-medium,.pri-high,.pri-urgent { background:#2c2823; color:var(--ink); }
}
.meta { color:var(--muted); font-size:.85rem; }
.empty { color:var(--muted); margin:.2rem 0 .6rem; }
.banner { background:#f6dfd2; color:#532; padding:.7rem 1rem; border-radius:12px; }
label { display:flex; flex-direction:column; gap:.25rem; font:600 .85rem/1.2 ui-sans-serif, system-ui, sans-serif; }
input, textarea, select { font:1rem/1.4 Georgia, serif; color:inherit; background:var(--bg); border:1px solid var(--line); border-radius:10px; padding:.45rem .6rem; width:100%; }
button, a.button { font:600 .92rem/1 ui-sans-serif, system-ui, sans-serif; background:var(--accent); color:var(--accent-ink); border:0; border-radius:999px; padding:.5rem .85rem; text-decoration:none; cursor:pointer; }
button.danger { background:transparent; color:var(--danger); border:1px solid var(--danger); }
button.ghost, a.button { background:transparent; color:var(--ink); border:1px solid var(--line); }
.stack { display:flex; flex-direction:column; gap:.55rem; margin-top:.8rem; }
.row > label { flex:1; min-width:8rem; }
.search input { min-width: 16rem; }
.search-results { position:sticky; bottom:1rem; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:.8rem; }
.search-results a { display:block; padding:.25rem 0; }
.md { white-space:pre-wrap; font: .95rem/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background:var(--bg); padding:.7rem; border-radius:10px; overflow:auto; }
.sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
.docs { padding-left:1.1rem; }
@media (max-width: 800px) {
  .board { grid-template-columns: 1fr; }
  .wrap { padding: .8rem; }
  h1 { font-size: 1.6rem; }
}
"""

JS = """
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && document.activeElement && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
    const box = document.querySelector('input[type="search"]');
    if (box) { event.preventDefault(); box.focus(); }
  }
});
document.querySelectorAll("form[data-state]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const xhrish = new FormData(form);
    void xhrish;
    const headersOk = true;
    if (!headersOk) event.preventDefault();
  });
});
const origFetch = window.fetch;
window.fetch = (url, opts={}) => origFetch(url, opts);
document.querySelectorAll("form[data-state]").forEach((form) => {
  form.addEventListener("submit", () => {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "_ts";
    input.value = "1";
    form.appendChild(input);
  });
});
const q = document.getElementById("q");
const out = document.getElementById("search-results");
if (q && out) {
  let timer;
  q.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const term = q.value.trim();
      if (!term) { out.hidden = true; out.innerHTML = ""; return; }
      const res = await fetch("/search?q=" + encodeURIComponent(term), {headers: {"X-Requested-With": "ProjectHub"}});
      const data = await res.json();
      if (!data.results.length) { out.hidden = false; out.textContent = "No matches."; return; }
      out.hidden = false;
      out.innerHTML = data.results.map((item) => `<a href="${item.href}"><strong>${escapeHtml(item.type)}</strong> ${escapeHtml(item.title)}</a>`).join("");
    }, 180);
  });
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
document.querySelectorAll("[data-revisions]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const box = btn.nextElementSibling;
    if (!box.hidden) { box.hidden = true; return; }
    const res = await fetch(btn.dataset.revisions, {headers: {"X-Requested-With": "ProjectHub"}});
    const rows = await res.json();
    box.hidden = false;
    box.innerHTML = rows.map((r) => `<details><summary>Revision ${r.revision} · ${escapeHtml(r.created_at)}</summary><pre class="md">${escapeHtml(r.body)}</pre></details>`).join("");
  });
});
"""


def page(title: str, body: str, status_note: str = "") -> str:
    note = f"<title>{html.escape(title)} · Project Hub</title>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{note}
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<main class="wrap">
{body}
<p class="meta">{html.escape(status_note)}</p>
</main>
<script src="/static/app.js"></script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ProjectHub"
    sys_version = ""

    def log_message(self, fmt, *args):
        line = "%s - %s" % (self.address_string(), fmt % args)
        if "Bearer" in line or "webhook.token" in line:
            return
        print(line, flush=True)

    def _require_host(self) -> str | None:
        raw = (self.headers.get("Host") or "").strip()
        if not raw or "/" in raw or "\\" in raw or "@" in raw:
            return "unexpected host"
        host = raw
        port = None
        if raw.startswith("["):
            end = raw.find("]")
            if end < 0:
                return "unexpected host"
            host = raw[1:end]
            rest = raw[end + 1:]
            if rest.startswith(":"):
                port = rest[1:]
            elif rest:
                return "unexpected host"
        elif raw.count(":") == 1:
            host, port = raw.split(":", 1)
        elif raw.count(":") > 1:
            return "unexpected host"
        host = host.lower()
        if host not in {"localhost", "127.0.0.1"}:
            return "unexpected host"
        allowed = {str(PORT)}
        addr = getattr(self.server, "server_address", None)
        if addr and len(addr) >= 2 and addr[1]:
            allowed.add(str(int(addr[1])))
        if port is None or port not in allowed:
            return "unexpected host"
        return None

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length < 0 or length > MAX_BODY:
            raise ValueError("body too large")
        return self.rfile.read(length) if length else b""

    def _dispatch(self, method: str):
        try:
            body = self._read_body() if method in {"POST", "PUT", "PATCH", "DELETE"} else b""
        except ValueError:
            self._send(413, {"error": "body too large"}, "json")
            return
        path = urlparse(self.path).path
        host_err = self._require_host()
        if host_err and path != "/webhook/tasks":
            self._send(403, {"error": host_err}, "json")
            return
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            if path != "/webhook/tasks":
                origin_err = self.server.app._require_origin(self.headers)
                if origin_err:
                    self._send(403, {"error": origin_err}, "json")
                    return
        try:
            status, payload, kind = self.server.app.handle(method, self.path, parse_qs(urlparse(self.path).query), self.headers, body)
        except Exception as exc:
            self._send(500, {"error": "internal error", "detail": type(exc).__name__}, "json")
            return
        self._send(status, payload, kind)

    def _send(self, status, payload, kind):
        if kind == "redirect":
            data = b""
            headers = {"Location": payload, "Content-Length": "0"}
            ctype = "text/plain"
        elif kind == "html":
            data = payload.encode("utf-8")
            ctype = "text/html; charset=utf-8"
            headers = {"Content-Length": str(len(data))}
        elif kind == "css":
            data = payload.encode("utf-8")
            ctype = "text/css; charset=utf-8"
            headers = {"Content-Length": str(len(data))}
        elif kind == "js":
            data = payload.encode("utf-8")
            ctype = "text/javascript; charset=utf-8"
            headers = {"Content-Length": str(len(data))}
        elif kind == "json-download":
            data = json.dumps(payload, indent=2).encode("utf-8")
            ctype = "application/json"
            headers = {
                "Content-Length": str(len(data)),
                "Content-Disposition": 'attachment; filename="project.json"',
            }
        elif kind == "md-download":
            data = payload.encode("utf-8")
            ctype = "text/markdown; charset=utf-8"
            headers = {
                "Content-Length": str(len(data)),
                "Content-Disposition": 'attachment; filename="project.md"',
            }
        elif kind == "file":
            filename, file_type, data = payload
            ctype = file_type or "application/octet-stream"
            headers = {
                "Content-Length": str(len(data)),
                "Content-Disposition": f'attachment; filename="{safe_filename(filename)}"',
                "X-Content-Type-Options": "nosniff",
            }
        else:
            data = json.dumps(payload).encode("utf-8")
            ctype = "application/json"
            headers = {"Content-Length": str(len(data))}
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if kind == "html":
            self.send_header("Set-Cookie", self.server.app._session_cookie_header())
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


class HubServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, secret):
        init_db()
        self.app = App(secret)
        super().__init__(addr, Handler)
        self.app._bound_server = self


def main() -> None:
    init_db()
    secret = load_secret()
    with _db_lock:
        conn = connect()
        seed_demo(conn)
        conn.close()
    server = HubServer((HOST, PORT), secret)
    print(f"project-hub listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
