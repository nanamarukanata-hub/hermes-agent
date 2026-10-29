# Hermes Discord + Obsidian-only storage

This branch removes the runtime SQLite `SessionDB` implementation.
The gateway's session key index, message transcripts, usage counters, and
participated Discord threads are saved inside a **local filesystem-backed**
Obsidian Vault. Do not mount Google Drive as a live POSIX lock provider.

## Before starting a gateway

Set `HERMES_OBSIDIAN_VAULT` to an existing local Vault directory.
The process will fail at startup when no Vault is configured; it will not
create a state.db or fall back to the old SQLite state store.

```bash
export HERMES_OBSIDIAN_VAULT=/srv/obsidian/ChatGPT-Obsidian-Vault
test -d "$HERMES_OBSIDIAN_VAULT"
python -m unittest discover -s tests -p 'test_obsidian_session_state.py' -v
```

Persistent session files are under
`06_Automation/Hermes/Sessions/<session_id>/metadata.json` and
`Transcript.md`. Gateway session routing and Discord thread participation
go to `06_Automation/Hermes/GatewayRouting/`.


Google Chat conversation-routing thread counters and the non-secret bot ID
cache are stored under `06_Automation/Hermes/GoogleChat/`. Before enabling
Google Chat on an existing installation, stop the gateway and copy
`$HERMES_HOME/google_chat_thread_counts.json` to
`$HERMES_OBSIDIAN_VAULT/06_Automation/Hermes/GoogleChat/google_chat_thread_counts.json`
if the legacy file exists and the Vault copy does not. Preserve the original
as a backup; do not overwrite an existing Vault copy. Startup refuses to
silently reset legacy thread counters because that can mix formerly isolated
conversations. Google Chat OAuth tokens and credentials are **not** copied
to the Vault, since a synced Vault is not a credential store.

## Optional one-time migration of legacy history

Stop Hermes CLI and the Discord gateway before the write step. Preserve a
separate backup of the original database and WAL/SHM files. The source database
is read-only to the migration code and is **never automatically deleted**.

```bash
python scripts/migrate_sessions_to_obsidian.py --source ~/.hermes/state.db
# Inspect the reported counts. Only then:
python scripts/migrate_sessions_to_obsidian.py --source ~/.hermes/state.db --commit
# With the gateway still stopped, verify every migrated session and message:
python scripts/migrate_sessions_to_obsidian.py --source ~/.hermes/state.db --verify-only
```

The `--verify-only` mode is read-only and compares complete source session metadata
and message contents with Vault records. It fails on missing or conflicting
sessions; aggregate counts alone are not treated as proof of migration.
Do not use this verification while the gateway is modifying session records.

The migrator's `sqlite3` dependency is offline and one-time. It is not used by
the Hermes runtime. Stop on conflicts instead of overwriting newer Vault state.

## Required acceptance tests before merge/deploy

- Compare original session/message counts with the migrated Vault.
- Test real Discord messages, concurrent replies, attachments and restarts.
- Verify restored conversation includes tool calls and multimodal content.
- Verify search, usage stats, session switching and session compression.
- Confirm no `state.db`, SQLite WAL or SHM are created during test startup.
- Confirm no durable Discord state is written outside the Vault.
- Execute the full regression suite and monitor failure logs for at least 24h.

## Current status

**Development only.** Changes are not merged or deployed. Full CI, actual
Discord connectivity and 24h continuous uptime are not yet established.
Some platform-specific features such as Telegram topic bindings are not
yet implemented by the Obsidian file backend. Do not deploy this branch as a
drop-in replacement for multi-platform production gateways.
