"""Authenticated, cookie-free resident connection from Muse VM to Render.

Once paired, Muse can maintain an outbound HTTPS long-poll and reconnect
after a VM restart. No Muse account session cookie, no scheduled AI task,
and no publicly exposed VM listener.

Security: short-lived single-use pairing code, HMAC-signed agent credential
derived from the existing persistent Render OAuth approval secret, explicit
task approval, bounded JSON and idempotent task claiming. The service's
current Free-plan filesystem remains EPHEMERAL; persistence across Render
restarts needs durable storage and is not provided by this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from typing import Any

PAIR_LIFETIME = 600
LEASE_SECONDS = 120
MAX_RESULT = 16000
MAX_DIFF = 16000
MAX_TEST_RESULTS = 12000
MAX_READ_WAIT = 25
_ALLOWED_ID = re.compile(r"^[0-9a-f]{16}$")
_ALLOWED_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,48}$")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _digest(s: str) -> bytes:
    return hashlib.sha256(s.encode("ascii")).digest()


class ResidentChannel:
    def __init__(self, store, approval_key: str, public_url: str = ""):
        if not approval_key or len(approval_key) < 20:
            raise ValueError("a persistent Render approval key is required")
        self.store = store
        self.public_url = public_url.rstrip("/")
        # Domain separation: the OAuth approval key itself never leaves Render.
        self._sign_key = hmac.new(approval_key.encode("utf-8"),
                                  b"muse-resident-channel-v1", "sha256").digest()
        self._lock = threading.RLock()
        self._pending_pair: tuple[bytes, float] | None = None
        self._last_seen = 0.0
        self._last_client = None

    def begin_pair(self, label: str = "muse-vm") -> dict:
        if not isinstance(label, str) or not _ALLOWED_NAME.fullmatch(label):
            return {"error": "invalid label"}
        code = secrets.token_urlsafe(30)
        with self._lock:
            self._pending_pair = (_digest(code), time.time() + PAIR_LIFETIME)
        return {
            "status": "pairing_open",
            "pair_code": code,
            "expires_in_seconds": PAIR_LIFETIME,
            "agent_base_url": self.public_url,
            "label": label,
            "note": ("Enter this code ONCE in your personal Muse chat for "
                     "the VM resident courier setup. Do not post the code "
                     "on GitHub or send it to unrelated sites. "
                     "The credential is stored on Muse VM after pairing.")
        }

    def exchange_pair(self, code: Any, label: Any = "muse-vm") -> dict:
        if not isinstance(code, str) or not 25 <= len(code) <= 96:
            return {"error": "invalid pairing code"}
        if not isinstance(label, str) or not _ALLOWED_NAME.fullmatch(label):
            return {"error": "invalid label"}
        with self._lock:
            pair = self._pending_pair
            if pair is None or time.time() >= pair[1] or not hmac.compare_digest(_digest(code), pair[0]):
                return {"error": "invalid or expired pairing code"}
            self._pending_pair = None  # never replay
        payload = _b64(json.dumps({
            "v": 1, "label": label, "iat": int(time.time()),
            "nonce": secrets.token_urlsafe(18),
        }, separators=(",", ":")).encode("utf-8"))
        mac = _b64(hmac.new(self._sign_key, payload.encode("ascii"), "sha256").digest())
        return {
            "agent_token": f"mrc1.{payload}.{mac}",
            "status": "paired",
            "note": ("Keep this credential in /home/hatch with mode 0600. "
                     "Valid until the Render approval key is rotated. "
                     "Do not print or commit it."),
        }

    def authorize(self, token: str) -> bool:
        if not isinstance(token, str) or len(token) > 1000:
            return False
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "mrc1":
            return False
        try:
            payload = json.loads(_unb64(parts[1]).decode("utf-8"))
            expected = hmac.new(self._sign_key, parts[1].encode("ascii"), "sha256").digest()
            actual = _unb64(parts[2])
        except (UnicodeDecodeError, ValueError, TypeError):
            return False
        return (hmac.compare_digest(expected, actual) and
                isinstance(payload, dict) and payload.get("v") == 1 and
                isinstance(payload.get("label"), str) and
                bool(_ALLOWED_NAME.fullmatch(payload["label"])) and
                isinstance(payload.get("iat"), int) and
                0 < payload["iat"] <= time.time() + 60 and
                isinstance(payload.get("nonce"), str) and
                10 <= len(payload["nonce"]) <= 64)

    def _mark_seen(self, label: str = "muse-vm") -> None:
        self._last_seen = time.time()
        self._last_client = label

    def status(self) -> dict:
        with self._lock:
            last = self._last_seen
            return {
                "mode": "cookie_free_resident",
                "connected_recently": bool(last and (time.time() - last) < 90),
                "seconds_since_last_contact": round(time.time() - last) if last else None,
                "service_state_durable": False,
                "note": ("Connectivity is inferred from authenticated courier requests. "
                         "The Render Free local task/OAuth store is not persistent."),
            }

    def _claim_now(self) -> dict | None:
        now = time.time()
        with self._lock:
            for task in self.store.list_tasks():
                state = task.get("status")
                if state == "approved" and task.get("approval") == "approved":
                    pass
                elif (state == "awaiting_muse" and task.get("agent_channel") == "resident"
                      and task.get("agent_lease_expires_at", float("inf")) <= now):
                    pass
                else:
                    continue
                task_id = task.get("task_id", "")
                if not _ALLOWED_ID.fullmatch(task_id):
                    continue
                attempt = int(task.get("agent_delivery_attempts") or 0) + 1
                self.store.update_task(task_id, status="awaiting_muse",
                                       agent_channel="resident",
                                       agent_lease_expires_at=now + LEASE_SECONDS,
                                       agent_delivery_attempts=attempt)
                return {
                    "task_id": task_id,
                    "session_id": task.get("session_id"),
                    "repo": task.get("repo"),
                    "instruction": task.get("instruction"),
                    "delivery_attempt": attempt,
                    "lease_seconds": LEASE_SECONDS,
                    "note": ("Idempotency key is task_id. Muse worker must "
                             "not execute duplicate deliveries. Report "
                             "completion explicitly using /agent/complete."),
                }
        return None

    def next_task(self, wait_seconds: int = 0) -> dict | None:
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= MAX_READ_WAIT:
            raise ValueError("invalid wait_seconds")
        deadline = time.monotonic() + wait_seconds
        with self._lock:
            self._mark_seen()
        while True:
            task = self._claim_now()
            if task:
                return task
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(1, remaining))

    def complete(self, task_id: Any, result: Any, failed: Any = False,
                 diff: Any = None, test_results: Any = None) -> dict:
        if not isinstance(task_id, str) or not _ALLOWED_ID.fullmatch(task_id):
            return {"error": "invalid task_id"}
        if not isinstance(result, str) or not result.strip() or len(result) > MAX_RESULT:
            return {"error": "result must be a nonempty string <= 16000 characters"}
        if type(failed) is not bool:
            return {"error": "failed must be boolean"}
        for name, value, bound in (("diff", diff, MAX_DIFF),
                                   ("test_results", test_results, MAX_TEST_RESULTS)):
            if value is not None and (not isinstance(value, str) or len(value) > bound):
                return {"error": f"{name} must be a string <= {bound} characters"}
        with self._lock:
            task = self.store.get_task(task_id)
            if task is None:
                return {"error": "task not found"}
            if task.get("agent_channel") != "resident" or task.get("status") != "awaiting_muse":
                return {"error": "task not awaiting completion from resident Muse"}
            self.store.update_task(task_id, status="failed" if failed else "done",
                                   result=result, diff=diff, test_results=test_results,
                                   agent_lease_expires_at=None,
                                   agent_completed_at=time.time())
            self._mark_seen()
            return {"task_id": task_id, "status": "failed" if failed else "done",
                    "note": "Muse-reported result; inspect evidence independently."}
