# Hermes Skills Hub — entire catalog access and safe export

The **public Skills Hub** at https://hermes-agent.nousresearch.com/docs/skills/
aggregates multiple registries, potentially tens of thousands of skills.
The number shown in the website UI is not the number installed on this fork,
and the official generated index can cover fewer entries than the live registries.

## What is already here?
The Hermes Agent fork already includes:
- `skills/`: bundled skills.
- `optional-skills/`: official optional skills (not automatically activated).
- `tools/skills_hub.py`: online registry adapters and search/install logic.
- `tools/skills_guard.py`: security scanner for downloaded skills.

Do not duplicate the entirety of community registries under `skills/`. The
registry is frequently changing and third-party content may include malicious
instructions/scripts, incompatible dependencies or copyrighted material.

## Get all skills that the upstream *index actually returns* — metadata only
Run locally:

```bash
python3 scripts/export_skillhub_catalog.py --output skillhub-export
cat skillhub-export/catalog-summary.json
```

The exporter downloads the index URL used by Hermes itself, checks structure,
and creates:
- `skillhub-export/skills-catalog.json` — the complete **index response**.
- `skillhub-export/catalog-summary.json` — count, per-source statistics, SHA-256
  and retrieval time.

It does not execute scripts, install skills, or commit enormous third-party content.
A mismatch between the declared and actual entry count exits nonzero.
A successful export is **not evidence that all community skill registries were exhaustive**.

### GitHub Actions
Use **Actions → Export Skills Hub Catalog (metadata only) → Run workflow**.
The resulting artifact is kept for 30 days. This workflow must be manually triggered
and never installs code. It does not push credentials or user files.

## Discover / inspect / selectively install safely
```bash
hermes skills browse
hermes skills browse --source official
hermes skills search music
hermes skills inspect official/creative/songwriting-and-ai-music
hermes skills install official/creative/songwriting-and-ai-music
hermes skills audit
hermes skills list
```

Bulk enabling 88,000+ third-party skills is intentionally NOT implemented:
registry search is already supported without copying their code, and indiscriminate
installation increases supply-chain attack risk and tool confusion.

## Runtime versus GitHub
Adding files to GitHub does **not** modify a running Hermes instance or
install them into `~/.hermes/skills/`. To activate built-in/optional skills,
use the CLI on the Hermes host and restart/reload the session as needed.

The skill `skills/hermes-skills-hub-companion/SKILL.md` provides a repeatable,
auditable workflow for searching, inspecting and installing selected Skills.
