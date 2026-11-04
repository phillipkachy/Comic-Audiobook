#!/usr/bin/env python3
"""Comic audiobook pipeline.

  python run.py <ep> init                      create episodes/<ep>/ from templates
  python run.py <ep> status                    where the episode stands + next step
  python run.py <ep> lint                      check script.md against the rules
  python run.py <ep> audio [--mock]            script.md -> mix.mp3      (needs: script approved)
  python run.py <ep> visuals [--mock]          mix + panels -> rough_cut.mp4 (needs: audio approved)
  python run.py <ep> approve <gate>            gates: outline script audio cut package
  python run.py <ep> reject <gate> "notes"
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

from pipeline import audio, visuals
from pipeline.eleven import ApiStop
from pipeline.media import MediaError
from pipeline.script import lint, parse
from pipeline.status import GATES, STAGE_PREREQ, GateError, Status

ROOT = Path(__file__).resolve().parent
DISCLOSURE_MARKERS = ("altered or synthetic content", "ai-generated")


class Fail(Exception):
    pass


def load_voices() -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "voices.yaml").read_text()) or {}
    voices = {k.upper(): (v or {}) for k, v in (cfg.get("voices") or {}).items()}
    for name, v in voices.items():
        # Hard rule: only original designed voices. No clones, no sound-alikes of real people.
        if v.get("origin") != "designed":
            raise Fail(f"voice {name}: origin must be 'designed' (cloned / imitation voices are not allowed)")
    if "NARRATOR" not in voices:
        raise Fail("config/voices.yaml must define NARRATOR")
    return {"defaults": cfg.get("defaults") or {}, "voices": voices}


def load_cues() -> dict:
    p = ROOT / "config" / "cues.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.is_file() else {}


def ep_path(ep: str, must_exist: bool = True) -> Path:
    d = ROOT / "episodes" / ep
    if must_exist and not d.is_dir():
        raise Fail(f"episode '{ep}' not found — run: python run.py {ep} init")
    return d


def do_lint(ep_dir: Path, quiet: bool = False):
    if not (ep_dir / "script.md").is_file():
        raise Fail("script.md does not exist yet")
    cfg = load_voices()
    rep = lint(parse(ep_dir / "script.md"), cfg["voices"], ep_dir, ROOT / "assets", load_cues(),
               max_character_share=float(cfg["defaults"].get("max_character_share", 0.15)),
               max_quote_words=int(cfg["defaults"].get("max_quote_words", 20)))
    if not quiet or not rep.ok:
        for e in rep.errors:
            print(f"ERROR   {e}")
        for w in rep.warnings:
            print(f"warning {w}")
        s = rep.stats
        print(f"{s['title']!r}: ~{s['est_minutes']} min, {s['character_lines']} character lines, "
              f"character share {s['character_share']:.0%}, {s['panels']} panel switches")
    return rep


def cmd_init(ep: str) -> None:
    d = ep_path(ep, must_exist=False)
    (d / "panels").mkdir(parents=True, exist_ok=True)
    for name in ("outline.md", "script.md", "package.md"):
        dst = d / f"{name}.template"
        if not (d / name).exists() and not dst.exists():
            shutil.copy(ROOT / "templates" / name, dst)
    st = Status(d)
    st.save()
    print(f"created {d.relative_to(ROOT)}/ — drop Phillip's panel files into panels/; "
          f"templates are *.template (rename when writing for real)")


def cmd_status(ep: str) -> None:
    d = ep_path(ep)
    st = Status(d)
    print(f"Episode {ep}")
    for g in GATES:
        info = st.gate(g)
        line = f"  {g:<8} {st.effective_state(g):<15}"
        if info.get("notes") and info["state"] == "rejected":
            line += f" notes: {info['notes']}"
        print(line)
    for stage, b in st.data["builds"].items():
        print(f"  build {stage}: {'MOCK ' if b.get('mock') else ''}{json.dumps({k: v for k, v in b.items() if k != 'mock'})}")
    print(f"Next: {st.next_step().replace('<ep>', ep)}")


def cmd_stage(ep: str, stage: str, mock: bool) -> None:
    d = ep_path(ep)
    st = Status(d)
    st.require(STAGE_PREREQ[stage])
    if stage == "audio":
        rep = do_lint(d, quiet=True)
        if not rep.ok:
            raise Fail("script failed lint — fix before generating audio")
        info = audio.build(d, ROOT, load_voices(), load_cues(), mock)
        st.record_build("audio", info)
        st.save()
        flag = " [MOCK placeholder audio]" if mock else ""
        print(f"mix.mp3 ready{flag}: {info['runtime']} runtime, {info['character_lines']} character lines "
              f"({info['character_runtime_share']:.0%} of runtime), {info['api_calls']} API calls, "
              f"{info['new_tts_characters']} new TTS characters -> {(d / 'mix.mp3').relative_to(ROOT)}")
        if info["character_runtime_share"] >= 0.15:
            print("warning: character lines are >= 15% of runtime — trim before asking for approval")
        if info["dropped_delivery_hints"]:
            print(f"note: {info['dropped_delivery_hints']} delivery hints ignored (voice model has no inline direction)")
    else:
        info = visuals.build(d, mock)
        info["mock"] = st.data["builds"].get("audio", {}).get("mock", mock)
        st.record_build("visuals", info)
        st.save()
        print(f"rough_cut.mp4 ready: {info['shots']} shots, {info['duration_sec']}s "
              f"-> {(d / 'rough_cut.mp4').relative_to(ROOT)}")


def cmd_approve(ep: str, gate: str) -> None:
    d = ep_path(ep)
    st = Status(d)
    if gate == "script" and not do_lint(d, quiet=True).ok:
        raise Fail("script fails lint — cannot approve")
    if gate in ("audio", "cut"):
        stage = "audio" if gate == "audio" else "visuals"
        if st.data["builds"].get(stage, {}).get("mock"):
            raise Fail(f"the current {gate} build is MOCK placeholder audio — re-run without --mock before approving")
    if gate == "package":
        text = (d / "package.md").read_text().lower() if (d / "package.md").is_file() else ""
        missing = [m for m in DISCLOSURE_MARKERS if m not in text]
        if missing:
            raise Fail(f"package.md must disclose AI voices (missing: {', '.join(missing)})")
    st.approve(gate)
    st.save()
    print(f"approved: {gate}")


def cmd_reject(ep: str, gate: str, notes: str) -> None:
    d = ep_path(ep)
    st = Status(d)
    st.reject(gate, notes)
    st.save()
    print(f"rejected: {gate} — notes saved to status.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ep")
    ap.add_argument("command", choices=["init", "status", "lint", "audio", "visuals", "approve", "reject"])
    ap.add_argument("gate", nargs="?")
    ap.add_argument("notes", nargs="?")
    ap.add_argument("--mock", action="store_true", help="placeholder audio, no API calls / credits")
    a = ap.parse_args(argv)
    try:
        if a.command in ("approve", "reject"):
            if a.gate not in GATES:
                raise Fail(f"gate must be one of: {', '.join(GATES)}")
            if a.command == "reject" and not a.notes:
                raise Fail('reject needs notes: python run.py <ep> reject <gate> "what to change"')
            cmd_approve(a.ep, a.gate) if a.command == "approve" else cmd_reject(a.ep, a.gate, a.notes)
        elif a.command == "init":
            cmd_init(a.ep)
        elif a.command == "status":
            cmd_status(a.ep)
        elif a.command == "lint":
            return 0 if do_lint(ep_path(a.ep)).ok else 1
        else:
            cmd_stage(a.ep, a.command, a.mock)
    except GateError as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2
    except ApiStop as e:
        print(f"STOPPED (API): {e}", file=sys.stderr)
        return 3
    except (Fail, ValueError, MediaError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
