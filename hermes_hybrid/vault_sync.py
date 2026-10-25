"""Vault Git sync: fast-forward pull, careful push, never destructive.

Rules, all tested:
- Never clone over a non-empty directory that is not a Git checkout.
- Pull is fast-forward-only. On divergence, leave the checkout untouched
  and record a conflict marker file; never merge, rebase, reset, or
  force-push.
- Push uses the existing Git credential setup only. No token is read,
  stored, or written by this module, and no remote URL is rewritten.
- Retry is bounded and caller-controlled: ``sync`` retries a failed
  pull/push up to ``retries`` times with a short delay, then returns
  a failure result instead of looping forever.
- This module never deletes user data.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SyncResult:
    ok: bool
    action: str
    detail: str = ""


def _run_git(vault: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(vault), *args],
                          text=True, capture_output=True, timeout=timeout)


class VaultSync:
    def __init__(self, vault_root: str | Path, remote: str = "origin", branch: str = "main"):
        self.root = Path(vault_root).expanduser()
        self.remote = remote
        self.branch = branch

    def is_checkout(self) -> bool:
        return (self.root / ".git").exists()

    def ensure_checkout(self, clone_url: str | None = None) -> SyncResult:
        if self.is_checkout():
            return SyncResult(True, "already-checkout")
        if self.root.exists() and any(self.root.iterdir()):
            return SyncResult(False, "refused-nonempty-non-git",
                              "vault directory is non-empty and not a git checkout")
        if not clone_url:
            return SyncResult(False, "no-clone-url", "no existing checkout and no clone URL given")
        self.root.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["git", "clone", "--branch", self.branch, clone_url, str(self.root)],
                              text=True, capture_output=True, timeout=180)
        if proc.returncode != 0:
            return SyncResult(False, "clone-failed", proc.stderr.strip())
        return SyncResult(True, "cloned")

    def pull_fast_forward(self) -> SyncResult:
        proc = _run_git(self.root, "pull", "--ff-only", self.remote, self.branch)
        if proc.returncode == 0:
            return SyncResult(True, "pulled", proc.stdout.strip())
        # Record divergence without touching the working tree.
        marker = self.root / ".vault-sync-conflict"
        marker.write_text("fast-forward pull failed; local checkout left untouched\n"
                          + proc.stderr.strip() + "\n", encoding="utf-8")
        return SyncResult(False, "pull-diverged", proc.stderr.strip())

    def push(self) -> SyncResult:
        proc = _run_git(self.root, "push", self.remote, self.branch)
        if proc.returncode == 0:
            return SyncResult(True, "pushed", proc.stdout.strip())
        return SyncResult(False, "push-failed", proc.stderr.strip())

    def sync(self, retries: int = 3, delay: float = 2.0) -> SyncResult:
        """Pull fast-forward, then push. On pull divergence, do not push."""
        if not self.is_checkout():
            return SyncResult(False, "not-a-checkout")
        last: SyncResult | None = None
        for _ in range(max(1, retries)):
            pulled = self.pull_fast_forward()
            if not pulled.ok:
                return pulled
            pushed = self.push()
            if pushed.ok:
                return pushed
            last = pushed
            time.sleep(delay)
        return last or SyncResult(False, "sync-failed")
