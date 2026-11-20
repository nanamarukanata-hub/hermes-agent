# OMEGA release review — 2026-11-13 (JST)

Status: **verified upstream release metadata; proposed evaluation only; no deployment**.

## Changes since 2026-11-12

- **Claude Code v2.1.323** (2026-11-12 22:12 UTC): [official release](https://github.com/anthropics/claude-code/releases/tag/v2.1.323). Adds opt-in experimental Code Mode, `plugin.json` `engines["claude-code"]` version compatibility gates, TypeScript hook validation warnings (`--strict` fails), and 30-second long-hook heartbeats. Fixes multiple cross-account/profile background-session credential and routing issues, MCP token-expiry handling, and transcript/session resume bugs. **Priority: high** for unattended agents and credential isolation. Do not enable Code Mode or install plugins without review. Test plugin engine ranges, strict hook validation, profile isolation, MCP re-authentication and resumptions in a disposable environment before upgrade.
- **GBrain v0.60.705.0** (2026-11-12 21:02 UTC): [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.705.0). Switches future release engineering to batched release PRs and changelog fragments, with CI and release stamping changes. Intermediate releases merit more immediate attention:
  - [v0.60.702.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.702.0): opt-in `gbrain access member labelled-only <id> on` restricts member credentials to explicitly labelled pages, including derived data. **Off by default**. High relevance to multi-user Discord RP and Obsidian privacy; test read/write denial and derived lineage before considering activation.
  - [v0.60.700.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.700.0): fixes a concurrent Git commit race that could revert another writer's page edit. **High priority** for Obsidian/Git synchronization. Reproduce using disposable concurrent edits, not private Vault.
  - [v0.60.695.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.695.0): fixes OAuth table-name collisions with other applications sharing PostgreSQL. Avoid shared schemas; test migrations on a disposable database.
  - [v0.60.691.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.691.0): fixes database-only privacy label repair being reverted by Git sync; offers explicit untracking. **Do not execute repair/untrack against real Vault without backup and dry-run.**
  - [v0.60.684.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.684.0): `GBRAIN_ENFORCED_MEMBER` limits a local agent's read view to one member, with constrained writes. Review launcher isolation before enabling.
- **Obsidian v1.15.1** (2026-11-12 15:43 UTC): [GitHub release](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.15.1) and [official changelog](https://obsidian.md/changelog/2026-11-12-desktop-v1.15.1/). New upstream tag; precise fixes and general-vs-Catalyst distribution not independently verified. Review Community Plugin, Mermaid/Pele and Bases compatibility before updating.
- **Codex** remains `rust-v0.167.2`; **Hermes Agent** remains `v0.23.1`. Alpha Codex tags excluded.

## Compatibility, security, licensing and DTM value

Highest value is **preserving private session boundaries and preventing lost/overwritten notes**. Relevant assets include Kontakt/KSP, JUCE/VST3 source notes, MIDI workflows, sound-library design, mix/mastering settings and RP state. None of these releases is evidence of improved audio DSP quality or mixing sound. Claude plugin engine constraints and hook validation can prevent incompatible automation; Code Mode changes the execution surface and needs explicit review. GBrain ACL and Git fixes need realistic concurrency and cross-member tests. External code, Skills and MCP servers were **not copied or executed**, so no new redistribution-license obligation was introduced; license compatibility must be checked before any future code import.

## Local OMEGA status / checks

- Reviewed `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, `.github/workflows/omega-release-radar.yml`, and `omega/knowledge/latest-releases.md` in PR #10's branch.
- `omega/docs/gbrain-obsidian-bridge.md` returned 404 on that branch. PR #8 / SQLite-free persistence design remains unresolved.
- Changed only this review and the release metadata; **no Python, Skills, workflow, deployment, private notes, credentials, or model files**.
- Official GitHub latest-release API 5/5; compared version/date with previous radar. Static text review for secrets: no credentials included. Runtime unit/integration tests **not run**, because no code changed; CI result must be checked separately.
- PR #10 remains draft; do not merge until CI and compatibility gates pass.
- Rollback: revert these two Markdown commits. No Vault modifications or software installations to roll back.
