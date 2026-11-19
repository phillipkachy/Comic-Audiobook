"""Stage 4: timeline.json + panels/ + mix.mp3 -> rough_cut.mp4."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import media

W, H, FPS = 1920, 1080, 30
ZOOM_LEAD, ZOOM_TAIL = 0.2, 0.4   # seconds of zoom before / after the line
PUSH_IN = 0.06                    # slow zoom-in amount during a zoom shot


def _zooms(ep_dir: Path, tl: dict, log) -> list[tuple[str, list[float], float, float]]:
    """(page, box, start, end) for each zooms.yaml entry matched to a spoken line in the timeline."""
    p = ep_dir / "zooms.yaml"
    if not p.is_file():
        return []
    import yaml
    specs = (yaml.safe_load(p.read_text()) or {}).get("zooms") or []
    lines = [e for e in tl["events"] if e["kind"] == "line"]
    out = []
    for z in specs:
        hit = [e for e in lines if f"{e['speaker']}: {e['text']}".lower().startswith(z["match"].lower())]
        if not hit:
            log(f"  warning: zoom '{z['match']}' matches no line — skipped")
            continue
        e = hit[0]
        out.append((z["page"], list(map(float, z["box"])), max(0.0, e["start"] - ZOOM_LEAD), e["end"] + ZOOM_TAIL))
    # Back-to-back lines on the same box become one continuous zoom.
    out.sort(key=lambda z: z[2])
    merged = []
    for z in out:
        if merged and merged[-1][0] == z[0] and merged[-1][1] == z[1] and z[2] - merged[-1][3] < 1.5:
            merged[-1] = (z[0], z[1], merged[-1][2], max(merged[-1][3], z[3]))
        else:
            merged.append(z)
    return merged


def build(ep_dir: Path, mock: bool = False, log=print) -> dict:
    media.need_ffmpeg()
    tl_path, mix = ep_dir / "timeline.json", ep_dir / "mix.mp3"
    if not tl_path.is_file() or not mix.is_file():
        raise ValueError("timeline.json / mix.mp3 missing — run the audio stage first")
    tl = json.loads(tl_path.read_text())
    panels = tl["panels"]
    if not panels:
        raise ValueError("script has no [PANEL] directives")
    total = media.duration(mix)

    # Page intervals: first panel covers anything before it; each holds until the next one.
    pages = []
    for i, p in enumerate(panels):
        start = 0.0 if i == 0 else p["start"]
        end = panels[i + 1]["start"] if i + 1 < len(panels) else total
        if end - start > 0.01:
            pages.append((p["file"], start, end))
    for f, _, _ in pages:
        if not (ep_dir / "panels" / f).is_file():
            raise ValueError(f"panel missing: {ep_dir / 'panels' / f}")
    zooms = _zooms(ep_dir, tl, log)

    # Cut the timeline at every page change and zoom edge; each piece is one shot.
    cuts = sorted({0.0, total, *[a for _, a, _ in pages], *[b for _, _, b in pages],
                   *[min(max(z[2], 0.0), total) for z in zooms], *[min(z[3], total) for z in zooms]})
    shots: list[list] = []  # [page, box|None, duration]
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 0.01:
            continue
        page = next(f for f, s, e in pages if s <= a < e) if a < pages[-1][2] else pages[-1][0]
        box = next((z[1] for z in zooms if z[0] == page and z[2] <= a < z[3]), None)
        if shots and shots[-1][0] == page and shots[-1][1] == box:
            shots[-1][2] += b - a
        else:
            shots.append([page, box, b - a])

    work = ep_dir / "build" / "work" / "video"
    work.mkdir(parents=True, exist_ok=True)
    boxes = {}
    segs = []
    preset = "veryfast" if mock else "medium"
    for n, (page, box, d) in enumerate(shots):
        img = ep_dir / "panels" / page
        if page not in boxes:
            boxes[page] = _content_px(img)
        cw, ch, cx, cy = boxes[page]
        if box:
            bx, by, bw, bh = box
            crop = f"crop={int(bw * cw)}:{int(bh * ch)}:{cx + int(bx * cw)}:{cy + int(by * ch)},"
            fit = f"scale={W - 80}:{H - 60}:force_original_aspect_ratio=decrease"
        else:
            crop = f"crop={cw}:{ch}:{cx}:{cy},"
            fit = f"scale=-2:{H - 40}"
        # Compose the still once (the blur is the expensive part): the page or zoomed region,
        # centred on a darkened, blurred copy of itself so it fills the 16:9 frame.
        frame = work / f"frame_{n:03d}.png"
        fc = (f"[0:v]{crop}split[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"boxblur=40:4,eq=brightness=-0.18:saturation=0.8[bg];"
              f"[b]{fit}[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
        media.ff("-i", str(img), "-filter_complex", fc, "-map", "[v]", "-frames:v", "1", str(frame))
        seg = work / f"shot_{n:03d}.mp4"
        if box:
            # Slow push-in while the character speaks.
            frames = max(1, round(d * FPS))
            vf = (f"scale={W * 2}:{H * 2},zoompan=z='1+{PUSH_IN}*on/{frames}':d=1"
                  f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},format=yuv420p")
            media.ff("-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", str(frame),
                     "-vf", vf, "-c:v", "libx264", "-preset", preset, "-crf", "20", "-r", str(FPS), str(seg))
        else:
            media.ff("-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", str(frame),
                     "-vf", "format=yuv420p", "-c:v", "libx264", "-tune", "stillimage",
                     "-preset", preset, "-crf", "20", "-r", str(FPS), str(seg))
        segs.append(seg)
    lst = work / "segments.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs))

    out = ep_dir / "rough_cut.mp4"
    media.ff("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(mix),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out))
    return {"shots": len(shots), "zooms": len(zooms), "duration_sec": round(total, 1), "resolution": f"{W}x{H}"}


def _content_px(img: Path) -> tuple[int, int, int, int]:
    """(w, h, x, y) of the page with black screenshot bars removed."""
    c = _content_box(img)
    if not c:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(img)], capture_output=True, text=True)
        w, h = map(int, r.stdout.strip().split(","))
        return w, h, 0, 0
    w, h, x, y = map(int, c[len("crop="):-1].split(":"))
    return w, h, x, y


def _content_box(img: Path) -> str:
    """crop=... filter (with trailing comma) that removes uniform black bars, or '' if none found."""
    import re
    import subprocess
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loop", "1", "-t", "0.2", "-i", str(img),
                        "-vf", "cropdetect=limit=0.06:round=2:reset=0", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    if not m:
        return ""
    w, h, x, y = map(int, m[-1])
    return f"crop={w}:{h}:{x}:{y}," if w > 100 and h > 100 else ""
