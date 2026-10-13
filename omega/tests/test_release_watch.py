import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from release_watch import SOURCES, render

class TestReleaseRadar(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(len(SOURCES), 4)
        self.assertEqual(SOURCES[0][1], "openai/codex")

    def test_repeatability(self):
        r = [("Codex", "openai/codex", {"tag": "v1.2.3", "published": "2026-10-13", "url": "https://github.com/openai/codex/releases"})]
        self.assertEqual(render(r), render(r))
        self.assertIn("v1.2.3", render(r))

    def test_escape(self):
        r = [("Codex", "openai/codex", {"tag": "v|x", "published": "2026-10-13", "url": "https://github.com/openai/codex/releases"})]
        self.assertIn("v\\|x", render(r))

if __name__ == "__main__":
    unittest.main()
