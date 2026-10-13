# OMEGA shared agent instructions

Scope is **omega/**, not the Hermes source tree above it.

## Purpose
Safely evaluate and improve shared workflows for ChatGPT Chat, Work and Codex. Prioritize agent reliability, DTM/music production, audio DSP, Kontakt/KSP, JUCE/VST3, mixing/mastering, MIDI, and design.

## Workflow
1. Inspect actual permissions, files, installed Skills and recent changes. Never assume access.
2. Confirm important claims with official docs, GitHub source, releases or reproducible tests. X posts are discovery leads.
3. Identify small improvements, security risks, overlap and platform-specific compatibility.
4. Make small changes within `omega/`; for changes to the wider repo, request explicit approval.
5. Run tests, record results, include rollback guidance and open PRs for review.
6. Clearly distinguish **live / verified / proposed / blocked**.

## Cross-surface policy
- Chat and Work retrieve this file via a connected repo or attachment; it is not a hidden global model instruction.
- Codex works from this file as its project instruction when working within `omega/`.
- Compatible agents may load `omega/skills/omega-ai-updates/SKILL.md`.
- Obsidian can mirror curated Markdown but does not automatically synchronize without extra device-side configuration.

## Safety
Never publish secrets or private user notes. Never auto-execute unreviewed third-party Skills or MCP servers. Do not silently change existing root AGENTS.md, CLAUDE.md or Hermes internals. Model weights and ChatGPT global plugin installation are out of scope.

## Optional Vault memory extension (GBrain-inspired)
- When the task concerns prior project knowledge and an authorized local Obsidian Vault is mounted, read `omega/skills/omega-vault-memory/SKILL.md` and `omega/docs/gbrain-obsidian-bridge.md`.
- Use `omega/scripts/vault_brain.py` for keyword search, citation-aware notes and explicit remember/correct/withdraw commands. These are local operations; no GBrain daemon, MCP server, or automatic ChatGPT memory injection is implied.
- Keep all private Vault content out of this public repository. Never index or write to a Vault whose path and authority are unknown. Run tests before modifying files.
