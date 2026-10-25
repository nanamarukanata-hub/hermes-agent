import base64
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_hybrid import (Adapter, AdapterRegistry, CharacterCard, EmotionalState,
                           GatewayConfig, RPCharacterState, SceneState, VaultStore,
                           VaultSync, WorldBook, audit, is_allowed, session_key,
                           should_respond)
from hermes_hybrid.session_manager import SessionManager
from hermes_hybrid.obsidian_store import validate_id


CARD = {"data": {"name": "TestChar", "description": "d", "personality": "p",
                 "scenario": "s", "first_mes": "hello", "mes_example": "ex",
                 "tags": ["t"],
                 "character_book": {"entries": [
                     {"keys": ["tavern"], "content": "a tavern by the river"},
                     {"keys": ["forest"], "content": "a dark forest"}]}}}


def make_card():
    return CharacterCard.from_sillytavern(CARD)


def test_character_consistency_and_card_loading():
    card = make_card()
    assert card.name == "TestChar"
    assert card.first_message == "hello"
    state = RPCharacterState(card=card)
    assert state.card.description == "d"
    bar = state.advance_turn(SceneState(location="tavern", outfit="coat", posture="standing"))
    assert "location=tavern" in bar and "outfit=coat" in bar
    assert state.turn_count == 1


def test_emotional_continuity_inertia():
    e = EmotionalState()
    e.move(1.0, 1.0, "happy")
    # One move cannot jump the full range: max_step is 0.35.
    assert e.valence == pytest.approx(0.35)
    e.move(1.0, 1.0, "happy")
    assert e.valence == pytest.approx(0.70)


def test_world_book_search():
    wb = WorldBook(make_card().world_book)
    hits = wb.search("meet me at the tavern")
    assert len(hits) == 1 and "tavern" in hits[0]["content"]
    assert wb.search("nowhere") == []
    assert wb.compact_index()[0]["keys"] == ["tavern"]


def test_png_card_roundtrip(tmp_path):
    payload = b"chara\x00" + json.dumps(CARD).encode()
    chunk = struct.pack(">I", len(payload)) + b"tEXt" + payload
    chunk += struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk
    png += struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    p = tmp_path / "card.png"
    p.write_bytes(png)
    card = CharacterCard.from_file(p)
    assert card.name == "TestChar"


def test_multi_user_isolation(tmp_path):
    store = VaultStore(tmp_path / "vault")
    mgr = SessionManager(store)
    s1 = mgr.start("userA", "chan1", make_card())
    s2 = mgr.start("userB", "chan1", make_card())
    mgr.record_turn(s1, "user", "secret A")
    mgr.record_turn(s2, "user", "secret B")
    restored = mgr.restore("userA", s1.session_id)
    assert restored is not None
    turns = store.load_document("Conversations", "userA", s1.session_id)["turns"]
    assert turns[0]["content"] == "secret A"
    assert "secret A" not in json.dumps(store.load_document("Conversations", "userB", s2.session_id))


def test_long_term_memory_and_restart_recovery(tmp_path):
    store = VaultStore(tmp_path / "vault")
    mgr = SessionManager(store)
    s = mgr.start("userA", "chan1", make_card())
    store.save_memory("userA", "fact1", {"text": "likes tea"})
    store.save_session("userA", s.session_id, s.to_dict())
    # Simulate restart: new manager over the same vault.
    mgr2 = SessionManager(VaultStore(tmp_path / "vault"))
    assert mgr2.restore("userA", s.session_id) is not None
    assert store.load_document("Memory", "userA", "fact1")["text"] == "likes tea"
    assert store.search("userA", "tea")[0]["kind"] == "Memory"


def test_context_compression(tmp_path):
    store = VaultStore(tmp_path / "vault")
    mgr = SessionManager(store)
    s = mgr.start("userA", "chan1", make_card())
    for i in range(25):
        mgr.record_turn(s, "user", f"msg {i}")
    compressed = store.compress_conversation("userA", s.session_id, keep_last=5, summary="early chat")
    assert len(compressed["turns"]) == 5
    assert compressed["archived_turn_count"] == 20
    archive = store.load_document("Conversations", "userA", f"{s.session_id}-archive")
    assert len(archive["archived_turns"]) == 20


def test_character_switch_keeps_old_state(tmp_path):
    store = VaultStore(tmp_path / "vault")
    mgr = SessionManager(store)
    s = mgr.start("userA", "chan1", make_card())
    other = CharacterCard(name="Other", description="o")
    mgr.switch_character(s, other)
    assert store.load_document("CharacterState", "userA", "TestChar") is not None
    assert store.load_document("CharacterState", "userA", "Other") is not None


def test_path_traversal_and_symlink_rejected(tmp_path):
    store = VaultStore(tmp_path / "vault")
    for bad in ("..", ".", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            validate_id(bad)
    with pytest.raises(ValueError):
        store.load_document("Sessions", "..", "x")
    # Symlink inside the vault pointing outside must be rejected.
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "vault" / "Sessions").mkdir(parents=True)
    os.symlink(outside, tmp_path / "vault" / "Sessions" / "userA")
    with pytest.raises(ValueError):
        store.load_document("Sessions", "userA", "x")


def test_atomic_and_concurrent_writes(tmp_path):
    store = VaultStore(tmp_path / "vault")
    # Atomic: no .tmp files remain after a write.
    store.save_document("Sessions", "userA", "s1", {"v": 1})
    assert list((tmp_path / "vault").rglob("*.tmp-*")) == []
    # Concurrent: 20 processes writing the same document must not corrupt it.
    script = (
        "import sys; sys.path.insert(0, %r);"
        "from hermes_hybrid import VaultStore;"
        "VaultStore(%r).save_document('Sessions', 'userA', 'shared', {'v': 1})"
        % (str(Path(__file__).resolve().parents[1]), str(tmp_path / "vault"))
    )
    procs = [subprocess.Popen([sys.executable, "-c", script]) for _ in range(20)]
    assert all(p.wait() == 0 for p in procs)
    assert store.load_document("Sessions", "userA", "shared") == {"v": 1}


def test_vault_sync_conflict_and_refusal(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("local only")
    sync = VaultSync(vault)
    result = sync.ensure_checkout()
    assert not result.ok and result.action == "refused-nonempty-non-git"
    assert (vault / "note.md").read_text() == "local only"
    # A real git repo whose pull diverges must not be reset/forced.
    subprocess.run(["git", "init", "-b", "main", str(vault)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", "t"], check=True)
    (vault / "a.md").write_text("a")
    subprocess.run(["git", "-C", str(vault), "add", "."], check=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-m", "init"], check=True, capture_output=True)
    pulled = sync.pull_fast_forward()
    # No remote is configured, so pull fails and must leave the tree untouched.
    assert not pulled.ok
    assert (vault / "a.md").read_text() == "a"


def test_sqlite_runtime_zero():
    result = audit(Path(__file__).resolve().parents[1] / "hermes_hybrid")
    assert result["sqlite_imports"] == []
    assert result["db_files"] == []


def test_gateway_and_mcp_bridge():
    cfg = GatewayConfig(allowed_user_ids={"u1"}, free_response_channel_ids={"c-free"})
    assert is_allowed(cfg, "u1") and not is_allowed(cfg, "u2")
    assert should_respond(cfg, is_dm=True, mentioned=False, channel_id="c1")
    assert should_respond(cfg, is_dm=False, mentioned=False, channel_id="c-free")
    assert not should_respond(cfg, is_dm=False, mentioned=False, channel_id="c1")
    assert session_key("u1", "c1", "char") != session_key("u2", "c1", "char")
    reg = AdapterRegistry()
    reg.register(Adapter(name="linux-mcp", kind="mcp"))
    reg.register(Adapter(name="gdrive", kind="drive"))
    reg.register(Adapter(name="colab", kind="colab"))
    assert reg.names() == ["colab", "gdrive", "linux-mcp"]
    assert len(reg.enabled("mcp")) == 1
