---
type: omega-release-review
date: 2026-11-05
status: verified-public-release-review
scope: public-only
---

# OMEGA release review — 2026-11-05 (UTC)

## Verified upstream changes

Compared against the 2026-11-04 release radar:

| Project | Previous | Current | Assessment |
|---|---|---|---|
| [Codex](https://github.com/openai/codex/releases) | rust-v0.166.0 | rust-v0.166.0 | Stable unchanged; 0.167/0.168 alpha builds excluded |
| [Claude Code](https://github.com/anthropics/claude-code/releases/tag/v2.1.318) | v2.1.317 | **v2.1.318** (Nov 4) | Permission/audit trace and mod model-effort controls |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.7) | v0.22.6 | **v0.22.7** (Nov 3) | Large patch rollup; compatibility validation needed |
| [GBrain](https://github.com/garrytan/gbrain/releases/tag/v0.60.568.0) | v0.60.567.0 | **v0.60.568.0** (Nov 3) | Embedded engine storage isolation; migration risk |
| [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.15.0) | v1.15.0 | v1.15.0 | No new GitHub release; distribution channel caveat |

## Priority 1 — GBrain embedded file storage isolation

[Official v0.60.568.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.568.0) makes file operations on engines opened from settings use the selected brain's `settings.storage` rather than process-wide storage. Supports local or explicitly configured S3 endpoints with optional one-segment key prefix. Missing backend settings fail with `engine_settings_invalid` instead of using ambient AWS credentials. A new `file_verify` checks object existence/hash, but only on embedded engines; it is not an MCP tool. Unreachable storage yields `store_unavailable`, not a false missing-file result.

**Critical migration caveat:** Files uploaded by embedded engines before this version may still physically reside in the old process store. The new `file_url` and `file_verify` look in per-brain storage, and purge can report `blobs_remaining`. Do not equate missing objects in the new store with successful deletion or migrate live private Vault material without a verified inventory and rollback. Test two synthetic brains, distinct storage roots and S3 prefixes, object URL/hash, denied fallback, purge, and old-store recovery before deployment.

**DTM value:** separation of private Kontakt/KSP scripts, JUCE/VST3 code, mix references and audio assets between agents; no verified audio/DSP improvement.

## Priority 2 — Hermes Agent v0.22.7

[Official release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.7) rolls up approximately 174 PRs and 581 non-merge commits across 554 changed files since v0.22.6. Highlights include group-chat routing/cron, plugin-catalog additions, Telegram shared gateway setup, and an opt-in Daybreak route for catalog-listed ChatGPT subscription models. Full curated notes are deferred to v0.23.0. CLI and Docker release channels update; installer-built Desktop app is explicitly unchanged in this patch.

**OMEGA compatibility gate:** do not auto-upgrade the customized Hermes Discord gateway or resident worker. First test gateway RP hooks, cron missed-run policy, plugin permissions, concurrent sessions, Obsidian persistence, restart recovery and any subscription-model authentication on an isolated clone. Model listing is not proof of free API entitlement.

## Priority 3 — Claude Code v2.1.318

[Official release](https://github.com/anthropics/claude-code/releases/tag/v2.1.318) adds a record of the user's response when asked to enable mods written during a session; `assume_role` on mantle AWS upstreams for cross-account roles/cost attribution; and a per-fork `effort` option for mod `$.model.fork`. Validate that permission records do not leak private project details into public logs and that lower-effort side jobs still pass KSP/JUCE regression checks. No production configuration changes proposed.

## Other surfaces and policy

Codex remains stable at 0.166.0; alpha releases are not adopted. Obsidian GitHub release remains 1.15.0; check Catalyst vs public channel and plugin compatibility before deployment. Reviewed project instructions `omega/AGENTS.md`, update Skill, and release workflow. No verified mandatory migration for OMEGA's Agent Skills/MCP configuration; official references: [ChatGPT release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes), [MCP specification](https://modelcontextprotocol.io/specification), [Agent Skills specification](https://agentskills.io/specification). Product availability should not be inferred from repository release tags.

Licenses: Codex Apache-2.0, Hermes Agent MIT, GBrain MIT as indicated by upstream repository license information. Claude Code and Obsidian have separate vendor terms. No upstream code or binary is redistributed.

## Validation, scope and rollback

- Five official GitHub release pages and existing OMEGA instructions, release radar and workflow inspected. Version rows and official source URLs checked.
- Only public Markdown metadata/review updated on existing Draft [PR #10](https://github.com/nanamarukanata-hub/hermes-agent/pull/10); no code, Skills, workflow, root AGENTS.md, private Vault or global software modified.
- No application unit/integration/runtime tests run because no executable files changed. Documentation static checks are not equivalent to GitHub CI.
- The requested `omega/docs/gbrain-obsidian-bridge.md` is absent on this branch; review [PR #8](https://github.com/nanamarukanata-hub/hermes-agent/pull/8) separately, including its SQLite FTS5 conflict with the SQLite-free requirement. [PR #9](https://github.com/nanamarukanata-hub/hermes-agent/pull/9) remains separately gated.
- Existing CI/Contributor Attribution failures and release-radar automated PR permissions remain unverified/resolution pending. No merge.
- No private Google Drive Vault content read or changed; no personal notes or credentials published.
- Rollback: revert the two documentation commits on the draft PR branch.
