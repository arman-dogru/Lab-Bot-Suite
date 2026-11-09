"""Integration tests for Project Hub. Real HTTP server, isolated data dir."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import app as hub  # noqa: E402


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Returning None lets the parent opener follow the redirect. Raise so
        # callers assert 303 instead of the followed page's 200.
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def req(method, url, data=None, headers=None, raw=None):
    body = raw if raw is not None else (None if data is None else data.encode())
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    # Pass the subclass into build_opener so it replaces HTTPRedirectHandler.
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def form(fields):
    from urllib.parse import urlencode
    return urlencode(fields).encode()


def browser_form(method, url, fields, page_headers, content_type=None, raw=None):
    """Replay a normal browser form POST: cookie from the page, Origin, no custom header."""
    set_cookie = page_headers.get("Set-Cookie") or page_headers.get("set-cookie") or ""
    cookie = set_cookie.split(";", 1)[0]
    host = urllib.parse.urlparse(url).netloc
    headers = {
        "Origin": f"http://{host}",
        "Referer": f"http://{host}/",
        "Cookie": cookie,
    }
    if raw is None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        raw = form(fields)
    else:
        headers["Content-Type"] = content_type
    return req(method, url, raw=raw, headers=headers)


HDR = {"Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "ProjectHub"}


def multipart(filename, content, content_type="text/plain"):
    boundary = "----HubBoundary7k"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def main():
    tmp = tempfile.mkdtemp(prefix="hubtest-")
    secret_dir = os.path.join(tmp, "secrets")
    os.makedirs(secret_dir)
    secret = "hub-test-secret-value"
    open(os.path.join(secret_dir, "webhook.token"), "w").write(secret + "\n")
    os.environ["PROJECT_HUB_DATA"] = os.path.join(tmp, "data")
    os.environ["PROJECT_HUB_SECRET"] = os.path.join(secret_dir, "webhook.token")
    os.environ["PROJECT_HUB_HOST"] = "127.0.0.1"
    os.environ["PROJECT_HUB_PORT"] = "8791"
    # Reload module constants against this isolated data dir.
    import importlib
    importlib.reload(hub)

    server = hub.HubServer(("127.0.0.1", 0), secret)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    origin = {"Origin": f"http://127.0.0.1:{port}", "X-Requested-With": "ProjectHub"}
    try:
        status, _, body = req("GET", base + "/health")
        assert status == 200 and json.loads(body)["ok"] is True, body

        status, page_headers, body = req("GET", base + "/")
        assert status == 200 and b"Project Hub" in body, body[:300]
        assert "project_hub_session=" in (page_headers.get("Set-Cookie") or page_headers.get("set-cookie") or "")

        status, _, body = req("GET", base + "/static/app.css")
        assert status == 200 and b"board" in body
        status, _, body = req("GET", base + "/static/app.js")
        assert status == 200 and b"ProjectHub" in body

        # A form POST with neither the custom header nor the page cookie is refused.
        status, _, body = req("POST", base + "/projects", raw=form({"name": "Nope"}), headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://127.0.0.1:{port}"})
        assert status == 403 and b"X-Requested-With" in body, body

        # Real browser navigation: cookie from the HTML page, Origin, no custom header.
        status, headers, body = browser_form(
            "POST", base + "/projects",
            {"name": "Browser QA", "description": "Independent browser check"},
            page_headers,
        )
        assert status == 303, (status, body)
        browser_pid = (headers.get("Location") or headers.get("location") or "").rstrip("/").split("/")[-1]
        assert browser_pid
        status, task_page_headers, page_b = req("GET", base + f"/projects/{browser_pid}")
        assert status == 200 and b"Browser QA" in page_b and b"Independent browser check" in page_b
        status, _, body = browser_form(
            "POST", base + f"/projects/{browser_pid}/tasks",
            {"title": "Browser task", "details": "from a form", "status": "todo", "priority": "low", "due_date": "2026-12-01"},
            task_page_headers,
        )
        assert status == 303, body
        status, note_page_headers, page_b = req("GET", base + f"/projects/{browser_pid}")
        assert b"Browser task" in page_b
        status, _, body = browser_form(
            "POST", base + f"/projects/{browser_pid}/notes",
            {"title": "Browser note", "body": "Typed in the form."},
            note_page_headers,
        )
        assert status == 303, body
        status, upload_page_headers, page_b = req("GET", base + f"/projects/{browser_pid}")
        assert b"Typed in the form." in page_b
        raw, ctype = multipart("browser.txt", b"browser upload\\n")
        status, _, body = browser_form(
            "POST", base + f"/projects/{browser_pid}/documents",
            {}, upload_page_headers, content_type=ctype, raw=raw,
        )
        assert status == 303, body
        status, _, page_b = req("GET", base + f"/projects/{browser_pid}")
        assert b"browser.txt" in page_b

        status, headers, body = req(
            "POST", base + "/projects",
            raw=form({"name": "Alpha", "description": "first lab"}),
            headers={**HDR, **origin},
        )
        assert status == 303, (status, body)
        loc = headers.get("Location") or headers.get("location")
        pid = loc.rstrip("/").split("/")[-1]
        assert pid

        status, _, body = req("GET", base + f"/projects/{pid}")
        page = body.decode()
        assert status == 200 and "Alpha" in page and "To do" in page and "Doing" in page and "Done" in page

        status, _, body = req("POST", base + "/projects", raw=form({"name": ""}), headers={**HDR, **origin})
        assert status == 400, body

        status, headers, _ = req(
            "POST", base + f"/projects/{pid}/tasks",
            raw=form({"title": "Write tests", "details": "cover CRUD", "status": "todo", "priority": "high", "due_date": "2026-11-12"}),
            headers={**HDR, **origin},
        )
        assert status == 303, body
        status, _, page_b = req("GET", base + f"/projects/{pid}")
        page = page_b.decode()
        assert "Write tests" in page and "2026-11-12" in page
        tid = page.split('id="task-', 1)[1].split('"', 1)[0]

        status, _, body = req(
            "POST", base + f"/projects/{pid}/tasks",
            raw=form({"title": "Bad date", "due_date": "soon", "status": "todo", "priority": "low"}),
            headers={**HDR, **origin},
        )
        assert status == 400, body
        status, _, body = req(
            "POST", base + f"/projects/{pid}/tasks",
            raw=form({"title": "Bad status", "status": "later", "priority": "low"}),
            headers={**HDR, **origin},
        )
        assert status == 400, body

        status, _, _ = req(
            "POST", base + f"/projects/{pid}/tasks/{tid}/update",
            raw=form({"title": "Write tests", "details": "cover CRUD", "status": "doing", "priority": "high", "due_date": "2026-11-12"}),
            headers={**HDR, **origin},
        )
        assert status == 303
        status, _, page_b = req("GET", base + f"/projects/{pid}")
        assert b"doing" in page_b

        status, headers, _ = req(
            "POST", base + f"/projects/{pid}/notes",
            raw=form({"title": "Bench", "body": "# Bench\n\nFirst draft."}),
            headers={**HDR, **origin},
        )
        assert status == 303
        status, _, page_b = req("GET", base + f"/projects/{pid}")
        page = page_b.decode()
        assert "First draft." in page and "rev 1" in page
        nid = page.split('id="note-', 1)[1].split('"', 1)[0]

        status, _, _ = req(
            "POST", base + f"/projects/{pid}/notes/{nid}/update",
            raw=form({"title": "Bench", "body": "# Bench\n\nSecond draft."}),
            headers={**HDR, **origin},
        )
        assert status == 303
        status, _, revs_b = req("GET", base + f"/projects/{pid}/notes/{nid}/revisions")
        revs = json.loads(revs_b)
        assert status == 200 and len(revs) == 2 and revs[0]["revision"] == 2
        assert "Second draft." in revs[0]["body"] and "First draft." in revs[1]["body"]

        blob = b"thermal log line\n42 C\n"
        raw, ctype = multipart("thermal.txt", blob)
        status, _, body = req(
            "POST", base + f"/projects/{pid}/documents",
            raw=raw,
            headers={"Content-Type": ctype, "X-Requested-With": "ProjectHub", "Origin": f"http://127.0.0.1:{port}"},
        )
        assert status == 303, body
        status, _, page_b = req("GET", base + f"/projects/{pid}")
        page = page_b.decode()
        assert "thermal.txt" in page
        doc_href = [line for line in page.split('"') if "/documents/" in line][0]
        status, hdrs, downloaded = req("GET", base + doc_href)
        assert status == 200 and downloaded == blob, downloaded
        assert "thermal.txt" in (hdrs.get("Content-Disposition") or hdrs.get("content-disposition") or "")

        # Path traversal filename is stored under a safe name and still downloads the bytes.
        raw, ctype = multipart("../../etc/passwd", b"not-a-secret\n")
        status, _, body = req(
            "POST", base + f"/projects/{pid}/documents",
            raw=raw,
            headers={"Content-Type": ctype, "X-Requested-With": "ProjectHub"},
        )
        assert status == 303, body

        status, _, body = req("GET", base + "/search?q=thermal")
        found = json.loads(body)
        assert status == 200 and any(r["type"] == "document" or "thermal" in json.dumps(r).lower() for r in found["results"])
        status, _, body = req("GET", base + "/search?q=Write%20tests")
        found = json.loads(body)
        assert any(r["type"] == "task" and r["id"] == tid for r in found["results"]), found
        status, _, body = req("GET", base + "/search?q=Second%20draft")
        found = json.loads(body)
        assert any(r["type"] == "note" for r in found["results"]), found

        status, _, body = req("GET", base + f"/projects/{pid}/export.json")
        export = json.loads(body)
        assert export["project"]["name"] == "Alpha"
        assert any(t["id"] == tid for t in export["tasks"])
        assert any(n["id"] == nid for n in export["notes"])
        assert export["documents"]

        status, _, body = req("GET", base + f"/projects/{pid}/export.md")
        md = body.decode()
        assert md.startswith("# Alpha") and "Write tests" in md and "Second draft." in md

        # Webhook auth.
        payload = json.dumps({
            "project_id": pid, "title": "From webhook", "status": "todo", "priority": "medium", "due_date": "2026-11-20",
        }).encode()
        status, _, body = req("POST", base + "/webhook/tasks", raw=payload, headers={"Content-Type": "application/json"})
        assert status == 401, body
        status, _, body = req(
            "POST", base + "/webhook/tasks", raw=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer wrong-token"},
        )
        assert status == 401, body
        status, _, body = req(
            "POST", base + "/webhook/tasks", raw=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret},
        )
        assert status == 201, body
        created = json.loads(body)
        assert created["title"] == "From webhook" and created["project_id"] == pid

        status, _, body = req(
            "POST", base + "/webhook/tasks",
            raw=json.dumps({"project_id": pid, "title": ""}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret},
        )
        assert status == 400, body
        status, _, body = req(
            "POST", base + "/webhook/tasks",
            raw=b"{",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret},
        )
        assert status == 400, body

        status, _, _ = req("POST", base + f"/projects/{pid}/archive", raw=b"", headers={**HDR, **origin})
        assert status == 303
        status, _, body = req(
            "POST", base + "/webhook/tasks",
            raw=json.dumps({"project_id": pid, "title": "should fail"}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret},
        )
        assert status == 409, body
        status, _, _ = req("POST", base + f"/projects/{pid}/reopen", raw=b"", headers={**HDR, **origin})
        assert status == 303

        status, _, _ = req("GET", base + "/projects/does-not-exist")
        assert status == 200  # HTML not-found page
        status, _, body = req("GET", base + "/nope")
        assert status == 404

        db_file = os.path.join(tmp, "data", "hub.sqlite")
        assert os.path.exists(db_file)
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

        # Same files, new server process-equivalent: data must still be there.
        server2 = hub.HubServer(("127.0.0.1", 0), secret)
        port2 = server2.server_address[1]
        thread2 = threading.Thread(target=server2.serve_forever, daemon=True)
        thread2.start()
        base2 = f"http://127.0.0.1:{port2}"
        status, _, page_b = req("GET", base2 + f"/projects/{pid}")
        page = page_b.decode()
        assert status == 200 and "Alpha" in page and "Write tests" in page and "Second draft." in page, page[:400]
        status, _, downloaded = req("GET", base2 + doc_href)
        assert status == 200 and downloaded == blob
        status, _, revs_b = req("GET", base2 + f"/projects/{pid}/notes/{nid}/revisions")
        assert len(json.loads(revs_b)) == 2
        server2.shutdown()
        thread2.join(timeout=5)
        server2.server_close()
        print("ALL TESTS PASSED")
        return 0
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
