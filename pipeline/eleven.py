"""Minimal ElevenLabs client.

Credit policy: every API call gets at most TWO attempts. A second failure raises
ApiStop, which halts the whole stage so nothing loops on paid endpoints.
Generated clips are cached by content hash, so re-running a stage only pays
for lines that actually changed.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

API = "https://api.elevenlabs.io/v1"
MAX_ATTEMPTS = 2


class ApiStop(Exception):
    pass


class Client:
    def __init__(self, api_key: str | None = None):
        self.key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not self.key:
            raise ApiStop("ELEVENLABS_API_KEY is not set (use --mock to test timing without credits)")
        self.calls = 0

    def _post(self, url: str, payload: dict, dst: Path, what: str) -> None:
        last = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.calls += 1
            try:
                r = requests.post(url, json=payload, timeout=180,
                                  headers={"xi-api-key": self.key, "accept": "audio/mpeg"})
                if r.ok and r.content:
                    tmp = dst.with_suffix(".part")
                    tmp.write_bytes(r.content)
                    tmp.replace(dst)
                    return
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code in (401, 402, 403, 422):
                    break  # auth / quota / bad request: a retry would not help
            except requests.RequestException as e:
                last = str(e)
            if attempt < MAX_ATTEMPTS:
                time.sleep(3)
        raise ApiStop(f"{what} failed ({last}). Stopped — not retrying further.")

    def tts(self, text: str, voice_id: str, model_id: str, settings: dict, dst: Path) -> None:
        payload = {"text": text, "model_id": model_id}
        if settings:
            payload["voice_settings"] = settings
        self._post(f"{API}/text-to-speech/{voice_id}?output_format=mp3_44100_128", payload, dst,
                   f"TTS ({voice_id}: {text[:40]!r})")

    def sfx(self, prompt: str, dst: Path, seconds: float | None = None) -> None:
        payload = {"text": prompt}
        if seconds:
            payload["duration_seconds"] = seconds
        self._post(f"{API}/sound-generation", payload, dst, f"SFX ({prompt!r})")

    def music(self, prompt: str, seconds: float, dst: Path) -> None:
        payload = {"prompt": prompt, "music_length_ms": int(seconds * 1000)}
        self._post(f"{API}/music", payload, dst, f"music ({prompt[:40]!r})")
