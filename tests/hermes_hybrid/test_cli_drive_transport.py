"""CliDriveTransport behaviour against a fake ``gws`` CLI runner.

No real CLI, network, or credential is involved: the runner is an
in-memory stand-in that mimics the measured quirks of the real CLI
(JSON downloads are printed to stdout with an injected ``retrieved_at``
envelope key; binary downloads honour ``--output``).
"""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from hermes_hybrid.cli_drive_transport import CliDriveTransport
from hermes_hybrid.drive_vault import DriveConflict, DriveVault


def _cp(stdout: bytes, returncode: int = 0, stderr: bytes = b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class FakeCli:
    def __init__(self):
        self.files = {}  # id -> dict(name, mime, data, parents)
        self.seq = 0

    def _meta(self, fid):
        f = self.files[fid]
        return {"id": fid, "name": f["name"], "mimeType": f["mime"],
                "md5Checksum": hashlib.md5(f["data"]).hexdigest(),
                "version": str(f.get("version", 1)),
                "modifiedTime": "2026-10-26T00:00:00Z",
                "size": len(f["data"]), "trashed": False}

    def __call__(self, args, capture_output=True, timeout=None, check=False):
        assert args[0].endswith("hatch_gws_cli") or "/" in args[0]
        cmd = args[1:]
        if cmd[:3] == ["drive", "files", "list"]:
            params = json.loads(cmd[cmd.index("--params") + 1])
            q = params["q"]
            out = []
            for fid, f in self.files.items():
                if "name = '" in q:
                    want = q.split("name = '", 1)[1].split("'", 1)[0]
                    if f["name"] != want:
                        continue
                if "in parents" in q:
                    parent = q.rsplit("'", 2)[-2] if q.count("'") >= 2 else None
                    parent = q.split("'", 2)[1] if "'" in q else None
                    if parent not in f["parents"] and f["name"] not in q:
                        # crude parent match for tests
                        if parent and parent not in f["parents"]:
                            continue
                out.append(self._meta(fid))
            return _cp(json.dumps({"files": out}).encode())
        if cmd[:3] == ["drive", "files", "get"]:
            params = json.loads(cmd[cmd.index("--params") + 1])
            fid = params["fileId"]
            if fid not in self.files:
                return _cp(b"", 1, b"Error: notFound 404")
            if params.get("alt") == "media":
                f = self.files[fid]
                if f["mime"] == "application/json":
                    payload = json.loads(f["data"])
                    payload["retrieved_at"] = {"utc": "now"}  # envelope quirk
                    return _cp(json.dumps(payload, indent=2).encode())
                if "--output" in cmd:
                    Path(cmd[cmd.index("--output") + 1]).write_bytes(f["data"])
                return _cp(b"")
            return _cp(json.dumps(self._meta(fid)).encode())
        if cmd[:3] == ["drive", "files", "create"]:
            body = json.loads(cmd[cmd.index("--json") + 1])
            self.seq += 1
            fid = f"fake-{self.seq}"
            self.files[fid] = {"name": body["name"], "mime": body["mimeType"],
                               "data": b"", "parents": body.get("parents", [])}
            return _cp(json.dumps({"id": fid}).encode())
        if cmd[:2] == ["drive", "+upload"]:
            path = Path(cmd[2])
            parent = cmd[cmd.index("--parent") + 1]
            self.seq += 1
            fid = f"fake-{self.seq}"
            mime = "application/json" if path.name.endswith(".json") else "text/plain"
            self.files[fid] = {"name": path.name, "mime": mime,
                               "data": path.read_bytes(), "parents": [parent]}
            return _cp(json.dumps({"id": fid}).encode())
        if cmd[:3] == ["drive", "files", "update"]:
            params = json.loads(cmd[cmd.index("--params") + 1])
            fid = params["fileId"]
            if "--upload" in cmd:
                data = Path(cmd[cmd.index("--upload") + 1]).read_bytes()
                self.files[fid]["data"] = data
                self.files[fid]["version"] = int(self.files[fid].get("version", 1)) + 1
            if "--json" in cmd:
                body = json.loads(cmd[cmd.index("--json") + 1])
                if body.get("trashed"):
                    self.files[fid]["trashed"] = True
            return _cp(json.dumps({"id": fid}).encode())
        raise AssertionError(f"unexpected CLI args: {cmd}")


@pytest.fixture()
def transport():
    cli = FakeCli()
    return CliDriveTransport(cli_path="/fake/hatch_gws_cli", runner=cli), cli


def test_json_download_strips_envelope(transport):
    t, cli = transport
    cli.files["j1"] = {"name": "status.json", "mime": "application/json",
                       "data": b'{"job_id": "j", "state": "queued"}\n',
                       "parents": ["root"]}
    data = json.loads(t.download("j1"))
    assert data == {"job_id": "j", "state": "queued"}
    assert "retrieved_at" not in data


def test_binary_download_uses_output_file(transport):
    t, cli = transport
    cli.files["b1"] = {"name": "note.md", "mime": "text/markdown",
                       "data": b"# hello\n", "parents": ["root"]}
    assert t.download("b1") == b"# hello\n"


def test_upload_and_update_verify_md5(transport):
    t, cli = transport
    meta = t.upload_new("a.json", "root", b'{"x": 1}\n', "application/json")
    assert cli.files[meta.id]["data"] == b'{"x": 1}\n'
    meta2 = t.update(meta.id, b'{"x": 2}\n', "application/json")
    assert meta2.revision != meta.revision
    assert cli.files[meta.id]["data"] == b'{"x": 2}\n'


def test_vault_roundtrip_over_cli_transport(transport):
    t, cli = transport
    cli.files["root"] = {"name": "ChatGPT-Obsidian-Vault",
                         "mime": "application/vnd.google-apps.folder",
                         "data": b"", "parents": []}
    vault = DriveVault(t, root_id="root", verify_writes=False)
    vault.write_json("06_Automation/Hermes/ColabJobs/job-t/status.json",
                     {"job_id": "job-t", "state": "queued"})
    got = vault.read_json("06_Automation/Hermes/ColabJobs/job-t/status.json")
    assert got == {"job_id": "job-t", "state": "queued"}


def test_md5_mismatch_raises_conflict(transport):
    t, cli = transport
    real_call = t._call

    def corrupting_update(args):
        out = real_call(args)
        if args[:3] == ["drive", "files", "update"] and "--upload" in args:
            params = json.loads(args[args.index("--params") + 1])
            cli.files[params["fileId"]]["data"] = b"corrupted"
        return out

    cli.files["u1"] = {"name": "x.json", "mime": "application/json",
                       "data": b"{}\n", "parents": ["root"]}
    t._call = corrupting_update
    with pytest.raises(DriveConflict):
        t.update("u1", b'{"y": 3}\n', "application/json")
