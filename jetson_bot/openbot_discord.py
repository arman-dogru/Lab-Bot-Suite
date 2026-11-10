"""Owner-only text bridge. Buttons call the approval route. They do not run tools."""
from jetson_bot.openbot_gate import ApprovalBook, help_text, initiator, redact


def inbound(author_id, owner_id, text):
    """Discord is not a door. Nothing is forwarded."""
    return {"forwarded": False, "posted": False, "channel_message": ""}


def tool_summary(tool, decision):
    return "%s %s" % (tool, decision)


def held_post(tool, args):
    shown = redact(args)
    return {"tool": tool, "arguments": shown, "buttons": ("allow", "deny"), "runs_tool": False}


def press(book, choice, takeover_active):
    return book.answer(choice, takeover_active)


def help_post(page_url, channel_url):
    return {"posted": False, "text": help_text(page_url, channel_url)}


def routine_result(needs_person, page_url, channel_url):
    return {"browser": False, "paused": bool(needs_person), "discord": None, "posted": False}


def label(source):
    return initiator(source)
