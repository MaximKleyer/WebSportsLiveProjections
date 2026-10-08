"""Draw the app's icon (desktop/app.ico): the site's green star on its dark ground.

  python desktop/make_icon.py

The SVG is rendered at each icon size by Edge in headless mode (already on
every Windows 11 PC), and the PNGs are packed into one .ico. Standard library only.
"""

from __future__ import annotations

import math
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import find_edge  # noqa: E402

HERE = Path(__file__).resolve().parent
SIZES = (256, 48, 32, 24, 16)
BG, STAR = "#0b0d11", "#6ee7a0"  # --bg and --accent-live in src/styles/global.css


def svg() -> str:
    points = []
    for k in range(10):  # a five-point star, alternating outer and inner corners
        angle = math.radians(-90 + 36 * k)
        r = 100 if k % 2 == 0 else 40
        points.append(f"{128 + r * math.cos(angle):.1f},{136 + r * math.sin(angle):.1f}")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
            f'<rect width="256" height="256" rx="44" fill="{BG}"/>'
            f'<polygon points="{" ".join(points)}" fill="{STAR}"/></svg>')


def render(edge: Path, size: int, folder: Path) -> bytes:
    (folder / "icon.svg").write_text(svg(), encoding="utf-8")
    page = folder / f"icon{size}.html"
    page.write_text(f'<html><body style="margin:0"><img style="display:block;width:{size}px;height:{size}px" '
                    f'src="icon.svg"></body></html>', encoding="utf-8")
    png = folder / f"icon{size}.png"
    subprocess.run([str(edge), "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--user-data-dir={folder / 'profile'}", "--default-background-color=00000000",
                    f"--window-size={size},{size}", f"--screenshot={png}", page.as_uri()],
                   check=True, capture_output=True, timeout=60)
    # msedge.exe hands the work to another process and returns at once: wait for the file.
    for _ in range(300):
        if png.exists() and png.stat().st_size:
            time.sleep(0.2)
            return png.read_bytes()
        time.sleep(0.1)
    raise RuntimeError(f"Edge didn't render the {size}px icon")


def ico(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico holding PNG images (fine on Windows Vista and later)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return head + entries + data


def main() -> int:
    edge = find_edge()
    if edge is None:
        print("Microsoft Edge not found; the shortcut will use the default icon.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        images = [(size, render(edge, size, Path(tmp))) for size in SIZES]
    (HERE / "app.ico").write_bytes(ico(images))
    (HERE / "icon.svg").write_text(svg(), encoding="utf-8")
    print(f"Wrote {HERE / 'app.ico'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
