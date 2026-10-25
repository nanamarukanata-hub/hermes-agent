"""Cookie-free resident Muse bridge: auth, pairing, lease, completion tests."""
import json
import tempfile
import time
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from muse_bridge.agent_channel import ResidentChannel
from muse_bridge.server import Bridge, make_handler
from muse_bridge.store import TaskStore


class ResidentChannelTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = TaskStore(tmp.name)
        self.bridge = Bridge(self.store, {})
        self.channel = ResidentChannel(self.store, "known-only-to-render-approval-key-12345678",
                                       "https://muse-chatgpt-bridge.onrender.com")
        self.bridge.channel = self.channel

    def _task(self):
        return self.bridge.call_tool("muse_send_instruction", {
            "repo": "hermes-agent", "instruction": "Update the project README."
        })

    def test_pair_only_once_and_signed_token_survives_new_channel_instance(self):
        data = self.channel.begin_pair()
        self.assertEqual(data["expires_in_seconds"], 600)
        self.assertNotIn("agent_token", data)
        self.assertIn("error", self.channel.exchange_pair("invalid"))
        exchanged = self.channel.exchange_pair(data["pair_code"])
        self.assertEqual(exchanged["status"], "paired")
        token = exchanged["agent_token"]
        self.assertTrue(self.channel.authorize(token))
        self.assertFalse(self.channel.authorize(token[:-1] + "x"))
        self.assertIn("error", self.channel.exchange_pair(data["pair_code"]))
        restored = ResidentChannel(self.store, "known-only-to-render-approval-key-12345678")
        self.assertTrue(restored.authorize(token))
        changed_key = ResidentChannel(self.store, "a-different-secret-approval-key-123456")
        self.assertFalse(changed_key.authorize(token))

    def test_pair_expires(self):
        data = self.channel.begin_pair()
        digest, _ = self.channel._pending_pair
        self.channel._pending_pair = (digest, time.time() - 1)
        self.assertIn("error", self.channel.exchange_pair(data["pair_code"]))

    def test_unapproved_work_never_leaves_render(self):
        task = self._task()
        self.assertEqual(task["status"], "needs_approval")
        self.assertIsNone(self.channel.next_task(0))
        self.assertTrue(self.channel.status()["connected_recently"])
        self.assertEqual(self.store.get_task(task["task_id"])["status"], "needs_approval")

    def test_approved_work_delivered_once_and_completed(self):
        task = self._task()
        self.bridge.call_tool("muse_approve", {"task_id": task["task_id"],
                                                "decision": "approve"})
        order = self.channel.next_task(0)
        self.assertEqual(order["task_id"], task["task_id"])
        self.assertEqual(order["delivery_attempt"], 1)
        self.assertIsNone(self.channel.next_task(0))
        self.assertIn("error", self.channel.complete(task["task_id"], ""))
        reply = self.channel.complete(task["task_id"], "Executed and verified.")
        self.assertEqual(reply["status"], "done")
        self.assertEqual(self.store.get_task(task["task_id"])["result"],
                         "Executed and verified.")
        self.assertIsNone(self.channel.next_task(0))
        self.assertIn("error", self.channel.complete(task["task_id"], "retry overwrite"))

    def test_expired_lease_retries_same_task_id(self):
        task = self._task()
        self.bridge.call_tool("muse_approve", {"task_id": task["task_id"],
                                                "decision": "approve"})
        first = self.channel.next_task()
        self.store.update_task(task["task_id"], agent_lease_expires_at=time.time() - 2)
        second = self.channel.next_task()
        self.assertEqual(second["task_id"], first["task_id"])
        self.assertEqual(second["delivery_attempt"], 2)

    def test_rejects_invalid_completion_and_unclaimed_task(self):
        task = self._task()
        self.assertIn("error", self.channel.complete(task["task_id"], "premature"))
        self.assertIn("error", self.channel.complete("../etc/passwd", "bad"))
        self.bridge.call_tool("muse_approve", {"task_id": task["task_id"],
                                                "decision": "approve"})
        self.channel.next_task(0)
        self.assertIn("error", self.channel.complete(task["task_id"], "good", failed="false"))
        self.assertIn("error", self.channel.complete(task["task_id"], "good", diff="x"*16001))

    def test_http_unauthenticated_next_and_complete_rejected(self):
        class OAuthStub:
            public_url = "https://muse-chatgpt-bridge.onrender.com"
            approval_key = "known-only-to-render-approval-key-12345678"

            @staticmethod
            def validate_access_token(token):
                return False

        server = ThreadingHTTPServer(("127.0.0.1", 0),
                  make_handler(self.bridge, None, OAuthStub(), self.channel))
        thr = Thread(target=server.serve_forever, daemon=True)
        thr.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = "http://127.0.0.1:" + str(server.server_port)
        for path, method, data in (
            ("/agent/next", "GET", None),
            ("/agent/complete", "POST", b'{"task_id":"x"}'),
        ):
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(origin + path, method=method, data=data), timeout=5)
            self.assertEqual(caught.exception.code, 401)
        p = self.channel.begin_pair()
        payload = json.dumps({"pair_code": p["pair_code"]}).encode("utf-8")
        request = Request(origin + "/agent/pair", data=payload,
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as reply:
            token = json.loads(reply.read())["agent_token"]
        auth_headers = {"Authorization": "Bearer " + token}
        with urlopen(Request(origin + "/agent/next?wait=0", headers=auth_headers),
                     timeout=5) as reply:
            self.assertEqual(reply.status, 204)

    def test_tool_annotations_pairing_is_write(self):
        from muse_bridge.server import TOOLS
        by_name = {t["name"]: t for t in TOOLS}
        self.assertTrue(by_name["muse_agent_status"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["muse_pair_begin"]["annotations"]["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
