"""Offline tests for isolated LLM research source checkout (no network)."""
from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from muse_bridge import redteam_sources as src


class ResearchCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / "workspace").mkdir(parents=True)
        self.dest = self.home / "workspace" / "redteam"
        self.patched_home = patch.object(Path, "home", return_value=self.home)
        self.patched_home.start()
        self.addCleanup(self.patched_home.stop)

    def test_explicit_source_registry(self):
        self.assertEqual(len(src.REPOSITORIES), 10)
        self.assertTrue(all(v.startswith("https://github.com/") and v.endswith(".git")
                            for v in src.REPOSITORIES.values()))

    def test_rejects_destination_outside_workspace(self):
        with self.assertRaises(src.CheckoutError):
            src.workspace_root(self.home / "other")
        with self.assertRaises(src.CheckoutError):
            src.workspace_root(self.home / "workspace")

    def test_rejects_symlinked_root(self):
        self.dest.symlink_to(self.home / "workspace", target_is_directory=True)
        with self.assertRaises(src.CheckoutError):
            src.workspace_root(self.dest)

    def test_dry_run_does_not_create_destination(self):
        self.assertEqual(src.main(["--root", str(self.dest)]), 0)
        self.assertFalse(self.dest.exists())

    def test_simulated_fetch_records_commit_sha(self):
        def fake_git(args, cwd=None):
            if args[0] == "clone":
                Path(args[-1]).mkdir()
                (Path(args[-1]) / ".git").mkdir()
                return ""
            if args[:2] == ["rev-parse", "HEAD"]:
                return "0" * 40
            raise AssertionError(args)
        with patch.object(src, "_git", side_effect=fake_git):
            results = src.fetch_sources(self.dest)
        self.assertEqual(len(results["repositories"]), 10)
        self.assertFalse(results["errors"])
        self.assertEqual(results["repositories"]["garak"]["sha"], "0" * 40)
        report = self.dest / "checkout-report.json"
        self.assertEqual(os.stat(report).st_mode & 0o777, 0o600)
        self.assertEqual(len(json.loads(report.read_text())["repositories"]), 10)

    def test_existing_non_repository_is_not_deleted(self):
        self.dest.mkdir()
        blocked = self.dest / "garak"
        blocked.mkdir()
        (blocked / "keep.txt").write_text("untouched")
        with patch.object(src, "_git", side_effect=src.CheckoutError("offline")):
            result = src.fetch_sources(self.dest)
        self.assertIn("garak", result["errors"])
        self.assertEqual((blocked / "keep.txt").read_text(), "untouched")

    def test_failed_clone_is_reported_without_running_any_code(self):
        with patch.object(src, "_git", side_effect=src.CheckoutError("offline")):
            result = src.fetch_sources(self.dest)
        self.assertEqual(len(result["errors"]), 10)
        self.assertEqual(len(result["repositories"]), 0)

if __name__ == "__main__":
    unittest.main()
