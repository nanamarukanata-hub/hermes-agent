# Holographic Memory Provider

Local Vault fact store with keyword search, trust scoring, entity resolution, and HRR-based compositional retrieval.

## Requirements

Set `HERMES_OBSIDIAN_VAULT` to the Obsidian Vault root. No SQLite runtime is used. NumPy is optional for HRR algebra.

## Setup

```bash
hermes memory setup    # select "holographic"
```

Or manually:
```bash
hermes config set memory.provider holographic
```

## Config

Config in `config.yaml` under `plugins.hermes-memory-store`:

| Key | Default | Description |
|-----|---------|-------------|
| `db_path` | unset | Optional directory inside `HERMES_OBSIDIAN_VAULT`; defaults to `06_Automation/Hermes/HolographicMemory` under the Vault |
| `auto_extract` | `false` | Auto-extract facts at session end |
| `default_trust` | `0.5` | Default trust score for new facts |
| `hrr_dim` | `1024` | HRR vector dimensions |

Existing `memory_store.db` settings are **not** silently redirected: remove or update
that configuration only after verifying a non-destructive migration of any old
data. The runtime rejects missing Vault configuration, paths outside the Vault,
and legacy `.db` paths. No existing database is deleted.

## Tools

| Tool | Description |
|------|-------------|
| `fact_store` | 9 actions: add, search, probe, related, reason, contradict, update, remove, list |
| `fact_feedback` | Rate facts as helpful/unhelpful (trains trust scores) |
