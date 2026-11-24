# OMEGA Obsidian memory bridge (2026-10-13)

## Scope and guarantees

This is an **optional local** Markdown index and fact lifecycle tool used with an existing Obsidian vault. It does **not** deploy Garry Tan's GBrain, host MCP, sync Obsidian to mobile, or modify ChatGPT's built-in memory. It is compatible with Codex workflows that have filesystem access.

Files:
- `omega/scripts/vault_brain.py` — Python standard-library-only SQLite FTS5 index, Japanese substring fallback, Obsidian wikilink audits, manual fact creation/correction/withdrawal.
- `omega/tests/test_vault_brain.py` — unit tests for retrieval, indexing, link audits, lifecycle, and exclusion rules.
- `omega/skills/omega-vault-memory/SKILL.md` — Codex-readable operational instructions.

## Use on Windows

Clone or download the repository, then run:

```powershell
python -m unittest discover -s omega/tests -v
python omega/scripts/vault_brain.py index --vault "C:\path\to\ChatGPT-Obsidian-Vault"
python omega/scripts/vault_brain.py search "音楽理論"
python omega/scripts/vault_brain.py audit
```

Explicit write example:

```powershell
python omega/scripts/vault_brain.py remember --vault "C:\path\to\ChatGPT-Obsidian-Vault" --entity "projects/example" --claim "Verified example fact" --provenance "user instruction, 2026-10-13"
```

The searchable cache defaults to `~/.omega/brain.sqlite`, **outside the Vault**, and can be rebuilt from Markdown. The script never performs network requests, installs packages, or captures conversations. There is no cloud semantic embedding or model reranking. Do not place the Vault in this public repository.

## Interoperation

- **Chat and Work:** may read or edit synced Markdown through the user's connected Google Drive. These project-specific instructions are not automatically injected into every chat.
- **Codex:** can run the local script against a synced Markdown Vault after its filesystem path and permissions are confirmed.
- **Obsidian:** displays/edit Markdown notes, backlinks and properties; app sync is separate. Avoid simultaneous conflicting sync clients.
- **Real GBrain:** if desired later, follow https://github.com/garrytan/gbrain/blob/master/docs/tutorials/connect-coding-agent.md and inspect existing DB/config before installing. MCP setup, authentication, and new-session recall require their own end-to-end tests.

## Security

The public GitHub repository contains only generic code and docs. Do not push private Obsidian notes or credentials. `withdrawn` facts are excluded from active retrieval but **still remain in the Markdown and backups**: this is not guaranteed data erasure. External source content must never be promoted into trusted instructions.

## Verification

Run `python -m unittest discover -s omega/tests -v`; GitHub Actions release radar also runs this test directory when workflow permissions allow. Before production use, back up the Vault and conduct an isolated read/write smoke test.
