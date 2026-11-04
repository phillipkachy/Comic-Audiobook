# PhillZeno — Comic Audiobook Pipeline

Turns a comic story into a narrated audio retelling with commentary, then into a panel-based
video for YouTube. Every stage stops at an approval gate.

```
pip install -r requirements.txt      # plus ffmpeg on PATH
python run.py ep01 init              # then drop your own panel files into episodes/ep01/panels/
python run.py ep01 status
python run.py ep01 lint
python run.py ep01 audio --mock      # timing check, no credits
python run.py ep01 audio             # real voices (needs ELEVENLABS_API_KEY)
python run.py ep01 visuals
```

The full process, rules and script format are in [CLAUDE.md](CLAUDE.md).

Layout:
- `run.py`: CLI (stages, gates, lint)
- `pipeline/`: script parser/lint, gate state, ElevenLabs client, audio mix, video render
- `config/voices.yaml`: designed voices only; `config/cues.yaml`: music cue prompts
- `assets/music`, `assets/sfx`: optional pre-made cues (`<name>.mp3`)
- `templates/`: outline / script / package starters
- `episodes/<ep>/`: outline.md, script.md, package.md, status.json (panels and renders are git-ignored)
