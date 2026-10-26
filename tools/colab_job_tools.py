"""Hermes model-callable Colab dispatch tool, backed by canonical Drive Vault.

Only visible when Hermes's existing Drive access token and the pinned
canonical Vault folder ID are configured. No new credentials are minted here.
A submit queues a job; it cannot boot a stopped Colab runtime.
"""
from __future__ import annotations

import os
import threading

from tools.registry import registry, tool_error, tool_result


_CREDENTIAL_LOCK = threading.RLock()
_CREDENTIALS = None


def _enabled() -> bool:
    # A pinned canonical Vault and EITHER a live access token or a mounted
    # renewable Application Default Credentials JSON are required.
    if not os.environ.get("HERMES_DRIVE_VAULT_ROOT_ID"):
        return False
    token = bool(os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN"))
    adc_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    return token or bool(adc_file and os.path.isfile(adc_file))


def _vault():
    from hermes_hybrid.drive_vault import (
        DriveVault, DriveAuthError, HttpDriveTransport, drive_vault_from_env,
    )
    if os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN"):
        return drive_vault_from_env()
    filename = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not filename or not os.path.isfile(filename):
        raise DriveAuthError("No authorized Google credentials are configured")
    try:
        import google.auth
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise DriveAuthError("Install google-auth for renewable Google credentials") from exc

    # Existing runtime credentials are used; no browser flow runs here.
    # Credentials may represent a service account or an authorized OAuth user.
    global _CREDENTIALS
    with _CREDENTIAL_LOCK:
        if _CREDENTIALS is None or _CREDENTIALS[0] != filename:
            credentials, _project = google.auth.load_credentials_from_file(
                filename, scopes=["https://www.googleapis.com/auth/drive"])
            _CREDENTIALS = (filename, credentials)
        credentials = _CREDENTIALS[1]

    def token_provider():
        with _CREDENTIAL_LOCK:
            if not credentials.valid:
                credentials.refresh(Request())
            if not credentials.token:
                raise DriveAuthError("Authorized credential has no access token")
            return credentials.token

    return DriveVault(
        HttpDriveTransport(token_provider),
        root_id=os.environ["HERMES_DRIVE_VAULT_ROOT_ID"],
    )


def _submit(args: dict, **_kwargs) -> str:
    if not _enabled():
        return tool_error("Hermes Drive credentials and pinned Vault root are not configured")
    try:
        from hermes_hybrid.colab_dispatch import submit_job
        kind = args.get("type")
        if kind == "echo":
            spec = {"type": kind, "message": args.get("message", "")}
        elif kind in ("text_stats", "hash_file"):
            spec = {"type": kind, "input": args.get("input", "")}
        else:
            return tool_error("unsupported Colab job type")
        if "executor" in args:
            spec["executor"] = args["executor"]
        result = submit_job(_vault(), spec, args.get("job_id") or None)
        return tool_result(success=True, **result,
                           note="Job queued in Drive; Colab executes only while worker notebook is running.")
    except (ValueError, TypeError, KeyError) as exc:
        return tool_error(str(exc))
    except Exception as exc:
        # Never leak network details, OAuth credentials, or a traceback.
        return tool_error("Colab dispatch unavailable: " + type(exc).__name__)


def _status(args: dict, **_kwargs) -> str:
    if not _enabled():
        return tool_error("Hermes Drive OAuth and pinned Vault root are not configured")
    try:
        from hermes_hybrid.colab_dispatch import job_report
        return tool_result(success=True, **job_report(_vault(), args.get("job_id", "")))
    except (ValueError, TypeError, KeyError) as exc:
        return tool_error(str(exc))
    except Exception as exc:
        return tool_error("Colab result read unavailable: " + type(exc).__name__)


def _health_status(_args: dict, **_kwargs) -> str:
    if not _enabled():
        return tool_error("Hermes Drive credentials and pinned Vault root are not configured")
    try:
        from hermes_hybrid.colab_dispatch import worker_health, resident_health
        vault = _vault()
        return tool_result(success=True, colab=worker_health(vault),
                           resident=resident_health(vault))
    except Exception as exc:
        return tool_error("Colab worker health unavailable: " + type(exc).__name__)


COLAB_HEALTH_SCHEMA = {
    "name": "colab_health",
    "description": "Read Colab notebook and 24h Linux resident worker heartbeats and current process uptime from Google Drive. No job ID required. Not proof of 24-hour uninterrupted availability.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}


COLAB_SUBMIT_SCHEMA = {
    "name": "colab_submit",
    "description": (
        "Queue an allowlisted job for a user-started Google Colab worker through "
        "the Hermes canonical Drive Vault. Returns job_id and queue status, "
        "NOT a guarantee that a Colab runtime is running. Operations: echo, "
        "text_stats, hash_file. Do not submit arbitrary Python code."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["echo", "text_stats", "hash_file"]},
            "message": {"type": "string", "maxLength": 2000},
            "input": {"type": "string", "description": "Vault-relative path under 06_Automation/Hermes/"},
            "job_id": {"type": "string", "description": "Optional idempotency key; reuse on retry"},
            "executor": {"type": "string", "enum": ["colab", "resident"],
                         "description": "colab (default): only active notebook; resident: Linux 24/7 worker"},
        },
        "required": ["type"],
    },
}

COLAB_STATUS_SCHEMA = {
    "name": "colab_status",
    "description": (
        "Read canonical Drive Vault Colab job status, result and worker heartbeat. "
        "Reports queued, running, done or failed, with worker online/stale state."
    ),
    "parameters": {
        "type": "object",
        "properties": {"job_id": {"type": "string"}},
        "required": ["job_id"],
    },
}

registry.register(
    name="colab_submit", toolset="colab", schema=COLAB_SUBMIT_SCHEMA,
    handler=_submit, check_fn=_enabled, requires_env=[],
    description=COLAB_SUBMIT_SCHEMA["description"], emoji="🧮",
)
registry.register(
    name="colab_status", toolset="colab", schema=COLAB_STATUS_SCHEMA,
    handler=_status, check_fn=_enabled, requires_env=[],
    description=COLAB_STATUS_SCHEMA["description"], emoji="🧮",
)

registry.register(
    name="colab_health", toolset="colab", schema=COLAB_HEALTH_SCHEMA,
    handler=_health_status, check_fn=_enabled, requires_env=[],
    description=COLAB_HEALTH_SCHEMA["description"], emoji="🩺",
)
