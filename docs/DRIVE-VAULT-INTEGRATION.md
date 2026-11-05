# Hermes × Google Drive Obsidian × Colab Integration

## Canonical-store principle

The existing Google Drive folder **`ChatGPT-Obsidian-Vault`** is the
single canonical persistent store for Hermes. There is no second
canonical "local Vault":

- A local directory may only be (a) a verified mount/sync view of that
  Drive folder (e.g. the Colab Drive mount, Google Drive for desktop),
  or (b) a disposable cache. Code must not treat a local Vault as the
  only copy of anything.
- The Vault root folder is never created, moved, renamed, or migrated
  by Hermes code. It is located by name (or pinned by folder id) and
  only read/written below that root.
- No existing data is deleted by the integration. Record removal in
  the Drive store is a move to the Drive trash (recoverable).

## Layout (unchanged Vault tree, now Drive-backed)

```
ChatGPT-Obsidian-Vault/
  06_Automation/Hermes/
    Sessions/<session_id>/        canonical session records (SessionDB)
    GatewayRouting/sessions.json  session-key -> id index
    Kanban/<board>/               Kanban board store (Vault JSON)
    Memory/  DeepMemory/          long-term memory stores
    ColabJobs/<job_id>/           Colab job definition/status/checkpoint/result
    WorkerLogs/  DiscordThreads/  record stores (DriveVaultDir)
```

## Access modes

1. **Drive API mode** — `hermes_hybrid/drive_vault.py`.
   `DriveVault` addresses files by Vault-relative path over a
   `DriveTransport`. `HttpDriveTransport` speaks Drive API v3 using an
   OAuth access token supplied by the runtime environment
   (`GOOGLE_DRIVE_ACCESS_TOKEN`); the existing OAuth grant is reused,
   no OAuth flow is rebuilt, and tokens are never stored or logged.
   `HERMES_DRIVE_VAULT_ROOT_ID` may pin the root folder id.
2. **Verified mount mode** — when the Drive folder is mounted locally
   (Colab: `/content/drive/MyDrive/ChatGPT-Obsidian-Vault`), the
   existing local stores (`SessionDB`, `VaultDir`) operate with
   `HERMES_OBSIDIAN_VAULT` pointing at the mount path. The mount is a
   view of the same canonical folder, not a separate Vault.

## Write safety (Drive API mode)

- **Optimistic concurrency:** callers may pass the revision obtained
  from a prior read; a mismatch refuses the write with
  `DriveConflict` *before* anything is overwritten.
- **Write verification:** every write is re-downloaded and
  hash-compared; a mismatch raises instead of reporting success.
- **Transient failures:** rate-limit/5xx/network errors retry with
  bounded backoff; exhaustion raises `DriveUnavailable` and the
  previously stored content remains intact.
- **Path safety:** Vault-relative paths only; `..`, absolute paths,
  and backslashes are rejected before any API call.

`DriveVaultDir` gives the record stores (Kanban ancillary state,
worker logs, Discord thread state, API responses) the same
read/write/list semantics as the local `VaultDir`, on Drive.

## Conversation history (Step 2)

The gateway transcript has exactly one canonical copy: the Obsidian
SessionDB. `SessionStore.append_to_transcript`/`rewrite_transcript`
no longer write a legacy JSONL mirror, and `gateway/mirror.py` mirror
records go to the SessionDB only. A legacy JSONL file that still
exists is read-only recovery input: `load_transcript` merges it with
the canonical history using multiset deduplication, so shared
messages appear exactly once and pre-migration history is never
truncated. Existing JSONL files are left on disk untouched.

## Colab (Steps 8-9)

`hermes_hybrid/colab_jobs.py` defines the job contract. A job is a
folder `ColabJobs/<job_id>/` in the Vault containing `job.json`
(definition), `status.json`, `checkpoint.json`, and `result.json`.
Only safe, declarative job types exist (`echo`, `text_stats`,
`hash_file`) — a job spec cannot carry arbitrary code.
`build_colab_notebook(job_id)` generates a self-contained notebook
that mounts Drive, reads the job from the Vault, and writes the
result back to the same folder, implementing the same semantics as
the reference runner `run_job_spec`, so a result produced in Colab
and a result produced locally are interchangeable.

## APEX-6 shared contract (Step 6)

APEX-6 and Hermes share this Vault as their common persistence base:
both address the same `06_Automation/Hermes/` tree, use the same
path-safety rules, and treat Drive revision checks as the conflict
mechanism when both sides write. Neither side may introduce a
private canonical store (local SQLite or a local-only Vault) for
state that belongs to the shared tree. SQLite remains absent from
the Hermes runtime stores (migration tools may read legacy SQLite
files read-only).

Verified APEX-6 side (PR #16, branch
`feat/obsidian-only-storage-20261025`, investigated 2026-10-25/26):

- `src/apex6/vault_store.py` is APEX's Vault store: records are
  Markdown files under `06_Automation/Hermes/Memory/`, append-only
  supersedes, file locking, symlink escape rejected, SQLite paths
  refused. Its root is configured by `APEX6_VAULT`, falling back to
  the shared `HERMES_OBSIDIAN_VAULT` — the same env contract as
  Hermes. Pointing either variable at the Drive Vault (API mode on
  the Hermes side, mount/sync view on the APEX side) puts both
  systems on the one canonical Drive folder with no code change
  needed in APEX's store.
- APEX's CI (`.github/workflows/test.yml`) already cross-verifies
  the Hermes PR #11 branch, so the two branches are pinned against
  each other in CI.
- APEX-6 has **no MCP server**: it is a finite CLI (`apex6`
  subcommands). Today the integration surface is therefore
  CLI invocation + the shared Vault tree, not an MCP transport.
  An MCP server inside APEX-6 is new work for that repository; it
  is not hidden or half-present, and must not be reported as
  connected until it exists and is exercised end to end.
- The GitHub connector used by Muse cannot read apex-6-private
  (the Muse GitHub App is not installed on that repository), so
  APEX-side verification happens through the user's signed-in
  browser session until the App installation is extended.

## Environment summary

| Variable | Meaning |
| --- | --- |
| `HERMES_OBSIDIAN_VAULT` | Vault root (mount mode) or local view path |
| `HERMES_DRIVE_VAULT_ROOT_ID` | Optional pinned Drive folder id of the Vault root |
| `GOOGLE_DRIVE_ACCESS_TOKEN` | OAuth access token for Drive API mode (runtime-supplied) |
