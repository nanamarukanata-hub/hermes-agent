"""Offline tests for the opt-in personal Muse CLI adapter."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from muse_bridge import muse_cli_adapter as muse


class MuseCLIAdapterTests(unittest.TestCase):
    def test_missing_binary(self):
        with patch.object(muse, "_available", return_value=False):
            self.assertEqual(muse.status()["error"], "MUSE_CLI_NOT_INSTALLED")

    def test_missing_authentication(self):
        with patch.object(muse, "_available", return_value=True), patch.object(muse, "_cookie_ready", return_value=False):
            self.assertEqual(muse.status()["error"], "MUSE_AUTH_NOT_CONFIGURED")

    def test_cookie_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / ".config" / "muse-cli"
            directory.mkdir(parents=True)
            f = directory / "cookies.txt"
            f.write_text("test-cookie", encoding="utf-8")
            f.chmod(0o644)
            self.assertFalse(muse._cookie_ready(tmp))
            f.chmod(0o600)
            self.assertTrue(muse._cookie_ready(tmp))

    def test_reply_not_proof_of_code_completion(self):
        with patch.object(muse, "_execute", return_value={"ok": True, "data": {"sent": True, "reply": {"text": "I will inspect"}}}):
            result = muse.send("Inspect", repo="hermes-agent", task_id="test123")
            self.assertTrue(result["sent"])
            self.assertTrue(result["reply_received"])
            self.assertIn("does not prove", result["note"])

    def test_unconfirmed_send(self):
        with patch.object(muse, "_execute", return_value={"ok": True, "data": {"sent": False}}):
            self.assertEqual(muse.send("Inspect", repo="hermes-agent", task_id="test123")["error"], "MUSE_SEND_UNCONFIRMED")

    def test_oversized_request_rejected(self):
        self.assertEqual(muse.send("x" * 6001, repo="hermes-agent", task_id="test123")["error"], "INVALID_INSTRUCTION")

    def test_safe_status_fields_only(self):
        with patch.object(muse, "_execute", return_value={"ok": True, "data": {"reachable": True, "session_count": 1, "access_token": "secret"}}):
            self.assertEqual(muse.status()["muse"], {"reachable": True, "session_count": 1})

    def test_errors_hide_stderr(self):
        fake = type("Finished", (), {"returncode": 1, "stderr": "hatch_sess=secret", "stdout": ""})()
        with patch.object(muse, "_available", return_value=True), patch.object(muse, "_cookie_ready", return_value=True), patch.object(muse.subprocess, "run", return_value=fake):
            self.assertEqual(muse._execute(["status"])["error"], "MUSE_CLI_COMMAND_FAILED")


if __name__ == "__main__":
    unittest.main()
