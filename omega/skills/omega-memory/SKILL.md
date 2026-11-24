---
name: omega-memory
description: Provenance-aware Obsidian memory workflow for ChatGPT, Work and Codex. Explicit memory writes, recall, correction, withdrawal and link auditing.
---
# OMEGA Memory

1. Read the Vault's `00_System/GBRAIN-MEMORY-ARCHITECTURE.md` and `03_Knowledge/Memory/MOC-Memory.md` if connected.
2. Retrieve only relevant notes; include each supporting original source and verification date in answers.
3. To preserve an explicit user instruction or durable fact, add a single `memory-fact` record with provenance. Never auto-ingest all chats.
4. Newer user corrections override older facts; mark older records as `superseded`. A withdrawn claim must not be surfaced as an active memory.
5. For local Codex, `python omega/scripts/vault_brain.py index --vault <path>`, then `search <phrase>` and `audit`.
6. These scripts are keyword search / link graph checks, not GBrain MCP or semantic embeddings. Never promise cross-chat automatic loading.
7. Keep all private Markdown out of this public GitHub repo. GBrain official installation and OAuth MCP remain separate verified steps.

Memory operations require user intent, scoped file write authority, and backing up before modifying existing factual records.
