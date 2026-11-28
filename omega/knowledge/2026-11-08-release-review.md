---
type: release-review
date_utc: 2026-11-08
status: verified-public-release; compatibility-not-tested
---

# OMEGA release review — 2026-11-08

## Verified official release changes

- **GBrain v0.60.601.0 (2026-11-07 UTC): security priority P0 for HTTP/MCP deployments.** [Official notes](https://github.com/garrytan/gbrain/releases/tag/v0.60.601.0). Fixes unauthorized overwrite via `POST /ingest` of other members' or private pages, remote `search_by_image` reading local host files, unsigned webhook metadata disclosure, and remote-added Git clone/LFS network contact. These issues affect exposed HTTP/MCP surfaces, **not default keyless stdio-only installations**. The vendor reports regression tests; OMEGA has not independently reproduced them.
- **GBrain v0.60.603.0 (2026-11-07 UTC):** [Official notes](https://github.com/garrytan/gbrain/releases/tag/v0.60.603.0). Managed brain clone/reclone preserves remote-added source hooks-off/LFS-off settings; frontmatter install-hook does not suggest re-enabling disabled hooks. Previously added URL sources may need an explicit audit/re-add; never re-add private sources automatically.
- **GBrain v0.60.608.0 (2026-11-07 UTC):** [Official notes](https://github.com/garrytan/gbrain/releases/tag/v0.60.608.0). Refresh write barrier ordering and conflict protections; `persistence.refresh_barrier` remains **off by default**. Do not enable without concurrency, recovery and rollback tests.
- **Codex rust-v0.167.0 (2026-11-06 UTC):** [Official notes](https://github.com/openai/codex/releases/tag/rust-v0.167.0). Agent command center, backend switching, local image previews, managed-network approval documentation and session/compaction fixes. Not an automatic upgrade of ChatGPT, Work or Codex installations.
- **Claude Code v2.1.318**, **Hermes Agent v0.23.0**, **Obsidian v1.15.0**: no newer stable release found in the checked official release listings ([Claude](https://github.com/anthropics/claude-code/releases), [Hermes](https://github.com/NousResearch/hermes-agent/releases), [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases)). Obsidian distribution channel remains a separate check.
- ChatGPT/Work [release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes), [MCP specification](https://modelcontextprotocol.io/specification), [Agent Skills specification](https://agentskills.io/specification): no independently verified mandatory OMEGA migration from this review. This is not exhaustive coverage of all announcements.

## Security, license, compatibility and DTM value

**Recommendation:** Do not expose a GBrain HTTP/MCP endpoint to additional users or ingest private Obsidian content until the deployed version, OAuth scopes, per-user ACLs, image URL validation, webhook signatures, and remotely added Git source handling have been audited. Back up and use a disposable test Brain for authorization-negative tests. Upstream fixes do not prove an already-running instance has been upgraded, nor do they remediate previously exposed information.

For Kontakt/KSP, JUCE/VST3, preset and mixing notes, unauthorized cross-user overwrite and file exfiltration risks outweigh the convenience of remote ingestion. Codex image preview and session improvements may help plugin UI review, but have no established DSP/audio-quality effect. Confirm upstream license and transitive dependency obligations before copying code; this review copies no implementation code.

## Change control, tests and rollback

Only public Markdown release metadata and review changed. No application upgrade, code, Skills, workflow, MCP registration, Vault access or secret transfer. No runtime/automated test executed for these documentation-only edits; upstream tests are not OMEGA tests. Validate links, Markdown structure and absence of secrets; review CI before merge. Rollback by reverting the two documentation commits.

The inspected branch does not contain `omega/docs/gbrain-obsidian-bridge.md`. The separate bridge proposal and SQLite-free design remain unresolved. Continue Hermes v0.23.0 gateway isolation, GBrain RLS/storage separation, and PR #10 Contributor Attribution/CI verification. Do not merge automatically.
