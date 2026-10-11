---
name: hermes-skills-hub-companion
description: Search and evaluate the full Hermes Skills Hub ecosystem, inspect origin/security/dependencies, and install selected skills on demand. Use when the user wants many Hermes skills or asks about Skills Hub.
version: 1.0.0
metadata:
  hermes:
    tags: [skillhub, registry, verification, source-security]
    category: productivity
---
# Hermes Skills Hub Companion

The public Hub is a dynamic registry aggregator, not a trustworthy single downloadable collection. Hermes already contains bundled skills and registry adapters. The website may claim more skills than are present in any cached snapshot.

## For every request to "install all"
1. Explain the distinction between **catalog access**, **metadata export** and **actually installed/executable skills**.
2. For on-demand discovery, prefer built-in `hermes skills browse`, `hermes skills search <query>`, `hermes skills inspect <identifier>`.
3. For full metadata snapshot, use `python3 scripts/export_skillhub_catalog.py --output skillhub-export`. Check `catalog-summary.json`: index declared count, loaded entries and source breakdown. This is metadata, not installed code.
4. Never run mass installation from arbitrary community registries: malicious code, hidden dependencies, licensing and disk consumption risks. Do not bypass blocked security findings with `--force`.
5. User-selected install: inspect each exact skill ID, review SKILL.md, scripts, source URL, dependencies, exfiltration behavior, shell commands and trust verdict. Only then use `hermes skills install <source-qualified-identifier>` and `hermes skills audit`.
6. Confirm with `hermes skills list` and test the actual workflow before claiming that the skill is installed or works.
7. Keep optional API keys and secrets outside GitHub; never persist them in Skill or snapshot files.
8. For automatic updates, explicitly separate periodic *catalog refresh* from *skill code updates*. Neither is a substitute for a review.

## CLI reference
- `hermes skills browse`
- `hermes skills search music`
- `hermes skills search music --source skills-sh`
- `hermes skills inspect official/creative/songwriting-and-ai-music`
- `hermes skills install official/creative/songwriting-and-ai-music`
- `hermes skills list --source hub`
- `hermes skills check`
- `hermes skills audit`
- `hermes skills tap add owner/repo`

Some commands need an installed Hermes runtime. GitHub repository changes do not install anything on the user's running host.
