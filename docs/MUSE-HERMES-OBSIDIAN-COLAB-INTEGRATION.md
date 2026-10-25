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

## Muse execution instruction — execute 10 development iterations

Do not spend iterations on Muse Bridge OAuth or Muse CLI authentication. Begin directly with the engineering tasks below. Complete each iteration only after recording changed files, commit SHA, test output and CI run URL; if blocked, report the blocker and continue with the next independently executable task. Never claim an unexecuted action succeeded.

1. Inspect Hermes PR #11 and APEX-6 PR #16, collect latest failing CI tests and identify exact root causes.
2. Fix duplicate Discord session messages caused by Vault/legacy JSONL merge; add idempotency tests and preserve all existing user data.
3. Make Vault the only canonical runtime store for session transcripts and memory; remove legacy write fallbacks without deleting old files.
4. Audit Kanban, thread metadata, worker logs, and workspaces for writes outside the configured Vault; fix and test.
5. Run Hermes Discord E2E and full regression suite; address storage-related failures, isolate unrelated failures.
6. Pin the tested Hermes commit in APEX-6 CI and validate cross-project integration.
7. Verify a safe test note can be written, synced to the existing Google Drive Obsidian Vault, and read back; handle conflicts and report evidence.
8. Implement a manually invoked Colab job contract with sanitized fixtures, bounded execution and Vault-based checkpoint/result paths; no unattended runtime claims.
9. Execute a permitted Colab smoke test, if a runtime is available, and verify its result is synchronized back into Vault; otherwise provide exact blocker and tested local equivalent.
10. Run restart/crash/concurrency and no-outside-Vault-write tests; produce a go/no-go readiness report. Keep both PRs Draft.

Do not merge, deploy, delete real data, publish secrets, or add RP content/filters. No production activation without explicit approval.

## Activation gates
- [ ] Hermes CI including Discord E2E and duplicate-message regression green.
- [ ] APEX-6 CI green on pinned Hermes commit (not moving branch).
- [ ] Obsidian Vault writable on target runtime; real Drive sync and readback proven.
- [ ] Colab notebook/job run manually with sanitized fixture and result stored in Vault.
- [ ] Failure/restart recovery and no-outside-Vault-write tests green.
- [ ] Only after independent verification, request explicit approval for production deployment.
