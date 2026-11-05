"""Unified RP Engine: character, psychology, emotion, scene, world book.

Original implementation for the Hermes hybrid project. It re-implements
the *data model* described by the source projects (card fields, a
five-dimension psychology profile, a per-turn scene status record, and a
keyword World Book index) in new code. It does not copy prompt text,
character settings, or world content from any source project, and it
does not add a content filter: it only tracks state and validates
structure (for example, a status record must be present each turn).
"""
from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _clean_id(value: str) -> str:
    if not value or value in (".", ".."):
        raise ValueError("invalid identifier")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError("identifier must not contain path separators")
    return value


@dataclass
class CharacterCard:
    """A SillyTavern-compatible character card, stored as data only."""

    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_message: str = ""
    dialogue_examples: str = ""
    tags: list[str] = field(default_factory=list)
    world_book: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_sillytavern(cls, data: dict[str, Any]) -> "CharacterCard":
        # SillyTavern V2/V3 wraps the card in a ``data`` object; V1 is flat.
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        if not isinstance(inner, dict):
            raise ValueError("character card must be a JSON object")
        book = inner.get("character_book") or {}
        entries = book.get("entries") or [] if isinstance(book, dict) else []
        return cls(
            name=str(inner.get("name") or ""),
            description=str(inner.get("description") or ""),
            personality=str(inner.get("personality") or ""),
            scenario=str(inner.get("scenario") or ""),
            first_message=str(inner.get("first_mes") or ""),
            dialogue_examples=str(inner.get("mes_example") or ""),
            tags=list(inner.get("tags") or []),
            world_book=list(entries),
            extra={k: v for k, v in inner.items()
                   if k not in {"name", "description", "personality", "scenario",
                                "first_mes", "mes_example", "tags", "character_book"}},
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "CharacterCard":
        path = Path(path)
        if path.suffix.lower() == ".png":
            return cls.from_png(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_sillytavern(data)

    @classmethod
    def from_png(cls, path: str | Path) -> "CharacterCard":
        """Extract a SillyTavern card embedded in a PNG tEXt/iTXt chunk.

        PNG chunks are parsed manually; no image library is required.
        Only a chunk whose keyword is ``chara`` is used, and its payload
        must be valid JSON. Anything else raises ValueError.
        """
        raw = Path(path).read_bytes()
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG file")
        pos = 8
        while pos + 8 <= len(raw):
            length = struct.unpack(">I", raw[pos:pos + 4])[0]
            ctype = raw[pos + 4:pos + 8].decode("ascii", errors="ignore")
            data = raw[pos + 8:pos + 8 + length]
            if ctype in ("tEXt", "iTXt"):
                if ctype == "tEXt":
                    keyword, _, payload = data.partition(b"\x00")
                    if keyword == b"chara":
                        return cls.from_sillytavern(json.loads(payload.decode("utf-8")))
                else:
                    keyword, _, rest = data.partition(b"\x00")
                    if keyword == b"chara":
                        # iTXt: compression flag, method, language, translated keyword
                        parts = rest.split(b"\x00", 3)
                        payload = parts[-1] if parts else b""
                        if len(parts) >= 2 and parts[0] == b"\x01":
                            payload = zlib.decompress(payload)
                        return cls.from_sillytavern(json.loads(payload.decode("utf-8")))
            if ctype == "IEND":
                break
            pos += 12 + length
        raise ValueError("no SillyTavern card found in PNG")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "description": self.description,
            "personality": self.personality, "scenario": self.scenario,
            "first_message": self.first_message,
            "dialogue_examples": self.dialogue_examples,
            "tags": list(self.tags), "world_book": list(self.world_book),
            "extra": dict(self.extra),
        }


@dataclass
class PsychologicalProfile:
    """Five-dimension psychology profile, as data, not as prompt text."""

    attachment: str = ""
    defence: str = ""
    core_schema: str = ""
    needs_hierarchy: list[str] = field(default_factory=list)
    moral_reasoning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"attachment": self.attachment, "defence": self.defence,
                "core_schema": self.core_schema,
                "needs_hierarchy": list(self.needs_hierarchy),
                "moral_reasoning": self.moral_reasoning}


@dataclass
class EmotionalState:
    """Bounded emotional state with inertia: no instant jumps.

    ``valence`` and ``arousal`` are in [-1, 1]. ``move`` clamps the
    per-turn change to ``max_step``, so a mood cannot flip in one turn
    without intermediate steps. This is a state rule, not a filter.
    """

    valence: float = 0.0
    arousal: float = 0.0
    label: str = "neutral"
    max_step: float = 0.35

    def move(self, valence: float, arousal: float, label: str) -> "EmotionalState":
        def clamp_step(old: float, new: float) -> float:
            delta = max(-self.max_step, min(self.max_step, new - old))
            return max(-1.0, min(1.0, old + delta))
        self.valence = clamp_step(self.valence, valence)
        self.arousal = clamp_step(self.arousal, arousal)
        self.label = label
        return self

    def to_dict(self) -> dict[str, Any]:
        return {"valence": self.valence, "arousal": self.arousal, "label": self.label}


@dataclass
class SceneState:
    """Per-turn scene status record. This is the truth source for a turn."""

    location: str = ""
    time: str = ""
    atmosphere: str = ""
    outfit: str = ""
    posture: str = ""
    psyche: str = ""
    desire: str = ""
    body: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"location": self.location, "time": self.time,
                "atmosphere": self.atmosphere, "outfit": self.outfit,
                "posture": self.posture, "psyche": self.psyche,
                "desire": self.desire, "body": self.body}

    def render_status_bar(self) -> str:
        parts = [f"{k}={v}" for k, v in self.to_dict().items() if v]
        return " | ".join(parts)


class WorldBook:
    """Keyword World Book index. Search is keyword-based, not vector.

    Entries are matched by their ``keys`` (case-insensitive substring)
    and returned in entry order. No model call is made.
    """

    def __init__(self, entries: list[dict[str, Any]] | None = None):
        self.entries: list[dict[str, Any]] = list(entries or [])

    def search(self, text: str, limit: int = 5) -> list[dict[str, Any]]:
        lowered = text.lower()
        hits: list[dict[str, Any]] = []
        for entry in self.entries:
            keys = entry.get("keys") or []
            if any(str(k).lower() in lowered for k in keys):
                hits.append(entry)
                if len(hits) >= limit:
                    break
        return hits

    def compact_index(self) -> list[dict[str, str]]:
        out = []
        for entry in self.entries:
            content = str(entry.get("content") or "")
            out.append({"keys": list(entry.get("keys") or []),
                        "summary": content[:200]})
        return out


@dataclass
class RPCharacterState:
    """Full per-character RP state for one user/session."""

    card: CharacterCard
    psychology: PsychologicalProfile = field(default_factory=PsychologicalProfile)
    emotion: EmotionalState = field(default_factory=EmotionalState)
    scene: SceneState = field(default_factory=SceneState)
    world_book: WorldBook = field(default_factory=WorldBook)
    relationship_stage: str = "stranger"
    turn_count: int = 0

    def __post_init__(self) -> None:
        if not self.world_book.entries and self.card.world_book:
            self.world_book = WorldBook(list(self.card.world_book))

    def advance_turn(self, scene: SceneState) -> str:
        """Record a new turn. Returns the status bar for that turn."""
        self.scene = scene
        self.turn_count += 1
        return scene.render_status_bar()

    def to_dict(self) -> dict[str, Any]:
        return {"card": self.card.to_dict(),
                "psychology": self.psychology.to_dict(),
                "emotion": self.emotion.to_dict(),
                "scene": self.scene.to_dict(),
                "world_book": list(self.world_book.entries),
                "relationship_stage": self.relationship_stage,
                "turn_count": self.turn_count}


def check_no_user_narration(reply: str, user_name: str) -> list[str]:
    """Structural check only: flag sentences where the reply claims to
    act/speak/feel *as* the user. This is not a content filter and it
    does not block anything by itself; callers decide how to use it.
    """
    if not user_name:
        return []
    pattern = re.compile(rf"\b{re.escape(user_name)}\b[^.?!]*\b(said|did|felt|thought|walked|smiled)\b",
                         re.IGNORECASE)
    return pattern.findall(reply)
