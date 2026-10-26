"""Drive transport backed by an already-authenticated ``gws`` CLI.

Some hosts (notably the Muse VM) hold Google Workspace access inside a
connector CLI rather than in a Hermes-visible OAuth token or ADC file.
This transport shells out to that CLI (``hatch_gws_cli`` by default) so the
credential never leaves the connector: no token is read, stored, or logged
by Hermes code. Every operation is one authenticated CLI call.

Behavioural notes (measured against the real CLI):

- ``files get`` with ``alt=media`` prints JSON files to stdout with an
  injected ``retrieved_at`` envelope key and ignores ``--output``; other
  files download byte-exactly through ``--output``. ``download`` handles
  both and strips envelope keys before returning JSON bytes.
- Write integrity is verified with the Drive ``md5Checksum`` returned by
  ``files get`` metadata, because the CLI's JSON re-serialisation makes
  byte-compare verification of downloads unreliable for ``.json`` files.
  Callers should therefore build :class:`DriveVault` with
  ``verify_writes=False`` when using this transport; uploads/updates here
  raise :class:`DriveConflict` on md5 mismatch instead.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .drive_vault import (DriveAuthError, DriveConflict, DriveNotFound,
                          DriveUnavailable, FileMeta)

DEFAULT_CLI = "/opt/hatch/bin/hatch_gws_cli"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_FIELDS = "id,name,mimeType,md5Checksum,version,modifiedTime,size,trashed"
_ENVELOPE_KEYS = ("retrieved_at", "drive_resource_modified_at")


def _meta(item: dict) -> FileMeta:
    return FileMeta(
        id=str(item.get("id", "")),
        name=str(item.get("name", "")),
        mime_type=str(item.get("mimeType", "")),
        md5=str(item.get("md5Checksum", "") or ""),
        revision=str(item.get("version", "") or ""),
        modified_time=str(item.get("modifiedTime", "") or ""),
        size=int(item.get("size", 0) or 0),
        trashed=bool(item.get("trashed", False)),
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class CliDriveTransport:
    """DriveTransport implementation over an authenticated ``gws`` CLI."""

    def __init__(self, cli_path: Optional[str] = None,
                 runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
                 timeout: float = 180.0):
        self._cli = cli_path or os.environ.get("HERMES_GWS_CLI") or DEFAULT_CLI
        self._run = runner or subprocess.run
        self._timeout = timeout

    # -- plumbing ---------------------------------------------------------

    def _call(self, args: list[str]) -> str:
        try:
            proc = self._run([self._cli, *args], capture_output=True,
                             timeout=self._timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DriveUnavailable(f"drive CLI call failed: {type(exc).__name__}") from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", "replace")
            lowered = stderr.lower()
            if "notfound" in lowered or "404" in lowered or "not found" in lowered:
                raise DriveNotFound("drive item not found")
            if "401" in lowered or "403" in lowered or "unauth" in lowered:
                raise DriveAuthError("drive CLI authentication was rejected")
            raise DriveUnavailable("drive CLI call returned an error")
        return (proc.stdout or b"").decode("utf-8", "replace")

    def _call_json(self, args: list[str]) -> dict:
        out = self._call(args)
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise DriveUnavailable("drive CLI returned non-JSON output") from exc
        if not isinstance(data, dict):
            raise DriveUnavailable("drive CLI returned an unexpected payload")
        return data

    def _list(self, query: str) -> list[FileMeta]:
        params = json.dumps({"q": query,
                             "fields": f"files({_FIELDS})",
                             "pageSize": 1000})
        data = self._call_json(["drive", "files", "list",
                                "--params", params, "--page-all"])
        files = data.get("files")
        if not isinstance(files, list):
            raise DriveUnavailable("drive list returned no files array")
        return [_meta(item) for item in files if isinstance(item, dict)]

    # -- DriveTransport protocol ------------------------------------------

    def find_by_name(self, name: str, parent_id: Optional[str]) -> list[FileMeta]:
        query = f"name = '{_escape(name)}' and trashed = false"
        if parent_id:
            query += f" and '{_escape(parent_id)}' in parents"
        return self._list(query)

    def list_children(self, folder_id: str) -> list[FileMeta]:
        return self._list(f"'{_escape(folder_id)}' in parents and trashed = false")

    def create_folder(self, name: str, parent_id: str) -> FileMeta:
        body = json.dumps({"name": name, "mimeType": _FOLDER_MIME,
                           "parents": [parent_id]})
        data = self._call_json(["drive", "files", "create", "--json", body])
        file_id = str(data.get("id", ""))
        if not file_id:
            raise DriveUnavailable("folder creation returned no id")
        return self.get_meta(file_id)

    def get_meta(self, file_id: str) -> FileMeta:
        params = json.dumps({"fileId": file_id, "fields": _FIELDS})
        data = self._call_json(["drive", "files", "get", "--params", params])
        if str(data.get("id", "")) != file_id:
            raise DriveNotFound(f"drive item {file_id} not found")
        return _meta(data)

    def download(self, file_id: str) -> bytes:
        meta = self.get_meta(file_id)
        params = json.dumps({"fileId": file_id, "alt": "media"})
        if meta.mime_type == "application/json" or meta.name.endswith(".json"):
            data = self._call_json(["drive", "files", "get", "--params", params])
            for key in _ENVELOPE_KEYS:
                data.pop(key, None)
            return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="hermes-drive-dl-") as tmp:
            target = Path(tmp) / "payload"
            self._call(["drive", "files", "get", "--params", params,
                        "--output", str(target)])
            if not target.exists():
                raise DriveUnavailable("drive download produced no file")
            return target.read_bytes()

    def _stage(self, name: str, data: bytes) -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory(prefix="hermes-drive-ul-")
        (Path(tmp.name) / name).write_bytes(data)
        return tmp

    def _verify_md5(self, file_id: str, data: bytes) -> FileMeta:
        meta = self.get_meta(file_id)
        if meta.md5 and meta.md5 != hashlib.md5(data).hexdigest():
            raise DriveConflict("write verification failed (md5 mismatch after write)")
        return meta

    def upload_new(self, name: str, parent_id: str, data: bytes,
                   mime_type: str) -> FileMeta:
        with self._stage(name, data) as tmp:
            staged = str(Path(tmp) / name)
            out = self._call(["drive", "+upload", staged, "--parent", parent_id])
        try:
            file_id = str(json.loads(out).get("id", ""))
        except json.JSONDecodeError:
            file_id = ""
        if not file_id:
            matches = [m for m in self.find_by_name(name, parent_id)]
            if not matches:
                raise DriveUnavailable("upload returned no file id")
            file_id = matches[0].id
        return self._verify_md5(file_id, data)

    def update(self, file_id: str, data: bytes, mime_type: str) -> FileMeta:
        meta = self.get_meta(file_id)
        with self._stage(meta.name or "payload", data) as tmp:
            staged = str(Path(tmp) / (meta.name or "payload"))
            params = json.dumps({"fileId": file_id})
            self._call(["drive", "files", "update", "--params", params,
                        "--upload", staged])
        return self._verify_md5(file_id, data)

    def trash(self, file_id: str) -> None:
        params = json.dumps({"fileId": file_id})
        self._call(["drive", "files", "update", "--params", params,
                    "--json", json.dumps({"trashed": True})])


def cli_vault(root_id: Optional[str] = None) -> DriveVault:
    """Build a DriveVault over the connector CLI transport.

    The Vault root is pinned by ``HERMES_DRIVE_VAULT_ROOT_ID`` when
    ``root_id`` is not given. Write verification is md5-based inside the
    transport, so the vault-level byte verification is disabled.
    """
    from .drive_vault import DriveVault

    pinned = root_id or os.environ.get("HERMES_DRIVE_VAULT_ROOT_ID") or None
    return DriveVault(CliDriveTransport(), root_id=pinned, verify_writes=False)
