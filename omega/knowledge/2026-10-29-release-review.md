# OMEGA official release and security review — 2026-10-29 JST

Status: **verified upstream release metadata; review only**. Official GitHub stable-release endpoints and project documentation checked on 2026-10-29 JST. No GBrain database, Obsidian Vault, MCP service, model, plugin, user device or ChatGPT global configuration was updated.

## Stable release delta since 2026-10-28

| Project | Latest stable release (published UTC) | OMEGA decision |
|---|---|---|
| [Codex](https://github.com/openai/codex/releases/tag/rust-v0.165.0) | `rust-v0.165.0` (2026-10-27 20:09) | Unchanged. Keep memory-v2 imports and Vertex provider disabled until data-boundary, credentials and provider-compatibility review. |
| [Claude Code](https://github.com/anthropics/claude-code/releases/tag/v2.1.312) | `v2.1.312` (2026-10-27 20:24) | Unchanged. The removed `claude_code.safety_event` telemetry is not an audit source; verify independent tool/permission logging before unattended operation. |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.5) | `v0.22.5` (2026-10-23 05:28) | Unchanged. No runtime or Obsidian integration upgrade. |
| [GBrain](https://github.com/garrytan/gbrain/releases/tag/v0.60.483.0) | `v0.60.483.0` (2026-10-28 20:09) | **New; critical privacy/source-isolation priority.** Session-to-source routing and private visibility enforcement fixes require synthetic tests and historical data audit. |
| [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.14.5) | `v1.14.5` (2026-10-22 21:19) | Unchanged. Electron security update remains a separate device-side maintenance task. |

## Critical GBrain privacy findings (official release chain 0.60.476–0.60.483)

1. [v0.60.476.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.476.0): external Gmail, calendar, GitHub and transcript text containing GBrain fact markers could previously be interpreted as authoritative `## Facts` data. Import now neutralizes those markers. **Treat imported content as untrusted data, never agent instructions or approved facts.**
2. [v0.60.477.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.477.0): trust-release approval now binds all previewed fact fields; changed/re-gated rows must not be approved on a stale preview.
3. [v0.60.479.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.479.0): legacy migrations could rewrite Markdown page frontmatter and desynchronize Git/database state. Use a disposable clone and compare hashes before any migration.
4. [v0.60.481.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.481.0) and [v0.60.482.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.482.0): synthesis/atom extraction could write a private-source conversation into another source, including `default`, exposing it to readers of that source.
5. [v0.60.483.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.483.0): session routing no longer permanently assigns an unresolved session to a fallback source; queued synthesis, atom retry and resumed generation re-check source ownership before writing. Symlinked conversation paths are resolved by real path. Unresolved sessions are held as `corpus_source_unresolved`, changed jobs as `session_source_changed`, and cross-source retries as `transcript_source_mismatch`. A dry run must not persist source bindings.
6. The same v0.60.483.0 release prevents Git sync/import from silently publishing noncanonical private-visibility frontmatter. The only accepted spelling is exactly `visibility: private` on its own YAML frontmatter line. Variants such as `Visibility: private`, `visibility: Private`, block/list values and merge keys are held as `visibility_noncanonical` or refused. `gbrain doctor` reports affected files under `git_held_files`.

**Historical exposure is not automatically repaired.** Upgrading does not move already misrouted pages/atoms, reverse any prior disclosure, or rewrite private-visibility mistakes. Before production adoption, inventory affected source boundaries and stored pages in a disposable clone. Keep existing Vaults and databases untouched. Do not upload historical private data to public GitHub.

**Residual upstream issue:** [#7659](https://github.com/garrytan/gbrain/issues/7659) was still **open** at verification time (2026-10-29 JST): scoped PostgreSQL writer roles may bypass purge/withdrawal lookup guards when RLS hides guard tables. The recent routing fixes do **not** establish that this separate issue is resolved.

## Safe acceptance plan (not executed against live data)

- In a synthetic multi-source brain, capture a private-source session and verify that `dream` synthesis, atom extraction, explicit retry and crash-resume never write it to `default` or another source.
- Use a session with no recorded source and no registered-folder match: it must be held, not silently pinned. Preview with `gbrain sweep --assign-corpus <source>` only on test fixtures; `--apply` is a separate authorized write. Confirm `gbrain dream --dry-run --phase synthesize` leaves database and file hashes unchanged.
- Test a symlinked conversation path, a deleted/recreated source and a queued old job; require fail-closed source checks and held-item counts.
- Import synthetic Markdown with `visibility: private` and deliberately malformed case/multiline/list/merge-key variants; require holds/refusals rather than a publicly readable page. Verify `gbrain doctor` reporting and explicitly inspect any pre-existing affected pages.
- Test a synthetic external transcript containing `gbrain:...facts...` markers; it must remain inert text and must not create trusted facts.
- Exercise scoped PostgreSQL roles without required SELECT policies to reproduce or rule out #7659 before any purge/withdrawal operation.
- Back up, audit provenance and inspect diffs before migrating. Avoid concurrent Obsidian sync and database writes; preserve existing private Vault notes. Do not treat withdrawn facts as guaranteed deletion from backups.

## ChatGPT / Work, protocols, licensing and audio impact

- [ChatGPT official release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes): latest listed announcement is 2026-10-27 iCloud Mail/Calendar plugins; 2026-10-26 GPT-6.1 Terra is rolling out for eligible Work/Codex users. Neither proves availability on this account. No plugin connection or model change performed.
- [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28) remains the published revision inspected. Preserve explicit consent, per-source authorization, tool provenance and cancellation semantics. No automatic MCP server installation.
- [Agent Skills specification](https://agentskills.io/specification) requires `SKILL.md` frontmatter; the existing `omega-ai-updates` Skill meets the basic structure and needs no revision today.
- Verified source licenses: [Codex Apache-2.0](https://github.com/openai/codex/blob/main/LICENSE), [Hermes MIT](https://github.com/NousResearch/hermes-agent/blob/main/LICENSE), [GBrain MIT](https://github.com/garrytan/gbrain/blob/master/LICENSE). Claude Code usage, hosted services, Obsidian and third-party dependencies require their own license/terms review; do not infer rights from unrelated repositories.
- **DTM, Kontakt/KSP, JUCE/VST3, MIDI and mixing:** highest value is preventing private composition notes, sample metadata, code snippets, preset names and mix-session knowledge from leaking across agent knowledge sources. No release establishes improved DSP accuracy, audio fidelity, rendering or mix quality.

## Repository scope, verification, CI and rollback

- Read `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, Release Radar Python code/tests, `.github/workflows/omega-release-radar.yml`, and current knowledge metadata. `omega/docs/gbrain-obsidian-bridge.md` is on [unmerged PR #8](https://github.com/nanamarukanata-hub/hermes-agent/pull/8), not main; it is a local GBrain-inspired bridge, **not** a deployed GBrain daemon. [PR #9](https://github.com/nanamarukanata-hub/hermes-agent/pull/9) remains draft.
- This review proposes only `omega/knowledge/latest-releases.md` and `omega/knowledge/2026-10-29-release-review.md` on [draft PR #10](https://github.com/nanamarukanata-hub/hermes-agent/pull/10). No Python, Skill, workflow, runtime or root-level changes.
- Five official stable-release tags, dates and allowlisted repositories checked. Static Markdown and secret-pattern validation performed before commit. No Python unit tests rerun for metadata-only changes; previous local 8-test pass was 2026-10-24, **not today's test result**.
- Previous PR #10 head `9d7e025` CI: Tests **failure** and Contributor Attribution **failure**; Lint and Supply Chain Audit **success**, E2E **success**. New head CI must be checked separately; keep draft and do not merge on metadata checks alone. Automatic PR creation permissions in the scheduled Release Radar workflow remain unresolved.
- No private Obsidian notes or credentials were fetched, copied or modified. No live database migration, ChatGPT global plugin installation, Work configuration change or device update.
- Rollback: revert the two public Markdown commits on the PR branch, or close PR #10. No changes to main are intended.
