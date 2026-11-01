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


@pytest.mark.parametrize("invalid_record", [
    None,
    {"session_key": "removed", "session_id": "old", "platform": "uninstalled-platform", "chat_type": "dm"},
])
def test_partial_index_load_does_not_leave_recovered_entries(tmp_path, invalid_record):
    store = _store(tmp_path)
    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    original = store.get_or_create_session(source)
    index = store.sessions_dir / "sessions.json"
    data = json.loads(index.read_text(encoding="utf-8"))
    assert original.session_key in data
    data["malformed"] = invalid_record
    damaged = json.dumps(data).encode("utf-8")
    index.write_bytes(damaged)

    restarted = _store(tmp_path)
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        restarted._ensure_loaded()

    assert restarted._entries == {}
    assert restarted._loaded is False
    assert index.read_bytes() == damaged


def test_symlinked_vault_root_is_rejected_before_initialization(tmp_path):
    real_vault = tmp_path / "real-vault"
    real_vault.mkdir()
    alias = tmp_path / "vault-alias"
    alias.symlink_to(real_vault, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        SessionStore(
            sessions_dir=tmp_path / "unused-legacy-sessions",
            config=GatewayConfig(),
            vault_path=alias,
        )
    assert not (real_vault / "06_Automation").exists()


def test_symlinked_gateway_routing_directory_cannot_escape_vault(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    store.sessions_dir.parent.mkdir(parents=True, exist_ok=True)
    store.sessions_dir.symlink_to(outside, target_is_directory=True)

    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    with pytest.raises(RuntimeError, match="symlink"):
        store.get_or_create_session(source)

    assert not (outside / "sessions.json").exists()
    assert store._loaded is False


def test_symlinked_routing_index_is_never_read_or_overwritten(tmp_path):
    store = _store(tmp_path)
    outside = tmp_path / "outside-index.json"
    payload = b'{"sensitive": "outside-vault"}'
    outside.write_bytes(payload)
    index = store.sessions_dir / "sessions.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.symlink_to(outside)

    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    with pytest.raises(RuntimeError, match="symlink"):
        store.get_or_create_session(source)

    assert outside.read_bytes() == payload
    assert index.is_symlink()
    assert store._loaded is False


def test_symlink_swap_after_load_blocks_subsequent_index_save(tmp_path):
    store = _store(tmp_path)
    source = SessionSource(platform=Platform.DISCORD, chat_id="room", user_id="42")
    entry = store.get_or_create_session(source)
    routing_dir = store.sessions_dir
    original_dir = routing_dir.with_name("GatewayRouting-original")
    routing_dir.rename(original_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    routing_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symlink"):
        store.update_session(entry.session_key)

    assert not (outside / "sessions.json").exists()
    assert (original_dir / "sessions.json").exists()
