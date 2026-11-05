#!/usr/bin/env python3
"""Stage Keina skills in Hermes without running third-party code.

Usage:
  python scripts/import_keina_skills.py /path/to/keina-discord-bot --dry-run
  python scripts/import_keina_skills.py /path/to/keina-discord-bot

The Keina repository must already be checked out through authorized access.
Never copies secrets, symlinks, or existing destination directories.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "skills" / "keina-import"


def import_skills(source_repo: Path, dry_run: bool = True) -> tuple[int, int]:
    source = source_repo.resolve() / "skills"
    if not source.is_dir():
        raise ValueError(f"Keina skills directory not found: {source}")
    installed = skipped = 0
    for skill in sorted(source.iterdir()):
        if not skill.is_dir() or skill.is_symlink():
            continue
        if not (skill / "SKILL.md").is_file():
            skipped += 1
            print(f"SKIP no SKILL.md: {skill.name}")
            continue
        target = DEST / skill.name
        if target.exists() or target.is_symlink():
            skipped += 1
            print(f"SKIP exists: {target}")
            continue
        if any(path.is_symlink() for path in skill.rglob("*")):
            skipped += 1
            print(f"SKIP symlinks: {skill.name}")
            continue
        print(f"{'PLAN' if dry_run else 'COPY'} {skill.name}")
        if not dry_run:
            DEST.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill, target, symlinks=False)
        installed += 1
    return installed, skipped


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("keina_checkout", type=Path)
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()
    n, skipped = import_skills(args.keina_checkout, dry_run=args.dry_run)
    print(f"{'planned' if args.dry_run else 'copied'}={n}, skipped={skipped}")
