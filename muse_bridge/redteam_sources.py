"""Isolated source checkout for LLM robustness research repositories.

Only clones reviewed public repositories into an isolated VM workspace.
Does not install dependencies, run adversarial prompts, contact a model,
modify Muse/Hermes runtime settings, or alter any policy control.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

# Explicit registry: only fixed HTTPS GitHub repository URLs may be cloned.
REPOSITORIES: dict[str, str] = {
    "garak": "https://github.com/NVIDIA/garak.git",
    "pyrit": "https://github.com/microsoft/PyRIT.git",
    "promptfoo": "https://github.com/promptfoo/promptfoo.git",
    "jailbreakbench": "https://github.com/JailbreakBench/jailbreakbench.git",
    "harmbench": "https://github.com/centerforaisafety/HarmBench.git",
    "agentdojo": "https://github.com/ethz-spylab/agentdojo.git",
    "llm-attacks": "https://github.com/llm-attacks/llm-attacks.git",
    "pair": "https://github.com/patrickrchao/JailbreakingLLMs.git",
    "tap": "https://github.com/RICommunity/TAP.git",
    "autodan": "https://github.com/SheltonLiu-N/AutoDAN.git",
}
DEFAULT_ROOT = Path.home() / "workspace" / "muse-redteam-sources"


class CheckoutError(RuntimeError):
    pass


def workspace_root(root: Path) -> Path:
    """Require the destination under ~/workspace; reject links in each segment."""
    home = Path.home().resolve()
    root = root.expanduser()
    if not root.is_absolute():
        raise CheckoutError("root must be absolute")
    try:
        relative = root.relative_to(home / "workspace")
    except ValueError:
        raise CheckoutError("checkout must be under ~/workspace") from None
    if not relative.parts or any(p in ("..", ".") for p in relative.parts):
        raise CheckoutError("checkout root is too broad")
    current = home
    for part in ("workspace", *relative.parts):
        current = current / part
        if current.is_symlink():
            raise CheckoutError("symlinks in checkout root are prohibited")
    return root


def _git(args: list[str], *, cwd: Path | None = None) -> str:
    env = {k: v for k, v in os.environ.items()
           if k in ("HOME", "PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        done = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=180, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CheckoutError("git unavailable or timed out") from None
    if done.returncode:
        # Never echo error output, which can include proxy/user configuration.
        raise CheckoutError("git exited nonzero: %d" % done.returncode)
    return done.stdout.decode("utf-8", "replace").strip()


def fetch_sources(root: Path) -> dict[str, Any]:
    root = workspace_root(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    results: dict[str, Any] = {"root": str(root), "repositories": {}, "errors": {}}
    for name, url in REPOSITORIES.items():
        folder = root / name
        if folder.is_symlink():
            results["errors"][name] = "existing symlink; skipped"
            continue
        try:
            if folder.exists():
                if not (folder / ".git").is_dir():
                    raise CheckoutError("existing non-repository directory")
                # Do not update existing checkouts or modify user changes.
                current = _git(["rev-parse", "HEAD"], cwd=folder)
                results["repositories"][name] = {"sha": current, "status": "already-present"}
                continue
            # Source fetch only. No pip/npm install; no checkout hooks.
            _git(["clone", "--depth", "1", "--filter=blob:none", "--",
                  url, str(folder)])
            current = _git(["rev-parse", "HEAD"], cwd=folder)
            results["repositories"][name] = {"sha": current, "status": "cloned"}
        except CheckoutError as exc:
            results["errors"][name] = str(exc)
    # Local report excludes private credentials and source contents.
    report = root / "checkout-report.json"
    fd = os.open(report, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch fixed GitHub LLM research sources (no execution)")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--fetch", action="store_true",
                   help="Clone sources. Without this flag, only list targets.")
    args = p.parse_args(argv)
    if not args.fetch:
        print(json.dumps({"root": str(args.root), "planned_sources": REPOSITORIES,
                          "executed": False}, indent=2))
        return 0
    try:
        report = fetch_sources(args.root)
    except CheckoutError as exc:
        print("checkout error: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
