# OMEGA release review — 2026-11-20 (JST)

Status: **verified public upstream release metadata; documentation-only proposal**. Comparison baseline: 2026-11-19 radar. GitHub REST `releases/latest` was checked directly on 2026-11-20.

## Confirmed deltas

- **Claude Code v2.1.327 → v2.1.329** ([v2.1.328](https://github.com/anthropics/claude-code/releases/tag/v2.1.328), [v2.1.329](https://github.com/anthropics/claude-code/releases/tag/v2.1.329)): v2.1.328 fixes scheduled-task-file loss on unreadable settings, resumed-cloud-session summary loss, MCP schema compatibility and multiple Code Mode continuation defects. v2.1.329 fixes `~/.claude.json` reset on read failure, MCP auth-refresh/logout races, background MCP reconnect, and Code Mode memory runaway; adds folder-hook trust warnings and MCP error visibility. **Priority: high** for unattended workflows and secrets integrity. Test with synthetic config, simulated file-lock/network interruption, and mock MCP before enabling new permission/Code Mode behaviors.
- **GBrain v0.60.739.0 → v0.60.743.0** ([release](https://github.com/garrytan/gbrain/releases/tag/v0.60.743.0)): encrypted secret-store backup and `gbrain doctor` health checks; owner-scoped remote MCP without a room can read private/held facts like local CLI (behavior expansion, not safe for shared credentials); page purge and trust-tier rename fixes; OpenClaw skill resolver now reads the current `event.prompt`. **Priority: critical privacy**. Known limit: purging a page does **not yet** purge encrypted originals from the secret store. Do not enable owner credentials for multi-user RP or export a private Vault. Test isolation and retention on synthetic data only. `gbrain setup openclaw` must be rerun for the fixed plugin; not applicable unless OpenClaw is deployed.

## No confirmed stable-version delta

- [Codex rust-v0.170.0](https://github.com/openai/codex/releases/tag/rust-v0.170.0), [Hermes Agent v0.23.2](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.2), [Obsidian v1.15.1](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.15.1) unchanged in official GitHub latest endpoint.

## Compatibility, licensing and DTM value

- Changes are documented, **not installed**. No third-party code, model weights or licenses copied. Recheck upstream licenses before any code adoption.
- Claude Code's config and MCP resilience benefits unattended Kontakt/KSP, JUCE/VST3 and audio tooling code generation; no demonstrated direct DSP, synthesis or mixing-quality improvement.
- GBrain's encrypted backups could protect production notes, presets, MIDI mappings and session metadata, but the remote-owner read expansion and incomplete purge of encrypted originals create privacy/retention risks. Keep private Obsidian content out of public GitHub.
- ChatGPT, Work, Agent Skills and MCP protocol: no independently verified mandatory migration in this pass; **not** a claim of exhaustive release-note review.

## Scope, tests, rollback, outstanding

- Updated `omega/knowledge/latest-releases.md` and created this review on the existing **Draft PR #10** branch. No source, Skill, workflow, binary or Vault edits.
- Verified five official release records and source Markdown/Skill/workflow. Markdown static checks and post-write readback required; no unit/e2e execution because no code was changed. CI status remains to be checked separately.
- `omega/docs/gbrain-obsidian-bridge.md` is absent from this PR branch (404); reconcile with PR #8 and SQLite-free persistence plan. Do not silently create it.
- Rollback: revert the two documentation commits. No runtime rollback required.
