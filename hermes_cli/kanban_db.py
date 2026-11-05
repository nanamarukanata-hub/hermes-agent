"""Vault-backed Kanban board for multi-profile, multi-project collaboration.

This module used to persist the board in SQLite (``kanban.db``). The
runtime store is now Obsidian Vault JSON, compatible with the layout
produced by ``scripts/migrate_kanban_to_vault.py`` (read-only legacy
export tool; the runtime never opens a legacy ``.db`` file):

* ``<store>/<task_id>.json`` — one JSON file per task (the legacy
  ``tasks`` row, with ``skills`` kept as a JSON string exactly as the
  migration tool writes it).
* ``<store>/links.json`` — ``task_links`` rows.
* ``<store>/comments.json`` — ``task_comments`` rows.
* ``<store>/events.json`` — ``task_events`` rows.
* ``<store>/runs.json`` — ``task_runs`` rows.
* ``<store>/notify_subs.json`` — ``kanban_notify_subs`` rows.
* ``<store>/counters.json`` — persistent AUTOINCREMENT-equivalent
  counters for comments, events and runs, so ids stay monotonic even
  when old events are garbage-collected.

Store location resolution:

* ``HERMES_OBSIDIAN_VAULT`` set → ``<vault>/06_Automation/Hermes/Kanban/<board>/``.
* Otherwise, when ``HERMES_KANBAN_BOARD`` (or an explicit ``board=`` arg)
  pins a board → that board's ``board_dir()`` (``<root>/kanban/boards/<slug>/``).
* Otherwise, when only the legacy ``HERMES_KANBAN_DB`` file pin is set →
  the pin's parent directory with ``.store`` appended to the file stem
  (e.g. ``/tmp/pinned/board.db`` → ``/tmp/pinned/board.store``). The
  legacy pin still wins for :func:`kanban_db_path` (see below), and
  every process sharing the same pin resolves to the same store dir.
* Otherwise → the active board's ``board_dir()``.

:func:`kanban_db_path` still returns the legacy ``kanban.db`` file path
for back-compat: the dispatcher injects it into worker env
(``HERMES_KANBAN_DB``) and existing tooling / tests read it. Workers
also receive ``HERMES_KANBAN_BOARD``, and store resolution prefers the
board slug, so dispatcher and worker converge on the same store even
though the legacy path itself is never opened by the runtime.

Board resolution order (highest precedence first, all optional):

* ``board=`` argument passed directly to :func:`connect` / :func:`init_db`.
* ``HERMES_KANBAN_BOARD`` env var.
* ``HERMES_KANBAN_DB`` env var (legacy file pin; store dir derived as above).
* ``<root>/kanban/current`` — a one-line text file holding the slug of
  the "currently selected" board.
* ``default``.

In standard installs ``<root>`` is ``~/.hermes``; see :func:`kanban_home`.

Concurrency strategy: one board-wide advisory lock
(``.vault-writer.lock``, ``flock``) + atomic per-file writes
(temp file + fsync + ``os.replace``). Every public function runs inside
a board scope: when no transaction is open it acquires the lock, loads
fresh state from disk, and saves on release; nested calls (including
:func:`write_txn`) reuse the outer scope, so a claim CAS
(read-modify-write under the lock) is atomic — at most one claimer can
win any given task, losers observe the task as already claimed.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_STATUSES = {"triage", "todo", "ready", "running", "blocked", "done", "archived"}
VALID_WORKSPACE_KINDS = {"scratch", "worktree", "dir"}

DEFAULT_CLAIM_TTL_SECONDS = 15 * 60

_CTX_MAX_PRIOR_ATTEMPTS = 10
_CTX_MAX_COMMENTS = 30
_CTX_MAX_FIELD_BYTES = 4 * 1024
_CTX_MAX_BODY_BYTES = 8 * 1024
_CTX_MAX_COMMENT_BYTES = 2 * 1024

_RESERVED_STORE_NAMES = frozenset(
    {"links", "comments", "events", "runs", "notify_subs", "counters"}
)
_TASK_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
_EVENT_RENAMES = (
    ("ready", "promoted"),
    ("priority", "reprioritized"),
    ("spawn_auto_blocked", "gave_up"),
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_BOARD = "default"

_BOARD_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,63}$")


def _normalize_board_slug(slug: Optional[str]) -> Optional[str]:
    """Lowercase + strip a slug; validate; return ``None`` for empty."""
    if slug is None:
        return None
    s = str(slug).strip().lower()
    if not s:
        return None
    if not _BOARD_SLUG_RE.match(s):
        raise ValueError(
            f"invalid board slug {slug!r}: must be 1-64 chars, lowercase "
            f"alphanumerics / hyphens / underscores, not starting with '-' or '_'"
        )
    return s


def kanban_home() -> Path:
    """Return the shared Hermes root that anchors the kanban board."""
    override = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    from hermes_constants import get_default_hermes_root

    return get_default_hermes_root()


def boards_root() -> Path:
    """Return ``<root>/kanban/boards`` — the parent of non-default board dirs."""
    return kanban_home() / "kanban" / "boards"


def current_board_path() -> Path:
    """Persist the active board selector inside the configured Vault."""
    return _checked_kanban_file(kanban_store_dir(board=DEFAULT_BOARD).parent / ".current-board")


def get_current_board() -> str:
    """Return the active board slug, honouring the resolution chain."""
    env = os.environ.get("HERMES_KANBAN_BOARD", "").strip()
    if env:
        try:
            normed = _normalize_board_slug(env)
            if normed:
                return normed
        except ValueError:
            pass
    try:
        f = current_board_path()
        if f.exists():
            val = f.read_text(encoding="utf-8").strip()
            if val:
                try:
                    normed = _normalize_board_slug(val)
                    if normed and board_exists(normed):
                        return normed
                except ValueError:
                    pass
    except OSError:
        pass
    return DEFAULT_BOARD


def set_current_board(slug: str) -> Path:
    """Persist ``slug`` as the active board. Returns the file written."""
    normed = _normalize_board_slug(slug)
    if not normed:
        raise ValueError("board slug is required")
    path = current_board_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_vault_text_atomic(path, normed + "\n")
    return path


def clear_current_board() -> None:
    """Remove ``<root>/kanban/current`` so the active board reverts to ``default``."""
    try:
        current_board_path().unlink()
    except FileNotFoundError:
        pass


def board_dir(board: Optional[str] = None) -> Path:
    """Return the on-disk directory for ``board``."""
    slug = _normalize_board_slug(board) or DEFAULT_BOARD
    return boards_root() / slug


def board_exists(board: Optional[str] = None) -> bool:
    """Discover only Vault-backed boards; never consult legacy DB paths."""
    slug = _normalize_board_slug(board) or DEFAULT_BOARD
    if slug == DEFAULT_BOARD:
        return True
    d = kanban_store_dir(board=slug)
    return d.is_dir() and (
        (d / "board.json").exists() or (d / "counters.json").exists()
        or (d / "links.json").exists()
    )


def kanban_db_path(board: Optional[str] = None) -> Path:
    """Return the legacy ``kanban.db`` file path for ``board``.

    Kept for back-compat (dispatcher→worker env pin, board metadata
    ``db_path`` field, existing path tests). The runtime never opens
    this file; the JSON store lives at :func:`kanban_store_dir`.
    """
    override = os.environ.get("HERMES_KANBAN_DB", "").strip()
    if override:
        return Path(override).expanduser()
    slug = _normalize_board_slug(board)
    if slug is None:
        slug = get_current_board()
    if slug == DEFAULT_BOARD:
        return kanban_home() / "kanban.db"
    return board_dir(slug) / "kanban.db"


def _pin_store_dir(pin: Path) -> Path:
    """Derive a store dir from a legacy ``.db`` file pin.

    ``/tmp/pinned/board.db`` → ``/tmp/pinned/board.store``; a pin that
    is already a directory is used as-is.
    """
    if pin.suffix == ".db":
        return pin.parent / (pin.stem + ".store")
    return pin


def kanban_store_dir(
    board: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Path:
    """Resolve a Kanban store; never let legacy DB pins bypass a configured Vault.

    A configured Vault is authoritative, even when callers still pass a
    legacy ``db_path`` or set ``HERMES_KANBAN_DB``. Explicit legacy
    paths are rejected rather than silently creating a second store.
    Until the remaining legacy callers/tests are migrated, the historical
    non-Vault resolution is retained below and must not be used in the
    Discord RP production runtime.
    """
    vault = os.environ.get("HERMES_OBSIDIAN_VAULT", "").strip()
    if vault:
        if db_path is not None:
            raise ValueError(
                "explicit Kanban db_path is forbidden with HERMES_OBSIDIAN_VAULT"
            )
        root = Path(vault).expanduser()
        if not root.is_dir():
            raise ValueError("HERMES_OBSIDIAN_VAULT must be an existing directory")
        slug = _normalize_board_slug(board) or get_current_board()
        target = root / "06_Automation" / "Hermes" / "Kanban" / slug
        # resolve() follows any existing intermediate symlinks, including
        # ones that would redirect the Kanban subtree outside the Vault.
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("Kanban store path escapes HERMES_OBSIDIAN_VAULT")
        return target
    raise ValueError("HERMES_OBSIDIAN_VAULT is required for Kanban persistence")


# Back-compat alias: the store directory is the board's data directory.
kanban_store_path = kanban_store_dir

# Legacy name kept for test fixtures that cleared the old per-path init
# cache. The Vault store needs no such cache (connect() auto-initializes
# on every open); this set is intentionally never populated.
_INITIALIZED_PATHS: set[str] = set()


def workspaces_root(board: Optional[str] = None) -> Path:
    """Keep scratch workspaces inside the configured Obsidian Vault.

    An explicit workspace override is permitted only if its resolved path
    stays inside the Vault; legacy Hermes-home fallbacks are forbidden.
    """
    override = os.environ.get("HERMES_KANBAN_WORKSPACES_ROOT", "").strip()
    if override:
        return _checked_kanban_file(Path(override).expanduser())
    slug = _normalize_board_slug(board) or get_current_board()
    return _checked_kanban_file(kanban_store_dir(board=slug) / "workspaces")


def worker_logs_dir(board: Optional[str] = None) -> Path:
    """Keep persistent per-task worker logs in the Obsidian Vault."""
    slug = _normalize_board_slug(board) or get_current_board()
    return _checked_kanban_file(kanban_store_dir(board=slug) / "logs")


def _checked_kanban_file(path: Path) -> Path:
    """Reject symlinks that redirect a Kanban metadata file outside Vault."""
    vault = os.environ.get("HERMES_OBSIDIAN_VAULT", "").strip()
    if not vault:
        raise ValueError("HERMES_OBSIDIAN_VAULT is required for Kanban persistence")
    if not path.resolve().is_relative_to(Path(vault).expanduser().resolve()):
        raise ValueError("Kanban metadata path escapes HERMES_OBSIDIAN_VAULT")
    return path


def _write_vault_text_atomic(path: Path, text: str) -> None:
    """Atomically replace Vault metadata without following a swapped file link.

    A plain Path.write_text() follows a final-component symlink introduced
    after the containment check, potentially overwriting a file outside Vault.
    A random exclusive temporary file and os.replace() replace the directory
    entry itself rather than opening the existing destination for writing.
    """
    import tempfile

    _checked_kanban_file(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        _checked_kanban_file(path)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def board_metadata_path(board: Optional[str] = None) -> Path:
    """Keep board metadata in the same Vault directory as its task store."""
    slug = _normalize_board_slug(board) or DEFAULT_BOARD
    return _checked_kanban_file(kanban_store_dir(board=slug) / "board.json")


def _default_board_display_name(slug: str) -> str:
    """Turn a slug into a reasonable default display name."""
    return " ".join(part.capitalize() for part in slug.replace("_", "-").split("-") if part) or slug


def read_board_metadata(board: Optional[str] = None) -> dict:
    """Return ``board.json`` contents (or synthesized defaults). Never raises."""
    slug = _normalize_board_slug(board) or DEFAULT_BOARD
    meta: dict[str, Any] = {
        "slug": slug,
        "name": _default_board_display_name(slug),
        "description": "",
        "icon": "",
        "color": "",
        "created_at": None,
        "archived": False,
    }
    try:
        p = board_metadata_path(slug)
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw["slug"] = slug
                meta.update(raw)
    except (OSError, json.JSONDecodeError):
        pass
    meta["db_path"] = str(kanban_db_path(slug))
    return meta


def write_board_metadata(
    board: Optional[str],
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
    archived: Optional[bool] = None,
) -> dict:
    """Create / update ``board.json`` for ``board``. Returns resulting metadata."""
    slug = _normalize_board_slug(board) or DEFAULT_BOARD
    meta = read_board_metadata(slug)
    meta.pop("db_path", None)
    if name is not None:
        meta["name"] = str(name).strip() or _default_board_display_name(slug)
    if description is not None:
        meta["description"] = str(description)
    if icon is not None:
        meta["icon"] = str(icon)
    if color is not None:
        meta["color"] = str(color)
    if archived is not None:
        meta["archived"] = bool(archived)
    if not meta.get("created_at"):
        meta["created_at"] = int(time.time())
    path = board_metadata_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_vault_text_atomic(
        path, json.dumps(meta, indent=2, ensure_ascii=False) + "\n"
    )
    meta["db_path"] = str(kanban_db_path(slug))
    return meta


def create_board(
    slug: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    color: Optional[str] = None,
) -> dict:
    """Create a new board directory + store + metadata. Idempotent."""
    normed = _normalize_board_slug(slug)
    if not normed:
        raise ValueError("board slug is required")
    meta = write_board_metadata(
        normed,
        name=name,
        description=description,
        icon=icon,
        color=color,
    )
    init_db(board=normed)
    return meta


def list_boards(*, include_archived: bool = True) -> list[dict]:
    """Enumerate all boards that exist on disk. ``default`` first, rest alphabetical."""
    entries: list[dict] = []
    seen: set[str] = set()
    entries.append(read_board_metadata(DEFAULT_BOARD))
    seen.add(DEFAULT_BOARD)
    root = kanban_store_dir(board=DEFAULT_BOARD).parent
    if root.is_dir():
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            slug = child.name
            try:
                normed = _normalize_board_slug(slug)
            except ValueError:
                continue
            if not normed or normed in seen:
                continue
            has_meta = (child / "board.json").exists()
            has_store = (child / "links.json").exists() or (child / "counters.json").exists()
            if not (has_meta or has_store):
                continue
            meta = read_board_metadata(normed)
            if meta.get("archived") and not include_archived:
                continue
            entries.append(meta)
            seen.add(normed)
    return entries


def remove_board(slug: str, *, archive: bool = True) -> dict:
    """Remove or archive a board. ``default`` cannot be removed."""
    normed = _normalize_board_slug(slug)
    if not normed:
        raise ValueError("board slug is required")
    if normed == DEFAULT_BOARD:
        raise ValueError("the 'default' board cannot be removed")
    d = kanban_store_dir(board=normed)
    if not d.exists():
        raise ValueError(f"board {normed!r} does not exist")
    if get_current_board() == normed:
        clear_current_board()
    if archive:
        archive_root = d.parent / "_archived"
        archive_root.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        target = archive_root / f"{normed}-{ts}"
        suffix = 1
        while target.exists():
            target = archive_root / f"{normed}-{ts}-{suffix}"
            suffix += 1
        d.rename(target)
        return {"slug": normed, "action": "archived", "new_path": str(target)}
    else:
        import shutil

        shutil.rmtree(d)
        return {"slug": normed, "action": "deleted", "new_path": ""}


# ---------------------------------------------------------------------------
# Vault JSON store (Board handle)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` atomically: temp file in the same dir + fsync + replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n"


def _compact(obj: Any) -> str:
    """Compact deterministic serialization for change detection only
    (much cheaper than the indented on-disk format)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _empty_state() -> dict:
    return {
        "tasks": {},
        "links": [],
        "comments": [],
        "events": [],
        "runs": {},
        "notify_subs": [],
        "counters": {"comment": 0, "event": 0, "run": 0},
    }


def _load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    if path.is_symlink():
        raise ValueError(f"refusing to follow symlink store file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class BoardStore:
    """A board handle: the Vault JSON replacement for a DB connection.

    Usable as a context manager (``with connect() as board:``) and via
    :func:`write_txn`. ``close()`` is a no-op kept for callers that
    managed the old connection's lifetime explicitly. The handle holds
    no lock outside a scope; each public function (or explicit
    :func:`write_txn` block) acquires the board lock, loads fresh state,
    and saves on release, so separate handles/processes always observe
    each other's committed writes.
    """

    def __init__(self, board: str, store_dir: Path):
        self.board = board
        self.store_dir = store_dir
        self.dir = store_dir
        self._lock = threading.RLock()
        self._lock_depth = 0
        self._lock_fd: Optional[int] = None
        self._state: Optional[dict] = None
        self._dirty = False
        self._task_snapshot: dict = {}
        self._agg_snapshot: dict = {}
        self._closed = False

    def __enter__(self) -> "BoardStore":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False

    def close(self) -> None:
        """Release the handle. No-op: locks are per-scope, not per-handle."""
        self._closed = True

    # -- locking / state -------------------------------------------------
    def _acquire(self) -> None:
        with self._lock:
            if self._lock_depth > 0:
                self._lock_depth += 1
                return
            self.store_dir.mkdir(parents=True, exist_ok=True)
            if self.store_dir.is_symlink():
                raise ValueError(f"refusing to follow symlink store dir: {self.store_dir}")
            lock_path = self.store_dir / ".vault-writer.lock"
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except Exception:
                os.close(fd)
                raise
            self._lock_fd = fd
            self._lock_depth = 1
            self._state = self._load_locked()
            self._dirty = False
            # Serialized snapshot for diff-based saves: only task files
            # whose content actually changed are rewritten, so a scope
            # touching one task on a 500-task board does not rewrite
            # all 500 files (that was O(n^2) fsyncs per batch).
            self._task_snapshot = {
                tid: _compact(row)
                for tid, row in self._state["tasks"].items()
            }
            self._agg_snapshot = {
                name: _compact(self._state[name])
                for name in ("links", "comments", "events", "notify_subs", "counters")
            }
            self._agg_snapshot["runs"] = _compact(
                [self._state["runs"][k] for k in sorted(self._state["runs"], key=lambda x: int(x) if str(x).isdigit() else 0)]
            )

    def _release(self) -> None:
        with self._lock:
            if self._lock_depth == 0:
                return
            self._lock_depth -= 1
            if self._lock_depth > 0:
                return
            try:
                if self._dirty and self._state is not None:
                    self._save_locked(self._state)
            finally:
                self._state = None
                self._dirty = False
                self._task_snapshot = {}
                if self._lock_fd is not None:
                    try:
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                    finally:
                        os.close(self._lock_fd)
                        self._lock_fd = None

    def _abort(self) -> None:
        """Discard uncommitted scope state (transaction rollback)."""
        with self._lock:
            if self._lock_depth == 0:
                return
            self._lock_depth -= 1
            if self._lock_depth > 0:
                return
            self._state = None
            self._dirty = False
            self._task_snapshot = {}
            self._agg_snapshot = {}
            if self._lock_fd is not None:
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(self._lock_fd)
                    self._lock_fd = None

    def _load_locked(self) -> dict:
        state = _empty_state()
        store = self.store_dir
        # Tasks: one file per task id.
        if store.is_dir():
            for child in sorted(store.iterdir()):
                if not child.is_file() or child.suffix != ".json":
                    continue
                stem = child.stem
                if stem in _RESERVED_STORE_NAMES or child.name.startswith("."):
                    continue
                if not _TASK_FILE_RE.match(stem):
                    continue
                if child.is_symlink():
                    raise ValueError(f"refusing to follow symlink task file: {child}")
                row = json.loads(child.read_text(encoding="utf-8"))
                if isinstance(row, dict) and row.get("id"):
                    state["tasks"][str(row["id"])] = row
        state["links"] = _load_json_file(store / "links.json", [])
        state["comments"] = _load_json_file(store / "comments.json", [])
        state["events"] = _load_json_file(store / "events.json", [])
        runs_raw = _load_json_file(store / "runs.json", [])
        if isinstance(runs_raw, list):
            state["runs"] = {str(r["id"]): r for r in runs_raw if isinstance(r, dict) and "id" in r}
        elif isinstance(runs_raw, dict):
            state["runs"] = {str(k): v for k, v in runs_raw.items()}
        state["notify_subs"] = _load_json_file(store / "notify_subs.json", [])
        counters = _load_json_file(store / "counters.json", {})
        if isinstance(counters, dict):
            for key in ("comment", "event", "run"):
                if isinstance(counters.get(key), int):
                    state["counters"][key] = int(counters[key])
        # Monotonic floor: counters must never fall below existing ids
        # (covers boards exported by migrate_kanban_to_vault.py, which
        # writes no counters file).
        for c in state["comments"]:
            state["counters"]["comment"] = max(state["counters"]["comment"], int(c.get("id", 0) or 0))
        for e in state["events"]:
            state["counters"]["event"] = max(state["counters"]["event"], int(e.get("id", 0) or 0))
        for rid in state["runs"]:
            try:
                state["counters"]["run"] = max(state["counters"]["run"], int(rid))
            except (TypeError, ValueError):
                pass
        return state

    def _save_locked(self, state: dict) -> None:
        store = self.store_dir
        store.mkdir(parents=True, exist_ok=True)
        # Task files first, then aggregates, then counters. Only task
        # files whose serialized content differs from the load snapshot
        # (or are new) are rewritten; unchanged files keep their bytes.
        snapshot = getattr(self, "_task_snapshot", None) or {}
        existing = {
            p.stem
            for p in store.iterdir()
            if p.is_file() and p.suffix == ".json" and p.stem not in _RESERVED_STORE_NAMES
            and not p.name.startswith(".")
        }
        for task_id, row in state["tasks"].items():
            task_path = store / f"{task_id}.json"
            if snapshot.get(task_id) != _compact(row) or not task_path.exists():
                _atomic_write(task_path, _json_dumps(row))
        for stale in existing - set(state["tasks"].keys()):
            # Only delete files that are actually task records (their
            # content id matches the filename). Other JSON files in
            # the board directory (notably board.json) must survive.
            try:
                stale_path = store / f"{stale}.json"
                row = json.loads(stale_path.read_text(encoding="utf-8"))
                if isinstance(row, dict) and str(row.get("id")) == stale:
                    stale_path.unlink()
            except (OSError, json.JSONDecodeError):
                pass
        agg_snapshot = getattr(self, "_agg_snapshot", None) or {}
        runs_list = [state["runs"][k] for k in sorted(state["runs"], key=lambda x: int(x) if str(x).isdigit() else 0)]
        for name, filename, value in (
            ("links", "links.json", state["links"]),
            ("comments", "comments.json", state["comments"]),
            ("events", "events.json", state["events"]),
            ("runs", "runs.json", runs_list),
            ("notify_subs", "notify_subs.json", state["notify_subs"]),
            ("counters", "counters.json", state["counters"]),
        ):
            if agg_snapshot.get(name) != _compact(value) or not (store / filename).exists():
                _atomic_write(store / filename, _json_dumps(value))

    def mark_dirty(self) -> None:
        self._dirty = True


# Aliases for callers / tests that name the handle descriptively.
VaultBoard = BoardStore
BoardHandle = BoardStore


def _as_board(conn: Any) -> BoardStore:
    if isinstance(conn, BoardStore):
        return conn
    raise TypeError(
        "expected a kanban BoardStore handle from connect(); "
        "SQLite connections are no longer supported"
    )


@contextlib.contextmanager
def _board_scope(conn: Any):
    """Acquire the board scope (lock + fresh state) unless already inside one."""
    board = _as_board(conn)
    if board._lock_depth > 0:
        yield board
        return
    board._acquire()
    try:
        yield board
    except Exception:
        board._abort()
        raise
    else:
        board._release()


@contextlib.contextmanager
def write_txn(conn: Any):
    """Context manager for an atomic board write transaction.

    Equivalent to the old ``BEGIN IMMEDIATE`` block: nested uses reuse
    the outer scope, and an exception discards the whole scope's
    writes (rollback). Yields the board handle.
    """
    with _board_scope(conn) as board:
        yield board


def connect(
    db_path: Optional[Path] = None,
    *,
    board: Optional[str] = None,
) -> BoardStore:
    """Open (and initialize if needed) the kanban board store.

    ``db_path`` explicit → pin-derived store dir (legacy callers, tests).
    ``board`` explicit → that board's store. Neither → resolution via
    Vault env → ``HERMES_KANBAN_BOARD`` → legacy pin → current board →
    ``default``. The first open creates the store directory and the
    aggregate JSON files; subsequent opens are cheap.
    """
    if db_path is not None:
        store_dir = kanban_store_dir(db_path=db_path)
        slug = _normalize_board_slug(board) or get_current_board()
    else:
        slug = _normalize_board_slug(board)
        if slug is None:
            # Preserve the board-env / pin / current resolution chain.
            slug = get_current_board()
            # When only the legacy pin is set (no board slug source),
            # kanban_store_dir() below derives from the pin; the slug
            # recorded on the handle is only descriptive in that case.
            if os.environ.get("HERMES_KANBAN_BOARD", "").strip():
                pass
        store_dir = kanban_store_dir(board=board)
    handle = BoardStore(slug, store_dir)
    # Auto-init: create the store files on first use (idempotent).
    with _board_scope(handle):
        handle.mark_dirty()
    return handle


def init_db(
    db_path: Optional[Path] = None,
    *,
    board: Optional[str] = None,
) -> Path:
    """Create the store if it doesn't exist; return the store directory.

    Always re-runs the store migration pass (event-kind renames +
    running-task run backfill), mirroring the old re-migration behaviour.
    """
    handle = connect(db_path, board=board)
    _migrate_store(handle)
    return handle.store_dir


def _migrate_store(board: BoardStore) -> None:
    """Vault equivalent of the old additive-migration pass.

    Renames legacy event kinds in place and synthesizes a ``running``
    run row for any ``running`` task whose ``current_run_id`` is NULL
    (the pre-runs backfill). Idempotent: after the first pass no rows
    match, so re-running is a no-op.
    """
    with _board_scope(board):
        st = board._state
        assert st is not None
        changed = False
        for ev in st["events"]:
            for old, new in _EVENT_RENAMES:
                if ev.get("kind") == old:
                    ev["kind"] = new
                    changed = True
        for task_id, row in list(st["tasks"].items()):
            if row.get("status") == "running" and not row.get("current_run_id"):
                started = row.get("started_at") or int(time.time())
                st["counters"]["run"] += 1
                run_id = st["counters"]["run"]
                st["runs"][str(run_id)] = {
                    "id": run_id,
                    "task_id": task_id,
                    "profile": row.get("assignee"),
                    "step_key": None,
                    "status": "running",
                    "claim_lock": row.get("claim_lock"),
                    "claim_expires": row.get("claim_expires"),
                    "worker_pid": row.get("worker_pid"),
                    "max_runtime_seconds": row.get("max_runtime_seconds"),
                    "last_heartbeat_at": row.get("last_heartbeat_at"),
                    "started_at": started,
                    "ended_at": None,
                    "outcome": None,
                    "summary": None,
                    "metadata": None,
                    "error": None,
                }
                row["current_run_id"] = run_id
                changed = True
        if changed:
            board.mark_dirty()


def _migrate_add_optional_columns(conn: Any) -> None:
    """Back-compat name for the store migration pass.

    The old function added optional columns to a SQLite schema; the
    Vault store has no columns, so this now runs :func:`_migrate_store`
    (event renames + running-task run backfill) and is a no-op on an
    already-migrated store.
    """
    _migrate_store(_as_board(conn))


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _new_task_id() -> str:
    """Generate a short, URL-safe task id (4 hex bytes)."""
    return "t_" + secrets.token_hex(4)


def _claimer_id() -> str:
    """Return a ``host:pid`` string that identifies this claimer."""
    import socket

    try:
        host = socket.gethostname() or "unknown"
    except Exception:
        host = "unknown"
    return f"{host}:{os.getpid()}"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict, a Row-like object, or an attribute object."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        keys = row.keys()  # type: ignore[attr-defined]
        if key in keys:
            return row[key]
    except Exception:
        pass
    try:
        return row[key]
    except Exception:
        pass
    return getattr(row, key, default)


def _parse_skills(value: Any) -> Optional[list]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(s) for s in value if s]
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(s) for s in parsed if s]
        except Exception:
            return None
    return None


@dataclass
class Task:
    """In-memory view of a task record."""

    id: str
    title: str
    body: Optional[str]
    assignee: Optional[str]
    status: str
    priority: int
    created_by: Optional[str]
    created_at: int
    started_at: Optional[int]
    completed_at: Optional[int]
    workspace_kind: str
    workspace_path: Optional[str]
    claim_lock: Optional[str]
    claim_expires: Optional[int]
    tenant: Optional[str]
    result: Optional[str] = None
    idempotency_key: Optional[str] = None
    consecutive_failures: int = 0
    worker_pid: Optional[int] = None
    last_failure_error: Optional[str] = None
    max_runtime_seconds: Optional[int] = None
    last_heartbeat_at: Optional[int] = None
    current_run_id: Optional[int] = None
    workflow_template_id: Optional[str] = None
    current_step_key: Optional[str] = None
    skills: Optional[list] = None
    max_retries: Optional[int] = None

    @classmethod
    def from_dict(cls, row: Any) -> "Task":
        """Build a Task from a store dict (or Row-like mapping)."""
        return cls(
            id=_row_get(row, "id"),
            title=_row_get(row, "title"),
            body=_row_get(row, "body"),
            assignee=_row_get(row, "assignee"),
            status=_row_get(row, "status"),
            priority=_row_get(row, "priority", 0) or 0,
            created_by=_row_get(row, "created_by"),
            created_at=_row_get(row, "created_at"),
            started_at=_row_get(row, "started_at"),
            completed_at=_row_get(row, "completed_at"),
            workspace_kind=_row_get(row, "workspace_kind") or "scratch",
            workspace_path=_row_get(row, "workspace_path"),
            claim_lock=_row_get(row, "claim_lock"),
            claim_expires=_row_get(row, "claim_expires"),
            tenant=_row_get(row, "tenant"),
            result=_row_get(row, "result"),
            idempotency_key=_row_get(row, "idempotency_key"),
            consecutive_failures=(
                _row_get(row, "consecutive_failures")
                if _row_get(row, "consecutive_failures") is not None
                else (_row_get(row, "spawn_failures") or 0)
            ),
            worker_pid=_row_get(row, "worker_pid"),
            last_failure_error=(
                _row_get(row, "last_failure_error")
                if _row_get(row, "last_failure_error") is not None
                else _row_get(row, "last_spawn_error")
            ),
            max_runtime_seconds=_row_get(row, "max_runtime_seconds"),
            last_heartbeat_at=_row_get(row, "last_heartbeat_at"),
            current_run_id=_row_get(row, "current_run_id"),
            workflow_template_id=_row_get(row, "workflow_template_id"),
            current_step_key=_row_get(row, "current_step_key"),
            skills=_parse_skills(_row_get(row, "skills")),
            max_retries=_row_get(row, "max_retries"),
        )

    @classmethod
    def from_row(cls, row: Any) -> "Task":
        """Back-compat alias for :meth:`from_dict` (no SQLite dependency)."""
        return cls.from_dict(row)

    def to_dict(self) -> dict:
        """Serialize to the on-disk task record (skills as JSON string)."""
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "assignee": self.assignee,
            "status": self.status,
            "priority": self.priority,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "workspace_kind": self.workspace_kind,
            "workspace_path": self.workspace_path,
            "claim_lock": self.claim_lock,
            "claim_expires": self.claim_expires,
            "tenant": self.tenant,
            "result": self.result,
            "idempotency_key": self.idempotency_key,
            "consecutive_failures": self.consecutive_failures,
            "worker_pid": self.worker_pid,
            "last_failure_error": self.last_failure_error,
            "max_runtime_seconds": self.max_runtime_seconds,
            "last_heartbeat_at": self.last_heartbeat_at,
            "current_run_id": self.current_run_id,
            "workflow_template_id": self.workflow_template_id,
            "current_step_key": self.current_step_key,
            "skills": json.dumps(self.skills) if self.skills is not None else None,
            "max_retries": self.max_retries,
        }


@dataclass
class Run:
    """In-memory view of a ``task_runs`` record."""

    id: int
    task_id: str
    profile: Optional[str]
    step_key: Optional[str]
    status: str
    claim_lock: Optional[str]
    claim_expires: Optional[int]
    worker_pid: Optional[int]
    max_runtime_seconds: Optional[int]
    last_heartbeat_at: Optional[int]
    started_at: int
    ended_at: Optional[int]
    outcome: Optional[str]
    summary: Optional[str]
    metadata: Optional[dict]
    error: Optional[str]

    @classmethod
    def from_dict(cls, row: Any) -> "Run":
        meta_raw = _row_get(row, "metadata")
        if isinstance(meta_raw, str) and meta_raw:
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = None
        else:
            meta = meta_raw if isinstance(meta_raw, dict) else None
        ended = _row_get(row, "ended_at")
        return cls(
            id=int(_row_get(row, "id")),
            task_id=_row_get(row, "task_id"),
            profile=_row_get(row, "profile"),
            step_key=_row_get(row, "step_key"),
            status=_row_get(row, "status"),
            claim_lock=_row_get(row, "claim_lock"),
            claim_expires=_row_get(row, "claim_expires"),
            worker_pid=_row_get(row, "worker_pid"),
            max_runtime_seconds=_row_get(row, "max_runtime_seconds"),
            last_heartbeat_at=_row_get(row, "last_heartbeat_at"),
            started_at=int(_row_get(row, "started_at") or 0),
            ended_at=(int(ended) if ended is not None else None),
            outcome=_row_get(row, "outcome"),
            summary=_row_get(row, "summary"),
            metadata=meta,
            error=_row_get(row, "error"),
        )

    @classmethod
    def from_row(cls, row: Any) -> "Run":
        """Back-compat alias for :meth:`from_dict` (no SQLite dependency)."""
        return cls.from_dict(row)

    def to_dict(self) -> dict:
        """Serialize to the on-disk run record (metadata as JSON string)."""
        return {
            "id": self.id,
            "task_id": self.task_id,
            "profile": self.profile,
            "step_key": self.step_key,
            "status": self.status,
            "claim_lock": self.claim_lock,
            "claim_expires": self.claim_expires,
            "worker_pid": self.worker_pid,
            "max_runtime_seconds": self.max_runtime_seconds,
            "last_heartbeat_at": self.last_heartbeat_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "summary": self.summary,
            "metadata": json.dumps(self.metadata, ensure_ascii=False) if self.metadata else None,
            "error": self.error,
        }


@dataclass
class Comment:
    id: int
    task_id: str
    author: str
    body: str
    created_at: int


@dataclass
class Event:
    id: int
    task_id: str
    kind: str
    payload: Optional[dict]
    created_at: int
    run_id: Optional[int] = None


# -- internal state helpers -------------------------------------------------


def _state(board: BoardStore) -> dict:
    assert board._state is not None, "kanban function called outside a board scope"
    return board._state


def _task_row(st: dict, task_id: str) -> Optional[dict]:
    return st["tasks"].get(task_id)


def _event_from_row(r: dict) -> Event:
    payload_raw = r.get("payload")
    if isinstance(payload_raw, str) and payload_raw:
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = None
    else:
        payload = payload_raw if isinstance(payload_raw, dict) else None
    run_id = r.get("run_id")
    return Event(
        id=int(r["id"]),
        task_id=r["task_id"],
        kind=r["kind"],
        payload=payload,
        created_at=r["created_at"],
        run_id=(int(run_id) if run_id is not None else None),
    )


def _next_id(st: dict, kind: str) -> int:
    st["counters"][kind] += 1
    return st["counters"][kind]


def _canonical_assignee(assignee: Optional[str]) -> Optional[str]:
    """Lowercase-assignee normalization for Kanban rows (dashboard/CLI parity)."""
    if assignee is None:
        return None
    from hermes_cli.profiles import normalize_profile_name

    return normalize_profile_name(assignee)


# ---------------------------------------------------------------------------
# Task creation / mutation
# ---------------------------------------------------------------------------

def create_task(
    conn: Any,
    *,
    title: str,
    body: Optional[str] = None,
    assignee: Optional[str] = None,
    created_by: Optional[str] = None,
    workspace_kind: str = "scratch",
    workspace_path: Optional[str] = None,
    tenant: Optional[str] = None,
    priority: int = 0,
    parents: Iterable[str] = (),
    triage: bool = False,
    idempotency_key: Optional[str] = None,
    max_runtime_seconds: Optional[int] = None,
    skills: Optional[Iterable[str]] = None,
    max_retries: Optional[int] = None,
) -> str:
    """Create a new task and optionally link it under parent tasks.

    Returns the new task id. Status is ``ready`` when there are no
    parents (or all parents already ``done``), otherwise ``todo``.
    ``triage=True`` forces status ``triage``. A non-archived task with
    the same ``idempotency_key`` is returned instead of duplicated.
    """
    assignee = _canonical_assignee(assignee)
    if not title or not title.strip():
        raise ValueError("title is required")
    if workspace_kind not in VALID_WORKSPACE_KINDS:
        raise ValueError(
            f"workspace_kind must be one of {sorted(VALID_WORKSPACE_KINDS)}, "
            f"got {workspace_kind!r}"
        )
    parents = tuple(p for p in parents if p)
    skills_list: Optional[list[str]] = None
    if skills is not None:
        cleaned: list[str] = []
        seen: set[str] = set()
        for s in skills:
            if not s:
                continue
            name = str(s).strip()
            if not name:
                continue
            if "," in name:
                raise ValueError(
                    f"skill name cannot contain comma: {name!r} "
                    f"(pass a list of separate names instead of a comma-joined string)"
                )
            if name in seen:
                continue
            seen.add(name)
            cleaned.append(name)
        skills_list = cleaned

    board = _as_board(conn)
    # Idempotency check before the write scope (race acceptable, as before).
    if idempotency_key:
        with _board_scope(board):
            st = _state(board)
            candidates = [
                r for r in st["tasks"].values()
                if r.get("idempotency_key") == idempotency_key and r.get("status") != "archived"
            ]
            if candidates:
                candidates.sort(key=lambda r: r.get("created_at", 0), reverse=True)
                return candidates[0]["id"]

    now = int(time.time())
    for attempt in range(2):
        task_id = _new_task_id()
        with write_txn(board):
            st = _state(board)
            if task_id in st["tasks"]:
                if attempt == 1:
                    raise RuntimeError("task id collision")
                continue
            if triage:
                initial_status = "triage"
            else:
                initial_status = "ready"
                if parents:
                    missing = _find_missing_parents(board, parents)
                    if missing:
                        raise ValueError(f"unknown parent task(s): {', '.join(missing)}")
                    if any(
                        st["tasks"][p].get("status") != "done" for p in parents
                    ):
                        initial_status = "todo"
            if triage and parents:
                missing = _find_missing_parents(board, parents)
                if missing:
                    raise ValueError(f"unknown parent task(s): {', '.join(missing)}")
            st["tasks"][task_id] = {
                "id": task_id,
                "title": title.strip(),
                "body": body,
                "assignee": assignee,
                "status": initial_status,
                "priority": priority,
                "created_by": created_by,
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "workspace_kind": workspace_kind,
                "workspace_path": workspace_path,
                "claim_lock": None,
                "claim_expires": None,
                "tenant": tenant,
                "result": None,
                "idempotency_key": idempotency_key,
                "consecutive_failures": 0,
                "worker_pid": None,
                "last_failure_error": None,
                "max_runtime_seconds": int(max_runtime_seconds) if max_runtime_seconds else None,
                "last_heartbeat_at": None,
                "current_run_id": None,
                "workflow_template_id": None,
                "current_step_key": None,
                "skills": json.dumps(skills_list) if skills_list is not None else None,
                "max_retries": int(max_retries) if max_retries is not None else None,
            }
            for pid in parents:
                if not any(
                    l.get("parent_id") == pid and l.get("child_id") == task_id
                    for l in st["links"]
                ):
                    st["links"].append({"parent_id": pid, "child_id": task_id})
            _append_event(
                board,
                task_id,
                "created",
                {
                    "assignee": assignee,
                    "status": initial_status,
                    "parents": list(parents),
                    "tenant": tenant,
                    "skills": list(skills_list) if skills_list else None,
                },
            )
            board.mark_dirty()
            return task_id
    raise RuntimeError("unreachable")


def _find_missing_parents(conn: Any, parents: Iterable[str]) -> list[str]:
    parents = list(parents)
    if not parents:
        return []
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        return [p for p in parents if p not in st["tasks"]]


def get_task(conn: Any, task_id: str) -> Optional[Task]:
    board = _as_board(conn)
    with _board_scope(board):
        row = _task_row(_state(board), task_id)
        return Task.from_dict(row) if row else None


def list_tasks(
    conn: Any,
    *,
    assignee: Optional[str] = None,
    status: Optional[str] = None,
    tenant: Optional[str] = None,
    include_archived: bool = False,
    limit: Optional[int] = None,
) -> list[Task]:
    board = _as_board(conn)
    with _board_scope(board):
        rows = list(_state(board)["tasks"].values())
        if assignee is not None:
            canon = _canonical_assignee(assignee)
            rows = [r for r in rows if r.get("assignee") == canon]
        if status is not None:
            if status not in VALID_STATUSES:
                raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
            rows = [r for r in rows if r.get("status") == status]
        if tenant is not None:
            rows = [r for r in rows if r.get("tenant") == tenant]
        if not include_archived and status != "archived":
            rows = [r for r in rows if r.get("status") != "archived"]
        rows.sort(key=lambda r: (-(r.get("priority") or 0), r.get("created_at", 0)))
        if limit:
            rows = rows[: int(limit)]
        return [Task.from_dict(r) for r in rows]


def assign_task(conn: Any, task_id: str, profile: Optional[str]) -> bool:
    """Assign or reassign a task. Returns True on success.

    Refuses to reassign a task that's currently running (claim_lock set).
    """
    profile = _canonical_assignee(profile)
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if not row:
            return False
        if row.get("claim_lock") is not None and row.get("status") == "running":
            raise RuntimeError(
                f"cannot reassign {task_id}: currently running (claimed). "
                "Wait for completion or reclaim the stale lock first."
            )
        if row.get("assignee") != profile:
            row["assignee"] = profile
            row["consecutive_failures"] = 0
            row["last_failure_error"] = None
        else:
            row["assignee"] = profile
        _append_event(board, task_id, "assigned", {"assignee": profile})
        board.mark_dirty()
        return True


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

def link_tasks(conn: Any, parent_id: str, child_id: str) -> None:
    if parent_id == child_id:
        raise ValueError("a task cannot depend on itself")
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        missing = _find_missing_parents(board, [parent_id, child_id])
        if missing:
            raise ValueError(f"unknown task(s): {', '.join(missing)}")
        if _would_cycle(board, parent_id, child_id):
            raise ValueError(
                f"linking {parent_id} -> {child_id} would create a cycle"
            )
        if not any(
            l.get("parent_id") == parent_id and l.get("child_id") == child_id
            for l in st["links"]
        ):
            st["links"].append({"parent_id": parent_id, "child_id": child_id})
        parent_status = st["tasks"][parent_id].get("status")
        if parent_status != "done":
            child = st["tasks"][child_id]
            if child.get("status") == "ready":
                child["status"] = "todo"
        _append_event(
            board, child_id, "linked",
            {"parent": parent_id, "child": child_id},
        )
        board.mark_dirty()


def _would_cycle(conn: Any, parent_id: str, child_id: str) -> bool:
    """Return True if adding parent->child creates a cycle."""
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        seen = set()
        stack = [child_id]
        while stack:
            node = stack.pop()
            if node == parent_id:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(
                l["child_id"] for l in st["links"] if l.get("parent_id") == node
            )
        return False


def unlink_tasks(conn: Any, parent_id: str, child_id: str) -> bool:
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        before = len(st["links"])
        st["links"] = [
            l for l in st["links"]
            if not (l.get("parent_id") == parent_id and l.get("child_id") == child_id)
        ]
        removed = before - len(st["links"])
        if removed:
            _append_event(
                board, child_id, "unlinked",
                {"parent": parent_id, "child": child_id},
            )
            board.mark_dirty()
        return removed > 0


def parent_ids(conn: Any, task_id: str) -> list[str]:
    board = _as_board(conn)
    with _board_scope(board):
        return sorted(
            l["parent_id"] for l in _state(board)["links"] if l.get("child_id") == task_id
        )


def child_ids(conn: Any, task_id: str) -> list[str]:
    board = _as_board(conn)
    with _board_scope(board):
        return sorted(
            l["child_id"] for l in _state(board)["links"] if l.get("parent_id") == task_id
        )


def parent_results(conn: Any, task_id: str) -> list[tuple[str, Optional[str]]]:
    """Return ``(parent_id, result)`` for every done parent of ``task_id``."""
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        out = []
        for pid in parent_ids(board, task_id):
            row = st["tasks"].get(pid)
            if row and row.get("status") == "done":
                out.append((pid, row.get("result")))
        out.sort(key=lambda item: (
            st["tasks"][item[0]].get("completed_at") or 0
        ))
        return out


# ---------------------------------------------------------------------------
# Comments & events
# ---------------------------------------------------------------------------

def add_comment(conn: Any, task_id: str, author: str, body: str) -> int:
    if not body or not body.strip():
        raise ValueError("comment body is required")
    if not author or not author.strip():
        raise ValueError("comment author is required")
    now = int(time.time())
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        if task_id not in st["tasks"]:
            raise ValueError(f"unknown task {task_id}")
        comment_id = _next_id(st, "comment")
        st["comments"].append(
            {"id": comment_id, "task_id": task_id, "author": author.strip(),
             "body": body.strip(), "created_at": now}
        )
        _append_event(board, task_id, "commented", {"author": author, "len": len(body)})
        board.mark_dirty()
        return comment_id


def list_comments(conn: Any, task_id: str) -> list[Comment]:
    board = _as_board(conn)
    with _board_scope(board):
        rows = [c for c in _state(board)["comments"] if c.get("task_id") == task_id]
        rows.sort(key=lambda c: c.get("created_at", 0))
        return [
            Comment(
                id=int(c["id"]), task_id=c["task_id"], author=c["author"],
                body=c["body"], created_at=c["created_at"],
            )
            for c in rows
        ]


def list_events(conn: Any, task_id: str) -> list[Event]:
    board = _as_board(conn)
    with _board_scope(board):
        rows = [e for e in _state(board)["events"] if e.get("task_id") == task_id]
        rows.sort(key=lambda e: (e.get("created_at", 0), e.get("id", 0)))
        return [_event_from_row(r) for r in rows]


def list_events_after(conn: Any, cursor: int, *, limit: int = 200) -> list[Event]:
    """Return events with ``id > cursor`` in id order (watch/stream support)."""
    board = _as_board(conn)
    with _board_scope(board):
        rows = [e for e in _state(board)["events"] if int(e.get("id", 0)) > int(cursor)]
        rows.sort(key=lambda e: int(e.get("id", 0)))
        return [_event_from_row(r) for r in rows[: int(limit)]]


def _append_event(
    conn: Any,
    task_id: str,
    kind: str,
    payload: Optional[dict] = None,
    *,
    run_id: Optional[int] = None,
) -> None:
    """Record an event row. Called from within an already-open scope."""
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        now = int(time.time())
        event_id = _next_id(st, "event")
        st["events"].append(
            {
                "id": event_id,
                "task_id": task_id,
                "run_id": run_id,
                "kind": kind,
                "payload": json.dumps(payload, ensure_ascii=False) if payload else None,
                "created_at": now,
            }
        )
        board.mark_dirty()


def _end_run(
    conn: Any,
    task_id: str,
    *,
    outcome: str,
    summary: Optional[str] = None,
    error: Optional[str] = None,
    metadata: Optional[dict] = None,
    status: Optional[str] = None,
) -> Optional[int]:
    """Close the currently-active run for ``task_id`` and clear the pointer."""
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if not row or not row.get("current_run_id"):
            return None
        run_id = int(row["current_run_id"])
        run = st["runs"].get(str(run_id))
        now = int(time.time())
        if run is not None and run.get("ended_at") is None:
            run["status"] = status or outcome
            run["outcome"] = outcome
            run["summary"] = summary
            run["error"] = error
            run["metadata"] = json.dumps(metadata, ensure_ascii=False) if metadata else None
            run["ended_at"] = now
            run["claim_lock"] = None
            run["claim_expires"] = None
            run["worker_pid"] = None
        row["current_run_id"] = None
        board.mark_dirty()
        return run_id


def _current_run_id(conn: Any, task_id: str) -> Optional[int]:
    board = _as_board(conn)
    with _board_scope(board):
        row = _task_row(_state(board), task_id)
        return int(row["current_run_id"]) if row and row.get("current_run_id") else None


def _synthesize_ended_run(
    conn: Any,
    task_id: str,
    *,
    outcome: str,
    summary: Optional[str] = None,
    error: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> int:
    """Insert a zero-duration, already-closed run row."""
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        now = int(time.time())
        row = _task_row(st, task_id)
        profile = row.get("assignee") if row else None
        step_key = row.get("current_step_key") if row else None
        run_id = _next_id(st, "run")
        st["runs"][str(run_id)] = {
            "id": run_id,
            "task_id": task_id,
            "profile": profile,
            "step_key": step_key,
            "status": outcome,
            "outcome": outcome,
            "summary": summary,
            "error": error,
            "metadata": json.dumps(metadata, ensure_ascii=False) if metadata else None,
            "started_at": now,
            "ended_at": now,
            "claim_lock": None,
            "claim_expires": None,
            "worker_pid": None,
            "max_runtime_seconds": None,
            "last_heartbeat_at": None,
        }
        board.mark_dirty()
        return run_id


# ---------------------------------------------------------------------------
# Dependency resolution (todo -> ready)
# ---------------------------------------------------------------------------

def recompute_ready(conn: Any) -> int:
    """Promote ``todo`` tasks to ``ready`` when all parents are ``done``."""
    board = _as_board(conn)
    promoted = 0
    with write_txn(board):
        st = _state(board)
        for row in list(st["tasks"].values()):
            if row.get("status") != "todo":
                continue
            task_id = row["id"]
            parents = [
                st["tasks"][l["parent_id"]]
                for l in st["links"]
                if l.get("child_id") == task_id and l.get("parent_id") in st["tasks"]
            ]
            if all(p.get("status") == "done" for p in parents):
                if row.get("status") == "todo":
                    row["status"] = "ready"
                    _append_event(board, task_id, "promoted", None)
                    promoted += 1
        board.mark_dirty()
    return promoted


# ---------------------------------------------------------------------------
# Claim / complete / block
# ---------------------------------------------------------------------------

def claim_task(
    conn: Any,
    task_id: str,
    *,
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    claimer: Optional[str] = None,
) -> Optional[Task]:
    """Atomically transition ``ready -> running``. Returns Task or None."""
    board = _as_board(conn)
    now = int(time.time())
    lock = claimer or _claimer_id()
    expires = now + int(ttl_seconds)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is not None and row.get("status") == "ready" and row.get("current_run_id"):
            run = st["runs"].get(str(row["current_run_id"]))
            if run is not None and run.get("ended_at") is None:
                run["status"] = "reclaimed"
                run["outcome"] = "reclaimed"
                if run.get("summary") is None:
                    run["summary"] = "invariant recovery on re-claim"
                run["ended_at"] = now
                run["claim_lock"] = None
                run["claim_expires"] = None
                run["worker_pid"] = None
        # CAS: ready + no lock.
        if row is None or row.get("status") != "ready" or row.get("claim_lock") is not None:
            return None
        row["status"] = "running"
        row["claim_lock"] = lock
        row["claim_expires"] = expires
        if row.get("started_at") is None:
            row["started_at"] = now
        run_id = _next_id(st, "run")
        st["runs"][str(run_id)] = {
            "id": run_id,
            "task_id": task_id,
            "profile": row.get("assignee"),
            "step_key": row.get("current_step_key"),
            "status": "running",
            "claim_lock": lock,
            "claim_expires": expires,
            "worker_pid": None,
            "max_runtime_seconds": row.get("max_runtime_seconds"),
            "last_heartbeat_at": None,
            "started_at": now,
            "ended_at": None,
            "outcome": None,
            "summary": None,
            "metadata": None,
            "error": None,
        }
        row["current_run_id"] = run_id
        _append_event(
            board, task_id, "claimed",
            {"lock": lock, "expires": expires, "run_id": run_id},
            run_id=run_id,
        )
        board.mark_dirty()
        return Task.from_dict(row)


def heartbeat_claim(
    conn: Any,
    task_id: str,
    *,
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    claimer: Optional[str] = None,
) -> bool:
    """Extend a running claim. Returns True if we still own it."""
    board = _as_board(conn)
    expires = int(time.time()) + int(ttl_seconds)
    lock = claimer or _claimer_id()
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None or row.get("status") != "running" or row.get("claim_lock") != lock:
            return False
        row["claim_expires"] = expires
        run_id = _current_run_id(board, task_id)
        if run_id is not None:
            run = st["runs"].get(str(run_id))
            if run is not None:
                run["claim_expires"] = expires
        board.mark_dirty()
        return True


def release_stale_claims(conn: Any, *, signal_fn=None) -> int:
    """Reset any ``running`` task whose claim has expired."""
    board = _as_board(conn)
    now = int(time.time())
    with _board_scope(board):
        st = _state(board)
        stale = [
            (r["id"], r.get("claim_lock"), r.get("worker_pid"))
            for r in st["tasks"].values()
            if r.get("status") == "running"
            and r.get("claim_expires") is not None
            and r["claim_expires"] < now
        ]
    reclaimed = 0
    for tid, claim_lock, worker_pid in stale:
        termination = _terminate_reclaimed_worker(worker_pid, claim_lock, signal_fn=signal_fn)
        with write_txn(board):
            st = _state(board)
            row = _task_row(st, tid)
            if (
                row is None
                or row.get("status") != "running"
                or row.get("claim_lock") != claim_lock
                or row.get("claim_expires") is None
                or not (row["claim_expires"] < now)
            ):
                continue
            row["status"] = "ready"
            row["claim_lock"] = None
            row["claim_expires"] = None
            row["worker_pid"] = None
            run_id = _end_run(
                board, tid,
                outcome="reclaimed", status="reclaimed",
                error=f"stale_lock={claim_lock}",
                metadata=termination,
            )
            payload = {"stale_lock": claim_lock}
            payload.update(termination)
            _append_event(board, tid, "reclaimed", payload, run_id=run_id)
            board.mark_dirty()
            reclaimed += 1
    return reclaimed


def reclaim_task(
    conn: Any,
    task_id: str,
    *,
    reason: Optional[str] = None,
    signal_fn=None,
) -> bool:
    """Operator-driven reclaim: release the claim and reset to ``ready``."""
    board = _as_board(conn)
    with _board_scope(board):
        row = _task_row(_state(board), task_id)
        if not row:
            return False
        if row.get("status") != "running" and row.get("claim_lock") is None:
            return False
        prev_lock = row.get("claim_lock")
        worker_pid = row.get("worker_pid")
    termination = _terminate_reclaimed_worker(worker_pid, prev_lock, signal_fn=signal_fn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if (
            row is None
            or row.get("status") not in ("running", "ready", "blocked")
            or row.get("claim_lock") != prev_lock
        ):
            return False
        row["status"] = "ready"
        row["claim_lock"] = None
        row["claim_expires"] = None
        row["worker_pid"] = None
        run_id = _end_run(
            board, task_id,
            outcome="reclaimed", status="reclaimed",
            error=(
                f"manual_reclaim: {reason}" if reason
                else f"manual_reclaim lock={prev_lock}"
            ),
            metadata=termination,
        )
        payload = {"manual": True, "reason": reason, "prev_lock": prev_lock}
        payload.update(termination)
        _append_event(board, task_id, "reclaimed", payload, run_id=run_id)
        board.mark_dirty()
    _clear_failure_counter(board, task_id)
    return True


def reassign_task(
    conn: Any,
    task_id: str,
    profile: Optional[str],
    *,
    reclaim_first: bool = False,
    reason: Optional[str] = None,
) -> bool:
    """Reassign a task, optionally reclaiming a stuck running worker first."""
    if reclaim_first:
        reclaim_task(conn, task_id, reason=reason or "reassign")
    try:
        return assign_task(conn, task_id, profile)
    except RuntimeError:
        return False


def _verify_created_cards(
    conn: Any,
    completing_task_id: str,
    claimed_ids: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Partition ``claimed_ids`` into (verified, phantom). Pure read."""
    claimed = [str(x).strip() for x in (claimed_ids or []) if str(x).strip()]
    if not claimed:
        return [], []
    seen: set[str] = set()
    ordered: list[str] = []
    for cid in claimed:
        if cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        row = _task_row(st, completing_task_id)
        if row is None:
            return [], ordered
        completing_assignee = row.get("assignee")
        found = {
            tid: st["tasks"][tid].get("created_by")
            for tid in ordered
            if tid in st["tasks"]
        }
        linked_children = set(child_ids(board, completing_task_id))
    verified: list[str] = []
    phantom: list[str] = []
    for cid in ordered:
        created_by = found.get(cid)
        if created_by is None:
            phantom.append(cid)
            continue
        if completing_assignee and created_by == completing_assignee:
            verified.append(cid)
        elif created_by == completing_task_id:
            verified.append(cid)
        elif cid in linked_children:
            verified.append(cid)
        else:
            phantom.append(cid)
    return verified, phantom


_TASK_ID_PROSE_RE = re.compile(r"\bt_[a-f0-9]{8,}\b")


def _scan_prose_for_phantom_ids(conn: Any, text: str) -> list[str]:
    """Regex-scan free-form text for ``t_<hex>`` references; return phantoms."""
    if not text:
        return []
    matches = _TASK_ID_PROSE_RE.findall(text)
    if not matches:
        return []
    seen: set[str] = set()
    unique: list[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            unique.append(m)
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        return [m for m in unique if m not in st["tasks"]]


class HallucinatedCardsError(ValueError):
    """Raised by ``complete_task`` when ``created_cards`` contains phantoms."""

    def __init__(self, phantom: list[str], completing_task_id: str):
        self.phantom = list(phantom)
        self.completing_task_id = completing_task_id
        super().__init__(
            f"completion blocked: claimed created_cards that do not exist "
            f"or were not created by this worker: {', '.join(phantom)}"
        )


def complete_task(
    conn: Any,
    task_id: str,
    *,
    result: Optional[str] = None,
    summary: Optional[str] = None,
    metadata: Optional[dict] = None,
    created_cards: Optional[Iterable[str]] = None,
    expected_run_id: Optional[int] = None,
) -> bool:
    """Transition ``running|ready|blocked -> done`` and record ``result``."""
    board = _as_board(conn)
    now = int(time.time())
    if created_cards:
        verified_cards, phantom_cards = _verify_created_cards(conn, task_id, created_cards)
        if phantom_cards:
            with write_txn(board):
                _append_event(
                    board, task_id, "completion_blocked_hallucination",
                    {
                        "phantom_cards": phantom_cards,
                        "verified_cards": verified_cards,
                        "summary_preview": (
                            (summary or result or "").strip().splitlines()[0][:200]
                            if (summary or result)
                            else None
                        ),
                    },
                )
                board.mark_dirty()
            raise HallucinatedCardsError(phantom_cards, task_id)
    else:
        verified_cards = []

    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None or row.get("status") not in ("running", "ready", "blocked"):
            return False
        if expected_run_id is not None and row.get("current_run_id") != int(expected_run_id):
            return False
        row["status"] = "done"
        row["result"] = result
        row["completed_at"] = now
        row["claim_lock"] = None
        row["claim_expires"] = None
        row["worker_pid"] = None
        run_id = _end_run(
            board, task_id,
            outcome="completed", status="done",
            summary=summary if summary is not None else result,
            metadata=metadata,
        )
        if run_id is None and (summary or metadata or result):
            run_id = _synthesize_ended_run(
                board, task_id,
                outcome="completed",
                summary=summary if summary is not None else result,
                metadata=metadata,
            )
        ev_summary = (summary if summary is not None else result) or ""
        ev_summary = ev_summary.strip().splitlines()[0][:400] if ev_summary else ""
        completed_payload: dict = {
            "result_len": len(result) if result else 0,
            "summary": ev_summary or None,
        }
        if verified_cards:
            completed_payload["verified_cards"] = verified_cards
        _append_event(board, task_id, "completed", completed_payload, run_id=run_id)
        board.mark_dirty()
    scan_text = " ".join(filter(None, [summary, result]))
    if scan_text:
        phantom_refs = _scan_prose_for_phantom_ids(board, scan_text)
        phantom_refs = [p for p in phantom_refs if p not in set(verified_cards)]
        if phantom_refs:
            with write_txn(board):
                _append_event(
                    board, task_id, "suspected_hallucinated_references",
                    {"phantom_refs": phantom_refs, "source": "completion_summary"},
                    run_id=run_id,
                )
                board.mark_dirty()
    _clear_failure_counter(board, task_id)
    recompute_ready(board)
    return True


def edit_completed_task_result(
    conn: Any,
    task_id: str,
    *,
    result: str,
    summary: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> bool:
    """Backfill the user-visible result for an already completed task."""
    board = _as_board(conn)
    handoff_summary = summary if summary is not None else result
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if not row or row.get("status") != "done":
            return False
        row["result"] = result
        completed_runs = [
            r for r in st["runs"].values()
            if r.get("task_id") == task_id and r.get("outcome") == "completed"
        ]
        completed_runs.sort(
            key=lambda r: (r.get("ended_at") or r.get("started_at") or 0, r["id"]),
            reverse=True,
        )
        run_id = int(completed_runs[0]["id"]) if completed_runs else None
        if run_id is None:
            run_id = _synthesize_ended_run(
                board, task_id,
                outcome="completed",
                summary=handoff_summary,
                metadata=metadata,
            )
        else:
            run = st["runs"][str(run_id)]
            run["summary"] = handoff_summary
            if metadata is not None:
                run["metadata"] = json.dumps(metadata, ensure_ascii=False)
        ev_summary = (
            handoff_summary.strip().splitlines()[0][:400]
            if handoff_summary else ""
        )
        _append_event(
            board, task_id, "edited",
            {
                "fields": (
                    ["result", "summary"]
                    + (["metadata"] if metadata is not None else [])
                ),
                "result_len": len(result) if result else 0,
                "summary": ev_summary or None,
            },
            run_id=run_id,
        )
        board.mark_dirty()
    return True


def block_task(
    conn: Any,
    task_id: str,
    *,
    reason: Optional[str] = None,
    expected_run_id: Optional[int] = None,
) -> bool:
    """Transition ``running|ready -> blocked``."""
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None or row.get("status") not in ("running", "ready"):
            return False
        if expected_run_id is not None and row.get("current_run_id") != int(expected_run_id):
            return False
        row["status"] = "blocked"
        row["claim_lock"] = None
        row["claim_expires"] = None
        row["worker_pid"] = None
        run_id = _end_run(board, task_id, outcome="blocked", status="blocked", summary=reason)
        if run_id is None and reason:
            run_id = _synthesize_ended_run(board, task_id, outcome="blocked", summary=reason)
        _append_event(board, task_id, "blocked", {"reason": reason}, run_id=run_id)
        board.mark_dirty()
        return True


def unblock_task(conn: Any, task_id: str) -> bool:
    """Transition ``blocked -> ready``."""
    board = _as_board(conn)
    now = int(time.time())
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is not None and row.get("status") == "blocked" and row.get("current_run_id"):
            run = st["runs"].get(str(row["current_run_id"]))
            if run is not None and run.get("ended_at") is None:
                run["status"] = "reclaimed"
                run["outcome"] = "reclaimed"
                if run.get("summary") is None:
                    run["summary"] = "invariant recovery on unblock"
                run["ended_at"] = now
                run["claim_lock"] = None
                run["claim_expires"] = None
                run["worker_pid"] = None
        if row is None or row.get("status") != "blocked":
            return False
        row["status"] = "ready"
        row["current_run_id"] = None
        _append_event(board, task_id, "unblocked", None)
        board.mark_dirty()
        return True


def specify_triage_task(
    conn: Any,
    task_id: str,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    author: Optional[str] = None,
) -> bool:
    """Flesh out a triage task and promote it to ``todo``."""
    if title is not None and not title.strip():
        raise ValueError("title cannot be blank")
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None or row.get("status") != "triage":
            return False
        changed_fields: list[str] = []
        if title is not None and title.strip() != (row.get("title") or ""):
            row["title"] = title.strip()
            changed_fields.append("title")
        if body is not None and (body or "") != (row.get("body") or ""):
            row["body"] = body
            changed_fields.append("body")
        row["status"] = "todo"
        if changed_fields and author and author.strip():
            comment_id = _next_id(st, "comment")
            st["comments"].append(
                {
                    "id": comment_id,
                    "task_id": task_id,
                    "author": author.strip(),
                    "body": "Specified — updated " + ", ".join(changed_fields) + " and promoted to todo.",
                    "created_at": int(time.time()),
                }
            )
        _append_event(
            board, task_id, "specified",
            {"changed_fields": changed_fields} if changed_fields else None,
        )
        board.mark_dirty()
    recompute_ready(board)
    return True


def archive_task(conn: Any, task_id: str) -> bool:
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None or row.get("status") == "archived":
            return False
        row["status"] = "archived"
        row["claim_lock"] = None
        row["claim_expires"] = None
        row["worker_pid"] = None
        run_id = _end_run(
            board, task_id,
            outcome="reclaimed", status="reclaimed",
            summary="task archived with run still active",
        )
        _append_event(board, task_id, "archived", None, run_id=run_id)
        board.mark_dirty()
        return True


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def resolve_workspace(task: Task, *, board: Optional[str] = None) -> Path:
    """Resolve (and create if needed) the workspace for a task."""
    kind = task.workspace_kind or "scratch"
    if kind == "scratch":
        if task.workspace_path:
            p = Path(task.workspace_path).expanduser()
            if not p.is_absolute():
                raise ValueError(
                    f"task {task.id} has non-absolute workspace_path "
                    f"{task.workspace_path!r}; workspace paths must be absolute"
                )
        else:
            p = workspaces_root(board=board) / task.id
        p.mkdir(parents=True, exist_ok=True)
        return p
    if kind == "dir":
        if not task.workspace_path:
            raise ValueError(
                f"task {task.id} has workspace_kind=dir but no workspace_path"
            )
        p = Path(task.workspace_path).expanduser()
        if not p.is_absolute():
            raise ValueError(
                f"task {task.id} has non-absolute workspace_path "
                f"{task.workspace_path!r}; use an absolute path "
                f"(relative paths are ambiguous against the dispatcher's CWD)"
            )
        p.mkdir(parents=True, exist_ok=True)
        return p
    if kind == "worktree":
        if not task.workspace_path:
            return Path.cwd() / ".worktrees" / task.id
        p = Path(task.workspace_path).expanduser()
        if not p.is_absolute():
            raise ValueError(
                f"task {task.id} has non-absolute worktree path "
                f"{task.workspace_path!r}; use an absolute path"
            )
        return p
    raise ValueError(f"unknown workspace_kind: {kind}")


def set_workspace_path(conn: Any, task_id: str, path: Path | str) -> None:
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is not None:
            row["workspace_path"] = str(path)
            board.mark_dirty()


# ---------------------------------------------------------------------------
# Dispatcher (one-shot pass)
# ---------------------------------------------------------------------------

DEFAULT_FAILURE_LIMIT = 2
DEFAULT_SPAWN_FAILURE_LIMIT = DEFAULT_FAILURE_LIMIT
DEFAULT_LOG_ROTATE_BYTES = 2 * 1024 * 1024


@dataclass
class DispatchResult:
    """Outcome of a single ``dispatch`` pass."""

    reclaimed: int = 0
    promoted: int = 0
    spawned: list[tuple[str, str, str]] = field(default_factory=list)
    skipped_unassigned: list[str] = field(default_factory=list)
    skipped_nonspawnable: list[str] = field(default_factory=list)
    crashed: list[str] = field(default_factory=list)
    auto_blocked: list[str] = field(default_factory=list)
    timed_out: list[str] = field(default_factory=list)


_RECENT_WORKER_EXIT_TTL_SECONDS = 600
_RECENT_WORKER_EXITS_MAX = 4096
_recent_worker_exits: "dict[int, tuple[int, float]]" = {}


def _record_worker_exit(pid: int, raw_status: int) -> None:
    """Record a reaped child's exit status for later classification."""
    if not pid or pid <= 0:
        return
    now = time.time()
    _recent_worker_exits[int(pid)] = (int(raw_status), now)
    if len(_recent_worker_exits) > _RECENT_WORKER_EXITS_MAX // 2:
        cutoff = now - _RECENT_WORKER_EXIT_TTL_SECONDS
        for _pid in [p for p, (_s, t) in _recent_worker_exits.items() if t < cutoff]:
            _recent_worker_exits.pop(_pid, None)
    if len(_recent_worker_exits) > _RECENT_WORKER_EXITS_MAX:
        ordered = sorted(_recent_worker_exits.items(), key=lambda kv: kv[1][1])
        for _pid, _ in ordered[: len(ordered) // 2]:
            _recent_worker_exits.pop(_pid, None)


def _classify_worker_exit(pid: int) -> "tuple[str, Optional[int]]":
    """Classify a recently-reaped worker by pid."""
    entry = _recent_worker_exits.get(int(pid))
    if entry is None:
        return ("unknown", None)
    raw, _ = entry
    try:
        if os.WIFEXITED(raw):
            code = os.WEXITSTATUS(raw)
            if code == 0:
                return ("clean_exit", 0)
            return ("nonzero_exit", code)
        if os.WIFSIGNALED(raw):
            return ("signaled", os.WTERMSIG(raw))
    except Exception:
        pass
    return ("unknown", None)


def _pid_alive(pid: Optional[int]) -> bool:
    """Return True if ``pid`` is still running on this host."""
    if not pid or pid <= 0:
        return False
    try:
        if hasattr(os, "kill"):
            os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    if sys.platform == "linux":
        try:
            with open(f"/proc/{int(pid)}/status", "r") as f:
                for line in f:
                    if line.startswith("State:"):
                        if "Z" in line.split(":", 1)[1]:
                            return False
                        break
        except (FileNotFoundError, PermissionError, OSError):
            pass
    elif sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(int(pid))],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
                check=False,
            )
            if proc.returncode != 0:
                return False
            if "Z" in (proc.stdout or "").strip():
                return False
        except (OSError, subprocess.SubprocessError, TimeoutError):
            pass
    return True


def _terminate_reclaimed_worker(
    pid: Optional[int],
    claim_lock: Optional[str],
    *,
    signal_fn=None,
) -> dict[str, Any]:
    """Best-effort host-local worker termination for reclaim paths."""
    import signal

    info: dict[str, Any] = {
        "prev_pid": int(pid) if pid else None,
        "host_local": False,
        "termination_attempted": False,
        "terminated": False,
        "sigkill": False,
    }
    if not pid or pid <= 0 or not claim_lock:
        return info
    host_prefix = f"{_claimer_id().split(':', 1)[0]}:"
    if not str(claim_lock).startswith(host_prefix):
        return info
    info["host_local"] = True
    kill = signal_fn if signal_fn is not None else (
        os.kill if hasattr(os, "kill") else None
    )
    if kill is None:
        return info
    info["termination_attempted"] = True
    try:
        kill(int(pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return info
    for _ in range(10):
        if not _pid_alive(pid):
            info["terminated"] = True
            return info
        time.sleep(0.5)
    if _pid_alive(pid):
        try:
            kill(int(pid), signal.SIGKILL)
            info["sigkill"] = True
        except (ProcessLookupError, OSError):
            return info
    info["terminated"] = not _pid_alive(pid)
    return info


def heartbeat_worker(
    conn: Any,
    task_id: str,
    *,
    note: Optional[str] = None,
    expected_run_id: Optional[int] = None,
) -> bool:
    """Record a ``heartbeat`` event + touch ``last_heartbeat_at``."""
    board = _as_board(conn)
    now = int(time.time())
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None or row.get("status") != "running":
            return False
        if expected_run_id is not None and row.get("current_run_id") != int(expected_run_id):
            return False
        row["last_heartbeat_at"] = now
        run_id = (
            int(expected_run_id)
            if expected_run_id is not None
            else _current_run_id(board, task_id)
        )
        if run_id is not None:
            run = st["runs"].get(str(run_id))
            if run is not None:
                run["last_heartbeat_at"] = now
        _append_event(
            board, task_id, "heartbeat",
            {"note": note} if note else None,
            run_id=run_id,
        )
        board.mark_dirty()
    return True


def enforce_max_runtime(conn: Any, *, signal_fn=None) -> list[str]:
    """Terminate workers whose per-task ``max_runtime_seconds`` has elapsed."""
    import signal

    board = _as_board(conn)
    timed_out: list[str] = []
    now = int(time.time())
    host_prefix = f"{_claimer_id().split(':', 1)[0]}:"
    with _board_scope(board):
        st = _state(board)
        rows = []
        for row in st["tasks"].values():
            if row.get("status") != "running":
                continue
            if row.get("max_runtime_seconds") is None or row.get("worker_pid") is None:
                continue
            run = st["runs"].get(str(row["current_run_id"])) if row.get("current_run_id") else None
            active_started = (run.get("started_at") if run else None) or row.get("started_at")
            if active_started is None:
                continue
            rows.append((row["id"], row["worker_pid"], int(active_started),
                         int(row["max_runtime_seconds"]), row.get("claim_lock") or ""))
    for tid, pid, active_started, limit_seconds, lock in rows:
        if not lock.startswith(host_prefix):
            continue
        elapsed = now - int(active_started)
        if elapsed < int(limit_seconds):
            continue
        pid = int(pid)
        killed = False
        kill = signal_fn if signal_fn is not None else (
            os.kill if hasattr(os, "kill") else None
        )
        if kill is not None:
            try:
                kill(pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            for _ in range(10):
                if not _pid_alive(pid):
                    break
                time.sleep(0.5)
            if _pid_alive(pid):
                try:
                    kill(pid, signal.SIGKILL)
                    killed = True
                except (ProcessLookupError, OSError):
                    pass
        updated = False
        with write_txn(board):
            st = _state(board)
            row = _task_row(st, tid)
            if row is not None and row.get("status") == "running":
                row["status"] = "ready"
                row["claim_lock"] = None
                row["claim_expires"] = None
                row["worker_pid"] = None
                row["last_heartbeat_at"] = None
                payload = {
                    "pid": pid,
                    "elapsed_seconds": int(elapsed),
                    "limit_seconds": int(limit_seconds),
                    "sigkill": killed,
                }
                run_id = _end_run(
                    board, tid,
                    outcome="timed_out", status="timed_out",
                    error=f"elapsed {int(elapsed)}s > limit {int(limit_seconds)}s",
                    metadata=payload,
                )
                _append_event(board, tid, "timed_out", payload, run_id=run_id)
                board.mark_dirty()
                timed_out.append(tid)
                updated = True
        if updated:
            _record_task_failure(
                board, tid,
                error=f"elapsed {int(elapsed)}s > limit {int(limit_seconds)}s",
                outcome="timed_out",
                release_claim=False,
                end_run=False,
                event_payload_extra={"pid": pid, "sigkill": killed},
            )
    return timed_out


def set_max_runtime(conn: Any, task_id: str, seconds: Optional[int]) -> bool:
    """Set or clear the per-task max_runtime_seconds. Returns True on success."""
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None:
            return False
        row["max_runtime_seconds"] = int(seconds) if seconds is not None else None
        board.mark_dirty()
        return True


def detect_crashed_workers(conn: Any) -> list[str]:
    """Reclaim ``running`` tasks whose worker PID is no longer alive."""
    board = _as_board(conn)
    crashed: list[str] = []
    crash_details: list[tuple[str, int, str, bool, str]] = []
    with write_txn(board):
        st = _state(board)
        host_prefix = f"{_claimer_id().split(':', 1)[0]}:"
        for row in list(st["tasks"].values()):
            if row.get("status") != "running" or row.get("worker_pid") is None:
                continue
            lock = row.get("claim_lock") or ""
            if not lock.startswith(host_prefix):
                continue
            if _pid_alive(row["worker_pid"]):
                continue
            pid = int(row["worker_pid"])
            kind, code = _classify_worker_exit(pid)
            if kind == "clean_exit":
                protocol_violation = True
                error_text = (
                    "worker exited cleanly (rc=0) without calling "
                    "kanban_complete or kanban_block — protocol violation"
                )
                event_kind = "protocol_violation"
                event_payload = {"pid": pid, "claimer": lock, "exit_code": code}
            else:
                protocol_violation = False
                if kind == "nonzero_exit":
                    error_text = f"pid {pid} exited with code {code}"
                elif kind == "signaled":
                    error_text = f"pid {pid} killed by signal {code}"
                else:
                    error_text = f"pid {pid} not alive"
                event_kind = "crashed"
                event_payload = {"pid": pid, "claimer": lock}
                if code is not None and kind != "unknown":
                    event_payload["exit_kind"] = kind
                    event_payload["exit_code"] = code
            if row.get("status") == "running":
                row["status"] = "ready"
                row["claim_lock"] = None
                row["claim_expires"] = None
                row["worker_pid"] = None
                run_id = _end_run(
                    board, row["id"],
                    outcome="crashed", status="crashed",
                    error=error_text,
                    metadata=dict(event_payload),
                )
                _append_event(board, row["id"], event_kind, event_payload, run_id=run_id)
                board.mark_dirty()
                crashed.append(row["id"])
                crash_details.append((row["id"], pid, lock, protocol_violation, error_text))
    auto_blocked: list[str] = []
    for tid, pid, claimer, protocol_violation, error_text in crash_details:
        tripped = _record_task_failure(
            board, tid,
            error=error_text,
            outcome="crashed",
            failure_limit=(1 if protocol_violation else None),
            release_claim=False,
            end_run=False,
            event_payload_extra={"pid": pid, "claimer": claimer},
        )
        if tripped:
            auto_blocked.append(tid)
    detect_crashed_workers._last_auto_blocked = auto_blocked  # type: ignore[attr-defined]
    return crashed


def _record_task_failure(
    conn: Any,
    task_id: str,
    error: str,
    *,
    outcome: str,
    failure_limit: int = None,
    release_claim: bool = False,
    end_run: bool = False,
    event_payload_extra: Optional[dict] = None,
) -> bool:
    """Record a non-success outcome and maybe trip the circuit breaker."""
    if failure_limit is None:
        failure_limit = DEFAULT_FAILURE_LIMIT
    board = _as_board(conn)
    blocked = False
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None:
            return False
        failures = int(row.get("consecutive_failures") or 0) + 1
        cur_status = row.get("status")
        task_override = row.get("max_retries")
        if task_override is not None:
            effective_limit = int(task_override)
            limit_source = "task"
        else:
            effective_limit = int(failure_limit)
            limit_source = "dispatcher"
        if failures >= effective_limit:
            if release_claim:
                if cur_status in ("running", "ready"):
                    row["status"] = "blocked"
                    row["claim_lock"] = None
                    row["claim_expires"] = None
                    row["worker_pid"] = None
                    row["consecutive_failures"] = failures
                    row["last_failure_error"] = error[:500]
            else:
                if cur_status in ("ready", "running"):
                    row["status"] = "blocked"
                    row["consecutive_failures"] = failures
                    row["last_failure_error"] = error[:500]
            run_id = None
            if end_run:
                run_id = _end_run(
                    board, task_id,
                    outcome="gave_up", status="gave_up",
                    error=error[:500],
                    metadata={
                        "failures": failures,
                        "trigger_outcome": outcome,
                        "effective_limit": effective_limit,
                        "limit_source": limit_source,
                    },
                )
            payload = {
                "failures": failures,
                "effective_limit": effective_limit,
                "limit_source": limit_source,
                "error": error[:500],
                "trigger_outcome": outcome,
            }
            if event_payload_extra:
                payload.update(event_payload_extra)
            _append_event(board, task_id, "gave_up", payload, run_id=run_id)
            board.mark_dirty()
            blocked = True
        else:
            if release_claim:
                if cur_status == "running":
                    row["status"] = "ready"
                    row["claim_lock"] = None
                    row["claim_expires"] = None
                    row["worker_pid"] = None
                    row["consecutive_failures"] = failures
                    row["last_failure_error"] = error[:500]
            else:
                row["consecutive_failures"] = failures
                row["last_failure_error"] = error[:500]
            if end_run:
                run_id = _end_run(
                    board, task_id,
                    outcome=outcome, status=outcome,
                    error=error[:500],
                    metadata={"failures": failures},
                )
                _append_event(
                    board, task_id, outcome,
                    {"error": error[:500], "failures": failures},
                    run_id=run_id,
                )
            board.mark_dirty()
    return blocked


def _record_spawn_failure(
    conn: Any,
    task_id: str,
    error: str,
    *,
    failure_limit: int = None,
) -> bool:
    return _record_task_failure(
        conn, task_id, error,
        outcome="spawn_failed",
        failure_limit=failure_limit,
        release_claim=True,
        end_run=True,
    )


def _set_worker_pid(conn: Any, task_id: str, pid: int) -> None:
    """Record the spawned child's pid + emit a ``spawned`` event."""
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is not None:
            row["worker_pid"] = int(pid)
        run_id = _current_run_id(board, task_id)
        if run_id is not None:
            run = st["runs"].get(str(run_id))
            if run is not None:
                run["worker_pid"] = int(pid)
        _append_event(board, task_id, "spawned", {"pid": int(pid)}, run_id=run_id)
        board.mark_dirty()


def _clear_failure_counter(conn: Any, task_id: str) -> None:
    """Reset the unified consecutive-failures counter."""
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is not None:
            row["consecutive_failures"] = 0
            row["last_failure_error"] = None
            board.mark_dirty()


_clear_spawn_failures = _clear_failure_counter


def has_spawnable_ready(conn: Any) -> bool:
    """Return True iff a ready+assigned+unclaimed task maps to a real profile."""
    board = _as_board(conn)
    with _board_scope(board):
        assignees = {
            r.get("assignee") for r in _state(board)["tasks"].values()
            if r.get("status") == "ready" and r.get("assignee") is not None
            and r.get("claim_lock") is None
        }
    if not assignees:
        return False
    try:
        from hermes_cli.profiles import profile_exists
    except Exception:
        return True
    for assignee in assignees:
        if profile_exists(assignee):
            return True
    return False


def dispatch_once(
    conn: Any,
    *,
    spawn_fn=None,
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS,
    dry_run: bool = False,
    max_spawn: Optional[int] = None,
    failure_limit: int = DEFAULT_SPAWN_FAILURE_LIMIT,
    board: Optional[str] = None,
) -> DispatchResult:
    """Run one dispatcher tick."""
    try:
        while True:
            try:
                _pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if _pid == 0:
                break
            _record_worker_exit(_pid, _status)
    except Exception:
        pass

    board_h = _as_board(conn)
    result = DispatchResult()
    result.reclaimed = release_stale_claims(board_h)
    result.crashed = detect_crashed_workers(board_h)
    _crash_auto_blocked = getattr(detect_crashed_workers, "_last_auto_blocked", [])
    if _crash_auto_blocked:
        result.auto_blocked.extend(_crash_auto_blocked)
    result.timed_out = enforce_max_runtime(board_h)
    result.promoted = recompute_ready(board_h)

    with _board_scope(board_h):
        ready_rows = [
            (r["id"], r.get("assignee")) for r in _state(board_h)["tasks"].values()
            if r.get("status") == "ready" and r.get("claim_lock") is None
        ]
        ready_rows.sort(
            key=lambda item: (
                -( _state(board_h)["tasks"][item[0]].get("priority") or 0),
                _state(board_h)["tasks"][item[0]].get("created_at", 0),
            )
        )
    spawned = 0
    for tid, assignee in ready_rows:
        if max_spawn is not None and spawned >= max_spawn:
            break
        if not assignee:
            result.skipped_unassigned.append(tid)
            continue
        try:
            from hermes_cli.profiles import profile_exists
        except Exception:
            profile_exists = None  # type: ignore[assignment]
        if profile_exists is not None and not profile_exists(assignee):
            result.skipped_nonspawnable.append(tid)
            continue
        if dry_run:
            result.spawned.append((tid, assignee, ""))
            continue
        claimed = claim_task(board_h, tid, ttl_seconds=ttl_seconds)
        if claimed is None:
            continue
        try:
            workspace = resolve_workspace(claimed, board=board)
        except Exception as exc:
            auto = _record_spawn_failure(
                board_h, claimed.id, f"workspace: {exc}",
                failure_limit=failure_limit,
            )
            if auto:
                result.auto_blocked.append(claimed.id)
            continue
        set_workspace_path(board_h, claimed.id, str(workspace))
        _spawn = spawn_fn if spawn_fn is not None else _default_spawn
        try:
            import inspect

            try:
                sig = inspect.signature(_spawn)
                if "board" in sig.parameters:
                    pid = _spawn(claimed, str(workspace), board=board)
                else:
                    pid = _spawn(claimed, str(workspace))
            except (TypeError, ValueError):
                pid = _spawn(claimed, str(workspace))
            if pid:
                _set_worker_pid(board_h, claimed.id, int(pid))
            result.spawned.append((claimed.id, claimed.assignee or "", str(workspace)))
            spawned += 1
        except Exception as exc:
            auto = _record_spawn_failure(
                board_h, claimed.id, str(exc),
                failure_limit=failure_limit,
            )
            if auto:
                result.auto_blocked.append(claimed.id)
    return result


def _rotate_worker_log(log_path: Path, max_bytes: int) -> None:
    """Rotate ``<log>`` to ``<log>.1`` if it exceeds ``max_bytes``."""
    try:
        if not log_path.exists():
            return
        if log_path.stat().st_size <= max_bytes:
            return
        rotated = log_path.with_suffix(log_path.suffix + ".1")
        try:
            if rotated.exists():
                rotated.unlink()
        except OSError:
            pass
        log_path.rename(rotated)
    except OSError:
        pass


def _default_spawn(
    task: Task,
    workspace: str,
    *,
    board: Optional[str] = None,
) -> Optional[int]:
    """Fire-and-forget ``hermes -p <profile> chat -q ...`` subprocess."""
    import subprocess

    if not task.assignee:
        raise ValueError(f"task {task.id} has no assignee")
    from hermes_cli.profiles import normalize_profile_name

    profile_arg = normalize_profile_name(task.assignee)
    prompt = f"work kanban task {task.id}"
    env = dict(os.environ)
    if task.tenant:
        env["HERMES_TENANT"] = task.tenant
    env["HERMES_KANBAN_TASK"] = task.id
    env["HERMES_KANBAN_WORKSPACE"] = workspace
    if task.current_run_id is not None:
        env["HERMES_KANBAN_RUN_ID"] = str(task.current_run_id)
    if task.claim_lock:
        env["HERMES_KANBAN_CLAIM_LOCK"] = task.claim_lock
    env["HERMES_KANBAN_DB"] = str(kanban_db_path(board=board))
    env["HERMES_KANBAN_WORKSPACES_ROOT"] = str(workspaces_root(board=board))
    resolved_board = _normalize_board_slug(board) or get_current_board()
    env["HERMES_KANBAN_BOARD"] = resolved_board
    env["HERMES_PROFILE"] = profile_arg
    cmd = [
        "hermes",
        "-p", profile_arg,
        "--skills", "kanban-worker",
    ]
    if task.skills:
        for sk in task.skills:
            if sk and sk != "kanban-worker":
                cmd.extend(["--skills", sk])
    cmd.extend(["chat", "-q", prompt])
    log_dir = worker_logs_dir(board=board)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{task.id}.log"
    _rotate_worker_log(log_path, DEFAULT_LOG_ROTATE_BYTES)
    log_f = open(log_path, "ab")
    try:
        proc = subprocess.Popen(  # noqa: S603 -- argv is a fixed list built above
            cmd,
            cwd=workspace if os.path.isdir(workspace) else None,
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        log_f.close()
        raise RuntimeError(
            "`hermes` executable not found on PATH. "
            "Install Hermes Agent or activate its venv before running the kanban dispatcher."
        )
    return proc.pid


def run_daemon(
    *,
    interval: float = 60.0,
    max_spawn: Optional[int] = None,
    failure_limit: int = DEFAULT_SPAWN_FAILURE_LIMIT,
    stop_event=None,
    on_tick=None,
) -> None:
    """Run the dispatcher in a loop until interrupted."""
    import signal
    import threading

    if stop_event is None:
        stop_event = threading.Event()

    def _handle(_signum, _frame):
        stop_event.set()

    if threading.current_thread() is threading.main_thread():
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _handle)
                except (ValueError, OSError):
                    pass

    while not stop_event.is_set():
        try:
            with contextlib.closing(connect()) as conn:
                res = dispatch_once(
                    conn,
                    max_spawn=max_spawn,
                    failure_limit=failure_limit,
                )
            if on_tick is not None:
                try:
                    on_tick(res)
                except Exception:
                    pass
        except Exception:
            import traceback

            traceback.print_exc()
        stop_event.wait(timeout=interval)


# ---------------------------------------------------------------------------
# Worker context builder (what a spawned worker sees)
# ---------------------------------------------------------------------------

def build_worker_context(conn: Any, task_id: str) -> str:
    """Return the full text a worker should read to understand its task."""
    board = _as_board(conn)
    task = get_task(board, task_id)
    if not task:
        raise ValueError(f"unknown task {task_id}")

    def _cap(s: Optional[str], limit: int = _CTX_MAX_FIELD_BYTES) -> str:
        if not s:
            return ""
        s = s.strip()
        if len(s) <= limit:
            return s
        return s[:limit] + f"… [truncated, {len(s) - limit} chars omitted]"

    lines: list[str] = []
    lines.append(f"# Kanban task {task.id}: {task.title}")
    lines.append("")
    lines.append(f"Assignee: {task.assignee or '(unassigned)'}")
    lines.append(f"Status:   {task.status}")
    if task.tenant:
        lines.append(f"Tenant:   {task.tenant}")
    lines.append(f"Workspace: {task.workspace_kind} @ {task.workspace_path or '(unresolved)'}")
    lines.append("")
    if task.body and task.body.strip():
        lines.append("## Body")
        lines.append(_cap(task.body, _CTX_MAX_BODY_BYTES))
        lines.append("")
    all_prior = [r for r in list_runs(board, task_id) if r.ended_at is not None]
    if len(all_prior) > _CTX_MAX_PRIOR_ATTEMPTS:
        omitted = len(all_prior) - _CTX_MAX_PRIOR_ATTEMPTS
        shown = all_prior[-_CTX_MAX_PRIOR_ATTEMPTS:]
        first_shown_idx = omitted + 1
    else:
        omitted = 0
        shown = all_prior
        first_shown_idx = 1
    if shown:
        lines.append("## Prior attempts on this task")
        if omitted:
            lines.append(
                f"_({omitted} earlier attempt{'s' if omitted != 1 else ''} "
                f"omitted; showing most recent {len(shown)})_"
            )
        for offset, run in enumerate(shown):
            idx = first_shown_idx + offset
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(run.started_at))
            profile = run.profile or "(unknown)"
            outcome = run.outcome or run.status
            lines.append(f"### Attempt {idx} — {outcome} ({profile}, {ts})")
            if run.summary and run.summary.strip():
                lines.append(_cap(run.summary))
            if run.error and run.error.strip():
                lines.append(f"_error_: {_cap(run.error)}")
            if run.metadata:
                try:
                    meta_str = json.dumps(run.metadata, ensure_ascii=False, sort_keys=True)
                    lines.append(f"_metadata_: `{_cap(meta_str)}`")
                except Exception:
                    pass
            lines.append("")
    parent_ids_list = parent_ids(board, task_id)
    if parent_ids_list:
        wrote_header = False
        for pid in parent_ids_list:
            pt = get_task(board, pid)
            if not pt or pt.status != "done":
                continue
            runs = [r for r in list_runs(board, pid) if r.outcome == "completed"]
            runs.sort(key=lambda r: r.started_at, reverse=True)
            run = runs[0] if runs else None
            if not wrote_header:
                lines.append("## Parent task results")
                wrote_header = True
            lines.append(f"### {pid}")
            body_lines: list[str] = []
            if run is not None and run.summary and run.summary.strip():
                body_lines.append(_cap(run.summary))
            elif pt.result:
                body_lines.append(_cap(pt.result))
            else:
                body_lines.append("(no result recorded)")
            if run is not None and run.metadata:
                try:
                    meta_str = json.dumps(run.metadata, ensure_ascii=False, sort_keys=True)
                    body_lines.append(f"_metadata_: `{_cap(meta_str)}`")
                except Exception:
                    pass
            lines.extend(body_lines)
            lines.append("")
    if task.assignee:
        with _board_scope(board):
            st = _state(board)
            role = []
            for r in st["runs"].values():
                if (
                    r.get("profile") == task.assignee
                    and r.get("task_id") != task_id
                    and r.get("outcome") == "completed"
                ):
                    trow = st["tasks"].get(r["task_id"])
                    if trow is not None:
                        role.append((r, trow))
            role.sort(key=lambda item: (item[0].get("ended_at") or 0), reverse=True)
            role = role[:5]
        if role:
            lines.append(f"## Recent work by @{task.assignee}")
            for run_row, trow in role:
                ts = time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(int(run_row.get("ended_at") or 0))
                )
                s = (run_row.get("summary") or "").strip().splitlines()
                first = s[0][:200] if s else "(no summary)"
                lines.append(f"- {run_row['task_id']} — {trow.get('title')} ({ts}): {first}")
            lines.append("")
    all_comments = list_comments(board, task_id)
    if len(all_comments) > _CTX_MAX_COMMENTS:
        omitted_c = len(all_comments) - _CTX_MAX_COMMENTS
        shown_c = all_comments[-_CTX_MAX_COMMENTS:]
    else:
        omitted_c = 0
        shown_c = all_comments
    if shown_c:
        lines.append("## Comment thread")
        if omitted_c:
            lines.append(
                f"_({omitted_c} earlier comment{'s' if omitted_c != 1 else ''} "
                f"omitted; showing most recent {len(shown_c)})_"
            )
        for c in shown_c:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(c.created_at))
            lines.append(f"**{c.author}** ({ts}):")
            lines.append(_cap(c.body, _CTX_MAX_COMMENT_BYTES))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Stats + SLA helpers
# ---------------------------------------------------------------------------

def board_stats(conn: Any) -> dict:
    """Per-status + per-assignee counts, plus the oldest ``ready`` age."""
    board = _as_board(conn)
    with _board_scope(board):
        tasks = [r for r in _state(board)["tasks"].values() if r.get("status") != "archived"]
        by_status: dict[str, int] = {}
        for row in tasks:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        by_assignee: dict[str, dict[str, int]] = {}
        for row in tasks:
            if row.get("assignee") is not None:
                by_assignee.setdefault(row["assignee"], {})
                by_assignee[row["assignee"]][row["status"]] = (
                    by_assignee[row["assignee"]].get(row["status"], 0) + 1
                )
        ready_ts = [r["created_at"] for r in _state(board)["tasks"].values()
                    if r.get("status") == "ready" and r.get("created_at") is not None]
        now = int(time.time())
        oldest_ready_age = (now - int(min(ready_ts))) if ready_ts else None
        return {
            "by_status": by_status,
            "by_assignee": by_assignee,
            "oldest_ready_age_seconds": oldest_ready_age,
            "now": now,
        }


def task_age(task: Task) -> dict:
    """Return age metrics for a single task. All values are seconds or None."""
    now = int(time.time())
    age_since_created = now - int(task.created_at) if task.created_at else None
    age_since_started = (
        now - int(task.started_at) if task.started_at else None
    )
    time_to_complete = (
        int(task.completed_at) - int(task.started_at or task.created_at)
        if task.completed_at else None
    )
    return {
        "created_age_seconds": age_since_created,
        "started_age_seconds": age_since_started,
        "time_to_complete_seconds": time_to_complete,
    }


# ---------------------------------------------------------------------------
# Notification subscriptions (used by the gateway kanban-notifier)
# ---------------------------------------------------------------------------

def add_notify_sub(
    conn: Any,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> None:
    """Register a gateway source that wants terminal-state notifications."""
    board = _as_board(conn)
    now = int(time.time())
    with write_txn(board):
        st = _state(board)
        key = (task_id, platform, chat_id, thread_id or "")
        if not any(
            (s.get("task_id"), s.get("platform"), s.get("chat_id"), s.get("thread_id")) == key
            for s in st["notify_subs"]
        ):
            st["notify_subs"].append(
                {
                    "task_id": task_id,
                    "platform": platform,
                    "chat_id": chat_id,
                    "thread_id": thread_id or "",
                    "user_id": user_id,
                    "created_at": now,
                    "last_event_id": 0,
                }
            )
            board.mark_dirty()


def list_notify_subs(conn: Any, task_id: Optional[str] = None) -> list[dict]:
    board = _as_board(conn)
    with _board_scope(board):
        rows = _state(board)["notify_subs"]
        if task_id is not None:
            rows = [s for s in rows if s.get("task_id") == task_id]
        return [dict(s) for s in rows]


def remove_notify_sub(
    conn: Any,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
) -> bool:
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        before = len(st["notify_subs"])
        st["notify_subs"] = [
            s for s in st["notify_subs"]
            if not (
                s.get("task_id") == task_id
                and s.get("platform") == platform
                and s.get("chat_id") == chat_id
                and s.get("thread_id") == (thread_id or "")
            )
        ]
        removed = before - len(st["notify_subs"])
        if removed:
            board.mark_dirty()
        return removed > 0


def unseen_events_for_sub(
    conn: Any,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    kinds: Optional[Iterable[str]] = None,
) -> tuple[int, list[Event]]:
    """Return ``(new_cursor, events)`` for a given subscription."""
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        sub = None
        for s in st["notify_subs"]:
            if (
                s.get("task_id") == task_id
                and s.get("platform") == platform
                and s.get("chat_id") == chat_id
                and s.get("thread_id") == (thread_id or "")
            ):
                sub = s
                break
        if sub is None:
            return 0, []
        cursor = int(sub.get("last_event_id") or 0)
        kind_list = list(kinds) if kinds else None
        rows = [
            e for e in st["events"]
            if e.get("task_id") == task_id and int(e.get("id", 0)) > cursor
            and (kind_list is None or e.get("kind") in kind_list)
        ]
        rows.sort(key=lambda e: int(e.get("id", 0)))
        out: list[Event] = []
        max_id = cursor
        for r in rows:
            out.append(_event_from_row(r))
            max_id = max(max_id, int(r["id"]))
        return max_id, out


def advance_notify_cursor(
    conn: Any,
    *,
    task_id: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str] = None,
    new_cursor: int,
) -> None:
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        for s in st["notify_subs"]:
            if (
                s.get("task_id") == task_id
                and s.get("platform") == platform
                and s.get("chat_id") == chat_id
                and s.get("thread_id") == (thread_id or "")
            ):
                s["last_event_id"] = int(new_cursor)
                board.mark_dirty()


# ---------------------------------------------------------------------------
# Retention + garbage collection
# ---------------------------------------------------------------------------

def gc_events(conn: Any, *, older_than_seconds: int = 30 * 24 * 3600) -> int:
    """Delete old events for tasks in a terminal state (``done``/``archived``)."""
    board = _as_board(conn)
    cutoff = int(time.time()) - int(older_than_seconds)
    with write_txn(board):
        st = _state(board)
        terminal = {
            tid for tid, r in st["tasks"].items()
            if r.get("status") in ("done", "archived")
        }
        before = len(st["events"])
        st["events"] = [
            e for e in st["events"]
            if not (e.get("created_at", 0) < cutoff and e.get("task_id") in terminal)
        ]
        removed = before - len(st["events"])
        if removed:
            board.mark_dirty()
        return removed


def gc_worker_logs(
    *, older_than_seconds: int = 30 * 24 * 3600,
    board: Optional[str] = None,
) -> int:
    """Delete worker log files older than ``older_than_seconds``."""
    log_dir = worker_logs_dir(board=board)
    if not log_dir.exists():
        return 0
    cutoff = time.time() - older_than_seconds
    removed = 0
    for p in log_dir.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


# ---------------------------------------------------------------------------
# Worker log accessor
# ---------------------------------------------------------------------------

def worker_log_path(task_id: str, *, board: Optional[str] = None) -> Path:
    """Return the path to a worker's log file. The file may not exist."""
    return worker_logs_dir(board=board) / f"{task_id}.log"


def read_worker_log(
    task_id: str, *, tail_bytes: Optional[int] = None,
    board: Optional[str] = None,
) -> Optional[str]:
    """Read the worker log for ``task_id``. Returns None if missing."""
    path = worker_log_path(task_id, board=board)
    if not path.exists():
        return None
    try:
        if tail_bytes is None:
            return path.read_text(encoding="utf-8", errors="replace")
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                probe = f.tell()
                partial = f.readline()
                if not partial.endswith(b"\n") and f.tell() >= size:
                    f.seek(probe)
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Assignee enumeration
# ---------------------------------------------------------------------------

def list_profiles_on_disk() -> list[str]:
    """Return the set of assignee/profile names discovered on disk."""
    try:
        from hermes_constants import get_default_hermes_root

        default_root = get_default_hermes_root()
        profiles_dir = default_root / "profiles"
    except Exception:
        return []
    names: set[str] = set()
    if default_root.exists():
        names.add("default")
    if profiles_dir.is_dir():
        try:
            for entry in sorted(profiles_dir.iterdir()):
                if not entry.is_dir():
                    continue
                if (entry / "config.yaml").is_file():
                    names.add(entry.name)
        except OSError:
            pass
    return sorted(names)


def known_assignees(conn: Any) -> list[dict]:
    """Return every assignee name known to the board or on disk."""
    on_disk = set(list_profiles_on_disk())
    board = _as_board(conn)
    with _board_scope(board):
        counts: dict[str, dict[str, int]] = {}
        for row in _state(board)["tasks"].values():
            if row.get("status") != "archived" and row.get("assignee") is not None:
                counts.setdefault(row["assignee"], {})
                counts[row["assignee"]][row["status"]] = (
                    counts[row["assignee"]].get(row["status"], 0) + 1
                )
    names = sorted(on_disk | set(counts.keys()))
    return [
        {"name": name, "on_disk": name in on_disk, "counts": counts.get(name, {})}
        for name in names
    ]


# ---------------------------------------------------------------------------
# Runs (attempt history on a task)
# ---------------------------------------------------------------------------

def list_runs(conn: Any, task_id: str, *, include_active: bool = True) -> list[Run]:
    """Return all runs for ``task_id`` in start order."""
    board = _as_board(conn)
    with _board_scope(board):
        rows = [r for r in _state(board)["runs"].values() if r.get("task_id") == task_id]
        if not include_active:
            rows = [r for r in rows if r.get("ended_at") is not None]
        rows.sort(key=lambda r: (r.get("started_at", 0), r["id"]))
        return [Run.from_dict(r) for r in rows]


def get_run(conn: Any, run_id: int) -> Optional[Run]:
    board = _as_board(conn)
    with _board_scope(board):
        row = _state(board)["runs"].get(str(int(run_id)))
        return Run.from_dict(row) if row else None


def active_run(conn: Any, task_id: str) -> Optional[Run]:
    """Return the currently-open run for ``task_id`` (``ended_at IS NULL``)."""
    board = _as_board(conn)
    with _board_scope(board):
        rows = [
            r for r in _state(board)["runs"].values()
            if r.get("task_id") == task_id and r.get("ended_at") is None
        ]
        rows.sort(key=lambda r: r.get("started_at", 0), reverse=True)
        return Run.from_dict(rows[0]) if rows else None


def latest_run(conn: Any, task_id: str) -> Optional[Run]:
    """Return the most recent run regardless of outcome."""
    board = _as_board(conn)
    with _board_scope(board):
        rows = [r for r in _state(board)["runs"].values() if r.get("task_id") == task_id]
        rows.sort(key=lambda r: (r.get("started_at", 0), r["id"]), reverse=True)
        return Run.from_dict(rows[0]) if rows else None


def latest_summary(conn: Any, task_id: str) -> Optional[str]:
    """Return the latest non-null ``task_runs.summary`` for ``task_id``."""
    board = _as_board(conn)
    with _board_scope(board):
        rows = [
            r for r in _state(board)["runs"].values()
            if r.get("task_id") == task_id and r.get("summary")
        ]
        rows.sort(
            key=lambda r: ((r.get("ended_at") or r.get("started_at") or 0), r["id"]),
            reverse=True,
        )
        return rows[0]["summary"] if rows else None


def latest_summaries(conn: Any, task_ids: Iterable[str]) -> dict[str, str]:
    """Batch-fetch latest non-null summaries for a list of task ids."""
    ids = list(task_ids)
    if not ids:
        return {}
    board = _as_board(conn)
    with _board_scope(board):
        out: dict[str, str] = {}
        for tid in ids:
            rows = [
                r for r in _state(board)["runs"].values()
                if r.get("task_id") == tid and r.get("summary")
            ]
            if not rows:
                continue
            rows.sort(
                key=lambda r: ((r.get("ended_at") or r.get("started_at") or 0), r["id"]),
                reverse=True,
            )
            out[tid] = rows[0]["summary"]
        return out


# ---------------------------------------------------------------------------
# Dashboard / CLI aggregate helpers (replacements for direct SQL aggregates)
# ---------------------------------------------------------------------------

def append_event(
    conn: Any,
    task_id: str,
    kind: str,
    payload: Optional[dict] = None,
    *,
    run_id: Optional[int] = None,
) -> None:
    """Public wrapper for :func:`_append_event` (dashboard direct writes)."""
    _append_event(conn, task_id, kind, payload, run_id=run_id)


def max_event_id(conn: Any) -> int:
    """Return the largest event id on the board (0 when empty)."""
    board = _as_board(conn)
    with _board_scope(board):
        ids = [int(e.get("id", 0)) for e in _state(board)["events"]]
        return max(ids) if ids else 0


def set_task_priority(conn: Any, task_id: str, priority: int) -> bool:
    """Set a task's priority and emit a ``reprioritized`` event."""
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None:
            return False
        row["priority"] = int(priority)
        _append_event(board, task_id, "reprioritized", {"priority": int(priority)})
        board.mark_dirty()
        return True


def edit_task_text(
    conn: Any,
    task_id: str,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
) -> bool:
    """Update a task's title/body and emit an ``edited`` event (payload NULL)."""
    board = _as_board(conn)
    with write_txn(board):
        st = _state(board)
        row = _task_row(st, task_id)
        if row is None:
            return False
        if title is not None:
            row["title"] = title
        if body is not None:
            row["body"] = body
        _append_event(board, task_id, "edited", None)
        board.mark_dirty()
        return True


def link_counts(conn: Any) -> dict[str, dict[str, int]]:
    """Return ``{task_id: {parents, children}}`` link counts for the board."""
    board = _as_board(conn)
    with _board_scope(board):
        out: dict[str, dict[str, int]] = {}
        for link in _state(board)["links"]:
            out.setdefault(link["parent_id"], {"parents": 0, "children": 0})["children"] += 1
            out.setdefault(link["child_id"], {"parents": 0, "children": 0})["parents"] += 1
        return out


def comment_counts(conn: Any) -> dict[str, int]:
    """Return ``{task_id: comment_count}`` for the board."""
    board = _as_board(conn)
    with _board_scope(board):
        out: dict[str, int] = {}
        for c in _state(board)["comments"]:
            out[c["task_id"]] = out.get(c["task_id"], 0) + 1
        return out


def child_progress(conn: Any) -> dict[str, dict[str, int]]:
    """Return ``{parent_id: {done, total}}`` over child task statuses."""
    board = _as_board(conn)
    with _board_scope(board):
        st = _state(board)
        out: dict[str, dict[str, int]] = {}
        for link in st["links"]:
            child = st["tasks"].get(link["child_id"])
            p = out.setdefault(link["parent_id"], {"done": 0, "total": 0})
            p["total"] += 1
            if child is not None and child.get("status") == "done":
                p["done"] += 1
        return out
