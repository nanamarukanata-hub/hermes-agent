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


def test_tool_annotations_and_instructions(tmp_path):
    server, _ = _server(tmp_path)
    try:
        port = server.server_address[1]
        import threading
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _, body = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, token="test-token")
        by_name = {t["name"]: t for t in body["result"]["tools"]}
        assert by_name["muse_get_result"]["annotations"]["readOnlyHint"] is True
        assert by_name["muse_get_diff"]["annotations"]["readOnlyHint"] is True
        assert by_name["muse_send_instruction"]["annotations"]["readOnlyHint"] is False
        assert by_name["muse_approve"]["annotations"]["readOnlyHint"] is False
        _, body = _http(port, {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}}, token="test-token")
        instructions = body["result"]["instructions"]
        assert len(instructions) <= 512
        assert "muse_send_instruction" in instructions and "muse_get_result" in instructions
    finally:
        server.shutdown()
        server.server_close()


def test_oauth_flow(tmp_path):
    import base64
    import hashlib
    import http.client
    import threading
    import urllib.parse

    from muse_bridge.oauth import OAuthServer

    store = TaskStore(tmp_path / "state")
    oauth = OAuthServer("http://127.0.0.1:0", "approval-key", tmp_path / "state")
    server = serve("127.0.0.1", 0, store, {}, None, None, oauth=oauth)
    try:
        port = server.server_address[1]
        oauth.public_url = f"http://127.0.0.1:{port}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=10) as resp:
                return resp.status, json.loads(resp.read())

        def post_json(path, payload):
            req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read() or b"{}")

        def post_form(path, fields):
            body = urllib.parse.urlencode(fields).encode()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            resp = conn.getresponse()
            raw = resp.read()
            headers = dict(resp.getheaders())
            conn.close()
            return resp.status, headers, raw

        _, meta = get("/.well-known/oauth-protected-resource/mcp")
        assert meta["resource"] == f"{base}/mcp" and meta["scopes_supported"] == ["mcp:tools"]
        _, as_meta = get("/.well-known/oauth-authorization-server")
        assert as_meta["registration_endpoint"] == f"{base}/register"
        assert "S256" in as_meta["code_challenge_methods_supported"]

        # Unauthenticated /mcp must challenge with protected-resource metadata.
        status, _ = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert status == 401

        status, reg = post_json("/register", {"redirect_uris": ["https://client.example/callback"]})
        assert status == 201 and reg["client_id"]
        client_id = reg["client_id"]

        verifier = "test-verifier-0123456789abcdef"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        auth_params = {"response_type": "code", "client_id": client_id,
                       "redirect_uri": "https://client.example/callback",
                       "code_challenge": challenge, "code_challenge_method": "S256",
                       "scope": "mcp:tools", "state": "s1"}
        # Wrong approval key must not issue a code.
        status, _, _ = post_form("/authorize", {**auth_params, "approval_key": "wrong"})
        assert status == 400
        status, headers, _ = post_form("/authorize", {**auth_params, "approval_key": "approval-key"})
        assert status == 302
        location = headers["Location"]
        assert location.startswith("https://client.example/callback?code=")
        code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["code"][0]

        # Wrong PKCE verifier must not exchange.
        status, _, _ = post_form("/token", {"grant_type": "authorization_code", "code": code,
                                            "redirect_uri": "https://client.example/callback",
                                            "code_verifier": "wrong-verifier", "client_id": client_id})
        assert status == 400
        status, _, raw = post_form("/token", {"grant_type": "authorization_code", "code": code,
                                              "redirect_uri": "https://client.example/callback",
                                              "code_verifier": verifier, "client_id": client_id})
        assert status == 200
        pair = json.loads(raw)
        assert pair["scope"] == "mcp:tools" and pair["refresh_token"]

        status, _ = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, token=pair["access_token"])
        assert status == 200

        # Refresh rotates: the new pair works, the old refresh and access die.
        status, _, raw = post_form("/token", {"grant_type": "refresh_token",
                                              "refresh_token": pair["refresh_token"], "client_id": client_id})
        assert status == 200
        pair2 = json.loads(raw)
        status, _, _ = post_form("/token", {"grant_type": "refresh_token",
                                            "refresh_token": pair["refresh_token"], "client_id": client_id})
        assert status == 400
        status, _ = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, token=pair["access_token"])
        assert status == 401
        status, _ = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, token=pair2["access_token"])
        assert status == 200

        # Revocation ends the rotated access token.
        status, _, _ = post_form("/revoke", {"token": pair2["access_token"]})
        assert status == 200
        status, _ = _http(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, token=pair2["access_token"])
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()
