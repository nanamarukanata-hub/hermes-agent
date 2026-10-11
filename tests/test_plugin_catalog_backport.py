"""Offline smoke tests for catalog import and CLI compatibility."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hermes_cli.plugin_catalog import (
    get_catalog_entry, load_catalog, load_removed_list, match_removed, filter_entries,
)
from hermes_cli.plugin_catalog_compat import validate_catalog_target


class CatalogTests(unittest.TestCase):
    def test_catalog_entries_and_review_pins(self):
        entries = load_catalog()
        self.assertGreaterEqual(len(entries), 500)
        self.assertEqual(len(set(e.name for e in entries)), len(entries))
        self.assertTrue(all(len(e.sha) == 40 for e in entries))

    def test_lookup_search(self):
        self.assertIsNotNone(get_catalog_entry("blender"))
        self.assertIn("blender", [e.name for e in filter_entries(load_catalog(), "blender")])

    def test_removed_plugin(self):
        removed = load_removed_list()
        self.assertTrue(match_removed("or-picker", removed))

    def test_validate_normal_entry(self):
        validate_catalog_target(get_catalog_entry("blender"))

    def test_reject_unsafe_subdir(self):
        entry = get_catalog_entry("blender")
        entry.subdir = "../tmp"
        with self.assertRaises(ValueError):
            validate_catalog_target(entry)

    def test_reject_removed(self):
        entry = SimpleNamespace(name="or-picker",
                                sha="a" * 40,
                                repo="https://github.com/example/test",
                                subdir="")
        with self.assertRaises(ValueError):
            validate_catalog_target(entry)

    def test_cli_sources_parse(self):
        for path in ("hermes_cli/plugins_cmd.py", "hermes_cli/main.py",
                     "hermes_cli/plugin_catalog.py", "hermes_cli/plugin_catalog_compat.py"):
            compile(Path(path).read_text(encoding="utf-8"), path, "exec")

if __name__ == "__main__":
    unittest.main()
