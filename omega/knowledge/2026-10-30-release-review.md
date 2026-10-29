# OMEGA official release and security review — 2026-10-30 JST

Status: **verified official upstream release pages; review only**. No application, model, plugin, Skill, MCP server, GBrain database, private Vault, or global ChatGPT configuration was upgraded.

## Latest stable releases and decisions

| Project | Stable release (published UTC) | Decision |
|---|---|---|
| [Codex](https://github.com/openai/codex/releases/tag/rust-v0.166.0) | `rust-v0.166.0` (2026-10-29 19:47) | **New.** Approval/permission state survives resume and fork; `codex doctor --network-only`, `codex queue --resume` and GPT-6.1 Terra catalog support added. Test approval persistence and network behavior before deployment. |
| [Claude Code](https://github.com/anthropics/claude-code/releases/tag/v2.1.312) | `v2.1.312` (2026-10-27) | Unchanged. The removed `claude_code.safety_event` telemetry is not a usable audit source. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5) | `v0.22.5` (2026-10-23) | Unchanged. MCP/plugin authorization still requires synthetic integration tests. |
| [GBrain](https://github.com/garrytan/gbrain/releases/tag/v0.60.493.0) | `v0.60.493.0` (2026-10-29 17:24) | **New, high priority.** Default search behavior and source-provenance fixes merit review. Do not migrate live knowledge. |
| [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.14.5) | `v1.14.5` (2026-10-22) | Unchanged. Desktop security update requires a separate backed-up device-side operation. |

## Official change analysis

- **Codex 0.166.0:** Preserves acknowledged approvals and permissions on resume/fork, fixes thread startup/shutdown races, adds network-only diagnostics, and preserves reasoning-token accounting across resume/fork. Relevance: safer recovery of long JUCE/VST3/Kontakt/KSP coding sessions; no demonstrated audio-quality or DSP improvement. [Official release](https://github.com/openai/codex/releases/tag/rust-v0.166.0).
- **GBrain 0.60.493.0:** `search.saved_facts_supersede` is on by default. Stemmed saved-fact matching and same-entity, non-lower-trust correction cues rank newer corrections ahead of superseded claims, annotating older facts with `corrected_by`. CJK matching remains substring-based. Upstream reports 27/27 correction-case reads versus 1/27 previously in its keyless replay; this is **vendor-reported**, not independently validated. Test rollback with `gbrain config set search.saved_facts_supersede false` in a disposable brain. This does not rewrite stored facts. [Official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.493.0).
- **GBrain 0.60.491.0:** `search.query_facts_block` now defaults on for budgeted `query`, reserving up to 22% of budget for dated facts. Upstream reports 183/200 vs 151/200 held-out answers. Disable for comparison with `gbrain config set search.query_facts_block false`. Evaluate Japanese music-production queries, correction quality, latency and token cost before adoption. [Official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.491.0).
- **GBrain 0.60.485–0.489:** Private-visibility fail-closed paths, remote provenance protection and maintenance trust markers were improved. These changes do not automatically repair any earlier private-source disclosure. [Official release list](https://github.com/garrytan/gbrain/releases).
- **GBrain 0.60.492.0:** Exact-tree green GitHub CI results can be reused for upstream remote gating; this does not imply OMEGA CI has passed. [Official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.492.0).

## ChatGPT, Work, standards and licensing

- [OpenAI ChatGPT release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) published an October 29 increase in Work/Codex allowance for named Pro and Business Premium plans and October 28 US Pro beta dot texting/API-credit promotions. Eligibility is plan-specific; no account changes were performed.
- [MCP current specification](https://modelcontextprotocol.io/specification/2026-07-28) remains 2026-07-28. Preserve user consent, scoped tools and source-level authorization.
- [Agent Skills specification](https://agentskills.io/specification) still requires a `SKILL.md` with valid `name` and `description`. The existing `omega-ai-updates` Skill meets the structural minimum; no change is required.
- Source licenses: [Codex Apache-2.0](https://github.com/openai/codex/blob/main/LICENSE); [Hermes MIT](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE); [GBrain MIT](https://github.com/garrytan/gbrain/blob/master/LICENSE). Hosted products, Claude Code, Obsidian and dependencies have separate terms.
- **DTM relevance:** Higher-quality retrieval of corrected facts could reduce obsolete KSP API notes, wrong VST3 parameter names, stale MIDI mappings and outdated mix-revision context. Ranking does not prove a fact true; no verified change improves DSP fidelity or mix sound.

## Security acceptance, repo state and rollback

1. Use only synthetic multi-source brains to test CJK stemming/substrings, fact corrections and withdrawals, cross-entity isolation, lower-trust facts, five-fact cap, query budget and source authorization. Do not connect the private Vault.
2. [GBrain issue #7659](https://github.com/garrytan/gbrain/issues/7659) is **open** on 2026-10-30: scoped PostgreSQL writers can encounter fail-open purge/withdrawal guard behavior under RLS. Search changes do not resolve it.
3. [OMEGA PR #8](https://github.com/nanamarukanata-hub/hermes-agent/pull/8) is an unmerged **local SQLite FTS5** Obsidian bridge, not GBrain itself. Reconcile its design with any SQLite-free Hermes deployment before merging. [PR #9](https://github.com/nanamarukanata-hub/hermes-agent/pull/9) is also unmerged/draft.
4. [Release Radar PR #10](https://github.com/nanamarukanata-hub/hermes-agent/pull/10) remains draft. Previous head's Tests and Contributor Attribution checks failed; Lint and Supply Chain Audit passed. Verify new head CI independently. Automated PR creation permissions in the workflow are unresolved.
5. Today only `omega/knowledge/latest-releases.md` and this public review are proposed. Static checks: five allowlisted sources, version/date format, public URLs, secret patterns, Markdown file readback. No code/Skill/workflow change, no Python tests rerun and no live integration test.
6. Rollback by reverting these Markdown-only commits on the PR branch or closing PR #10. No merge to main or changes to private notes, credentials or user software.
