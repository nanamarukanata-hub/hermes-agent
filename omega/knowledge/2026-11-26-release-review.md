# OMEGA release review — 2026-11-26 UTC

Status: verified upstream metadata; adoption proposed, not deployed.

## Material changes
- [Claude Code v2.1.332](https://github.com/anthropics/claude-code/releases/tag/v2.1.332), 2026-11-25 UTC: beta MCP-delivered skills and a new PreSkillShellCommand hook; older clients may display a startup dialog for this hook. Keep remote skills untrusted and require explicit review of shell commands. SSE reconnect and excessive output handling fixes are useful for unattended development.
- [Hermes v0.23.4](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4), 2026-11-24 UTC: stable rollup of 229 PRs and 1,316 changed files; curated notes deferred to v0.24.0. Installer-based desktop app is unchanged. Do not bulk merge; isolate gateway migration, plugin configuration and managed security settings for regression testing.
- [GBrain v0.60.772.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.772.0), 2026-11-25 UTC: enrichment now restricts evidence to data visible to all target-page readers; member source access tightened; refusal fallback disabled by default; conflict detection proposes changes by default; held-deletion handling changes. Keep private data out of shared enrichment and test with synthetic identities. Do not change production sharing or deletion settings.
- Codex and Obsidian latest non-prerelease tags unchanged.

## Standards and product surfaces
[MCP Skills extension](https://modelcontextprotocol.io/extensions/skills/overview) is documented; Claude's beta support does not imply all clients support it. [MCP anniversary post](https://blog.modelcontextprotocol.io/posts/mcp-turns-two/) dated 2026-11-24 is not itself a protocol release. [Agent Skills evaluation guidance](https://agentskills.io/skill-creation/evaluating-skills) supports task-based regression evaluation. No new mandatory protocol migration verified. [ChatGPT release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) reviewed; no new dated Work change independently established in this run.

## Adoption gates
- Synthetic multi-user access matrix: private facts must never appear in shared enrichment, including after permission changes.
- Gateway restart, session persistence and plugin configuration tests before Hermes adoption.
- Remote skill provenance, shell denial and old-client compatibility tests before enabling MCP-delivered skills.
- DTM value is development reliability for JUCE/VST3, KSP and MIDI tooling, not measured audio quality or DSP throughput.
- Repository license metadata: Codex Apache-2.0, Hermes and GBrain MIT; Claude Code and Obsidian repository SPDX not established by this check. No third-party code copied; dependency and tag-specific license audit remains pending.
- Bridge PR #8 still documents SQLite FTS5 and is not actual GBrain integration. Do not merge as a SQLite-free design.
- PR #10 prior head had zero returned PR workflow runs and zero combined status entries; this is not a passing CI result. Documentation-only change; runtime tests not run. Rollback: revert the documentation commits through review. No private vault access, installation or global settings changes.
