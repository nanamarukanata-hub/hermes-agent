---
type: release-review
date_utc: 2026-11-07
status: verified-public-release; compatibility-not-tested
---

# OMEGA release review — 2026-11-07

## Verified changes

- **GBrain v0.60.587.0**, published 2026-11-06 UTC: [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.587.0). HTTP MCP callers may provide `X-Gbrain-Agent-Label`; attribution/history views show the caller-declared `agent_label`. **The label is unverified text, not an identity or authorization claim**. It does not change grants or trust tiers. Treat as audit context only, never an access-control input.
- **GBrain v0.60.586.0**, published 2026-11-06 UTC: [official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.586.0). `remember` validation errors now show the valid call shape and require provenance, reducing retries for MCP agents. Do not weaken provenance requirements to improve success rate.
- **Hermes Agent v0.23.0**, **Claude Code v2.1.318**, **Codex rust-v0.166.0**, and **Obsidian v1.15.0**: no newer stable release identified in the checked official release listings. [Hermes](https://github.com/NousResearch/hermes-agent/releases), [Claude Code](https://github.com/anthropics/claude-code/releases), [Codex](https://github.com/openai/codex/releases), [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases). Obsidian channel availability must be checked separately.
- ChatGPT/Work, [MCP](https://modelcontextprotocol.io/specification), and [Agent Skills](https://agentskills.io/specification): no confirmed migration requirement for OMEGA in this review; this is not a claim that all announcements were exhaustively surveyed.

## Security, compatibility, licensing and audio value

GBrain attribution is useful for tracking which named agent submitted edits to Kontakt/KSP, JUCE/VST3, MIDI or mixing knowledge, but a malicious or mistaken client can spoof the label. Preserve OAuth identity, source provenance and independent audit logs. New `remember` error hints improve reliability without adding audio DSP functionality.

Do not copy implementation code without checking the applicable license and dependencies. No application, model, plugin, MCP server or private Vault content was installed or modified.

## Change control and outstanding

Only public Markdown metadata/review updated. No Python, Skills or workflow code changes; unit/integration tests not run. GitHub PR checks and runtime compatibility remain unverified. Rollback: revert the documentation commits.

The inspected branch lacks `omega/docs/gbrain-obsidian-bridge.md`; evaluate its separate PR before integration. Continue SQLite-free design reconciliation, Hermes gateway/session isolation tests, GBrain RLS and per-user storage tests, and CI Contributor Attribution investigation. Do not merge automatically.
