"""Strict owner-delegated, no-per-task-confirmation operations.

Only a single JSON object in the exact AUTO_VM_V1 protocol qualifies.
All natural-language requests and all privileged or write operations
retain the normal Bridge approval / Muse review workflow.
"""
from __future__ import annotations

import json
from typing import Any

PREFIX = "AUTO_VM_V1 "
MAX_INSTRUCTION = 512

READ_FILES = frozenset(("README.md", "muse_bridge/VM-COURIER.md"))
TEST_FILES = frozenset((
    "test_muse_vm_courier.py",
    "test_muse_vm_operator.py",
    "test_muse_agent_config_operator.py",
))
SAFE_ACTIONS = frozenset(("git_status", "git_diff", "read_file", "run_tests"))


class AutoSyntaxError(ValueError):
    """Invalid or unsupported automatic action."""


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AutoSyntaxError("duplicate keys are not permitted")
        result[key] = value
    return result


def parse(instruction: Any) -> dict[str, str] | None:
    """Return a validated fixed command or None for an ordinary instruction.

    An invalid AUTO_VM_V1 request is an error, never silently approved.
    """
    if not isinstance(instruction, str) or not instruction.startswith(PREFIX):
        return None
    if len(instruction) > MAX_INSTRUCTION:
        raise AutoSyntaxError("automatic instruction too long")
    try:
        obj = json.loads(instruction[len(PREFIX):], object_pairs_hook=_unique_pairs)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AutoSyntaxError("invalid automatic instruction JSON") from exc
    if not isinstance(obj, dict):
        raise AutoSyntaxError("automatic instruction must be an object")
    action = obj.get("action")
    if not isinstance(action, str) or action not in SAFE_ACTIONS:
        raise AutoSyntaxError("action is not auto-approved")
    if action in ("git_status", "git_diff"):
        if set(obj) != {"action"}:
            raise AutoSyntaxError("unexpected parameters")
    elif action == "read_file":
        if set(obj) != {"action", "path"} or obj["path"] not in READ_FILES:
            raise AutoSyntaxError("only allowlisted documentation may be read")
    elif action == "run_tests":
        if set(obj) != {"action", "pattern"} or obj["pattern"] not in TEST_FILES:
            raise AutoSyntaxError("only allowlisted regression suites may run")
    return obj


def encode(action: str, **kwargs: str) -> str:
    obj = {"action": action, **kwargs}
    result = PREFIX + json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    if parse(result) is None:
        raise AutoSyntaxError("not an automatic action")
    return result
