"""Step 2 regression tests: one canonical transcript, no duplicates.

The Obsidian SessionDB is the only write destination for gateway
transcripts. Legacy JSONL transcripts that still exist on disk are a
read-only recovery source: ``load_transcript`` merges them with
multiset deduplication so a message stored in both places is returned
exactly once, while history that only exists in the legacy file is
not truncated.
"""

import json

import pytest

from gateway.config import GatewayConfig
from gateway.session import SessionStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("HERMES_OBSIDIAN_VAULT", str(vault))
    s = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    s._loaded = True
    return s


def _write_legacy_jsonl(store, session_id, messages):
    path = store.get_transcript_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return path


def _msg(role, content, ts):
    return {"role": role, "content": content, "timestamp": ts}


class TestSingleCanonicalWrite:
    def test_append_writes_only_to_sessiondb(self, store):
        sid = "dedup_append"
        store._db.create_session(sid, source="test")
        store.append_to_transcript(sid, _msg("user", "hello", "2026-01-01T00:00:00"))
        store.append_to_transcript(sid, _msg("assistant", "hi", "2026-01-01T00:00:01"))

        assert not store.get_transcript_path(sid).exists()
        loaded = store.load_transcript(sid)
        assert [m["content"] for m in loaded] == ["hello", "hi"]

    def test_append_skip_db_writes_nothing(self, store):
        """skip_db means the agent already persisted: no second copy anywhere."""
        sid = "dedup_skip"
        store._db.create_session(sid, source="test")
        store._db.append_message(sid, role="user", content="already stored")
        store.append_to_transcript(
            sid, {"role": "user", "content": "already stored"}, skip_db=True,
        )

        assert not store.get_transcript_path(sid).exists()
        loaded = store.load_transcript(sid)
        assert len(loaded) == 1

    def test_rewrite_replaces_only_sessiondb(self, store):
        sid = "dedup_rewrite"
        store._db.create_session(sid, source="test")
        store.append_to_transcript(sid, _msg("user", "one", "2026-01-01T00:00:00"))
        store.append_to_transcript(sid, _msg("assistant", "two", "2026-01-01T00:00:01"))
        legacy = _write_legacy_jsonl(store, sid, [_msg("user", "legacy", "2025-01-01T00:00:00")])
        legacy_before = legacy.read_text()

        store.rewrite_transcript(sid, [_msg("user", "only", "2026-01-02T00:00:00")])

        # Canonical store holds exactly the replacement...
        assert [m["content"] for m in store._db.get_messages_as_conversation(sid)] == ["only"]
        # ...and the legacy file was not rewritten or deleted.
        assert legacy.read_text() == legacy_before


class TestDeduplicatedLoad:
    def test_identical_sources_return_each_message_once(self, store):
        sid = "dedup_identical"
        store._db.create_session(sid, source="test")
        msgs = [
            _msg("user", "a", "2026-01-01T00:00:00"),
            _msg("assistant", "b", "2026-01-01T00:00:01"),
        ]
        for m in msgs:
            store.append_to_transcript(sid, m)
        _write_legacy_jsonl(store, sid, msgs)

        loaded = store.load_transcript(sid)
        assert [m["content"] for m in loaded] == ["a", "b"]

    def test_legacy_only_history_is_recovered_without_truncation(self, store):
        """Partially migrated session: DB has the tail, JSONL the full history."""
        sid = "dedup_partial"
        store._db.create_session(sid, source="test")
        history = [
            _msg("user", "old1", "2026-01-01T00:00:00"),
            _msg("assistant", "old2", "2026-01-01T00:00:01"),
            _msg("user", "new1", "2026-01-01T00:00:02"),
        ]
        # DB only received the newest message (first post-migration turn).
        store._db.append_message(sid, role="user", content="new1")
        _write_legacy_jsonl(store, sid, history)

        loaded = store.load_transcript(sid)
        assert [m["content"] for m in loaded] == ["old1", "old2", "new1"]

    def test_diverged_sources_merge_by_anchor_without_duplicates(self, store):
        sid = "dedup_diverged"
        store._db.create_session(sid, source="test")
        store._db.append_message(sid, role="user", content="shared")
        store._db.append_message(sid, role="assistant", content="canon-only")
        _write_legacy_jsonl(store, sid, [
            _msg("user", "legacy-only", "2026-01-01T00:00:00"),
            _msg("user", "shared", "2026-01-01T00:00:01"),
        ])

        loaded = store.load_transcript(sid)
        assert [m["content"] for m in loaded] == ["legacy-only", "shared", "canon-only"]

    def test_legitimate_repeat_messages_are_kept(self, store):
        """Same text sent twice (distinct turns) is not a duplicate."""
        sid = "dedup_repeat"
        store._db.create_session(sid, source="test")
        msgs = [
            _msg("user", "yes", "2026-01-01T00:00:00"),
            _msg("user", "yes", "2026-01-01T00:05:00"),
        ]
        for m in msgs:
            store.append_to_transcript(sid, m)
        _write_legacy_jsonl(store, sid, msgs)

        loaded = store.load_transcript(sid)
        assert [m["content"] for m in loaded] == ["yes", "yes"]

    def test_merge_helper_multiset_semantics(self):
        a = _msg("user", "x", "2026-01-01T00:00:00")
        merged = SessionStore._merge_transcripts([a, a], [a])
        assert merged == [a, a]
