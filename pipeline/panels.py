"""Detect comic panels on a page by recursive XY-cuts along light gutters.

Boxes are fractions (x, y, w, h) of the page *after* the black screenshot bars are trimmed,
numbered in reading order (top-to-bottom rows, left-to-right within a row).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

BAR_LUMA = 20          # rows darker than this on average count as screenshot bars
GUTTER_LUMA = 225      # a gutter pixel is at least this bright
GUTTER_FRAC = 0.985    # share of a line that must be gutter-bright
MIN_GUTTER = 6         # px
MIN_PANEL = 0.08       # of page size, per side


def content_box(arr: np.ndarray) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) with uniform dark screenshot bars removed."""
    luma = arr.mean(axis=2)
    rows = np.where(luma.mean(axis=1) > BAR_LUMA)[0]
    cols = np.where(luma.mean(axis=0) > BAR_LUMA)[0]
    if len(rows) == 0 or len(cols) == 0:
        return 0, 0, arr.shape[1], arr.shape[0]
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def _runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def _split(bright: np.ndarray, axis: int, min_size: int) -> list[tuple[int, int]]:
    """Segments between gutters along axis (0 = rows, 1 = columns)."""
    share = bright.mean(axis=1 - axis)
    gutters = _runs(share >= GUTTER_FRAC, MIN_GUTTER)
    n = bright.shape[axis]
    edges, prev = [], 0
    for a, b in gutters:
        if a - prev >= min_size:
            edges.append((prev, a))
        prev = b
    if n - prev >= min_size:
        edges.append((prev, n))
    return edges or [(0, n)]


def _trim(bright: np.ndarray) -> tuple[int, int, int, int] | None:
    ink = ~bright
    rows, cols = np.where(ink.any(axis=1))[0], np.where(ink.any(axis=0))[0]
    if len(rows) == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def detect(img_path: Path) -> dict:
    arr = np.asarray(Image.open(img_path).convert("RGB"))
    x0, y0, x1, y1 = content_box(arr)
    page = arr[y0:y1, x0:x1]
    H, W = page.shape[:2]
    bright = page.min(axis=2) >= GUTTER_LUMA
    boxes = []

    def cut(x, y, w, h, depth):
        sub = bright[y:y + h, x:x + w]
        t = _trim(sub)
        if t is None:
            return
        x, y, w, h = x + t[0], y + t[1], t[2] - t[0], t[3] - t[1]
        sub = bright[y:y + h, x:x + w]
        rows = _split(sub, 0, int(MIN_PANEL * H))
        if len(rows) > 1 and depth < 6:
            for a, b in rows:
                cut(x, y + a, w, b - a, depth + 1)
            return
        cols = _split(sub, 1, int(MIN_PANEL * W))
        if len(cols) > 1 and depth < 6:
            for a, b in cols:
                cut(x + a, y, b - a, h, depth + 1)
            return
        if w >= MIN_PANEL * W and h >= MIN_PANEL * H:
            boxes.append((x, y, w, h))

    cut(0, 0, W, H, 0)
    boxes.sort(key=lambda b: (round(b[1] / (H * 0.05)), b[0]))
    return {
        "content": [x0, y0, x1 - x0, y1 - y0],
        "panels": [[round(x / W, 4), round(y / H, 4), round(w / W, 4), round(h / H, 4)]
                   for x, y, w, h in boxes],
    }


def detect_all(ep_dir: Path) -> dict:
    out = {}
    for img in sorted((ep_dir / "panels").iterdir()):
        if img.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            out[img.name] = detect(img)
    return out


def contact_sheet(img_path: Path, info: dict, dst: Path, width: int = 600) -> None:
    im = Image.open(img_path).convert("RGB")
    x0, y0, w0, h0 = info["content"]
    im = im.crop((x0, y0, x0 + w0, y0 + h0))
    s = width / im.width
    im = im.resize((width, int(im.height * s)))
    d = ImageDraw.Draw(im)
    for i, (x, y, w, h) in enumerate(info["panels"], 1):
        box = (x * im.width, y * im.height, (x + w) * im.width, (y + h) * im.height)
        d.rectangle(box, outline=(255, 0, 255), width=4)
        d.rectangle((box[0], box[1], box[0] + 34, box[1] + 30), fill=(255, 0, 255))
        d.text((box[0] + 8, box[1] + 6), str(i), fill=(255, 255, 255), font_size=22)
    im.save(dst)


if __name__ == "__main__":
    import sys
    ep = Path(sys.argv[1])
    print(json.dumps(detect_all(ep), indent=1))
