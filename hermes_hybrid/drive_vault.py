"""Google Drive-backed Obsidian Vault storage (Drive is canonical).

Design principle (2026-10-26 integration order): the existing Google
Drive folder ``ChatGPT-Obsidian-Vault`` is the ONE canonical persistent
store for Hermes. A local Vault directory is at most a cache or a
verified mount view of that folder -- never a second canonical store,
and this module never creates, moves, or renames the Vault root.

Layers:

- :class:`DriveTransport` -- the minimal Drive API surface this module
  needs. :class:`HttpDriveTransport` implements it against Drive API v3
  with an externally supplied OAuth access token (existing OAuth is
  reused; this module never runs an OAuth flow, never stores a token,
  and never logs one). :class:`FakeDriveTransport` is an in-memory
  implementation for tests and dry runs.
- :class:`DriveVault` -- path-addressed reads/writes under the Vault
  root with optimistic-concurrency conflict detection (revision
  check before write), bounded retry on transient failures, and
  write verification (re-read + hash compare) so a failed or raced
  write is reported instead of silently losing data.
- :class:`DriveVaultDir` -- a Drive-backed counterpart of
  :class:`hermes_hybrid.vault_kv.VaultDir` (one JSON record per file)
  for the migrated subsystems (Kanban exports, worker logs, Discord
  thread state, memory stores) that were previously local-only.

No SQLite is used anywhere in this module.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

VAULT_ROOT_NAME = "ChatGPT-Obsidian-Vault"
HERMES_TREE = ("06_Automation", "Hermes")
_FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveError(Exception):
    """Base class for Drive Vault failures."""


class DriveNotFound(DriveError):
    """The requested file or folder does not exist."""


class DriveConflict(DriveError):
    """A write was refused because the remote copy changed underneath us."""


class DriveUnavailable(DriveError):
    """Transient failure (rate limit / 5xx / network); retryable."""


class DriveAuthError(DriveError):
    """The supplied credential was rejected; not retryable here."""


@dataclass
class FileMeta:
    id: str
    name: str
    mime_type: str = ""
    md5: str = ""
    revision: str = ""
    modified_time: str = ""
    size: int = 0
    trashed: bool = False


class DriveTransport(Protocol):
    def find_by_name(self, name: str, parent_id: Optional[str]) -> list[FileMeta]: ...
    def list_children(self, folder_id: str) -> list[FileMeta]: ...
    def create_folder(self, name: str, parent_id: str) -> FileMeta: ...
    def get_meta(self, file_id: str) -> FileMeta: ...
    def download(self, file_id: str) -> bytes: ...
    def upload_new(self, name: str, parent_id: str, data: bytes, mime_type: str) -> FileMeta: ...
    def update(self, file_id: str, data: bytes, mime_type: str) -> FileMeta: ...
    def trash(self, file_id: str) -> None: ...


# ---------------------------------------------------------------------------
# HTTP transport (Drive API v3, stdlib only)
# ---------------------------------------------------------------------------

class HttpDriveTransport:
    """Drive API v3 transport.

    ``token_provider`` is called for every request and must return a
    valid OAuth access token. How the token was obtained is the
    caller's business (existing OAuth); the token is never stored on
    this object beyond the duration of one request and never logged.
    """

    _API = "https://www.googleapis.com/drive/v3"
    _UPLOAD = "https://www.googleapis.com/upload/drive/v3"
    _FIELDS = "id,name,mimeType,md5Checksum,headRevisionId,modifiedTime,size,trashed"

    def __init__(self, token_provider: Callable[[], str], timeout: float = 60.0):
        self._token_provider = token_provider
        self._timeout = timeout

    # -- plumbing ------------------------------------------------------
    def _request(self, method: str, url: str, data: bytes | None = None,
                 content_type: str | None = None) -> bytes:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self._token_provider()}")
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise DriveNotFound("drive object not found") from e
            if e.code in (409, 412):
                raise DriveConflict(f"drive conflict (http {e.code})") from e
            if e.code in (401, 403):
                raise DriveAuthError(f"drive auth rejected (http {e.code})") from e
            if e.code == 429 or e.code >= 500:
                raise DriveUnavailable(f"drive transient failure (http {e.code})") from e
            raise DriveError(f"drive request failed (http {e.code})") from e
        except urllib.error.URLError as e:
            raise DriveUnavailable(f"drive unreachable: {e.reason}") from e

    def _json(self, method: str, url: str, payload: dict | None = None) -> dict:
        data = None
        ctype = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            ctype = "application/json"
        raw = self._request(method, url, data=data, content_type=ctype)
        return json.loads(raw.decode("utf-8")) if raw else {}

    @classmethod
    def _meta(cls, item: dict) -> FileMeta:
        return FileMeta(
            id=item["id"], name=item.get("name", ""),
            mime_type=item.get("mimeType", ""),
            md5=item.get("md5Checksum", ""),
            revision=item.get("headRevisionId", ""),
            modified_time=item.get("modifiedTime", ""),
            size=int(item.get("size", 0) or 0),
            trashed=bool(item.get("trashed", False)),
        )

    # -- transport API -------------------------------------------------
    def _list_files(self, query: str) -> list[FileMeta]:
        """Read every Drive API page; silently truncating a Vault listing loses records."""
        files: list[FileMeta] = []
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        while True:
            params = {
                "q": query,
                "fields": f"nextPageToken,files({self._FIELDS})",
                "pageSize": "1000",
            }
            if page_token:
                params["pageToken"] = page_token
            url = f"{self._API}/files?{urllib.parse.urlencode(params)}"
            response = self._json("GET", url)
            files.extend(self._meta(item) for item in response.get("files", []))
            next_token = response.get("nextPageToken")
            if not next_token:
                return files
            if next_token in seen_tokens:
                raise DriveError("Drive API returned a repeated pagination token")
            seen_tokens.add(next_token)
            page_token = next_token

    def find_by_name(self, name: str, parent_id: Optional[str]) -> list[FileMeta]:
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        q = f"name = '{escaped}' and trashed = false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        return self._list_files(q)

    def list_children(self, folder_id: str) -> list[FileMeta]:
        return self._list_files(f"'{folder_id}' in parents and trashed = false")

    def create_folder(self, name: str, parent_id: str) -> FileMeta:
        out = self._json("POST", f"{self._API}/files?fields={self._FIELDS}", {
            "name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id],
        })
        return self._meta(out)

    def get_meta(self, file_id: str) -> FileMeta:
        out = self._json("GET", f"{self._API}/files/{file_id}?fields={self._FIELDS}")
        return self._meta(out)

    def download(self, file_id: str) -> bytes:
        return self._request("GET", f"{self._API}/files/{file_id}?alt=media")

    def upload_new(self, name: str, parent_id: str, data: bytes, mime_type: str) -> FileMeta:
        boundary = "hermes-drive-vault"
        head = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            + json.dumps({"name": name, "parents": [parent_id]})
            + f"\r\n--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        body = head + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        url = f"{self._UPLOAD}/files?uploadType=multipart&fields={self._FIELDS}"
        raw = self._request("POST", url, data=body,
                            content_type=f"multipart/related; boundary={boundary}")
        return self._meta(json.loads(raw.decode("utf-8")))

    def update(self, file_id: str, data: bytes, mime_type: str) -> FileMeta:
        url = f"{self._UPLOAD}/files/{file_id}?uploadType=media&fields={self._FIELDS}"
        raw = self._request("PATCH", url, data=data, content_type=mime_type)
        return self._meta(json.loads(raw.decode("utf-8")))

    def trash(self, file_id: str) -> None:
        self._json("PATCH", f"{self._API}/files/{file_id}", {"trashed": True})


# ---------------------------------------------------------------------------
# In-memory transport (tests / dry runs)
# ---------------------------------------------------------------------------

class FakeDriveTransport:
    """In-memory DriveTransport with failure injection for tests."""

    def __init__(self):
        self._items: dict[str, dict[str, Any]] = {}
        self._counter = 0
        self._fail_next = 0
        self._fail_exc: type[Exception] = DriveUnavailable

    # test controls ----------------------------------------------------
    def fail_next(self, count: int = 1, exc: type[Exception] = DriveUnavailable):
        self._fail_next = count
        self._fail_exc = exc

    def _maybe_fail(self):
        if self._fail_next > 0:
            self._fail_next -= 1
            raise self._fail_exc("injected failure")

    def _new_id(self) -> str:
        self._counter += 1
        return f"fake-{self._counter}"

    def _meta_of(self, item: dict) -> FileMeta:
        data: bytes = item["data"]
        return FileMeta(
            id=item["id"], name=item["name"], mime_type=item["mime"],
            md5=hashlib.md5(data).hexdigest() if data else "",
            revision=str(item["revision"]),
            modified_time=item["modified"], size=len(data),
            trashed=item["trashed"],
        )

    def _add(self, name, parent_id, mime, data=b""):
        fid = self._new_id()
        self._items[fid] = {
            "id": fid, "name": name, "parent": parent_id, "mime": mime,
            "data": data, "revision": 1, "modified": "2026-01-01T00:00:00Z",
            "trashed": False,
        }
        return self._meta_of(self._items[fid])

    def find_by_name(self, name, parent_id):
        self._maybe_fail()
        return [self._meta_of(i) for i in self._items.values()
                if i["name"] == name and i["parent"] == parent_id and not i["trashed"]]

    def list_children(self, folder_id):
        self._maybe_fail()
        return [self._meta_of(i) for i in self._items.values()
                if i["parent"] == folder_id and not i["trashed"]]

    def create_folder(self, name, parent_id):
        self._maybe_fail()
        return self._add(name, parent_id, _FOLDER_MIME)

    def get_meta(self, file_id):
        self._maybe_fail()
        item = self._items.get(file_id)
        if item is None or item["trashed"]:
            raise DriveNotFound(file_id)
        return self._meta_of(item)

    def download(self, file_id):
        self._maybe_fail()
        item = self._items.get(file_id)
        if item is None or item["trashed"]:
            raise DriveNotFound(file_id)
        return bytes(item["data"])

    def upload_new(self, name, parent_id, data, mime_type):
        self._maybe_fail()
        return self._add(name, parent_id, mime_type, bytes(data))

    def update(self, file_id, data, mime_type):
        self._maybe_fail()
        item = self._items.get(file_id)
        if item is None or item["trashed"]:
            raise DriveNotFound(file_id)
        item["data"] = bytes(data)
        item["mime"] = mime_type
        item["revision"] += 1
        return self._meta_of(item)

    def trash(self, file_id):
        self._maybe_fail()
        item = self._items.get(file_id)
        if item is None:
            raise DriveNotFound(file_id)
        item["trashed"] = True

    # convenience for tests: seed a root folder
    def seed_root(self, name: str = VAULT_ROOT_NAME) -> FileMeta:
        return self._add(name, None, _FOLDER_MIME)


# ---------------------------------------------------------------------------
# DriveVault: path-addressed canonical store
# ---------------------------------------------------------------------------

def _validate_rel(rel: str) -> tuple[str, ...]:
    if not rel or rel.strip() != rel or rel.startswith("/"):
        raise ValueError(f"invalid vault path: {rel!r}")
    # Validate the RAW segments: PurePosixPath would silently normalise
    # "a/../../b" to "b", hiding an escape attempt.
    parts = tuple(rel.split("/"))
    if any(p in ("", ".", "..") or "\\" in p or "\x00" in p for p in parts):
        raise ValueError(f"invalid vault path: {rel!r}")
    return parts


class DriveVault:
    """Path-addressed store rooted at the existing Drive Vault folder.

    The root folder is located by name (or pinned by id) and is never
    created, moved, or renamed by this class. Subfolders below the
    root are created on demand by writes.
    """

    def __init__(self, transport: DriveTransport, root_name: str = VAULT_ROOT_NAME,
                 root_id: Optional[str] = None, verify_writes: bool = True,
                 retries: int = 3, sleeper: Callable[[float], None] = time.sleep):
        self._t = transport
        self._root_name = root_name
        self._root_id = root_id
        self._verify = verify_writes
        self._retries = max(1, retries)
        self._sleep = sleeper
        self._folder_cache: dict[tuple[str, ...], str] = {}

    # -- root / folders --------------------------------------------------
    def root_id(self) -> str:
        if self._root_id:
            return self._root_id
        matches = [m for m in self._retry(lambda: self._t.find_by_name(self._root_name, None))
                   if m.mime_type == _FOLDER_MIME]
        if not matches:
            raise DriveNotFound(f"vault root folder not found: {self._root_name}")
        if len(matches) > 1:
            raise DriveError(
                f"multiple folders named {self._root_name}; pin root_id explicitly")
        self._root_id = matches[0].id
        return self._root_id

    def _retry(self, fn):
        last = None
        for attempt in range(self._retries):
            try:
                return fn()
            except DriveUnavailable as e:
                last = e
                if attempt + 1 < self._retries:
                    self._sleep(0.5 * (2 ** attempt))
        raise last if last else DriveUnavailable("retry exhausted")

    def _folder_id(self, parts: tuple[str, ...], create: bool = False) -> str:
        current = self.root_id()
        walked: tuple[str, ...] = ()
        for part in parts:
            walked = walked + (part,)
            if walked in self._folder_cache:
                current = self._folder_cache[walked]
                continue
            found = [m for m in self._retry(lambda p=part, c=current: self._t.find_by_name(p, c))
                     if m.mime_type == _FOLDER_MIME]
            if len(found) > 1:
                raise DriveConflict(f"multiple Vault folders at {'/'.join(walked)}")
            if found:
                current = found[0].id
            elif create:
                current = self._retry(lambda p=part, c=current: self._t.create_folder(p, c)).id
            else:
                raise DriveNotFound(f"folder not found: {'/'.join(walked)}")
            self._folder_cache[walked] = current
        return current

    def _find_file(self, rel: str) -> Optional[FileMeta]:
        parts = _validate_rel(rel)
        try:
            parent = self._folder_id(parts[:-1])
        except DriveNotFound:
            return None
        matches = [m for m in self._retry(lambda: self._t.find_by_name(parts[-1], parent))
                   if m.mime_type != _FOLDER_MIME]
        if len(matches) > 1:
            raise DriveConflict(f"multiple Vault files at {rel}")
        return matches[0] if matches else None

    # -- reads -----------------------------------------------------------
    def exists(self, rel: str) -> bool:
        return self._find_file(rel) is not None

    def read_bytes(self, rel: str) -> Optional[bytes]:
        meta = self._find_file(rel)
        if meta is None:
            return None
        return self._retry(lambda: self._t.download(meta.id))

    def read_with_revision(self, rel: str) -> tuple[Optional[bytes], Optional[str]]:
        meta = self._find_file(rel)
        if meta is None:
            return None, None
        data = self._retry(lambda: self._t.download(meta.id))
        fresh = self._retry(lambda: self._t.get_meta(meta.id))
        return data, fresh.revision

    def read_text(self, rel: str) -> Optional[str]:
        data = self.read_bytes(rel)
        return data.decode("utf-8") if data is not None else None

    def read_json(self, rel: str) -> Optional[dict]:
        text = self.read_text(rel)
        if text is None:
            return None
        data = json.loads(text)
        return data if isinstance(data, dict) else None

    def list_names(self, rel_dir: str) -> list[str]:
        parts = _validate_rel(rel_dir) if rel_dir else ()
        try:
            folder = self._folder_id(parts)
        except DriveNotFound:
            return []
        return sorted(m.name for m in self._retry(lambda: self._t.list_children(folder)))

    # -- writes ------------------------------------------------------------
    def write_bytes(self, rel: str, data: bytes, mime_type: Optional[str] = None,
                    expected_revision: Optional[str] = None) -> FileMeta:
        parts = _validate_rel(rel)
        mime = mime_type or mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
        parent = self._folder_id(parts[:-1], create=True)
        existing = self._find_file(rel)
        if existing is None and expected_revision is not None:
            raise DriveConflict(f"{rel}: expected revision {expected_revision}, file missing")
        if existing is not None and expected_revision is not None:
            current = self._retry(lambda: self._t.get_meta(existing.id))
            if current.revision != expected_revision:
                raise DriveConflict(
                    f"{rel}: expected revision {expected_revision}, "
                    f"found {current.revision}; refusing to overwrite")

        def _do_write() -> FileMeta:
            if existing is not None:
                return self._t.update(existing.id, data, mime)
            return self._t.upload_new(parts[-1], parent, data, mime)

        meta = self._retry(_do_write)
        if self._verify:
            back = self._retry(lambda: self._t.download(meta.id))
            if hashlib.sha256(back).digest() != hashlib.sha256(data).digest():
                raise DriveConflict(
                    f"{rel}: write verification failed (content mismatch after write)")
        return meta

    def write_text(self, rel: str, text: str, expected_revision: Optional[str] = None) -> FileMeta:
        return self.write_bytes(rel, text.encode("utf-8"), "text/plain; charset=utf-8",
                                expected_revision=expected_revision)

    def write_json(self, rel: str, data: dict, expected_revision: Optional[str] = None) -> FileMeta:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        return self.write_bytes(rel, text.encode("utf-8"), "application/json",
                                expected_revision=expected_revision)

    def trash(self, rel: str) -> bool:
        meta = self._find_file(rel)
        if meta is None:
            return False
        self._retry(lambda: self._t.trash(meta.id))
        return True


def drive_vault_from_env(transport: Optional[DriveTransport] = None) -> DriveVault:
    """Build a DriveVault from environment configuration.

    - ``HERMES_DRIVE_VAULT_ROOT_ID`` pins the Vault root folder id.
    - ``GOOGLE_DRIVE_ACCESS_TOKEN`` supplies the OAuth access token for
      the default HTTP transport (existing OAuth; never stored/logged).
    """
    import os

    if transport is None:
        def _token() -> str:
            token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
            if not token:
                raise DriveAuthError("GOOGLE_DRIVE_ACCESS_TOKEN is not set")
            return token

        transport = HttpDriveTransport(_token)
    return DriveVault(transport, root_id=os.environ.get("HERMES_DRIVE_VAULT_ROOT_ID") or None)


# ---------------------------------------------------------------------------
# DriveVaultDir: VaultDir-shaped record store on Drive
# ---------------------------------------------------------------------------

class DriveVaultDir:
    """One directory of JSON records under the Drive Vault tree.

    Mirrors :class:`hermes_hybrid.vault_kv.VaultDir` semantics
    (read/write/list) with Drive as the store. ``delete`` moves the
    record to the Drive trash (recoverable); nothing is hard-deleted.
    """

    def __init__(self, vault: DriveVault, *subpath: str):
        self._vault = vault
        self._base = "/".join(subpath)

    def _rel(self, record_id: str) -> str:
        from hermes_hybrid.vault_kv import validate_id

        return f"{self._base}/{validate_id(record_id)}.json" if self._base \
            else f"{validate_id(record_id)}.json"

    def read(self, record_id: str) -> Optional[dict]:
        try:
            return self._vault.read_json(self._rel(record_id))
        except (DriveNotFound, json.JSONDecodeError):
            return None

    def read_with_revision(self, record_id: str) -> tuple[Optional[dict], Optional[str]]:
        data, revision = self._vault.read_with_revision(self._rel(record_id))
        if data is None:
            return None, None
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, revision
        return (parsed if isinstance(parsed, dict) else None), revision

    def write(self, record_id: str, data: dict,
              expected_revision: Optional[str] = None) -> FileMeta:
        return self._vault.write_json(self._rel(record_id), data,
                                      expected_revision=expected_revision)

    def delete(self, record_id: str) -> bool:
        return self._vault.trash(self._rel(record_id))

    def list_ids(self) -> list[str]:
        return sorted(
            name[:-5] for name in self._vault.list_names(self._base)
            if name.endswith(".json")
        )

    def list_records(self) -> list[dict]:
        out = []
        for record_id in self.list_ids():
            data = self.read(record_id)
            if data is not None:
                out.append(data)
        return out
