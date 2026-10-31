"""Tests for issue #860 — canonical Obsidian Vault transcript deduplication.

Verifies that:
1. _flush_messages_to_session_db uses _last_flushed_db_idx to avoid re-writing
2. Multiple _persist_session calls do not duplicate messages
3. append_to_transcript(skip_db=True) does not duplicate canonical Vault writes
4. The gateway never creates a legacy JSONL mirror when a SessionDB exists
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test: _flush_messages_to_session_db only writes new messages
# ---------------------------------------------------------------------------

class TestFlushDeduplication:
    """Verify _flush_messages_to_session_db tracks what it already wrote."""

    def _make_agent(self, session_db):
        """Create a minimal AIAgent with a real session DB."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            from run_agent import AIAgent
            agent = AIAgent(
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                model="test/model",
                quiet_mode=True,
                session_db=session_db,
                session_id="test-session-860",
                skip_context_files=True,
                skip_memory=True,
            )
        # Simulate lazy session creation (normally done by run_conversation)
        agent._ensure_db_session()
        return agent

    def test_flush_writes_only_new_messages(self):
        """First flush writes all new messages, second flush writes none."""
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db = SessionDB(vault_path=tmpdir)

            agent = self._make_agent(db)

            conversation_history = [
                {"role": "user", "content": "old message"},
            ]
            messages = list(conversation_history) + [
                {"role": "user", "content": "new question"},
                {"role": "assistant", "content": "new answer"},
            ]

            # First flush — should write 2 new messages
            agent._flush_messages_to_session_db(messages, conversation_history)

            rows = db.get_messages(agent.session_id)
            assert len(rows) == 2, f"Expected 2 messages, got {len(rows)}"

            # Second flush with SAME messages — should write 0 new messages
            agent._flush_messages_to_session_db(messages, conversation_history)

            rows = db.get_messages(agent.session_id)
            assert len(rows) == 2, f"Expected still 2 messages after second flush, got {len(rows)}"

    def test_flush_writes_incrementally(self):
        """Messages added between flushes are written exactly once."""
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db = SessionDB(vault_path=tmpdir)

            agent = self._make_agent(db)

            conversation_history = []
            messages = [
                {"role": "user", "content": "hello"},
            ]

            # First flush — 1 message
            agent._flush_messages_to_session_db(messages, conversation_history)
            rows = db.get_messages(agent.session_id)
            assert len(rows) == 1

            # Add more messages
            messages.append({"role": "assistant", "content": "hi there"})
            messages.append({"role": "user", "content": "follow up"})

            # Second flush — should write only 2 new messages
            agent._flush_messages_to_session_db(messages, conversation_history)
            rows = db.get_messages(agent.session_id)
            assert len(rows) == 3, f"Expected 3 total messages, got {len(rows)}"

    def test_persist_session_multiple_calls_no_duplication(self):
        """Multiple _persist_session calls don't duplicate DB entries."""
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db = SessionDB(vault_path=tmpdir)

            agent = self._make_agent(db)
            # Stub out _save_session_log to avoid file I/O
            agent._save_session_log = MagicMock()

            conversation_history = [{"role": "user", "content": "old"}]
            messages = list(conversation_history) + [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": "a2"},
            ]

            # Simulate multiple persist calls (like the agent's many exit paths)
            for _ in range(5):
                agent._persist_session(messages, conversation_history)

            rows = db.get_messages(agent.session_id)
            assert len(rows) == 4, f"Expected 4 messages, got {len(rows)} (duplication bug!)"

    def test_flush_reset_after_compression(self):
        """After compression creates a new session, flush index resets."""
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db = SessionDB(vault_path=tmpdir)

            agent = self._make_agent(db)

            # Write some messages
            messages = [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "reply1"},
            ]
            agent._flush_messages_to_session_db(messages, [])

            old_session = agent.session_id
            assert agent._last_flushed_db_idx == 2

            # Simulate what _compress_context does: new session, reset idx
            agent.session_id = "compressed-session-new"
            db.create_session(session_id=agent.session_id, source="test")
            agent._last_flushed_db_idx = 0

            # Now flush compressed messages to new session
            compressed_messages = [
                {"role": "user", "content": "summary of conversation"},
            ]
            agent._flush_messages_to_session_db(compressed_messages, [])

            new_rows = db.get_messages(agent.session_id)
            assert len(new_rows) == 1

            # Old session should still have its 2 messages
            old_rows = db.get_messages(old_session)
            assert len(old_rows) == 2


# ---------------------------------------------------------------------------
# Test: append_to_transcript skip_db parameter
# ---------------------------------------------------------------------------

class TestAppendToTranscriptSkipDb:
    """Verify the Vault is canonical and legacy JSONL is never dual-written."""

    @pytest.fixture()
    def store(self, tmp_path):
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore
        config = GatewayConfig()
        with patch("gateway.session.SessionStore._ensure_loaded"):
            s = SessionStore(sessions_dir=tmp_path / "legacy_sessions", config=config, vault_path=tmp_path)
        s._db = None  # standalone test-double fallback, not the production Vault path
        s._loaded = True
        return s

    def test_skip_db_writes_jsonl_only(self, store, tmp_path):
        """Without a SessionDB, a test double can still write its Vault-local JSONL."""
        session_id = "test-skip-db"
        msg = {"role": "assistant", "content": "hello world"}
        store.append_to_transcript(session_id, msg, skip_db=True)

        # JSONL should have the message
        jsonl_path = store.get_transcript_path(session_id)
        assert jsonl_path.exists()
        with open(jsonl_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["content"] == "hello world"

    def test_skip_db_does_not_duplicate_vault_or_create_jsonl(self, tmp_path):
        """With a real Vault backend, skip_db=True performs no additional writes."""
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore
        from hermes_state import SessionDB

        db = SessionDB(vault_path=tmp_path)

        config = GatewayConfig()
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path / "legacy_sessions", config=config, vault_path=tmp_path)
        store._db = db
        store._loaded = True

        session_id = "test-skip-db-real"
        db.create_session(session_id=session_id, source="test")

        msg = {"role": "assistant", "content": "hello world"}
        store.append_to_transcript(session_id, msg, skip_db=True)

        # The caller has already flushed this message, so the gateway must
        # neither append it to the canonical Vault nor create a JSONL mirror.
        assert db.get_messages(session_id) == []
        assert not store.get_transcript_path(session_id).exists()

    def test_default_writes_only_canonical_vault(self, tmp_path):
        """Without skip_db, persist once to the Vault and never dual-write JSONL."""
        from gateway.config import GatewayConfig
        from gateway.session import SessionStore
        from hermes_state import SessionDB

        db = SessionDB(vault_path=tmp_path)

        config = GatewayConfig()
        with patch("gateway.session.SessionStore._ensure_loaded"):
            store = SessionStore(sessions_dir=tmp_path / "legacy_sessions", config=config, vault_path=tmp_path)
        store._db = db
        store._loaded = True

        session_id = "test-default-write"
        db.create_session(session_id=session_id, source="test")

        msg = {"role": "user", "content": "test message"}
        store.append_to_transcript(session_id, msg)

        # The canonical Vault contains the message; no JSONL mirror is made.
        rows = db.get_messages(session_id)
        assert len(rows) == 1
        assert rows[0]["content"] == "test message"
        assert not store.get_transcript_path(session_id).exists()
        assert store.load_transcript_unified(session_id)[0]["content"] == "test message"


# ---------------------------------------------------------------------------
# Test: _last_flushed_db_idx initialization
# ---------------------------------------------------------------------------

class TestFlushIdxInit:
    """Verify _last_flushed_db_idx is properly initialized."""

    def test_init_zero(self):
        """Agent starts with _last_flushed_db_idx = 0."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            from run_agent import AIAgent
            agent = AIAgent(
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                model="test/model",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        assert agent._last_flushed_db_idx == 0

    def test_no_session_db_noop(self):
        """Without session_db, flush is a no-op and doesn't crash."""
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            from run_agent import AIAgent
            agent = AIAgent(
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                model="test/model",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        messages = [{"role": "user", "content": "test"}]
        agent._flush_messages_to_session_db(messages, [])
        # Should not crash, idx should remain 0
        assert agent._last_flushed_db_idx == 0
