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


def test_default_store_rejects_symlinked_vault_parent(vault, tmp_path):
    """Default storage must not follow 06_Automation outside the Vault."""
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "06_Automation").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="inside HERMES_OBSIDIAN_VAULT"):
        MemoryStore()

    assert not (outside / "Hermes").exists()


@pytest.mark.parametrize("child_name", ["facts", "banks"])
def test_store_rejects_symlinked_data_directory(vault, tmp_path, child_name):
    """Pre-existing facts/banks links must not redirect durable memory."""
    root = vault / "06_Automation" / "Hermes" / "HolographicMemory"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / child_name).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="path escapes its Vault directory"):
        MemoryStore()

    assert list(outside.iterdir()) == []


def test_store_rejects_symlinked_lock_file(vault, tmp_path):
    """A lock-file link must not open and modify an unrelated file."""
    root = vault / "06_Automation" / "Hermes" / "HolographicMemory"
    root.mkdir(parents=True)
    external_lock = tmp_path / "external.lock"
    external_lock.write_text("original", encoding="utf-8")
    (root / ".vault-writer.lock").symlink_to(external_lock)

    with pytest.raises(ValueError, match="path escapes its Vault directory"):
        MemoryStore()

    assert external_lock.read_text(encoding="utf-8") == "original"


def test_store_rejects_symlinked_entity_index(vault, tmp_path):
    """Entity reads must not follow a pre-existing link outside the Vault."""
    root = vault / "06_Automation" / "Hermes" / "HolographicMemory"
    root.mkdir(parents=True)
    external_index = tmp_path / "external.json"
    external_index.write_text('{"by_name": {}}', encoding="utf-8")
    (root / "entities.json").symlink_to(external_index)

    with pytest.raises(ValueError, match="path escapes its Vault directory"):
        MemoryStore()

    assert external_index.read_text(encoding="utf-8") == '{"by_name": {}}'


def test_lockfile_symlink_added_after_initialization_is_rejected(vault, tmp_path):
    """The lock must not follow a link planted after the constructor check."""
    store = MemoryStore()
    outside = tmp_path / "external.lock"
    outside.write_text("untouched", encoding="utf-8")
    store._lock_file.symlink_to(outside)

    with pytest.raises(RuntimeError, match="Unsafe Vault memory lockfile"):
        store.add_fact("Must not persist")

    assert outside.read_text(encoding="utf-8") == "untouched"
    assert list(store.facts_dir.glob("*.json")) == []


def test_lockfile_inode_replacement_after_flock_is_rejected(vault, monkeypatch):
    """Do not write facts under a lock whose pathname was replaced."""
    import fcntl
    import os

    store = MemoryStore()
    original_flock = fcntl.flock
    replaced = False

    def swap_after_lock(fd, operation):
        nonlocal replaced
        original_flock(fd, operation)
        if operation == fcntl.LOCK_EX and not replaced:
            replaced = True
            replacement = store.db_path / ".replacement-lock"
            replacement.write_text("replacement", encoding="utf-8")
            os.replace(replacement, store._lock_file)

    monkeypatch.setattr(fcntl, "flock", swap_after_lock)
    with pytest.raises(RuntimeError, match="changed during acquisition"):
        store.add_fact("Must not persist")

    assert replaced
    assert store._lock_depth == 0
    assert list(store.facts_dir.glob("*.json")) == []


@pytest.mark.parametrize("dangling", [False, True])
def test_entity_index_symlink_added_after_initialization_is_rejected(vault, tmp_path, dangling):
    """A late-planted entity index link must never be read outside the Vault."""
    store = MemoryStore()
    outside = tmp_path / "external-entities.json"
    if not dangling:
        outside.write_text('{"next_id": 900, "by_name": {"External": 1}}', encoding="utf-8")
    index_path = store.db_path / "entities.json"
    index_path.symlink_to(outside)
    original = outside.read_bytes() if not dangling else None

    with pytest.raises(RuntimeError, match="Unsafe Vault entity index"):
        store.add_fact('Alice Smith remembers the event')

    assert index_path.is_symlink()
    if dangling:
        assert not outside.exists()
    else:
        assert outside.read_bytes() == original
    assert list(store.facts_dir.glob("*.json")) == []


def test_entity_index_symlink_swap_at_open_is_rejected(vault, tmp_path, monkeypatch):
    """O_NOFOLLOW must reject a link planted just before the entity-index open."""
    import os
    from pathlib import Path

    store = MemoryStore()
    fact_id = store.add_fact("Alice Smith remembers the event")
    assert store.fact_entity_names(fact_id) == ["Alice Smith"]

    index_path = store.db_path / "entities.json"
    outside = tmp_path / "external-entities.json"
    outside.write_text('{"next_id": 900, "by_name": {"External": 1}}', encoding="utf-8")
    original = outside.read_bytes()
    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path) == index_path and not swapped:
            swapped = True
            index_path.unlink()
            index_path.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(RuntimeError, match="Unsafe Vault entity index"):
        store._entities_index()

    assert swapped
    assert outside.read_bytes() == original


@pytest.mark.parametrize("use_bulk_reader", [False, True])
def test_fact_symlink_swap_at_open_is_ignored(vault, tmp_path, monkeypatch, use_bulk_reader):
    """A fact file swapped to an outside link during open must not be read."""
    import os
    from pathlib import Path

    store = MemoryStore()
    fact_id = store.add_fact("Original safe fact")
    fact_path = store._fact_path(fact_id)
    outside = tmp_path / "external-fact.json"
    outside.write_text(
        '{"fact_id": 999, "content": "External private fact"}',
        encoding="utf-8",
    )
    original = outside.read_bytes()
    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path) == fact_path and not swapped:
            swapped = True
            fact_path.unlink()
            fact_path.symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    if use_bulk_reader:
        assert store._all_facts() == []
    else:
        assert store._read_fact(fact_id) is None

    assert swapped
    assert outside.read_bytes() == original
