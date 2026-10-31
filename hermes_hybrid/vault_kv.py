"""Small Vault-format key/value store, shared by the migrated subsystems.

This is the non-session counterpart of :mod:`hermes_hybrid.obsidian_store`:
a directory of one JSON file per record, written atomically
(temp file in the same directory, fsync, ``os.replace``) and serialised
across processes with a per-directory flock file.  No SQLite is used.

Layout (all under ``HERMES_OBSIDIAN_VAULT`` when it is set, otherwise
under a caller-supplied root, typically ``<hermes_home>/06_Automation/
Hermes/`` so the format is identical in both cases):

- ``RetainQueue/<id>.json``       — RetainDB write-behind queue
- ``HolographicMemory/facts/<id>.json`` — holographic facts
- ``HolographicMemory/banks/<name>.json`` — holographic memory banks
- ``ApiResponses/<response_id>.json``    — API response store
- ``ApiResponses/conversations.json``    — conversation -> response map

Record ids are validated the same way as the session store: no ``..``,
no separators, no NUL.  Existing data is never deleted by this module
except through an explicit ``delete`` call for the record being removed.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def validate_id(value: str) -> str:
    if not value or value in (".", "..") or not _ID_RE.match(value):
        raise ValueError(f"invalid identifier: {value!r}")
    return value


def resolve_vault_root(explicit: str | Path | None = None) -> Path | None:
    """Return the Vault root, or ``None`` when neither explicit nor env."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("HERMES_OBSIDIAN_VAULT", "").strip()
    return Path(env).expanduser() if env else None


class VaultDir:
    """One directory of JSON records under a Vault tree."""

    def __init__(self, root: str | Path, *subpath: str):
        # The root is supplied by the caller; every child component must stay
        # inside it.  Check *before* mkdir, which otherwise follows symlinks.
        self.root = Path(root).expanduser()
        for part in subpath:
            if not isinstance(part, str) or part in ("", ".", "..") or Path(part).name != part:
                raise ValueError(f"invalid vault subpath: {part!r}")
        self._subpath = subpath
        self.dir = self.root.joinpath(*subpath)
        self._assert_contained()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._assert_contained()
        self.lock_file = self.dir / ".vault-writer.lock"

    def _assert_contained(self) -> None:
        """Reject a redirected Vault root or any symlinked child directory."""
        if self.root.is_symlink():
            raise ValueError("symlink in vault path is rejected")
        anchor = self.root.resolve(strict=False)
        current = self.root
        for part in self._subpath:
            current = current / part
            if current.is_symlink():
                raise ValueError("symlink in vault path is rejected")
            resolved = current.resolve(strict=False)
            if resolved != anchor and anchor not in resolved.parents:
                raise ValueError("path escapes vault")

    def path(self, record_id: str) -> Path:
        self._assert_contained()
        candidate = self.dir / f"{validate_id(record_id)}.json"
        if candidate.is_symlink():
            raise ValueError("symlink in vault path is rejected")
        return candidate

    def _locked(self, fn):
        self._assert_contained()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._assert_contained()
        # Do not follow lock symlinks or block on an attacker-created FIFO.
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        fd = os.open(self.lock_file, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("vault lock is not a regular file")
            with os.fdopen(fd, "a+", encoding="utf-8") as lock:
                fd = -1  # fdopen owns the descriptor
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    # A replaced lock inode can let another process acquire a
                    # different lock and overwrite our concurrent updates.
                    opened = os.fstat(lock.fileno())
                    named = os.stat(self.lock_file, follow_symlinks=False)
                    if (
                        (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                        or not stat.S_ISREG(named.st_mode)
                    ):
                        raise ValueError("vault lock changed during acquisition")
                    self._assert_contained()
                    return fn()
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        # A predictable PID-based temp name can be pre-created as a symlink.
        # mkstemp uses O_EXCL and an unpredictable name in the same directory.
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.lexists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _read_regular_json(p: Path) -> dict[str, Any] | None:
        """Read a JSON object without following a replaced final-component link."""
        # A pathname symlink check followed by read_text() is racy. Open the
        # final component without following links; O_NONBLOCK also prevents
        # an attacker-controlled FIFO from hanging a gateway worker.
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                fd = -1  # fdopen now owns the descriptor
                try:
                    data = json.load(stream)
                except (OSError, UnicodeError, json.JSONDecodeError):
                    return None
        finally:
            if fd >= 0:
                os.close(fd)
        return data if isinstance(data, dict) else None

    def read(self, record_id: str) -> dict[str, Any] | None:
        return self._read_regular_json(self.path(record_id))

    def write(self, record_id: str, data: dict[str, Any]) -> None:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

        def _do():
            p = self.path(record_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(p, text)

        self._locked(_do)

    def update(self, record_id: str, transform) -> None:
        """Atomically read, transform, and replace a JSON record under one lock.

        Refuse to overwrite a present but unreadable record: treating a
        damaged index as empty would silently discard its existing contents.
        """

        def _do():
            path = self.path(record_id)
            current = self.read(record_id)
            if current is None:
                if path.exists():
                    raise ValueError(f"cannot update invalid vault record: {record_id!r}")
                current = {}
            updated = transform(current)
            if not isinstance(updated, dict):
                raise TypeError("vault record update must produce a dict")
            self._atomic_write(
                self.path(record_id),
                json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
            )

        self._locked(_do)

    def delete(self, record_id: str) -> bool:
        p = self.path(record_id)
        if not p.exists():
            return False
        self._locked(lambda: p.unlink(missing_ok=True))
        return True

    def list_ids(self) -> list[str]:
        self._assert_contained()
        ids = []
        for p in sorted(self.dir.glob("*.json")):
            if p.is_symlink():
                continue
            ids.append(p.stem)
        return ids

    def list_records(self) -> list[dict[str, Any]]:
        records = []
        for record_id in self.list_ids():
            data = self.read(record_id)
            if data is not None:
                records.append(data)
        return records

    def next_id(self, counter_name: str = "_counter") -> int:
        """Allocate a monotonically increasing integer id, atomically."""

        def _do():
            # The legacy default "_counter" is an internal reserved name;
            # all caller-supplied alternatives must pass the normal ID guard.
            if counter_name != "_counter":
                validate_id(counter_name)
            counter = self.dir / f"{counter_name}.json"
            if counter.is_symlink():
                raise ValueError("symlink in vault path is rejected")
            current = 0
            if os.path.lexists(counter):
                # The counter is authoritative: never reset a present but
                # unreadable file and risk reissuing a previously allocated ID.
                # Reuse the O_NOFOLLOW reader to reject open-time link swaps.
                record = self._read_regular_json(counter)
                if record is None:
                    raise ValueError(f"invalid vault counter: {counter_name!r}")
                value = record.get("next")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, str))
                    or (isinstance(value, str) and not value.isdecimal())
                ):
                    raise ValueError(f"invalid vault counter: {counter_name!r}")
                current = int(value)
                if current < 0:
                    raise ValueError(f"invalid vault counter: {counter_name!r}")
            if current == 0:
                for record_id in self.list_ids():
                    if record_id.isdigit():
                        current = max(current, int(record_id))
            nxt = current + 1
            # Store the last allocated ID, not one beyond it. Older Vaults
            # may contain the old next+1 value; treating that value as a
            # conservative floor skips an ID but cannot reissue an old one.
            self._atomic_write(counter, json.dumps({"next": nxt}) + "\n")
            return nxt

        return self._locked(_do)
