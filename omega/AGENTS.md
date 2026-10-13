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
