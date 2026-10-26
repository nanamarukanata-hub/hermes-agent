# Hermes → Google Colab invocation (Drive queue, no always-on claims)

## What works

Hermes Discord's model toolset contains two new tools when configured:

- `colab_submit` — enqueue allowlisted `echo`, `text_stats`, `hash_file` jobs
- `colab_status` — read job lifecycle, result JSON, and worker heartbeat

The canonical Drive Vault folder is the **only** persistent queue and result store:

    ChatGPT-Obsidian-Vault/
      06_Automation/Hermes/ColabJobs/
        worker.json
        job-<id>/job.json
        job-<id>/status.json
        job-<id>/checkpoint.json
        job-<id>/result.json
        hermes-colab-queue-worker.ipynb  (worker notebook, user-started)

No SQLite, second queue database, made-up RP character, or 5-minute AI task.

## Hermes authentication and configuration

On the Hermes Linux runtime, use an **authorized** Google Drive OAuth flow
that has access to the existing shared Vault. Pin the canonical Vault root:

    HERMES_DRIVE_VAULT_ROOT_ID=1DEgP8VbExIRrizosKK4Jo0GOhet9oni3

Two credential sources are supported:

1. **Renewable credentials (recommended):** provide a secure file containing
   an authorized OAuth user's or a service account's Google Application Default
   Credentials (ADC), configure `GOOGLE_APPLICATION_CREDENTIALS` to that
   protected file path, and install the optional `google-auth` Python package
   on Hermes. The Colab tool refreshes tokens as needed; never place this
   credential JSON in GitHub, Obsidian, Colab notebook or chat. The Google
   identity represented by the file must have access to the existing folder.
   The Colab user sign-in is NOT a portable Hermes credential.
2. **Short-lived credentials:** set `GOOGLE_DRIVE_ACCESS_TOKEN` from an
   existing runtime-managed OAuth provider. The existing DriveVault transport
   reads this env value but **does not refresh it**, so it will expire.

The root ID above was verified as **ChatGPT-Obsidian-Vault**. Credentials are
not created or exported by this PR. No unattended work begins without the
real Hermes runtime being independently authorized.

When the pinned Vault ID or both credential methods are absent, the two new
model tools are **not advertised** by the Hermes registry. When the token expires, commands
return an error rather than claiming success. No production activation is
performed by this code branch.

## Colab worker

The generated `hermes-colab-queue-worker.ipynb` notebook has three cells:
introduction, Google-provided OAuth/Drive mount, and a bounded queue worker.
The notebook contains no passwords, cookies or tokens.

1. Open the notebook in Colab on iPhone, using the Google account that can
   access the canonical Vault folder.
2. Run the **authentication cell**, approve Google's own prompts if needed.
   Do not share any 6-digit code with the agent or GitHub.
3. Run the **worker cell**. It will poll Drive every 15 seconds for up to
   30 minutes or 50 completed jobs, whichever comes first.
4. Leave the cell executing while Hermes submits jobs. Hermes reads the
   same result/status files via its configured Drive credentials.
5. When Colab stops (runtime idle/reconnect/expiration), queued jobs remain
   untouched in Drive. Re-run the worker cell after reconnect.

**Limitation:** Google Colab free/standard interactive notebooks do not
provide a supported, durable, always-available API that an external Linux
process can rely on to boot an idle notebook remotely. "Hermes calls Colab"
here means Hermes dispatches asynchronously and Colab picks up the work
**while the user-started worker is active**. The worker never operates
unattended after its session expires.

The worker handles only allowlisted operations, refuses arbitrary code and
paths outside `06_Automation/Hermes/`. One active worker is supported;
Google Drive mounted folders are eventually consistent, not a transactional
multi-consumer queue. For multi-worker exactly-once guarantees, use a proper
server-run worker with atomic leasing instead of free Colab.

## Continuous Linux worker (24h-capable service)

**Not an always-on Colab runtime.** The supervised Linux worker continues
running while the underlying Muse or Hermes VM is available, even after the
user closes Colab. Whether that VM truly remains available for 24 hours is
an operational property that requires a real 24-hour measurement; code/CI is
not proof of uninterrupted service. VM replacement/restart needs an active
Supervisor/Hook or user-systemd service.

Job routing:

- `{"executor":"colab"}` or omitted: only a *manually-started* Colab
  notebook executes the job. No remote restart claim.
- `{"executor":"resident"}`: only the **Linux resident worker**
  processes the job. The Colab notebook skips it to avoid dual execution.
- Both executors consume the same canonical Drive Vault path, with separate
  `worker.json` (Colab) and `resident-worker.json` (Linux) heartbeats.
- Resident computation is intentionally restricted to `echo`,
  `text_stats`, and `hash_file` (no arbitrary remote Python execution).
  Existing Colab jobs and completed jobs are never re-run.

Example Hermes native tool call:

    colab_submit({
      "executor": "resident",
      "type": "echo",
      "message": "Check Hermes resident worker availability"
    })

Once the **authorized** Hermes Linux runtime has access to the canonical
Drive Vault, from the verified Hermes checkout path:

    python3 -m hermes_hybrid.resident_worker --once
    python3 -m hermes_hybrid.resident_worker --interval 30

The `--once` option scans one queue cycle, then leaves a `stopped`
heartbeat. Continuous mode checks the Drive JSON queue every 30 seconds
(**I/O only; not a repeating AI task**). A singleton `flock` prevents two
copies under the same Unix user on one host. Do NOT use multi-replica
deployment without a stronger distributed leasing mechanism.

To survive process crashes/reboots under Linux `systemd --user`:

1. First verify the real checkout path and Python interpreter in
   `deploy/hermes-colab-resident.service`; adapt `WorkingDirectory`
   and `ExecStart` to the target VM. The template alone does nothing.
2. Keep the actual ADC file readable only by the intended service account,
   outside the repository. In the private
   `~/.config/hermes/colab-resident.env`, put only the authorized file
   **path** and the canonical Drive folder ID, such as

       HERMES_DRIVE_VAULT_ROOT_ID=1DEgP8VbExIRrizosKK4Jo0GOhet9oni3
       GOOGLE_APPLICATION_CREDENTIALS=/absolute/private/path/to/owner-authorized-adc.json

   Do not include the credential JSON or OAuth tokens in this file.
3. Install user service and restart it using the VM's systemd, or register
   the same single process with the **existing Muse Supervisor/Hook**:

       mkdir -p ~/.config/systemd/user
       cp deploy/hermes-colab-resident.service ~/.config/systemd/user/
       systemctl --user daemon-reload
       systemctl --user enable --now hermes-colab-resident.service
       systemctl --user status hermes-colab-resident.service

   When authorized by the VM owner, use `loginctl enable-linger <user>` to
   allow a **user** systemd manager after logout. On hosted VMs, prefer
   their existing real supervisor and boot recovery mechanisms instead.
4. Verify restart independently and use a new unique echo job, then read
   Drive `status.json`, `result.json`, and `resident-worker.json`.
   Do not kill an existing Hermes gateway, Discord worker, Supervisor or Hook.
5. For a 24h uptime claim, collect 24h wall-clock heartbeats and post-reboot
   evidence. A 24h guarantee requires infrastructure SLA/redundancy, not
   just a service definition. Report downtime/restarts honestly.

**Operational limitation:** One VM with local `flock` and Google Drive as
queue storage is not an exactly-once distributed queue. Competing machines
must never both run this resident worker against the same queue. The workers
recover stale `running` **idempotent** jobs after 120 seconds, repair
`result.json`-saved-but-status-not-final crash cases, and leave the
Colab-specific queue alone.

### Muse VM: existing authenticated Workspace CLI (no new OAuth secrets)

On the Muse VM, `/opt/hatch/bin/hatch_gws_cli` provides a user-authorized
Drive connector. This is a **different** credential source from the
Google-account login inside Colab. `hermes_hybrid/cli_drive_transport.py`
supports this existing CLI without extracting its tokens. The operator should
first verify `hatch_gws_cli drive status` reports connected **in the VM**
before selecting the provider.

Set these non-secret values in private `~/.config/hermes/colab-resident.env`
(permissions `chmod 600`, owned by the same Unix user):

    HERMES_DRIVE_VAULT_ROOT_ID=1DEgP8VbExIRrizosKK4Jo0GOhet9oni3
    HERMES_DRIVE_PROVIDER=hatch
    HERMES_GWS_CLI=/opt/hatch/bin/hatch_gws_cli

Then, in the actual Hermes checkout, once verified by the owner:

    python3 -m hermes_hybrid.resident_worker --once
    bash deploy/start-hermes-colab-worker.sh

The launcher spawns `deploy/hermes-colab-watchdog.sh`. `flock` prevents
multiple watchdogs on the **same Unix host**, and the watchdog will restart
the resident process if that process exits. It **does not** reboot an offline
VM or respawn after the VM is destroyed and re-created. The host's
authenticated boot/recovery hook or an independent always-on supervisor
must relaunch the script for true crash/reboot survivability. Do not modify
the existing Muse console-only Supervisor and Hook blindly.

Muse's independent read-only task report showed a worker kill/restart test
(PID 7233 → 7973 → 8484) and a completed resident echo job. Google Drive
independently contained `job-resident-echo-20261026-01` with `done`
status and `result.ok=true`, and `resident-worker.json` was running
on October 26, 2026. That is proof of startup and job delivery, **not**
24 hours of uninterrupted availability or VM re-creation recovery.

## Native tool examples

When Drive OAuth is configured, from the Hermes Discord bot say:

    Have Colab compute stats for 06_Automation/Hermes/IntegrationTests/drive-vault-test-20261026.md.

The agent can submit:

    colab_submit({
      "type": "text_stats",
      "input": "06_Automation/Hermes/IntegrationTests/drive-vault-test-20261026.md",
      "job_id": "stats-first-demo"
    })

It returns a queued response and `worker.online`. Later:

    colab_status({"job_id": "stats-first-demo"})

Or start with:

    colab_submit({"type": "echo", "message": "Hermes calls Colab"})

Never imply the job has run just because it was queued.

## Verifications

The dedicated `.github/workflows/colab-dispatch-ci.yml` runs tests covering:
tool registry, path isolation, ID collisions, result readback, worker liveness,
notebook compilation and execution of the **actual generated worker code**
against a fake Drive mount, plus existing DriveVault regression tests.

A successful prior manual run of `job-drive-test-20261026` verified Google
OAuth, Drive mount, `status.json=done`, `result.json.ok=true` and result
readback. That prior test was a manually started notebook, not proof that
Hermes can yet remotely dispatch to the authenticated Colab worker.

All changes remain in a separate development branch until integrated and
configured. Do not merge or deploy implicitly.
