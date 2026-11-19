"""Stage 4: timeline.json + panels/ + mix.mp3 -> rough_cut.mp4."""
from __future__ import annotations

import json
from pathlib import Path

from . import media

W, H, FPS = 1920, 1080, 30


def build(ep_dir: Path, mock: bool = False) -> dict:
    media.need_ffmpeg()
    tl_path, mix = ep_dir / "timeline.json", ep_dir / "mix.mp3"
    if not tl_path.is_file() or not mix.is_file():
        raise ValueError("timeline.json / mix.mp3 missing — run the audio stage first")
    tl = json.loads(tl_path.read_text())
    panels = tl["panels"]
    if not panels:
        raise ValueError("script has no [PANEL] directives")
    total = media.duration(mix)

    # First panel covers anything before it; each panel holds until the next one.
    shots = []
    for i, p in enumerate(panels):
        start = 0.0 if i == 0 else p["start"]
        end = panels[i + 1]["start"] if i + 1 < len(panels) else total
        if end - start > 0.01:
            shots.append((ep_dir / "panels" / p["file"], end - start))
    for img, _ in shots:
        if not img.is_file():
            raise ValueError(f"panel missing: {img}")

    work = ep_dir / "build" / "work" / "video"
    work.mkdir(parents=True, exist_ok=True)

    # One segment per shot: page with black screenshot bars trimmed, fit to height,
    # centred on a darkened, blurred copy of itself so a tall page fills the 16:9 frame.
    segs = []
    for n, (img, d) in enumerate(shots):
        crop = _content_box(img)
        frame = work / f"frame_{n:03d}.png"
        seg = work / f"shot_{n:03d}.mp4"
        # Compose the still once (the blur is the expensive part), then loop it.
        fc = (f"[0:v]{crop}split[a][b];"
              f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"boxblur=40:4,eq=brightness=-0.18:saturation=0.8[bg];"
              f"[b]scale=-2:{H - 40}[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")
        media.ff("-i", str(img), "-filter_complex", fc, "-map", "[v]", "-frames:v", "1", str(frame))
        media.ff("-loop", "1", "-framerate", str(FPS), "-t", f"{d:.3f}", "-i", str(frame),
                 "-vf", "format=yuv420p", "-c:v", "libx264", "-tune", "stillimage",
                 "-preset", "veryfast" if mock else "medium", "-crf", "20", "-r", str(FPS), str(seg))
        segs.append(seg)
    lst = work / "segments.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in segs))

    out = ep_dir / "rough_cut.mp4"
    media.ff("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(mix),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out))
    return {"shots": len(shots), "duration_sec": round(total, 1), "resolution": f"{W}x{H}"}


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
