"""Owner review modes. Decisions are local; they never call a model.

`auto` and `manual` share one rule table. `manual` is the operator-facing name
for holding the same actions. Hermes `approvals.mode: smart` is not used.
"""
import json
import os
import re
from pathlib import Path

MODES = ("all", "auto", "manual")
READ_TOOLS = {"read_file", "list_dir", "search_files", "web_search", "web_extract"}
_DENY = re.compile(
    r"(stripe|checkout|paypal|\bpurchase\b|credit\s*card|"
    r"/etc/jetson-bot/(?:xai-key|local-token|discord\.json)|"
    r"\bDISCORD_BOT_TOKEN\b|\bxai-key\b|\bprovider-key\b)",
    re.IGNORECASE)
_CHAIN = re.compile(r"[;&|`]|\$\(")
_READ = re.compile(r"^(ls|cat|head|tail|wc|stat|file|find|rg|grep)\b")
_PYTHON = re.compile(r"^(python3?|pytest)\b")
_INSTALL = re.compile(r"\b(pip|install)\b")
_CURL_WRITE = re.compile(r"(^|\s)(-d|--data|-F|--upload-file|-X|--request)\b")


def mode_path(home=None):
    root = Path(home or os.environ.get("HERMES_HOME", "/opt/data"))
    return root / "jetson-review-mode"


def current_mode(home=None):
    try:
        value = mode_path(home).read_text().strip().lower()
    except OSError:
        return "all"
    return value if value in MODES else "all"


def set_mode(mode, home=None):
    if mode not in MODES:
        raise ValueError("Unknown review mode")
    path = mode_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(mode + "\n")
    os.chmod(str(temporary), 0o600)
    os.replace(str(temporary), str(path))
    return mode


def review_command(parts, home=None):
    if not parts:
        return "Review mode: %s\nNo model call was used." % current_mode(home)
    if len(parts) == 1 and parts[0].lower() in MODES:
        mode = set_mode(parts[0].lower(), home)
        return "Review mode: %s\nNo model call was used." % mode
    return "Use /jetson review all | auto | manual. No model call was used."


def denied(text):
    return bool(_DENY.search(text or ""))


def safe(text):
    if not text or _CHAIN.search(text) or "/etc/jetson-bot" in text:
        return False
    if _READ.match(text):
        return True
    if _PYTHON.match(text) and not _INSTALL.search(text):
        return "/workspace" in text or "/opt/data/home" in text or bool(
            re.search(r"\bunittest\b|\bpytest\b", text))
    if text.startswith("curl ") and not _CURL_WRITE.search(text):
        return True
    return False


def classify(text, mode, interactive=True):
    if mode not in MODES:
        raise ValueError("Unknown review mode")
    if denied(text):
        return "deny"
    if mode == "all" or safe(text):
        return "allow"
    if not interactive:
        return "deny"
    return "prompt"


def subject(tool_name, args):
    if isinstance(args, dict):
        command = args.get("command")
        if isinstance(command, str):
            return command
        if tool_name in READ_TOOLS:
            return "cat " + " ".join(str(value) for value in args.values())
    try:
        rendered = json.dumps(args or {}, default=str, sort_keys=True)
    except (TypeError, ValueError):
        rendered = str(args)
    return (tool_name or "") + " " + rendered


def decide_tool(tool_name, args, mode=None, interactive=True):
    """Return a Hermes pre_tool_call directive, or None to allow the tool."""
    decision = classify(subject(tool_name, args), mode or current_mode(), interactive)
    if decision == "allow":
        return None
    if decision == "deny":
        return {"action": "block",
                "message": "Jetson review denied this action. Purchases, checkout, and credential reads stay refused."}
    return {"action": "approve",
            "message": "Jetson review is holding this action for the operator."}
