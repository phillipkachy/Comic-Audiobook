"""Parse and lint episodes/<ep>/script.md."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

WORDS_PER_SEC = 2.6  # ~155 wpm narration pace, used for estimates and mock audio

RE_PANEL = re.compile(r"^\[PANEL\s+(\S+)\]\s*$", re.I)
RE_MUSIC = re.compile(r"^\[MUSIC\s+(\S+)\]\s*$", re.I)
RE_PAUSE = re.compile(r"^\[PAUSE\s+([0-9]*\.?[0-9]+)\]\s*$", re.I)
RE_SFX = re.compile(r"^SFX:\s*(.+)$", re.I)
RE_LINE = re.compile(r"^([A-Z][A-Z0-9_ \-']*?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$")
RE_TITLE = re.compile(r"^#\s+(.+)$")

MUSIC_STOP = {"none", "stop", "off"}


@dataclass
class Event:
    kind: str  # line | panel | music | sfx | pause
    lineno: int
    speaker: str | None = None
    hint: str | None = None
    text: str | None = None
    value: str | float | None = None

    @property
    def words(self) -> int:
        return len(self.text.split()) if self.text else 0


@dataclass
class Script:
    title: str
    events: list[Event] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def parse(path: Path) -> Script:
    script = Script(title="")
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("<!--") or line.startswith("//"):
            continue
        if m := RE_TITLE.match(line):
            script.title = script.title or m.group(1).strip()
        elif m := RE_PANEL.match(line):
            script.events.append(Event("panel", n, value=m.group(1)))
        elif m := RE_MUSIC.match(line):
            script.events.append(Event("music", n, value=m.group(1)))
        elif m := RE_PAUSE.match(line):
            script.events.append(Event("pause", n, value=float(m.group(1))))
        elif m := RE_SFX.match(line):
            script.events.append(Event("sfx", n, text=m.group(1).strip(), value=slugify(m.group(1).split(",")[0])))
        elif m := RE_LINE.match(line):
            script.events.append(Event("line", n, speaker=m.group(1).strip().upper(),
                                       hint=(m.group(2) or "").strip() or None, text=m.group(3).strip()))
        elif line.startswith("["):
            script.errors.append(f"line {n}: unknown directive: {line}")
        else:
            script.errors.append(f"line {n}: not a recognised line (SPEAKER: text, SFX:, [PANEL], [MUSIC], [PAUSE]): {line[:60]}")
    if not script.title:
        script.errors.append("missing '# Episode title' heading")
    return script


@dataclass
class LintReport:
    errors: list[str]
    warnings: list[str]
    stats: dict

    @property
    def ok(self) -> bool:
        return not self.errors


def lint(script: Script, voices: dict, ep_dir: Path, assets: Path, cues: dict,
         max_character_share: float = 0.15, max_quote_words: int = 20) -> LintReport:
    errors = list(script.errors)
    warnings: list[str] = []
    narr_words = char_words = char_lines = 0
    panels_dir = ep_dir / "panels"
    seen_panel = False
    for ev in script.events:
        if ev.kind == "line":
            if ev.speaker not in voices:
                errors.append(f"line {ev.lineno}: speaker {ev.speaker} is not in config/voices.yaml")
            if ev.speaker == "NARRATOR":
                narr_words += ev.words
            else:
                char_words += ev.words
                char_lines += 1
                if ev.words > max_quote_words:
                    warnings.append(f"line {ev.lineno}: {ev.speaker} line is {ev.words} words; keep character "
                                    f"lines short (<= {max_quote_words}) and retell the rest in narration")
        elif ev.kind == "panel":
            seen_panel = True
            if not (panels_dir / str(ev.value)).is_file():
                errors.append(f"line {ev.lineno}: panel {ev.value} not found in {panels_dir}")
        elif ev.kind == "music" and str(ev.value).lower() not in MUSIC_STOP:
            if not (assets / "music" / f"{ev.value}.mp3").is_file() and ev.value not in cues.get("music", {}):
                errors.append(f"line {ev.lineno}: music cue '{ev.value}' has no assets/music/{ev.value}.mp3 "
                              f"and no prompt in config/cues.yaml")
        elif ev.kind == "sfx":
            if not (assets / "sfx" / f"{ev.value}.mp3").is_file():
                warnings.append(f"line {ev.lineno}: SFX '{ev.value}' not in assets/sfx — will be generated")
    if not seen_panel:
        warnings.append("no [PANEL] directives — visuals stage will have nothing to show")
    total = narr_words + char_words
    share = char_words / total if total else 0.0
    if share >= max_character_share:
        errors.append(f"character lines are {share:.0%} of spoken words (limit {max_character_share:.0%}); "
                      f"move more into narrated retelling")
    pauses = sum(float(e.value) for e in script.events if e.kind == "pause")
    stats = {
        "title": script.title,
        "narration_words": narr_words,
        "character_words": char_words,
        "character_lines": char_lines,
        "character_share": round(share, 3),
        "est_minutes": round((total / WORDS_PER_SEC + pauses) / 60, 1),
        "panels": sum(1 for e in script.events if e.kind == "panel"),
    }
    return LintReport(errors, warnings, stats)
