"""Fetch one page and keep its readable text. Parsing does not use the network."""
import sys
from urllib.request import Request, urlopen

try:
    from jetson_bot.read_doc import html_text
except ImportError:  # running the file directly from the Jetson tree
    from read_doc import html_text

MAX_BYTES = 1_000_000


def page_text(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    title, body = html_text(raw)
    return {"title": title, "text": body}


def fetch(url, limit=MAX_BYTES):
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("Only https URLs can be fetched")
    request = Request(url, headers={"User-Agent": "JetsonBot/1.0"})
    with urlopen(request, timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Page is larger than the fetch cap")
    parsed = page_text(raw)
    parsed["url"] = url
    return parsed


def main(argv):
    if len(argv) != 2:
        raise SystemExit("Usage: fetch_page.py https://example")
    parsed = fetch(argv[1])
    print(parsed["title"])
    print(parsed["text"])


if __name__ == "__main__":
    main(sys.argv)
