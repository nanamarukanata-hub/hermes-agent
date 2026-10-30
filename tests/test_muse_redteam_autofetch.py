"""Offline tests for exact, source-only Courier auto-fetch actions."""
from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from muse_bridge import vm_courier as courier
from muse_bridge import redteam_sources as research


class AutoSourceFetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "courier"
        self.state.mkdir(mode=0o700)
        courier._save_private(self.state / "agent.token", b"mrc1.head.signature\n")
        self.task = {
            "task_id": "0123456789abcdef",
            "repo": "hermes-agent",
            "instruction": "MUSE_SOURCE_FETCH_V1",
        }
        self.report = {
            "repositories": {"garak": {"sha": "a" * 40, "status": "cloned"}},
            "errors": {},
        }

    def test_only_exact_marker_is_eligible(self):
        for value in [
            "Please MUSE_SOURCE_FETCH_V1",
            "MUSE_SOURCE_FETCH_V1\nrm -rf ~/workspace",
            "MUSE_SOURCE_FETCH_V1 ",
            "muse_source_fetch_v1",
            "run arbitrary shell",
        ]:
            with self.subTest(value=value):
                with patch.object(research, "fetch_sources") as fetch:
                    self.assertFalse(courier._auto_source_fetch(
                        "https://bridge.example", self.state,
                        {**self.task, "instruction": value}))
                    fetch.assert_not_called()

    def test_wrong_repo_never_executes(self):
        with patch.object(research, "fetch_sources") as fetch:
            self.assertFalse(courier._auto_source_fetch(
                "https://bridge.example", self.state,
                {**self.task, "repo": "untrusted"}))
            fetch.assert_not_called()

    def test_received_task_is_only_saved_without_enable_flag(self):
        with patch.object(courier, "_request", return_value=(200, self.task)), \
             patch.object(research, "fetch_sources") as fetch:
            courier.poll("https://bridge.example", self.state, once=True)
            fetch.assert_not_called()
        self.assertTrue((self.state / "inbox" / (self.task["task_id"] + ".json")).exists())

    def test_auto_fetch_reports_and_requires_server_ack(self):
        def api(base, path, **kwargs):
            if path.startswith("/agent/next"):
                return 200, self.task
            if path == "/agent/complete":
                payload = kwargs["payload"]
                self.assertEqual(payload["task_id"], self.task["task_id"])
                self.assertIn("source-only", payload["result"].lower())
                return 200, {"task_id": self.task["task_id"], "status": "done"}
            raise AssertionError(path)

        with patch.object(courier, "_request", side_effect=api), \
             patch.object(research, "fetch_sources", return_value=self.report) as fetch:
            courier.poll("https://bridge.example", self.state, once=True, auto_source_fetch=True)
            fetch.assert_called_once()
        self.assertTrue((self.state / "done" / (self.task["task_id"] + ".json")).exists())
        self.assertTrue((self.state / "auto-source-results" /
                         (self.task["task_id"] + ".json")).exists())

    def test_auto_fetch_failure_never_claims_success(self):
        self.assertTrue(courier._save_inbox(self.state, self.task))
        calls = []
        def api(base, path, **kwargs):
            calls.append(kwargs["payload"])
            return 200, {"task_id": self.task["task_id"], "status": "failed"}
        with patch.object(research, "fetch_sources", side_effect=research.CheckoutError("network")), \
             patch.object(courier, "_request", side_effect=api):
            self.assertTrue(courier._auto_source_fetch("https://bridge.example", self.state, self.task))
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["failed"])
        self.assertNotIn("network", calls[0]["result"])

    def test_unknown_task_is_not_marked_done(self):
        def api(base, path, **kwargs):
            return 200, {"unexpected": "response"}
        self.assertTrue(courier._save_inbox(self.state, self.task))
        with patch.object(research, "fetch_sources", return_value=self.report), \
             patch.object(courier, "_request", side_effect=api):
            with self.assertRaises(courier.CourierError):
                courier._auto_source_fetch("https://bridge.example", self.state, self.task)
        self.assertTrue((self.state / "inbox" / (self.task["task_id"] + ".json")).exists())

if __name__ == "__main__":
    unittest.main()
