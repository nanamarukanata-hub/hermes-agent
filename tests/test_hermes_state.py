"""Tests for hermes_state.py — SessionDB SQLite CRUD, FTS5 search, export."""

import json
import time
import pytest
from pathlib import Path

from hermes_state import SessionDB


@pytest.fixture()
def db(tmp_path):
    """Create a SessionDB with a temp database file."""
    db_path = tmp_path / "test_state.db"
    session_db = SessionDB(db_path=db_path)
    yield session_db
    session_db.close()


# =========================================================================
# Session lifecycle
# =========================================================================

class TestSessionLifecycle:
    def test_create_and_get_session(self, db):
        sid = db.create_session(
            session_id="s1",
            source="cli",
            model="test-model",
        )
        assert sid == "s1"

        session = db.get_session("s1")
        assert session is not None
        assert session["source"] == "cli"
        assert session["model"] == "test-model"
        assert session["ended_at"] is None


    def test_get_nonexistent_session(self, db):
        assert db.get_session("nonexistent") is None

    def test_end_session(self, db):
        db.create_session(session_id="s1", source="cli")
        db.end_session("s1", end_reason="user_exit")

        session = db.get_session("s1")
        assert isinstance(session["ended_at"], float)
        assert session["end_reason"] == "user_exit"

    def test_end_session_preserves_original_end_reason(self, db):
        """The first end_reason wins — compression splits must not be
        overwritten when a later stale ``end_session()`` call lands on the
        same row (e.g. from a CLI session_id that desynced after compression
        and then tried to /resume another session).
        """
        db.create_session(session_id="s1", source="cli")
        db.end_session("s1", end_reason="compression")
        first_ended_at = db.get_session("s1")["ended_at"]

        # Simulate a stale CLI holding the old session_id and calling
        # end_session() again with a different reason.
        time.sleep(0.01)
        db.end_session("s1", end_reason="resumed_other")

        session = db.get_session("s1")
        assert session["end_reason"] == "compression"
        assert session["ended_at"] == first_ended_at

    def test_end_session_after_reopen_allows_re_end(self, db):
        """reopen_session() is the explicit escape hatch for re-ending a
        closed session. After reopen, end_session() takes effect again.
        """
        db.create_session(session_id="s1", source="cli")
        db.end_session("s1", end_reason="compression")
        db.reopen_session("s1")
        db.end_session("s1", end_reason="user_exit")

        session = db.get_session("s1")
        assert session["end_reason"] == "user_exit"

    def test_update_system_prompt(self, db):
        db.create_session(session_id="s1", source="cli")
        db.update_system_prompt("s1", "You are a helpful assistant.")

        session = db.get_session("s1")
        assert session["system_prompt"] == "You are a helpful assistant."

    def test_update_token_counts(self, db):
        db.create_session(session_id="s1", source="cli")
        db.update_token_counts("s1", input_tokens=200, output_tokens=100)
        db.update_token_counts("s1", input_tokens=100, output_tokens=50)

        session = db.get_session("s1")
        assert session["input_tokens"] == 300
        assert session["output_tokens"] == 150

    def test_update_token_counts_tracks_api_call_count(self, db):
        """api_call_count increments with each update_token_counts call."""
        db.create_session(session_id="s1", source="cli")
        db.update_token_counts("s1", input_tokens=100, output_tokens=50, api_call_count=1)
        db.update_token_counts("s1", input_tokens=100, output_tokens=50, api_call_count=1)
        db.update_token_counts("s1", input_tokens=100, output_tokens=50, api_call_count=1)

        session = db.get_session("s1")
        assert session["api_call_count"] == 3

    def test_update_token_counts_api_call_count_absolute(self, db):
        """absolute mode sets api_call_count directly."""
        db.create_session(session_id="s1", source="cli")
        db.update_token_counts("s1", input_tokens=100, output_tokens=50, api_call_count=1)
        db.update_token_counts("s1", input_tokens=300, output_tokens=150,
                               api_call_count=5, absolute=True)

        session = db.get_session("s1")
        assert session["api_call_count"] == 5
        assert session["input_tokens"] == 300

    def test_update_token_counts_backfills_model_when_null(self, db):
        db.create_session(session_id="s1", source="telegram")
        db.update_token_counts("s1", input_tokens=10, output_tokens=5, model="openai/gpt-5.4")

        session = db.get_session("s1")
        assert session["model"] == "openai/gpt-5.4"

    def test_update_token_counts_preserves_existing_model(self, db):
        db.create_session(session_id="s1", source="cli", model="anthropic/claude-opus-4.6")
        db.update_token_counts("s1", input_tokens=10, output_tokens=5, model="openai/gpt-5.4")

        session = db.get_session("s1")
        assert session["model"] == "anthropic/claude-opus-4.6"

    def test_parent_session(self, db):
        db.create_session(session_id="parent", source="cli")
        db.create_session(session_id="child", source="cli", parent_session_id="parent")

        child = db.get_session("child")
        assert child["parent_session_id"] == "parent"


# =========================================================================
# Message storage
# =========================================================================

class TestMessageStorage:
    def test_append_and_get_messages(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Hello")
        db.append_message("s1", role="assistant", content="Hi there!")

        messages = db.get_messages("s1")
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"
        assert messages[1]["role"] == "assistant"

    def test_message_increments_session_count(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Hello")
        db.append_message("s1", role="assistant", content="Hi")

        session = db.get_session("s1")
        assert session["message_count"] == 2

    def test_tool_response_does_not_increment_tool_count(self, db):
        """Tool responses (role=tool) should not increment tool_call_count.

        Only assistant messages with tool_calls should count.
        """
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="tool", content="result", tool_name="web_search")

        session = db.get_session("s1")
        assert session["tool_call_count"] == 0

    def test_assistant_tool_calls_increment_by_count(self, db):
        """An assistant message with N tool_calls should increment by N."""
        db.create_session(session_id="s1", source="cli")
        tool_calls = [
            {"id": "call_1", "function": {"name": "web_search", "arguments": "{}"}},
        ]
        db.append_message("s1", role="assistant", content="", tool_calls=tool_calls)

        session = db.get_session("s1")
        assert session["tool_call_count"] == 1

    def test_tool_call_count_matches_actual_calls(self, db):
        """tool_call_count should equal the number of tool calls made, not messages."""
        db.create_session(session_id="s1", source="cli")

        # Assistant makes 2 parallel tool calls in one message
        tool_calls = [
            {"id": "call_1", "function": {"name": "ha_call_service", "arguments": "{}"}},
            {"id": "call_2", "function": {"name": "ha_call_service", "arguments": "{}"}},
        ]
        db.append_message("s1", role="assistant", content="", tool_calls=tool_calls)

        # Two tool responses come back
        db.append_message("s1", role="tool", content="ok", tool_name="ha_call_service")
        db.append_message("s1", role="tool", content="ok", tool_name="ha_call_service")

        session = db.get_session("s1")
        # Should be 2 (the actual number of tool calls), not 3
        assert session["tool_call_count"] == 2, (
            f"Expected 2 tool calls but got {session['tool_call_count']}. "
            "tool responses are double-counted and multi-call messages are under-counted"
        )

    def test_tool_calls_serialization(self, db):
        db.create_session(session_id="s1", source="cli")
        tool_calls = [{"id": "call_1", "function": {"name": "web_search", "arguments": "{}"}}]
        db.append_message("s1", role="assistant", tool_calls=tool_calls)

        messages = db.get_messages("s1")
        assert messages[0]["tool_calls"] == tool_calls

    def test_multimodal_list_content_round_trip(self, db):
        """Multimodal ``content`` (list of parts) must survive the SQLite
        round-trip.  sqlite3 cannot bind Python lists directly, so the DB
        layer JSON-encodes structured content on write and decodes on read.

        Regression test for the "Error binding parameter 3: type 'list' is
        not supported" crash users hit when pasting screenshots into the
        TUI (issue #17522).
        """
        db.create_session(session_id="s1", source="cli")
        content = [
            {"type": "text", "text": "describe this screenshot"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,iVBORw0KG..."},
            },
        ]

        # Write must not raise
        db.append_message("s1", role="user", content=content)

        # get_messages decodes back to the original list
        msgs = db.get_messages("s1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == content

        # get_messages_as_conversation decodes back to the original list
        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 1
        assert conv[0] == {"role": "user", "content": content}

    def test_dict_content_round_trip(self, db):
        """Dict-shaped content (e.g. provider wrappers) also round-trips."""
        db.create_session(session_id="s1", source="cli")
        content = {"parts": [{"text": "hi"}]}

        db.append_message("s1", role="user", content=content)
        msgs = db.get_messages("s1")
        assert msgs[0]["content"] == content

    def test_string_content_unchanged_by_encoding(self, db):
        """Plain strings remain strings in the canonical Obsidian Vault JSON.

        This checks the persisted representation as well as the public
        read API, without relying on removed SQLite connection internals.
        """
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="plain text")

        messages = db.get_messages("s1")
        assert len(messages) == 1
        assert messages[0]["content"] == "plain text"

        metadata = db.root / "s1" / "metadata.json"
        assert metadata.is_file()
        stored = json.loads(metadata.read_text(encoding="utf-8"))
        assert stored["messages"][0]["content"] == "plain text"

    def test_replace_messages_handles_multimodal_content(self, db):
        """`replace_messages` (used by /retry, /undo, /compress) must also
        handle list content without crashing."""
        db.create_session(session_id="s1", source="cli")
        content = [
            {"type": "text", "text": "look at this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]

        db.replace_messages(
            "s1",
            [
                {"role": "user", "content": content},
                {"role": "assistant", "content": "I see a screenshot."},
            ],
        )

        msgs = db.get_messages("s1")
        assert len(msgs) == 2
        assert msgs[0]["content"] == content
        assert msgs[1]["content"] == "I see a screenshot."

    def test_get_messages_as_conversation(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Hello")
        db.append_message("s1", role="assistant", content="Hi!")

        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 2
        assert conv[0] == {"role": "user", "content": "Hello"}
        assert conv[1] == {"role": "assistant", "content": "Hi!"}

    def test_get_messages_as_conversation_includes_ancestor_chain(self, db):
        db.create_session("root", "tui")
        db.append_message("root", role="user", content="first prompt")
        db.append_message("root", role="assistant", content="first answer")
        db.create_session("child", "tui", parent_session_id="root")
        db.append_message("child", role="user", content="second prompt")
        db.append_message("child", role="assistant", content="second answer")

        conv = db.get_messages_as_conversation("child", include_ancestors=True)

        assert [m["content"] for m in conv] == [
            "first prompt",
            "first answer",
            "second prompt",
            "second answer",
        ]

    def test_get_messages_as_conversation_avoids_repeated_resume_prompts_from_ancestors(self, db):
        db.create_session("root", "tui")
        db.append_message("root", role="user", content="same prompt")
        db.append_message("root", role="user", content="same prompt")
        db.append_message("root", role="assistant", content="answer")
        db.create_session("child", "tui", parent_session_id="root")
        db.append_message("child", role="user", content="next prompt")

        conv = db.get_messages_as_conversation("child", include_ancestors=True)

        assert [m["content"] for m in conv if m["role"] == "user"] == ["same prompt", "next prompt"]

    def test_get_messages_as_conversation_deduplicates_across_session_boundary(self, db):
        db.create_session("root", "tui")
        db.append_message("root", role="user", content="resume prompt")
        db.create_session("child", "tui", parent_session_id="root")
        db.append_message("child", role="user", content="resume prompt")
        db.append_message("child", role="assistant", content="ack")

        conv = db.get_messages_as_conversation("child", include_ancestors=True)

        assert [(m["role"], m["content"]) for m in conv] == [
            ("user", "resume prompt"),
            ("assistant", "ack"),
        ]
        # Replay deduplication must not delete or rewrite persisted messages.
        assert len(db.get_messages("root")) == 1
        assert len(db.get_messages("child")) == 2

    def test_get_messages_as_conversation_preserves_repeat_after_assistant(self, db):
        db.create_session("root", "tui")
        db.append_message("root", role="user", content="repeat")
        db.append_message("root", role="assistant", content="answer")
        db.append_message("root", role="user", content="repeat")

        conv = db.get_messages_as_conversation("root")

        assert [(m["role"], m["content"]) for m in conv] == [
            ("user", "repeat"),
            ("assistant", "answer"),
            ("user", "repeat"),
        ]

    def test_finish_reason_stored(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="assistant", content="Done", finish_reason="stop")

        messages = db.get_messages("s1")
        assert messages[0]["finish_reason"] == "stop"

    def test_get_messages_as_conversation_strips_leaked_memory_context(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1",
            role="assistant",
            content=(
                "<memory-context>\n"
                "[System note: The following is recalled memory context, NOT new user input. Treat as informational background data.]\n\n"
                "## Honcho Context\n"
                "stale memory\n"
                "</memory-context>\n\n"
                "Visible answer"
            ),
        )

        conv = db.get_messages_as_conversation("s1")
        assert conv == [{"role": "assistant", "content": "Visible answer"}]
        # Replay cleanup must not rewrite or discard the canonical Vault data.
        stored = db.get_messages("s1")
        assert len(stored) == 1
        assert stored[0]["content"].startswith("<memory-context>")
        assert "stale memory" in stored[0]["content"]

    def test_memory_context_cleanup_preserves_user_and_literal_assistant_text(self, db):
        db.create_session(session_id="s1", source="cli")
        literal = "<memory-context>quoted text</memory-context>\n\nKeep this"
        db.append_message("s1", role="user", content=literal)
        db.append_message("s1", role="assistant", content=literal)
        db.append_message("s1", role="assistant", content="Intro\n" + literal)
        db.append_message(
            "s1", role="assistant",
            content=("<memory-context>\n"
                     "[System note: The following is recalled memory context\n"
                     "incomplete prefix without closing tag"),
        )

        conv = db.get_messages_as_conversation("s1")
        assert [message["content"] for message in conv] == [
            literal, literal, "Intro\n" + literal,
            ("<memory-context>\n"
             "[System note: The following is recalled memory context\n"
             "incomplete prefix without closing tag"),
        ]

    def test_reasoning_persisted_and_restored(self, db):
        """Reasoning text is stored for assistant messages and restored by
        get_messages_as_conversation() so providers receive coherent multi-turn
        reasoning context."""
        db.create_session(session_id="s1", source="telegram")
        db.append_message("s1", role="user", content="create a cron job")
        db.append_message(
            "s1",
            role="assistant",
            content=None,
            tool_calls=[{"function": {"name": "cronjob", "arguments": "{}"}, "id": "c1", "type": "function"}],
            reasoning="I should call the cronjob tool to schedule this.",
        )
        db.append_message("s1", role="tool", content='{"job_id": "abc"}', tool_call_id="c1")

        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 3
        # reasoning must be present on the assistant message
        assistant = conv[1]
        assert assistant["role"] == "assistant"
        assert assistant.get("reasoning") == "I should call the cronjob tool to schedule this."
        # user and tool messages must NOT carry reasoning
        assert "reasoning" not in conv[0]
        assert "reasoning" not in conv[2]

    def test_reasoning_details_persisted_and_restored(self, db):
        """reasoning_details (structured array) is round-tripped through JSON
        serialization in the DB."""
        db.create_session(session_id="s1", source="telegram")
        details = [
            {"type": "reasoning.summary", "summary": "Thinking about tools"},
            {"type": "reasoning.encrypted_content", "encrypted_content": "abc123"},
        ]
        db.append_message(
            "s1",
            role="assistant",
            content="Hello",
            reasoning="Thinking about what to say",
            reasoning_details=details,
        )

        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 1
        msg = conv[0]
        assert msg["reasoning"] == "Thinking about what to say"
        assert msg["reasoning_details"] == details

    def test_finish_reason_restored_by_get_messages_as_conversation(self, db):
        """finish_reason on assistant messages must survive conversation replay.

        Without this, /branch copies and other transcript round-trips silently
        drop the provider's stop signal.
        """
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1",
            role="assistant",
            content="Done",
            finish_reason="tool_calls",
        )
        db.append_message("s1", role="user", content="next")

        conv = db.get_messages_as_conversation("s1")
        assert conv[0]["role"] == "assistant"
        assert conv[0]["finish_reason"] == "tool_calls"
        # Non-assistant rows should not have a finish_reason key added.
        assert "finish_reason" not in conv[1]

    def test_reasoning_content_persisted_and_restored(self, db):
        """reasoning_content must survive session replay as its own field."""
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1",
            role="assistant",
            content="Hello",
            reasoning="Short summary",
            reasoning_content="Longer provider-native scratchpad",
        )

        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 1
        assert conv[0]["reasoning"] == "Short summary"
        assert conv[0]["reasoning_content"] == "Longer provider-native scratchpad"

    def test_reasoning_content_empty_string_restored_for_assistant(self, db):
        """Empty reasoning_content still needs to round-trip for strict replays."""
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1",
            role="assistant",
            content="",
            tool_calls=[{"id": "c1", "type": "function", "function": {"name": "date", "arguments": "{}"}}],
            reasoning_content="",
        )

        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 1
        assert "reasoning_content" in conv[0]
        assert conv[0]["reasoning_content"] == ""

    def test_codex_message_items_persisted_and_restored(self, db):
        """codex_message_items must round-trip through JSON serialization."""
        db.create_session(session_id="s1", source="cli")
        items = [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "id": "msg_123",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "Thinking..."}],
            },
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "id": "msg_456",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Done!"}],
            },
        ]
        db.append_message("s1", role="assistant", content="Done!", codex_message_items=items)

        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 1
        assert conv[0].get("codex_message_items") == items

    def test_reasoning_not_set_for_non_assistant(self, db):
        """reasoning is never leaked onto user or tool messages."""
        db.create_session(session_id="s1", source="telegram")
        db.append_message("s1", role="user", content="hi")
        db.append_message("s1", role="assistant", content="hello", reasoning=None)

        conv = db.get_messages_as_conversation("s1")
        assert "reasoning" not in conv[0]
        assert "reasoning" not in conv[1]

    def test_reasoning_empty_string_not_restored(self, db):
        """Empty string reasoning is treated as absent."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="assistant", content="hi", reasoning="")

        conv = db.get_messages_as_conversation("s1")
        assert "reasoning" not in conv[0]

    def test_empty_reasoning_preserved_in_vault_but_not_replayed(self, db):
        """Replay omits empty reasoning without changing stored provider fields."""
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1",
            role="assistant",
            content="hi",
            reasoning="",
            reasoning_content="",
        )

        stored = db.get_messages("s1")
        assert stored[0]["reasoning"] == ""
        assert stored[0]["reasoning_content"] == ""

        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 1
        assert "reasoning" not in conv[0]
        assert conv[0]["reasoning_content"] == ""
        # Reading the model-facing projection must not mutate the Vault record.
        assert db.get_messages("s1")[0]["reasoning"] == ""

    def test_codex_reasoning_items_persisted_and_restored(self, db):
        """codex_reasoning_items (encrypted blobs for Codex Responses API) are
        round-tripped through JSON serialization in the DB."""
        db.create_session(session_id="s1", source="cli")
        codex_items = [
            {"type": "reasoning", "id": "rs_abc", "encrypted_content": "enc_blob_123"},
            {"type": "reasoning", "id": "rs_def", "encrypted_content": "enc_blob_456"},
        ]
        db.append_message(
            "s1",
            role="assistant",
            content="Done",
            codex_reasoning_items=codex_items,
        )

        conv = db.get_messages_as_conversation("s1")
        assert len(conv) == 1
        assert conv[0]["codex_reasoning_items"] == codex_items
        assert conv[0]["codex_reasoning_items"][0]["encrypted_content"] == "enc_blob_123"


# =========================================================================
# FTS5 search
# =========================================================================

class TestFTS5Search:
    def test_search_finds_content(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="How do I deploy with Docker?")
        db.append_message("s1", role="assistant", content="Use docker compose up.")

        results = db.search_messages("docker")
        assert len(results) == 2
        # At least one result should mention docker
        snippets = [r.get("snippet", "") for r in results]
        assert any("docker" in s.lower() or "Docker" in s for s in snippets)

    def test_search_empty_query(self, db):
        assert db.search_messages("") == []
        assert db.search_messages("   ") == []

    def test_search_with_source_filter(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="CLI question about Python")

        db.create_session(session_id="s2", source="telegram")
        db.append_message("s2", role="user", content="Telegram question about Python")

        results = db.search_messages("Python", source_filter=["telegram"])
        # Should only find the telegram message
        sources = [r["source"] for r in results]
        assert all(s == "telegram" for s in sources)

    def test_search_default_sources_include_acp(self, db):
        db.create_session(session_id="s1", source="acp")
        db.append_message("s1", role="user", content="ACP question about Python")

        results = db.search_messages("Python")
        sources = [r["source"] for r in results]
        assert "acp" in sources

    def test_search_default_includes_all_platforms(self, db):
        """Default search (no source_filter) should find sessions from any platform."""
        for src in ("cli", "telegram", "signal", "homeassistant", "acp", "matrix"):
            sid = f"s-{src}"
            db.create_session(session_id=sid, source=src)
            db.append_message(sid, role="user", content=f"universal search test from {src}")

        results = db.search_messages("universal search test")
        found_sources = {r["source"] for r in results}
        assert found_sources == {"cli", "telegram", "signal", "homeassistant", "acp", "matrix"}

    def test_search_with_role_filter(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="What is FastAPI?")
        db.append_message("s1", role="assistant", content="FastAPI is a web framework.")

        results = db.search_messages("FastAPI", role_filter=["assistant"])
        roles = [r["role"] for r in results]
        assert all(r == "assistant" for r in roles)

    def test_search_returns_context(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Tell me about Kubernetes")
        db.append_message("s1", role="assistant", content="Kubernetes is an orchestrator.")

        results = db.search_messages("Kubernetes")
        assert len(results) == 2
        assert "context" in results[0]
        assert isinstance(results[0]["context"], list)
        assert len(results[0]["context"]) > 0

    def test_search_context_uses_session_neighbors_when_ids_are_interleaved(self, db):
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="cli")

        db.append_message("s1", role="user", content="before needle")
        db.append_message("s2", role="user", content="other session message")
        db.append_message("s1", role="assistant", content="needle match")
        db.append_message("s2", role="assistant", content="another other session message")
        db.append_message("s1", role="user", content="after needle")

        results = db.search_messages('"needle match"')
        needle_result = next(r for r in results if r["session_id"] == "s1" and "needle match" in r["snippet"])

        assert [msg["content"] for msg in needle_result["context"]] == [
            "before needle",
            "needle match",
            "after needle",
        ]

    def test_search_quoted_phrase_preserves_exact_sequence(self, db):
        """Quoted FTS-style phrases still work with the SQLite-free Vault search."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="needle match")
        db.append_message("s1", role="assistant", content="needle separated by a match")
        db.append_message("s1", role="user", content="NEEDLE MATCH")

        results = db.search_messages('"needle match"')
        assert {result["snippet"] for result in results} == {
            "needle match", "NEEDLE MATCH",
        }
        assert db.search_messages('""') == []

    def test_search_special_chars_do_not_crash(self, db):
        """FTS5 special characters in queries must not raise OperationalError."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="How do I use C++ templates?")

        # Each of these previously caused sqlite3.OperationalError
        dangerous_queries = [
            'C++',              # + is FTS5 column filter
            '"unterminated',    # unbalanced double-quote
            '(problem',         # unbalanced parenthesis
            'hello AND',        # dangling boolean operator
            '***',              # repeated wildcard
            '{test}',           # curly braces (column reference)
            'OR hello',         # leading boolean operator
            'a AND OR b',       # adjacent operators
        ]
        for query in dangerous_queries:
            # Must not raise — should return list (possibly empty)
            results = db.search_messages(query)
            assert isinstance(results, list), f"Query {query!r} did not return a list"

    def test_search_sanitized_query_still_finds_content(self, db):
        """Sanitization must not break normal keyword search."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Learning C++ templates today")

        # "C++" sanitized to "C" should still match "C++"
        results = db.search_messages("C++")
        # The word "C" appears in the content, so FTS5 should find it
        assert isinstance(results, list)

    def test_search_hyphenated_term_does_not_crash(self, db):
        """Hyphenated terms like 'chat-send' must not crash FTS5."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Run the chat-send command")

        results = db.search_messages("chat-send")
        assert isinstance(results, list)
        assert len(results) >= 1
        assert any("chat-send" in (r.get("snippet") or r.get("content", "")).lower()
                    for r in results)

    def test_search_dotted_term_does_not_crash(self, db):
        """Dotted terms like 'P2.2' or 'simulate.p2.test.ts' should not crash FTS5."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Working on P2.2 session_search edge cases")
        db.append_message("s1", role="assistant", content="See simulate.p2.test.ts for details")

        results = db.search_messages("P2.2")
        assert isinstance(results, list)
        assert len(results) >= 1

        results2 = db.search_messages("simulate.p2.test.ts")
        assert isinstance(results2, list)
        assert len(results2) >= 1

    def test_search_quoted_phrase_preserved(self, db):
        """User-provided quoted phrases should be preserved for exact matching."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="docker networking is complex")
        db.append_message("s1", role="assistant", content="networking docker tips")

        # Quoted phrase should match only the exact order
        results = db.search_messages('"docker networking"')
        assert isinstance(results, list)
        # Should find the user message (exact phrase) but may or may not find
        # the assistant message depending on FTS5 phrase matching
        assert len(results) >= 1

    def test_vault_search_treats_fts_syntax_as_literal(self, db):
        """Vault search matches punctuation literally, without SQL parsing."""
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1", role="user",
            content='C++ "unterminated (problem {test} deploy* ***',
        )
        for query in ("C++", '"unterminated', "(problem", "{test}",
                      "deploy*", "***"):
            assert len(db.search_messages(query)) == 1, query
        assert db.search_messages("missing++") == []

    def test_vault_search_preserves_quoted_or_phrase(self, db):
        """OR inside a fully quoted phrase is text, not an operator."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="hello OR world")
        db.append_message("s1", role="assistant", content="hello world")
        assert [r["snippet"] for r in db.search_messages('"hello OR world"')] == [
            "hello OR world",
        ]

    def test_vault_search_hyphenated_terms(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Run chat-send and docker-compose-up")
        assert len(db.search_messages("chat-send")) == 1
        assert len(db.search_messages("docker-compose-up")) == 1
        assert db.search_messages("chat_send") == []

    def test_vault_search_dotted_terms(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="P2.2 uses my-app.config.ts")
        assert len(db.search_messages("P2.2")) == 1
        assert len(db.search_messages("my-app.config.ts")) == 1
        assert db.search_messages("P2-2") == []

    def test_vault_search_underscored_terms(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="sp_new1 docker-compose_up")
        assert len(db.search_messages("sp_new1")) == 1
        assert len(db.search_messages("docker-compose_up")) == 1
        assert db.search_messages("sp-new1") == []

    def test_vault_search_or_union_and_casefold(self, db):
        """Unquoted OR finds either phrase and matching is case-insensitive."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="HELLO WORLD")
        db.append_message("s1", role="assistant", content="goodbye moon")
        db.append_message("s1", role="user", content="unrelated")
        results = db.search_messages("hello world OR goodbye moon")
        assert {r["snippet"] for r in results} == {
            "HELLO WORLD", "goodbye moon",
        }


# =========================================================================
# CJK (Chinese/Japanese/Korean) LIKE fallback
# =========================================================================

class TestCJKSearchFallback:
    """Regression tests for CJK substring search using the SQLite-free Vault."""

    def test_vault_cjk_substrings_cover_all_scripts(self, db):
        """Chinese, Japanese, Korean and mixed strings need no SQL fallback."""
        db.create_session(session_id="s1", source="cli")
        for content in ("昨天的记忆断裂问题", "こんにちは世界", "カタカナ",
                        "안녕하세요", "日本語mixedwithenglish"):
            db.append_message("s1", role="user", content=content)
        for query in ("记忆断裂", "こんにちは", "カタカナ", "안녕",
                      "日本語mixed"):
            assert len(db.search_messages(query)) == 1, query
        assert db.search_messages("") == []

    def test_chinese_multichar_query_returns_results(self, db):
        """The headline bug: multi-char Chinese query must not return []."""
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1", role="user",
            content="昨天和其他Agent的聊天记录，记忆断裂问题复现了",
        )
        results = db.search_messages("记忆断裂")
        assert len(results) == 1
        assert results[0]["session_id"] == "s1"

    def test_chinese_bigram_query(self, db):
        db.create_session(session_id="s1", source="telegram")
        db.append_message("s1", role="user", content="今天讨论A2A通信协议的实现")
        results = db.search_messages("通信")
        assert len(results) == 1

    def test_korean_query_returns_results(self, db):
        """Guards against Hangul range typos (\\uac00-\\ud7af, not \\ud7a0-)."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="안녕하세요 반갑습니다")
        results = db.search_messages("안녕")
        assert len(results) == 1

    def test_japanese_query_returns_results(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="こんにちは世界")
        assert len(db.search_messages("こんにちは")) == 1
        assert len(db.search_messages("世界")) == 1

    def test_cjk_fallback_preserves_source_filter(self, db):
        """Guards against the SQL-builder bug where filter clauses land
        after LIMIT/OFFSET (seen in one of the duplicate PRs)."""
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="telegram")
        db.append_message("s1", role="user", content="记忆断裂在CLI")
        db.append_message("s2", role="user", content="记忆断裂在Telegram")

        results = db.search_messages("记忆断裂", source_filter=["telegram"])
        assert len(results) == 1
        assert results[0]["source"] == "telegram"

    def test_cjk_fallback_preserves_exclude_sources(self, db):
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="tool")
        db.append_message("s1", role="user", content="记忆断裂在CLI")
        db.append_message("s2", role="assistant", content="记忆断裂在tool")

        results = db.search_messages("记忆断裂", exclude_sources=["tool"])
        sources = {r["source"] for r in results}
        assert "tool" not in sources
        assert "cli" in sources

    def test_cjk_fallback_preserves_role_filter(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="用户说的记忆断裂")
        db.append_message("s1", role="assistant", content="助手说的记忆断裂")

        results = db.search_messages("记忆断裂", role_filter=["assistant"])
        assert len(results) == 1
        assert results[0]["role"] == "assistant"

    def test_cjk_snippet_is_centered_on_match(self, db):
        """Snippet should contain the search term, not just the first N chars."""
        db.create_session(session_id="s1", source="cli")
        long_prefix = "这是一段很长的前缀用来把匹配位置推到文档中间" * 3
        long_suffix = "这是一段很长的后缀内容填充剩余空间" * 3
        db.append_message(
            "s1", role="user",
            content=f"{long_prefix}记忆断裂{long_suffix}",
        )
        results = db.search_messages("记忆断裂")
        assert len(results) == 1
        # The centered substr() snippet must include the matched term.
        assert "记忆断裂" in results[0]["snippet"]

    def test_english_query_still_uses_fts5_fast_path(self, db):
        """English queries must not trigger the LIKE fallback (fast path regression)."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Deploy docker containers")
        results = db.search_messages("docker")
        assert len(results) == 1
        # No CJK in query → LIKE fallback must not run. We don't assert this
        # directly (no instrumentation), but the FTS5 path produces an
        # FTS5-style snippet with highlight markers when the term is short.
        # At minimum: english queries must still match.

    def test_cjk_query_with_no_matches_returns_empty(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="unrelated English content")
        results = db.search_messages("记忆断裂")
        assert results == []

    def test_mixed_cjk_english_query(self, db):
        """Mixed queries should still fall back to LIKE when FTS5 misses."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="讨论Agent通信协议")
        # "Agent通信" is CJK+English — FTS5 default tokenizer indexes the
        # whole CJK run with embedded "agent" as separate tokens; the LIKE
        # fallback handles the substring correctly.
        results = db.search_messages("Agent通信")
        assert len(results) == 1

    def test_cjk_partial_fts5_results_supplemented_by_like(self, db):
        """When FTS5 returns *some* CJK results, LIKE must still find all matches.

        Regression test for #15500 / #14829: FTS5 unicode61 tokenizer drops
        certain CJK characters, so multi-character queries may return partial
        results.  The LIKE path must always run for CJK queries.
        """
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="telegram")
        db.append_message("s1", role="user", content="昨晚讨论了记忆系统")
        db.append_message("s2", role="user", content="昨晚的会议纪要已发送")
        results = db.search_messages("昨晚")
        assert len(results) == 2
        session_ids = {r["session_id"] for r in results}
        assert session_ids == {"s1", "s2"}

    def test_cjk_like_dedup_no_duplicates(self, db):
        """When FTS5 and LIKE both find the same message, no duplicates."""
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="测试去重逻辑")
        results = db.search_messages("测试")
        assert len(results) == 1

    def test_cjk_like_escapes_wildcards(self, db):
        """Special characters (%, _) in CJK queries are treated as literals."""
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="cli")
        db.append_message("s1", role="user", content="达成100%完成率")
        db.append_message("s2", role="user", content="达成100完成率是目标")
        # The % in the query must be literal — should only match s1
        results = db.search_messages("100%完成")
        assert len(results) == 1
        assert results[0]["session_id"] == "s1"

    def test_cjk_trigram_preserves_boolean_operators(self, db):
        """Boolean operators (OR, AND, NOT) work in CJK trigram queries."""
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="cli")
        db.append_message("s1", role="user", content="记忆系统很好用")
        db.append_message("s2", role="user", content="断裂连接需要修复")
        results = db.search_messages("记忆系统 OR 断裂连接")
        assert len(results) == 2
        session_ids = {r["session_id"] for r in results}
        assert session_ids == {"s1", "s2"}


# =========================================================================
# Session search and listing
# =========================================================================

class TestSearchSessions:
    def test_list_all_sessions(self, db):
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="telegram")

        sessions = db.search_sessions()
        assert len(sessions) == 2

    def test_filter_by_source(self, db):
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="telegram")

        sessions = db.search_sessions(source="cli")
        assert len(sessions) == 1
        assert sessions[0]["source"] == "cli"

    def test_pagination(self, db):
        for i in range(5):
            db.create_session(session_id=f"s{i}", source="cli")

        page1 = db.search_sessions(limit=2)
        page2 = db.search_sessions(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["id"] != page2[0]["id"]


# =========================================================================
# Counts
# =========================================================================

class TestCounts:
    def test_session_count(self, db):
        assert db.session_count() == 0
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="telegram")
        assert db.session_count() == 2

    def test_session_count_by_source(self, db):
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="telegram")
        db.create_session(session_id="s3", source="cli")
        assert db.session_count(source="cli") == 2
        assert db.session_count(source="telegram") == 1

    def test_message_count_total(self, db):
        assert db.message_count() == 0
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Hello")
        db.append_message("s1", role="assistant", content="Hi")
        assert db.message_count() == 2

    def test_message_count_per_session(self, db):
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="cli")
        db.append_message("s1", role="user", content="A")
        db.append_message("s2", role="user", content="B")
        db.append_message("s2", role="user", content="C")
        assert db.message_count(session_id="s1") == 1
        assert db.message_count(session_id="s2") == 2


# =========================================================================
# Delete and export
# =========================================================================

class TestDeleteAndExport:
    def test_delete_session(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message("s1", role="user", content="Hello")

        assert db.delete_session("s1") is True
        assert db.get_session("s1") is None
        assert db.message_count(session_id="s1") == 0

    def test_delete_nonexistent(self, db):
        assert db.delete_session("nope") is False

    def test_resolve_session_id_exact(self, db):
        db.create_session(session_id="20260315_092437_c9a6ff", source="cli")
        assert db.resolve_session_id("20260315_092437_c9a6ff") == "20260315_092437_c9a6ff"

    def test_resolve_session_id_unique_prefix(self, db):
        db.create_session(session_id="20260315_092437_c9a6ff", source="cli")
        assert db.resolve_session_id("20260315_092437_c9a6") == "20260315_092437_c9a6ff"

    def test_resolve_session_id_ambiguous_prefix_returns_none(self, db):
        db.create_session(session_id="20260315_092437_c9a6aa", source="cli")
        db.create_session(session_id="20260315_092437_c9a6bb", source="cli")
        assert db.resolve_session_id("20260315_092437_c9a6") is None

    def test_resolve_session_id_escapes_like_wildcards(self, db):
        db.create_session(session_id="20260315_092437_c9a6ff", source="cli")
        db.create_session(session_id="20260315X092437_c9a6ff", source="cli")
        assert db.resolve_session_id("20260315_092437") == "20260315_092437_c9a6ff"

    def test_export_session(self, db):
        db.create_session(session_id="s1", source="cli", model="test")
        db.append_message("s1", role="user", content="Hello")
        db.append_message("s1", role="assistant", content="Hi")

        export = db.export_session("s1")
        assert isinstance(export, dict)
        assert export["source"] == "cli"
        assert len(export["messages"]) == 2

    def test_export_nonexistent(self, db):
        assert db.export_session("nope") is None

    def test_export_all(self, db):
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="telegram")
        db.append_message("s1", role="user", content="A")

        exports = db.export_all()
        assert len(exports) == 2

    def test_export_all_with_source(self, db):
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="telegram")

        exports = db.export_all(source="cli")
        assert len(exports) == 1
        assert exports[0]["source"] == "cli"


# =========================================================================
# Prune
# =========================================================================

class TestPruneSessions:
    def test_prune_old_ended_sessions(self, db):
        # Create and end an "old" session
        db.create_session(session_id="old", source="cli")
        db.end_session("old", end_reason="done")
        # Backdate only this temporary Vault fixture, without a SQL connection.
        db._edit("old", lambda item: item.update(started_at=time.time() - 100 * 86400))

        # Create a recent session
        db.create_session(session_id="new", source="cli")

        pruned = db.prune_sessions(older_than_days=90)
        assert pruned == 1
        assert db.get_session("old") is None
        session = db.get_session("new")
        assert session is not None
        assert session["id"] == "new"

    def test_prune_skips_active_sessions(self, db):
        db.create_session(session_id="active", source="cli")
        # Active sessions must survive even when their Vault timestamp is old.
        db._edit("active", lambda item: item.update(started_at=time.time() - 200 * 86400))

        pruned = db.prune_sessions(older_than_days=90)
        assert pruned == 0
        assert db.get_session("active") is not None

    def test_prune_with_source_filter(self, db):
        for sid, src in [("old_cli", "cli"), ("old_tg", "telegram")]:
            db.create_session(session_id=sid, source=src)
            db.end_session(sid, end_reason="done")
            db._edit(sid, lambda item: item.update(started_at=time.time() - 200 * 86400))

        pruned = db.prune_sessions(older_than_days=90, source="cli")
        assert pruned == 1
        assert db.get_session("old_cli") is None
        assert db.get_session("old_tg") is not None

    def test_prune_with_multilevel_chain(self, db):
        """Pruning old sessions orphans newer children instead of crashing on FK."""
        old_ts = time.time() - 200 * 86400
        recent_ts = time.time() - 10 * 86400

        # Chain: A (old) -> B (old) -> C (recent) -> D (recent)
        db.create_session(session_id="A", source="cli")
        db.end_session("A", end_reason="compressed")
        db.create_session(session_id="B", source="cli", parent_session_id="A")
        db.end_session("B", end_reason="compressed")
        db.create_session(session_id="C", source="cli", parent_session_id="B")
        db.end_session("C", end_reason="compressed")
        db.create_session(session_id="D", source="cli", parent_session_id="C")
        db.end_session("D", end_reason="done")

        # Backdate A and B to be old; C and D stay recent
        for sid, ts in [("A", old_ts), ("B", old_ts), ("C", recent_ts), ("D", recent_ts)]:
            db._edit(sid, lambda item, ts=ts: item.update(started_at=ts))

        # Pruning must not leave a dangling parent link
        pruned = db.prune_sessions(older_than_days=90)
        assert pruned == 2  # only A and B
        assert db.get_session("A") is None
        assert db.get_session("B") is None
        # C and D survive, C is orphaned in the Vault
        c = db.get_session("C")
        assert c is not None
        assert c["parent_session_id"] is None
        d = db.get_session("D")
        assert d is not None
        assert d["parent_session_id"] == "C"

    def test_prune_entire_old_chain(self, db):
        """All sessions in a chain are old — entire chain is pruned."""
        old_ts = time.time() - 200 * 86400

        db.create_session(session_id="X", source="cli")
        db.end_session("X", end_reason="compressed")
        db.create_session(session_id="Y", source="cli", parent_session_id="X")
        db.end_session("Y", end_reason="compressed")
        db.create_session(session_id="Z", source="cli", parent_session_id="Y")
        db.end_session("Z", end_reason="done")

        for sid in ("X", "Y", "Z"):
            db._edit(sid, lambda item: item.update(started_at=old_ts))

        pruned = db.prune_sessions(older_than_days=90)
        assert pruned == 3
        for sid in ("X", "Y", "Z"):
            assert db.get_session(sid) is None


    def test_prune_surviving_child_persists_without_sqlite(self, db):
        """Pruning a parent preserves the child's transcript and orphan state."""
        db.create_session(session_id="parent", source="cli")
        db.end_session("parent", end_reason="compressed")
        db._edit("parent", lambda item: item.update(started_at=time.time() - 200 * 86400))
        db.create_session(session_id="child", source="cli", parent_session_id="parent")
        db.append_message("child", role="user", content="Keep this message")

        assert db.prune_sessions(older_than_days=90) == 1
        reopened = SessionDB(vault_path=db.vault)
        try:
            assert reopened.get_session("parent") is None
            assert reopened.get_session("child")["parent_session_id"] is None
            assert reopened.get_messages("child")[0]["content"] == "Keep this message"
            assert not list(db.vault.rglob("*.db"))
            assert not list(db.vault.rglob("*.sqlite*"))
        finally:
            reopened.close()


class TestDeleteSessionOrphansChildren:
    def test_delete_orphans_children(self, db):
        """Deleting a parent session orphans its children."""
        db.create_session(session_id="parent", source="cli")
        db.create_session(session_id="child", source="cli", parent_session_id="parent")
        db.create_session(session_id="grandchild", source="cli", parent_session_id="child")

        # Should not raise IntegrityError
        result = db.delete_session("parent")
        assert result is True
        assert db.get_session("parent") is None
        # Child is orphaned, not deleted
        child = db.get_session("child")
        assert child is not None
        assert child["parent_session_id"] is None
        # Grandchild is untouched
        grandchild = db.get_session("grandchild")
        assert grandchild is not None
        assert grandchild["parent_session_id"] == "child"


# =========================================================================
# Schema and WAL mode
# =========================================================================

# =========================================================================
# Session title
# =========================================================================

class TestSessionTitle:
    def test_set_and_get_title(self, db):
        db.create_session(session_id="s1", source="cli")
        assert db.set_session_title("s1", "My Session") is True

        session = db.get_session("s1")
        assert session["title"] == "My Session"

    def test_set_title_nonexistent_session(self, db):
        assert db.set_session_title("nonexistent", "Title") is False

    def test_title_initially_none(self, db):
        db.create_session(session_id="s1", source="cli")
        session = db.get_session("s1")
        assert session["title"] is None

    def test_update_title(self, db):
        db.create_session(session_id="s1", source="cli")
        db.set_session_title("s1", "First Title")
        db.set_session_title("s1", "Updated Title")

        session = db.get_session("s1")
        assert session["title"] == "Updated Title"

    def test_title_in_search_sessions(self, db):
        db.create_session(session_id="s1", source="cli")
        db.set_session_title("s1", "Debugging Auth")
        db.create_session(session_id="s2", source="cli")

        sessions = db.search_sessions()
        titled = [s for s in sessions if s.get("title") == "Debugging Auth"]
        assert len(titled) == 1
        assert titled[0]["id"] == "s1"

    def test_title_in_export(self, db):
        db.create_session(session_id="s1", source="cli")
        db.set_session_title("s1", "Export Test")
        db.append_message("s1", role="user", content="Hello")

        export = db.export_session("s1")
        assert export["title"] == "Export Test"

    def test_title_with_special_characters(self, db):
        db.create_session(session_id="s1", source="cli")
        title = "PR #438 — fixing the 'auth' middleware"
        db.set_session_title("s1", title)

        session = db.get_session("s1")
        assert session["title"] == title

    def test_title_empty_string_normalized_to_none(self, db):
        """Empty strings are normalized to None (clearing the title)."""
        db.create_session(session_id="s1", source="cli")
        db.set_session_title("s1", "My Title")
        # Setting to empty string should clear the title (normalize to None)
        db.set_session_title("s1", "")

        session = db.get_session("s1")
        assert session["title"] is None

    def test_multiple_empty_titles_no_conflict(self, db):
        """Multiple sessions can have empty-string (normalized to NULL) titles."""
        db.create_session(session_id="s1", source="cli")
        db.create_session(session_id="s2", source="cli")
        db.set_session_title("s1", "")
        db.set_session_title("s2", "")
        # Both should be None, no uniqueness conflict
        assert db.get_session("s1")["title"] is None
        assert db.get_session("s2")["title"] is None

    def test_title_survives_end_session(self, db):
        db.create_session(session_id="s1", source="cli")
        db.set_session_title("s1", "Before End")
        db.end_session("s1", end_reason="user_exit")

        session = db.get_session("s1")
        assert session["title"] == "Before End"
        assert session["ended_at"] is not None


class TestSanitizeTitle:
    """Tests for SessionDB.sanitize_title() validation and cleaning."""

    def test_normal_title_unchanged(self):
        assert SessionDB.sanitize_title("My Project") == "My Project"

    def test_strips_whitespace(self):
        assert SessionDB.sanitize_title("  hello world  ") == "hello world"

    def test_collapses_internal_whitespace(self):
        assert SessionDB.sanitize_title("hello   world") == "hello world"

    def test_tabs_and_newlines_collapsed(self):
        assert SessionDB.sanitize_title("hello\t\nworld") == "hello world"

    def test_none_returns_none(self):
        assert SessionDB.sanitize_title(None) is None

    def test_empty_string_returns_none(self):
        assert SessionDB.sanitize_title("") is None

    def test_whitespace_only_returns_none(self):
        assert SessionDB.sanitize_title("   \t\n  ") is None

    def test_control_chars_stripped(self):
        # Null byte, bell, backspace, etc.
        assert SessionDB.sanitize_title("hello\x00world") == "helloworld"
        assert SessionDB.sanitize_title("\x07\x08test\x1b") == "test"

    def test_del_char_stripped(self):
        assert SessionDB.sanitize_title("hello\x7fworld") == "helloworld"

    def test_zero_width_chars_stripped(self):
        # Zero-width space (U+200B), zero-width joiner (U+200D)
        assert SessionDB.sanitize_title("hello\u200bworld") == "helloworld"
        assert SessionDB.sanitize_title("hello\u200dworld") == "helloworld"

    def test_rtl_override_stripped(self):
        # Right-to-left override (U+202E) — used in filename spoofing attacks
        assert SessionDB.sanitize_title("hello\u202eworld") == "helloworld"

    def test_bom_stripped(self):
        # Byte order mark (U+FEFF)
        assert SessionDB.sanitize_title("\ufeffhello") == "hello"

    def test_only_control_chars_returns_none(self):
        assert SessionDB.sanitize_title("\x00\x01\x02\u200b\ufeff") is None

    def test_max_length_allowed(self):
        title = "A" * 100
        assert SessionDB.sanitize_title(title) == title

    def test_exceeds_max_length_raises(self):
        title = "A" * 101
        with pytest.raises(ValueError, match="too long"):
            SessionDB.sanitize_title(title)

    def test_unicode_emoji_allowed(self):
        assert SessionDB.sanitize_title("🚀 My Project 🎉") == "🚀 My Project 🎉"

    def test_cjk_characters_allowed(self):
        assert SessionDB.sanitize_title("我的项目") == "我的项目"

    def test_accented_characters_allowed(self):
        assert SessionDB.sanitize_title("Résumé éditing") == "Résumé éditing"

    def test_special_punctuation_allowed(self):
        title = "PR #438 — fixing the 'auth' middleware"
        assert SessionDB.sanitize_title(title) == title

    def test_sanitize_applied_in_set_session_title(self, db):
        """set_session_title applies sanitize_title internally."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "  hello\x00  world  ")
        assert db.get_session("s1")["title"] == "hello world"

    def test_too_long_title_rejected_by_set(self, db):
        """set_session_title raises ValueError for overly long titles."""
        db.create_session("s1", "cli")
        with pytest.raises(ValueError, match="too long"):
            db.set_session_title("s1", "X" * 150)


class TestSchemaInit:
    """Vault persistence invariants replacing obsolete SQLite schema checks."""

    def test_vault_only_backend_does_not_create_sqlite(self, db):
        assert db.db_path == db.root
        assert db.root.is_relative_to(db.vault)
        db.create_session("vault-only", "cli")
        db.append_message("vault-only", "user", "hello")
        assert (db.root / "vault-only" / "metadata.json").is_file()
        assert (db.root / "vault-only" / "Transcript.md").is_file()
        assert not any(
            path.name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"))
            for path in db.vault.rglob("*")
            if path.is_file()
        )

    def test_vault_session_survives_reopen(self, db):
        db.create_session("reopen", "cli", model="test-model")
        db.append_message("reopen", "user", "persist me")
        reopened = SessionDB(vault_path=db.vault)
        assert reopened.get_session("reopen")["model"] == "test-model"
        assert reopened.get_messages("reopen")[0]["content"] == "persist me"
        reopened.close()

    def test_vault_parent_reference_survives_reopen(self, db):
        db.create_session("parent", "cli")
        db.create_session("child", "cli", parent_session_id="parent")
        reopened = SessionDB(vault_path=db.vault)
        assert reopened.get_session("child")["parent_session_id"] == "parent"
        assert reopened.get_session("parent") is not None
        reopened.close()

    def test_vault_metadata_fields_are_persisted(self, db):
        db.create_session("metadata", "cli")
        db.set_session_title("metadata", "Vault Title")
        metadata_path = db.root / "metadata" / "metadata.json"
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert payload["id"] == "metadata"
        assert payload["title"] == "Vault Title"
        assert payload["messages"] == []
        assert db.get_session("metadata")["title"] == "Vault Title"

    def test_vault_transcript_is_readable_projection(self, db):
        db.create_session("transcript", "cli")
        db.append_message("transcript", "user", "Japanese: 日本語")
        folder = db.root / "transcript"
        canonical = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        transcript = (folder / "Transcript.md").read_text(encoding="utf-8")
        assert canonical["messages"][0]["content"] == "Japanese: 日本語"
        assert "## user" in transcript
        assert "Japanese: 日本語" in transcript

    def test_legacy_sqlite_path_is_not_opened_or_migrated(self, tmp_path):
        """A legacy db_path must never trigger SQLite I/O or mutate old data."""
        legacy_db = tmp_path / "legacy.db"
        legacy_bytes = b"legacy SQLite fixture - must stay untouched"
        legacy_db.write_bytes(legacy_bytes)
        vault = tmp_path / "vault"
        vault.mkdir()

        db = SessionDB(db_path=legacy_db, vault_path=vault)
        assert db.db_path == db.root
        assert db.root.is_relative_to(vault)
        assert not (db.root / "state_meta.json").exists()
        assert db.get_telegram_topic_binding("208214988", "17585") is None
        assert not (db.root / "state_meta.json").exists()
        db.create_session("vault-only", "telegram", user_id="208214988")
        db.close()

        reopened = SessionDB(db_path=legacy_db, vault_path=vault)
        assert reopened.get_session("vault-only")["user_id"] == "208214988"
        assert legacy_db.read_bytes() == legacy_bytes
        reopened.close()

    def test_apply_telegram_topic_migration_is_vault_noop(self, tmp_path):
        """The legacy migration API must not rewrite existing Vault state."""
        vault = tmp_path / "vault"
        vault.mkdir()
        db = SessionDB(vault_path=vault)
        db.create_session("topic-session", "telegram", user_id="208214988")
        db.enable_telegram_topic_mode("208214988", "208214988")
        db.bind_telegram_topic(
            chat_id="208214988",
            thread_id="17585",
            user_id="208214988",
            session_key="telegram:dm:208214988:thread:17585",
            session_id="topic-session",
        )
        state_file = db.root / "state_meta.json"
        before = state_file.read_bytes()
        assert db.apply_telegram_topic_migration() is None
        assert state_file.read_bytes() == before
        db.close()

        reopened = SessionDB(vault_path=vault)
        assert reopened.is_telegram_topic_mode_enabled("208214988", "208214988")
        assert reopened.get_telegram_topic_binding("208214988", "17585")["session_id"] == "topic-session"
        reopened.close()

    def test_telegram_topic_binding_roundtrip_persists_vault_metadata(self, tmp_path):
        """Bindings are stored and restored in Vault JSON, not SQL tables."""
        vault = tmp_path / "vault"
        vault.mkdir()
        db = SessionDB(vault_path=vault)
        db.create_session(
            session_id="topic-session",
            source="telegram",
            user_id="208214988",
        )

        assert db.get_telegram_topic_binding(chat_id="208214988", thread_id="17585") is None

        db.bind_telegram_topic(
            chat_id="208214988",
            thread_id="17585",
            user_id="208214988",
            session_key="telegram:dm:208214988:thread:17585",
            session_id="topic-session",
        )

        binding = db.get_telegram_topic_binding(chat_id="208214988", thread_id="17585")
        assert binding is not None
        assert binding["chat_id"] == "208214988"
        assert binding["thread_id"] == "17585"
        assert binding["user_id"] == "208214988"
        assert binding["session_key"] == "telegram:dm:208214988:thread:17585"
        assert binding["session_id"] == "topic-session"
        state_file = db.root / "state_meta.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["telegram_topic_bindings"]["208214988"]["17585"]["session_id"] == "topic-session"
        assert not any(p.suffix in (".db", ".sqlite", ".sqlite3") for p in vault.rglob("*"))
        db.close()

        reopened = SessionDB(vault_path=vault)
        assert reopened.get_telegram_topic_binding("208214988", "17585") == binding
        reopened.close()

    def test_telegram_topic_binding_refuses_to_relink_session_to_another_topic(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session(
            session_id="topic-session",
            source="telegram",
            user_id="208214988",
        )
        db.bind_telegram_topic(
            chat_id="208214988",
            thread_id="17585",
            user_id="208214988",
            session_key="key-17585",
            session_id="topic-session",
        )

        with pytest.raises(ValueError, match="already linked"):
            db.bind_telegram_topic(
                chat_id="208214988",
                thread_id="99999",
                user_id="208214988",
                session_key="key-99999",
                session_id="topic-session",
            )
        db.close()

    def test_list_unlinked_telegram_sessions_for_user_excludes_bound_and_other_users(self, tmp_path):
        db = SessionDB(db_path=tmp_path / "state.db")
        db.create_session(
            session_id="old-unlinked",
            source="telegram",
            user_id="208214988",
        )
        db.set_session_title("old-unlinked", "Old research")
        db.append_message("old-unlinked", "user", "first prompt")
        db.create_session(
            session_id="already-linked",
            source="telegram",
            user_id="208214988",
        )
        db.bind_telegram_topic(
            chat_id="208214988",
            thread_id="17585",
            user_id="208214988",
            session_key="key-17585",
            session_id="already-linked",
        )
        db.create_session(
            session_id="other-user",
            source="telegram",
            user_id="someone-else",
        )

        sessions = db.list_unlinked_telegram_sessions_for_user(
            chat_id="208214988",
            user_id="208214988",
        )

        assert [s["id"] for s in sessions] == ["old-unlinked"]
        assert sessions[0]["title"] == "Old research"
        assert sessions[0]["preview"] == "first prompt"
        db.close()

    def test_legacy_v2_sqlite_is_preserved_without_implicit_import(self, tmp_path):
        """Opening the Vault backend must not mutate or silently import legacy SQLite.

        An explicit, separately validated migration is still required.
        """
        import sqlite3

        db_path = tmp_path / "legacy-v2.db"
        with sqlite3.connect(db_path) as conn:
            conn.executescript("""
                CREATE TABLE schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version VALUES (2);
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at REAL NOT NULL
                );
                INSERT INTO sessions VALUES ('existing', 'cli', 1000.0);
            """)
        original = db_path.read_bytes()
        vault = tmp_path / "vault"
        vault.mkdir()

        db = SessionDB(db_path=db_path, vault_path=vault)
        assert db.get_session("existing") is None
        db.create_session("vault-session", "cli")
        assert db.set_session_title("vault-session", "Vault Title") is True
        db.close()

        assert db_path.read_bytes() == original
        reopened = SessionDB(db_path=db_path, vault_path=vault)
        assert reopened.get_session("existing") is None
        assert reopened.get_session("vault-session")["title"] == "Vault Title"
        reopened.close()

    def test_legacy_v7_schema_gap_is_not_modified_by_vault(self, tmp_path):
        """A legacy DB missing reasoning_content remains untouched on Vault open."""
        import sqlite3

        db_path = tmp_path / "legacy-v7.db"
        with sqlite3.connect(db_path) as conn:
            conn.executescript("""
                CREATE TABLE schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version VALUES (7);
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    reasoning TEXT
                );
                INSERT INTO messages (session_id, role, content)
                VALUES ('old', 'assistant', 'legacy message');
            """)
        original = db_path.read_bytes()
        vault = tmp_path / "vault"
        vault.mkdir()

        db = SessionDB(db_path=db_path, vault_path=vault)
        assert db.get_messages("old") == []
        db.create_session("new", "cli")
        db.append_message(
            "new", "assistant", "new message", reasoning_content="preserved reasoning"
        )
        db.close()

        assert db_path.read_bytes() == original
        reopened = SessionDB(db_path=db_path, vault_path=vault)
        messages = reopened.get_messages("new")
        assert len(messages) == 1
        assert messages[0]["reasoning_content"] == "preserved reasoning"
        reopened.close()

    def test_vault_reopen_is_idempotent(self, tmp_path):
        """Reopening a Vault does not rewrite metadata or duplicate messages."""
        vault = tmp_path / "vault"
        vault.mkdir()
        db = SessionDB(vault_path=vault)
        db.create_session("s1", "cli")
        db.append_message("s1", "user", "hello")
        metadata = db.root / "s1" / "metadata.json"
        transcript = db.root / "s1" / "Transcript.md"
        before_metadata = metadata.read_bytes()
        before_transcript = transcript.read_bytes()
        db.close()

        reopened = SessionDB(vault_path=vault)
        assert [m["content"] for m in reopened.get_messages("s1")] == ["hello"]
        assert metadata.read_bytes() == before_metadata
        assert transcript.read_bytes() == before_transcript
        reopened.close()

    def test_vault_json_is_source_of_truth(self, tmp_path):
        """Canonical JSON stores fields that Markdown only projects for inspection."""
        vault = tmp_path / "vault"
        vault.mkdir()
        db = SessionDB(vault_path=vault)
        db.create_session("s1", "discord", model="test-model")
        db.append_message(
            "s1", "assistant", "reply", reasoning_content="internal reasoning"
        )
        metadata = db.root / "s1" / "metadata.json"
        transcript = db.root / "s1" / "Transcript.md"
        raw = json.loads(metadata.read_text(encoding="utf-8"))
        assert raw["source"] == "discord"
        assert raw["model"] == "test-model"
        assert raw["messages"][0]["reasoning_content"] == "internal reasoning"
        assert "reply" in transcript.read_text(encoding="utf-8")
        db.close()

        reopened = SessionDB(vault_path=vault)
        assert reopened.get_session("s1")["model"] == "test-model"
        assert reopened.get_messages("s1")[0]["reasoning_content"] == "internal reasoning"
        reopened.close()


class TestTitleUniqueness:
    """Tests for unique title enforcement and title-based lookups."""

    def test_duplicate_title_raises(self, db):
        """Setting a title already used by another session raises ValueError."""
        db.create_session("s1", "cli")
        db.create_session("s2", "cli")
        db.set_session_title("s1", "my project")
        with pytest.raises(ValueError, match="already in use"):
            db.set_session_title("s2", "my project")

    def test_same_session_can_keep_title(self, db):
        """A session can re-set its own title without error."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        # Should not raise — it's the same session
        assert db.set_session_title("s1", "my project") is True

    def test_null_titles_not_unique(self, db):
        """Multiple sessions can have NULL titles (no constraint violation)."""
        db.create_session("s1", "cli")
        db.create_session("s2", "cli")
        # Both have NULL titles — no error
        assert db.get_session("s1")["title"] is None
        assert db.get_session("s2")["title"] is None

    def test_get_session_by_title(self, db):
        db.create_session("s1", "cli")
        db.set_session_title("s1", "refactoring auth")
        result = db.get_session_by_title("refactoring auth")
        assert result is not None
        assert result["id"] == "s1"

    def test_get_session_by_title_not_found(self, db):
        assert db.get_session_by_title("nonexistent") is None

    def test_get_session_title(self, db):
        db.create_session("s1", "cli")
        assert db.get_session_title("s1") is None
        db.set_session_title("s1", "my title")
        assert db.get_session_title("s1") == "my title"

    def test_get_session_title_nonexistent(self, db):
        assert db.get_session_title("nonexistent") is None


class TestTitleLineage:
    """Tests for title lineage resolution and auto-numbering."""

    def test_resolve_exact_title(self, db):
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        assert db.resolve_session_by_title("my project") == "s1"

    def test_resolve_returns_latest_numbered(self, db):
        """When numbered variants exist, return the most recent one."""
        import time
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        time.sleep(0.01)
        db.create_session("s2", "cli")
        db.set_session_title("s2", "my project #2")
        time.sleep(0.01)
        db.create_session("s3", "cli")
        db.set_session_title("s3", "my project #3")
        # Resolving "my project" should return s3 (latest numbered variant)
        assert db.resolve_session_by_title("my project") == "s3"

    def test_resolve_exact_numbered(self, db):
        """Resolving an exact numbered title returns that specific session."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        db.create_session("s2", "cli")
        db.set_session_title("s2", "my project #2")
        # Resolving "my project #2" exactly should return s2
        assert db.resolve_session_by_title("my project #2") == "s2"

    def test_resolve_nonexistent_title(self, db):
        assert db.resolve_session_by_title("nonexistent") is None

    def test_next_title_no_existing(self, db):
        """With no existing sessions, base title is returned as-is."""
        assert db.get_next_title_in_lineage("my project") == "my project"

    def test_next_title_first_continuation(self, db):
        """First continuation after the original gets #2."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        assert db.get_next_title_in_lineage("my project") == "my project #2"

    def test_next_title_increments(self, db):
        """Each continuation increments the number."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        db.create_session("s2", "cli")
        db.set_session_title("s2", "my project #2")
        db.create_session("s3", "cli")
        db.set_session_title("s3", "my project #3")
        assert db.get_next_title_in_lineage("my project") == "my project #4"

    def test_next_title_strips_existing_number(self, db):
        """Passing a numbered title strips the number and finds the base."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        db.create_session("s2", "cli")
        db.set_session_title("s2", "my project #2")
        # Even when called with "my project #2", it should return #3
        assert db.get_next_title_in_lineage("my project #2") == "my project #3"

    def test_next_title_strips_legacy_number(self, db):
        """A legacy '(2)' title continues the same #N lineage."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        db.create_session("s2", "cli")
        db.set_session_title("s2", "my project (2)")
        assert db.get_next_title_in_lineage("my project (2)") == "my project #3"

    def test_next_title_when_only_numbered_session_remains(self, db):
        """Pruning the original session must not restart an existing lineage."""
        db.create_session("s2", "cli")
        db.set_session_title("s2", "my project #2")
        assert db.get_next_title_in_lineage("my project #2") == "my project #3"
        assert db.get_next_title_in_lineage("my project") == "my project #3"

    def test_next_title_numbered_without_any_existing_lineage(self, db):
        """An unused numbered input starts with its unnumbered base title."""
        assert db.get_next_title_in_lineage("new project #2") == "new project"


class TestTitleSqlWildcards:
    """Titles containing SQL LIKE wildcards (%, _) must not cause false matches."""

    def test_resolve_title_with_underscore(self, db):
        """A title like 'test_project' should not match 'testXproject #2'."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "test_project")
        db.create_session("s2", "cli")
        db.set_session_title("s2", "testXproject #2")
        # Resolving "test_project" should return s1 (exact), not s2
        assert db.resolve_session_by_title("test_project") == "s1"

    def test_resolve_title_with_percent(self, db):
        """A title with '%' should not wildcard-match unrelated sessions."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "100% done")
        db.create_session("s2", "cli")
        db.set_session_title("s2", "100X done #2")
        # Should resolve to s1 (exact), not s2
        assert db.resolve_session_by_title("100% done") == "s1"

    def test_next_lineage_with_underscore(self, db):
        """get_next_title_in_lineage with underscores doesn't match wrong sessions."""
        db.create_session("s1", "cli")
        db.set_session_title("s1", "test_project")
        db.create_session("s2", "cli")
        db.set_session_title("s2", "testXproject #2")
        # Only "test_project" exists, so next should be "test_project #2"
        assert db.get_next_title_in_lineage("test_project") == "test_project #2"


class TestListSessionsRich:
    """Tests for enhanced session listing with preview and last_active."""

    def test_preview_from_first_user_message(self, db):
        db.create_session("s1", "cli")
        db.append_message("s1", "system", "You are a helpful assistant.")
        db.append_message("s1", "user", "Help me refactor the auth module please")
        db.append_message("s1", "assistant", "Sure, let me look at it.")
        sessions = db.list_sessions_rich()
        assert len(sessions) == 1
        assert "Help me refactor the auth module" in sessions[0]["preview"]

    def test_preview_truncated_at_60(self, db):
        db.create_session("s1", "cli")
        long_msg = "A" * 100
        db.append_message("s1", "user", long_msg)
        sessions = db.list_sessions_rich()
        assert len(sessions[0]["preview"]) == 63  # 60 chars + "..."
        assert sessions[0]["preview"].endswith("...")

    def test_preview_empty_when_no_user_messages(self, db):
        db.create_session("s1", "cli")
        db.append_message("s1", "system", "System prompt")
        sessions = db.list_sessions_rich()
        assert sessions[0]["preview"] == ""

    def test_last_active_from_latest_message(self, db):
        import time
        db.create_session("s1", "cli")
        db.append_message("s1", "user", "Hello")
        time.sleep(0.01)
        db.append_message("s1", "assistant", "Hi there!")
        sessions = db.list_sessions_rich()
        # last_active should be close to now (the assistant message)
        assert sessions[0]["last_active"] > sessions[0]["started_at"]

    def test_last_active_fallback_to_started_at(self, db):
        db.create_session("s1", "cli")
        sessions = db.list_sessions_rich()
        # No messages, so last_active falls back to started_at
        assert sessions[0]["last_active"] == sessions[0]["started_at"]

    @staticmethod
    def _pin_vault_times(db, sid, *, started_at=None, ended_at=None,
                         end_reason=None, message_times=None):
        """Adjust deterministic fixture timestamps through the Vault writer."""
        def edit(item):
            if started_at is not None:
                item["started_at"] = started_at
            if ended_at is not None:
                item["ended_at"] = ended_at
            if end_reason is not None:
                item["end_reason"] = end_reason
            for message in item["messages"]:
                if message_times and message["content"] in message_times:
                    message["timestamp"] = message_times[message["content"]]
        db._edit(sid, edit)

    def test_order_by_last_active_surfaces_recently_touched_older_session_first(self, db):
        t0 = 1709500000.0
        db.create_session("old", "cli")
        db.create_session("new", "cli")
        db.append_message("old", "user", "old first")
        db.append_message("new", "user", "new first")
        db.append_message("old", "assistant", "old touched later")

        self._pin_vault_times(
            db, "old", started_at=t0,
            message_times={"old first": t0 + 1, "old touched later": t0 + 20},
        )
        self._pin_vault_times(
            db, "new", started_at=t0 + 10,
            message_times={"new first": t0 + 11},
        )

        assert [s["id"] for s in db.list_sessions_rich(limit=5)] == ["new", "old"]
        assert [
            s["id"] for s in db.list_sessions_rich(limit=5, order_by_last_active=True)
        ] == ["old", "new"]
        # Timestamp edits and ordering must survive a fresh Vault reader.
        reopened = type(db)(vault_path=db.vault)
        assert [
            s["id"] for s in reopened.list_sessions_rich(limit=5, order_by_last_active=True)
        ] == ["old", "new"]
        assert not list(db.vault.rglob("*.db"))

    def test_order_by_last_active_uses_compression_tip_activity(self, db):
        """An old root's recently active continuation wins even with limit=1."""
        t0 = 1709500000.0
        db.create_session("root1", "cli")
        db.append_message("root1", "user", "old ask")
        db.end_session("root1", "compression")
        self._pin_vault_times(
            db, "root1", started_at=t0, ended_at=t0 + 100,
            message_times={"old ask": t0 + 1},
        )

        db.create_session("tip1", "cli", parent_session_id="root1")
        db.append_message("tip1", "user", "latest message")
        self._pin_vault_times(
            db, "tip1", started_at=t0 + 101,
            message_times={"latest message": t0 + 10_000},
        )

        for i in range(5):
            sid = f"newer{i}"
            db.create_session(sid, "cli")
            db.append_message(sid, "user", f"msg {i}")
            self._pin_vault_times(
                db, sid, started_at=t0 + 500 + i,
                message_times={f"msg {i}": t0 + 500 + i},
            )

        top = db.list_sessions_rich(limit=1, order_by_last_active=True)
        assert len(top) == 1
        assert top[0]["id"] == "tip1"
        assert top[0]["_lineage_root_id"] == "root1"
        assert top[0]["last_active"] == t0 + 10_000

        reopened = type(db)(vault_path=db.vault)
        persisted = reopened.list_sessions_rich(limit=1, order_by_last_active=True)
        assert persisted[0]["id"] == "tip1"
        assert persisted[0]["_lineage_root_id"] == "root1"

    def test_rich_list_includes_title(self, db):
        db.create_session("s1", "cli")
        db.set_session_title("s1", "refactoring auth")
        sessions = db.list_sessions_rich()
        assert sessions[0]["title"] == "refactoring auth"

    def test_rich_list_source_filter(self, db):
        db.create_session("s1", "cli")
        db.create_session("s2", "telegram")
        sessions = db.list_sessions_rich(source="cli")
        assert len(sessions) == 1
        assert sessions[0]["id"] == "s1"

    def test_preview_newlines_collapsed(self, db):
        db.create_session("s1", "cli")
        db.append_message("s1", "user", "Line one\nLine two\nLine three")
        sessions = db.list_sessions_rich()
        assert "\n" not in sessions[0]["preview"]
        assert "Line one Line two" in sessions[0]["preview"]

    def test_branch_session_visible_in_list(self, db):
        """Branch sessions (parent ended with 'branched') must appear in list_sessions_rich."""
        db.create_session("parent", "cli")
        db.end_session("parent", "branched")
        db.create_session("branch", "cli", parent_session_id="parent")
        db.append_message("branch", "user", "Exploring the alternative approach")

        sessions = db.list_sessions_rich()
        ids = [s["id"] for s in sessions]
        assert "branch" in ids, "Branch session should be visible in default list"

    def test_subagent_session_still_hidden(self, db):
        """Sub-agent children (parent NOT ended with 'branched') remain hidden."""
        db.create_session("root", "cli")
        db.create_session("delegate", "cli", parent_session_id="root")

        sessions = db.list_sessions_rich()
        ids = [s["id"] for s in sessions]
        assert "delegate" not in ids, "Delegate sub-agent should not appear in default list"
        assert "root" in ids

    def test_compression_child_still_hidden(self, db):
        """A post-compression continuation is hidden without tip projection."""
        import time as _time

        t0 = _time.time()
        db.create_session("root", "cli")
        db._edit("root", lambda item: item.update(
            started_at=t0, ended_at=t0 + 1800, end_reason="compression"
        ))
        db.create_session("continuation", "cli", parent_session_id="root")
        db._edit(
            "continuation",
            lambda item: item.update(started_at=t0 + 1801),
        )

        reopened = type(db)(vault_path=db.vault)
        ids = [
            s["id"] for s in reopened.list_sessions_rich(
                project_compression_tips=False
            )
        ]
        assert "continuation" not in ids
        assert "root" in ids


class TestCompressionChainProjection:
    """Tests for lineage-aware list_sessions_rich — compressed conversations
    surface as their live continuation tip, not the dead parent root.
    """

    def _build_compression_chain(self, db, t0: float):
        """Build root -> delegate and root -> mid -> tip in the Vault."""
        db.create_session("root1", "cli")
        db._edit("root1", lambda item: item.update(started_at=t0))
        db.append_message("root1", "user", "help me refactor auth")

        # This delegate predates the parent's compression.
        db.create_session("delegate1", "cli", parent_session_id="root1")
        db._edit(
            "delegate1",
            lambda item: item.update(started_at=t0 + 600, ended_at=t0 + 650),
        )
        db.append_message("delegate1", "user", "delegate task")

        t_compress_root = t0 + 1800
        db._edit(
            "root1",
            lambda item: item.update(
                ended_at=t_compress_root, end_reason="compression"
            ),
        )

        db.create_session("mid1", "cli", parent_session_id="root1")
        db._edit(
            "mid1", lambda item: item.update(started_at=t_compress_root + 1)
        )
        db.append_message("mid1", "user", "continuing")

        t_compress_mid = t_compress_root + 1800
        db._edit(
            "mid1",
            lambda item: item.update(
                ended_at=t_compress_mid, end_reason="compression"
            ),
        )

        db.create_session("tip1", "cli", parent_session_id="mid1")
        db._edit(
            "tip1", lambda item: item.update(started_at=t_compress_mid + 1)
        )
        db.append_message("tip1", "user", "latest message")
        return ("root1", "delegate1", "mid1", "tip1")

    def test_get_compression_tip_walks_full_chain(self, db):
        import time as _time
        self._build_compression_chain(db, _time.time() - 3600)
        assert db.get_compression_tip("root1") == "tip1"
        assert db.get_compression_tip("mid1") == "tip1"
        assert db.get_compression_tip("tip1") == "tip1"

    def test_get_compression_tip_returns_self_for_uncompressed(self, db):
        db.create_session("solo", "cli")
        assert db.get_compression_tip("solo") == "solo"

    def test_get_compression_tip_skips_delegate_children(self, db):
        """Delegate subagents have parent_session_id set but were created
        BEFORE the parent ended. They must not be followed as compression
        continuations — the started_at >= ended_at guard handles this.
        """
        import time as _time
        self._build_compression_chain(db, _time.time() - 3600)
        # delegate1 is a child of root1 but NOT a compression continuation.
        # root1's tip must be tip1 (via mid1), not delegate1.
        assert db.get_compression_tip("root1") == "tip1"

    def test_list_surfaces_tip_for_compressed_root(self, db):
        """The list must show the tip's id/message_count/preview in place of
        the root row, so users can see and resume the live conversation.
        """
        import time as _time
        self._build_compression_chain(db, _time.time() - 3600)
        # Add an uncompressed root for comparison.
        db.create_session("solo", "cli")
        db.append_message("solo", "user", "standalone")

        sessions = db.list_sessions_rich(source="cli", limit=20)
        ids = [s["id"] for s in sessions]
        # Only top-level conversations appear: tip1 (projected from root1) + solo.
        # Delegate children, mid1, and the dead root1 must NOT be in the list.
        assert "tip1" in ids
        assert "solo" in ids
        assert "root1" not in ids
        assert "mid1" not in ids
        assert "delegate1" not in ids

        tip_row = next(s for s in sessions if s["id"] == "tip1")
        # The row surfaces the tip's identity but preserves the root's start
        # timestamp for stable ordering and lineage tracking.
        assert tip_row["_lineage_root_id"] == "root1"
        assert tip_row["preview"].startswith("latest message")
        assert tip_row["ended_at"] is None  # tip is still live
        assert tip_row["end_reason"] is None

    def test_list_without_projection_returns_raw_root(self, db):
        """project_compression_tips=False returns the raw parent-NULL root
        rows — useful for admin/debug UIs.
        """
        import time as _time
        self._build_compression_chain(db, _time.time() - 3600)
        sessions = db.list_sessions_rich(
            source="cli", limit=20, project_compression_tips=False
        )
        ids = [s["id"] for s in sessions]
        assert "root1" in ids
        assert "tip1" not in ids

        root_row = next(s for s in sessions if s["id"] == "root1")
        assert root_row["end_reason"] == "compression"
        assert "_lineage_root_id" not in root_row

    def test_list_preserves_sort_by_started_at(self, db):
        """Chronological ordering uses the ROOT's started_at (conversation
        start), not the tip's. This keeps lineage entries stable in the list
        even as new compressions push the tip forward in time.
        """
        import time as _time
        t0 = _time.time() - 3600
        self._build_compression_chain(db, t0)

        # Create a newer standalone session that should sort above the lineage
        # if we used tip.started_at, but below if we correctly use root.started_at.
        t_between = t0 + 120  # between root1 and its compression
        db.create_session("newer", "cli")
        db._edit("newer", lambda item: item.update(started_at=t_between))
        db.append_message("newer", "user", "newer session started after root1")

        sessions = db.list_sessions_rich(source="cli", limit=20)
        ids_in_order = [s["id"] for s in sessions]
        # 'newer' started AFTER root1 but BEFORE tip1's actual started_at.
        # Correct ordering (by root started_at): newer > tip1's lineage entry.
        assert ids_in_order.index("newer") < ids_in_order.index("tip1")

    def test_list_handles_broken_chain_gracefully(self, db):
        """A compression root with no child (e.g. DB corruption or a partial
        end_session call that didn't finish creating the child) must not
        crash the list — it should fall back to surfacing the root as-is.
        """
        import time as _time
        t0 = _time.time() - 100
        db.create_session("orphan", "cli")
        db._edit("orphan", lambda item: item.update(
            started_at=t0, ended_at=t0 + 10, end_reason="compression"
        ))

        sessions = db.list_sessions_rich(source="cli", limit=10)
        ids = [s["id"] for s in sessions]
        assert "orphan" in ids
        row = next(s for s in sessions if s["id"] == "orphan")
        # No tip means no projection — row stays raw.
        assert "_lineage_root_id" not in row
        assert row["end_reason"] == "compression"


# =========================================================================
# Session source exclusion (--source flag for third-party isolation)
# =========================================================================

class TestExcludeSources:
    """Tests for exclude_sources on list_sessions_rich and search_messages."""

    def test_list_sessions_rich_excludes_tool_source(self, db):
        db.create_session("s1", "cli")
        db.create_session("s2", "tool")
        db.create_session("s3", "telegram")
        sessions = db.list_sessions_rich(exclude_sources=["tool"])
        ids = [s["id"] for s in sessions]
        assert "s1" in ids
        assert "s3" in ids
        assert "s2" not in ids

    def test_list_sessions_rich_no_exclusion_returns_all(self, db):
        db.create_session("s1", "cli")
        db.create_session("s2", "tool")
        sessions = db.list_sessions_rich()
        ids = [s["id"] for s in sessions]
        assert "s1" in ids
        assert "s2" in ids

    def test_list_sessions_rich_source_and_exclude_combined(self, db):
        """When source= is explicit, exclude_sources should not conflict."""
        db.create_session("s1", "cli")
        db.create_session("s2", "tool")
        db.create_session("s3", "telegram")
        # Explicit source filter: only tool sessions, no exclusion
        sessions = db.list_sessions_rich(source="tool")
        ids = [s["id"] for s in sessions]
        assert ids == ["s2"]

    def test_list_sessions_rich_exclude_multiple_sources(self, db):
        db.create_session("s1", "cli")
        db.create_session("s2", "tool")
        db.create_session("s3", "cron")
        db.create_session("s4", "telegram")
        sessions = db.list_sessions_rich(exclude_sources=["tool", "cron"])
        ids = [s["id"] for s in sessions]
        assert "s1" in ids
        assert "s4" in ids
        assert "s2" not in ids
        assert "s3" not in ids

    def test_search_messages_excludes_tool_source(self, db):
        db.create_session("s1", "cli")
        db.append_message("s1", "user", "Python deployment question")
        db.create_session("s2", "tool")
        db.append_message("s2", "user", "Python automated question")
        results = db.search_messages("Python", exclude_sources=["tool"])
        sources = [r["source"] for r in results]
        assert "cli" in sources
        assert "tool" not in sources

    def test_search_messages_no_exclusion_returns_all_sources(self, db):
        db.create_session("s1", "cli")
        db.append_message("s1", "user", "Rust deployment question")
        db.create_session("s2", "tool")
        db.append_message("s2", "user", "Rust automated question")
        results = db.search_messages("Rust")
        sources = [r["source"] for r in results]
        assert "cli" in sources
        assert "tool" in sources

    def test_search_messages_source_include_and_exclude(self, db):
        """source_filter (include) and exclude_sources can coexist."""
        db.create_session("s1", "cli")
        db.append_message("s1", "user", "Golang test")
        db.create_session("s2", "telegram")
        db.append_message("s2", "user", "Golang test")
        db.create_session("s3", "tool")
        db.append_message("s3", "user", "Golang test")
        # Include cli+tool, but exclude tool → should only return cli
        results = db.search_messages(
            "Golang", source_filter=["cli", "tool"], exclude_sources=["tool"]
        )
        sources = [r["source"] for r in results]
        assert sources == ["cli"]


class TestResolveSessionByNameOrId:
    """Tests for the main.py helper that resolves names or IDs."""

    def test_resolve_by_id(self, db):
        db.create_session("test-id-123", "cli")
        session = db.get_session("test-id-123")
        assert session is not None
        assert session["id"] == "test-id-123"

    def test_resolve_by_title_falls_back(self, db):
        db.create_session("s1", "cli")
        db.set_session_title("s1", "my project")
        result = db.resolve_session_by_title("my project")
        assert result == "s1"


# =========================================================================
# Concurrent write safety / lock contention fixes (#3139)
# =========================================================================

class TestConcurrentWriteSafety:
    def test_create_session_insert_or_ignore_is_idempotent(self, db):
        """create_session with the same ID twice must not raise (INSERT OR IGNORE)."""
        db.create_session(session_id="dup-1", source="cli", model="m")
        # Second call should be silent — no IntegrityError
        db.create_session(session_id="dup-1", source="gateway", model="m2")
        session = db.get_session("dup-1")
        # Row should exist (first write wins with OR IGNORE)
        assert session is not None
        assert session["source"] == "cli"

    def test_ensure_session_creates_missing_row(self, db):
        """ensure_session must create a minimal row when the session doesn't exist."""
        assert db.get_session("orphan-session") is None
        db.ensure_session("orphan-session", source="gateway", model="test-model")
        row = db.get_session("orphan-session")
        assert row is not None
        assert row["source"] == "gateway"
        assert row["model"] == "test-model"

    def test_ensure_session_is_idempotent(self, db):
        """ensure_session on an existing row must be a no-op (no overwrite)."""
        db.create_session(session_id="existing", source="cli", model="original-model")
        db.ensure_session("existing", source="gateway", model="overwrite-model")
        row = db.get_session("existing")
        # First write wins — ensure_session must not overwrite
        assert row["source"] == "cli"
        assert row["model"] == "original-model"

    def test_ensure_session_allows_append_message_after_failed_create(self, db):
        """Messages can be flushed even when create_session failed at startup.

        Simulates the #3139 scenario: create_session raises (lock), then
        ensure_session is called during flush, then append_message succeeds.
        """
        # Simulate failed create_session — row absent
        db.ensure_session("late-session", source="gateway", model="gpt-4")
        db.append_message(
            session_id="late-session",
            role="user",
            content="hello after lock",
        )
        msgs = db.get_messages("late-session")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hello after lock"

    def test_vault_writer_lock_serializes_concurrent_access(self, db):
        """Vault writes are serialized by flock, not a SQLite busy timeout."""
        import fcntl

        assert db._lockfile.is_relative_to(db.vault)
        # A distinct open file description must not acquire the same lock
        # while the first writer holds it (including across processes).
        with db._guard():
            with db._lockfile.open("a+b") as competing_writer:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(
                        competing_writer, fcntl.LOCK_EX | fcntl.LOCK_NB
                    )

        # The lock must be released when the writer exits its guard.
        with db._lockfile.open("a+b") as competing_writer:
            fcntl.flock(competing_writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(competing_writer, fcntl.LOCK_UN)


# =========================================================================
# Auto-maintenance: state_meta + vacuum + maybe_auto_prune_and_vacuum
# =========================================================================

class TestStateMeta:
    def test_get_meta_missing_returns_none(self, db):
        assert db.get_meta("nonexistent") is None

    def test_set_then_get_meta(self, db):
        db.set_meta("foo", "bar")
        assert db.get_meta("foo") == "bar"

    def test_set_meta_upsert(self, db):
        """set_meta overwrites existing value (ON CONFLICT DO UPDATE)."""
        db.set_meta("key", "v1")
        db.set_meta("key", "v2")
        assert db.get_meta("key") == "v2"


class TestVacuum:
    def test_vacuum_runs_without_error(self, db):
        """VACUUM must succeed on a fresh DB (no rows to reclaim)."""
        db.create_session(session_id="s1", source="cli")
        db.append_message(session_id="s1", role="user", content="hi")
        # Should not raise, even though there's nothing significant to reclaim.
        db.vacuum()


class TestAutoMaintenance:
    def _make_old_ended(self, db, sid: str, days_old: int = 100):
        """Create a session that is ended and was started `days_old` days ago."""
        db.create_session(session_id=sid, source="cli")
        db.end_session(sid, end_reason="done")
        db._conn.execute(
            "UPDATE sessions SET started_at = ? WHERE id = ?",
            (time.time() - days_old * 86400, sid),
        )
        db._conn.commit()

    def test_first_run_prunes_and_vacuums(self, db):
        self._make_old_ended(db, "old1", days_old=100)
        self._make_old_ended(db, "old2", days_old=100)
        db.create_session(session_id="new", source="cli")  # active, must survive

        result = db.maybe_auto_prune_and_vacuum(retention_days=90)
        assert result["skipped"] is False
        assert result["pruned"] == 2
        assert result["vacuumed"] is True
        assert result.get("error") is None
        assert db.get_session("old1") is None
        assert db.get_session("old2") is None
        assert db.get_session("new") is not None

    def test_second_call_within_interval_skips(self, db):
        self._make_old_ended(db, "old", days_old=100)
        first = db.maybe_auto_prune_and_vacuum(
            retention_days=90, min_interval_hours=24
        )
        assert first["skipped"] is False
        assert first["pruned"] == 1

        # Create another prunable session; a second call within
        # min_interval_hours should still skip without touching it.
        self._make_old_ended(db, "old2", days_old=100)
        second = db.maybe_auto_prune_and_vacuum(
            retention_days=90, min_interval_hours=24
        )
        assert second["skipped"] is True
        assert second["pruned"] == 0
        assert db.get_session("old2") is not None  # untouched

    def test_second_call_after_interval_runs_again(self, db):
        self._make_old_ended(db, "old", days_old=100)
        db.maybe_auto_prune_and_vacuum(retention_days=90, min_interval_hours=24)

        # Backdate the last-run marker to force another run.
        db.set_meta("last_auto_prune", str(time.time() - 48 * 3600))

        self._make_old_ended(db, "old2", days_old=100)
        result = db.maybe_auto_prune_and_vacuum(
            retention_days=90, min_interval_hours=24
        )
        assert result["skipped"] is False
        assert result["pruned"] == 1
        assert db.get_session("old2") is None

    def test_no_prunable_sessions_no_vacuum(self, db):
        """When prune deletes 0 rows, VACUUM is skipped (wasted I/O)."""
        db.create_session(session_id="fresh", source="cli")  # too recent
        result = db.maybe_auto_prune_and_vacuum(retention_days=90)
        assert result["skipped"] is False
        assert result["pruned"] == 0
        assert result["vacuumed"] is False
        # But last-run is still recorded so we don't retry immediately.
        assert db.get_meta("last_auto_prune") is not None

    def test_vacuum_disabled_via_flag(self, db):
        self._make_old_ended(db, "old", days_old=100)
        result = db.maybe_auto_prune_and_vacuum(retention_days=90, vacuum=False)
        assert result["pruned"] == 1
        assert result["vacuumed"] is False

    def test_corrupt_last_run_marker_treated_as_no_prior_run(self, db):
        """A non-numeric marker must not break maintenance."""
        db.set_meta("last_auto_prune", "not-a-timestamp")
        self._make_old_ended(db, "old", days_old=100)
        result = db.maybe_auto_prune_and_vacuum(retention_days=90)
        assert result["skipped"] is False
        assert result["pruned"] == 1

    def test_state_meta_survives_vacuum(self, db):
        """Marker written just before VACUUM must still be readable after."""
        self._make_old_ended(db, "old", days_old=100)
        db.maybe_auto_prune_and_vacuum(retention_days=90)
        marker = db.get_meta("last_auto_prune")
        assert marker is not None
        # Should parse as a float timestamp close to now.
        assert abs(float(marker) - time.time()) < 60

    def test_auto_prune_deletes_transcript_files(self, db, tmp_path):
        """Issue #3015: auto-prune must also delete on-disk transcript files."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        self._make_old_ended(db, "old1", days_old=100)
        self._make_old_ended(db, "old2", days_old=100)
        db.create_session(session_id="new", source="cli")  # active

        # Transcript files mimicking real gateway/CLI layout
        (sessions_dir / "old1.json").write_text("{}")
        (sessions_dir / "old1.jsonl").write_text("{}\n")
        (sessions_dir / "old2.jsonl").write_text("{}\n")
        (sessions_dir / "request_dump_old1_001.json").write_text("{}")
        (sessions_dir / "new.jsonl").write_text("{}\n")  # active, must survive

        result = db.maybe_auto_prune_and_vacuum(
            retention_days=90, sessions_dir=sessions_dir
        )
        assert result["pruned"] == 2

        # Pruned transcript files are gone
        assert not (sessions_dir / "old1.json").exists()
        assert not (sessions_dir / "old1.jsonl").exists()
        assert not (sessions_dir / "old2.jsonl").exists()
        assert not (sessions_dir / "request_dump_old1_001.json").exists()
        # Active session's transcript is untouched
        assert (sessions_dir / "new.jsonl").exists()

    def test_auto_prune_without_sessions_dir_preserves_files(self, db, tmp_path):
        """Backward-compat: no sessions_dir = DB-only cleanup (legacy behavior)."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        self._make_old_ended(db, "old", days_old=100)
        (sessions_dir / "old.jsonl").write_text("{}\n")

        result = db.maybe_auto_prune_and_vacuum(retention_days=90)
        assert result["pruned"] == 1
        # File stays — caller didn't opt in
        assert (sessions_dir / "old.jsonl").exists()

    def test_prune_sessions_deletes_files_for_pruned_only(self, db, tmp_path):
        """Active-session transcripts must never be deleted by prune."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        self._make_old_ended(db, "old", days_old=100)
        db.create_session(session_id="active", source="cli")  # not ended
        (sessions_dir / "old.jsonl").write_text("{}\n")
        (sessions_dir / "active.jsonl").write_text("{}\n")

        count = db.prune_sessions(older_than_days=90, sessions_dir=sessions_dir)
        assert count == 1
        assert not (sessions_dir / "old.jsonl").exists()
        assert (sessions_dir / "active.jsonl").exists()


# =========================================================================
# FTS5 indexing of tool_calls / tool_name (#16751)
# =========================================================================

class TestFTS5ToolCallIndexing:
    """Regression tests: search_messages must see tool_name and tool_calls.

    Before #16751's fix, `messages_fts` only indexed `messages.content`, so
    tokens that only appeared in `tool_name` or the serialized `tool_calls`
    JSON were invisible to session_search even though the row was in the DB.
    """

    def test_tool_name_is_searchable(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1", role="assistant", content="",
            tool_name="UNIQUETOOLNAME",
        )
        results = db.search_messages("UNIQUETOOLNAME")
        assert len(results) == 1

    def test_tool_calls_args_are_searchable(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1", role="assistant", content="",
            tool_calls=[{
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": '{"query": "UNIQUESEARCHTOKEN"}',
                },
            }],
        )
        results = db.search_messages("UNIQUESEARCHTOKEN")
        assert len(results) == 1

    def test_tool_function_name_in_tool_calls_is_searchable(self, db):
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1", role="assistant", content="",
            tool_calls=[{
                "id": "c1",
                "type": "function",
                "function": {"name": "UNIQUEFUNCNAME", "arguments": "{}"},
            }],
        )
        results = db.search_messages("UNIQUEFUNCNAME")
        assert len(results) == 1

    def test_delete_message_row_does_not_crash(self, db):
        """DELETE on messages must not raise when FTS rows reference tool fields.

        Previously the messages_fts_delete trigger passed old.content to the
        FTS5 delete-command but the inserted row was the concatenation of
        content || tool_name || tool_calls, so FTS5 rejected the delete with
        'SQL logic error' and every session delete path broke.
        """
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1", role="assistant", content="hello",
            tool_name="web_search",
            tool_calls=[{
                "id": "c1",
                "type": "function",
                "function": {"name": "web_search", "arguments": '{"q": "x"}'},
            }],
        )
        # end_session + end-time prune path would exercise DELETE; hit the
        # row directly through the write helper to keep the regression focused.
        def _delete(conn):
            conn.execute("DELETE FROM messages WHERE session_id = ?", ("s1",))
        db._execute_write(_delete)  # must not raise

        assert db.search_messages("hello") == []
        assert db.search_messages("web_search") == []

    def test_update_message_reindexes_tool_fields(self, db):
        """UPDATE must refresh the FTS row so old tokens drop out and new tokens appear."""
        db.create_session(session_id="s1", source="cli")
        db.append_message(
            "s1", role="assistant", content="",
            tool_name="ORIGINALTOOL",
        )
        assert len(db.search_messages("ORIGINALTOOL")) == 1

        def _update(conn):
            conn.execute(
                "UPDATE messages SET tool_name = ? WHERE session_id = ?",
                ("RENAMEDTOOL", "s1"),
            )
        db._execute_write(_update)

        assert db.search_messages("ORIGINALTOOL") == []
        assert len(db.search_messages("RENAMEDTOOL")) == 1


class TestFTS5ToolCallMigration:
    """v11 migration: pre-existing state.db with old external-content FTS tables
    must be re-indexed so tool_name / tool_calls become searchable after upgrade."""

    def test_v10_to_v11_upgrade_backfills_tool_fields(self, tmp_path):
        """Simulate an existing user: build a v10-shaped DB by hand, insert a
        row with tool_calls, then open via SessionDB (which runs migrations).
        After upgrade, the tool_calls token must be searchable."""
        import sqlite3

        db_path = tmp_path / "legacy.db"

        # Build the pre-v11 schema by hand: external-content FTS tables +
        # old triggers that only reference new.content.
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (10);

            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                source TEXT,
                started_at REAL,
                ended_at REAL,
                title TEXT,
                parent_session_id TEXT,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                api_call_count INTEGER DEFAULT 0
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_name TEXT,
                tool_calls TEXT,
                tool_call_id TEXT,
                token_count INTEGER,
                finish_reason TEXT,
                reasoning TEXT,
                reasoning_content TEXT,
                reasoning_details TEXT,
                codex_reasoning_items TEXT,
                codex_message_items TEXT
            );

            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content, content=messages, content_rowid=id
            );
            CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END;

            CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(
                content, content=messages, content_rowid=id, tokenize='trigram'
            );
            CREATE TRIGGER messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts_trigram(rowid, content) VALUES (new.id, new.content);
            END;
        """)
        conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("s1", "cli", time.time()),
        )
        conn.execute(
            "INSERT INTO messages (session_id, timestamp, role, content, tool_name, tool_calls) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("s1", time.time(), "assistant", "", "LEGACYTOOL",
             '{"function":{"name":"web_search","arguments":"{\\"q\\":\\"LEGACYARG\\"}"}}'),
        )
        conn.commit()

        # Verify the legacy FTS rows don't contain the tool tokens yet.
        legacy_hits = conn.execute(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'LEGACYTOOL'"
        ).fetchall()
        assert legacy_hits == [], "sanity: legacy FTS must NOT contain tool_name"
        conn.close()

        # Now open via SessionDB — migration runs.
        session_db = SessionDB(db_path=db_path)
        try:
            assert len(session_db.search_messages("LEGACYTOOL")) == 1, \
                "v11 migration must backfill tool_name into FTS"
            assert len(session_db.search_messages("LEGACYARG")) == 1, \
                "v11 migration must backfill tool_calls JSON into FTS"
            # schema_version bumped
            row = session_db._conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            version = row["version"] if hasattr(row, "keys") else row[0]
            assert version == 11
        finally:
            session_db.close()

