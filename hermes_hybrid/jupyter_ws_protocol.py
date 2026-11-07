"""Strict Jupyter kernel WebSocket envelope codec (stdlib only).

Jupyter servers negotiate either the classic 32-bit big-endian binary
envelope or the v1.kernel.websocket.jupyter.org 64-bit little-endian
multipart envelope. A WebSocket handshake alone does not establish that
an execute_request uses the correct wire format.
"""
from __future__ import annotations

import json
import struct
import uuid
from datetime import datetime, timezone
from typing import Any

V1 = "v1.kernel.websocket.jupyter.org"
MAX_PARTS = 256
MAX_FRAME = 64 * 1024 * 1024


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def kernel_request(message_type: str, content: dict | None = None, *,
                   session: str | None = None, channel: str = "shell") -> dict:
    """Construct a complete Jupyter message; no transport/credentials here."""
    if message_type not in ("kernel_info_request", "execute_request"):
        raise ValueError("only harmless probe and explicit execute are supported")
    if channel != "shell":
        raise ValueError("requests must use the shell channel")
    content = {} if content is None else content
    if not isinstance(content, dict):
        raise ValueError("content must be an object")
    if message_type == "kernel_info_request" and content:
        raise ValueError("kernel_info_request has empty content")
    if message_type == "execute_request":
        code = content.get("code")
        if not isinstance(code, str) or len(code.encode("utf-8")) > 1024 * 1024:
            raise ValueError("execute_request code must be UTF-8 text <= 1 MiB")
        content = {
            "code": code, "silent": False, "store_history": True,
            "user_expressions": {}, "allow_stdin": False, "stop_on_error": True,
        }
    return {
        "channel": channel,
        "header": {
            "msg_id": uuid.uuid4().hex, "username": "hermes",
            "session": session or uuid.uuid4().hex,
            "date": datetime.now(timezone.utc).isoformat(),
            "msg_type": message_type, "version": "5.3",
        },
        "parent_header": {}, "metadata": {}, "content": content,
        "buffers": [],
    }


def encode_v1(message: dict) -> bytes:
    """Encode v1 multipart offsets as 64-bit little-endian integers."""
    pieces = [
        str(message["channel"]).encode("utf-8"),
        _json(message["header"]),
        _json(message.get("parent_header", {})),
        _json(message.get("metadata", {})),
        _json(message["content"]),
    ] + [bytes(b) for b in message.get("buffers", [])]
    count = len(pieces) + 1
    if count > MAX_PARTS:
        raise ValueError("too many Jupyter message buffers")
    start = 8 * (count + 1)
    offsets = [start]
    for part in pieces:
        offsets.append(offsets[-1] + len(part))
    if offsets[-1] > MAX_FRAME:
        raise ValueError("message exceeds maximum frame")
    return struct.pack("<Q", count) + struct.pack("<" + "Q" * count, *offsets) + b"".join(pieces)


def decode_v1(data: bytes) -> dict:
    if not isinstance(data, bytes) or len(data) < 56 or len(data) > MAX_FRAME:
        raise ValueError("invalid Jupyter v1 binary frame size")
    count = struct.unpack_from("<Q", data)[0]
    if not (6 <= count <= MAX_PARTS) or len(data) < 8 * (count + 1):
        raise ValueError("invalid Jupyter v1 offset count")
    offsets = struct.unpack_from("<" + "Q" * count, data, 8)
    if offsets[0] != 8 * (count + 1) or offsets[-1] != len(data):
        raise ValueError("invalid Jupyter v1 offsets")
    if any(a > b for a, b in zip(offsets, offsets[1:])):
        raise ValueError("out-of-order Jupyter v1 offsets")
    parts = [data[a:b] for a, b in zip(offsets, offsets[1:])]
    return {
        "channel": parts[0].decode("utf-8"),
        "header": json.loads(parts[1]), "parent_header": json.loads(parts[2]),
        "metadata": json.loads(parts[3]), "content": json.loads(parts[4]),
        "buffers": parts[5:],
    }


def encode_default(message: dict) -> bytes:
    """Encode the classic Jupyter 32-bit BE offset format."""
    payload = {key: message.get(key, {}) for key in
               ("channel", "header", "parent_header", "metadata", "content")}
    pieces = [_json(payload)] + [bytes(b) for b in message.get("buffers", [])]
    count = len(pieces)
    if count > MAX_PARTS:
        raise ValueError("too many Jupyter message buffers")
    first = 4 * (count + 1)
    offsets = [first]
    for part in pieces[:-1]:
        offsets.append(offsets[-1] + len(part))
    total_size = 4 * (count + 1) + sum(map(len, pieces))
    if total_size > MAX_FRAME:
        raise ValueError("message exceeds maximum frame")
    return struct.pack(">I", count) + struct.pack(">" + "I" * count, *offsets) + b"".join(pieces)


def decode_default(data: bytes | str) -> dict:
    if isinstance(data, str):
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("invalid JSON Jupyter message")
        return value
    if not isinstance(data, bytes) or len(data) < 8 or len(data) > MAX_FRAME:
        raise ValueError("invalid classic Jupyter binary frame")
    count = struct.unpack_from(">I", data)[0]
    if not (1 <= count <= MAX_PARTS) or len(data) < 4 * (count + 1):
        raise ValueError("invalid classic Jupyter offset count")
    offsets = struct.unpack_from(">" + "I" * count, data, 4)
    if offsets[0] != 4 * (count + 1) or any(a > b for a, b in zip(offsets, offsets[1:])):
        raise ValueError("invalid classic Jupyter offsets")
    offsets = offsets + (len(data),)
    msg = json.loads(data[offsets[0]:offsets[1]])
    if not isinstance(msg, dict):
        raise ValueError("classic Jupyter JSON must be an object")
    msg["buffers"] = [data[a:b] for a, b in zip(offsets[1:], offsets[2:])]
    return msg


def encode(message: dict, subprotocol: str | None) -> bytes | str:
    """Match JupyterLab: legacy bufferless messages are TEXT WebSocket frames.

    Default-protocol binary framing is reserved for messages with binary
    buffers; the negotiated v1 subprotocol is always binary.
    """
    if subprotocol == V1:
        return encode_v1(message)
    if not message.get("buffers"):
        return _json(message).decode("utf-8")
    return encode_default(message)


def decode(data: bytes | str, subprotocol: str | None) -> dict:
    if subprotocol == V1:
        if isinstance(data, str):
            raise ValueError("v1 Jupyter requires a binary WebSocket frame")
        return decode_v1(data)
    return decode_default(data)
