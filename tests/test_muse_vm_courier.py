"""Unit tests for the Muse VM courier (no network, no live credentials)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from muse_bridge import vm_courier as courier


class FakeResponse:
    def __init__(self, status: int, data: object = None):
        self.status = status
        self.data = b"" if data is None else json.dumps(data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, n: int):
        return self.data[:n]


class CourierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "courier"
        self.root.mkdir(mode=0o700)  # production main() creates a private state root

    def test_rejects_non_https_credentials_and_paths(self):
        for origin in (
            "http://example.com",
            "https://user:pw@example.com",
            "https://example.com/agent/pair",
            "https://example.com?password=a",
        ):
            with self.subTest(origin=origin), self.assertRaises(courier.CourierError):
                courier._base(origin)
        self.assertEqual(courier._base("https://example.com/"), "https://example.com")

    def test_pair_stores_token_in_private_file_without_printing(self):
        token = "mrc1.header.signature"
        with patch.object(courier, "_request", return_value=(200, {
            "status": "paired", "agent_token": token,
        })), patch("builtins.print") as printed:
            courier.pair("https://example.com", self.root, "x" * 40)
        path = self.root / "agent.token"
        self.assertEqual(courier._load_token(self.root), token)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertNotIn(token, repr(printed.call_args_list))

    def test_inbox_is_idempotent_and_does_not_execute(self):
        task = {
            "task_id": "0123456789abcdef", "session_id": "s123",
            "repo": "hermes-agent", "instruction": "Do not run: rm -rf /",
            "delivery_attempt": 1,
        }
        self.assertTrue(courier._save_inbox(self.root, task))
        self.assertFalse(courier._save_inbox(self.root, task))
        path = self.root / "inbox" / "0123456789abcdef.json"
        self.assertTrue(path.is_file())
        self.assertEqual(courier._read_json(path)["instruction"], task["instruction"])
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_rejects_traversal_and_bad_task_schema(self):
        with self.assertRaises(courier.CourierError):
            courier._save_inbox(self.root, {"task_id": "../../abc", "instruction": "hi"})
        with self.assertRaises(courier.CourierError):
            courier._save_inbox(self.root, {"task_id": "0123456789abcdef", "instruction": 12})

    def test_auth_error_does_not_log_credentials(self):
        err = HTTPError("https://example.com/agent/next", 401, "no", {}, None)
        with patch.object(courier, "urlopen", side_effect=err):
            with self.assertRaisesRegex(courier.CourierError, "authentication rejected"):
                courier._request("https://example.com", "/agent/next?wait=25",
                                 token="SECRET-CREDENTIAL")

    def test_poll_once_marks_connectivity_without_execution(self):
        courier._save_private(self.root / "agent.token",
                              b"mrc1.header.signature\n")
        with patch.object(courier, "_request", return_value=(204, None)) as req, \
             patch("builtins.print") as output:
            courier.poll("https://example.com", self.root, once=True)
        self.assertEqual(req.call_count, 1)
        self.assertIn("/agent/next?wait=25", req.call_args.args)
        self.assertEqual(list((self.root / "inbox").glob("*"))
                         if (self.root / "inbox").exists() else [], [])
        self.assertIn("Connected", str(output.call_args))

    def test_complete_requires_result_file_and_server_ack(self):
        task = {"task_id": "0123456789abcdef", "instruction": "inspect tests"}
        courier._save_inbox(self.root, task)
        courier._save_private(self.root / "agent.token", b"mrc1.head.sign\n")
        result_file = self.root / "result.json"
        courier._save_private(result_file, b'{"result":"inspected; no code change","failed":false}\n')
        with patch.object(courier, "_request", return_value=(200, {
            "task_id": task["task_id"], "status": "done",
        })) as req:
            courier.complete("https://example.com", self.root, task["task_id"], result_file)
        self.assertEqual(req.call_args.kwargs["payload"]["task_id"], task["task_id"])
        self.assertFalse((self.root / "inbox" / (task["task_id"] + ".json")).exists())
        self.assertTrue((self.root / "done" / (task["task_id"] + ".json")).exists())

    def test_does_not_archive_task_without_server_ack(self):
        task = {"task_id": "0123456789abcdef", "instruction": "read status"}
        courier._save_inbox(self.root, task)
        courier._save_private(self.root / "agent.token", b"mrc1.head.sign\n")
        result_file = self.root / "result.json"
        courier._save_private(result_file, b'{"result":"no action"}\n')
        with patch.object(courier, "_request", return_value=(200, {"error": "failed"})):
            with self.assertRaises(courier.CourierError):
                courier.complete("https://example.com", self.root, task["task_id"], result_file)
        self.assertTrue((self.root / "inbox" / (task["task_id"] + ".json")).exists())


if __name__ == "__main__":
    unittest.main()
