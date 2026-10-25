"""Offline checks: personal Muse sessions, not the Bridge's local task store."""
import tempfile
import unittest
from unittest.mock import patch

from muse_bridge import muse_cli_adapter
from muse_bridge.server import Bridge, TOOLS, INSTRUCTIONS
from muse_bridge.store import TaskStore


class PersonalMuseThreadsTest(unittest.TestCase):
    def test_cli_json_list_is_supported_and_filtered(self):
        response = {"ok": True, "data": [
            {"session_id": "id1", "title": "main", "thread": False,
             "pinned": True, "archived": False, "updated": "2026-10-25 16:00",
             "session_cookie": "DO_NOT_RETURN"},
        ]}
        with patch.object(muse_cli_adapter, "_execute", return_value=response) as run:
            data = muse_cli_adapter.threads()
        self.assertEqual(data["source"], "personal_muse_ai")
        self.assertEqual(data["sessions"][0]["session_id"], "id1")
        self.assertNotIn("session_cookie", data["sessions"][0])
        run.assert_called_once_with(["threads"], timeout=35, allow_list=True)

    def test_include_archived_passes_flag(self):
        with patch.object(muse_cli_adapter, "_execute",
                          return_value={"ok": True, "data": []}) as run:
            self.assertTrue(muse_cli_adapter.threads(include_archived=True)["ok"])
        run.assert_called_once_with(["threads", "--all"], timeout=35, allow_list=True)

    def test_errors_are_not_misreported_as_empty(self):
        with patch.object(muse_cli_adapter, "_execute",
                          return_value={"ok": False, "error": "MUSE_AUTH_NOT_CONFIGURED"}):
            result = muse_cli_adapter.threads()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "MUSE_AUTH_NOT_CONFIGURED")
        self.assertNotIn("sessions", result)

    def test_unexpected_schema_does_not_fake_zero_sessions(self):
        with patch.object(muse_cli_adapter, "_execute",
                          return_value={"ok": True, "data": {"sessions": []}}):
            result = muse_cli_adapter.threads()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "MUSE_THREADS_UNEXPECTED_SCHEMA")

    def test_mcp_exposes_real_threads_separately(self):
        by_name = {tool["name"]: tool for tool in TOOLS}
        self.assertTrue(by_name["muse_remote_threads"]["annotations"]["readOnlyHint"])
        self.assertIn("work-order", by_name["muse_list_sessions"]["description"])
        self.assertLessEqual(len(INSTRUCTIONS), 512)
        with tempfile.TemporaryDirectory() as temp:
            bridge = Bridge(TaskStore(temp), {})
            local = bridge.call_tool("muse_list_sessions", {})
            self.assertEqual(local["source"], "render_bridge_work_orders_only")
            with patch("muse_bridge.server.muse_cli_adapter.threads", return_value={
                "ok": True, "source": "personal_muse_ai", "sessions": [
                    {"session_id": "muse-real-session"}
                ]}) as mock:
                remote = bridge.call_tool("muse_remote_threads", {"include_archived": True})
                self.assertEqual(remote["sessions"][0]["session_id"], "muse-real-session")
                mock.assert_called_once_with(True)

    def test_top_level_list_decode(self):
        fake = type("Result", (), {"returncode": 0, "stdout": '[{"session_id":"s1"}]', "stderr": ""})()
        with patch.object(muse_cli_adapter, "_available", return_value=True), \
             patch.object(muse_cli_adapter, "_cookie_ready", return_value=True), \
             patch.object(muse_cli_adapter.subprocess, "run", return_value=fake):
            result = muse_cli_adapter._execute(["threads"], allow_list=True)
            self.assertEqual(result["data"][0]["session_id"], "s1")
            self.assertEqual(muse_cli_adapter._execute(["threads"])["error"], "MUSE_CLI_INVALID_JSON")


if __name__ == "__main__":
    unittest.main()
