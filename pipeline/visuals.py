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

    work = ep_dir / "build" / "work"
    work.mkdir(parents=True, exist_ok=True)
    lst = work / "panels.txt"
    lines = []
    for img, d in shots:
        lines += [f"file '{img.resolve()}'", f"duration {d:.3f}"]
    lines.append(f"file '{shots[-1][0].resolve()}'")  # concat demuxer needs the last file repeated
    lst.write_text("\n".join(lines) + "\n")

    out = ep_dir / "rough_cut.mp4"
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS},format=yuv420p")
    media.ff("-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(mix),
             "-vf", vf, "-map", "0:v", "-map", "1:a",
             "-c:v", "libx264", "-preset", "veryfast" if mock else "medium", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out))
    return {"shots": len(shots), "duration_sec": round(total, 1), "resolution": f"{W}x{H}"}
