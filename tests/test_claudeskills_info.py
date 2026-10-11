"""Offline ClaudeSkills.info HTML page and source URL parsing tests."""
from pathlib import Path
import importlib.util
import unittest

ROOT=Path(__file__).resolve().parents[1]
p=ROOT/"scripts/index_claudeskills_info.py"
spec=importlib.util.spec_from_file_location("claude_skills_indexer",p)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class CatalogTests(unittest.TestCase):
    def test_source_links(self):
        html='''<html><a href="/skills/vercel-labs/skills/find-skills/">find-skills</a>
        <a href="/ja/skills/anthropics/skills/frontend-design/">frontend-design</a>
        <a href="/skills/page/171/">pagination</a>
        <a href="/skills/category/coding/">coding</a>
        <a href="https://malicious.example/skills/evil/repo/x/">escape</a></html>'''
        parsed=mod.parse_page(html)
        self.assertEqual(len(parsed),2)
        self.assertEqual({x["id"] for x in parsed},
                         {"vercel-labs/skills/find-skills","anthropics/skills/frontend-design"})
        self.assertTrue(all(x["source"].startswith("https://claudeskills.info/skills/") for x in parsed))
    def test_invalid_identifiers(self):
        for url in ("https://evil.example/skills/a/b/c/",
                    "/skills/page/1/", "/skills/a/b/../../secrets/",
                    "javascript:alert(1)", "/skills/a/b/%2fetc/"):
            self.assertIsNone(mod.normalize_url(url),url)
    def test_no_execution(self):
        self.assertIsNone(mod.normalize_url("/skills/category/design/"))

if __name__=="__main__":
    unittest.main()
