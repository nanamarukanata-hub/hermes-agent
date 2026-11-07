#!/usr/bin/env python3
"""Owner-operated Jupyter kernel WS probe; never prints auth headers or URL.

Requires websocket-client. This does not allocate a Colab runtime or obtain
browser credentials. Provide a URL and any authorized proxy token through
the caller's environment, not on the command line or in Git.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys
import time

from hermes_hybrid.jupyter_ws_protocol import (
    V1, decode, encode, kernel_request,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Probe an existing Colab Jupyter kernel")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--execute-file", type=Path, help="Run explicit local Python file instead of a read-only kernel-info probe")
    choice.add_argument("--lora-job-id", help="Execute the generated SDXL LoRA training cell for an existing Drive job")
    parser.add_argument("--timeout", type=int, default=30, help="Seconds to wait for the initial reply")
    args = parser.parse_args(argv)
    if not 3 <= args.timeout <= 600:
        parser.error("--timeout must be 3..600")
    url = os.environ.get("HERMES_KERNEL_WS_URL", "").strip()
    if not url.startswith(("wss://", "ws://")):
        parser.error("HERMES_KERNEL_WS_URL must contain the authorized kernel WebSocket URL")
    if url.startswith("ws://") and not any(host in url.lower() for host in
                                           ("localhost", "127.0.0.1", "[::1]")):
        parser.error("unencrypted WS is only allowed for a localhost tunnel")
    requested = os.environ.get("HERMES_KERNEL_SUBPROTOCOL", "").strip()
    if requested not in ("", V1):
        parser.error("subprotocol must be blank or v1.kernel.websocket.jupyter.org")
    origin = os.environ.get("HERMES_KERNEL_ORIGIN", "").strip() or None
    if args.lora_job_id:
        from hermes_hybrid.vault_kv import validate_id
        from hermes_hybrid.lora_training import build_notebook

        job_id = validate_id(args.lora_job_id)
        notebook = build_notebook(job_id)
        source = "".join(notebook["cells"][2]["source"])
        msg_type, content = "execute_request", {"code": source}
        print("Executing validated Hermes LoRA job:", job_id)
    elif args.execute_file:
        if not args.execute_file.is_file() or args.execute_file.stat().st_size > 1_048_576:
            parser.error("--execute-file must be an existing Python source file <=1 MiB")
        source = args.execute_file.read_text(encoding="utf-8")
        msg_type, content = "execute_request", {"code": source}
        print("Attempting explicit kernel execution using the negotiated Jupyter envelope.")
    else:
        msg_type, content = "kernel_info_request", {}
        print("Testing kernel_info_request before any code execution.")
    try:
        import websocket
    except ImportError:
        parser.error("Install websocket-client in the calling environment")
    headers = []
    token = os.environ.get("HERMES_KERNEL_PROXY_TOKEN")
    cookie = os.environ.get("HERMES_KERNEL_COOKIE")
    if token:
        headers.append("X-Colab-Runtime-Proxy-Token: " + token)
    if cookie:
        headers.append("Cookie: " + cookie)
    ws = None
    try:
        ws = websocket.create_connection(
            url, timeout=args.timeout, header=headers, origin=origin,
            subprotocols=([requested] if requested else []),
        )
        selected = ws.getsubprotocol()
        if selected not in (None, "", V1):
            raise RuntimeError("unexpected server-negotiated Jupyter subprotocol")
        if requested and selected != requested:
            print("Server did not negotiate requested subprotocol; using classic framing.")
        protocol = selected or None
        request = kernel_request(msg_type, content)
        message_id = request["header"]["msg_id"]
        ws.send(encode(request, protocol), opcode=websocket.ABNF.OPCODE_BINARY)
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline or args.execute_file is not None or args.lora_job_id is not None:
            try:
                response = decode(ws.recv(), protocol)
            except socket.timeout:
                if args.execute_file is None and args.lora_job_id is None:
                    break
                continue
            if response.get("parent_header", {}).get("msg_id") != message_id:
                continue
            reply_type = response.get("header", {}).get("msg_type")
            body = response.get("content", {})
            if reply_type == "stream":
                print(str(body.get("text", "")), end="", flush=True)
            elif reply_type == "error":
                print("Kernel error:", body.get("ename", "error"),
                      str(body.get("evalue", ""))[:250])
            elif reply_type == ("kernel_info_reply" if msg_type == "kernel_info_request"
                                else "execute_reply"):
                ok = body.get("status", "ok") == "ok"
                print("Kernel replied:", reply_type, "status:", body.get("status", "ok"))
                return 0 if ok else 1
        print("No matching reply; connected WebSocket does not yet prove kernel execution.")
        return 2
    except websocket.WebSocketException as exc:
        print("WebSocket disconnected:", type(exc).__name__, file=sys.stderr)
        return 3
    finally:
        if ws is not None:
            ws.close()


if __name__ == "__main__":
    raise SystemExit(main())
