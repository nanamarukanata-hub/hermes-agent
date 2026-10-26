"""Opt-in adult-character roleplay voice and non-graphic scene event state.

This module does not fetch or reproduce third-party dialogue. It provides
reusable style controls, deterministic typography, and a compact event graph.
Characters must explicitly declare an adult age in their card extra data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

BEATS = ("banter", "rivalry", "rapport", "closeness", "reflection")
EVENT_TRANSITIONS = {
    "challenge": ("rivalry", 0, 1),
    "comeback": ("banter", 0, -1),
    "honesty": ("rapport", 1, -1),
    "mutual_trust": ("closeness", 2, -1),
    "reconcile": ("reflection", 1, -2),
    "playful_reset": ("banter", 0, -2),
}


@dataclass(frozen=True)
class AdultStyleProfile:
    """Opt-in style metadata. A declared age of 18+ is required."""

    age: int
    voice: str = "confident, teasing, quick-witted"
    sass: int = 4
    warmth: int = 2
    vulnerability: int = 2
    initiative: int = 3

    @classmethod
    def from_extra(cls, extra: dict[str, Any] | None) -> "AdultStyleProfile | None":
        raw = (extra or {}).get("adult_rp")
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            return None
        age = raw.get("age")
        if isinstance(age, bool) or not isinstance(age, int) or age < 18:
            return None

        def level(key: str, default: int) -> int:
            value = raw.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int):
                return default
            return max(0, min(5, value))

        voice = str(raw.get("voice") or cls.voice).strip()[:160]
        return cls(age=age, voice=voice, sass=level("sass", 4),
                   warmth=level("warmth", 2),
                   vulnerability=level("vulnerability", 2),
                   initiative=level("initiative", 3))

    def prompt(self, event_state: "AdultSceneEvents") -> str:
        return (
            "## Opt-in adult-character voice (age explicitly declared: "
            + str(self.age) + ")\n"
            "Play the existing character, not a new character. Maintain adult "
            "agency, individual motives, emotional continuity and mutual consent.\n"
            "Dialogue traits: witty teasing, confident verbal sparring, "
            "occasional sincerity; contrast bravado with believable vulnerability. "
            "Do not mimic, quote, or reproduce existing authors' dialogue.\n"
            "Voice: " + self.voice + "\n"
            "Style sliders (0-5): sass=" + str(self.sass)
            + ", warmth=" + str(self.warmth)
            + ", vulnerability=" + str(self.vulnerability)
            + ", initiative=" + str(self.initiative) + "\n"
            "Current scene beat: " + event_state.beat
            + "; trust=" + str(event_state.trust)
            + "; tension=" + str(event_state.tension) + ".\n"
            "Typography: do not output adjacent ♡ symbols (including spaces "
            "between them). Do not end two consecutive replies with ♡. "
            "Avoid repetitive stock catchphrases or sentence-ending marks. "
            "Keep action and emotional beats grounded in the current scene."
        )


@dataclass
class AdultSceneEvents:
    beat: str = "banter"
    trust: int = 0
    tension: int = 0
    last_reply_ended_heart: bool = False
    event_count: int = 0
    last_event: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AdultSceneEvents":
        data = data if isinstance(data, dict) else {}
        beat = data.get("beat", "banter")
        if beat not in BEATS:
            beat = "banter"

        def bounded(key: str) -> int:
            value = data.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int):
                return 0
            return max(0, min(10, value))

        count = data.get("event_count", 0)
        if isinstance(count, bool) or not isinstance(count, int):
            count = 0
        return cls(beat=beat, trust=bounded("trust"),
                   tension=bounded("tension"),
                   last_reply_ended_heart=data.get("last_reply_ended_heart") is True,
                   event_count=max(0, count),
                   last_event=str(data.get("last_event") or "")[:64])

    def transition(self, event_name: str) -> "AdultSceneEvents":
        if event_name not in EVENT_TRANSITIONS:
            raise ValueError("unknown adult roleplay event")
        beat, trust_delta, tension_delta = EVENT_TRANSITIONS[event_name]
        self.beat = beat
        self.trust = max(0, min(10, self.trust + trust_delta))
        self.tension = max(0, min(10, self.tension + tension_delta))
        self.last_event = event_name
        self.event_count += 1
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "beat": self.beat,
            "trust": self.trust,
            "tension": self.tension,
            "last_reply_ended_heart": self.last_reply_ended_heart,
            "event_count": self.event_count,
            "last_event": self.last_event,
        }


def format_heart_punctuation(reply: str, *, previous_ended_heart: bool = False) -> str:
    """Normalize contiguous heart glyphs without touching other text.

    Preserves single hearts inside a turn. If the prior reply ended with a
    heart, removes only an immediately repeated *ending* heart this turn.
    """
    fixed = re.sub(r"♡(?:[ \t]*♡)+", "♡", reply)
    if previous_ended_heart:
        fixed = re.sub(r"♡(?=[ \t]*$)", "", fixed).rstrip(" \t")
    return fixed


def reply_ends_with_heart(reply: str) -> bool:
    return reply.rstrip().endswith("♡")
