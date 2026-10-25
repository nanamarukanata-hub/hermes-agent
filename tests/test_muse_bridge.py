"""Tests for muse_bridge (Muse <-> ChatGPT bridge).

No network, no model, no token, no real repo data: the server runs
on a loopback ephemeral port with a temporary state directory, and
the only checkout it touches is a temporary git repo created here.
"""

import json
import subprocess
import urllib.request
import urllib.error

from muse_bridge.policy import classify
from muse_bridge.server import Bridge, serve
from muse_bridge.store import TaskStore


def _bridge(tmp_path):
    store = TaskStore(tmp_path / "state")
    return Bridge(store, {}, default_checkout=None), store


def _http(port, payload, token=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _server(tmp_path, token="test-token"):
    store = TaskStore(tmp_path / "state")
    server = serve("127.0.0.1", 0, store, {}, None, token)
    return server, store


def test_classify_policy():
    assert classify("run tests for hermes-agent") == "auto"
    assert classify("git status") == "auto"
    assert classify("Implement a new feature in the gateway") == "needs_approval"
    assert classify("deploy to production now") == "never"
    assert classify("rm -rf the repo") == "never"


def test_send_awaiting_and_complete(tmp_path):
    bridge, store = _bridge(tmp_path)
    sent = bridge.call_tool("muse_send_instruction", {"repo": "hermes-agent", "instruction": "Implement a new feature in the gateway"})
    assert sent["status"] in ("awaiting_muse", "needs_approval")
    # needs_approval path: approve, then Muse completes it.
    if sent["status"] == "needs_approval":
        approved = bridge.call_tool("muse_approve", {"task_id": sent["task_id"], "decision": "approve"})
        assert approved["status"] == "approved"
    done = bridge.call_tool("muse_complete_task", {"task_id": sent["task_id"], "result": "Implemented and tested locally."})
    assert done["status"] == "done"
    result = bridge.call_tool("muse_get_result", {"task_id": sent["task_id"]})
    assert result["result"] == "Implemented and tested locally."
    session = bridge.call_tool("muse_get_session", {"session_id": sent["session_id"]})
    assert session["tasks"][0]["task_id"] == sent["task_id"]


def test_send_refuses_disallowed_repo_and_destructive(tmp_path):
    bridge, _ = _bridge(tmp_path)
    assert "error" in bridge.call_tool("muse_send_instruction", {"repo": "random-repo", "instruction": "hi"})
    rejected = bridge.call_tool("muse_send_instruction", {"repo": "hermes-agent", "instruction": "deploy to production now"})
    assert rejected["status"] == "rejected"


def test_session_continuation(tmp_path):
    bridge, _ = _bridge(tmp_path)
    first = bridge.call_tool("muse_send_instruction", {"repo": "hermes-agent", "instruction": "First step"})
    second = bridge.call_tool("muse_send_instruction", {"repo": "hermes-agent", "instruction": "Second step", "session_id": first["session_id"]})
    assert second["session_id"] == first["session_id"]
    sessions = bridge.call_tool("muse_list_sessions", {})
    assert sessions["sessions"][0]["session_id"] == first["session_id"]
    assert len(sessions["sessions"][0]["task_ids"]) == 2


def test_http_auth_and_tools(tmp_path):
    server, _ = _server(tmp_path)
    try:
        port = server.server_address[1]
        import threading
        threading.Thread(target=server.serve_forever, daemon=True).start()
        status, _ = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert status == 401
        status, body = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, token="test-token")
        assert status == 200
        names = [t["name"] for t in body["result"]["tools"]]
        assert "muse_send_instruction" in names and "muse_get_result" in names
        status, body = _http(port, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                    "params": {"name": "muse_send_instruction",
                                               "arguments": {"repo": "hermes-agent", "instruction": "Check the gateway"}}},
                             token="test-token")
        assert status == 200
        sent = json.loads(body["result"]["content"][0]["text"])
        assert sent["session_id"]
    finally:
        server.shutdown()
        server.server_close()


def test_run_tests_and_diff_in_tmp_checkout(tmp_path):
    checkout = tmp_path / "repo"
    (checkout / "tests").mkdir(parents=True)
    (checkout / "tests" / "test_dummy.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=checkout, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=checkout, check=True)
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=checkout, check=True)
    store = TaskStore(tmp_path / "state")
    bridge = Bridge(store, {"hermes-agent": str(checkout)}, default_checkout=None)
    result = bridge.call_tool("muse_run_tests", {"repo": "hermes-agent", "test_path": "tests/test_dummy.py"})
    assert result["returncode"] == 0
    diff = bridge.call_tool("muse_get_diff", {"repo": "hermes-agent"})
    # A test run may leave __pycache__ behind, so only the shape is asserted.
    assert "status" in diff and "diff" in diff
    # No checkout for apex-6-private on this machine: honest error, not a crash.
    assert "error" in bridge.call_tool("muse_run_tests", {"repo": "apex-6-private"})
    assert "error" in bridge.call_tool("muse_get_diff", {"repo": "apex-6-private"})
