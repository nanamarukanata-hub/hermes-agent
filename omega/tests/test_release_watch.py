import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_watch import SOURCES, fetch_latest, main, render

class TestReleaseRadar(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(len(SOURCES), 5)
        self.assertEqual([repo for _, repo in SOURCES], [
            "openai/codex", "anthropics/claude-code", "NousResearch/hermes-agent",
            "garrytan/gbrain", "obsidianmd/obsidian-releases"])

    def test_repeatability(self):
        r = [("Codex", "openai/codex", {"tag": "v1.2.3", "published": "2026-10-13", "url": "https://github.com/openai/codex/releases"})]
        self.assertEqual(render(r), render(r))
        self.assertIn("v1.2.3", render(r))

    def test_escape(self):
        r = [("Codex", "openai/codex", {"tag": "v|x", "published": "2026-10-13", "url": "https://github.com/openai/codex/releases"})]
        self.assertIn("v\\|x", render(r))


    def _mock_release(self, url="https://github.com/garrytan/gbrain/releases/tag/v0.1.0", published="2026-10-23T12:00:00Z"):
        payload = {"tag_name": "v0.1.0", "published_at": published, "html_url": url}
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def test_official_release_metadata(self):
        with patch("release_watch.urlopen", return_value=self._mock_release()):
            self.assertEqual(fetch_latest("garrytan/gbrain")["published"], "2026-10-23")

    def test_rejects_unofficial_release_url(self):
        with patch("release_watch.urlopen", return_value=self._mock_release(url="https://evil.example/releases/tag/v0.1.0")):
            with self.assertRaises(ValueError):
                fetch_latest("garrytan/gbrain")

    def test_rejects_bad_date(self):
        with patch("release_watch.urlopen", return_value=self._mock_release(published="tomorrow")):
            with self.assertRaises(ValueError):
                fetch_latest("garrytan/gbrain")

    def test_partial_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "latest.md"
            out.write_text("existing\n", encoding="utf-8")
            with patch("release_watch.fetch_latest", side_effect=ValueError("test failure")):
                with patch.object(sys, "argv", ["release_watch.py", "--output", str(out)]):
                    self.assertEqual(main(), 1)
            self.assertEqual(out.read_text(encoding="utf-8"), "existing\n")

    def test_idempotent_success(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "latest.md"
            sample = {"tag": "v1.0", "published": "2026-10-23", "url": "https://github.com/openai/codex/releases/tag/v1.0"}
            with patch("release_watch.fetch_latest", return_value=sample):
                with patch.object(sys, "argv", ["release_watch.py", "--output", str(out)]):
                    self.assertEqual(main(), 0)
                    first = out.read_text(encoding="utf-8")
                    self.assertEqual(main(), 0)
                    self.assertEqual(out.read_text(encoding="utf-8"), first)
                    self.assertIn("GBrain", first)

if __name__ == "__main__":
    unittest.main()
