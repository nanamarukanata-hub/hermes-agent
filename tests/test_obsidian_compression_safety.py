"""Regression tests for lossless, retryable Obsidian session compression."""

import pytest

from hermes_hybrid.obsidian_store import VaultStore


def _messages(start, end):
    return [{"role": "user", "content": f"msg {i}"} for i in range(start, end)]


def _contents(messages):
    return [message["content"] for message in messages]


def test_repeated_compression_appends_to_existing_archive(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.save_session("alice", "s1", {"messages": _messages(0, 6)})

    first = store.compress_conversation("alice", "s1", keep_last=2)
    assert first["archived_message_count"] == 4

    for i in range(6, 9):
        store.append_session_message("s1", "user", f"msg {i}", user_id="alice")
    second = store.compress_conversation("alice", "s1", keep_last=2)

    assert _contents(store.get_messages("s1")) == ["msg 7", "msg 8"]
    assert _contents(store.get_messages("s1-archive")) == [
        f"msg {i}" for i in range(7)
    ]
    assert second["archived_message_count"] == 7
    assert second["compression_generation"] == 2


def test_archive_owner_collision_leaves_active_session_untouched(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.save_session("alice", "s1", {"messages": _messages(0, 5)})
    store.save_session("bob", "s1-archive", {"messages": _messages(50, 52)})

    with pytest.raises(PermissionError, match="archive is not owned"):
        store.compress_conversation("alice", "s1", keep_last=2)

    assert _contents(store.get_messages("s1")) == [
        f"msg {i}" for i in range(5)
    ]
    assert _contents(store.get_messages("s1-archive")) == ["msg 50", "msg 51"]


def test_failed_active_write_can_retry_without_duplicate_archive(tmp_path, monkeypatch):
    store = VaultStore(tmp_path / "vault")
    store.save_session("alice", "s1", {"messages": _messages(0, 6)})
    original_write = store._atomic_write
    active_path = store.session_path("s1")

    def fail_active(path, text):
        if path == active_path:
            raise OSError("injected active session write failure")
        return original_write(path, text)

    monkeypatch.setattr(store, "_atomic_write", fail_active)
    with pytest.raises(OSError, match="injected"):
        store.compress_conversation("alice", "s1", keep_last=2)
    monkeypatch.setattr(store, "_atomic_write", original_write)

    assert len(store.get_messages("s1")) == 6
    assert len(store.get_messages("s1-archive")) == 4
    compressed = store.compress_conversation("alice", "s1", keep_last=2)
    assert compressed["archived_message_count"] == 4
    assert _contents(store.get_messages("s1-archive")) == [
        f"msg {i}" for i in range(4)
    ]
    assert _contents(store.get_messages("s1")) == ["msg 4", "msg 5"]


def test_keep_zero_archives_all_and_negative_count_is_rejected(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.save_session("alice", "s1", {"messages": _messages(0, 3)})

    with pytest.raises(ValueError, match="non-negative"):
        store.compress_conversation("alice", "s1", keep_last=-1)
    assert len(store.get_messages("s1")) == 3

    result = store.compress_conversation("alice", "s1", keep_last=0)
    assert result["messages"] == []
    assert result["archived_message_count"] == 3
    assert _contents(store.get_messages("s1-archive")) == [
        "msg 0", "msg 1", "msg 2"
    ]
