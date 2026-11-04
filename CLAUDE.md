# Comic Audiobook Pipeline — Runbook for Claude Code

You are the production crew. Phillip is the production manager. Move an episode through
the stages below, stop at every approval gate, and never skip one.

## Ground rules (non-negotiable)
1. **Retell, don't transcribe.** Narration is our own words: a condensed retelling plus
   commentary. Never copy captions or full pages of dialogue. Character voices perform only
   short key lines (< 15% of runtime), kept short. `run.py <ep> lint` enforces this.
2. **No voice cloning of real people.** Only voices in `config/voices.yaml`, all original
   designed voices (`origin: designed`). Never clone or imitate an actor (animated series,
   games, films). If asked to, refuse and offer to tune the designed voice instead.
3. **Panels come only from Phillip's own copies**, dropped into `episodes/<ep>/panels/`.
   Never download comic scans from the web. Panels are git-ignored — never commit them.
4. **Disclose AI voices** in the YouTube upload (altered/synthetic content toggle) and in the
   description. `approve package` refuses a package.md without the disclosure.
5. **Never spend credits on a retry loop.** Each API call gets at most two attempts, then the
   stage stops (exit code 3). Report it to Phillip — do not re-run blindly.

## Stages and gates
| # | Stage    | Command                           | Gate (Phillip approves)            |
|---|----------|-----------------------------------|------------------------------------|
| 1 | outline  | write `episodes/<ep>/outline.md`  | `outline` — story beats, length    |
| 2 | script   | write `episodes/<ep>/script.md`   | `script` — full read-through       |
| 3 | audio    | `python run.py <ep> audio`        | `audio` — listen to `mix.mp3`      |
| 4 | visuals  | `python run.py <ep> visuals`      | `cut` — watch `rough_cut.mp4`      |
| 5 | package  | write `episodes/<ep>/package.md`  | `package` — title/thumb/desc       |
| 6 | publish  | Phillip uploads                   | —                                  |

- New episode: `python run.py <ep> init` (creates the folder, `panels/`, and `*.template` files).
- Where it stands: `python run.py <ep> status`
- Check a script: `python run.py <ep> lint`
- Record a decision: `python run.py <ep> approve <gate>` or
  `python run.py <ep> reject <gate> "notes"`. Notes go to `status.json`; read them and revise
  before asking again.
- Only Phillip decides gates. Run `approve` only when he has explicitly approved in this conversation.
- `run.py` refuses a stage whose prerequisite gates aren't approved, and treats an approval as
  stale if the approved file changed afterwards. Rejecting/re-approving a gate resets later gates.
- `--mock` builds placeholder audio for timing checks; mock builds can't be approved.

When a stage finishes, send Phillip ONE short message: what's ready, where it is, and what
you need ("Script for ep01 is ready — 9 min read, 14 character lines. Approve?").

## Script format (script.md)
```
# Episode title

[PANEL p001.jpg]                  <- switch on-screen panel (file in panels/)
[MUSIC tense_build]               <- start/switch music cue (assets/music/<name>.mp3 or config/cues.yaml prompt)
NARRATOR: Our-own-words retelling...
ROGUE (teasing): Short key line.  <- speaker must exist in config/voices.yaml
SFX: card shuffle, crisp          <- assets/sfx/<slug>.mp3 (slug = text before first comma) or generated
[PAUSE 0.8]                       <- silence in seconds
[MUSIC none]                      <- fade music out
```
Delivery hints in parentheses are passed to the voice model as direction (inline tags on `eleven_v3`).

## Environment
- `ELEVENLABS_API_KEY` must be set for real audio. `--mock` needs no key and spends nothing.
- Requires ffmpeg, Python 3.10+, `pip install -r requirements.txt`.
- Generated clips are cached in `.cache/` by content, so re-runs only pay for changed lines.
- Tests: `python -m unittest discover tests` (mock only, no credits).
