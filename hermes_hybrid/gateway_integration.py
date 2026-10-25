"""Discord Gateway -> RP Engine integration.

This is the wiring layer between the official Hermes gateway and the
hybrid RP Engine. It does NOT re-implement the gateway, the Discord
adapter, or model generation:

- Discord messages already flow: Discord adapter -> gateway/run.py
  ``_handle_message_with_agent`` -> ``_run_agent``. That handler is
  the insertion point (see the RP hook block in gateway/run.py).
- This module is called from that handler, before ``_run_agent`` and
  after it returns. It is deliberately duck-typed: the gateway passes
  its real ``SessionDB`` (PR #11) if available, and tests pass a fake
  with the same ``get_messages_as_conversation`` / ``search_messages``
  methods. No Discord token, network, or model call happens here.
- If no character card is active for the user, ``before_agent``
  returns an empty string and the gateway behaves exactly as before.
  No character or world setting is invented here: a card only exists
  if the user stored one in the Vault (see ``set_active_card``).

Session keys mirror gateway/session.py ``build_session_key`` for
Discord (DM / group / channel / thread rules, per-user isolation).
The session *id* used by SessionDB is different: it is the gateway's
timestamp-hex id, mapped from the key in
``06_Automation/Hermes/GatewayRouting/sessions.json``. RP character
state is keyed by (user_id, character_id), not by session id, so it
survives a session reset / gateway restart.
"""
from __future__ import annotations

from typing import Any

from .obsidian_store import VaultStore, validate_id
from .rp_engine import CharacterCard, RPCharacterState, WorldBook


def discord_session_key(*, chat_type: str, chat_id: str = "", thread_id: str = "",
                        user_id: str = "", group_sessions_per_user: bool = True,
                        thread_sessions_per_user: bool = False,
                        platform: str = "discord") -> str:
    """Mirror gateway/session.py build_session_key (Discord rules)."""
    if chat_type == "dm":
        if chat_id:
            if thread_id:
                return f"agent:main:{platform}:dm:{chat_id}:{thread_id}"
            return f"agent:main:{platform}:dm:{chat_id}"
        if thread_id:
            return f"agent:main:{platform}:dm:{thread_id}"
        return f"agent:main:{platform}:dm"
    parts = ["agent:main", platform, chat_type]
    if chat_id:
        parts.append(chat_id)
    if thread_id:
        parts.append(thread_id)
    isolate = group_sessions_per_user
    if thread_id and not thread_sessions_per_user:
        isolate = False
    if isolate and user_id:
        parts.append(str(user_id))
    return ":".join(parts)


def build_rp_prompt(state: RPCharacterState, *, world_hits: list[dict[str, Any]] | None = None,
                    memory_hits: list[dict[str, Any]] | None = None) -> str:
    """Render RP state as a context section for the conversation model.

    Only data the user stored (card, psychology, emotion, scene,
    World Book entries, Vault memories) is included. No new setting
    is invented, and no content filter is applied.
    """
    card = state.card
    lines = ["## RP Context (from the active character card and Vault state)", ""]
    lines.append(f"**Character:** {card.name}")
    if card.description:
        lines.append(f"**Description:** {card.description}")
    if card.personality:
        lines.append(f"**Personality:** {card.personality}")
    if card.scenario:
        lines.append(f"**Scenario:** {card.scenario}")
    if card.first_message:
        lines.append(f"**Opening message (style anchor):** {card.first_message}")
    if state.psychology.attachment or state.psychology.core_schema:
        lines.append(f"**Psychology:** attachment={state.psychology.attachment}; "
                     f"defence={state.psychology.defence}; "
                     f"core_schema={state.psychology.core_schema}")
    lines.append(f"**Emotion:** {state.emotion.label} "
                 f"(valence={state.emotion.valence:.2f}, arousal={state.emotion.arousal:.2f})")
    bar = state.scene.render_status_bar()
    if bar:
        lines.append(f"**Scene status:** {bar}")
    lines.append(f"**Relationship stage:** {state.relationship_stage}")
    if world_hits:
        lines.append("")
        lines.append("**World Book entries matched:**")
        for hit in world_hits:
            lines.append(f"- {hit.get('content', '')}")
    if memory_hits:
        lines.append("")
        lines.append("**Long-term memories matched:**")
        for hit in memory_hits:
            data = hit.get("data") or hit
            text = data.get("content") or data.get("text") or str(data)[:200]
            lines.append(f"- {text}")
    return "\n".join(lines)


class RPConversation:
    """Per-user RP state on top of the unified Vault store."""

    def __init__(self, store: VaultStore, session_db: Any | None = None):
        self.store = store
        self.session_db = session_db

    # -- card ---------------------------------------------------------
    def set_active_card(self, user_id: str, card: CharacterCard) -> None:
        state = RPCharacterState(card=card)
        self.store.save_character_state(user_id, validate_id(card.name or "character"),
                                        state.to_dict())
        # "active" is a pointer, not a second copy of the card.
        self.store.save_character_state(user_id, "active",
                                        {"active_character": card.name,
                                         "character_state_ref": card.name})

    def load_active_state(self, user_id: str) -> RPCharacterState | None:
        pointer = self.store.load_document("CharacterState", user_id, "active")
        if not pointer or not pointer.get("active_character"):
            return None
        data = self.store.load_document("CharacterState", user_id, pointer["active_character"])
        if not data:
            return None
        card_data = data.get("card") or {}
        card = CharacterCard(
            name=card_data.get("name", ""), description=card_data.get("description", ""),
            personality=card_data.get("personality", ""),
            scenario=card_data.get("scenario", ""),
            first_message=card_data.get("first_message", ""),
            dialogue_examples=card_data.get("dialogue_examples", ""),
            tags=list(card_data.get("tags") or []),
            world_book=list(card_data.get("world_book") or []))
        from .rp_engine import EmotionalState, PsychologicalProfile, SceneState
        psych = data.get("psychology") or {}
        emo = data.get("emotion") or {}
        scene = data.get("scene") or {}
        state = RPCharacterState(
            card=card,
            psychology=PsychologicalProfile(
                attachment=psych.get("attachment", ""), defence=psych.get("defence", ""),
                core_schema=psych.get("core_schema", ""),
                needs_hierarchy=list(psych.get("needs_hierarchy") or []),
                moral_reasoning=psych.get("moral_reasoning", "")),
            emotion=EmotionalState(valence=float(emo.get("valence", 0.0)),
                                   arousal=float(emo.get("arousal", 0.0)),
                                   label=emo.get("label", "neutral")),
            scene=SceneState(**{k: scene.get(k, "") for k in
                                ("location", "time", "atmosphere", "outfit",
                                 "posture", "psyche", "desire", "body")}),
            relationship_stage=data.get("relationship_stage", "stranger"),
            turn_count=int(data.get("turn_count", 0)))
        state.world_book = WorldBook(list(card.world_book))
        return state

    # -- gateway hook ---------------------------------------------------
    def before_agent(self, user_id: str, session_id: str, message_text: str) -> str:
        """Build the RP prompt section for one inbound message.

        Returns "" when no card is active (gateway unchanged). Otherwise
        searches Vault memory, the session history (via SessionDB when
        supplied), and the card's World Book, and renders them.
        """
        state = self.load_active_state(user_id)
        if state is None:
            return ""
        # Memory search is keyword-based: a full user message is not a
        # single keyword, so search its significant words and deduplicate.
        memory_hits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for word in sorted({w.strip(".,!?\"'").lower() for w in message_text.split()
                            if len(w.strip(".,!?\"'")) >= 3}):
            for hit in self.store.search(user_id, word, kinds=["Memory"]):
                if hit["id"] not in seen:
                    seen.add(hit["id"])
                    memory_hits.append(hit)
                if len(memory_hits) >= 5:
                    break
            if len(memory_hits) >= 5:
                break
        world_hits = state.world_book.search(message_text, limit=5)
        # Session history is already loaded by the gateway; this hook
        # only adds RP-relevant memory, it does not re-load history.
        prompt = build_rp_prompt(state, world_hits=world_hits, memory_hits=memory_hits)
        # Deep memory (layered, episodic, graph-aware) is a no-op when
        # no DeepMemory records exist, so existing RP behaviour is
        # unchanged for users who have not stored any.
        import os as _os

        if _os.environ.get("HERMES_DEEP_MEMORY_ENABLED", "1").lower() not in ("0", "false", "no"):
            try:
                from .deep_memory import DeepMemory

                deep = DeepMemory(self.store.root)
                result = deep.retrieve(message_text, budget_tokens=500, viewer=user_id)
                if result["items"]:
                    prompt += "\n\n## Deep Memory (layered, token-budgeted)\n" + result["section"] + "\n"
                # Scene continuity: the most recent sealed scene's
                # compact state is injected as well.
                recent = deep.recent_scenes(limit=1)
                if recent:
                    ctx = deep.scene_context(recent[0]["id"])
                    if ctx:
                        prompt += "\n## Scene continuity\n" + ctx + "\n"
            except Exception:
                pass
        return prompt

    def after_agent(self, user_id: str, session_id: str, user_message: str,
                    assistant_response: str) -> RPCharacterState | None:
        """Persist RP state after a completed turn.

        Conversation turns themselves are persisted by the gateway's
        SessionDB (PR #11); this only advances RP turn state so a
        restart restores the same character, emotion, and scene.
        """
        state = self.load_active_state(user_id)
        if state is None:
            return None
        state.turn_count += 1
        self.store.save_character_state(user_id, validate_id(state.card.name or "character"),
                                        state.to_dict())
        return state

    def restore(self, user_id: str, session_id: str) -> dict[str, Any]:
        """Restart recovery: RP state + session history from the Vault."""
        state = self.load_active_state(user_id)
        history: list[dict[str, Any]] = []
        if self.session_db is not None:
            try:
                history = list(self.session_db.get_messages_as_conversation(session_id))
            except Exception:
                history = []
        if not history:
            history = [{"role": m.get("role"), "content": m.get("content")}
                       for m in self.store.get_messages(session_id)]
        return {"rp_state": state.to_dict() if state else None,
                "history": history,
                "session_id": session_id}
