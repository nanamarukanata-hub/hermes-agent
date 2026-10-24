"""Obsidian Vault is the exclusive Hermes session store.

The former SQLite SessionDB/FTS/WAL implementation was intentionally removed.
The compatibility alias keeps callers using `from hermes_state import SessionDB`
working while persisting exclusively inside HERMES_OBSIDIAN_VAULT.
"""
import os
from pathlib import Path

from hermes_obsidian_state import SessionDB

# Compatibility-only path consumed by older availability checks. This does not
# represent a SQLite file; the runtime never opens or creates it.
_vault = os.environ.get("HERMES_OBSIDIAN_VAULT", "")
DEFAULT_DB_PATH = (Path(_vault).expanduser().resolve() / "06_Automation" / "Hermes" /
                   "Sessions" / "metadata.json") if _vault else Path("/nonexistent-obsidian-vault/metadata.json")

__all__ = ["SessionDB", "DEFAULT_DB_PATH"]
