# OMEGA official release review — 2026-11-12

Status: verified release metadata; compatibility assessment only; no deployment.

## Verified changes
- **GBrain v0.60.682.0** (published 2026-11-11 23:30 UTC): fixes owner access to derived pages/facts when the owner can read all origins. A separate operator-only, host-side opt-in `gbrain access owner reads-restricted on` permits the owner to read other members' restricted pages (but not another member's own member page). **Default is off. Do not enable automatically.** Validate ACL and provenance with synthetic multi-user fixtures before any integration. [Official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.682.0).
- **Hermes Agent v0.23.1** (published 2026-11-10 01:42 UTC): increases accepted plugin catalog from 2 MiB to 16 MiB, fixing fallback to stale bundled plugin metadata, including missing removal entries. Large patch rollup; no production Gateway/Discord/Obsidian migration without isolated regression tests. [Official release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.1).
- **Codex rust-v0.167.2** (published 2026-11-11 22:44 UTC): incremental tool updates by default for Google Cloud Vertex AI. Configuration and provider-specific behavior need verification; no mandatory OMEGA code change identified. [Official release](https://github.com/openai/codex/releases/tag/rust-v0.167.2).
- Claude Code `v2.1.322` and Obsidian `v1.15.0`: no newer GitHub non-prerelease tags in this check.

## Security, licensing, compatibility and music-production value
- GBrain's optional owner-wide access expands read privileges: never turn it on for a shared or private Vault without explicit review. Test owner/member isolation, derived facts, search caches and remote MCP credentials. Do not infer that owner labels alone establish authentication.
- Hermes plugin catalog correctness is a supply-chain issue: inspect provenance, signatures/permissions, pinned versions and removed entries before installing plugins. Do not automatically execute third-party Skills.
- No third-party source code or binaries were copied; no new license obligations introduced by these documentation changes. Confirm each upstream license before any future code reuse.
- DTM/Kontakt/KSP/JUCE/VST3/mixing benefit is indirect: safer knowledge retrieval, plugin discovery and agent reliability. No evidence of DSP, audio quality or rendering-speed improvements.

## Scope and rollback
- Documentation-only changes: release metadata and this review. Rollback by reverting these two documentation commits.
- No local software upgrade, no Google Drive/Obsidian note writes, no credentials, personal notes, or user-specific paths published.
- `omega/docs/gbrain-obsidian-bridge.md` was unavailable on the reviewed branch; reconcile its separate PR and the no-SQLite policy before integration.
- Runtime tests for GBrain ACL, Hermes Gateway and Claude Code MCP were **not performed**. CI must be verified independently before merging.
