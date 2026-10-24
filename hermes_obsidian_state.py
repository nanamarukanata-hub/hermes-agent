"""Obsidian Vault session backend for Hermes (SQLite-free).

Canonical session data is JSON inside the Obsidian Vault, with a generated
Markdown transcript for human inspection. Linux flock serializes writers.
No database, WAL, FTS, or non-Vault persistent index is created.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_VALID_ID = re.compile(r"^[\w.-]{1,180}$", flags=re.ASCII)


class SessionDB:
    """Hermes SessionDB-compatible file backend for the Discord/CLI core."""

    def __init__(self, db_path=None):
        base = os.environ.get("HERMES_OBSIDIAN_VAULT")
        if not base:
            raise RuntimeError("HERMES_OBSIDIAN_VAULT is required: SQLite is disabled")
        self.vault = Path(base).expanduser().resolve()
        if not self.vault.is_dir():
            raise RuntimeError(f"Obsidian Vault directory is missing: {self.vault}")
        self.root = self.vault / "06_Automation" / "Hermes" / "Sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.resolve().is_relative_to(self.vault):
            raise RuntimeError("Sessions directory must remain inside the Vault")
        self.db_path = self.root    # Compatibility: points to a directory, never a .db.
        self._lockfile = self.root / ".session-writer.lock"

    @contextmanager
    def _guard(self):
        # Never follow an attacker-controlled lockfile symlink. Every writer
        # must lock the same file inside the Vault.
        if (self._lockfile.is_symlink() or
                (self._lockfile.exists() and not self._lockfile.is_file())):
            raise RuntimeError("Unsafe Vault session lockfile")
        with self._lockfile.open("a+b") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)

    def _folder(self, session_id):
        # A valid filename is not necessarily a safe child directory: "." and
        # ".." would otherwise address Sessions/ and its parent (including
        # through delete_session's recursive removal path).
        if (not isinstance(session_id, str) or session_id in (".", "..")
                or not _VALID_ID.fullmatch(session_id)):
            raise ValueError("Invalid session ID")
        folder = self.root / session_id
        # Reject existing symlinked session folders before any read, write or
        # recursive deletion. Do not silently follow links outside the Vault.
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise RuntimeError(f"Unsafe Vault session directory: {session_id}")
        if not folder.resolve().is_relative_to(self.root.resolve()):
            raise RuntimeError(f"Session directory escapes Vault: {session_id}")
        return folder

    @staticmethod
    def _write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            dirfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _load(self, sid):
        folder = self._folder(sid)
        metadata = folder / "metadata.json"
        # A symlinked metadata file could leak arbitrary files outside the
        # Vault on reads. Reject it even when the link target is missing.
        if metadata.is_symlink():
            raise RuntimeError(f"Unsafe Vault session metadata: {sid}")
        if not metadata.exists():
            return None
        if not metadata.is_file():
            raise RuntimeError(f"Invalid Vault session metadata: {sid}")
        return json.loads(metadata.read_text(encoding="utf-8"))

    def _save(self, sid, item):
        folder = self._folder(sid)
        self._write(folder / "metadata.json", json.dumps(item, ensure_ascii=False, indent=2))
        # Markdown is a projection of canonical JSON; it can be regenerated.
        out = ["---", f"session_id: {json.dumps(sid)}",
               f"source: {json.dumps(item.get('source'))}", "---", ""]
        for m in item.get("messages", []):
            out.append(f"## {m.get('role', 'unknown')} — {m.get('timestamp', '')}")
            out.append("")
            val = m.get("content", "")
            out.append(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))
            out.append("")
        self._write(folder / "Transcript.md", "\n".join(out))

    def _items(self):
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or folder.is_symlink():
                continue
            try:
                item = self._load(folder.name)
                if item is not None:
                    yield item
            except (ValueError, OSError, json.JSONDecodeError):
                # The caller must not interpret a corrupt session as an empty one.
                raise RuntimeError(f"Unreadable Vault session: {folder.name}")

    def close(self):
        return None

    def create_session(self, session_id, source, **kwargs):
        with self._guard():
            if self._load(session_id) is None:
                now = time.time()
                item = {
                    "id": session_id, "source": source, "started_at": now,
                    "ended_at": None, "end_reason": None,
                    "title": None, "model": kwargs.get("model"),
                    "model_config": kwargs.get("model_config"),
                    "system_prompt": kwargs.get("system_prompt"),
                    "user_id": kwargs.get("user_id"),
                    "parent_session_id": kwargs.get("parent_session_id"),
                    "message_count": 0, "tool_call_count": 0,
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "reasoning_tokens": 0, "api_call_count": 0,
                    "messages": [],
                }
                self._save(session_id, item)
        return session_id

    def ensure_session(self, session_id, source="unknown", model=None, **kwargs):
        return self.create_session(session_id, source, model=model, **kwargs)

    def _edit(self, session_id, transform):
        with self._guard():
            item = self._load(session_id)
            if item is None:
                return None
            result = transform(item)
            self._save(session_id, item)
            return result

    def end_session(self, session_id, end_reason):
        def fn(item):
            if item["ended_at"] is None:
                item["ended_at"] = time.time()
                item["end_reason"] = end_reason
        self._edit(session_id, fn)

    def reopen_session(self, session_id):
        self._edit(session_id, lambda item: item.update(ended_at=None, end_reason=None))

    def update_system_prompt(self, session_id, system_prompt):
        self._edit(session_id, lambda item: item.update(system_prompt=system_prompt))

    def update_token_counts(self, session_id, input_tokens=0, output_tokens=0, model=None,
                            cache_read_tokens=0, cache_write_tokens=0, reasoning_tokens=0,
                            estimated_cost_usd=None, actual_cost_usd=None,
                            cost_status=None, cost_source=None, pricing_version=None,
                            billing_provider=None, billing_base_url=None, billing_mode=None,
                            api_call_count=0, absolute=False):
        self.ensure_session(session_id)
        def fn(item):
            for key, value in {
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
                "reasoning_tokens": reasoning_tokens, "api_call_count": api_call_count,
            }.items():
                item[key] = value if absolute else item.get(key, 0) + value
            if estimated_cost_usd is not None:
                old = item.get("estimated_cost_usd") or 0
                item["estimated_cost_usd"] = estimated_cost_usd if absolute else old + estimated_cost_usd
            if actual_cost_usd is not None:
                old = item.get("actual_cost_usd") or 0
                item["actual_cost_usd"] = actual_cost_usd if absolute else old + actual_cost_usd
            for key, val in {"cost_status": cost_status, "cost_source": cost_source,
                             "pricing_version": pricing_version, "billing_provider": billing_provider,
                             "billing_base_url": billing_base_url, "billing_mode": billing_mode}.items():
                if val is not None:
                    item[key] = val
            if model and not item.get("model"):
                item["model"] = model
        self._edit(session_id, fn)

    def get_session(self, session_id):
        item = self._load(session_id)
        return {k: v for k, v in item.items() if k != "messages"} if item else None

    def resolve_session_id(self, fragment):
        exact = self.get_session(fragment)
        if exact: return fragment
        matches = [s["id"] for s in self._items() if s["id"].startswith(fragment)]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def sanitize_title(title):
        if not title: return None
        clean = " ".join("".join(c for c in title if c.isprintable()).split())
        return clean[:100] if clean else None

    def set_session_title(self, session_id, title):
        title = self.sanitize_title(title)
        with self._guard():
            if title and any(s["id"] != session_id and s.get("title") == title for s in self._items()):
                raise ValueError("Session title already in use")
            item = self._load(session_id)
            if item is None: return False
            item["title"] = title
            self._save(session_id, item)
            return True

    def get_session_title(self, session_id):
        s = self.get_session(session_id)
        return s.get("title") if s else None

    def get_session_by_title(self, title):
        return next((s for s in self.search_sessions(limit=100000) if s.get("title") == title), None)

    def resolve_session_by_title(self, title):
        s = self.get_session_by_title(title)
        return s["id"] if s else None

    def get_next_title_in_lineage(self, base_title):
        titles = {s.get("title") for s in self.search_sessions(limit=100000)}
        if base_title not in titles: return base_title
        for n in range(2, 100001):
            choice = f"{base_title} ({n})"
            if choice not in titles: return choice
        raise RuntimeError("No available lineage title")

    def get_compression_tip(self, session_id):
        seen = set()
        sid = session_id
        while sid not in seen:
            seen.add(sid)
            children = [s for s in self._items() if s.get("parent_session_id") == sid
                        and s.get("source") != "subagent"]
            if not children: return sid
            sid = max(children, key=lambda s: s["started_at"])["id"]
        return sid

    def append_message(self, session_id, role, content=None, tool_name=None,
                       tool_calls=None, tool_call_id=None, token_count=None,
                       finish_reason=None, reasoning=None, reasoning_content=None,
                       reasoning_details=None, codex_reasoning_items=None,
                       codex_message_items=None):
        self.ensure_session(session_id)
        def fn(item):
            msg_id = len(item["messages"]) + 1
            entry = {"id": msg_id, "session_id": session_id, "role": role,
                     "content": content, "tool_name": tool_name,
                     "tool_calls": tool_calls, "tool_call_id": tool_call_id,
                     "token_count": token_count, "finish_reason": finish_reason,
                     "reasoning": reasoning, "reasoning_content": reasoning_content,
                     "reasoning_details": reasoning_details,
                     "codex_reasoning_items": codex_reasoning_items,
                     "codex_message_items": codex_message_items,
                     "timestamp": time.time()}
            item["messages"].append(entry)
            item["message_count"] = len(item["messages"])
            item["tool_call_count"] = sum(len(m["tool_calls"]) if isinstance(m.get("tool_calls"), list) else bool(m.get("tool_calls")) for m in item["messages"])
            return msg_id
        return self._edit(session_id, fn)

    def replace_messages(self, session_id, messages):
        self.ensure_session(session_id)
        def fn(item):
            now = time.time()
            item["messages"] = [dict(m, id=i + 1, session_id=session_id,
                                     timestamp=m.get("timestamp", now + i * 0.000001))
                                for i, m in enumerate(messages)]
            item["message_count"] = len(messages)
            item["tool_call_count"] = sum(len(m["tool_calls"]) if isinstance(m.get("tool_calls"), list) else bool(m.get("tool_calls")) for m in messages)
        self._edit(session_id, fn)

    def get_messages(self, session_id):
        item = self._load(session_id)
        return list(item["messages"]) if item else []

    def _lineage(self, session_id):
        path, seen = [], set()
        while session_id and session_id not in seen:
            seen.add(session_id)
            path.append(session_id)
            s = self.get_session(session_id)
            if not s: break
            session_id = s.get("parent_session_id")
        return list(reversed(path))

    def resolve_resume_session_id(self, session_id):
        if self.get_messages(session_id): return session_id
        sid = self.get_compression_tip(session_id)
        return sid if self.get_messages(sid) else session_id

    def get_messages_as_conversation(self, session_id, include_ancestors=False):
        sessions = self._lineage(session_id) if include_ancestors else [session_id]
        messages = []
        for sid in sessions:
            for msg in self.get_messages(sid):
                # Internal storage fields are excluded from model conversation.
                fields = ("role", "content", "tool_call_id", "tool_calls", "tool_name",
                          "finish_reason", "reasoning", "reasoning_content",
                          "reasoning_details", "codex_reasoning_items", "codex_message_items")
                messages.append({key: msg[key] for key in fields if msg.get(key) is not None})
        return messages

    def search_sessions(self, source=None, limit=20, offset=0):
        data = []
        for item in self._items():
            if source and item.get("source") != source: continue
            row = {k: v for k, v in item.items() if k != "messages"}
            row["last_active"] = max((m.get("timestamp", 0) for m in item["messages"]), default=item["started_at"])
            data.append(row)
        data.sort(key=lambda s: (s["last_active"], s["started_at"]), reverse=True)
        return data[offset:offset + limit]

    def list_sessions_rich(self, source=None, exclude_sources=None, limit=20, offset=0,
                           include_children=False, project_compression_tips=True,
                           order_by_last_active=False):
        found = []
        for s in self._items():
            if source and s["source"] != source: continue
            if exclude_sources and s["source"] in exclude_sources: continue
            if not include_children and s.get("parent_session_id"): continue
            if project_compression_tips:
                tip = self.get_compression_tip(s["id"])
                s = self._load(tip) or s
            messages = s["messages"]
            first = next((str(m.get("content", "")) for m in messages if m["role"] == "user"), "")
            row = {k: v for k, v in s.items() if k != "messages"}
            row["preview"] = first[:60]
            row["last_active"] = max((m.get("timestamp", 0) for m in messages), default=s["started_at"])
            found.append(row)
        found.sort(key=lambda s: (s["last_active"] if order_by_last_active else s["started_at"]), reverse=True)
        return found[offset:offset + limit]

    def search_messages(self, query, source_filter=None, exclude_sources=None,
                        role_filter=None, limit=20, offset=0):
        if not query.strip(): return []
        found = []
        for item in self._items():
            if source_filter is not None and item["source"] not in source_filter: continue
            if exclude_sources is not None and item["source"] in exclude_sources: continue
            for idx, msg in enumerate(item["messages"]):
                if role_filter and msg["role"] not in role_filter: continue
                content = str(msg.get("content") or "")
                if query.casefold() not in (content + " " + str(msg.get("tool_name", ""))).casefold(): continue
                found.append({"id": msg["id"], "session_id": item["id"],
                              "role": msg["role"], "snippet": content[:120],
                              "timestamp": msg["timestamp"], "source": item["source"],
                              "model": item.get("model"), "session_started": item["started_at"],
                              "context": [{"role": m["role"], "content": str(m.get("content", ""))[:200]}
                                          for m in item["messages"][max(0, idx - 1):idx + 2]]})
        found.sort(key=lambda r: r["timestamp"], reverse=True)
        return found[offset:offset + limit]

    def session_count(self, source=None):
        return sum(1 for s in self._items() if source is None or s["source"] == source)

    def message_count(self, session_id=None):
        return len(self.get_messages(session_id)) if session_id else sum(len(s["messages"]) for s in self._items())

    def export_session(self, session_id):
        item = self._load(session_id)
        return dict(item) if item else None

    def export_all(self, source=None):
        return [dict(s) for s in self._items() if source is None or s["source"] == source]

    def clear_messages(self, session_id):
        self.replace_messages(session_id, [])

    def delete_session(self, session_id, sessions_dir=None):
        with self._guard():
            item = self._load(session_id)
            if item is None: return False
            for other in list(self._items()):
                if other.get("parent_session_id") == session_id:
                    other["parent_session_id"] = None
                    self._save(other["id"], other)
            shutil.rmtree(self._folder(session_id))
        return True

    def prune_sessions(self, older_than_days=90, source=None, sessions_dir=None):
        cutoff = time.time() - 86400 * older_than_days
        ids = [s["id"] for s in self._items() if s.get("ended_at") is not None
               and s.get("started_at", 0) < cutoff and (source is None or s["source"] == source)]
        for sid in ids: self.delete_session(sid, sessions_dir)
        return len(ids)

    def prune_empty_ghost_sessions(self, sessions_dir=None):
        cutoff = time.time() - 86400
        ids = [s["id"] for s in self._items() if s.get("source") == "tui"
               and s.get("ended_at") and not s.get("messages")
               and not s.get("title") and s["started_at"] < cutoff]
        for sid in ids: self.delete_session(sid, sessions_dir)
        return len(ids)

    def finalize_orphaned_compression_sessions(self):
        return 0

    def _read_state_meta(self):
        path = self.root / "state_meta.json"
        # Global metadata needs the same containment checks as per-session
        # metadata. A dangling symlink is unsafe too.
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError("Unsafe Vault state metadata")
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("Invalid Vault state metadata")
        return data

    def get_meta(self, key):
        return self._read_state_meta().get(key)

    def set_meta(self, key, value):
        with self._guard():
            data = self._read_state_meta()
            data[key] = value
            self._write(self.root / "state_meta.json",
                        json.dumps(data, ensure_ascii=False, indent=2))

    def vacuum(self):
        return None

    def maybe_auto_prune_and_vacuum(self, retention_days=90, min_interval_hours=24,
                                    vacuum=True, sessions_dir=None):
        last = float(self.get_meta("last_auto_prune") or 0)
        if time.time() - last < min_interval_hours * 3600:
            return {"skipped": True, "pruned": 0, "vacuumed": False}
        count = self.prune_sessions(retention_days, sessions_dir=sessions_dir)
        self.set_meta("last_auto_prune", str(time.time()))
        return {"skipped": False, "pruned": count, "vacuumed": False}
