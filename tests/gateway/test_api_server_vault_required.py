"""Regression tests for Vault-only API response persistence."""

from pathlib import Path

import pytest

from gateway.platforms.api_server import ResponseStore


def test_missing_vault_rejects_implicit_hermes_home(monkeypatch, tmp_path):
    """No Vault means no durable response writes under HERMES_HOME."""
    home = tmp_path / "home"
    monkeypatch.delenv("HERMES_OBSIDIAN_VAULT", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(home))

    with pytest.raises(RuntimeError, match="HERMES_OBSIDIAN_VAULT"):
        ResponseStore()

    assert not home.exists()


def test_explicit_db_path_outside_vault_is_rejected(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    outside = tmp_path / "outside" / "responses.db"
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault))

    with pytest.raises(ValueError, match="inside HERMES_OBSIDIAN_VAULT"):
        ResponseStore(db_path=str(outside))

    assert not outside.parent.exists()


def test_configured_vault_persists_across_store_instances(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault))

    first = ResponseStore()
    first.put("resp_1", {"output": "persisted"})
    first.set_conversation("chat", "resp_1")
    first.close()

    second = ResponseStore()
    assert second.get("resp_1") == {"output": "persisted"}
    assert second.get_conversation("chat") == "resp_1"
    assert (vault / "06_Automation" / "Hermes" / "ApiResponses" / "resp_1.json").is_file()
    second.close()


def test_legacy_db_path_is_allowed_only_inside_vault(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault))
    store = ResponseStore(db_path=str(vault / "Legacy" / "responses.db"))
    store.put("resp_2", {"output": "inside"})
    assert (vault / "Legacy" / "responses" / "resp_2.json").is_file()
    store.close()


def test_explicit_memory_mode_works_without_vault(monkeypatch):
    monkeypatch.delenv("HERMES_OBSIDIAN_VAULT", raising=False)
    store = ResponseStore(db_path=":memory:")
    temporary_path = Path(store._temp_dir)
    try:
        store.put("resp_3", {"output": "ephemeral"})
        assert store.get("resp_3") == {"output": "ephemeral"}
    finally:
        store.close()
    assert not temporary_path.exists()


def test_conversation_updates_do_not_lose_concurrent_names(monkeypatch, tmp_path):
    """Independent writers must not overwrite one another's index entries."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(tmp_path / "vault"))
    store = ResponseStore()
    original_read = store._conversations

    def delayed_read():
        mapping = original_read()
        time.sleep(0.02)  # Widen the old unprotected read-modify-write race.
        return mapping

    monkeypatch.setattr(store, "_conversations", delayed_read)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(
            lambda i: store.set_conversation(f"chat-{i}", f"resp-{i}"),
            range(8),
        ))

    for i in range(8):
        assert store.get_conversation(f"chat-{i}") == f"resp-{i}"


def test_conversation_index_symlink_does_not_read_external_file(monkeypatch, tmp_path):
    """The index must not follow a symlink outside the configured Vault."""
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(tmp_path / "vault"))
    store = ResponseStore()
    outside = tmp_path / "external.json"
    outside.write_text('{"private": "external-response"}', encoding="utf-8")
    store._conversations_path.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        store.get_conversation("private")
    with pytest.raises(ValueError, match="symlink"):
        store.set_conversation("new", "resp_4")
    assert outside.read_text(encoding="utf-8") == '{"private": "external-response"}'


def test_invalid_conversation_index_is_not_overwritten(monkeypatch, tmp_path):
    """A malformed index is not silently replaced by an empty mapping."""
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(tmp_path / "vault"))
    store = ResponseStore()
    store._conversations_path.write_text('{"chat": ', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid vault record"):
        store.set_conversation("new", "resp_5")
    assert store._conversations_path.read_text(encoding="utf-8") == '{"chat": '


def test_predictable_temp_symlink_cannot_overwrite_external_file(monkeypatch, tmp_path):
    """A pre-planted legacy PID temp symlink must not redirect index writes."""
    import os

    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(tmp_path / "vault"))
    store = ResponseStore()
    outside = tmp_path / "external.txt"
    outside.write_text("untouched", encoding="utf-8")
    legacy_temp = store._conversations_path.with_name(
        f".conversations.json.tmp-{os.getpid()}"
    )
    legacy_temp.symlink_to(outside)

    store.set_conversation("chat", "resp_6")
    assert store.get_conversation("chat") == "resp_6"
    assert outside.read_text(encoding="utf-8") == "untouched"
    assert legacy_temp.is_symlink()
