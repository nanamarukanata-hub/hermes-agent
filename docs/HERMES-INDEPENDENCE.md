# Hermes independence audit — RP build-out status and Muse dependencies

Audit date: 2026-10-26, branch `feat/hermes-colab-dispatch-20261026`.
Principle (from the overhaul order): Hermes Agent itself is the
Discord RP character. No separate character bot/persona engine is built
on top of it; Muse is only the development environment. No
character name/personality/world content was invented, and no
RP-specific content restrictions were added.

## 1. What already exists in Hermes itself (verified in-repo)

| Capability | Where | Status |
| --- | --- | --- |
| Discord gateway adapter | `gateway/platforms/discord.py` (discord.py), token env `DISCORD_BOT_TOKEN` | Implemented upstream; channel/user session management, threads, reconnect handled by the gateway |
| RP hook in gateway path | `gateway/run.py` — `HERMES_RP_ENABLED` gate calling the hybrid RP flow | Implemented; integration tests pass (see below) |
| RP engine | `hermes_hybrid/rp_engine.py`, `session_manager.py` (Vault-backed sessions, per-user isolation, restore after restart) | Implemented + tested |
| Long-term memory | `hermes_hybrid/deep_memory.py`, `obsidian_store.py`, Vault layout `06_Automation/Hermes/{Sessions,Memory,CharacterState,Relationships,DeepMemory}` | Implemented; DeepMemory LLM auto-extraction is explicit-API only (by design) |
| Character state persistence | Vault `CharacterState` via session manager | Implemented + tested |
| Multi-agent | `tools/delegate_tool.py` (`delegate_task`, leaf/orchestrator roles, depth/concurrency caps) + kanban multi-agent board | Implemented upstream |
| Multimodal | toolsets `vision`, `image_gen`, `tts`, `video`, `stt` config sections | Implemented upstream |
| MCP | `mcp_serve.py` (serve), MCP client config, `hermes_hybrid/mcp_bridge.py`, catalog bridges | Implemented |
| Skills/plugins | 325 skill files, 552-entry plugin catalog, 15 plugin dirs | Implemented, load-on-demand |
| GitHub (dev side) | This repo + Actions CI; Hermes-side use via GitHub MCP (see EXTENSION-CATALOG) | CI verified; Hermes-owned GitHub credential not yet provisioned |
| Drive/Obsidian memory | `hermes_hybrid/drive_vault.py` (HTTP transport + env token) and Vault-canonical SessionDB | Implemented; see dependency table for the credential |
| Colab / Manager / Resident | `colab_manager.py`, `colab_job_recovery.py`, `resident_worker.py`, watchdog | Implemented + live-verified 2026-10-26 |

Tests run for this audit (local venv): `tests/hermes_hybrid` (80) +
`tests/test_gateway_rp_integration.py` + `tests/test_hybrid.py` +
`tests/test_hybrid_user_isolation.py` (23) = **103 passed**.

## 2. Not yet live (and exactly why)

| Item | Blocker |
| --- | --- |
| Hermes speaking on Discord (live) | `~/.hermes/config.yaml` on this VM is effectively empty (only `onboarding:`) and no `DISCORD_BOT_TOKEN` / LLM API key is provisioned. Both are operator secrets; they must be placed by the user in `~/.hermes/.env` (Hermes' own file), never via chat. After that: `hermes gateway run` (or the gateway service) + `HERMES_RP_ENABLED=1` and `HERMES_OBSIDIAN_VAULT` pointing at a local Vault sync/mount or the Drive transport. |
| Real-Discord multi-user test | Same as above; isolation logic is unit-tested, live test pending credentials |
| Keina RP assets migration | **Not found.** Repo-wide and Drive-Vault-wide searches for "Keina" return a single catalog line ("the Keina 92 Skills remain installed") inside `codex-plugins/claude-skills-hub/skills/claudeskills-hub-codex/SKILL.md`. No Keina skills/plugins/history exist in this repo or the Vault. Migration needs the actual source location from the user. |
| Colab real GPU run | Queued job waits for the user to run the Drive notebook (no official API to start Colab) |

## 3. Muse dependency inventory (the point of §9 of the order)

| # | Current dependency on Muse | Hermes-native replacement | Status |
| --- | --- | --- | --- |
| 1 | Resident worker reaches Drive through the Muse connector CLI (`cli_drive_transport.py`) | `drive_vault_from_env()` → `HttpDriveTransport` with `GOOGLE_DRIVE_ACCESS_TOKEN` (or ADC) held by Hermes | Code ready + unit-tested (`test_drive_env_transport.py`). **Credential not provisioned** — user must complete their own Google OAuth once and place the token/ADC for Hermes; until then the worker keeps using the connector channel |
| 2 | VM-regen recovery via Muse Runtime Hook (`hermes-worker-keeper`) | `deploy/hermes-boot.sh` + `deploy/hermes-resident-system.service` (systemd system unit) or any host boot mechanism on a Hermes-owned host | Kit added in this change. On THIS Muse-provided VM, boot persistence of /etc units is a platform property, so the Hook remains the working layer here; on Hermes' own host the kit is fully Muse-independent |
| 3 | GitHub pushes via Muse browser login | Hermes host git credentials (SSH key or PAT in Hermes' own store) + GitHub MCP for API ops | Not provisioned; CI itself is GitHub-native |
| 4 | Development itself (this session) | — | By design temporary; Hermes runtime does not import Muse code |
| 5 | Discord/LLM runtime | Hermes gateway + its own `.env` | See §2 — pending operator secrets only |

What is already independent today: the job queue and all memory live
on Drive (not in Muse storage); the Colab notebook runs under the
user's own Google account; the watchdog/worker processes run detached
from any Muse session; killing the Muse *conversation* stops
nothing. The remaining hard dependencies are #1 (Drive credential) and,
on this specific VM, #2 (boot layer) — both are provisioning steps,
not code gaps.

## 4. Go-live checklist for the operator (no chat secrets)

1. Put `DISCORD_BOT_TOKEN` and the LLM provider key into
   `~/.hermes/.env` on the host (Hermes' own file).
2. Set `model`/`provider` in `~/.hermes/config.yaml`; enable the
   discord platform; set `HERMES_RP_ENABLED=1`.
3. Provision Hermes' own Google credential (OAuth token or service
   account with access to the Vault) and set
   `GOOGLE_DRIVE_ACCESS_TOKEN` / `HERMES_DRIVE_VAULT_ROOT_ID`; the
   worker then stops using the Muse connector channel.
4. On a Hermes-owned host: install
   `deploy/hermes-resident-system.service` for boot recovery.
5. Start the gateway; run the §11 live tests (Discord conversation,
   restart restore, multi-user) and record results.
