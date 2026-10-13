---
type: moc
status: active
updated: 2026-10-13
tags: [omega, ai, agents]
---

# AI Systems

Index of verified mechanisms used by PROJECT OMEGA.

- **Policies**: `../AGENTS.md`
- **AI update skill**: `../skills/omega-ai-updates/SKILL.md`
- **Release monitoring**: [[latest-releases]]
- **Run automation**: `../../.github/workflows/omega-release-radar.yml`
- **Existing skill search infrastructure**: `../../codex-plugins/claude-skills-hub/` (do not duplicate)

## Architecture decisions

- ChatGPT Chat: consult this repo through authenticated GitHub connector.
- Work: import or retrieve these files as task context; propose reviewed PRs.
- Codex: use project-level AGENTS.md in the relevant directory.
- Obsidian: curated Markdown mirror, with device-side synchronization configured separately.

No automatic GPT foundation-model update is claimed.
