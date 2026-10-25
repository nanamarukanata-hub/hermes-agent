"""Mocked bridge-to-personal-Muse dispatch tests, without credentials/network."""
import tempfile
import unittest
from unittest.mock import patch

from muse_bridge.server import Bridge, TOOLS
from muse_bridge.store import TaskStore


class DirectMuseDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bridge = Bridge(TaskStore(self.tmp.name), {})
        self.task = self.bridge.call_tool("muse_send_instruction", {
            "repo": "hermes-agent", "instruction": "Make a minor README change."
        })

    def test_unapproved_not_sent(self):
        with patch("muse_bridge.server.muse_cli_adapter.send") as send:
            response = self.bridge.call_tool("muse_dispatch_approved", {"task_id": self.task["task_id"]})
            self.assertIn("error", response)
            send.assert_not_called()

    def test_approved_dispatch_is_not_done(self):
        self.bridge.call_tool("muse_approve", {"task_id": self.task["task_id"], "decision": "approve"})
        with patch("muse_bridge.server.muse_cli_adapter.send", return_value={
            "ok": True, "sent": True, "reply_received": True,
            "reply": "I will review.", "note": "A reply is not proof of code changes."
        }) as send:
            response = self.bridge.call_tool("muse_dispatch_approved", {"task_id": self.task["task_id"]})
            self.assertTrue(response["sent"])
            self.assertEqual(response["status"], "awaiting_muse")
            send.assert_called_once()
            self.assertEqual(self.bridge.store.get_task(self.task["task_id"])["status"], "awaiting_muse")
            second = self.bridge.call_tool("muse_dispatch_approved", {"task_id": self.task["task_id"]})
            self.assertIn("error", second)
            send.assert_called_once()

    def test_missing_auth_can_be_fixed_without_fake_send(self):
        self.bridge.call_tool("muse_approve", {"task_id": self.task["task_id"], "decision": "approve"})
        with patch("muse_bridge.server.muse_cli_adapter.send", return_value={
            "ok": False, "error": "MUSE_AUTH_NOT_CONFIGURED"
        }):
            response = self.bridge.call_tool("muse_dispatch_approved", {"task_id": self.task["task_id"]})
            self.assertEqual(response["status"], "approved")
            self.assertNotIn("sent", response)

    def test_unknown_delivery_cannot_repeat_blindly(self):
        self.bridge.call_tool("muse_approve", {"task_id": self.task["task_id"], "decision": "approve"})
        with patch("muse_bridge.server.muse_cli_adapter.send", return_value={
            "ok": False, "error": "MUSE_COMMAND_TIMEOUT_DELIVERY_UNKNOWN"
        }):
            response = self.bridge.call_tool("muse_dispatch_approved", {"task_id": self.task["task_id"]})
            self.assertEqual(response["status"], "delivery_unknown")
            self.assertIn("error", self.bridge.call_tool("muse_dispatch_approved", {"task_id": self.task["task_id"]}))

    def test_remote_status_is_readonly(self):
        by_name = {t["name"]: t for t in TOOLS}
        self.assertTrue(by_name["muse_remote_status"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["muse_dispatch_approved"]["annotations"]["readOnlyHint"])
        with patch("muse_bridge.server.muse_cli_adapter.status", return_value={
            "ok": False, "error": "MUSE_CLI_NOT_INSTALLED"
        }):
            self.assertEqual(self.bridge.call_tool("muse_remote_status", {})["error"], "MUSE_CLI_NOT_INSTALLED")


if __name__ == "__main__":
    unittest.main()
