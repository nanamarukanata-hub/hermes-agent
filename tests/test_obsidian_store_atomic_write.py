"""Regression tests for exclusive temporary files in hybrid Obsidian Vault writes."""

import os

import pytest

from hermes_hybrid import obsidian_store
from hermes_hybrid.obsidian_store import VaultStore


@pytest.mark.parametrize("kind", ["session", "memory"])
def test_preplanted_pid_temp_symlink_cannot_overwrite_external_file(tmp_path, kind):
    store = VaultStore(tmp_path / "vault")
    if kind == "session":
        target = store.session_path("s1")
    else:
        target = store.memory_path("m1")
    target.parent.mkdir(parents=True, exist_ok=True)

    outside = tmp_path / "external-secret.txt"
    outside.write_text("DO NOT MODIFY", encoding="utf-8")
    predictable = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    predictable.symlink_to(outside)

    if kind == "session":
        store.save_session("alice", "s1", {"messages": []})
    else:
        store.save_memory("alice", "m1", {"text": "hello"})

    assert outside.read_text(encoding="utf-8") == "DO NOT MODIFY"
    assert predictable.is_symlink()
    assert target.is_file() and not target.is_symlink()
    assert list(target.parent.glob(f".{target.name}.tmp-*")) == [predictable]


def test_replace_failure_keeps_original_and_removes_temp(tmp_path, monkeypatch):
    store = VaultStore(tmp_path / "vault")
    store.save_session("alice", "s1", {"messages": [{"role": "user", "content": "original"}]})
    target = store.session_path("s1")
    before = target.read_text(encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(obsidian_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        store.save_session("alice", "s1", {"messages": [{"role": "user", "content": "changed"}]})

    assert target.read_text(encoding="utf-8") == before
    assert list(target.parent.glob(f".{target.name}.tmp-*")) == []


def test_writer_lock_symlink_cannot_target_outside_vault(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.lock_file.parent.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged", encoding="utf-8")
    store.lock_file.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        store.save_session("alice", "s1", {"messages": []})

    assert outside.read_text(encoding="utf-8") == "unchanged"
    assert not store.session_path("s1").exists()


def test_writer_lock_rejects_symlinked_parent(tmp_path):
    store = VaultStore(tmp_path / "vault")
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "06_Automation").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        store.save_session("alice", "s1", {"messages": []})

    assert list(outside.iterdir()) == []


def test_writer_lock_rejects_fifo_without_blocking(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.lock_file.parent.mkdir(parents=True)
    os.mkfifo(store.lock_file)

    with pytest.raises(ValueError, match="regular file"):
        store.save_session("alice", "s1", {"messages": []})

    assert not store.session_path("s1").exists()


def test_writer_lock_inode_swap_after_flock_aborts_write(tmp_path, monkeypatch):
    store = VaultStore(tmp_path / "vault")
    original_flock = obsidian_store.fcntl.flock
    replaced = False

    def swap_after_lock(fd, operation):
        nonlocal replaced
        original_flock(fd, operation)
        if operation == obsidian_store.fcntl.LOCK_EX and not replaced:
            replaced = True
            replacement = store.tree / ".replacement-lock"
            replacement.write_text("replacement", encoding="utf-8")
            os.replace(replacement, store.lock_file)

    monkeypatch.setattr(obsidian_store.fcntl, "flock", swap_after_lock)
    with pytest.raises(RuntimeError, match="changed during acquisition"):
        store.save_session("alice", "s1", {"messages": []})

    assert replaced
    assert not store.session_path("s1").exists()
