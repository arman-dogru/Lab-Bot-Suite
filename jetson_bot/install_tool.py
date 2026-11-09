"""Decide whether a tool install is allowed. The decision does not download anything."""
import re

HOST_ALLOW = {"tesseract-ocr", "antiword", "catdoc"}
PIP_SPEC = re.compile(r"[A-Za-z0-9_.-]+==[A-Za-z0-9*_.!+-]+")
HOST_DENY = ("nvidia", "linux-image", "cuda", "linux-headers")


def plan_install(kind, spec):
    if kind == "pip" and PIP_SPEC.fullmatch(spec or ""):
        return "allow"
    if kind == "host" and spec in HOST_ALLOW:
        return "allow"
    if kind == "host" and spec and any(part in spec for part in HOST_DENY):
        return "deny"
    return "deny"


def main(argv):
    if len(argv) != 3:
        raise SystemExit("Usage: install_tool.py pip|host SPEC")
    decision = plan_install(argv[1], argv[2])
    print(decision)
    if decision != "allow":
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    main(sys.argv)
