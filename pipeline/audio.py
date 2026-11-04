"""Stage 3: script.md -> mix.mp3 + timeline.json."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import media
from .eleven import ApiStop, Client
from .script import MUSIC_STOP, WORDS_PER_SEC, parse

AUDIO_TAG_MODELS = {"eleven_v3"}  # models that take [bracketed] delivery direction inline
MUSIC_LOOP_MAX = 150.0  # seconds of music generated per cue; longer segments loop it


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def build(ep_dir: Path, root: Path, voices_cfg: dict, cues: dict, mock: bool, log=print) -> dict:
    media.need_ffmpeg()
    script = parse(ep_dir / "script.md")
    if script.errors:
        raise ValueError("script has errors — run `lint` first:\n  " + "\n  ".join(script.errors))

    defaults = voices_cfg.get("defaults", {})
    voices = voices_cfg["voices"]
    model_default = defaults.get("model_id", "eleven_v3")
    line_gap = float(defaults.get("line_gap", 0.35))
    music_volume = float(defaults.get("music_volume", 0.35))

    if not mock:
        missing = sorted({e.speaker for e in script.events if e.kind == "line" and not voices[e.speaker].get("voice_id")})
        if missing:
            raise ApiStop(f"no voice_id set in config/voices.yaml for: {', '.join(missing)}")
    client = None if mock else Client()

    build_dir = ep_dir / "build"
    cache = root / ".cache" / ("mock" if mock else "eleven")
    work = build_dir / "work"
    for d in (build_dir, cache, work):
        d.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    timeline: list[dict] = []
    panels: list[dict] = []
    music_marks: list[dict] = []
    t = 0.0
    char_secs = narr_secs = 0.0
    new_chars = 0
    dropped_hints = 0
    speakers = sorted(voices)

    def add(src: Path, kind: str, **meta) -> float:
        nonlocal t
        wav = work / f"{len(parts):04d}_{kind}.wav"
        if src.suffix == ".wav":
            wav.write_bytes(src.read_bytes())
        else:
            media.to_wav(src, wav)
        d = media.duration(wav)
        parts.append(wav)
        timeline.append({"kind": kind, "start": round(t, 3), "end": round(t + d, 3), **meta})
        t += d
        return d

    def gap(seconds: float, kind: str = "gap", **meta) -> None:
        if seconds <= 0:
            return
        p = cache / f"silence_{seconds:.3f}.wav"
        if not p.exists():
            media.silence(seconds, p)
        add(p, kind, **meta)

    for ev in script.events:
        if ev.kind == "panel":
            panels.append({"file": ev.value, "start": round(t, 3)})
        elif ev.kind == "music":
            music_marks.append({"cue": str(ev.value), "start": round(t, 3)})
        elif ev.kind == "pause":
            gap(float(ev.value), "pause", line=ev.lineno)
        elif ev.kind == "sfx":
            asset = root / "assets" / "sfx" / f"{ev.value}.mp3"
            if asset.is_file():
                src = asset
            else:
                src = cache / f"sfx_{_key(ev.text)}.{'wav' if mock else 'mp3'}"
                if not src.exists():
                    if mock:
                        media.noise(1.0, src)
                    else:
                        log(f"  generating SFX: {ev.text}")
                        client.sfx(ev.text, src)
            add(src, "sfx", text=ev.text, line=ev.lineno)
        elif ev.kind == "line":
            v = voices[ev.speaker]
            model = v.get("model_id", model_default)
            text = ev.text
            if ev.hint:
                if model in AUDIO_TAG_MODELS:
                    text = f"[{ev.hint}] {text}"
                else:
                    dropped_hints += 1
            settings = v.get("settings") or {}
            if mock:
                src = cache / f"tts_{_key(ev.speaker, text)}.wav"
                if not src.exists():
                    secs = max(0.6, len(ev.text.split()) / WORDS_PER_SEC)
                    media.tone(secs, 220 + 40 * speakers.index(ev.speaker), src)
            else:
                src = cache / f"tts_{_key(v['voice_id'], model, text, settings)}.mp3"
                if not src.exists():
                    new_chars += len(text)
                    client.tts(text, v["voice_id"], model, settings, src)
            d = add(src, "line", speaker=ev.speaker, text=ev.text, line=ev.lineno)
            if ev.speaker == "NARRATOR":
                narr_secs += d
            else:
                char_secs += d
            gap(line_gap)

    if not parts:
        raise ValueError("script has no audio events")
    total = t
    voice = build_dir / "voice.wav"
    media.concat_wavs(parts, voice, work / "voice.txt")

    # Music bed: each cue runs until the next [MUSIC] mark (or the end), looped to fit.
    bed_parts: list[Path] = []
    cursor = 0.0
    segments = []
    for i, mk in enumerate(music_marks):
        end = music_marks[i + 1]["start"] if i + 1 < len(music_marks) else total
        if mk["cue"].lower() not in MUSIC_STOP and end > mk["start"]:
            segments.append((mk["cue"], mk["start"], end))
    longest: dict[str, float] = {}
    for cue, s, e in segments:
        longest[cue] = max(longest.get(cue, 0), e - s)
    sources: dict[str, Path] = {}
    for cue, need in longest.items():
        asset = root / "assets" / "music" / f"{cue}.mp3"
        if asset.is_file():
            sources[cue] = asset
            continue
        length = min(need, MUSIC_LOOP_MAX)
        prompt = cues.get("music", {}).get(cue)
        if mock:
            p = cache / f"music_{_key(cue)}_{length:.1f}.wav"
            if not p.exists():
                media.tone(length, 110 + 15 * (sum(map(ord, cue)) % 8), p, volume=0.05)
        else:
            if not prompt:
                raise ValueError(f"music cue '{cue}' has no asset and no prompt in config/cues.yaml")
            p = cache / f"music_{_key(cue, prompt)}_{length:.1f}.mp3"
            if not p.exists():
                log(f"  generating music cue: {cue} ({length:.0f}s)")
                client.music(prompt, length, p)
        sources[cue] = p
    for n, (cue, s, e) in enumerate(segments):
        if s > cursor:
            sil = work / f"bed_{n:03d}_gap.wav"
            media.silence(s - cursor, sil)
            bed_parts.append(sil)
        seg = work / f"bed_{n:03d}_{cue}.wav"
        dur = e - s
        fade = min(1.5, dur / 4)
        media.ff("-stream_loop", "-1", "-i", str(sources[cue]), "-t", f"{dur:.3f}",
                 "-af", f"afade=t=in:d={fade:.2f},afade=t=out:st={dur - fade:.3f}:d={fade:.2f}",
                 "-ac", "1", "-ar", str(media.SR), "-c:a", "pcm_s16le", str(seg))
        bed_parts.append(seg)
        cursor = e

    mix = ep_dir / "mix.mp3"
    if bed_parts:
        bed = build_dir / "music.wav"
        media.concat_wavs(bed_parts, bed, work / "bed.txt")
        media.ff("-i", str(voice), "-i", str(bed), "-filter_complex",
                 f"[1:a]volume={music_volume}[m];[0:a]asplit[v][sc];"
                 "[m][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[md];"
                 "[v][md]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[out]",
                 "-map", "[out]", "-ar", str(media.SR), "-c:a", "libmp3lame", "-b:a", "192k", str(mix))
    else:
        media.ff("-i", str(voice), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                 "-ar", str(media.SR), "-c:a", "libmp3lame", "-b:a", "192k", str(mix))

    spoken = narr_secs + char_secs
    report = {
        "mock": mock,
        "runtime_sec": round(total, 1),
        "runtime": f"{int(total // 60)}:{int(total % 60):02d}",
        "character_lines": sum(1 for x in timeline if x["kind"] == "line" and x["speaker"] != "NARRATOR"),
        "character_runtime_share": round(char_secs / total, 3) if total else 0,
        "character_spoken_share": round(char_secs / spoken, 3) if spoken else 0,
        "new_tts_characters": new_chars,
        "api_calls": client.calls if client else 0,
        "dropped_delivery_hints": dropped_hints,
        "music_cues": sorted(longest),
    }
    (ep_dir / "timeline.json").write_text(json.dumps(
        {"title": script.title, "duration": round(total, 3), "panels": panels,
         "music": music_marks, "events": timeline, "report": report}, indent=2) + "\n")
    return report
