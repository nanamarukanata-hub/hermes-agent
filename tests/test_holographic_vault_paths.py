"""Regression tests: holographic facts must persist only inside the Obsidian Vault."""

import pytest

from plugins.memory.holographic import HolographicMemoryProvider
from plugins.memory.holographic.store import MemoryStore


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(root))
    return root


def test_provider_without_db_path_uses_vault(vault):
    provider = HolographicMemoryProvider(config={"default_trust": 0.5})
    provider.initialize("test-session")
    expected = vault / "06_Automation" / "Hermes" / "HolographicMemory"
    assert provider._store.db_path == expected
    fact_id = provider._store.add_fact("Vault-only test fact")
    assert (expected / "facts" / f"{fact_id}.json").is_file()
    provider.shutdown()


def test_store_without_vault_configuration_fails_before_writing(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_OBSIDIAN_VAULT", raising=False)
    with pytest.raises(RuntimeError, match="HERMES_OBSIDIAN_VAULT"):
        MemoryStore()
    assert not (tmp_path / "06_Automation").exists()


def test_explicit_path_outside_vault_is_rejected_without_writing(vault, tmp_path):
    outside = tmp_path / "outside" / "memory"
    with pytest.raises(ValueError, match="inside HERMES_OBSIDIAN_VAULT"):
        MemoryStore(db_path=outside)
    assert not outside.exists()
    assert not vault.exists()


def test_provider_rejects_explicit_path_outside_vault(vault, tmp_path):
    outside = tmp_path / "other-memory"
    provider = HolographicMemoryProvider(config={"db_path": str(outside)})
    with pytest.raises(ValueError, match="inside HERMES_OBSIDIAN_VAULT"):
        provider.initialize("test-session")
    assert not outside.exists()
    assert not vault.exists()


def test_legacy_sqlite_path_is_rejected(vault):
    with pytest.raises(ValueError, match="Legacy SQLite"):
        MemoryStore(db_path=vault / "memory_store.db")
    assert not vault.exists()


def test_symlinked_subdirectory_cannot_escape_vault(vault, tmp_path):
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="inside HERMES_OBSIDIAN_VAULT"):
        MemoryStore(db_path=vault / "linked" / "memory")
    assert not (outside / "memory").exists()


def test_explicit_directory_inside_vault_is_allowed(vault):
    root = vault / "06_Automation" / "Hermes" / "CustomFacts"
    store = MemoryStore(db_path=root)
    assert store.db_path == root
    assert (root / "facts").is_dir()
    store.close()
