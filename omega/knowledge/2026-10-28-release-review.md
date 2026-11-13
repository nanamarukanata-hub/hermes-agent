# OMEGA official release and security review — 2026-10-28 JST

Status: **verified upstream metadata; review only**. No application, plugin, model, MCP service, Obsidian Vault, or database was upgraded. Sources were checked on 2026-10-28 JST against official GitHub Releases and official documentation. Stable GitHub releases only; no prerelease promotion.

## Release delta since 2026-10-27

| Project | Stable release (published UTC) | Assessment |
|---|---|---|
| [Codex](https://github.com/openai/codex/releases/tag/rust-v0.165.0) | `rust-v0.165.0` (2026-10-27 20:09 UTC) | **New; security/reliability priority.** Marketplace URL validation now closes a gap where Git could connect to a different host than the checked allowlist. Remote MCP requests cancel when their response future is dropped; new profiles default to memory v2, with external-agent memory import. GPT-6.1 Terra is in the model catalog and Vertex AI provider support was added. Audit memory imports, network destinations, permissions, and provider credentials before enabling. |
| [Claude Code](https://github.com/anthropics/claude-code/releases/tag/v2.1.312) | `v2.1.312` (2026-10-27 20:24 UTC) | **New; observability change.** Fixes overwriting truncated long CLAUDE.md/rules files, path-scoped Skills after `cd`, and managed policy retrieval. The `claude_code.safety_event` OpenTelemetry event is **no longer emitted**. Do not rely on this event as a sole safety/audit signal; verify alternative telemetry before adoption. Hook concurrency limits also changed. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5) | `v0.22.5` (2026-10-23 05:28 UTC) | Unchanged. MCP serving, delegation, and plugin boundaries still need isolated compatibility tests. No runtime update. |
| [GBrain](https://github.com/garrytan/gbrain/releases/tag/v0.60.475.0) | `v0.60.475.0` (2026-10-27 23:17 UTC) | **New; high-priority integrity/security.** Fixes `GIT_LITERAL_PATHSPECS` leaking into Git hooks, which had allowed pattern-based pre-commit checks to miss invalid files. Prior `v0.60.474.0` makes writer-state guards fail closed when scoped PostgreSQL roles cannot read required state under RLS; `v0.60.473.0` locks index refreshes to prevent staged-change loss. Test against disposable repositories and DB roles; do not migrate the live Vault. |
| [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.14.5) | `v1.14.5` (2026-10-22 21:19 UTC) | Unchanged. Electron security update remains subject to device-side backup and plugin compatibility checks. |

## GBrain security boundary and unresolved issue

- [v0.60.475.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.475.0) removes inherited global Git pathspec overrides from hooks, filters and helpers. This restores pattern-based pre-commit validation. Confirm a synthetic malformed `*.json` file is rejected and that filenames containing metacharacters still stage literally.
- [v0.60.474.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.474.0) fixes an RLS-hidden writer-state fail-open path by returning `writer_coordinator_required`; it replaces five function bodies without rewriting data or changing privileges. Verify managed and unmanaged brains under scoped roles with and without SELECT policies before production use.
- [v0.60.473.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.473.0) uses Git `index.lock` for index refresh. A failed post-commit refresh can still mean the commit **already landed**; never blindly retry a write without checking commit state.
- **Open residual risk:** [GBrain issue #7659](https://github.com/garrytan/gbrain/issues/7659) remains open as checked today: purge/withdrawal lookup guards can fail open for scoped roles lacking lookup-table SELECT policies. The writer-state fix in 0.60.474.0 must **not** be described as resolving #7659. Keep write roles constrained and test purge/withdrawal guards in a disposable database. Prior temporary-schema hardening in 0.60.470.0 remains relevant.
- Do not interpret `withdrawn` as guaranteed physical erasure from Markdown, backups, or external synchronization.

## ChatGPT, Work, protocols, licensing and audio relevance

- [OpenAI ChatGPT release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes): 2026-10-27 introduced iCloud Mail and Calendar plugins; no account connection was added. 2026-10-26 GPT-6.1 Terra rollout to eligible Work/Codex users remains an availability announcement, not proof of access or an installed model.
- [MCP specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28): no new stable revision requiring an OMEGA migration identified today. Remote MCP authorization, scoped access, cancellation, and version negotiation still require integration tests.
- [Agent Skills specification](https://agentskills.io/specification): `SKILL.md` with valid frontmatter remains required. Existing `omega-ai-updates` Skill was inspected; no Skill code or metadata change needed and no third-party Skill installed.
- Official repository LICENSE files checked: [Codex Apache-2.0](https://github.com/openai/codex/blob/main/LICENSE), [Hermes MIT](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE), [GBrain MIT](https://github.com/garrytan/gbrain/blob/master/LICENSE). Claude Code and hosted model/service use have their own applicable terms; dependency licenses remain separate.
- **DTM/Kontakt/KSP/JUCE/VST3/MIDI/mixing:** better protection for code, preset manifests, knowledge-base provenance, and automated build checks. No demonstrated direct improvement to DSP fidelity, audio rendering, loudness, or mix quality. Prioritize synthetic fixture tests and auditability over new model/plugin installations.

## Repository status and verification

- Inspected `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, `.github/workflows/omega-release-radar.yml`, Release Radar script/tests and knowledge. `omega/docs/gbrain-obsidian-bridge.md` remains on unmerged [PR #8](https://github.com/nanamarukanata-hub/hermes-agent/pull/8), not main. [PR #9](https://github.com/nanamarukanata-hub/hermes-agent/pull/9) remains draft.
- This change updates **only** `omega/knowledge/latest-releases.md` and adds `omega/knowledge/2026-10-28-release-review.md` on existing [draft PR #10](https://github.com/nanamarukanata-hub/hermes-agent/pull/10). No Python, workflow, or Skills code changes today.
- Five official release feeds checked; dates, tags and repository allowlist validated. Documentation contains no Vault note bodies, user identifiers, local paths or credentials. Local unit tests **not rerun** because no code changed and this environment cannot directly download the private branch into its isolated test container. The previously reported 8 passing unit tests were on 2026-10-24, not today.
- The prior PR #10 head's GitHub Actions still reports **Tests: failure**, **Contributor Attribution Check: failure**, **Lint: success**, **Supply Chain Audit: success**. Today's new head CI must be checked independently. Do not merge until failures are triaged. Release Radar's automatic PR-creation permission remains unresolved; the workflow is unchanged.
- Google Drive's private ChatGPT-Obsidian-Vault was not read, modified or deleted. No public notes, credentials, or user data were exported. No global ChatGPT settings or device software were changed.

## Review gates and rollback

1. Keep PR #10 draft until CI and attribution checks pass or their upstream-only failures are explicitly isolated and reviewed.
2. On disposable Git/DB fixtures, test GBrain hook enforcement, index-lock contention, scoped writer-state RLS and purge/withdrawal guard behavior.
3. Before using Claude Code 2.1.312 for unattended tasks, confirm the replacement audit trail for removed `claude_code.safety_event` telemetry.
4. Before enabling Codex memory v2 imports or new Vertex provider, verify data boundaries, network allowlists, credentials and model entitlements.
5. Roll back the two Markdown-only commits if metadata is wrong; do not run live GBrain migrations or modify the Vault as part of this review.
