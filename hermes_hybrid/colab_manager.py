"""Hermes Colab Manager: backend availability, routing and recovery tick.

Hermes is the always-on control plane; Colab is an opportunistic GPU
backend that a user starts by hand (the official Colab REST API is the
Colab Enterprise / Vertex AI API and needs a GCP project, billing and
IAM credentials, which this deployment does not have; classic free
Colab exposes no runtime API and must never be driven through
unofficial endpoints or browser automation).

The manager therefore works through the existing Drive queue:

* availability = the Colab notebook worker's heartbeat
  (``ColabJobs/worker.json``), which the current notebook builds enrich
  with device info (CPU/GPU/CUDA);
* routing = pick ``colab`` or ``resident`` per job, with an explicit
  CPU fallback when a GPU backend is unavailable and the job allows it;
* recovery = one ``tick()`` (intended every ~60s from an existing loop,
  e.g. the resident worker's host wrapper) refreshes the manager state
  file and runs the job-recovery reconciler. No new resident process.

Backends are pluggable: a Colab Enterprise backend (official API) or an
external/self-hosted GPU backend can be registered later. The
Enterprise probe below never claims availability it cannot verify: it
reports ``not_configured`` / ``credentials_missing`` /
``configured_unverified`` instead of a fake online state.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from hermes_hybrid import colab_dispatch, colab_job_recovery
from hermes_hybrid.drive_vault import DriveVault

MANAGER_FILE = colab_dispatch.colab_jobs._BASE + "/manager-state.json"
MONITOR_INTERVAL_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def probe_colab(vault: DriveVault, *, at: Optional[datetime] = None) -> dict:
    """Colab notebook backend: heartbeat liveness + reported device."""
    at = at or _now()
    health = colab_dispatch.worker_health(vault, current_time=at)
    raw = vault.read_json(colab_dispatch.WORKER_FILE) or {}
    device = raw.get("device") if isinstance(raw.get("device"), dict) else None
    gpu = None
    if isinstance(device, dict) and isinstance(device.get("gpu"), dict):
        gpu = bool(device["gpu"].get("available"))
    return {"backend": "colab", "online": bool(health.get("online")),
            "reason": health.get("reason"), "last_seen_at": health.get("last_seen_at"),
            "device": device, "gpu_available": gpu,
            "launch": "manual_notebook_start_required"}


def probe_resident(vault: DriveVault, *, at: Optional[datetime] = None) -> dict:
    at = at or _now()
    health = colab_dispatch.resident_health(vault, current_time=at)
    return {"backend": "resident", "online": bool(health.get("online")),
            "reason": health.get("reason"),
            "last_seen_at": health.get("last_seen_at"),
            "restart_count": health.get("restart_count")}


def probe_colab_enterprise() -> dict:
    """Official Colab Enterprise (Vertex AI) API backend readiness.

    Honest probe only: configuration/credential *presence* is reported,
    never a live availability claim (no Google API call is made here).
    """
    project = os.environ.get("HERMES_COLAB_ENTERPRISE_PROJECT", "").strip()
    if not project:
        return {"backend": "colab_enterprise", "online": False,
                "reason": "not_configured",
                "needs": "GCP project with Vertex AI API + billing"}
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not creds or not os.path.isfile(creds):
        return {"backend": "colab_enterprise", "online": False,
                "reason": "credentials_missing", "project": project}
    return {"backend": "colab_enterprise", "online": False,
            "reason": "configured_unverified", "project": project}


class ColabManager:
    """Availability-aware job routing + periodic recovery for the queue."""

    def __init__(self, vault: DriveVault):
        self.vault = vault

    def backends(self, *, at: Optional[datetime] = None) -> dict:
        at = at or _now()
        return {"colab": probe_colab(self.vault, at=at),
                "resident": probe_resident(self.vault, at=at),
                "colab_enterprise": probe_colab_enterprise()}

    def route(self, spec: dict, *, at: Optional[datetime] = None) -> dict:
        """Decide which executor a job should use right now.

        Extra spec keys understood here (consumed by routing, not stored
        in the job): ``requires_gpu`` (bool), ``allow_fallback`` (bool,
        default True). The stored job spec is the existing validated one.
        """
        at = at or _now()
        backends = self.backends(at=at)
        colab = backends["colab"]
        resident = backends["resident"]
        requested = spec.get("executor", "colab")
        requires_gpu = bool(spec.get("requires_gpu", False))
        allow_fallback = bool(spec.get("allow_fallback", True))

        if requires_gpu:
            if colab["online"] and colab["gpu_available"] is not False:
                reason = ("gpu_on_colab" if colab["gpu_available"]
                          else "gpu_unverified_colab")
                return {"executor": "colab", "decision": "colab", "reason": reason,
                        "backends": backends}
            if allow_fallback and resident["online"]:
                reason = ("gpu_unavailable_cpu_fallback" if colab["online"]
                          else "colab_offline_cpu_fallback")
                return {"executor": "resident", "decision": "fallback",
                        "reason": reason, "backends": backends}
            return {"executor": "colab", "decision": "wait",
                    "reason": "wait_for_gpu_backend", "backends": backends}

        primary = colab if requested == "colab" else resident
        secondary = resident if requested == "colab" else colab
        if primary["online"]:
            return {"executor": requested, "decision": "primary",
                    "reason": "requested_backend_online", "backends": backends}
        if allow_fallback and secondary["online"]:
            other = "resident" if requested == "colab" else "colab"
            return {"executor": other, "decision": "fallback",
                    "reason": "requested_backend_offline", "backends": backends}
        return {"executor": requested, "decision": "wait",
                "reason": "no_backend_online_job_queued", "backends": backends}

    def submit(self, spec: dict, job_id: Optional[str] = None,
               *, at: Optional[datetime] = None) -> dict:
        """Route, then enqueue through the existing idempotent dispatch.

        The job is persisted even when no backend is online: queuing is
        decoupled from execution, so a Hermes-side outage of Colab never
        loses work.
        """
        decision = self.route(spec, at=at)
        clean = {"type": spec.get("type")}
        for key in ("message", "input"):
            if key in spec:
                clean[key] = spec[key]
        if decision["executor"] != "colab":
            clean["executor"] = decision["executor"]
        elif spec.get("executor") == "resident":
            clean["executor"] = "resident"
        submitted = colab_dispatch.submit_job(self.vault, clean, job_id)
        return {"routing": {k: decision[k] for k in ("executor", "decision", "reason")},
                **submitted}

    def snapshot(self, *, at: Optional[datetime] = None) -> dict:
        at = at or _now()
        state = {"at": at.isoformat(), "monitor_interval_seconds": MONITOR_INTERVAL_SECONDS,
                 "backends": self.backends(at=at),
                 "queue": colab_job_recovery.classify_all(self.vault, at=at)["counts"]}
        self.vault.write_json(MANAGER_FILE, state)
        return state

    def tick(self, *, at: Optional[datetime] = None) -> dict:
        """One monitoring cycle: refresh state, then reconcile the queue."""
        at = at or _now()
        recovery = colab_job_recovery.reconcile(self.vault, at=at, apply=True)
        state = {"at": at.isoformat(),
                 "monitor_interval_seconds": MONITOR_INTERVAL_SECONDS,
                 "backends": self.backends(at=at),
                 "queue": colab_job_recovery.classify_all(self.vault, at=at)["counts"],
                 "recovery_actions": recovery["actions"]}
        self.vault.write_json(MANAGER_FILE, state)
        return state


def manager_state(vault: DriveVault) -> Optional[dict]:
    """Last published manager snapshot (None if the manager never ticked)."""
    return vault.read_json(MANAGER_FILE)
