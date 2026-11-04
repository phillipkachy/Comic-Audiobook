"""Thin ffmpeg/ffprobe helpers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SR = 44100


class MediaError(Exception):
    pass


def need_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise MediaError(f"{tool} not found on PATH")


def ff(*args: str) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise MediaError(f"ffmpeg failed: {' '.join(cmd)}\n{r.stderr.strip()}")


def duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError as e:
        raise MediaError(f"could not read duration of {p}: {r.stderr.strip()}") from e


def to_wav(src: Path, dst: Path) -> None:
    """Normalise any clip to mono 44.1k PCM so concatenation is sample-exact."""
    ff("-i", str(src), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(dst))


def silence(seconds: float, dst: Path) -> None:
    ff("-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{seconds:.3f}",
       "-c:a", "pcm_s16le", str(dst))


def tone(seconds: float, freq: float, dst: Path, volume: float = 0.08) -> None:
    ff("-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={SR}", "-t", f"{seconds:.3f}",
       "-af", f"volume={volume},afade=t=in:d=0.05,afade=t=out:st={max(seconds - 0.05, 0):.3f}:d=0.05",
       "-ac", "1", "-c:a", "pcm_s16le", str(dst))


def noise(seconds: float, dst: Path, volume: float = 0.05) -> None:
    ff("-f", "lavfi", "-i", f"anoisesrc=r={SR}:a={volume}:c=pink", "-t", f"{seconds:.3f}",
       "-ac", "1", "-c:a", "pcm_s16le", str(dst))


def concat_wavs(parts: list[Path], dst: Path, listfile: Path) -> None:
    listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    ff("-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(dst))
