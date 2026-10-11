---
name: claudeskills-hub-codex
description: Discover any public ClaudeSkills.info Skill by keyword and GitHub repository, inspect exact SKILL.md upstream, then selectively install vetted compatible skills for Codex. Use when the user asks to find, install, enable, compare or migrate Claude Skills into Codex.
---
# ClaudeSkills Hub for Codex

This skill bridges the ClaudeSkills.info indexed directory. The site's public
directory has over 42k entries, but individual projects and Skills may be
incompatible or have no reusable license. Directory visibility != installation.

Tools via `claude_skills_hub` MCP:
- `skills_search`: find skill metadata from the indexed directory
- `skills_inspect`: show original GitHub repository and directory source
- `skills_catalog_status`: actual indexed/available count and runtime readiness
- `skills_refresh_index`: rebuild index from public site (network, read-only)
- `skills_install_codex`: install one exact skill in this Codex project using
  verified npx Skills CLI, only upon user authorization

For a matching task, search the index and prefer reviewed, reputable source
repositories; read the actual upstream SKILL.md and verify dependencies before
installing. Never execute arbitrary community scripts without assessment.

If all skills were requested, make all **catalog entries searchable** but do
NOT auto-activate 42k untrusted instructions: large numbers of conflicting
instructions, supply-chain risks, dependency/OS limitations and licenses are
independent of the user's willingness to install them. Instead offer on-demand
installation of the selected Skill, and maintain exact success/failure states.

The previous Hermes 551-plugin catalog and the Keina 92 Skills remain installed
as separate Codex marketplace packages. Do not confuse those with this index.

The local Python MCP server only reads metadata until an explicit install
or refresh tool is invoked. It does not claim to connect ChatGPT services.
