"""Obsidian Vault store, unified with the PR #11 / PR #16 layout.

Canonical layout (all under ``HERMES_OBSIDIAN_VAULT``):

- ``06_Automation/Hermes/Sessions/<session_id>/metadata.json``
  — exactly the PR #11 ``SessionDB`` format: ``id``, ``source``,
  ``started_at``, ``ended_at``, ``user_id``, ``messages`` (each with
  ``role``/``content``/``timestamp``), plus a generated ``Transcript.md``.
  A gateway ``session_key`` (colon form, e.g.
  ``agent:main:discord:dm:<chat_id>``) is NOT a session id: the gateway
  keeps the key -> id mapping in ``GatewayRouting/sessions.json`` and
  the session id itself is the timestamp-hex id created by the gateway.
- ``06_Automation/Hermes/Memory/<memory_id>.md``
  — PR #16 style Markdown with a frontmatter block (``id``, ``user_id``,
  ``kind``, ``created_at``) and the memory text as the body.
- ``06_Automation/Hermes/CharacterState/<user_id>/<character_id>.json``
  — RP character state (new in the hybrid layer; no PR #11 equivalent).
- ``06_Automation/Hermes/Relationships/<user_id>/<other_id>.json``
- ``06_Automation/Hermes/GatewayRouting/sessions.json``
  — gateway session-key -> session-id index (owned by gateway/session.py;
  this module only exposes its path).

No SQLite is used anywhere in this module, and no index or database
file is created outside the Vault.

Safety rules (all enforced, all tested):
- Every user-supplied identifier is validated: no ``..``, ``.``,
  separators, or NUL.
- Every resolved path must stay inside the Vault root.
- Symlink components in a Vault path are rejected before mkdir/write.
- Writes are atomic: write to a temp file in the same directory,
  fsync, then os.replace.
- Concurrent writers are serialised with a per-Vault flock file.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_TREE = ("06_Automation", "Hermes")


def validate_id(value: str) -> str:
    if not value or value in (".", "..") or not _ID_RE.match(value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        try:
            meta[key.strip()] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            meta[key.strip()] = raw
    return meta, text[end + 4:].lstrip("\n")


class VaultStore:
    def __init__(self, vault_root: str | Path):
        self.root = Path(vault_root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.tree = self.root.joinpath(*_TREE)
        self.sessions_root = self.tree / "Sessions"
        self.memory_root = self.tree / "Memory"
        self.character_root = self.tree / "CharacterState"
        self.relationships_root = self.tree / "Relationships"
        self.gateway_root = self.tree / "GatewayRouting"
        self.lock_file = self.tree / ".vault-writer.lock"

    # -- path safety -------------------------------------------------
    def _unified(self, *parts: str) -> Path:
        """Build a path under the Vault tree, rejecting symlinks."""
        candidate = self.root
        for part in parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise ValueError("symlink in vault path is rejected")
        resolved = candidate.resolve(strict=False)
        root_resolved = self.root.resolve(strict=False)
        if resolved != root_resolved and root_resolved not in resolved.parents:
            raise ValueError("path escapes vault")
        return candidate

    def session_path(self, session_id: str) -> Path:
        return self._unified(*_TREE, "Sessions", validate_id(session_id), "metadata.json")

    def transcript_path(self, session_id: str) -> Path:
        return self._unified(*_TREE, "Sessions", validate_id(session_id), "Transcript.md")

    def memory_path(self, memory_id: str) -> Path:
        return self._unified(*_TREE, "Memory", f"{validate_id(memory_id)}.md")

    def character_state_path(self, user_id: str, character_id: str) -> Path:
        return self._unified(*_TREE, "CharacterState", validate_id(user_id),
                             f"{validate_id(character_id)}.json")

    def gateway_sessions_path(self) -> Path:
        return self._unified(*_TREE, "GatewayRouting", "sessions.json")

    def _atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _locked(self, fn):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_file, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    # -- PR #11 session format ---------------------------------------
    def _render_transcript(self, item: dict[str, Any]) -> str:
        out = ["---", f"session_id: {json.dumps(item.get('id'))}",
               f"source: {json.dumps(item.get('source'))}", "---", ""]
        for m in item.get("messages", []):
            out.append(f"## {m.get('role', 'unknown')} — {m.get('timestamp', '')}")
            out.append("")
            val = m.get("content", "")
            out.append(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))
            out.append("")
        return "\n".join(out)

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        path = self.session_path(session_id)
        if path.is_symlink() or not path.exists():
            if path.is_symlink():
                raise ValueError("symlink in vault path is rejected")
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_session_data(self, session_id: str, data: dict[str, Any]) -> Path:
        # Session folders are shared by all users. Never allow a caller to
        # replace an existing session with another user's (or ownerless)
        # payload, including via save_document("Sessions", ...).
        incoming_owner = data.get("user_id")
        if incoming_owner is not None:
            validate_id(incoming_owner)

        def _do():
            existing = self.load_session(session_id)
            if existing is not None and existing.get("user_id") != incoming_owner:
                raise PermissionError("session is not owned by this user")
            item = {"id": session_id, "source": data.get("source", "gateway"),
                    "started_at": data.get("started_at", time.time()),
                    "ended_at": data.get("ended_at"),
                    "title": data.get("title"),
                    "model": data.get("model"),
                    "user_id": data.get("user_id"),
                    "parent_session_id": data.get("parent_session_id"),
                    "message_count": len(data.get("messages", []) or []),
                    "messages": list(data.get("messages", []) or []),
                    **{k: v for k, v in data.items()
                       if k not in ("id", "source", "started_at", "ended_at",
                                    "title", "model", "user_id",
                                    "parent_session_id", "messages")}}
            item["message_count"] = len(item["messages"])
            path = self.session_path(session_id)
            self._atomic_write(path, json.dumps(item, ensure_ascii=False, indent=2) + "\n")
            self._atomic_write(self.transcript_path(session_id), self._render_transcript(item))
            return path
        return self._locked(_do)

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        item = self.load_session(session_id)
        return list(item.get("messages", [])) if item else []

    def append_session_message(self, session_id: str, role: str, content: str,
                               *, user_id: str | None = None) -> Path:
        # Owner checks must happen under the same lock as the append.
        # The low-level gateway path may omit user_id; user-scoped callers
        # must supply it and may never adopt an ownerless legacy session.
        if user_id is not None:
            user_id = validate_id(user_id)

        def _locked_do():
            existing = self.load_session(session_id)
            if existing is not None and user_id is not None and existing.get("user_id") != user_id:
                raise PermissionError("session is not owned by this user")
            item = existing or {
                "id": session_id, "source": "gateway", "started_at": time.time(),
                "ended_at": None, "user_id": user_id, "messages": []}
            item.setdefault("messages", []).append({
                "id": len(item["messages"]) + 1, "session_id": session_id,
                "role": role, "content": content, "timestamp": time.time()})
            item["message_count"] = len(item["messages"])
            path = self.session_path(session_id)
            self._atomic_write(path, json.dumps(item, ensure_ascii=False, indent=2) + "\n")
            self._atomic_write(self.transcript_path(session_id), self._render_transcript(item))
            return path
        return self._locked(_locked_do)

    # -- generic document store (unified kinds) -----------------------
    def save_document(self, kind: str, user_id: str, doc_id: str, data: dict[str, Any]) -> Path:
        kind = validate_id(kind)
        user_id = validate_id(user_id)
        doc_id = validate_id(doc_id)
        if kind in ("Sessions", "Conversations"):
            # Conversations are Sessions in PR #11 format. "turns" are
            # converted to PR #11 "messages"; the session id is the doc id.
            messages = list(data.get("messages", []) or [])
            if "turns" in data and not messages:
                messages = [{"role": t.get("role", "user"),
                             "content": t.get("content", ""),
                             "timestamp": time.time()}
                            for t in data.get("turns", [])]
            merged = {**data, "id": doc_id, "user_id": user_id, "messages": messages}
            merged.pop("turns", None)
            return self.save_session_data(doc_id, merged)
        if kind == "Memory":
            return self.save_memory(user_id, doc_id, data)
        if kind == "CharacterState":
            return self.save_character_state(user_id, doc_id, data)
        if kind == "Relationships":
            return self.save_relationship(user_id, doc_id, data)
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

        def _do():
            json_path = self._unified(*_TREE, kind, user_id, f"{doc_id}.json")
            md_path = self._unified(*_TREE, kind, user_id, f"{doc_id}.md")
            self._atomic_write(json_path, payload + "\n")
            self._atomic_write(md_path, f"# {doc_id}\n\n```json\n{payload}\n```\n")
            return json_path
        return self._locked(_do)

    def load_document(self, kind: str, user_id: str, doc_id: str) -> dict[str, Any] | None:
        kind = validate_id(kind)
        user_id = validate_id(user_id)
        doc_id = validate_id(doc_id)
        if kind in ("Sessions", "Conversations"):
            item = self.load_session(doc_id)
            if item is None or item.get("user_id") != user_id:
                # A missing owner is not a public session.
                return None
            if kind == "Conversations":
                return {**item, "session_id": item.get("id"),
                        "turns": [{"role": m.get("role"), "content": m.get("content")}
                                  for m in item.get("messages", [])]}
            return item
        if kind == "Memory":
            path = self.memory_path(doc_id)
            if not path.exists():
                return None
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            if meta.get("user_id") != user_id:
                # APEX-6 legacy notes may be ownerless; do not expose them
                # through a Discord user-scoped RP memory lookup.
                return None
            body = body.strip("\n")
            return {**meta, "content": body, "text": body}
        if kind == "CharacterState":
            path = self.character_state_path(user_id, doc_id)
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if kind == "Relationships":
            path = self._unified(*_TREE, "Relationships", user_id, f"{doc_id}.json")
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        path = self._unified(*_TREE, kind, user_id, f"{doc_id}.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def list_documents(self, kind: str, user_id: str) -> list[str]:
        kind = validate_id(kind)
        user_id = validate_id(user_id)
        if kind in ("Sessions", "Conversations"):
            if not self.sessions_root.exists():
                return []
            return sorted(p.name for p in self.sessions_root.iterdir()
                          if p.is_dir() and not p.is_symlink()
                          and _ID_RE.fullmatch(p.name)
                          and (p / "metadata.json").exists()
                          and (self.load_session(p.name) or {}).get("user_id") == user_id)
        if kind == "Memory":
            if not self.memory_root.exists():
                return []
            # Memory files are not per-user on disk (PR #16); filter by frontmatter.
            out = []
            for p in sorted(self.memory_root.glob("*.md")):
                if p.is_symlink():
                    raise ValueError("symlinked memory file is rejected")
                meta, _ = _parse_frontmatter(p.read_text(encoding="utf-8"))
                if meta.get("user_id") == user_id:
                    out.append(p.stem)
            return out
        if kind == "CharacterState":
            directory = self._unified(*_TREE, "CharacterState", user_id)
            return sorted(p.stem for p in directory.glob("*.json")) if directory.exists() else []
        if kind == "Relationships":
            directory = self._unified(*_TREE, "Relationships", user_id)
            return sorted(p.stem for p in directory.glob("*.json")) if directory.exists() else []
        directory = self._unified(*_TREE, kind, user_id)
        return sorted(p.stem for p in directory.glob("*.json")) if directory.exists() else []

    # -- typed helpers ------------------------------------------------
    def save_conversation_turn(self, user_id: str, session_id: str, turn: dict[str, Any]) -> Path:
        return self.append_session_message(
            session_id, turn.get("role", "user"), turn.get("content", ""),
            user_id=validate_id(user_id))

    def save_memory(self, user_id: str, memory_id: str, data: dict[str, Any]) -> Path:
        user_id = validate_id(user_id)
        body = str(data.get("content") or data.get("text") or "")
        front = {"id": memory_id, "user_id": user_id,
                 "kind": data.get("kind", "fact"),
                 "created_at": data.get("created_at", time.time())}
        for key in ("source", "valid_until", "supersedes"):
            if data.get(key) is not None:
                front[key] = data[key]
        text = "---\n" + "".join(f"{k}: {json.dumps(v, ensure_ascii=False)}\n"
                                     for k, v in front.items()) + "---\n\n" + body + "\n"

        def _do():
            path = self.memory_path(memory_id)
            # Memory uses a flat filename layout for PR #16 compatibility.
            # Reject cross-user collisions and ownerless legacy notes under
            # the same lock as the write, rather than silently taking over.
            if path.exists():
                existing_meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
                if existing_meta.get("user_id") != user_id:
                    raise PermissionError("memory is not owned by this user")
            self._atomic_write(path, text)
            return path
        return self._locked(_do)

    def save_session(self, user_id: str, session_id: str, data: dict[str, Any]) -> Path:
        merged = {**data, "user_id": validate_id(user_id)}
        return self.save_session_data(session_id, merged)

    def save_character_state(self, user_id: str, character_id: str, data: dict[str, Any]) -> Path:
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

        def _do():
            json_path = self.character_state_path(user_id, character_id)
            md_path = self._unified(*_TREE, "CharacterState", user_id,
                                    f"{character_id}.md")
            self._atomic_write(json_path, payload + "\n")
            self._atomic_write(md_path, f"# {character_id}\n\n```json\n{payload}\n```\n")
            return json_path
        return self._locked(_do)

    def save_relationship(self, user_id: str, other_id: str, data: dict[str, Any]) -> Path:
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

        def _do():
            path = self._unified(*_TREE, "Relationships", user_id, f"{other_id}.json")
            self._atomic_write(path, payload + "\n")
            return path
        return self._locked(_do)

    # -- search & compression -----------------------------------------
    def search(self, user_id: str, query: str, kinds: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Keyword search over a user's Vault documents. No FTS, no DB."""
        user_id = validate_id(user_id)
        kinds = list(kinds) if kinds else ["Conversations", "Memory", "Sessions", "CharacterState"]
        needle = query.lower()
        hits: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for kind in kinds:
            for doc_id in self.list_documents(kind, user_id):
                key = (kind, doc_id)
                if key in seen:
                    continue
                seen.add(key)
                data = self.load_document(kind, user_id, doc_id)
                if kind in ("Sessions", "Conversations") and data and data.get("user_id") != user_id:
                    continue
                if data and needle in json.dumps(data, ensure_ascii=False).lower():
                    hits.append({"kind": kind, "id": doc_id, "data": data})
        return hits

    def compress_conversation(self, user_id: str, session_id: str, keep_last: int = 20,
                              summary: str = "") -> dict[str, Any]:
        """Keep the last N messages verbatim, archive older ones to a
        sibling session folder. The summary text is supplied by the
        caller; this function does not generate text."""
        session = self.load_session(session_id)
        if not session:
            raise KeyError(f"session not found: {session_id}")
        if session.get("user_id") != validate_id(user_id):
            raise PermissionError("session is not owned by this user")
        messages = list(session.get("messages") or [])
        if len(messages) <= keep_last:
            return session
        archived, kept = messages[:-keep_last], messages[-keep_last:]
        compressed = {**session, "messages": kept, "message_count": len(kept),
                      "archived_message_count": len(archived),
                      "archive_summary": summary}
        self.save_session_data(session_id, compressed)
        archive_id = f"{session_id}-archive"
        self.save_session_data(archive_id, {
            "id": archive_id, "source": session.get("source", "gateway"),
            "started_at": session.get("started_at"), "user_id": session.get("user_id"),
            "messages": archived, "summary": summary})
        return compressed
