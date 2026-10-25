"""CLI for the Muse <-> ChatGPT bridge.

Usage:
    python3 -m muse_bridge.cli serve [--host 127.0.0.1] [--port 8765]
        [--state DIR] [--repos repos.yaml] [--token-file FILE] [--allow-no-auth]
        [--public-url URL] [--oauth-approval-file FILE]
    python3 -m muse_bridge.cli create-token [--state DIR]
        # prints a token to stdout; store it outside the repo, never commit it
    python3 -m muse_bridge.cli create-approval-key [--state DIR]
        # prints an OAuth approval key to stdout; store it outside the repo
    python3 -m muse_bridge.cli status --state DIR --task TASK_ID
    python3 -m muse_bridge.cli complete --state DIR --task TASK_ID --result TEXT
        [--diff TEXT] [--test-results TEXT] [--failed]

The server binds loopback by default.  Binding a public address is
deliberately not a one-flag convenience: it requires a TLS-terminating
relay with its own authentication (see docs/MUSE-CHATGPT-BRIDGE.md),
which this package does not provision.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

from .server import VERSION, serve
from .store import TaskStore

DEFAULT_PORT = 8765


def _state_dir(args) -> Path:
    if args.state:
        return Path(args.state).expanduser()
    return Path(os.environ.get("MUSE_BRIDGE_STATE", Path.cwd() / "muse_bridge_state")).expanduser()


def _token(args) -> str | None:
    if os.environ.get("MUSE_BRIDGE_TOKEN"):
        return os.environ["MUSE_BRIDGE_TOKEN"]
    if getattr(args, "token_file", None):
        path = Path(args.token_file).expanduser()
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muse_bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_p = sub.add_parser("serve")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_p.add_argument("--state", default=None)
    serve_p.add_argument("--repos", default=None)
    serve_p.add_argument("--token-file", default=None)
    serve_p.add_argument("--allow-no-auth", action="store_true")
    serve_p.add_argument("--public-url", default=None)
    serve_p.add_argument("--oauth-approval-file", default=None)
    token_p = sub.add_parser("create-token")
    token_p.add_argument("--state", default=None)
    approval_p = sub.add_parser("create-approval-key")
    approval_p.add_argument("--state", default=None)
    status_p = sub.add_parser("status")
    status_p.add_argument("--state", default=None)
    status_p.add_argument("--task", required=True)
    complete_p = sub.add_parser("complete")
    complete_p.add_argument("--state", default=None)
    complete_p.add_argument("--task", required=True)
    complete_p.add_argument("--result", required=True)
    complete_p.add_argument("--diff", default=None)
    complete_p.add_argument("--test-results", default=None)
    complete_p.add_argument("--failed", action="store_true")
    args = parser.parse_args(argv)

    if args.command in ("create-token", "create-approval-key"):
        value = secrets.token_urlsafe(32)
        state = _state_dir(args)
        state.mkdir(parents=True, exist_ok=True)
        path = state / ("oauth_approval" if args.command == "create-approval-key" else "token")
        path.write_text(value + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        print(value)
        print(f"(also saved to {path} with mode 0600; do not commit it)", file=sys.stderr)
        return 0

    store = TaskStore(_state_dir(args))
    if args.command == "status":
        task = store.get_task(args.task)
        if task is None:
            print("task not found", file=sys.stderr)
            return 2
        print(f"{task['task_id']} {task['status']} session={task['session_id']} repo={task['repo']}")
        print(task["instruction"])
        return 0
    if args.command == "complete":
        task = store.update_task(args.task, status="failed" if args.failed else "done",
                                 result=args.result, diff=args.diff, test_results=args.test_results)
        if task is None:
            print("task not found", file=sys.stderr)
            return 2
        print(f"completed {args.task} as {task['status']}")
        return 0

    # serve
    from .oauth import OAuthServer
    from .policy import load_repos

    token = _token(args)
    approval_key = os.environ.get("MUSE_BRIDGE_OAUTH_APPROVAL_KEY")
    if approval_key is None and getattr(args, "oauth_approval_file", None):
        path = Path(args.oauth_approval_file).expanduser()
        if path.exists():
            approval_key = path.read_text(encoding="utf-8").strip()
    oauth = None
    if getattr(args, "public_url", None):
        if not approval_key:
            print("error: --public-url requires an OAuth approval key: set MUSE_BRIDGE_OAUTH_APPROVAL_KEY or pass --oauth-approval-file.", file=sys.stderr)
            return 2
        oauth = OAuthServer(args.public_url, approval_key, _state_dir(args))
    if token is None and oauth is None and not args.allow_no_auth:
        print("error: no token configured. Set MUSE_BRIDGE_TOKEN, pass --token-file, or pass --allow-no-auth (loopback only).", file=sys.stderr)
        return 2
    if args.host not in ("127.0.0.1", "localhost", "::1") and token is None and oauth is None:
        print("error: refusing to bind a non-loopback address without a token or OAuth.", file=sys.stderr)
        return 2
    repos = load_repos(args.repos)
    server = serve(args.host, args.port, store, repos, default_checkout=Path.cwd(), token=token, oauth=oauth)
    print(f"muse-bridge {VERSION} listening on http://{args.host}:{args.port}/mcp (health: /health, oauth: {'on' if oauth else 'off'})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
