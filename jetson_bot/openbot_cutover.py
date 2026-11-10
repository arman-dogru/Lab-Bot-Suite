"""Stop the Hermes Discord units. The OpenBot UI on the Jetson replaces them."""
from jetson_bot.openbot_gate import chat_bind_allowed

STOP = ("jetson-bot.service", "jetson-discord-watch.service")
KEEP = ("jetson-budget.service", "jetson-clock.service", "jetson-clock.timer")
HERMES_FILES = (
    "deploy/run-hermes.sh",
    "deploy/soul.md",
    "deploy/jetson-bot.service",
    "deploy/jetson-discord-watch.service",
)


def apply_cutover(units, files, chat_host):
    if not chat_bind_allowed(chat_host, sign_in=chat_host not in ("127.0.0.1", "localhost")):
        raise ValueError("Chat bind refused")
    state = dict(units)
    present = dict(files)
    for name in KEEP:
        if state.get(name) != "active":
            raise RuntimeError("Required service is not running: " + name)
    for path in HERMES_FILES:
        if not present.get(path):
            raise RuntimeError("Hermes file missing: " + path)
    for name in STOP:
        if name not in state:
            raise RuntimeError("Discord unit missing: " + name)
        state[name] = "inactive"
    return {"units": state, "files": present, "chat_host": chat_host, "discord_bridge": False}
