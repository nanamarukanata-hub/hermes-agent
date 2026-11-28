# OMEGA release review — 2026-11-23 (UTC)

## Verified upstream delta

Official GitHub Releases API checked 2026-11-23. Compared with the 2026-11-21 radar, only GBrain has a newly verified **non-prerelease** release: `v0.60.761.0` (published 2026-11-22 02:45 UTC; prior recorded `v0.60.748.0`). Codex `rust-v0.171.0`, Claude Code `v2.1.330`, Hermes Agent `v0.23.3`, and Obsidian `v1.15.1` remain the latest non-prerelease tags returned by their official APIs.

- Stable GBrain: https://github.com/garrytan/gbrain/releases/tag/v0.60.761.0
- Newer **prerelease**, not a stable recommendation: `v0.60.764.0` (2026-11-22 17:36 UTC): https://github.com/garrytan/gbrain/releases/tag/v0.60.764.0
- Official sources: https://github.com/openai/codex/releases ; https://github.com/anthropics/claude-code/releases ; https://github.com/NousResearch/hermes-agent/releases ; https://github.com/obsidianmd/obsidian-releases/releases

## Security and privacy — high priority

GBrain prereleases `v0.60.762.0` through `v0.60.764.0` contain multiple fixes for **publishing private content**: private Timeline/fact rows/hidden comments leaking into shared files, guest/frontmatter leakage, unsafe YAML frontmatter parsing, and PDF output carrying private Timeline/fact rows. `v0.60.763.0` also restricts remote secret reveal to callers able to see the secret marker. These are **prerelease fixes**, not evidence that the latest stable release contains them. Do **not** auto-publish, PDF-export, or bulk-share private Obsidian/RP memory through GBrain until a stable fix and isolated regression tests are verified. No production Vault or sharing settings were changed.

Stable `v0.60.761.0` limits expensive held-note Git-history screening to two minutes per pass while retaining partial results and retry semantics. It also makes provider refusals explicit in bundled skills and avoids writing refused book-mirror chapters as completed. This improves reliability and failure observability but does not establish that the prerelease privacy fixes are in stable.

## Compatibility, license, and audio-production value

- **Compatibility:** no automatic Hermes, MCP, Codex, Claude Code or Obsidian upgrade; test with synthetic private/public pages, CJK filenames/frontmatter, concurrent Git sync, PDF export, and distinct users before integration. The OMEGA policy remains Obsidian-backed persistence without reintroducing SQLite.
- **License:** no third-party code, model weights, plugin packages or external skills were copied. Upstream license and distribution terms must be reviewed separately before code adoption.
- **DTM/Kontakt/KSP/JUCE/VST3/mixing:** GBrain changes help protect unreleased compositions, samples, DSP design notes and client mixes in searchable knowledge stores; they do not improve audio fidelity, plugin DSP throughput or mastering quality directly.
- **ChatGPT/Work/Agent Skills/MCP:** no new mandatory compatibility change was established by this GitHub release check. A complete product-announcement audit is not claimed.

## Repository and validation status

Read `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, `omega/knowledge/latest-releases.md` and `.github/workflows/omega-release-radar.yml`. `omega/docs/gbrain-obsidian-bridge.md` is absent on the checked branch; reconcile with the separate bridge PR before modifying. This update changes documentation only; no code, Skills or workflows were modified, so no new code unit tests were run. GitHub write/read-back and CI status must be reported separately; no CI pass is asserted here.

**Rollback:** revert this review and the matching radar metadata commit. No private Google Drive Vault data or authentication material was accessed or published. No global ChatGPT plugin or application installation was changed.
