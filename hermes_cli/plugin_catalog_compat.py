"""Compatibility bridge for Hermes Plugin Catalog on the existing fork.

The catalog itself is imported from upstream as pinned YAML. Bare catalog
names install only the declared SHA. Default installation is disabled;
the explicit --enable flag opts into running the plugin.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from hermes_constants import get_hermes_home
from hermes_cli.plugin_catalog import (
    get_catalog_entry, load_catalog, load_removed_list, match_removed,
    filter_entries,
)

_SHA = re.compile(r"^[0-9a-f]{40}$")
_PART = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_catalog_target(entry) -> None:
    """Only validated, pinned reviewed catalog entries may be installed."""
    if entry is None or not _SHA.fullmatch(entry.sha):
        raise ValueError("Catalog entry must have a full reviewed SHA")
    u = urlsplit(entry.repo)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("Catalog entry must use a plain HTTPS Git URL")
    if entry.subdir:
        parts = entry.subdir.split("/")
        if any(not _PART.fullmatch(part) or part in (".", "..") for part in parts):
            raise ValueError("Unsafe catalog plugin subdir")
    removed = load_removed_list()
    bad = match_removed(entry.name, removed) or match_removed(entry.repo, removed)
    if bad:
        raise ValueError(f"Plugin removed from catalog: {bad.reason}")


def _run_git(args: list[str], *, timeout: int = 120) -> str:
    proc = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(f"Git failed: {(proc.stderr or proc.stdout)[-500:]}")
    return proc.stdout.strip()


def _verify_no_symlinks(path: Path) -> None:
    # Never follow a symlink (including git worktrees with symlinked directories)
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"Symlink not allowed in plugin bundle: {item.relative_to(path)}")


def install_pinned_catalog_entry(entry, *, force: bool = False):
    """Clone at reviewed SHA, select safe subdir, stage and atomically publish.

    Does not enable or load plugin code. Caller controls user approval.
    """
    validate_catalog_target(entry)
    from hermes_cli.plugins_cmd import _plugins_dir, _sanitize_plugin_name, _read_manifest, PluginOperationError

    dest_root = _plugins_dir()
    target = _sanitize_plugin_name(entry.name, dest_root)
    if target.exists() and not force:
        raise PluginOperationError(f"Plugin '{entry.name}' already exists; use --force")

    try:
        with tempfile.TemporaryDirectory(prefix=".catalog-stage-", dir=str(dest_root)) as td:
            stage_root = Path(td)
            repo_path = stage_root / "repo"
            _run_git(["git", "clone", "--quiet", "--no-checkout", entry.repo, str(repo_path)], timeout=180)
            _run_git(["git", "-C", str(repo_path), "fetch", "--quiet", "--depth", "1", "origin", entry.sha], timeout=180)
            _run_git(["git", "-C", str(repo_path), "checkout", "--quiet", "--detach", "FETCH_HEAD"], timeout=60)
            actual_sha = _run_git(["git", "-C", str(repo_path), "rev-parse", "HEAD"])
            if actual_sha != entry.sha:
                raise ValueError("Checkout SHA does not match the catalog pin")

            source = (repo_path / entry.subdir) if entry.subdir else repo_path
            source = source.resolve()
            if source == repo_path.resolve() or repo_path.resolve() in source.parents:
                pass
            else:
                raise ValueError("Subdir escapes repository")
            if not source.is_dir():
                raise ValueError("Catalog subdir missing at pinned SHA")
            _verify_no_symlinks(source)

            stage = stage_root / "install"
            shutil.copytree(source, stage, ignore=shutil.ignore_patterns(".git", ".github"))
            # This older Hermes fork only loads traditional Python directory plugins.
            if not (stage / "plugin.yaml").is_file() or not (stage / "__init__.py").is_file():
                raise ValueError(
                    "This catalog plugin is not compatible with this fork's Python plugin loader. "
                    "Update Hermes to use Desktop / portable / manifest-only catalog plugins."
                )

            manifest = _read_manifest(stage)
            version = manifest.get("manifest_version", 1)
            if int(version) > 1:
                raise ValueError(f"Unsupported plugin manifest_version={version}")

            (stage / ".hermes-catalog.json").write_text(
                json.dumps({"name": entry.name, "repo": entry.repo,
                            "sha": entry.sha, "subdir": entry.subdir,
                            "catalog": "NousResearch/hermes-agent/plugin-catalog",
                            "reviewed_pin": True}, indent=2) + "\n", encoding="utf-8"
            )

            backup = stage_root / "old-install"
            if target.exists():
                target.rename(backup)
            try:
                stage.rename(target)
            except BaseException:
                if backup.exists() and not target.exists():
                    backup.rename(target)
                raise
            installed_manifest = _read_manifest(target)
            return target, installed_manifest, installed_manifest.get("name") or target.name
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise PluginOperationError(str(exc)) from exc


def catalog_command(action: str, *, query: str = "", name: str = "") -> None:
    """Read-only browsing; installing requires separate explicit install command."""
    from rich.console import Console
    from rich.table import Table
    from rich.markdown import Markdown

    console = Console()
    entries = load_catalog()
    if action == "info":
        entry = get_catalog_entry(name)
        if not entry:
            console.print(f"[red]Catalog plugin '{name}' not found[/red]")
            raise SystemExit(1)
        blocked = match_removed(entry.name, load_removed_list()) or match_removed(entry.repo, load_removed_list())
        console.print(Markdown(f"# {entry.name}\n\n{entry.description}"))
        console.print(f"Tier: {entry.tier} | Category: {entry.category}")
        console.print(f"Repository: {entry.repo}")
        console.print(f"Reviewed SHA: {entry.sha}")
        console.print(f"Subdirectory: {entry.subdir or '(root)'}")
        console.print(f"Tools: {', '.join(entry.capabilities.provides_tools) or 'none'}")
        console.print(f"Hooks: {', '.join(entry.capabilities.provides_hooks) or 'none'}")
        console.print(f"Required env: {', '.join(entry.capabilities.requires_env) or 'none'}")
        console.print(f"Status: {'REMOVED' if blocked else 'catalogued'}")
        if blocked:
            console.print(f"Removal reason: {blocked.reason}")
        return
    results = filter_entries(entries, query) if action == "search" else entries
    tab = Table(title=f"Plugin Catalog ({len(results)} matches / {len(entries)} indexed)")
    tab.add_column("Name")
    tab.add_column("Tier")
    tab.add_column("Category")
    tab.add_column("Summary")
    for item in results:
        tab.add_row(item.name, item.tier, item.category, item.description[:85])
    console.print(tab)
    console.print("[dim]Inspect: hermes plugins info NAME | Install pinned: hermes plugins install NAME --no-enable[/dim]")
