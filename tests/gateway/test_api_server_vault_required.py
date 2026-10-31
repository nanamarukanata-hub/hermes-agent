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
