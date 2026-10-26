"""Contract tests for connector-backed Google Drive pagination (no credentials)."""
import json
import subprocess

from hermes_hybrid.cli_drive_transport import CliDriveTransport


def fake_runner(result):
    def run(command, **kwargs):
        assert command[0] == "mock-hatch"
        assert kwargs["capture_output"] is True
        return subprocess.CompletedProcess(command, 0, result.encode(), b"")
    return run


def test_pagination_accepts_single_response_page():
    payload = {"files": [{"id": "id1", "name": "note.md", "mimeType": "text/plain"}]}
    drive = CliDriveTransport(cli_path="mock-hatch", runner=fake_runner(json.dumps(payload)))
    files = drive.list_children("folder-id")
    assert [(x.id, x.name) for x in files] == [("id1", "note.md")]


def test_pagination_accepts_multi_page_ndjson():
    # GWS --page-all responds with one JSON object per page (not an array).
    output = "\n".join([
        json.dumps({"files": [{"id": "a", "name": "a.md"}],
                    "nextPageToken": "page2"}),
        json.dumps({"files": [{"id": "b", "name": "b.md"}]}),
    ]) + "\n"
    drive = CliDriveTransport(cli_path="mock-hatch", runner=fake_runner(output))
    files = drive.list_children("folder-id")
    assert [(x.id, x.name) for x in files] == [("a", "a.md"), ("b", "b.md")]


def test_pagination_rejects_invalid_page():
    output = json.dumps({"files": []}) + "\n" + '{"unexpected":true}\n'
    drive = CliDriveTransport(cli_path="mock-hatch", runner=fake_runner(output))
    from hermes_hybrid.drive_vault import DriveUnavailable
    import pytest
    with pytest.raises(DriveUnavailable):
        drive.list_children("folder-id")


def test_json_download_strips_only_connector_envelope():
    file_id = "json-id"
    def run(command, **kwargs):
        if "--params" in command:
            params = json.loads(command[command.index("--params") + 1])
        else:
            params = {}
        if params.get("alt") == "media":
            data = {"job_id": "sample", "state": "done",
                    "retrieved_at": "connector-diagnostic"}
        else:
            data = {"id": file_id, "name": "status.json",
                    "mimeType": "application/json", "version": "1"}
        return subprocess.CompletedProcess(command, 0, json.dumps(data).encode(), b"")
    drive = CliDriveTransport(cli_path="mock-hatch", runner=run)
    payload = json.loads(drive.download(file_id))
    assert payload == {"job_id": "sample", "state": "done"}
