"""No network or billable calls: mocked Muse -> GPT connector regression tests."""
import io
import json
import os
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from muse_bridge import gpt_connector
from muse_bridge.agent_channel import ResidentChannel
from muse_bridge.server import Bridge, make_handler
from muse_bridge.store import TaskStore


class Reply:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n):
        return json.dumps({
            "status": "completed",
            "output": [{"type": "reasoning"}, {"type": "message", "content": [
                {"type": "output_text", "text": "GPT test reply"}]}],
            "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        }).encode()


class GPTConnectorUnitTests(unittest.TestCase):
    def test_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gpt_connector.ask("hello")["error"], "OPENAI_API_KEY_NOT_CONFIGURED")

    def test_prompt_is_bounded(self):
        self.assertEqual(gpt_connector.ask("x" * 6001)["error"], "INVALID_PROMPT")
        self.assertEqual(gpt_connector.ask(" ")["error"], "INVALID_PROMPT")

    def test_api_called_only_with_operator_model(self):
        seen = []
        def fake(req, timeout):
            seen.append((req, timeout))
            return Reply()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-test", "MUSE_GPT_MODEL": "gpt-4.1-mini"}):
            with patch.object(gpt_connector, "urlopen", side_effect=fake):
                result = gpt_connector.ask("Hi Muse")
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "GPT test reply")
        req, timeout = seen[0]
        self.assertEqual(timeout, 75)
        sent = json.loads(req.data)
        self.assertEqual(sent["input"], "Hi Muse")
        self.assertEqual(sent["model"], "gpt-4.1-mini")
        self.assertIs(sent["store"], False)
        self.assertEqual(sent["max_output_tokens"], 1024)
        self.assertEqual(req.get_header("Authorization"), "Bearer secret-test")
        self.assertNotIn("secret-test", str(result))

    def test_upstream_auth_error_is_sanitized(self):
        err = HTTPError(gpt_connector.API_URL, 401, "secret details", {}, io.BytesIO(b"api error secret"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-test"}):
            with patch.object(gpt_connector, "urlopen", side_effect=err):
                result = gpt_connector.ask("Hi")
        self.assertEqual(result, {"error": "OPENAI_AUTH_FAILED", "http_status": 401})


class HTTPAuthTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = TaskStore(tmp.name)
        channel = ResidentChannel(store, "test-only-persistent-approval-key-0123456", "https://example.invalid")
        bridge = Bridge(store, {})
        bridge.channel = channel
        self.channel = channel
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(bridge, None, None, channel))
        t = Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def post(self, token=None, data=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request(self.base + "/agent/gpt", data=json.dumps(data or {"prompt": "hello"}).encode(),
                      headers=headers, method="POST")
        try:
            with urlopen(req, timeout=4) as r:
                return r.status, json.loads(r.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_no_token_is_unauthorized_without_call(self):
        with patch("muse_bridge.server.gpt_connector.ask") as f:
            status, data = self.post()
            self.assertEqual(status, 401)
            f.assert_not_called()

    def test_invalid_token_is_unauthorized(self):
        status, _ = self.post(token="bad-token")
        self.assertEqual(status, 401)

    def test_valid_paired_token_calls_api(self):
        p = self.channel.begin_pair()
        token = self.channel.exchange_pair(p["pair_code"])["agent_token"]
        with patch("muse_bridge.server.gpt_connector.ask", return_value={"ok": True, "answer": "Hi"}) as fn:
            status, reply = self.post(token=token)
        self.assertEqual(status, 200)
        self.assertEqual(reply["answer"], "Hi")
        fn.assert_called_once_with("hello")

    def test_missing_key_returns_503_only_after_auth(self):
        p = self.channel.begin_pair()
        token = self.channel.exchange_pair(p["pair_code"])["agent_token"]
        with patch.dict(os.environ, {}, clear=True):
            status, reply = self.post(token=token)
        self.assertEqual(status, 503)
        self.assertEqual(reply["error"], "OPENAI_API_KEY_NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
