"""Regression tests: Discord RP user isolation must fail closed."""
import os

import pytest

from hermes_hybrid import CharacterCard, VaultStore
from hermes_hybrid.session_manager import SessionManager


def test_session_lookup_listing_and_search_never_cross_users(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.save_document("Conversations", "alice", "alice-session",
                        {"turns": [{"role": "user", "content": "alice private secret"}]})
    store.save_document("Conversations", "bob", "bob-session",
                        {"turns": [{"role": "user", "content": "bob private secret"}]})

    assert store.load_document("Conversations", "bob", "alice-session") is None
    assert store.load_document("Sessions", "alice", "bob-session") is None
    assert store.list_documents("Sessions", "alice") == ["alice-session"]
    assert store.list_documents("Conversations", "bob") == ["bob-session"]
    assert store.search("bob", "alice private secret", kinds=["Conversations"]) == []


def test_ownerless_memory_not_exposed_as_public_discord_memory(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.save_memory("alice", "alice-note", {"text": "alice private secret"})
    legacy = store.memory_path("ownerless-note")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('---\nid: "ownerless-note"\nkind: "fact"\n---\n\nownerless private secret\n',
                      encoding="utf-8")

    assert store.load_document("Memory", "bob", "alice-note") is None
    assert store.load_document("Memory", "bob", "ownerless-note") is None
    assert store.load_document("Memory", "alice", "ownerless-note") is None
    assert store.list_documents("Memory", "alice") == ["alice-note"]
    assert store.list_documents("Memory", "bob") == []
    assert store.search("bob", "secret", kinds=["Memory"]) == []


def test_symlinked_memory_note_refused_before_read(tmp_path):
    store = VaultStore(tmp_path / "vault")
    outside = tmp_path / "outside.md"
    outside.write_text("sensitive content", encoding="utf-8")
    path = store.memory_path("external")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, path)
    with pytest.raises(ValueError, match="symlink"):
        store.list_documents("Memory", "alice")


def test_restore_and_compression_reject_other_users(tmp_path):
    store = VaultStore(tmp_path / "vault")
    manager = SessionManager(store)
    session = manager.start("alice", "channel1", CharacterCard(name="Example"))
    assert manager.restore("alice", session.session_id) is not None
    assert manager.restore("bob", session.session_id) is None
    with pytest.raises(PermissionError, match="not owned"):
        store.save_conversation_turn("bob", session.session_id,
                                     {"role": "user", "content": "unauthorized append"})
    assert store.get_messages(session.session_id) == []
    with pytest.raises(PermissionError, match="not owned"):
        store.compress_conversation("bob", session.session_id, keep_last=1)


def test_flat_memory_ids_cannot_be_overwritten_by_another_user(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.save_memory("alice", "shared-id", {"text": "alice private"})
    with pytest.raises(PermissionError, match="not owned"):
        store.save_memory("bob", "shared-id", {"text": "bob takeover"})
    assert store.load_document("Memory", "alice", "shared-id")["text"] == "alice private"
    assert store.load_document("Memory", "bob", "shared-id") is None

    # The rightful owner can still edit the same note.
    store.save_memory("alice", "shared-id", {"text": "alice update"})
    assert store.load_document("Memory", "alice", "shared-id")["text"] == "alice update"

    # Ownerless legacy records cannot be silently claimed by a Discord user.
    ownerless = store.memory_path("legacy")
    ownerless.write_text('---\nid: "legacy"\n---\n\nlegacy note\n', encoding="utf-8")
    with pytest.raises(PermissionError, match="not owned"):
        store.save_memory("alice", "legacy", {"text": "takeover"})


def test_session_save_cannot_reassign_or_erase_existing_owner(tmp_path):
    store = VaultStore(tmp_path / "vault")
    store.save_document("Conversations", "alice", "shared-session",
                        {"turns": [{"role": "user", "content": "alice secret"}]})

    with pytest.raises(PermissionError, match="not owned"):
        store.save_document("Sessions", "bob", "shared-session",
                            {"turns": [{"role": "user", "content": "bob takeover"}]})
    with pytest.raises(PermissionError, match="not owned"):
        store.save_session_data("shared-session", {"messages": []})
    assert store.load_document("Conversations", "alice", "shared-session")["turns"][0]["content"] == "alice secret"

    # Explicit user_id argument is authoritative, not an untrusted data field.
    store.save_session("alice", "shared-session",
                       {"user_id": "bob", "messages": [{"role": "user", "content": "alice update"}]})
    assert store.load_session("shared-session")["user_id"] == "alice"
