"""Offline tests for explicit owner-owned agent Markdown edits."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from muse_bridge import agent_config_operator as app
from muse_bridge import vm_courier


class AgentConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir(mode=0o700)
        self.state = base / "courier"
        self.state.mkdir(mode=0o700)
        self.task_id = "0123456789abcdef"
        vm_courier._save_inbox(self.state, {
            "task_id": self.task_id, "repo": "hermes-agent",
            "instruction": "Owner approved AGENT_CONFIG_EDIT[muse_soul] for this task"
        })
        self.file = self.home / "SOUL.md"
        self.file.write_text("# Original owner instructions\n", encoding="utf-8")

    def call(self, *, operation="status", target="muse_soul",
             source_name=None, expected_sha256=None, execute=False):
        return app.run(state=self.state, home=self.home, task_id=self.task_id,
                       target=target, operation=operation, source_name=source_name,
                       expected_sha256=expected_sha256, execute=execute)

    def prepare(self, name="new.md", text="# Replacement text\n"):
        staging = self.state / "staging"
        staging.mkdir(mode=0o700, exist_ok=True)
        dest = staging / name
        dest.write_text(text, encoding="utf-8")
        dest.chmod(0o600)
        return dest

    def test_status_only_needs_authenticated_inbox_not_edit_marker(self):
        task = self.state / "inbox" / (self.task_id + ".json")
        work = json.loads(task.read_text())
        work["instruction"] = "Just inspect local configuration"
        task.write_text(json.dumps(work))
        self.assertTrue(self.call()["exists"])
        self.assertEqual(json.loads((self.state / "config-audit.jsonl").read_text().splitlines()[-1])["result"], "inspected")

    def test_status_never_exposes_contents(self):
        response = self.call()
        self.assertFalse(response["contents_exposed"])
        self.assertNotIn("# Original owner instructions", str(response))
        self.assertEqual(response["sha256"], hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual(json.loads((self.state/"config-audit.jsonl").read_text().splitlines()[-1])["result"], "inspected")

    def test_requires_target_specific_owner_work_order(self):
        with self.assertRaises(app.CourierError):
            self.call(target="muse_agents", operation="apply", execute=True)
        self.assertIn('"denied"', (self.state/"config-audit.jsonl").read_text())

    def test_requires_execute_and_matching_digest(self):
        self.prepare()
        with self.assertRaisesRegex(app.CourierError, "execute"):
            self.call(operation="apply", source_name="new.md")
        with self.assertRaisesRegex(app.CourierError, "changed since review"):
            self.call(operation="apply", source_name="new.md", expected_sha256="0"*64,
                      execute=True)
        self.assertEqual(self.file.read_text(), "# Original owner instructions\n")

    def test_apply_and_private_backup(self):
        self.prepare()
        sha = self.call()["sha256"]
        out = self.call(operation="apply", source_name="new.md", expected_sha256=sha, execute=True)
        self.assertTrue(out["applied"])
        self.assertEqual(self.file.read_text(), "# Replacement text\n")
        backup = Path(out["backup"])
        self.assertEqual(backup.read_text(), "# Original owner instructions\n")
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertNotIn("# Original owner instructions", (self.state/"config-audit.jsonl").read_text())

    def test_refuses_repeat(self):
        self.prepare()
        sha = self.call()["sha256"]
        self.call(operation="apply", source_name="new.md", expected_sha256=sha, execute=True)
        with self.assertRaises(app.CourierError):
            self.call(operation="apply", source_name="new.md",
                      expected_sha256=self.call()["sha256"], execute=True)

    def test_denies_symlink_target_and_stage(self):
        self.file.unlink()
        outside = self.home / "actual.md"
        outside.write_text("outside\n")
        self.file.symlink_to(outside)
        with self.assertRaisesRegex(app.CourierError, "symlink"):
            self.call()
        self.file.unlink()
        self.file.write_text("original\n")
        stage = self.state / "staging"
        stage.mkdir(mode=0o700)
        (stage / "alias.md").symlink_to(outside)
        with self.assertRaises(app.CourierError):
            self.call(operation="apply", source_name="alias.md",
                      expected_sha256=self.call()["sha256"], execute=True)

    def test_denies_other_user_files_and_secret_config(self):
        with self.assertRaises(app.CourierError):
            app._target(self.home, "hermes_config_yaml")
        with self.assertRaises(app.CourierError):
            app._target(self.home, "../something")

    def test_denies_unmarked_work_order(self):
        task = self.state / "inbox" / (self.task_id + ".json")
        payload = json.loads(task.read_text())
        payload["instruction"] = "inspect unrelated project"
        task.write_text(json.dumps(payload))
        with self.assertRaisesRegex(app.CourierError, "explicitly authorize"):
            self.call(operation="apply", execute=True)

if __name__ == "__main__":
    unittest.main()
