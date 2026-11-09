"""Copy or resize one image under the gateway's per-image byte cap.

The budget service does not decode pixels. Run this before sending a local
photo, plot, or page screenshot.
"""
import shutil
import sys
from pathlib import Path

MAX_BYTES = 1_500_000
SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def prepare(source, dest, resizer=None):
    src, out = Path(source), Path(dest)
    if src.suffix.lower() not in SUFFIXES:
        raise ValueError("Use a JPEG, PNG, WebP, or GIF")
    data = src.read_bytes()
    if src.suffix.lower() in (".jpg", ".jpeg") and len(data) <= MAX_BYTES:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out)
        return "copied"
    if resizer is None:
        raise ValueError("Image needs resizing. Install Pillow in the documents environment and rerun jetson-image.")
    out.parent.mkdir(parents=True, exist_ok=True)
    resizer(data, out, MAX_BYTES)
    if out.stat().st_size > MAX_BYTES:
        raise ValueError("Resized image is still over the gateway byte cap")
    return "resized"


def _pillow_resize(data, dest, limit):
    import io
    from PIL import Image
    image = Image.open(io.BytesIO(data))
    image = image.convert("RGB")
    for edge in (1600, 1280, 1024, 800, 640):
        copy = image.copy()
        copy.thumbnail((edge, edge))
        buffer = io.BytesIO()
        copy.save(buffer, format="JPEG", quality=80)
        if buffer.tell() <= limit:
            dest.write_bytes(buffer.getvalue())
            return
    raise ValueError("Could not fit the image under the byte cap")


def main(argv):
    if len(argv) != 3:
        raise SystemExit("Usage: jetson_image.py SOURCE DEST.jpg")
    try:
        from PIL import Image  # noqa: F401
        resizer = _pillow_resize
    except ImportError:
        resizer = None
    print(prepare(argv[1], argv[2], resizer))


if __name__ == "__main__":
    main(sys.argv)
