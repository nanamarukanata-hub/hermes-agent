"""
Session mirroring for cross-platform message delivery.

When a message is sent to a platform (via send_message or cron delivery),
this module appends a "delivery-mirror" record to the target session's
transcript so the receiving-side agent has context about what was sent.

Standalone -- works from CLI, cron, and gateway contexts without needing
the full SessionStore machinery.

Canonical-store rule: mirror records are written ONLY to the Obsidian
SessionDB (the same store SessionStore uses). They are never written to
a legacy JSONL file: the old dual write created a second, divergent copy
of the history under the local Hermes home, outside the Vault.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from hermes_cli.config import get_hermes_home

logger = logging.getLogger(__name__)

# Legacy index location (read-only fallback for pre-Vault installs).
_SESSIONS_DIR = get_hermes_home() / "sessions"
_SESSIONS_INDEX = _SESSIONS_DIR / "sessions.json"


def _index_candidates():
    """Session-index files to consult, canonical (Vault) first.

    The gateway's SessionStore keeps its index in the Vault at
    ``06_Automation/Hermes/GatewayRouting/sessions.json``; the legacy
    home-local index is only a fallback for pre-migration installs.
    """
    candidates = []
    vault = os.environ.get("HERMES_OBSIDIAN_VAULT")
    if vault:
        candidates.append(
            Path(vault).expanduser() / "06_Automation" / "Hermes"
            / "GatewayRouting" / "sessions.json"
        )
    candidates.append(_SESSIONS_INDEX)
    return candidates


def mirror_to_session(
    platform: str,
    chat_id: str,
    message_text: str,
    source_label: str = "cli",
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> bool:
    """
    Append a delivery-mirror message to the target session's transcript.

    Finds the gateway session that matches the given platform + chat_id,
    then writes a mirror entry to the canonical Obsidian SessionDB only.

    Returns True if mirrored successfully, False if no matching session or error.
    All errors are caught -- this is never fatal.
    """
    try:
        session_id = _find_session_id(
            platform,
            str(chat_id),
            thread_id=thread_id,
            user_id=user_id,
        )
        if not session_id:
            logger.debug(
                "Mirror: no session found for %s:%s:%s:%s",
                platform,
                chat_id,
                thread_id,
                user_id,
            )
            return False

        mirror_msg = {
            "role": "assistant",
            "content": message_text,
            "timestamp": datetime.now().isoformat(),
            "mirror": True,
            "mirror_source": source_label,
        }

        _append_to_canonical(session_id, mirror_msg)

        logger.debug("Mirror: wrote to session %s (from %s)", session_id, source_label)
        return True

    except Exception as e:
        logger.debug(
            "Mirror failed for %s:%s:%s:%s: %s",
            platform,
            chat_id,
            thread_id,
            user_id,
            e,
        )
        return False


def _find_session_id(
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[str]:
    """
    Find the active session_id for a platform + chat_id pair.

    Scans the canonical Vault sessions index (falling back to the legacy
    home-local index) and matches where origin.chat_id == chat_id on the
    right platform.  DM session keys don't embed the chat_id
    (e.g. "agent:main:telegram:dm"), so we check the origin dict.

    When *user_id* is provided, prefer exact sender matches. If multiple
    same-chat candidates exist and none matches the user, return None instead
    of guessing and contaminating another participant's session.
    """
    data = None
    for candidate in _index_candidates():
        if candidate.exists():
            try:
                with open(candidate, encoding="utf-8") as f:
                    data = json.load(f)
                break
            except Exception:
                continue
    if data is None:
        return None

    platform_lower = platform.lower()
    candidates = []

    for _key, entry in data.items():
        origin = entry.get("origin") or {}
        entry_platform = (origin.get("platform") or entry.get("platform", "")).lower()

        if entry_platform != platform_lower:
            continue

        origin_chat_id = str(origin.get("chat_id", ""))
        if origin_chat_id == str(chat_id):
            origin_thread_id = origin.get("thread_id")
            if thread_id is not None and str(origin_thread_id or "") != str(thread_id):
                continue
            candidates.append(entry)

    if not candidates:
        return None

    if user_id:
        exact_user_matches = [
            entry for entry in candidates
            if str((entry.get("origin") or {}).get("user_id") or "") == str(user_id)
        ]
        if exact_user_matches:
            candidates = exact_user_matches
        elif len(candidates) > 1:
            return None
    elif len(candidates) > 1:
        distinct_user_ids = {
            str((entry.get("origin") or {}).get("user_id") or "").strip()
            for entry in candidates
            if str((entry.get("origin") or {}).get("user_id") or "").strip()
        }
        if len(distinct_user_ids) > 1:
            return None

    best_entry = max(candidates, key=lambda entry: entry.get("updated_at", ""))
    return best_entry.get("session_id")


def _append_to_canonical(session_id: str, message: dict) -> None:
    """Append a message to the canonical Obsidian SessionDB."""
    db = None
    try:
        from hermes_state import SessionDB
        db = SessionDB()
        db.append_message(
            session_id=session_id,
            role=message.get("role", "assistant"),
            content=message.get("content"),
        )
    except Exception as e:
        logger.debug("Mirror canonical write failed: %s", e)
    finally:
        if db is not None:
            db.close()


# Backwards-compatible alias: this was the SessionDB write half of the
# old dual write (the JSONL half is gone by design).
_append_to_sqlite = _append_to_canonical
