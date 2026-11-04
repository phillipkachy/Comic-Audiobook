"""End-to-end smoke test in --mock mode (no API calls). Run: python -m unittest discover tests"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SCRIPT = """# Test Episode

[PANEL p001.png]
[MUSIC tense_build]
NARRATOR: The night the deal went wrong started like any other, with a quiet room and a nervous dealer.
NARRATOR: Everybody at the table thought they knew who was bluffing, and every one of them was wrong.
ROGUE (teasing): Y'all sure about that?
[PAUSE 0.5]
SFX: card shuffle, crisp
[PANEL p002.png]
NARRATOR: What makes this scene work is how little is said out loud; the art carries the tension for us.
NARRATOR: And that is exactly where our story turns, so let's slow down and look closer at the cards.
[MUSIC none]
"""


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for item in ("run.py", "pipeline", "config", "templates", "assets"):
            src = REPO / item
            (shutil.copytree if src.is_dir() else shutil.copy)(src, self.tmp / item)
        voices = (self.tmp / "config" / "voices.yaml").read_text()
        voices += "  ROGUE:\n    origin: designed\n    voice_id: null\n"
        (self.tmp / "config" / "voices.yaml").write_text(voices)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_cli(self, *args, ok=True):
        r = subprocess.run([sys.executable, "run.py", "ep01", *args], cwd=self.tmp, capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_full_flow_mock(self):
        self.run_cli("init")
        ep = self.tmp / "episodes" / "ep01"
        for i, color in enumerate(["red", "blue"], 1):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            f"color={color}:s=800x1200", "-frames:v", "1", str(ep / "panels" / f"p00{i}.png")], check=True)

        # Gates are enforced in order.
        self.assertEqual(self.run_cli("audio", "--mock", ok=False).returncode, 2)
        (ep / "outline.md").write_text("# outline\n")
        (ep / "script.md").write_text(SCRIPT)
        self.assertNotEqual(self.run_cli("approve", "script", ok=False).returncode, 0)  # outline first
        self.run_cli("reject", "outline", "tighten beat 2")
        self.assertEqual(json.loads((ep / "status.json").read_text())["gates"]["outline"]["notes"], "tighten beat 2")
        self.run_cli("approve", "outline")
        self.run_cli("lint")
        self.run_cli("approve", "script")

        self.run_cli("audio", "--mock")
        self.assertTrue((ep / "mix.mp3").is_file())
        # Mock audio cannot be approved.
        self.assertNotEqual(self.run_cli("approve", "audio", ok=False).returncode, 0)
        # Without an API key a real run stops cleanly before spending anything.
        r = self.run_cli("audio", ok=False)
        self.assertEqual(r.returncode, 3, r.stderr)

        # Force-approve the mock build for the test so visuals can run.
        st = json.loads((ep / "status.json").read_text())
        st["builds"]["audio"]["mock"] = False
        (ep / "status.json").write_text(json.dumps(st))
        self.run_cli("approve", "audio")
        self.run_cli("visuals", "--mock")
        self.assertTrue((ep / "rough_cut.mp4").is_file())

        # Editing the script makes the script approval stale and blocks downstream stages.
        (ep / "script.md").write_text(SCRIPT + "NARRATOR: One more thought.\n")
        self.assertEqual(self.run_cli("visuals", "--mock", ok=False).returncode, 2)
        self.assertIn("approved-stale", self.run_cli("status").stdout)

    def test_lint_rules(self):
        self.run_cli("init")
        ep = self.tmp / "episodes" / "ep01"
        (ep / "script.md").write_text("# T\n[PANEL missing.jpg]\nNARRATOR: short.\nROGUE: " + "word " * 30 + "\nGAMBIT: hi\n")
        r = self.run_cli("lint", ok=False)
        self.assertIn("panel missing.jpg not found", r.stdout)
        self.assertIn("GAMBIT is not in config/voices.yaml", r.stdout)
        self.assertIn("character lines are", r.stdout)
        self.assertIn("keep character lines short", r.stdout)

    def test_rejects_non_designed_voice(self):
        p = self.tmp / "config" / "voices.yaml"
        p.write_text(p.read_text() + "  CLONE:\n    origin: cloned\n")
        self.run_cli("init")
        (self.tmp / "episodes" / "ep01" / "script.md").write_text("# T\nNARRATOR: hi\n")
        self.assertIn("origin must be 'designed'", self.run_cli("lint", ok=False).stderr)


if __name__ == "__main__":
    unittest.main()
