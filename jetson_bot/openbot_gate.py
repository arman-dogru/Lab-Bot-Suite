"""Decisions for the OpenBot control plane. No model call and no agent loop."""
import importlib.util
import ipaddress
import json
import re
from pathlib import Path

WORKSPACE = "/srv/jetson-bot/workspace"
GATEWAY = "http://127.0.0.1:8870/v1"
COMPUTER = "127.0.0.1"
COMPUTER_PORT = 8871
LAN_HOSTS = ("192.168.55.1",)
LAB_WIFI_PREFIX = "10.206."
POSTGRES_BIND = "127.0.0.1"
MIN_FREE_MB = 700
TAKEOVER_WAIT_S = 30 * 60
SHELL_ALLOW = ("ls", "git", "python", "python3", "pytest")
HOLD_TOOLS = {"git_push", "install_tool", "send", "deploy", "write_file", "github_write"}
DENY_TOOLS = {"purchase", "checkout", "imagine"}


def _policy():
    path = Path(__file__).resolve().parents[1] / "deploy" / "control_plugin" / "policy.py"
    spec = importlib.util.spec_from_file_location("jetson_owner_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stricter(left, right):
    rank = {"allow": 0, "hold": 1, "prompt": 1, "deny": 2}
    return left if rank[left] >= rank[right] else right


def gateway_profile():
    """The only model hop OpenBot is allowed to use."""
    return {
        "base_url": GATEWAY,
        "model": "grok-4.7",
        "stream": False,
        "max_images": 4,
        "function_tools_only": True,
        "daily_cap_ticks": 200000000000,
        "timezone": "America/Toronto",
        "input_ticks_per_token": 20000,
        "output_ticks_per_token": 60000,
        "max_output_tokens": 8192,
        "max_input_tokens": 191808,
    }


def openbot_env(bind_host):
    """Environment for the OpenBot server. Never contains the raw xAI key."""
    if not chat_bind_allowed(bind_host, sign_in=bind_host not in ("127.0.0.1", "localhost")):
        raise ValueError("Chat bind refused")
    env = {"OPENAI_BASE_URL": GATEWAY, "OPENAI_API_KEY": "local-tunnel-token"}
    if bind_host in ("127.0.0.1", "localhost"):
        env["OPENBOT_SINGLE_USER"] = "true"
    else:
        env["OPENBOT_SINGLE_USER"] = "false"
        env["OPENBOT_SIGN_IN"] = "required"
    if env["OPENAI_API_KEY"].startswith("sk-") or "xai-" in env["OPENAI_API_KEY"]:
        raise ValueError("Raw model key")
    return env


def chat_bind_allowed(host, sign_in):
    """The OpenBot UI may listen on the Jetson LAN address when sign-in is on."""
    if host in ("0.0.0.0", "::", ""):
        return False
    if host in ("127.0.0.1", "localhost"):
        return True
    if host in LAN_HOSTS or host.startswith(LAB_WIFI_PREFIX):
        return bool(sign_in)
    return bool(sign_in)


def client_may_open(client, bind_host):
    if bind_host in ("127.0.0.1", "localhost"):
        return client in ("127.0.0.1", "::1", "localhost")
    network = ipaddress.ip_network(bind_host + "/24", strict=False)
    try:
        return ipaddress.ip_address(client) in network
    except ValueError:
        return False


def _folded(text):
    folded = str(text).replace("\\", "/")
    while "//" in folded:
        folded = folded.replace("//", "/")
    return folded


def _normalize(text):
    """Fold slashes, drop '.', and apply '..'. A leading '//' is just '/'."""
    folded = _folded(text)
    absolute = folded.startswith("/")
    drive = len(folded) > 1 and folded[1] == ":"
    parts = []
    climbed = False
    for part in folded.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                climbed = True
            continue
        parts.append(part)
    if climbed and not absolute and not drive:
        return None
    if absolute:
        return "/" + "/".join(parts)
    return "/".join(parts)


def _escapes(path):
    """True when a path is outside the workspace. Relative names with no '..' stay inside."""
    if not path:
        return False
    folded = _folded(path)
    absolute = folded.startswith("/") or (len(folded) > 1 and folded[1] == ":")
    climbed = ".." in [part for part in folded.split("/") if part]
    normalized = _normalize(path)
    if normalized is None or (climbed and not absolute and normalized != WORKSPACE and not str(normalized).startswith(WORKSPACE + "/")):
        if climbed or absolute:
            return True
    if not absolute and not climbed:
        return False
    if normalized is None:
        return True
    return not (normalized == WORKSPACE or normalized.startswith(WORKSPACE + "/"))


def review(tool, args, mode, persona="", openbot="allow", interactive=True):
    """Nano policy and the OpenBot decision. The stricter one wins. Persona is ignored."""
    policy = _policy()
    nano = policy.classify(policy.subject(tool, args or {}), mode, interactive)
    if tool in DENY_TOOLS or policy.denied(json.dumps(args or {})):
        nano = "deny"
    path = (args or {}).get("path") or (args or {}).get("url") or ""
    if _escapes(path) and tool in {"read_file", "write_file", "read_doc", "list_directory"}:
        nano = "deny"
    if tool in HOLD_TOOLS or tool == "git_push":
        if nano != "deny":
            nano = "allow" if mode == "all" else ("hold" if interactive else "deny")
    if tool in {"read_file", "list_directory", "read_doc", "fetch_url", "search_files"} and nano != "deny":
        if tool == "fetch_url" or not _escapes(path):
            nano = "allow"
    return stricter(nano, openbot if openbot in ("allow", "hold", "prompt", "deny") else "deny")


def _argument_escapes(text):
    """Any absolute or '..' segment in the argument, after slash folding."""
    folded = _folded(text)
    pattern = re.compile(r"(?:\.\./)+[^\s\"')]*|/[A-Za-z.][^\s\"')]*|[A-Za-z]:/[^\s\"')]*")
    for match in pattern.finditer(folded):
        if _escapes(match.group(0).rstrip("),;")):
            return True
    return False


def shell_review(argv, mode, interactive=True):
    if not argv or argv[0] in {"sudo", "su"} or "sudo" in argv:
        return "deny"
    policy = _policy()
    if policy.denied(" ".join(str(part) for part in argv)):
        return "deny"
    for arg in argv[1:]:
        if _escapes(str(arg)) or _argument_escapes(str(arg)):
            return "deny"
    base = argv[0]
    if base not in SHELL_ALLOW:
        return "deny"
    held = base == "git" and "push" in argv
    if held:
        if mode == "all":
            return "allow"
        return "hold" if interactive else "deny"
    return "allow"


def initiator(source):
    if source in {"cron", "routine", "schedule"}:
        return "routine"
    if source == "handoff":
        return "handoff"
    if source == "person":
        return "person"
    raise ValueError("Unknown initiator")


def redact(args):
    hidden = {}
    for key, value in (args or {}).items():
        if key.lower() in {"password", "token", "secret", "api_key", "authorization"}:
            hidden[key] = "[redacted]"
        elif isinstance(value, str) and value.startswith("http"):
            hidden[key] = value.split("?", 1)[0]
        else:
            hidden[key] = value
    return hidden


def help_text(page_url, channel_url):
    """No Discord body. The channel page is the only notice."""
    return ""


def new_board():
    return {"inflight": False}


def begin_run(board, initiator, parent=None):
    """Start one model run. Depth stops at 2, fan-out at 4, and only one call is in flight."""
    if initiator not in ("person", "handoff", "routine"):
        raise ValueError("Unknown initiator")
    if board["inflight"]:
        return {"ok": False, "reason": "single-flight", "discord": None}
    depth = 1
    if parent is not None:
        if parent["depth"] >= 2:
            return {"ok": False, "reason": "depth", "discord": None}
        if parent["children"] >= 4:
            return {"ok": False, "reason": "fanout", "discord": None}
        parent["children"] += 1
        depth = parent["depth"] + 1
        initiator = "handoff"
    board["inflight"] = True
    run = {"depth": depth, "children": 0, "initiator": initiator}
    return {"ok": True, "run": run, "discord": None}


def finish_run(board):
    board["inflight"] = False


def secret_audit(secret):
    return {"supplied": True, "length": len(secret or ""), "preview": ""}


def chromium_start(outbound_list, free_mb):
    if not outbound_list:
        return {"started": False, "reason": "no-outbound-list"}
    if free_mb < MIN_FREE_MB:
        return {"started": False, "reason": "memory", "continue_with": "fetch_url"}
    return {"started": True}


class Takeover(object):
    def __init__(self):
        self.status = "RUNNING"
        self.audit = []
        self.page = ""
        self.controlling = False
        self.chromium = False
        self.session = None
        self.discord = ""

    def request_help(self, page_url):
        self.status = "WAITING"
        self.page = (page_url or "").split("?", 1)[0]
        self.audit.append({"event": "computer.help_requested", "url": self.page})
        return self.status

    def reopen_after_restart(self):
        return self.page

    def take_control(self):
        self.controlling = True
        self.audit.append({"event": "computer.control_taken"})
        return "WAITING"

    def bot_action(self, decision):
        if self.status == "DENIED" or self.controlling:
            return "deny"
        return decision

    def release(self, session=None):
        self.controlling = False
        self.chromium = False
        target = session if session is not None else self.session
        if target is not None:
            target["chromium"] = False
        if self.status == "DENIED":
            return self.status
        self.status = "RUNNING"
        self.audit.append({"event": "computer.control_released"})
        return self.status

    def wait(self, elapsed_s):
        if self.status == "WAITING" and not self.controlling and elapsed_s >= TAKEOVER_WAIT_S:
            self.status = "DENIED"
            self.audit.append({"event": "computer.wait_denied"})
            self.discord = ""
            return "deny"
        return "wait"


class ApprovalBook(object):
    def __init__(self, route):
        self.route = route
        self.answered = None
        self.ignored = []

    def answer(self, choice, takeover_active):
        if takeover_active and choice == "allow":
            self.ignored.append(choice)
            return {"applied": False, "reason": "takeover"}
        if self.answered is not None:
            self.ignored.append(choice)
            return {"applied": False, "reason": "duplicate"}
        self.route(choice)
        self.answered = choice
        return {"applied": True, "choice": choice}
