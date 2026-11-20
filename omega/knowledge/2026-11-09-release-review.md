# OMEGA official release review — 2026-11-09 JST

Status: **verified public upstream release notes; documentation-only change; no deployment**.

## Changes since 2026-11-08 radar

- **Claude Code v2.1.319**, published 2026-11-06 UTC: [official release](https://github.com/anthropics/claude-code/releases/tag/v2.1.319). Adds background handling for MCP tasks, `CLAUDE_CODE_DISABLE_MCP_TASK_BACKGROUND` to preserve synchronous waiting, and `permissions.denyInsteadOfPrompting`. Auto mode with telemetry/feature flags disabled or behind gateways now asks or denies uncheckable calls rather than falling back to a local classifier; managed gateway settings may need explicit review. **Priority high** for unattended agent runs; test permission denial, asynchronous task completion, cancellation and gateway policy before adoption. License/terms: do not assume this is an Apache/MIT drop-in replacement.
- **GBrain v0.60.619.0**, published 2026-11-08 UTC: [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.619.0). Managed `git commit-push` no longer claims an unpushed local commit was pushed; reports `unpublished` and upstream without changing exit code. This corrects observability, **not** publication. Check the persistence owner before relying on remote backups.
- **GBrain v0.60.617.0**: [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.617.0). `doctor url_source_origin` identifies legacy URL sources without provenance; `sources record-origin ... --remote|--local` previews unless `--apply`. Remote-added sources get safer reclone settings; git-lfs sources can be refused. **Priority high** for old private Vault/source setups: inventory and manually classify, never guess remote/local.
- **GBrain v0.60.613.0**, [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.613.0): ingest-capture idempotency prevents replayed captures overwriting newer edits, with 90-day applied-key retention. v0.60.612.0 [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.612.0) fixes erroneous fact retirement and adds preview-first repair `unwithdrawn-facts`; avoid running repair without isolated backup and review. v0.60.609.0 [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.609.0) reduces GitHub webhook timing disclosure and corrects ingest backlog/slug responses; v0.60.610.0 fixes legacy capture-key retry. **Priority high** for HTTP/MCP ingestion and memory integrity.
- **GBrain v0.60.618.0** [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.618.0): refresh can replan only for its own ledgered, published commits; foreign or unpublished commits still refuse. `persistence.refresh_barrier` remains opt-in. No automatic enabling.
- Codex remains `rust-v0.167.0`; Hermes Agent remains `v0.23.0`; Obsidian GitHub remains `v1.15.0`. No new stable release delta identified for those three.

## OMEGA impact and controls

1. **Security:** prioritize testing GBrain HTTP ingress, webhook HMAC, source provenance, private-page ACLs and legacy reclone behavior. Do not publish or copy private Obsidian notes, credentials, OAuth state or personal paths.
2. **Compatibility:** Hermes gateway/session persistence and Discord RP remain unverified against v0.23.0. Claude MCP background tasks can change call ordering; use explicit synchronization where state must be committed before the next step. Windows and Linux testing required before production adoption.
3. **DTM/audio:** highest practical benefit is reliable retention of Kontakt/KSP, JUCE/VST3, MIDI, mixing decisions and provenance, not direct DSP or sound-quality changes. Validate versioned facts and revisions with synthetic test notes, not real Vault material.
4. **Licensing:** re-check each upstream LICENSE and applicable hosted-service terms before copying code or packaging a Skill. Release metadata and hyperlinks alone do not transfer code licenses.
5. **Rollback:** keep current pinned deployments, independent backups and a reversible test branch. Any migration/repair or source classification requires operator review.

## Execution and limitations

Reviewed `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, release radar, and `.github/workflows/omega-release-radar.yml` on PR #10 branch. Requested `omega/docs/gbrain-obsidian-bridge.md` was not available on this branch. No code, Skills, workflow, production service, private Drive Vault, ChatGPT or plugins changed. No runtime/compatibility tests executed; CI pass is **not** asserted. Open PR #10 remains draft. Review upstream ChatGPT/Work, Agent Skills and MCP official documentation separately for announcements not captured by GitHub release radar.
