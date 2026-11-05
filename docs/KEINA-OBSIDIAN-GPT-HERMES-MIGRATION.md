# Keina RP skills + Obsidian GPT -> Hermes migration

## Existing sources
- Private source repository: `nanamarukanata-hub/keina-discord-bot`; GitHub listing showed 92 entries under `skills/` (including a README file). This is an inventory, **not proof of installation**.
- RP-related source skill directories include `agent-rpg`, `ai-persona-engine`, `ai-persona-os`, `daily-roleplay-game`, `fanfic-writer`, `lore`, `open-persona`, `openclaw-tavern`, `personality-switcher`, `roleplay`, `soul-generator`, `venice-characters`.
- Keina also has `codex-plugins/keina-skills` and a separate `keina_rp_engine.js`; these are not equivalent to Hermes native skills. Audit their entry points and dependencies before porting.
- Canonical Vault is the **existing Google Drive folder** `ChatGPT-Obsidian-Vault` (folder ID `1DEgP8VbExIRrizosKK4Jo0GOhet9oni3`). Do not create a second canonical local Vault.
- Existing Obsidian GPT structure includes `GPT-HOME.md`, `AGENTS.md`, `00_System` through `09_Archive`, and existing Hermes/bridge content. Preserve existing user files and metadata.

## Migration order
1. Snapshot inventories and compare skill manifests, versions, licenses, permissions, and dependencies. Never execute imported skill scripts before review.
2. With an authorized checkout of the private Keina repository, run `python scripts/import_keina_skills.py /path/to/keina-discord-bot --dry-run`, inspect output, then run without `--dry-run` on the development branch. The importer stages files in `skills/keina-import/`; verify Hermes skill discovery supports this nesting before enabling any of them.
3. Port Keina-specific JavaScript RP engine and Codex plugin APIs through a compatibility adapter where needed; do not silently assume native Hermes support.
4. Adapt Obsidian GPT workflows and Markdown schemas to Hermes sessions, memory, tools, and skills while retaining existing notes. Use a Drive API transport or verified mount; a Drive folder ID is not a local path. Do not write to SQLite or a separate authoritative JSONL.
5. Add tests for skill discovery, RP context continuity, concurrent Vault writes, Drive revision conflicts, restart recovery, and readback from the actual Google Drive Vault using sanitized fixtures.
6. Run CI on Hermes PR #11 and APEX-6 PR #16. Keep both Draft; no merge, production deployment, data deletion, secret disclosure, or unsolicited character/world creation.

## Important limitations
- Source inventory and staging tool do not constitute installation or activation.
- Actual Google Drive Vault read/write, imported skill runtime compatibility, and CI for the new importer are not yet verified.
- The user's request is for full import; report exact count successfully imported and separately report items blocked by missing dependencies or incompatibilities.
