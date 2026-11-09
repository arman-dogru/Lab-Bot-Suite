import io
import json
import tempfile
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path

from jetson_bot.fetch_page import page_text
from jetson_bot.install_tool import plan_install
from jetson_bot.jetson_image import prepare
from jetson_bot.read_doc import read_path


class DocumentTests(unittest.TestCase):
    def test_readers_and_saved_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.html").write_text("<html><title>Notes</title><style>x{}</style><p>Hello body</p></html>")
            (root / "rows.csv").write_text("a,b\n1,2\n")
            (root / "book.ipynb").write_text(json.dumps({"cells": [{"source": ["print(1)"]}]}))
            message = EmailMessage()
            message["Subject"] = "Letter"
            message.set_content("See you")
            (root / "note.eml").write_bytes(message.as_bytes())
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("content.xml", "<office:body><text:p>ODT line</text:p></office:body>")
            (root / "page.odt").write_bytes(buffer.getvalue())
            listing = io.BytesIO()
            with zipfile.ZipFile(listing, "w") as archive:
                archive.writestr("readme.txt", "hi")
            (root / "bundle.zip").write_bytes(listing.getvalue())
            self.assertIn("Hello body", read_path(root / "notes.html")[1])
            self.assertIn("1, 2", read_path(root / "rows.csv")[1])
            self.assertIn("print(1)", read_path(root / "book.ipynb")[1])
            self.assertEqual(read_path(root / "note.eml")[0], "Letter")
            self.assertIn("See you", read_path(root / "note.eml")[1])
            self.assertIn("ODT line", read_path(root / "page.odt")[1])
            self.assertIn("readme.txt", read_path(root / "bundle.zip")[1])
        parsed = page_text("<html><title>Guide</title><p>Use the Nano.</p></html>")
        self.assertEqual(parsed["title"], "Guide")
        self.assertIn("Use the Nano.", parsed["text"])

    def test_install_allowlist_and_image_copy(self):
        self.assertEqual(plan_install("pip", "pillow==11.3.0"), "allow")
        self.assertEqual(plan_install("pip", "pillow"), "deny")
        self.assertEqual(plan_install("pip", "git+https://example/tool.git"), "deny")
        self.assertEqual(plan_install("host", "tesseract-ocr"), "allow")
        self.assertEqual(plan_install("host", "antiword"), "allow")
        self.assertEqual(plan_install("host", "catdoc"), "allow")
        self.assertEqual(plan_install("host", "nvidia-driver-470"), "deny")
        self.assertEqual(plan_install("host", "linux-image-4.9"), "deny")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "small.jpg"
            source.write_bytes(b"\xff\xd8\xff\xd9")
            self.assertEqual(prepare(source, root / "out.jpg"), "copied")
            source.write_bytes(b"x" * 1_500_001)
            with self.assertRaises(ValueError):
                prepare(source, root / "big.jpg")
