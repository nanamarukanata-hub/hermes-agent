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

On the Hermes Linux runtime, use an **authorized, renewable** Google Drive
OAuth flow that has permission to access the existing shared Vault. Configure
the transport already present in `hermes_hybrid.drive_vault`:

    HERMES_DRIVE_VAULT_ROOT_ID=1DEgP8VbExIRrizosKK4Jo0GOhet9oni3
    GOOGLE_DRIVE_ACCESS_TOKEN=<OAuth access token obtained securely at runtime>

The folder ID above was checked against the existing shared Google Drive folder
**ChatGPT-Obsidian-Vault**. Never post tokens in Discord, ChatGPT, GitHub,
notebooks, or logs. The existing transport reads the token from the environment;
it **does not refresh it**. For durable production operation, integrate with a
proper Google OAuth refresh mechanism managed by the runtime (or a genuine,
verified live Drive mount). Colab's OAuth does not grant credentials to Hermes.

When either environment variable is absent, the two new model tools are
**not advertised** by the Hermes registry. When the token expires, commands
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
