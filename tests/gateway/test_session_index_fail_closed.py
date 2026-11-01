"""A damaged Obsidian gateway routing index must never be silently replaced."""

import json

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore


def _store(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return SessionStore(
        sessions_dir=tmp_path / "unused-legacy-sessions",
        config=GatewayConfig(),
        vault_path=vault,
    )


@pytest.mark.parametrize("payload", [
    b'{"broken": ',
    b'[]',
    b'{"bad-entry": null}',
    b'\xff\xfe',
])
def test_corrupt_vault_routing_index_fails_closed_without_overwrite(tmp_path, payload):
    store = _store(tmp_path)
    index = store.sessions_dir / "sessions.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_bytes(payload)

    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        store.get_or_create_session(source)

    assert index.read_bytes() == payload
    assert store._loaded is False
    assert store._entries == {}
    assert not (tmp_path / "unused-legacy-sessions").exists()


def test_partial_index_load_does_not_leave_recovered_entries(tmp_path):
    store = _store(tmp_path)
    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    original = store.get_or_create_session(source)
    index = store.sessions_dir / "sessions.json"
    data = json.loads(index.read_text(encoding="utf-8"))
    assert original.session_key in data
    data["malformed"] = None
    damaged = json.dumps(data).encode("utf-8")
    index.write_bytes(damaged)

    restarted = _store(tmp_path)
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        restarted._ensure_loaded()

    assert restarted._entries == {}
    assert restarted._loaded is False
    assert index.read_bytes() == damaged
