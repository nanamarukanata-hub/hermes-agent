# OMEGA official release and security review — 2026-10-27 JST

Status: **review only**. Official upstream release metadata and repository documentation checked on 2026-10-27 JST. No software, hosted model, global plugin, MCP server, Obsidian Vault, or database was upgraded or modified.

## Release delta since 2026-10-26

| Project | Latest stable release (published UTC) | Delta and decision |
|---|---|---|
| [Codex](https://github.com/openai/codex/releases/tag/rust-v0.164.0) | `rust-v0.164.0` (2026-10-23) | Unchanged. Retain MCP OAuth, sandbox and credential-masking regression gates. |
| [Claude Code](https://github.com/anthropics/claude-code/releases/tag/v2.1.311) | `v2.1.311` (2026-10-26) | **Changed.** Fixes late-connecting MCP tools for subagents, stale MEMORY.md/CLAUDE.md after compaction, stalled MCP/Skill interruption, background task restart and worktree lifetime, and non-retryable API request handling. Test on an isolated agent runner before adopting. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5) | `v0.22.5` (2026-10-23) | Unchanged. MCP serving and delegation still need explicit trust-boundary tests; no runtime update. |
| [GBrain](https://github.com/garrytan/gbrain/releases/tag/v0.60.470.0) | `v0.60.470.0` (2026-10-27) | **High-priority security change.** Hardens PostgreSQL function search paths against temporary-table/view shadowing of purge and withdrawal guards. Migration v277 and `function_search_path` doctor check; not a complete fix for scoped-writer RLS. |
| [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.14.5) | `v1.14.5` (2026-10-22) | Unchanged. Desktop Electron security update remains a manual installation decision. |

## Security gate: GBrain temporary-schema shadowing

The [official v0.60.470.0 release](https://github.com/garrytan/gbrain/releases/tag/v0.60.470.0) documents that a database session could shadow purge/forget lookup tables with a temporary table or view, making the guards accept previously purged content or reactivate forgotten facts. The fix appends `pg_temp` last in relevant PostgreSQL function `search_path` settings, without converting guards to `SECURITY DEFINER`.

- **Migration required:** official instructions are `gbrain upgrade` or `gbrain apply-migrations --yes`, followed by `gbrain doctor --json`; require `function_search_path: ok`. **Do not run these against the user's live database or Vault as part of this review.** First inventory role ownership, backups, and migration impact on a synthetic clone.
- **Partial ownership:** migration v277 updates functions owned by the migrating role. `gbrain doctor` identifies functions still requiring an `ALTER FUNCTION` by their actual owners.
- **Still unresolved:** [GBrain issue #7659](https://github.com/garrytan/gbrain/issues/7659) is open. A scoped PostgreSQL writer without a matching SELECT RLS policy on purge/withdrawal lookup tables may still pass guard checks as if the lists were empty. Do not claim the release closes this gap. Restrict write roles and validate SELECT policies for each writable source.
- **Regression plan (not executed):** with synthetic claims, test purge/withdrawal reinsert refusal under temp-table and temp-view shadowing, owner and scoped-writer roles, function ownership mismatch, and RLS policy absence. Confirm doctor output and rollback on a disposable database.

**Impact on OMEGA:** prevents sensitive or withdrawn facts from being silently reintroduced into a shared agent memory graph. It does not prove data deletion from Markdown, backups, or external Obsidian sync.

## ChatGPT / Work / Codex model announcement

[OpenAI ChatGPT release notes, 2026-10-26](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) announce **GPT-6.1 Terra** rolling out in Work and Codex for eligible Plus, Pro, Business, Enterprise and Edu plans. This is a hosted model availability announcement, **not** a local model installation or an entitlement guarantee. No change to OMEGA code, pricing, global plugins, or inference residency was made. The announcement notes no UAE inference residency support. Compare task quality and cost in an authorized environment before changing model defaults.

## Protocols, Skills, licensing and audio-development value

- [MCP specification](https://modelcontextprotocol.io/specification) still resolves to 2026-07-28. Explicit consent, tool authorization, scoped data access and opt-in extensions remain required; no breaking OMEGA migration identified.
- [Agent Skills specification](https://agentskills.io/specification) still requires a valid `SKILL.md` with matching `name` and `description`; the existing `omega-ai-updates` Skill meets the basic structural requirements. No third-party Skill installed.
- Verified upstream LICENSE texts: [Codex Apache-2.0](https://github.com/openai/codex/blob/main/LICENSE), [Hermes MIT](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE), [GBrain MIT](https://github.com/garrytan/gbrain/blob/master/LICENSE). Hosted offerings and third-party dependencies retain separate terms.
- For Kontakt/KSP, JUCE/VST3, MIDI, mixing and mastering notes, the direct benefit is safer access-controlled technical memory and more reliable agent tooling. There is **no demonstrated DSP, audio-rendering or mix-quality improvement**.

## OMEGA repository, validation and blockers

- Read `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, the main release radar workflow and metadata, plus `omega/docs/gbrain-obsidian-bridge.md` on the still-unmerged PR #8 branch. PR #9 is also still a draft.
- This review changes **Markdown metadata and review documentation only** on existing [draft PR #10](https://github.com/nanamarukanata-hub/hermes-agent/pull/10). No Python, workflow, Skills, or application code changed today. Static metadata checks: five fixed official repositories, expected release tags, and basic secret-pattern scan passed. **No new local Python tests were run today.**
- The previous PR #10 head had GitHub check failures, contrary to earlier reports that CI was unverified. On the 2026-10-26 run, the broad upstream test job reported **55 failed, 20,821 passed, 49 skipped and 4 errors**; the attribution job failed because a contributor email was not in the repository-wide AUTHOR_MAP. Other checks passed. These are blockers to merging; attribution and upstream tests are outside the authorized `omega/` change scope. No root-level fix attempted.
- The `.github/workflows/omega-release-radar.yml` PR-creation permission issue remains unresolved. Its workflow was inspected but not modified.
- No private Google Drive/Obsidian note bodies were read or copied. No Vault content, credentials, personal identifiers, database rows, or local filesystem paths were published. No user-device or ChatGPT-global changes.

## Review and rollback

Keep PR #10 as **draft** until the GitHub CI failures are triaged, the release radar workflow's PR permissions are reviewed, and isolated GBrain security tests pass. Roll back the two documentation commits if their metadata is inaccurate; do not deploy a database migration without a tested backup/restore path.
