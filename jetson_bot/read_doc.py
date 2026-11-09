"""Read the document kinds the Nano accepts. Standard library only."""
import csv
import email
import io
import json
import zipfile
from html.parser import HTMLParser
from pathlib import Path


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = []
        self._skip = 0
        self._title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "title":
            self._title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._title = False

    def handle_data(self, data):
        if self._title:
            self.title.append(data)
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def html_text(raw):
    parser = _Text()
    parser.feed(raw)
    title = " ".join("".join(parser.title).split())
    body = "\n".join(parser.parts)
    return title, body


def _odt_text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("content.xml").decode("utf-8", "replace")
    return html_text(xml.replace("<text:p", "\n<text:p"))[1]


def read_path(path):
    file = Path(path)
    suffix = file.suffix.lower()
    data = file.read_bytes()
    if suffix in (".html", ".htm"):
        title, body = html_text(data.decode("utf-8", "replace"))
        return title, body
    if suffix == ".csv":
        rows = list(csv.reader(io.StringIO(data.decode("utf-8", "replace"))))
        return file.name, "\n".join(", ".join(row) for row in rows)
    if suffix == ".ipynb":
        notebook = json.loads(data.decode("utf-8"))
        chunks = []
        for cell in notebook.get("cells", []):
            source = cell.get("source", "")
            chunks.append("".join(source) if isinstance(source, list) else str(source))
        return file.name, "\n\n".join(chunks)
    if suffix == ".eml":
        message = email.message_from_bytes(data)
        body = []
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    body.append(part.get_payload(decode=True).decode("utf-8", "replace"))
        else:
            payload = message.get_payload(decode=True)
            body.append(payload.decode("utf-8", "replace") if payload else "")
        return message.get("subject", file.name), "\n".join(body)
    if suffix == ".odt":
        return file.name, _odt_text(data)
    if suffix == ".zip":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
        return file.name, "\n".join(names)
    raise ValueError("Unsupported document type: " + suffix)


def main(argv):
    if len(argv) != 2:
        raise SystemExit("Usage: read_doc.py FILE")
    title, body = read_path(argv[1])
    print(title)
    print(body)


if __name__ == "__main__":
    import sys
    main(sys.argv)
