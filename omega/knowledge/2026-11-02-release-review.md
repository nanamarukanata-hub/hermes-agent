# OMEGA official release and security review — 2026-11-02 JST

Status: **verified official upstream metadata; documentation-only proposal**. No application, Skill, model, MCP server, database, private Vault or ChatGPT global configuration was updated.

## Latest stable GitHub releases

| Project | Stable release | Published UTC | Change since 2026-11-01 review |
|---|---|---|---|
| [Codex](https://github.com/openai/codex/releases/tag/rust-v0.166.0) | `rust-v0.166.0` | 2026-10-29 19:47 | Unchanged |
| [Claude Code](https://github.com/anthropics/claude-code/releases/tag/v2.1.316) | `v2.1.316` | 2026-10-31 01:01 | Unchanged |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5) | `v0.22.5` | 2026-10-23 05:28 | Unchanged |
| [GBrain](https://github.com/garrytan/gbrain/releases/tag/v0.60.542.0) | `v0.60.542.0` | 2026-11-01 23:31 | **New; security, trust and SkillOpt changes in release series** |
| [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.14.5) | `v1.14.5` | 2026-10-22 21:19 | Unchanged |

All five records were checked against the official GitHub Releases API `/releases/latest`, excluding prereleases. UTC publication dates are not installed-version evidence.

## Priority 1 — GBrain quarantined facts and untrusted content

[Official v0.60.541.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.541.0) closes a read-eligibility gap: saved facts from quarantined pages and facts marked `needs_rederive` following purge were previously exposed via entity-fact search/query and other reads despite protections in recall and saved_facts. This release applies the read gate more consistently across remote MCP and local reads. It also quotes additional untrusted fields returned to remote agents and fixes some CRLF whole-page round trips.

[Official v0.60.536.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.536.0), [v0.60.538.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.538.0), and [v0.60.539.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.539.0) expand quotation/fencing of agent-written, ingested and unverified-origin content in MCP results, hooks, chronicle/timeline reads, titles and frontmatter. This reduces the chance of treating lower-trust memory as instructions, but **quote fences are a presentation boundary, not a substitute for authorization or prompt-injection testing**. Older owner-authored notes without origin metadata may be treated as unverified until a reviewed trust-source claim and backfill. Do not run trust backfill against the user's Vault automatically.

[Issue #7659](https://github.com/garrytan/gbrain/issues/7659), concerning scoped-writer RLS trigger design, was still open when checked on 2026-11-02 JST. Do not declare the permission boundary fully resolved. Previously published private pages or Git history still need separate audit.

**OMEGA adoption gate:** test synthetic per-user sources, quarantined/purged fact visibility across `search`, `query`, `recall` and MCP; validate trust fencing and Markdown/CRLF round trips; test scoped RLS roles against a disposable DB. Never grant a shared Discord token broad access to private member sources.

## Priority 2 — GBrain SkillOpt default behavior changes

[Official v0.60.542.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.542.0) makes a **single gated SkillOpt rewrite** the default. The former iterative optimizer requires `--loop` or `optimizer_mode: "reflect"`. Existing queued jobs without a mode retain their queued behavior, and resume keeps the checkpoint's mode. The optimizer now sees training criteria, not held-out answers; the final candidate is tested before a write and rejected if it does not beat baseline selection performance.

The developer's Cat 31 held-out re-run scored **0.54 for both** one-shot and full loop; the loop used approximately **1.8x** the cost. This is an upstream benchmark, not independent proof of equal quality for OMEGA, Japanese RP, Kontakt/KSP, JUCE/VST3 or mixing. The benchmark library now offers explicit train/selection/test task sets and rollout hooks. A Skill may explicitly request reviewed read-only tools `recall` and `get_timeline` during rollouts, but not write tools.

**OMEGA decision:** do not run `gbrain skillopt`, dream-cycle rewrites, or automatic Skill mutations against production Skills or the private Vault. If evaluated later, use a disposable Skill copy, synthetic train/selection/held-out tasks, a fixed cost budget, explicit approval and a reviewed diff/rollback. Preserve a read-only holdout and verify no private notes enter optimizer prompts.

## Other changes in the GBrain release series

- [v0.60.540.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.540.0) reuses unchanged fenced-page embedding vectors, avoiding repeated embedding of every chunk on fact/timeline writes. One reporter saw 13–15 million embedding tokens/day before the fix; this is not a measured OMEGA saving.
- [v0.60.537.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.537.0) prevents ownership-marker refusals in one source from indefinitely consuming shared sync slots and starving other sources.
- [v0.60.533.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.533.0) reports delegated sync completion only after markers settle and deferred embeddings are queued. This does not mean vector search is already fully populated.

## ChatGPT, Work, Codex, Claude Code, Hermes, MCP, Agent Skills, Obsidian

- [OpenAI ChatGPT release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes): latest listed entry remains **2026-10-30** account switching on mobile; **2026-10-29** Work/Codex allowance adjustments apply only to specified plans. No new product release requiring an OMEGA change found.
- [Codex v0.166.0](https://github.com/openai/codex/releases/tag/rust-v0.166.0), [Claude Code v2.1.316](https://github.com/anthropics/claude-code/releases/tag/v2.1.316), [Hermes v0.22.5](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5), [Obsidian v1.14.5](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.14.5): no newer stable releases in this run. Prior Windows permission, hook, and MCP security review gates still apply.
- [MCP specification](https://modelcontextprotocol.io/specification) still resolves to **2026-07-28**; [Agent Skills specification](https://agentskills.io/specification) still requires `SKILL.md` with valid `name` and `description` frontmatter. The existing `omega-ai-updates` Skill meets those required structural fields; no migration justified.
- Official licenses inspected: [Codex Apache-2.0](https://github.com/openai/codex/blob/main/LICENSE), [Hermes MIT](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE), [GBrain MIT](https://github.com/garrytan/gbrain/blob/master/LICENSE), and [Claude Code commercial terms](https://github.com/anthropics/claude-code/blob/main/LICENSE.md). These do not authorize publishing user notes or third-party licensed material.

## DTM and audio engineering impact

The practical gains are safer shared-memory retrieval, more reliable fact corrections, potentially lower embedding API cost and safer Skill benchmarking for Kontakt/KSP, JUCE/VST3, MIDI tooling, sample-library catalogs and mixing project notes. No upstream release provides measured improvements to DSP fidelity, plugin sound, mixing quality, real-time audio latency or OMEGA inference speed. Do not equate SkillOpt's benchmark with audio task performance.

## Repository, validation, deployment and rollback

- Checked `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, `omega/knowledge/`, and `.github/workflows/omega-release-radar.yml` on PR #10's branch. `omega/docs/gbrain-obsidian-bridge.md` exists on [PR #8](https://github.com/nanamarukanata-hub/hermes-agent/pull/8), **not** PR #10; it documents a SQLite FTS5 cache and conflicts with the separate SQLite-free Hermes persistence objective. [PR #9](https://github.com/nanamarukanata-hub/hermes-agent/pull/9) remains a draft, not a verified device deployment.
- Documentation-only changes today: `omega/knowledge/latest-releases.md` and this new review. No Python, Skill or workflow source changes; no new unit/integration tests or device/DB tests are claimed.
- Previous PR #10 head `b0774664` CI completed with **Lint pass; Supply Chain Audit pass; Tests fail; Contributor Attribution Check fail**. New-head checks must be inspected; **do not merge** until required CI and architectural gates pass.
- The workflow's auto-PR creation permission remains an unresolved operational issue. Do not change repository settings or force-push unrelated branches.
- No private Google Drive/Obsidian content was copied, changed or deleted. No credentials or private paths were published. No ChatGPT app, model, global plugin or device software was installed/updated.
- Rollback: revert this review and the release-radar metadata commit on the draft branch. No runtime rollback is needed because nothing was deployed.
