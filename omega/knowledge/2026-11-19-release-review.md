# OMEGA release review — 2026-11-19

Scope: public upstream release metadata and security/compatibility assessment only. No private Vault content, credentials, or personal data.

## Verified official GitHub non-prerelease releases
- Codex `rust-v0.170.0` — 2026-11-18 03:40 UTC — https://github.com/openai/codex/releases/tag/rust-v0.170.0
- Claude Code `v2.1.327` — 2026-11-17 23:17 UTC — https://github.com/anthropics/claude-code/releases/tag/v2.1.327
- Hermes Agent `v0.23.2` — 2026-11-16 20:59 UTC — https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.2
- GBrain `v0.60.739.0` — 2026-11-18 08:16 UTC — https://github.com/garrytan/gbrain/releases/tag/v0.60.739.0
- Obsidian `v1.15.1` — 2026-11-12 15:43 UTC — unchanged — https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.15.1

## Priorities and risk
1. **GBrain — high, security and privacy.** New `--read-shared on` grants access to all current and future federated shared sources (not other members' private sources); default is off. Multi-person setup can block when preexisting personal history is unlabelled. Secret-redaction publishing now rewrites unpushed commits with markers and retains original commits under local `refs/gbrain/backup/` for seven days. This changes Git history and secret recovery behavior. `reveal_secret` is a separately granted, audited MCP operation. **Do not enable shared reading, reveal privileges, repair commands, or automatic secret rewriting on the production Vault without a disposable-repository test and owner review.** A conservative alternative for push behavior is `GBRAIN_SECRETS_ON_PUSH=refuse`. Ensure encrypted key backup and check that backup refs are not pushed.
2. **Codex — high, Windows/MCP.** v0.170.0 fixes MCP OAuth credential persistence races, sandbox restriction inheritance, Windows atomic file replacement and permission preservation; adds experimental same-named MCP servers across environments. Test cancellation/logout races, sandbox denial, and Windows file permissions before rollout.
3. **Claude Code — high, local-file safety.** v2.1.327 fixes Remote Control deleting git-ignored files such as `.env`, project-scope MCP edits dropping unrelated `.mcp.json` keys, and export overwrites. `claude mcp add-json <name> -` supports stdin to keep credentials out of shell arguments. Regression-test with synthetic credentials and disposable files.
4. **Hermes — medium-high, Gateway continuity.** v0.23.2 includes durable subagents across restarts, new plugin APIs, and fixes for reconnect/streaming duplicate responses. Check Gateway, Discord, session ownership, Obsidian persistence and restart idempotency before updating; patch notes defer full curated details to v0.24.0.
5. **Obsidian — unchanged.** No confirmed release delta. Keep existing Vault and Community Plugins unchanged.

## DTM / audio-development impact
For Kontakt/KSP, JUCE/VST3, MIDI and mix/mastering workflows, the direct value is safer editing of project files, predictable agent session recovery and protection of private project notes. None of these releases demonstrates a DSP-quality or audio-rendering improvement. No third-party code or samples were incorporated; upstream licenses must be checked separately before any code reuse.

## Scope and validation
Reviewed `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, `omega/knowledge/latest-releases.md`, and `.github/workflows/omega-release-radar.yml`. `omega/docs/gbrain-obsidian-bridge.md` was not found on this PR branch. ChatGPT, Work, Agent Skills and MCP official announcement coverage was not exhaustive; no mandatory migration was established from this review. No software, plugins, Skills, MCP configuration, private Drive Vault or production data were changed. Documentation-only change; no code tests or production compatibility tests performed. CI status requires separate confirmation.

## Rollback
Revert these documentation commits on the PR branch if upstream metadata is corrected. Do not merge until the existing PR CI and any later implementation tests are reviewed.
