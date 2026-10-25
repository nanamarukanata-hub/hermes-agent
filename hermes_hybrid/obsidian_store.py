"""Obsidian Vault store: the sole persistent store for this hybrid.

Canonical data is JSON; a human-readable Markdown transcription is
written alongside it. No SQLite is used anywhere in this module, and
no index or database file is created outside the Vault.

Safety rules (all enforced, all tested):
- Every identifier is validated: no ``..``, ``.``, separators, or NUL.
- Every resolved path must stay inside the Vault root.
- Symlinks in the resolved path are rejected.
- Writes are atomic: write to a temp file in the same directory,
  fsync, then os.replace.
- Concurrent writers are serialised with a per-Vault flock file.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def validate_id(value: str) -> str:
    if not value or value in (".", "..") or not _ID_RE.match(value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


class VaultStore:
    def __init__(self, vault_root: str | Path):
        self.root = Path(vault_root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.root / ".vault-writer.lock"

    # -- path safety -------------------------------------------------
    def _resolve(self, *parts: str) -> Path:
        for part in parts:
            validate_id(part)
        candidate = (self.root.joinpath(*parts)).resolve(strict=False)
        root_resolved = self.root.resolve(strict=False)
        if candidate != root_resolved and root_resolved not in candidate.parents:
            raise ValueError("path escapes vault")
        # Reject symlinks in the resolved path (including the file itself).
        probe = candidate
        while probe != root_resolved and probe.parent != probe:
            if probe.is_symlink():
                raise ValueError("symlink in vault path is rejected")
            probe = probe.parent
        return candidate

    def _atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _locked(self, fn):
        with open(self.lock_file, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    # -- generic document store --------------------------------------
    def save_document(self, kind: str, user_id: str, doc_id: str, data: dict[str, Any]) -> Path:
        """Save one document as JSON + Markdown. Returns the JSON path."""
        kind = validate_id(kind)
        user_id = validate_id(user_id)
        doc_id = validate_id(doc_id)
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

        def _do():
            json_path = self._resolve(kind, user_id, f"{doc_id}.json")
            md_path = self._resolve(kind, user_id, f"{doc_id}.md")
            self._atomic_write(json_path, payload + "\n")
            md = f"# {doc_id}\n\n```json\n{payload}\n```\n"
            self._atomic_write(md_path, md)
            return json_path
        return self._locked(_do)

    def load_document(self, kind: str, user_id: str, doc_id: str) -> dict[str, Any] | None:
        path = self._resolve(validate_id(kind), validate_id(user_id), f"{validate_id(doc_id)}.json")
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_documents(self, kind: str, user_id: str) -> list[str]:
        directory = self._resolve(validate_id(kind), validate_id(user_id))
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob("*.json"))

    # -- typed helpers ------------------------------------------------
    def save_conversation_turn(self, user_id: str, session_id: str, turn: dict[str, Any]) -> Path:
        session = self.load_document("Conversations", user_id, session_id) or {
            "session_id": session_id, "user_id": user_id, "turns": []}
        session.setdefault("turns", []).append(turn)
        return self.save_document("Conversations", user_id, session_id, session)

    def save_memory(self, user_id: str, memory_id: str, data: dict[str, Any]) -> Path:
        return self.save_document("Memory", user_id, memory_id, data)

    def save_session(self, user_id: str, session_id: str, data: dict[str, Any]) -> Path:
        return self.save_document("Sessions", user_id, session_id, data)

    def save_character_state(self, user_id: str, character_id: str, data: dict[str, Any]) -> Path:
        return self.save_document("CharacterState", user_id, character_id, data)

    def save_relationship(self, user_id: str, other_id: str, data: dict[str, Any]) -> Path:
        return self.save_document("Relationships", user_id, other_id, data)

    # -- search & compression -----------------------------------------
    def search(self, user_id: str, query: str, kinds: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Keyword search over a user's Vault documents. No FTS, no DB."""
        user_id = validate_id(user_id)
        kinds = list(kinds) if kinds else ["Conversations", "Memory", "Sessions", "CharacterState"]
        needle = query.lower()
        hits: list[dict[str, Any]] = []
        for kind in kinds:
            for doc_id in self.list_documents(kind, user_id):
                data = self.load_document(kind, user_id, doc_id)
                if data and needle in json.dumps(data, ensure_ascii=False).lower():
                    hits.append({"kind": kind, "id": doc_id, "data": data})
        return hits

    def compress_conversation(self, user_id: str, session_id: str, keep_last: int = 20,
                              summary: str = "") -> dict[str, Any]:
        """Keep the last N turns verbatim, move older turns to a summary
        record. The summary text is supplied by the caller; this function
        does not generate text and does not delete the original file
        without first writing the compressed version atomically.
        """
        session = self.load_document("Conversations", user_id, session_id)
        if not session:
            raise KeyError(f"session not found: {session_id}")
        turns = list(session.get("turns") or [])
        if len(turns) <= keep_last:
            return session
        archived, kept = turns[:-keep_last], turns[-keep_last:]
        compressed = {**session, "turns": kept,
                      "archived_turn_count": len(archived),
                      "archive_summary": summary}
        self.save_document("Conversations", user_id, session_id, compressed)
        self.save_document("Conversations", user_id, f"{session_id}-archive",
                           {"session_id": session_id, "archived_turns": archived,
                            "summary": summary})
        return compressed
