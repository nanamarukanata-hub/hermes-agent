# OMEGA upstream review — 2026-11-21 (JST)

Status: **official upstream metadata verified; deployment NOT performed**. Baseline: 2026-11-20 radar. Sources are public GitHub release records; release tags are not local installed versions.

## Verified changes
| Project | Prior | Current | Published UTC | Official |
|---|---|---|---|---|
| Codex | rust-v0.170.0 | rust-v0.171.0 | 2026-11-20 00:44 | https://github.com/openai/codex/releases/tag/rust-v0.171.0 |
| Claude Code | v2.1.329 | v2.1.330 | 2026-11-20 22:32 | https://github.com/anthropics/claude-code/releases/tag/v2.1.330 |
| Hermes Agent | v0.23.2 | v0.23.3 | 2026-11-20 00:34 | https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.3 |
| GBrain | v0.60.743.0 | v0.60.748.0 | 2026-11-19 22:48 | https://github.com/garrytan/gbrain/releases/tag/v0.60.748.0 |
| Obsidian | v1.15.1 | v1.15.1 | 2026-11-12 15:43 | https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.15.1 |

## Priority and actionable risk
1. **GBrain — HIGH / privacy**. Unspecified `brain.audience` now counts distinct people behind MCP clients rather than client count; check explicit audience and member bindings before sharing. Owner-only-derived takes and timeline rows now remain hidden across more read paths. Release notes document 24 pinned sharing-contract exceptions (8 archive rescope pending; 16 direct library member-add paths). Treat these as known test limitations, not proof of perfect isolation. A Google model alias/default changed to gemini-3.5-flash-lite; pin model IDs where reproducibility matters. Local llama.cpp/Ollama context overflow is classified as request_too_large, useful for local music-production knowledge retrieval. Do not auto-enable shared access or run repair commands on private Vault.
2. **Claude Code — HIGH / integrity**. v2.1.330 fixes worktree deletion while another session uses it, concurrent settings/task/plugin updates, MCP server lifecycle shared by subagents, and incorrect restoration via rewind. Also fixes long-compaction and MCP schema failures. Test with disposable worktrees and mock MCP servers before using concurrent audio-tool code generation. Built-in easel charts are platform/plan constrained (macOS/Linux), not a Windows DTM upgrade.
3. **Codex — MEDIUM / security and workflow**. v0.171.0 enables the TUI workspace by default, adds `codex exec --remote`, and fixes permission profile persistence and approval races. Rust 1.99.1 and XML dependency updates affect source builders. Test trusted-directory boundaries and remote endpoint authentication before adoption.
4. **Hermes Agent — MEDIUM / compatibility**. v0.23.3 is a large patch rollup; prompt-cache lifecycle, proactive tool pruning, archived tool results, and a persistence abstraction (still includes SQLite backend) can affect long-running Discord/Obsidian bridges. This upstream SQLite implementation is not a mandate to reintroduce SQLite into OMEGA. Dependency security bumps are noted, but specific CVE closure is not independently established. Test gateway restart, user separation, and archival on synthetic sessions.
5. **Obsidian — no GitHub stable-release change**. Catalyst/general distribution and plugin compatibility still require separate checks.

## ChatGPT, Work, Agent Skills, MCP, DTM assessment
No new mandatory migration for these surfaces established in this review; **not an exhaustive no-change assertion**. Music relevance is indirect: more reliable multi-agent KSP/JUCE/VST3 code editing, versioned DSP experiments, audio asset metadata, and secure private project notes. No evidence of direct improvement to synthesis, audio fidelity, latency or mixing algorithms. No external code, Skills, models, or plugins imported; therefore no new third-party redistribution obligations incurred. Before importing any upstream implementation, inspect LICENSE, transitive licenses and compatibility.

## Repo and safety
Read `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, `omega/knowledge/latest-releases.md`, and `.github/workflows/omega-release-radar.yml`. `omega/docs/gbrain-obsidian-bridge.md` is absent from this branch (404); reconcile separately with PR #8 and the SQLite-free design. Public documentation only; no private Obsidian content, credentials, personal information, or private Google Drive content used. No runtime software, global ChatGPT plugins, Skills, or MCP registrations updated.

**Tests**: official GitHub releases/latest API 5/5 retrieved; documentation-only change. Unit/e2e and actual integration tests **not run**; CI must be checked on new HEAD. **Rollback**: revert these Markdown-only commits. PR #10 remains Draft; do not merge without CI and design review.
