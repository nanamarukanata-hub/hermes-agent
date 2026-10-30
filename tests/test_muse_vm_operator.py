"""Security and behavior regression tests for the reviewed VM operator."""
from __future__ import annotations

import argparse
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse_bridge import vm_courier, vm_operator as op


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.state = base / "state"
        self.state.mkdir(mode=0o700)
        self.workspace = base / "hermes-agent"
        (self.workspace / "muse_bridge").mkdir(parents=True)
        (self.workspace / "muse_bridge" / "vm_courier.py").write_text("# courier\n")
        (self.workspace / "tests").mkdir()
        self.task_id = "0123456789abcdef"
        vm_courier._save_inbox(self.state, {
            "task_id": self.task_id,
            "repo": "hermes-agent",
            "instruction": "read project status",
        })

    def args(self, action, **kwargs):
        opts = dict(home=self.state, workspace=self.workspace, task_id=self.task_id,
                    action=action, path=None, content_file=None,
                    test_pattern="test_muse_vm_operator.py", overwrite=False, execute=False)
        opts.update(kwargs)
        return argparse.Namespace(**opts)

    def audit_events(self):
        path = self.state / "operator-audit.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_invalid_task_is_rejected_and_audited(self):
        with self.assertRaises(op.CourierError):
            op.execute(self.args("git_status", task_id="0" * 16))
        self.assertEqual(self.audit_events()[-1]["result"], "denied")

    def test_write_without_execute_is_rejected_and_audited(self):
        with self.assertRaisesRegex(op.CourierError, "execute"):
            op.execute(self.args("write_file", path="new.txt"))
        self.assertEqual(self.audit_events()[-1]["result"], "denied")

    def test_path_traversal_is_rejected_and_audited(self):
        with self.assertRaises(op.CourierError):
            op.execute(self.args("read_file", path="../../outside.txt"))
        self.assertEqual(self.audit_events()[-1]["result"], "denied")

    def test_rejects_sensitive_paths(self):
        for name in (".env", ".git/config", "private.key", "agent_token.txt"):
            with self.subTest(path=name), self.assertRaises(op.CourierError):
                op._safe_path(self.workspace, name, allow_missing=True)

    def test_rejects_symlink_escape(self):
        link = self.workspace / "external"
        link.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(op.CourierError):
            op._safe_path(self.workspace, "external/secret.txt", allow_missing=True)

    def test_read_is_audited(self):
        (self.workspace / "notes.txt").write_text("hello\n")
        result = op.execute(self.args("read_file", path="notes.txt"))
        self.assertEqual(result, "hello\n")
        self.assertEqual(self.audit_events()[-1]["result"], "executed")

    def test_write_requires_execute_and_overwrite(self):
        source = self.workspace / "source.txt"
        source.write_text("hello")
        a = self.args("write_file", path="target.txt", content_file=source, execute=True)
        op.execute(a)
        target = self.workspace / "target.txt"
        self.assertEqual(target.read_text(), "hello")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(op.CourierError):
            op.execute(a)
        self.assertEqual(self.audit_events()[-1]["result"], "error")
        a.overwrite = True
        source.write_text("updated")
        op.execute(a)
        self.assertEqual(target.read_text(), "updated")

    def test_git_status_uses_fixed_argv(self):
        with patch.object(op, "_run", return_value="return_code=0") as run:
            op.execute(self.args("git_status"))
        self.assertEqual(run.call_args.args[0], ["git", "status", "--short"])

    def test_git_diff_uses_fixed_argv(self):
        with patch.object(op, "_run", return_value="return_code=0") as run:
            op.execute(self.args("git_diff"))
        self.assertEqual(run.call_args.args[0], ["git", "diff", "--no-ext-diff", "--stat"])

    def test_tests_deny_user_supplied_path_or_globs(self):
        with self.assertRaises(op.CourierError):
            op.execute(self.args("run_tests", execute=True, test_pattern="../../x.py"))
        self.assertEqual(self.audit_events()[-1]["result"], "denied")

    def test_tests_run_only_after_explicit_execute(self):
        with self.assertRaises(op.CourierError):
            op.execute(self.args("run_tests"))
        with patch.object(op, "_run", return_value="return_code=0") as run:
            op.execute(self.args("run_tests", execute=True))
        self.assertEqual(run.call_args.args[0][1:],
                         ["-m", "unittest", "discover", "-s", "tests",
                          "-p", "test_muse_vm_operator.py", "-v"])

    def test_runtime_exception_is_audited(self):
        with patch.object(op, "_run", side_effect=OSError("simulated")):
            with self.assertRaises(OSError):
                op.execute(self.args("git_status"))
        self.assertEqual(self.audit_events()[-1]["result"], "error")

    def test_audit_does_not_include_file_contents(self):
        note = self.workspace / "note.txt"
        note.write_text("private fixture text")
        op.execute(self.args("read_file", path="note.txt"))
        audit = (self.state / "operator-audit.jsonl").read_text()
        self.assertNotIn("private fixture text", audit)

    def test_wrong_repository_is_rejected_and_audited(self):
        path = self.state / "inbox" / (self.task_id + ".json")
        task = json.loads(path.read_text())
        task["repo"] = "another-project"
        path.write_text(json.dumps(task))
        with self.assertRaises(op.CourierError):
            op.execute(self.args("git_status"))
        self.assertEqual(self.audit_events()[-1]["result"], "denied")


if __name__ == "__main__":
    unittest.main()
