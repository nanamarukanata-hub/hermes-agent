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
        self.root = Path(root).expanduser()
        self.dir = self.root.joinpath(*subpath)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.dir / ".vault-writer.lock"

    def path(self, record_id: str) -> Path:
        candidate = self.dir / f"{validate_id(record_id)}.json"
        if candidate.is_symlink():
            raise ValueError("symlink in vault path is rejected")
        return candidate

    def _locked(self, fn):
        self.dir.mkdir(parents=True, exist_ok=True)
        # A lock symlink must never redirect the writer outside the Vault.
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        fd = os.open(self.lock_file, flags, 0o600)
        with os.fdopen(fd, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

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

    def read(self, record_id: str) -> dict[str, Any] | None:
        p = self.path(record_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def write(self, record_id: str, data: dict[str, Any]) -> None:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

        def _do():
            p = self.path(record_id)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(p, text)

        self._locked(_do)

    def delete(self, record_id: str) -> bool:
        p = self.path(record_id)
        if not p.exists():
            return False
        self._locked(lambda: p.unlink(missing_ok=True))
        return True

    def list_ids(self) -> list[str]:
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
            if counter.exists():
                try:
                    current = int(json.loads(counter.read_text(encoding="utf-8")).get("next", 0))
                except (OSError, ValueError, json.JSONDecodeError, AttributeError):
                    current = 0
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
