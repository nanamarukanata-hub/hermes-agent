# OMEGA release review — 2026-11-18 JST

Status: verified upstream metadata and security review; **not installed or deployed**.

## Verified differences
- **Codex**: [rust-v0.169.1](https://github.com/openai/codex/releases/tag/rust-v0.169.1), published 2026-11-17 22:21 UTC, replaces rust-v0.169.0. Release notes list chores only, with no verified functional/audio-DSP improvement. No immediate migration.
- **GBrain**: [v0.60.734.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.734.0), published 2026-11-17 02:15 UTC, is explicitly **pre-release**, not the stable baseline v0.60.722.0. The release fixes a remote remember operation that could republish a higher-trust private fact as world-visible, and repairs preserving write-gate quarantine flags. It explicitly documents **two remaining exposure paths** (remote get_page/search for held-page text, and re-saving the same claim under a different entity). Do not deploy to a shared/private Vault until those are addressed and tested.
- New `gbrain repair open-facts` can intentionally widen visibility of old private facts. **Never run --apply on production** without owner review, explicit allowlist and a tested undo. Its preview is not proof of safe publication.
- Claude Code, Hermes Agent, and Obsidian: no independently verified newer stable release in this check. Their last verified radar baselines remain unchanged; stale API/search caches mean absence of evidence is not proof of no release.

## OMEGA decision
- **Security priority P0:** isolate private Obsidian and Discord RP notes from GBrain remote/world visibility; test entity-alias restatement, get_page/search on held facts, quarantine retention, and withdrawn/purged claims in synthetic fixtures before integration.
- **DTM/audio relevance:** private project files, unreleased compositions, Kontakt/KSP and JUCE/VST3 source and mix notes need confidentiality; these are data-governance fixes, not DSP or audio-quality improvements.
- **License:** no third-party code, binaries or model weights imported; only upstream links and independently written commentary.
- **Compatibility:** no changes to Hermes gateway, Codex config, MCP registration, Agent Skills or Obsidian plugins; no automatic upgrades.

## Scope and verification
Read `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, `omega/knowledge/latest-releases.md` and `.github/workflows/omega-release-radar.yml` on the PR branch. `omega/docs/gbrain-obsidian-bridge.md` returned 404 on that branch; reconcile with its design PR separately. ChatGPT/Work, MCP and Agent Skills have no **verified mandatory OMEGA migration** in this run, not a comprehensive no-change guarantee.

Only Markdown release metadata/review is modified. No Python/Skills/workflow modifications, no production tests or app upgrades. Keep PR as Draft pending CI and privacy regression testing. Never copy private Vault content, identifiers or credentials into this public repository.
