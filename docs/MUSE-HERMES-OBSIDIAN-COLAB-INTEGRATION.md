# Muse-directed Hermes Discord RP Bot — integration handoff (2026-10-26)

## Objective
Muse is the operator/development coordinator; Hermes Agent runs the Discord RP bot; Obsidian Vault is the sole durable source of truth for conversations, memory, sessions, and Kanban. Google Drive holds/synchronizes the Obsidian Vault; Colab provides opt-in compute jobs only. Do not introduce RP-specific filters, characters, or worldbuilding.

## Existing development state (verify live before acting)
- Hermes: PR #11, branch `feat/obsidian-session-backend-20261025` (Draft).
- APEX-6: PR #16, branch `feat/obsidian-only-storage-20261025` (Draft).
- Google Drive has a folder named `ChatGPT-Obsidian-Vault`; its presence does not prove a mounted local filesystem or bidirectional sync.
- Existing Muse Bridge MCP URL: `https://muse-chatgpt-bridge.onrender.com/mcp`; real Muse CLI authentication and tool invocation must be verified.

## Architecture contracts
1. Discord gateway -> Hermes session API -> Obsidian-backed state. Never silently write session/memory/kanban records to SQLite, JSONL, or legacy Hermes home outside Vault.
2. Vault files use stable identifiers, atomic writes, per-session locks or equivalent concurrency control, and deterministic replay; test duplicate message prevention and crash recovery.
3. Google Drive is the synchronization/transport layer, not a second independent writable truth. Implement a single-writer strategy or explicit conflict resolution; do not assume Google Drive folder IDs are filesystem paths.
4. Colab is an ephemeral job executor: job specs/results/checkpoints are stored in Vault, no unattended guarantee, no secrets in notebooks/logs, no assumption that a free runtime is always available.
5. Muse -> authenticated MCP bridge -> authorized CLI execution -> GitHub development PRs. OAuth for ChatGPT-to-bridge and Muse CLI login are separate authentications; verify both with a harmless read-only command before allowing code changes.

## Muse execution instruction (send through authenticated muse-cli when available)
Inspect Hermes PR #11 and APEX-6 PR #16, including latest GitHub Actions logs. Audit the Discord session load/save path for duplicate messages and dual JSONL/Vault persistence; implement a minimal fix and regression tests on the Hermes development branch. Confirm Obsidian-only durability, then test Google Drive Vault synchronization with a non-sensitive test note and verify readback. Design and validate a manually triggered Colab job with Vault-backed checkpoints. Keep both PRs Draft; do not merge, deploy, delete real data, publish secrets, or add RP content/filters. Report exact commands, commits, CI URLs and outstanding failures. Do not claim any unexecuted step as complete.

## Activation gates
- [ ] Muse CLI authentication and read-only command executed; record redacted output.
- [ ] Hermes CI including Discord E2E and duplicate-message regression green.
- [ ] APEX-6 CI green on pinned Hermes commit (not moving branch).
- [ ] Obsidian Vault writable on target runtime; real Drive sync and readback proven.
- [ ] Colab notebook/job run manually with sanitized fixture and result stored in Vault.
- [ ] Failure/restart recovery and no-outside-Vault-write tests green.
- [ ] Only after independent verification, request explicit approval for production deployment.
