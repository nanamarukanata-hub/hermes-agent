# Hermes × Colab Manager — design, backends and fault model

Hermes is the always-on control plane (Linux resident worker supervised
by the watchdog + `hermes-worker-keeper` Runtime Hook). Colab is an
opportunistic compute backend that a user starts by hand. The Drive
Vault folder `ChatGPT-Obsidian-Vault` / `06_Automation/Hermes/ColabJobs`
remains the only persistent queue and state store; nothing here moves
or duplicates it.

## Components

| Piece | Role |
| --- | --- |
| `hermes_hybrid/colab_dispatch.py` | Idempotent job submission + worker health (existing) |
| `hermes_hybrid/colab_manager.py` | Backend availability, routing with CPU fallback, 60s `tick()` = state publish + recovery reconcile |
| `hermes_hybrid/colab_job_recovery.py` | Job state machine + reconciler (retry/backoff/quarantine) |
| `hermes_hybrid/colab_worker.py` | Upstream bounded Colab notebook worker (existing) |
| `notebooks/hermes_colab_worker.ipynb` | Manager-edition Colab notebook: device detection (CPU/GPU/CUDA), heartbeat with device info, checkpoints, progress log, safe interruption marking |
| Resident worker + watchdog + keeper Hook | Supervised Linux execution and VM-level recovery (existing, unchanged) |

`ColabManager.tick()` is designed to be called every ~60 seconds from
an *existing* loop (e.g. the resident worker's host wrapper). It adds
no new resident process and no AI polling task.

## Official Colab API — investigation result

The REST API documented at developers.google.com/colab is **Colab
Enterprise** (Vertex AI, `aiplatform.googleapis.com`): it requires a
GCP project with billing enabled, the Vertex AI API, IAM roles such as
`roles/aiplatform.colabEnterpriseUser`, and GCP credentials (ADC /
service account). This deployment has no GCP project credentials, so
the API is **not usable here** and no runtime can be created or started
programmatically. Classic (free) Colab exposes no official runtime API.

Consequences, by design:

* Hermes never auto-starts Colab. The user opens
  `hermes_colab_worker.ipynb` from Drive and runs it; Hermes detects
  the start from the worker heartbeat (`worker.json`, ≤120s old).
* No unofficial APIs and no browser automation are used to start or
  keep a runtime alive.
* `probe_colab_enterprise()` reports configuration honestly
  (`not_configured` / `credentials_missing` / `configured_unverified`)
  and never claims availability. If a GCP project + credentials are
  provisioned later, a real Enterprise backend can be registered
  behind the same manager interface, as can an external GPU service or
  a self-hosted GPU host.

## Job states and recovery

States: `pending → running → completed | failed`, with
`checkpointed`, `interrupted`, `retrying` for the failure path.

* A job that produced `result.json` is terminal and is **never**
  re-executed (side-effect prevention).
* `running` whose status is older than 180s = `interrupted`
  (worker/runtime died mid-job).
* Interrupted → `retrying` with exponential backoff
  (30s × 2^attempts, capped 900s) → requeued with attempts + 1.
* Attempts exhausted (default 3) → `failed` + `quarantine.json`
  (manual review; never auto-requeued).
* Every reconcile action is appended to `recovery-events.jsonl` in the
  queue root; the manager publishes `manager-state.json` each tick.
* Duplicate execution is prevented by job-ID idempotency in dispatch,
  the resident worker's file lock, executor separation (Colab never
  touches resident jobs and vice versa), and the reconciler re-checking
  for a result immediately before requeueing.

## Routing

`ColabManager.submit()` accepts `requires_gpu` and `allow_fallback`
(default true):

* GPU job + Colab online with GPU → Colab.
* GPU job + Colab offline / no GPU → resident CPU fallback
  (job still completes; result records the executing backend).
* Fallback disallowed → job stays queued for a GPU backend
  (`decision: wait`); queuing never loses the job.

## Fault model (A–J)

| # | Fault | Detection | Recovery |
| --- | --- | --- | --- |
| A | Colab runtime disconnect | `worker.json` stale > 120s | Jobs stay queued; GPU jobs fall back or wait; resume on next notebook start |
| B | GPU deallocation | Heartbeat device info `gpu.available=false` | Routing switches to CPU fallback / wait |
| C | Resident worker stops | Keeper Hook process check (60s) | Watchdog restart (proven live) |
| D | Watchdog stops | Keeper Hook process check | Hook relaunches via `start.sh` (proven live) |
| E | VM regeneration | New boot + Hook polls | Hook restarts watchdog/worker (mechanism proven live; console stack proves Hook runs in new boots) |
| F | Drive transient failure | Transport errors | Bounded retries in DriveVault + heartbeat retry (tested) |
| G | Job interrupted mid-run | Stale `running` status | Reconciler: interrupted → retrying → requeue (proven live with simulated crash) |
| H | Double start | flock singleton (resident), one-notebook guidance (Colab) | Second instance exits / skips claimed jobs |
| I | Auth expiry | Colab: notebook auth cell fails visibly to the user; Hermes side uses the authorized connector channel (no stored token) | Manual re-auth by the user in Colab UI; manager reports backend offline meanwhile — never faked |
| J | Network outage | Heartbeat/status writes fail | Backoff + persistent Drive queue; everything resumes when connectivity returns |

Cases that cannot be automated (Colab start, Google re-auth) are
surfaced as explicit waiting states, never as success.

## What 24h operation guarantees — and what it does not

Guaranteed by construction + live tests: the Hermes control plane
(resident worker) recovers automatically from process death, watchdog
death and heartbeat stalls; jobs are persisted on Drive and are not
lost when Colab or the worker dies; interrupted jobs resume
automatically. Not guaranteed: free Colab availability or session
length (Google-controlled), and VM uptime itself — the VM can be
regenerated; recovery after regeneration is automatic via the keeper
Hook, with the Drive queue as the durable state.
