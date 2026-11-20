# OMEGA official release and security review — 2026-10-31 JST

Status: **verified official upstream release pages; review only**. No app, model, plugin, Skill, MCP server, database, private Vault, or global ChatGPT configuration was upgraded.

## Latest stable releases (UTC)

| Project | Release | Published | Decision |
|---|---|---|---|
| [Codex](https://github.com/openai/codex/releases/tag/rust-v0.166.0) | `rust-v0.166.0` | 2026-10-29 19:47 | Unchanged since prior OMEGA review. |
| [Claude Code](https://github.com/anthropics/claude-code/releases/tag/v2.1.314) | `v2.1.314` | 2026-10-30 01:06 | **New: high-priority permission/policy changes.** |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5) | `v0.22.5` | 2026-10-23 05:28 | Unchanged; local integration still untested. |
| [GBrain](https://github.com/garrytan/gbrain/releases/tag/v0.60.517.0) | `v0.60.517.0` | 2026-10-30 20:45 | **New: storage fail-closed fix and improved test reliability in this release series.** |
| [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.14.5) | `v1.14.5` | 2026-10-22 21:19 | Unchanged; desktop security update still needs separate device-side review. |

## Official change analysis and adoption gates

### Claude Code v2.1.314 — high priority for automated agents
- New `permissions.blockWritesOutsideWorkingDirectories` prevents Write, Edit and NotebookEdit outside working directories in every permission mode. **Proposed policy, not enabled here**. Test path canonicalization, symlinks, Windows path handling, allowed worktrees and legitimate temporary build outputs before adopting.
- Remote managed `forceRemoteSettingsRefresh` now applies after the cached policy expires; CI sessions with `CI` set refuse untrusted workspaces immediately. These fixes matter for unattended coding agents.
- MCP logout now reports a failure when credentials cannot actually be removed, and MCP servers usable without sign-in can work signed out after expired auth. Validate authorization boundaries and audit logging; anonymous access is not equivalent to authorized access to private notes.
- `claude -p` startup failure now exits nonzero rather than silently succeeding. This helps pipeline error detection.
- The release does **not** establish that OMEGA's Claude Code settings, MCP connections or devices were upgraded. [Official release](https://github.com/anthropics/claude-code/releases/tag/v2.1.314).

### GBrain v0.60.517.0 and notable intervening patches
- **v0.60.514.0 (2026-10-30):** redaction markers no longer trigger false positives in `gbrain transcripts audit-secrets`; detector version 3 requires a fresh audit. More importantly, `db_only` / `supabase_only` storage configuration that cannot be fully resolved now **fails closed**, and company-brain pages stored only in the database are protected from erroneous retirement. This is the most relevant data-integrity fix for a private Obsidian knowledge bridge. [Official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.514.0).
- **v0.60.512.0:** in `self_upgrade.mode auto`, the supervisor now restarts the managed serve after upgrading when it still runs the old build. Do not enable automatic upgrades for OMEGA without rollback, backup and permission checks. [Official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.512.0).
- **v0.60.513.0:** optimizes a projection-readiness query when many pages are pending; upstream performance measurements are not an OMEGA benchmark. [Official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.513.0).
- **v0.60.516.0 / 517.0:** improve the crash-robot test harness (failed Git commit detection, parallel writer runs, deterministic manifests) and split owner-journey tests to avoid CI timeouts. These are principally **upstream validation improvements**, not proof that user-facing search, privacy or persistence has been fixed in OMEGA. [Official 516](https://github.com/garrytan/gbrain/releases/tag/v0.60.516.0) / [517](https://github.com/garrytan/gbrain/releases/tag/v0.60.517.0).
- [GBrain issue #7659](https://github.com/garrytan/gbrain/issues/7659) remains **open** at review time: scoped-writer RLS guard behavior needs a security design pass. Do not connect production private knowledge or assume withdrawn facts are erased.

### Other sources, standards, license and DTM impact
- [ChatGPT official release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) most recently list the 2026-10-29 Work/Codex allowance change for specific Pro/Business Premium plans, 2026-10-28 dot texting/API credit changes and 2026-10-26 Terra availability. These do not imply an entitlement or software upgrade in this workspace.
- [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28) remains 2026-07-28; [Agent Skills specification](https://agentskills.io/specification) still documents `SKILL.md` metadata. No mandatory OMEGA Skill migration was identified.
- Official LICENSE files: [Codex Apache-2.0](https://github.com/openai/codex/blob/main/LICENSE), [Hermes Agent MIT](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE), [GBrain MIT](https://github.com/garrytan/gbrain/blob/master/LICENSE). Claude Code distribution, hosted services, Obsidian and transitive dependencies have their own terms; no third-party code was copied.
- **Audio production value:** protected workspace writes reduce the chance of unintended changes to JUCE/VST3/KSP projects, plugin presets and DAW support files. Correct `db_only` handling reduces the risk of deleting production notes and revision records. Neither change demonstrates an improvement in sound quality, DSP precision or mix/mastering performance.

## OMEGA integration and CI status

1. [PR #8](https://github.com/nanamarukanata-hub/hermes-agent/pull/8) is unmerged and describes a **local SQLite FTS5** optional Obsidian index, **not** real GBrain. Its cache design conflicts with a strict SQLite-free architecture and must be reconciled before merge. [PR #9](https://github.com/nanamarukanata-hub/hermes-agent/pull/9) is unmerged/draft.
2. [PR #10](https://github.com/nanamarukanata-hub/hermes-agent/pull/10) is draft. On the previous head (`94731c0`), GitHub Actions **Lint** and **Supply Chain Audit** succeeded, while **Tests** and **Contributor Attribution Check** failed. These are real blockers; no CI green claim is made for the new head.
3. [Release Radar workflow](https://github.com/nanamarukanata-hub/hermes-agent/blob/omega/daily-maintenance-20261024/.github/workflows/omega-release-radar.yml) requests `contents: write` and `pull-requests: write`, but repository-level permission for Actions-created PRs was previously denied. The permission remains unmodified and must be separately verified.
4. Recommended isolated acceptance: (a) Claude write-scope checks including symlinks and Windows paths; (b) GBrain `db_only`/invalid YAML and source authorization tests on **synthetic** notes; (c) crash/rollback and transcript secret audit; (d) Hermes–MCP–Obsidian integration only after a separate test environment is ready.
5. **Today's changes:** only `omega/knowledge/latest-releases.md` and this public review; no code, Skills or workflow edits. Static five-source/date/URL/secret-pattern checks and GitHub file readback are planned. Python unit tests and live integration tests were **not rerun** because no executable code changed.
6. Rollback: revert the two Markdown commits on the draft PR branch. No merge to main; no private Vault content read, changed, deleted or copied to public GitHub.
