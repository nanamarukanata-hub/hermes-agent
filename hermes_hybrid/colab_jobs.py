"""Google Colab jobs whose definitions and results live in the Drive Vault.

Job layout in the canonical Drive Vault (``ChatGPT-Obsidian-Vault``)::

    06_Automation/Hermes/ColabJobs/<job_id>/job.json        definition
    06_Automation/Hermes/ColabJobs/<job_id>/status.json     lifecycle state
    06_Automation/Hermes/ColabJobs/<job_id>/checkpoint.json progress checkpoints
    06_Automation/Hermes/ColabJobs/<job_id>/result.json     final result

Colab mounts the same Drive folder, so a notebook reads ``job.json``
from the Vault and writes ``result.json`` back to the same folder;
Hermes then reads the result through the same :class:`DriveVault`.
The job *semantics* are shared: :func:`run_job_spec` is the reference
implementation, and :func:`build_colab_notebook` generates a
self-contained notebook implementing exactly the same safe job types:

- ``echo``       -- returns the spec's message (connectivity probe)
- ``text_stats`` -- line/word/char counts + SHA-256 of a Vault text file
- ``hash_file``  -- SHA-256 + size of a Vault file

No arbitrary code execution is part of the job spec: a job can only
name these operations and Vault-relative input paths.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from hermes_hybrid.drive_vault import DriveVault, HERMES_TREE, VAULT_ROOT_NAME
from hermes_hybrid.vault_kv import validate_id

JOB_TYPES = ("echo", "text_stats", "hash_file")
_BASE = "/".join((*HERMES_TREE, "ColabJobs"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(job_id: str) -> str:
    return f"{_BASE}/{validate_id(job_id)}"


def write_job(vault: DriveVault, job_id: str, spec: dict) -> dict:
    """Validate and persist a job definition + queued status."""
    if spec.get("type") not in JOB_TYPES:
        raise ValueError(f"unsupported job type: {spec.get('type')!r}")
    if spec["type"] in ("text_stats", "hash_file") and not spec.get("input"):
        raise ValueError("job spec requires a Vault-relative 'input' path")
    job = {"job_id": validate_id(job_id), "spec": dict(spec),
           "created_at": _now(), "schema": 1}
    vault.write_json(f"{job_dir(job_id)}/job.json", job)
    vault.write_json(f"{job_dir(job_id)}/status.json",
                     {"job_id": job_id, "state": "queued", "updated_at": _now()})
    vault.write_json(f"{job_dir(job_id)}/checkpoint.json",
                     {"job_id": job_id, "stage": "queued", "updated_at": _now()})
    return job


def read_job(vault: DriveVault, job_id: str) -> Optional[dict]:
    return vault.read_json(f"{job_dir(job_id)}/job.json")


def read_status(vault: DriveVault, job_id: str) -> Optional[dict]:
    return vault.read_json(f"{job_dir(job_id)}/status.json")


def read_result(vault: DriveVault, job_id: str) -> Optional[dict]:
    return vault.read_json(f"{job_dir(job_id)}/result.json")


def run_job_spec(read_text, read_bytes, spec: dict) -> dict:
    """Reference job implementation.

    ``read_text``/``read_bytes`` resolve a Vault-relative path, so the
    same function runs against DriveVault, a mounted Vault, or a fake.
    """
    jtype = spec.get("type")
    if jtype == "echo":
        return {"type": jtype, "message": spec.get("message", "")}
    if jtype in ("text_stats", "hash_file"):
        data = read_bytes(spec["input"])
        if data is None:
            raise FileNotFoundError(f"vault input not found: {spec['input']}")
        result = {
            "type": jtype, "input": spec["input"], "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if jtype == "text_stats":
            text = data.decode("utf-8")
            result.update({
                "lines": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
                "words": len(text.split()),
                "chars": len(text),
            })
        return result
    raise ValueError(f"unsupported job type: {jtype!r}")


def run_job(vault: DriveVault, job_id: str) -> dict:
    """Execute a queued job against the Vault and persist the outcome.

    Writes status transitions (running -> done/failed), a checkpoint
    before and after execution, and the result -- all in the Vault.
    Returns the result dict (also persisted). Failures are recorded in
    status/result, never silently dropped.
    """
    base = job_dir(job_id)
    job = read_job(vault, job_id)
    if job is None:
        raise FileNotFoundError(f"job not found: {job_id}")
    vault.write_json(f"{base}/status.json",
                     {"job_id": job_id, "state": "running", "updated_at": _now()})
    vault.write_json(f"{base}/checkpoint.json",
                     {"job_id": job_id, "stage": "executing", "updated_at": _now()})
    try:
        result_data = run_job_spec(vault.read_text, vault.read_bytes, job["spec"])
        result = {"job_id": job_id, "ok": True, "result": result_data,
                  "finished_at": _now()}
        state = "done"
    except Exception as e:  # recorded, not swallowed
        result = {"job_id": job_id, "ok": False,
                  "error": f"{type(e).__name__}: {e}", "finished_at": _now()}
        state = "failed"
    vault.write_json(f"{base}/result.json", result)
    vault.write_json(f"{base}/checkpoint.json",
                     {"job_id": job_id, "stage": state, "updated_at": _now()})
    vault.write_json(f"{base}/status.json",
                     {"job_id": job_id, "state": state, "updated_at": _now()})
    return result


# ---------------------------------------------------------------------------
# Colab notebook generation
# ---------------------------------------------------------------------------

_NOTEBOOK_CELL = '''\
# Hermes x Colab job runner -- generated by hermes_hybrid.colab_jobs
# Mounts Google Drive, runs ONE queued job from the canonical Vault
# (ChatGPT-Obsidian-Vault), and writes the result back to the Vault.
import hashlib, json
from pathlib import Path

JOB_ID = "__JOB_ID__"
VAULT_NAME = "__VAULT_NAME__"

from google.colab import drive
drive.mount("/content/drive")

vault = Path("/content/drive/MyDrive") / VAULT_NAME
job_dir = vault / "06_Automation" / "Hermes" / "ColabJobs" / JOB_ID
job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
spec = job["spec"]

def write_json(name, payload):
    (job_dir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\\n",
                                encoding="utf-8")

from datetime import datetime, timezone
def now():
    return datetime.now(timezone.utc).isoformat()

write_json("status.json", {"job_id": JOB_ID, "state": "running", "updated_at": now()})
write_json("checkpoint.json", {"job_id": JOB_ID, "stage": "executing", "updated_at": now()})
try:
    jtype = spec["type"]
    if jtype == "echo":
        result_data = {"type": jtype, "message": spec.get("message", "")}
    elif jtype in ("text_stats", "hash_file"):
        data = (vault / spec["input"]).read_bytes()
        result_data = {"type": jtype, "input": spec["input"], "size": len(data),
                       "sha256": hashlib.sha256(data).hexdigest()}
        if jtype == "text_stats":
            text = data.decode("utf-8")
            result_data.update({
                "lines": text.count("\\n") + (1 if text and not text.endswith("\\n") else 0),
                "words": len(text.split()), "chars": len(text)})
    else:
        raise ValueError(f"unsupported job type: {jtype!r}")
    result = {"job_id": JOB_ID, "ok": True, "result": result_data, "finished_at": now()}
    state = "done"
except Exception as e:
    result = {"job_id": JOB_ID, "ok": False, "error": f"{type(e).__name__}: {e}",
              "finished_at": now()}
    state = "failed"
write_json("result.json", result)
write_json("checkpoint.json", {"job_id": JOB_ID, "stage": state, "updated_at": now()})
write_json("status.json", {"job_id": JOB_ID, "state": state, "updated_at": now()})
print(json.dumps(result, ensure_ascii=False))
'''


def build_colab_notebook(job_id: str, vault_name: str = VAULT_ROOT_NAME) -> dict:
    """A self-contained .ipynb (nbformat 4) that runs one Vault job in Colab."""
    validate_id(job_id)
    source = _NOTEBOOK_CELL.replace("__JOB_ID__", job_id).replace("__VAULT_NAME__", vault_name)
    return {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {
            "colab": {"name": f"hermes-job-{job_id}.ipynb", "provenance": []},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
        },
        "cells": [
            {"cell_type": "markdown", "metadata": {},
             "source": [f"# Hermes Colab job `{job_id}`\n",
                        "Reads the job definition from the canonical Drive Vault "
                        "and writes the result back to the same folder."]},
            {"cell_type": "code", "metadata": {}, "execution_count": None,
             "outputs": [], "source": source.splitlines(keepends=True)},
        ],
    }


def notebook_bytes(job_id: str, vault_name: str = VAULT_ROOT_NAME) -> bytes:
    return (json.dumps(build_colab_notebook(job_id, vault_name),
                       indent=2, ensure_ascii=False) + "\n").encode("utf-8")
