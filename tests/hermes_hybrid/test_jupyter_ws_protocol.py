"""Jupyter WebSocket protocol framing tests, no network or credentials."""
from __future__ import annotations

import struct

import pytest

from hermes_hybrid.jupyter_ws_protocol import (
    V1, decode, decode_default, decode_v1,
    encode, encode_default, encode_v1, kernel_request,
)


@pytest.mark.parametrize("protocol", [None, V1])
def test_kernel_info_roundtrip(protocol):
    request = kernel_request("kernel_info_request", {}, session="test")
    payload = encode(request, protocol)
    assert isinstance(payload, bytes)
    result = decode(payload, protocol)
    assert result["header"] == request["header"]
    assert result["channel"] == "shell"
    assert result["content"] == {}


@pytest.mark.parametrize("protocol", [None, V1])
def test_execute_request_roundtrip(protocol):
    request = kernel_request("execute_request", {"code": "print('hello')"},
                             session="testsession")
    output = decode(encode(request, protocol), protocol)
    assert output["header"]["msg_type"] == "execute_request"
    assert output["content"]["code"] == "print('hello')"
    assert output["content"]["silent"] is False
    assert output["content"]["allow_stdin"] is False


def test_v1_wire_prefix_and_binary_buffers():
    message = kernel_request("kernel_info_request")
    message["buffers"] = [b"\x00\xff"]
    payload = encode_v1(message)
    assert struct.unpack_from("<Q", payload)[0] == 7  # 5 JSON/channel parts + buffer + final offset
    offsets = struct.unpack_from("<" + "Q" * 7, payload, 8)
    assert offsets[0] == 64
    assert offsets[-1] == len(payload)
    assert decode_v1(payload)["buffers"] == [b"\x00\xff"]


def test_classic_wire_prefix_and_binary_buffers():
    message = kernel_request("kernel_info_request")
    message["buffers"] = [b"\x00\xff"]
    payload = encode_default(message)
    assert struct.unpack_from(">I", payload)[0] == 2
    assert struct.unpack_from(">I", payload, 4)[0] == 12
    assert decode_default(payload)["buffers"] == [b"\x00\xff"]


@pytest.mark.parametrize("payload", [
    b"", b"\x00" * 20,
    struct.pack("<Q", 9999) + b"\x00" * 48,
    struct.pack("<Q", 6) + b"\x00" * 48,
])
def test_corrupt_v1_rejected(payload):
    with pytest.raises(ValueError):
        decode_v1(payload)


def test_wrong_wire_type_and_wrong_payload_rejected():
    request = kernel_request("kernel_info_request")
    with pytest.raises(ValueError):
        decode(encode_default(request), V1)
    with pytest.raises(ValueError):
        decode(encode_v1(request), None)
    with pytest.raises(ValueError):
        kernel_request("execute_request", {"code": None})
    with pytest.raises(ValueError):
        kernel_request("shutdown_request", {})


def test_legacy_json_message_decode():
    data = '{"channel":"shell","content":{"ok":true}}'
    assert decode_default(data)["content"]["ok"] is True
