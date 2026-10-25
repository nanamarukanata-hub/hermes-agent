"""Approval policy for the bridge.

This is the automation the user asked for, scoped honestly:

- AUTO (no human gate): read-only actions (status, diff, logs,
  session history) and bounded test runs inside a configured
  checkout.  These are the actions a ChatGPT read can trigger.
- NEEDS_APPROVAL: anything that changes a repo (patch staging,
  commit, push), deletes data, deploys, or runs an instruction
  outside the auto categories.  Such a task is stored as
  ``needs_approval`` and is only executed after an explicit
  ``muse_approve`` call *and* Muse review.
- NEVER (refused by the server, even with approval): deleting a
  repo checkout, force-push, production deploy, secret exfiltration,
  and any repo not listed in ``repos.yaml``.

Muse's own approval cards in the main chat are a separate system
and cannot be auto-approved from here (the agent's permission tools
are read-only).  This policy governs the bridge server only, and
it does not bypass Muse's own approvals.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

AUTO_PATTERNS = (
    re.compile(r"^\s*(git\s+)?(status|diff|log)\b", re.IGNORECASE),
    re.compile(r"^\s*run tests?\b", re.IGNORECASE),
    re.compile(r"^\s*test\b", re.IGNORECASE),
    re.compile(r"^\s*show (status|diff|logs?|session)\b", re.IGNORECASE),
)
NEVER_PATTERNS = (
    re.compile(r"\b(rm\s+-rf|force-push|--force|deploy|production)\b", re.IGNORECASE),
    re.compile(r"\b(delete|drop)\s+(repo|database|branch)\b", re.IGNORECASE),
)


def classify(instruction: str) -> str:
    """Return 'auto', 'needs_approval', or 'never' for an instruction."""
    text = instruction or ""
    for pattern in NEVER_PATTERNS:
        if pattern.search(text):
            return "never"
    for pattern in AUTO_PATTERNS:
        if pattern.search(text):
            return "auto"
    return "needs_approval"


def load_repos(config_path: str | Path | None = None) -> dict[str, str]:
    """Load repo -> local checkout path mapping.

    Default (no config file): hermes-agent maps to the checkout this
    server runs from when it looks like a hermes-agent checkout,
    otherwise it is unmapped and tools report 'no local checkout'.
    apex-6-private has no local checkout on this VM (private repo,
    no auth configured here), so it is unmapped until the user
    points it at a checkout.  A ``repos.yaml`` with lines
    ``name: /absolute/path`` overrides the defaults.
    """
    repos: dict[str, str] = {}
    if config_path is not None:
        path = Path(config_path).expanduser()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                name, _, checkout = line.partition(":")
                repos[name.strip()] = checkout.strip()
    return repos


def checkout_for(repo: str, repos: dict[str, str], default_checkout: str | Path | None = None) -> str | None:
    if repo in repos:
        candidate = Path(repos[repo]).expanduser()
        return str(candidate) if candidate.is_dir() else None
    if default_checkout is not None and repo == "hermes-agent":
        candidate = Path(default_checkout).expanduser()
        if (candidate / "AGENTS.md").exists() or (candidate / "run_agent.py").exists():
            return str(candidate)
    return None
