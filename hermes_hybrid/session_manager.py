"""RP session management on top of the Obsidian store.

One session is keyed by (discord_user_id, channel_id, character_id).
Sessions for different users never share a directory, so state cannot
leak between users. Restart recovery is just re-loading the session
document from the Vault; no in-memory-only state is trusted.

Modes: ``chat`` (reply mode) and ``live`` (autonomous background loop,
as in OpenChara). The mode is data only; the actual loop lives in the
official Hermes gateway, which this module does not re-implement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .obsidian_store import VaultStore, validate_id
from .rp_engine import CharacterCard, RPCharacterState


@dataclass
class Session:
    user_id: str
    channel_id: str
    character_id: str
    mode: str = "chat"
    character_state: dict[str, Any] | None = None
    turn_count: int = 0

    @property
    def session_id(self) -> str:
        return f"{self.user_id}-{self.channel_id}-{self.character_id}"

    def to_dict(self) -> dict[str, Any]:
        return {"user_id": self.user_id, "channel_id": self.channel_id,
                "character_id": self.character_id, "mode": self.mode,
                "character_state": self.character_state,
                "turn_count": self.turn_count}


class SessionManager:
    def __init__(self, store: VaultStore):
        self.store = store

    def start(self, user_id: str, channel_id: str, card: CharacterCard,
              mode: str = "chat") -> Session:
        validate_id(user_id); validate_id(channel_id)
        character_id = validate_id(card.name or "character")
        if mode not in ("chat", "live"):
            raise ValueError("mode must be chat or live")
        state = RPCharacterState(card=card)
        session = Session(user_id=user_id, channel_id=channel_id,
                          character_id=character_id, mode=mode,
                          character_state=state.to_dict(), turn_count=0)
        self.store.save_session(user_id, session.session_id, session.to_dict())
        self.store.save_character_state(user_id, character_id, state.to_dict())
        return session

    def restore(self, user_id: str, session_id: str) -> Session | None:
        data = self.store.load_document("Sessions", validate_id(user_id), validate_id(session_id))
        if not data:
            return None
        return Session(user_id=data["user_id"], channel_id=data["channel_id"],
                       character_id=data["character_id"], mode=data.get("mode", "chat"),
                       character_state=data.get("character_state"),
                       turn_count=data.get("turn_count", 0))

    def switch_character(self, session: Session, card: CharacterCard) -> Session:
        """Switch character inside one user's session. The previous
        character's state stays in the Vault under its own id."""
        new_state = RPCharacterState(card=card)
        session.character_id = validate_id(card.name or "character")
        session.character_state = new_state.to_dict()
        session.turn_count = 0
        self.store.save_session(session.user_id, session.session_id, session.to_dict())
        self.store.save_character_state(session.user_id, session.character_id, new_state.to_dict())
        return session

    def record_turn(self, session: Session, role: str, content: str) -> None:
        session.turn_count += 1
        self.store.save_conversation_turn(session.user_id, session.session_id,
                                          {"role": role, "content": content,
                                           "turn": session.turn_count})
        self.store.save_session(session.user_id, session.session_id, session.to_dict())
