# PROJECT OMEGA — Shared AI Operations Layer

Shared, versioned operating guidance for **ChatGPT Chat / Work / Codex**. Hermes and Claude Code patterns are researched and adapted, not blindly installed.

## Actual capabilities

- **Chat**: use GitHub connector to read `omega/AGENTS.md` and `omega/knowledge/latest-releases.md`; these do not automatically load in every unrelated chat.
- **Work**: read these files as project context; evaluate changes, test, and prepare PRs with available permissions.
- **Codex**: when working in `omega/`, follow `omega/AGENTS.md` and the specialized `omega/skills/omega-ai-updates/SKILL.md`.
- **Obsidian**: optional Markdown mirror of `omega/knowledge/`; requires device-side sync setup.
- **Existing capabilities**: preserve the repository's `codex-plugins/claude-skills-hub/` and the Hermes upstream sources. Do not duplicate or modify them as part of OMEGA.

## Update cycle

1. Daily ChatGPT automation researches important AI platform, agent and Skills changes.
2. Scheduled GitHub Actions reads *official GitHub Releases* metadata, runs tests and proposes versioned Markdown updates in a PR.
3. Review proposed changes before adoption; never auto-install unknown code.
4. After merge, Chat/Work/Codex may use updated shared files on demand.

## Source of truth

`omega/AGENTS.md` (policy); `omega/skills/omega-ai-updates/SKILL.md` (process); `omega/knowledge/latest-releases.md` (source-verified feed, not installation recommendations).

## Limitations

This cannot update the GPT model weights, ChatGPT system behavior, globally installed plugins or all open conversations. GitHub Actions may need manual enabling on forks, and permission for Actions to create PRs. All files are in a **public** repository: do not include private Obsidian notes, credentials or personal data.
