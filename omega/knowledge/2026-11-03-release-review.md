---
type: omega-release-review
date: 2026-11-03
status: verified-public-metadata-review
scope: public-only
---

# OMEGA release review — 2026-11-03 (UTC)

## Verified changes since the 2026-11-02 radar

Official GitHub Releases API, read on 2026-11-03 UTC:

| Project | Previous radar | Current | Assessment |
|---|---|---|---|
| [Codex](https://github.com/openai/codex/releases/tag/rust-v0.166.0) | rust-v0.166.0 | rust-v0.166.0 | No new release |
| [Claude Code](https://github.com/anthropics/claude-code/releases/tag/v2.1.316) | v2.1.316 | v2.1.316 | No new release |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.6) | v0.22.5 | **v0.22.6** (2026-11-02 17:18 UTC) | Important: large integration surface |
| [GBrain](https://github.com/garrytan/gbrain/releases/tag/v0.60.555.0) | v0.60.542.0 | **v0.60.555.0** (2026-11-02 22:41 UTC) | **High-priority security fixes** |
| [Obsidian](https://github.com/obsidianmd/obsidian-releases/releases/tag/v1.15.0) | v1.14.5 | **v1.15.0** (GitHub 2026-11-02 15:39 UTC) | Plugin/API compatibility review |

All five queried GitHub release objects had `draft=false` and `prerelease=false`. For Obsidian, its [official changelog](https://obsidian.md/changelog/2026-10-22-desktop-v1.15.0/) still says **Catalyst / early access**, dated 2026-10-22. GitHub release metadata and distribution availability are distinct; do not assert a public stable desktop/mobile rollout from the GitHub flag alone.

## Priority 1 — GBrain: untrusted working-directory code execution

The upstream [v0.60.547.0 release](https://github.com/garrytan/gbrain/releases/tag/v0.60.547.0) (2026-11-02) documents a script-mode Bun startup risk: session-directory `bunfig.toml` preloads or `.env` could be evaluated when agent hooks ran in an untrusted checkout, without an explicit approval for that repository's code. A dedicated launcher now changes to a controlled directory.

The upstream [v0.60.550.0 release](https://github.com/garrytan/gbrain/releases/tag/v0.60.550.0) (2026-11-02) extends this mitigation to stdio MCP servers, remaining bootstrap/plugin hook lanes and Git `core.fsmonitor` probes. **Installing a new binary alone may not rewrite every existing hook or MCP registration.** Upstream states that Codex and bootstrap entries often need an explicit user-run `gbrain setup codex` or matching `gbrain bootstrap` command; `gbrain doctor` flags `hook_session_cwd` wiring.

**OMEGA decision:** do not auto-install or run GBrain against an actual Vault. If a script-mode GBrain install is present, prioritize a backed-up, isolated upgrade and inspect `gbrain doctor`, hook launchers, stdio MCP entries, setup receipts and untrusted-checkout behavior before production use. Do not automatically re-trust Codex hooks or execute third-party repository configuration. The upstream [Issue #7659](https://github.com/garrytan/gbrain/issues/7659) (guard triggers / scoped writers) remains open at review time; it is a separate access-control follow-up.

Other relevant fixes:
- [v0.60.553.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.553.0): normalizes schema-invalid empty optional arguments (`0`, `[]`, `{}`, `false`) in read-tool calls, including GPT-6.1 Sol paths; required or genuinely invalid non-empty inputs still fail.
- [v0.60.554.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.554.0): repairs installed admin UI / OAuth consent asset packaging, packaged-Skill adoption and source-scoped MCP diagnostics.
- [v0.60.555.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.555.0): restores `gbrain query --limit N` behavior without explicit token budgets, bounds budgeted fact blocks, fixes fact labels and prevents their scores from outranking pages. Verify any downstream parsers that assumed the previous oversized result shape.

**DTM value:** safer retrieval of private mix revisions, Kontakt/KSP snippets, JUCE/VST3 documentation, preset provenance and audio project notes. This is retrieval reliability and confidentiality, **not a DSP/sonic-quality improvement**.

## Priority 2 — Hermes Agent v0.22.6: large patch rollup

[Official release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.22.6) states that the patch aggregates approximately **776 merged PRs** since v0.22.5; curated release notes are deferred to v0.23.0. Notable changes include `hermes webapp`, per-Skill write locks, opt-in `compression.prepare_ahead`, `hermes cron cancel` and missed-run policy, optional OS-keychain vault-key storage, `requires_hermes` checks on plugin install, `pre_llm_call` blocking hook, and selectable ElevenLabs voices.

**OMEGA decision:** this is a wide compatibility surface despite the patch number. Do not auto-upgrade a live Discord RP worker, resident worker, Obsidian bridge, cron schedule or voice pipeline. In a disposable test environment, check gateway/Discord events, RP state persistence, Skills write locks, MCP compatibility, scheduled jobs, authorization, voice output and rollback. The voice option can help narration/TTS workflows, but it does not improve DAW mixing DSP.

## Priority 3 — Obsidian v1.15.0: renderer/API migration

[Official changelog](https://obsidian.md/changelog/2026-10-22-desktop-v1.15.0/) describes a **Catalyst / early-access** 1.15.0 desktop release using Pele instead of bundled Mermaid for diagrams (Mermaid 12.1 syntax), reorderable Bases filters, and URI/CLI commands to append/prepend under a heading. `window.mermaid` and `loadMermaid()` compatibility shims remain, but SVG classes are now `.pele-*`; `PIXI` is lazy-loaded.

**OMEGA decision:** first test a *copy* of the Vault and installed Community plugins, especially Mermaid-rendering/CSS, graph integrations and Bases. Do not change the user's Google Drive-backed Vault or plugin configuration. Channel ambiguity means no automatic app update. Potential value: cleaner music-production architecture and signal-flow diagrams, not sound quality.

## ChatGPT, Work, Codex, Claude Code, MCP and Agent Skills

- [OpenAI ChatGPT release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) currently list 2026-10-30 account switching and 2026-10-29 Work/Codex allowance changes; no new actionable Nov 2–3 change confirmed.
- [Codex v0.166.0](https://github.com/openai/codex/releases/tag/rust-v0.166.0) and [Claude Code v2.1.316](https://github.com/anthropics/claude-code/releases/tag/v2.1.316) remain unchanged since the previous radar. Existing security/permission fixes still merit testing before adoption.
- [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28) and [Agent Skills specification](https://agentskills.io/specification) were reviewed. No demonstrated new mandatory migration for `omega/skills/omega-ai-updates/SKILL.md`; do not modify Skills solely for novelty.

## Licensing and boundaries

Upstream repository LICENSE files checked: Codex **Apache-2.0**; Hermes Agent **MIT**; GBrain **MIT**. Claude Code distribution and Obsidian application have their own vendor terms; their release notes do not grant a general right to redistribute binaries or private user data. No upstream code, Skill payload or model weights are copied into OMEGA.

The requested `omega/docs/gbrain-obsidian-bridge.md` is **not on the reviewed PR #10 branch or main**; it appears as a proposed addition in the still-open [PR #8](https://github.com/nanamarukanata-hub/hermes-agent/pull/8). That PR's optional bridge uses **SQLite FTS5**, conflicting with the stated SQLite-free Hermes architecture. Do not assume it is installed, merged or connected. [PR #9](https://github.com/nanamarukanata-hub/hermes-agent/pull/9) is also open.

## Validation, deployment and rollback

- Verified: official GitHub release metadata and selected release bodies; official Obsidian, OpenAI, MCP and Agent Skills documentation; upstream LICENSE files; PR #8/#9/#10 metadata.
- This review modifies **only** `omega/knowledge/latest-releases.md` and this dated Markdown report on Draft PR #10. No Python, workflows, Skills, root AGENTS.md, private notes or user application settings are changed.
- Static checks: 5 distinct project rows; release URLs; no private Vault text, credentials or tokens in generated Markdown. These checks are not equivalent to runtime tests.
- No new unit or end-to-end test was run for an installed Hermes/GBrain/Obsidian instance. The previous PR #10 commit's GitHub Actions showed Tests and Contributor Attribution failures; do not merge until CI is reviewed.
- Rollback: revert these documentation commits on the PR branch; there are no application binaries, database migrations or Vault changes to roll back.
- Google Drive private Vault is intentionally **not read or modified** during this public-metadata review. ChatGPT, Work, global plugins and user devices were **not updated**.
