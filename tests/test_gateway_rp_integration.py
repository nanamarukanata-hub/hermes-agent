"""Discord Gateway -> RP Engine integration tests.

These tests simulate the exact gateway path without a Discord token,
a network connection, or a model call:

- Discord message -> session key (gateway rules) -> RP before_agent
  (card + World Book + Vault memory -> prompt section) -> RP after_agent
  (state persisted) -> restart (new RPConversation over the same Vault)
  -> state + history restored.

The real gateway hook in gateway/run.py _handle_message_with_agent
calls the same RPConversation.before_agent / after_agent methods.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_hybrid import CharacterCard, RPConversation, VaultStore, discord_session_key


CARD = {"data": {"name": "TestChar", "description": "d", "personality": "p",
                 "scenario": "s", "first_mes": "hello", "mes_example": "ex",
                 "tags": ["t"],
                 "character_book": {"entries": [
                     {"keys": ["tavern"], "content": "a tavern by the river"}]}}}


class FakeSessionDB:
    """Duck-typed stand-in for PR #11 SessionDB."""

    def __init__(self):
        self.messages = {}

    def get_messages_as_conversation(self, session_id):
        return list(self.messages.get(session_id, []))

    def search_messages(self, query, **kwargs):
        return []


def _setup(tmp_path, user="userA"):
    store = VaultStore(tmp_path / "vault")
    db = FakeSessionDB()
    rp = RPConversation(store, session_db=db)
    rp.set_active_card(user, CharacterCard.from_sillytavern(CARD))
    store.save_memory(user, "fact1", {"text": "likes tea", "kind": "fact"})
    return store, db, rp


def test_discord_session_key_mirrors_gateway_rules():
    # DM: per-chat, thread differentiates.
    assert discord_session_key(chat_type="dm", chat_id="111") == "agent:main:discord:dm:111"
    assert discord_session_key(chat_type="dm", chat_id="111", thread_id="222") == \
        "agent:main:discord:dm:111:222"
    # Group: per-user by default, shared in threads by default.
    assert discord_session_key(chat_type="group", chat_id="c1", user_id="u1") == \
        "agent:main:discord:group:c1:u1"
    assert discord_session_key(chat_type="group", chat_id="c1", user_id="u1") != \
        discord_session_key(chat_type="group", chat_id="c1", user_id="u2")
    assert discord_session_key(chat_type="channel", chat_id="c1", thread_id="t1",
                               user_id="u1") == "agent:main:discord:channel:c1:t1"
    assert discord_session_key(chat_type="channel", chat_id="c1", thread_id="t1",
                               user_id="u1", thread_sessions_per_user=True) == \
        "agent:main:discord:channel:c1:t1:u1"


def test_gateway_rp_flow_and_restart_recovery(tmp_path):
    store, db, rp = _setup(tmp_path)
    session_id = "20261025_120000_ab12cd34"
    db.messages[session_id] = [{"role": "user", "content": "hello at the tavern"}]

    # before_agent: card, World Book, and Vault memory must reach the prompt.
    prompt = rp.before_agent("userA", session_id, "meet me at the tavern, I like tea")
    assert "TestChar" in prompt
    assert "a tavern by the river" in prompt
    assert "likes tea" in prompt

    # after_agent: RP turn state persists; conversation itself stays in SessionDB.
    state = rp.after_agent("userA", session_id, "hello", "hi there")
    assert state.turn_count == 1

    # Restart: a fresh RPConversation over the same Vault restores state + history.
    rp2 = RPConversation(VaultStore(tmp_path / "vault"), session_db=db)
    restored = rp2.restore("userA", session_id)
    assert restored["rp_state"]["turn_count"] == 1
    assert restored["rp_state"]["card"]["name"] == "TestChar"
    assert restored["history"][0]["content"] == "hello at the tavern"


def test_no_card_is_noop_and_users_are_isolated(tmp_path):
    store, db, rp = _setup(tmp_path)
    # No card for userB: gateway behaviour is unchanged.
    assert rp.before_agent("userB", "20261025_120000_ab12cd34", "hello") == ""
    assert rp.after_agent("userB", "20261025_120000_ab12cd34", "hi", "hello") is None
    # userA's RP state and memory are not visible to userB.
    rp_b = RPConversation(VaultStore(tmp_path / "vault"), session_db=db)
    assert rp_b.load_active_state("userB") is None
    assert store.search("userB", "tea") == []


def test_rp_state_survives_session_reset(tmp_path):
    store, db, rp = _setup(tmp_path)
    old_session = "20261025_120000_ab12cd34"
    rp.after_agent("userA", old_session, "hi", "hello")
    # New gateway session id after a reset: RP state is keyed by
    # (user, character), not by session id, so it restores.
    rp2 = RPConversation(VaultStore(tmp_path / "vault"), session_db=db)
    restored = rp2.restore("userA", "20261025_130000_cd34ef56")
    assert restored["rp_state"]["turn_count"] == 1
