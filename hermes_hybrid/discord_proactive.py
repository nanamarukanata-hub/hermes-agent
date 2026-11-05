"""Discord proactive participation, as pure decision logic.

Original implementation, inspired by Neptunia's mention/name-trigger,
random interjection and dead-channel starter behaviour, TomoriBot's
memory-aware participation, and the SillyTavern-Discord-Connector's
channel routing.  No code was copied from those projects (Neptunia
and the connector are MIT/AGPL respectively; see
``docs/HERMES-DEEP-MEMORY.md``).

This module never sends a message, never starts a thread, and never
calls a model.  It answers two questions for the single Hermes
gateway, which remains the only sender:

- ``should_respond(message)`` — for an inbound message, should the
  agent reply, ignore, or defer?  Mentions, replies to the agent,
  and name triggers reply; everything else is ignored unless a
  deterministic random draw (seeded by the caller) says otherwise.
- ``should_start_topic(channel)`` — for a quiet channel, is it time
  to start a topic?  Only after ``dead_channel_seconds`` of silence,
  and only up to ``max_starts_per_day`` per channel (tracked in a
  Vault JSON file, not a database).

``dry_run=True`` (the default) means the gateway logs the decision
instead of acting on it.  Enabling proactive sending is a gateway
configuration decision, not something this module does on its own.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProactivePolicy:
    enabled: bool = False
    dry_run: bool = True
    bot_user_id: str = ""
    bot_names: list[str] = field(default_factory=list)
    ignore_probability: float = 0.3
    random_interject_probability: float = 0.05
    dead_channel_seconds: float = 3600.0
    max_starts_per_day: int = 2
    allowed_channel_ids: list[str] = field(default_factory=list)
    seed: int | None = None

    def __post_init__(self):
        if not 0.0 <= self.ignore_probability <= 1.0:
            raise ValueError("ignore_probability must be in [0, 1]")
        if not 0.0 <= self.random_interject_probability <= 1.0:
            raise ValueError("random_interject_probability must be in [0, 1]")


def _mentions_bot(text: str, policy: ProactivePolicy) -> bool:
    lowered = text.lower()
    if policy.bot_user_id and f"<@{policy.bot_user_id}>" in text:
        return True
    return any(name.lower() in lowered for name in policy.bot_names)


def should_respond(
    message: dict[str, Any],
    policy: ProactivePolicy,
    *,
    now: float | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Decide on an inbound Discord message.

    ``message`` keys: ``content``, ``author_id``, ``is_reply_to_bot``,
    ``channel_id``.  Returns ``{"action": "respond"|"ignore"|"defer",
    "reason": str}``.  Defer means "hold for the queue" (the gateway's
    existing pending-message mechanism), used when another reply is
    already in flight for the same channel.
    """
    if not policy.enabled:
        return {"action": "ignore", "reason": "proactive disabled"}
    if policy.allowed_channel_ids and message.get("channel_id") not in policy.allowed_channel_ids:
        return {"action": "ignore", "reason": "channel not allowed"}
    if message.get("author_id") == policy.bot_user_id:
        return {"action": "ignore", "reason": "own message"}
    content = message.get("content", "") or ""
    if message.get("is_reply_to_bot") or _mentions_bot(content, policy):
        return {"action": "respond", "reason": "mention or reply to bot"}
    rng = rng or random.Random(policy.seed)
    if rng.random() < policy.ignore_probability:
        return {"action": "ignore", "reason": "random ignore draw"}
    if rng.random() < policy.random_interject_probability:
        return {"action": "respond", "reason": "random interjection draw"}
    return {"action": "ignore", "reason": "no trigger"}


class ProactiveWatcher:
    """Dead-channel starter tracker, persisted as one Vault JSON file."""

    def __init__(self, vault_root: str | Path, policy: ProactivePolicy):
        self.policy = policy
        self.state_path = Path(vault_root).expanduser() / "06_Automation" / "Hermes" / "DeepMemory" / "proactive.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, state: dict) -> None:
        tmp = self.state_path.with_name(f".proactive.json.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def should_start_topic(self, channel_id: str, last_activity_at: float, *, now: float | None = None) -> dict[str, Any]:
        """Return whether to start a topic in a quiet channel."""
        now = now if now is not None else time.time()
        if not self.policy.enabled:
            return {"start": False, "reason": "proactive disabled"}
        if self.policy.allowed_channel_ids and channel_id not in self.policy.allowed_channel_ids:
            return {"start": False, "reason": "channel not allowed"}
        if now - last_activity_at < self.policy.dead_channel_seconds:
            return {"start": False, "reason": "channel not dead yet"}
        state = self._load()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        key = f"{channel_id}:{day}"
        count = int(state.get(key, 0))
        if count >= self.policy.max_starts_per_day:
            return {"start": False, "reason": "daily start cap reached"}
        state[key] = count + 1
        self._save(state)
        return {"start": True, "reason": "dead channel, under daily cap", "dry_run": self.policy.dry_run}
