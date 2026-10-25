"""Tests for hermes_hybrid deep memory, Vault search, and Discord proactive logic.

All Vaults are temporary; no model, token, or network is used.
"""

import random

from hermes_hybrid.deep_memory import DeepMemory
from hermes_hybrid.vault_search import search_vault
from hermes_hybrid.discord_proactive import ProactivePolicy, ProactiveWatcher, should_respond


def _deep(tmp_path):
    return DeepMemory(tmp_path / "vault")


def test_layer_priority_and_token_budget(tmp_path):
    dm = _deep(tmp_path)
    dm.add_memory("Alice trusts Bob deeply and always will", layer="canon", entities=["Alice", "Bob"], confidence=0.9)
    dm.add_memory("Alice saw a red bird in the garden today", layer="reflex", entities=["Alice"], confidence=0.5)
    result = dm.retrieve("Alice Bob", budget_tokens=100, viewer="user1")
    assert result["items"][0]["layer"] == "canon"
    assert result["tokens_used"] <= 100
    # Tight budget: only the highest-priority item fits, others skipped.
    tight = dm.retrieve("Alice", budget_tokens=5, viewer="user1")
    assert tight["tokens_used"] <= 20  # single-item truncation allowance
    assert tight["items"]


def test_visible_to_acl_is_mechanical(tmp_path):
    dm = _deep(tmp_path)
    dm.add_memory("Alice told Bob a secret about the vault key", layer="canon",
                  entities=["Alice", "Bob"], visible_to=["bob"], confidence=0.9)
    assert dm.retrieve("vault key secret", viewer="alice")["items"] == []
    assert dm.retrieve("vault key secret", viewer="bob")["items"] != []


def test_supersede_and_contradiction(tmp_path):
    dm = _deep(tmp_path)
    old = dm.add_memory("Alice trusts Bob", layer="canon", entities=["Alice", "Bob"])
    new = dm.add_memory("Alice no longer trusts Bob", layer="canon", entities=["Alice", "Bob"])
    # Negation language creates a contradiction record, old stays active until resolved.
    assert len(dm.list_contradictions(status="open")) >= 1
    contradiction = dm.list_contradictions(status="open")[0]
    resolved = dm.resolve_contradiction(contradiction["id"], "keep_new")
    assert resolved["status"] == "resolved"
    assert dm.get_memory(old["id"])["status"] == "superseded"
    assert dm.get_memory(new["id"])["status"] == "active"
    # Explicit supersession does not need a contradiction.
    replacement = dm.add_memory("Alice trusts Bob again", layer="canon",
                                entities=["Alice", "Bob"], supersedes=new["id"])
    assert dm.get_memory(new["id"])["status"] == "superseded"
    assert dm.get_memory(new["id"])["superseded_by"] == replacement["id"]


def test_promote_and_forget_never_deletes(tmp_path):
    dm = _deep(tmp_path)
    rec = dm.add_memory("A passing thought", layer="reflex", entities=[])
    assert dm.promote(rec["id"], "canon")["layer"] == "canon"
    assert dm.forget(rec["id"]) is True
    assert dm.get_memory(rec["id"])["status"] == "retracted"


def test_episode_and_scene_continuity(tmp_path):
    dm = _deep(tmp_path)
    scene = dm.start_scene("scene-1", location="Harbor Town", time="evening",
                           present=["Alice", "Bob"], props=["lantern"], tone="calm")
    assert dm.scene_context("scene-1").startswith("time: evening")
    closed = dm.end_scene("scene-1", "Alice and Bob talked at the harbor and agreed to travel.")
    assert closed["episode_id"]
    assert len(dm.list_episodes()) == 1
    assert dm.recent_scenes(limit=1)[0]["id"] == "scene-1"


def test_relationship_graph_cap(tmp_path):
    dm = _deep(tmp_path)
    dm.register_entity("Alice", "character", aliases=["Al"])
    rel = dm.add_relation("Alice", "Bob", feelings=[("warm", "high"), ("cautious", "medium")],
                          knows=["Bob's secret"], status="friends")
    assert rel["pair"] == ["Alice", "Bob"]
    assert dm.get_relation("Bob", "Alice")["status"] == "friends"
    assert len(dm.graph_neighbors("Alice")) == 1


def test_drift_audit(tmp_path):
    dm = _deep(tmp_path)
    dm.add_memory("Alice and Bob share the lighthouse duty", layer="canon", entities=["Alice", "Bob"])
    dm.add_memory("Zebra quantum violin concert tickets refund policy", layer="canon", entities=["Alice", "Bob"])
    # High entity overlap, low content overlap -> drift contradiction recorded.
    assert dm.drift_audit() != []


def test_vault_search_with_trace(tmp_path):
    vault = tmp_path / "vault"
    mem = vault / "06_Automation" / "Hermes" / "Memory"
    mem.mkdir(parents=True)
    (mem / "note1.md").write_text("---\nid: \"n1\"\ntags: [\"lighthouse\"]\n---\nAlice and the old lighthouse keeper share a secret.\n", encoding="utf-8")
    (mem / "note2.md").write_text("Unrelated cooking notes about bread.\n", encoding="utf-8")
    hits = search_vault(vault, "lighthouse keeper")
    assert hits and hits[0]["title"] == "note1"
    assert "lighthouse" in hits[0]["matched"]
    assert hits[0]["trace"]["tree"] == "Memory"
    assert search_vault(vault, "lighthouse", tags=["other"]) == []


def test_proactive_decisions(tmp_path):
    policy = ProactivePolicy(enabled=True, dry_run=True, bot_user_id="bot1",
                             bot_names=["Hermes"], ignore_probability=0.0,
                             random_interject_probability=0.0)
    assert should_respond({"content": "hey <@bot1> hello", "author_id": "u1", "channel_id": "c1"}, policy)["action"] == "respond"
    assert should_respond({"content": "just chatting", "author_id": "u1", "channel_id": "c1"}, policy)["action"] == "ignore"
    assert should_respond({"content": "hello", "author_id": "bot1", "channel_id": "c1"}, policy)["action"] == "ignore"
    watcher = ProactiveWatcher(tmp_path / "vault", policy)
    first = watcher.should_start_topic("c1", last_activity_at=0.0, now=100000.0)
    assert first["start"] is True
    second = watcher.should_start_topic("c1", last_activity_at=0.0, now=100001.0)
    assert second["start"] is True  # cap is 2 per day
    third = watcher.should_start_topic("c1", last_activity_at=0.0, now=100002.0)
    assert third["start"] is False
    assert watcher.should_start_topic("c1", last_activity_at=99999.0, now=100000.0)["start"] is False
