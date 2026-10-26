"""Hermes model-callable Colab dispatch tool, backed by canonical Drive Vault.

Only visible when Hermes's existing Drive access token and the pinned
canonical Vault folder ID are configured. No new credentials are minted here.
A submit queues a job; it cannot boot a stopped Colab runtime.
"""
from __future__ import annotations

import os

from tools.registry import registry, tool_error, tool_result


def _enabled() -> bool:
    return bool(
        os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN")
        and os.environ.get("HERMES_DRIVE_VAULT_ROOT_ID")
    )


def _vault():
    from hermes_hybrid.drive_vault import drive_vault_from_env
    return drive_vault_from_env()


def _submit(args: dict, **_kwargs) -> str:
    if not _enabled():
        return tool_error("Hermes Drive OAuth and pinned Vault root are not configured")
    try:
        from hermes_hybrid.colab_dispatch import submit_job
        kind = args.get("type")
        if kind == "echo":
            spec = {"type": kind, "message": args.get("message", "")}
        elif kind in ("text_stats", "hash_file"):
            spec = {"type": kind, "input": args.get("input", "")}
        else:
            return tool_error("unsupported Colab job type")
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
