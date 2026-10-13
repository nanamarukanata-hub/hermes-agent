# GBrain-inspired Obsidian Memory Bridge

- Source: https://github.com/garrytan/gbrain ; reviewed 2026-10-13.
- Integration: Obsidian Markdown is the source of truth; SQLite FTS5 is a rebuildable *local* index; public GitHub contains only open implementation and tests.
- CLI: `omega/scripts/vault_brain.py` supports `index`, `search`, `audit`, `remember`, `correct`, `withdraw`. No automatic capture, embeddings, API cost, cloud upload, nor remote MCP is part of this script.
- Tests: `python -m unittest discover -s omega/tests -v`. Current local smoke: 4/4 pass in an isolated temporary Vault.
- Actual user Vault: connected Drive folder `ChatGPT-Obsidian-Vault`. The private Vault is NOT synchronized into this public repository.
- Chat/Work: Google Drive connector can read/write authorized note files; GitHub stores shareable code and change history. Automatic context loading in all chats is not guaranteed.
- Codex: run on a computer with a local synced Vault and Python 3.9+ with SQLite FTS5; the default index DB is `~/.omega/brain.sqlite`.
- GBrain proper: to enable persistent cross-harness DB via MCP, follow https://github.com/garrytan/gbrain/blob/master/docs/tutorials/connect-coding-agent.md and https://github.com/garrytan/gbrain/blob/master/docs/guides/memory-boundaries.md . The owner must install/setup GBrain locally/hosted, decide search mode and authorizations. Local PGLite stdio processes should not simultaneously own the same DB.
- Privacy: status `withdrawn` excludes a fact from active lookup but does not erase backups. Full data deletion is separate.
- Knowledge-graph scope: only wiki-link extraction and broken-link audit. No typed semantic graph, semantic retrieval or GBrain Dream Cycle.

Implementation deliberately small, verifiable and non-destructive. Obsidian app / Windows GSync / iPhone settings are NOT remotely modified.
