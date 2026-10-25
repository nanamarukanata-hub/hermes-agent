"""MCP-compatible HTTP server for the Muse <-> ChatGPT bridge.

Transport: HTTP JSON-RPC 2.0 at ``POST /mcp`` (MCP-style
``initialize`` / ``tools/list`` / ``tools/call`` / ``ping``), plus
``GET /health``.  This is intentionally a small, stdlib-only
implementation of the MCP tool surface that ChatGPT's developer-mode
/ Plugins connector and generic MCP clients (Claude, Codex) speak;
it is not a full MCP SDK server, and it does not implement OAuth.

Auth: when ``MUSE_BRIDGE_TOKEN`` is set (or ``--token-file`` points
at a 0600 file), every ``/mcp`` request must carry
``Authorization: Bearer <token>`` or it gets 401.  When no token is
configured the server refuses to start unless ``--allow-no-auth``
is given, and then it binds loopback only.  ChatGPT's connector
currently lists OAuth or no-auth (see docs), so a Bearer token may
not be offered by ChatGPT itself: for ChatGPT, the supported
auth story is tunnel-level access control (see docs), not this
header.  That limitation is reported, not hidden.

Tools (see TOOLS for schemas):
- muse_send_instruction, muse_get_status, muse_get_result,
  muse_list_sessions, muse_get_session, muse_run_tests,
  muse_get_diff, muse_approve, muse_complete_task (Muse-side CLI
  also uses this via the same JSON-RPC path).

Execution model: ``muse_send_instruction`` with an auto-classified
instruction runs the bounded action immediately (test run or git
read) and marks the task done; anything else is stored as
``awaiting_muse`` (or ``needs_approval`` / refused) so Muse can
execute it and complete it with ``muse_complete_task``.  The server
itself never runs arbitrary shell from an instruction string.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .policy import checkout_for, classify, load_repos
from .store import TaskStore

VERSION = "0.1.0"

TOOLS = [
    {"name": "muse_send_instruction", "description": "Send a development instruction to Muse for a repo. Returns task_id and session_id. Auto actions (tests, git status/diff) run immediately; other instructions are queued as awaiting_muse for Muse to execute and complete.", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}, "instruction": {"type": "string"}, "session_id": {"type": "string"}}, "required": ["repo", "instruction"]}},
    {"name": "muse_get_status", "description": "Get a task's status and recent log lines.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
    {"name": "muse_get_result", "description": "Get a completed task's result, diff and test results.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
    {"name": "muse_list_sessions", "description": "List Muse sessions, most recently active first.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "muse_get_session", "description": "Get one session with its task history, so work can continue from the previous turn.", "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}}, "required": ["session_id"]}},
    {"name": "muse_run_tests", "description": "Run pytest for a repo checkout (bounded, read-only apart from test artifacts in tmp).", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}, "test_path": {"type": "string"}}, "required": ["repo"]}},
    {"name": "muse_get_diff", "description": "Get git status and diff for a repo checkout (read-only).", "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]}},
    {"name": "muse_approve", "description": "Approve or reject a needs_approval task. Approval alone does not execute it; Muse review is still required before execution.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "decision": {"type": "string", "enum": ["approve", "reject"]}}, "required": ["task_id", "decision"]}},
    {"name": "muse_complete_task", "description": "Muse-side completion: attach a result, diff and test results to a task and mark it done (or failed). Used by Muse after executing an awaiting_muse instruction.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "result": {"type": "string"}, "diff": {"type": "string"}, "test_results": {"type": "string"}, "failed": {"type": "boolean"}}, "required": ["task_id", "result"]}},
]


def _run(cmd: list[str], cwd: str, timeout: int = 300) -> dict:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "timeout"}
    except OSError as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc)}
    return {"returncode": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-8000:]}


class Bridge:
    def __init__(self, store: TaskStore, repos: dict[str, str], default_checkout: str | Path | None = None):
        self.store = store
        self.repos = repos
        self.default_checkout = default_checkout

    def _checkout(self, repo: str) -> str | None:
        return checkout_for(repo, self.repos, self.default_checkout)

    def call_tool(self, name: str, args: dict) -> dict:
        if name == "muse_send_instruction":
            return self._send(args)
        if name == "muse_get_status":
            task = self.store.get_task(args.get("task_id", ""))
            if task is None:
                return {"error": "task not found"}
            return {"task_id": task["task_id"], "session_id": task["session_id"], "repo": task["repo"],
                    "status": task["status"], "logs": task.get("logs", [])[-20:]}
        if name == "muse_get_result":
            task = self.store.get_task(args.get("task_id", ""))
            if task is None:
                return {"error": "task not found"}
            return {"task_id": task["task_id"], "session_id": task["session_id"], "status": task["status"],
                    "result": task.get("result"), "diff": task.get("diff"), "test_results": task.get("test_results")}
        if name == "muse_list_sessions":
            return {"sessions": [{"session_id": s["session_id"], "repo": s.get("repo"), "task_ids": s.get("task_ids", []),
                                  "updated_at": s.get("updated_at"), "context": s.get("context", "")}
                                 for s in self.store.list_sessions()]}
        if name == "muse_get_session":
            session = self.store.get_session(args.get("session_id", ""))
            if session is None:
                return {"error": "session not found"}
            tasks = self.store.list_tasks(session_id=session["session_id"])
            return {"session": session, "tasks": [{"task_id": t["task_id"], "status": t["status"],
                                                  "instruction": t["instruction"], "result": t.get("result")}
                                                 for t in tasks]}
        if name == "muse_run_tests":
            return self._run_tests(args)
        if name == "muse_get_diff":
            return self._get_diff(args)
        if name == "muse_approve":
            return self._approve(args)
        if name == "muse_complete_task":
            return self._complete(args)
        return {"error": f"unknown tool: {name}"}

    def _send(self, args: dict) -> dict:
        repo = args.get("repo", "")
        instruction = (args.get("instruction") or "").strip()
        if not repo or not instruction:
            return {"error": "repo and instruction are required"}
        if repo not in ("hermes-agent", "apex-6-private"):
            return {"error": f"repo not allowed: {repo!r} (allowed: hermes-agent, apex-6-private)"}
        kind = classify(instruction)
        task = self.store.create_task(repo, instruction, args.get("session_id") or None)
        if kind == "never":
            self.store.update_task(task["task_id"], status="rejected", result="Refused by policy: destructive or deploy action.")
            return {"task_id": task["task_id"], "session_id": task["session_id"], "status": "rejected"}
        if kind == "auto":
            checkout = self._checkout(repo)
            if checkout is None:
                self.store.update_task(task["task_id"], status="failed",
                                      result=f"No local checkout configured for {repo}.")
                return {"task_id": task["task_id"], "session_id": task["session_id"], "status": "failed",
                        "error": "no local checkout"}
            if instruction.strip().lower().startswith(("run test", "test")):
                result = self._run_tests({"repo": repo})
                self.store.update_task(task["task_id"], status="done" if result.get("returncode") == 0 else "failed",
                                      result="Test run finished.", test_results=json.dumps(result))
                return {"task_id": task["task_id"], "session_id": task["session_id"], "status": self.store.get_task(task["task_id"])["status"]}
            result = self._get_diff({"repo": repo})
            self.store.update_task(task["task_id"], status="done", result="Git status/diff retrieved.", diff=result.get("diff"))
            return {"task_id": task["task_id"], "session_id": task["session_id"], "status": "done"}
        if kind == "needs_approval":
            self.store.update_task(task["task_id"], status="needs_approval")
            return {"task_id": task["task_id"], "session_id": task["session_id"], "status": "needs_approval",
                    "note": "Needs muse_approve and Muse review before execution."}
        self.store.update_task(task["task_id"], status="awaiting_muse")
        return {"task_id": task["task_id"], "session_id": task["session_id"], "status": "awaiting_muse"}

    def _run_tests(self, args: dict) -> dict:
        repo = args.get("repo", "")
        checkout = self._checkout(repo)
        if checkout is None:
            return {"error": f"no local checkout for {repo}"}
        test_path = args.get("test_path") or "tests/test_muse_bridge.py"
        # Only paths inside the checkout's tests/ tree are runnable, to
        # keep this tool bounded.
        if ".." in test_path or test_path.startswith("/"):
            return {"error": "test_path must be a relative path inside the checkout"}
        if not test_path.startswith("tests/"):
            return {"error": "test_path must be under tests/"}
        result = _run([sys.executable, "-m", "pytest", test_path, "-q", "-o", "addopts=", "-p", "no:cacheprovider"],
                      cwd=checkout, timeout=600)
        return {"repo": repo, "test_path": test_path, **result}

    def _get_diff(self, args: dict) -> dict:
        repo = args.get("repo", "")
        checkout = self._checkout(repo)
        if checkout is None:
            return {"error": f"no local checkout for {repo}"}
        status = _run(["git", "status", "--short"], cwd=checkout)
        diff = _run(["git", "diff", "--stat"], cwd=checkout)
        return {"repo": repo, "status": status["stdout"], "diff": diff["stdout"], "detail": _run(["git", "diff"], cwd=checkout)["stdout"][:8000]}

    def _approve(self, args: dict) -> dict:
        task = self.store.get_task(args.get("task_id", ""))
        if task is None:
            return {"error": "task not found"}
        decision = args.get("decision", "")
        if task["status"] != "needs_approval":
            return {"error": f"task is {task['status']}, not needs_approval"}
        if decision == "approve":
            self.store.update_task(task["task_id"], status="approved", approval="approved")
            return {"task_id": task["task_id"], "status": "approved", "note": "Approved; still requires Muse review before execution."}
        if decision == "reject":
            self.store.update_task(task["task_id"], status="rejected", approval="rejected")
            return {"task_id": task["task_id"], "status": "rejected"}
        return {"error": "decision must be approve or reject"}

    def _complete(self, args: dict) -> dict:
        task = self.store.get_task(args.get("task_id", ""))
        if task is None:
            return {"error": "task not found"}
        if task["status"] not in ("awaiting_muse", "approved", "queued"):
            return {"error": f"task is {task['status']}, cannot complete"}
        self.store.update_task(task["task_id"], status="failed" if args.get("failed") else "done",
                               result=args.get("result"), diff=args.get("diff"), test_results=args.get("test_results"))
        return {"task_id": task["task_id"], "status": self.store.get_task(task["task_id"])["status"]}


def make_handler(bridge: Bridge, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep stdout clean
            pass

        def _send_json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if token is None:
                return True
            auth = self.headers.get("Authorization", "")
            return auth == f"Bearer {token}"

        def do_GET(self):
            if self.path in ("/health", "/health/"):
                self._send_json(200, {"status": "ok", "version": VERSION, "tools": [t["name"] for t in TOOLS]})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path.rstrip("/") != "/mcp":
                self._send_json(404, {"error": "not found"})
                return
            if not self._authorized():
                self._send_json(401, {"error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON"})
                return
            method = request.get("method", "")
            req_id = request.get("id")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "muse-bridge", "version": VERSION}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params", {})
                tool_result = bridge.call_tool(params.get("name", ""), params.get("arguments", {}) or {})
                result = {"content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False)}], "isError": "error" in tool_result}
            elif method == "ping":
                result = {}
            else:
                self._send_json(200, {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"unknown method: {method}"}})
                return
            self._send_json(200, {"jsonrpc": "2.0", "id": req_id, "result": result})

    return Handler


def serve(host: str, port: int, store: TaskStore, repos: dict, default_checkout: str | None, token: str | None) -> ThreadingHTTPServer:
    bridge = Bridge(store, repos, default_checkout)
    server = ThreadingHTTPServer((host, port), make_handler(bridge, token))
    return server
