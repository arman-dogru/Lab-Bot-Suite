"""Localhost computer tools. A deny returns before any read, write, or process."""
import os
from pathlib import Path

from jetson_bot.fetch_page import page_text
from jetson_bot.jetson_image import prepare
from jetson_bot.openbot_gate import (
    COMPUTER,
    COMPUTER_PORT,
    WORKSPACE,
    chromium_start,
    review,
    shell_review,
)
from jetson_bot.read_doc import read_path

LISTEN = (COMPUTER, COMPUTER_PORT)


def listen_on():
    if LISTEN[0] != "127.0.0.1":
        raise RuntimeError("Computer tools must listen on localhost")
    return LISTEN


def _inside(root, raw):
    root_path = Path(root).resolve()
    candidate = (root_path / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError:
        return None
    return candidate


def read_workspace(root, raw_path, mode, reader, persona="", openbot="allow"):
    decision = review("read_file", {"path": raw_path}, mode, persona, openbot)
    target = _inside(root, raw_path)
    if decision == "deny" or target is None:
        return {"ok": False, "ran": False, "decision": "deny"}
    return {"ok": True, "ran": True, "decision": "allow", "text": reader(target)}


def run_shell(argv, mode, runner, interactive=True, timeout_s=30, output_cap=200000):
    decision = shell_review(argv, mode, interactive)
    if decision != "allow":
        return {"ok": False, "ran": False, "decision": decision, "timeout_s": timeout_s, "output_cap": output_cap}
    output = runner(argv, timeout_s)
    text = output[:output_cap]
    return {"ok": True, "ran": True, "decision": "allow", "text": text, "truncated": len(output) > output_cap}


def read_document(root, raw_path, mode):
    decision = review("read_doc", {"path": raw_path}, mode)
    target = _inside(root, raw_path)
    if decision == "deny" or target is None:
        return {"ok": False, "ran": False, "decision": "deny"}
    title, body = read_path(target)
    return {"ok": True, "ran": True, "title": title, "text": body}


def fetch_saved(html, mode):
    decision = review("fetch_url", {"url": "https://example.test/page"}, mode)
    if decision == "deny":
        return {"ok": False, "ran": False}
    parsed = page_text(html)
    return {"ok": True, "ran": True, "title": parsed["title"], "text": parsed["text"]}


def resize_image(root, raw_path, dest, mode):
    decision = review("read_file", {"path": raw_path}, mode)
    target = _inside(root, raw_path)
    if decision == "deny" or target is None:
        return {"ok": False, "ran": False, "decision": "deny"}
    return {"ok": True, "ran": True, "result": prepare(target, dest)}


def start_browser(outbound, free_mb, session):
    gate = chromium_start(outbound, free_mb)
    if not gate["started"]:
        return gate
    session["chromium"] = True
    return gate


def finish(session):
    session["chromium"] = False
    return session
