# Muse VM Courier — installation and operation

Scope: personal Muse VM to existing Muse ChatGPT Bridge.
This file is on a review branch; it is **not yet installed inside Muse VM**
and does not modify Render.

## Prerequisites
- An authorized personal Muse VM shell.
- The existing nanamarukanata-hub/hermes-agent checkout with branch
  feat/muse-vm-courier-20261030 (or the reviewed vm_courier.py copied
  into muse_bridge package).
- Python 3.10+, standard library only. No Muse account cookies.
- Do not send the Render OAuth approval key to Muse. The 600-second,
  single-use pairing code is separate.

## 1. Tests
Run from repository root:

    python3 -m unittest discover -s tests -p 'test_muse_vm_courier.py' -v

Do not proceed if tests fail.

## 2. Issue a pair code
From authenticated ChatGPT, call muse_pair_begin with no parameters
when Muse VM is ready. Old codes expire in 600 seconds.

## 3. Pair once from within Muse VM
Run from repository root:

    python3 -m muse_bridge.vm_courier pair

Enter the short-lived code at the hidden prompt. For noninteractive
shells only, create a temporary file with chmod 600:

    python3 -m muse_bridge.vm_courier pair --code-file /home/hatch/.pair-code

Delete the temporary file immediately after pairing. Never supply
the code as a command-line argument or write it to GitHub or logs.
The credential is saved to
~/.local/share/muse-bridge-courier/agent.token with mode 0600.
Do not display that credential.

## 4. Verify the HTTPS resident channel
Run:

    python3 -m muse_bridge.vm_courier poll --once

Within 90 seconds, call muse_agent_status from ChatGPT. The expected
connected_recently field is true. This confirms an actual authenticated
GET /agent/next, not merely token-file existence.

## 5. Run under existing Muse VM supervisor
Run:

    python3 -m muse_bridge.vm_courier poll

The courier only long-polls GET /agent/next?wait=25 and saves approved
work orders to a private inbox:
~/.local/share/muse-bridge-courier/inbox/<task_id>.json

No instructions are executed automatically. Muse must read/review each
item and confirm authorization before taking action. Duplicate delivery
will not overwrite an existing inbox item. The courier never auto-acks.

Use the Muse VM's existing keeper/supervisor restart hook to supervise
this one process after VM reconstruction. Do not create competing cron
jobs, expose an inbound listener, or presume nohup survives reboot.

## 6. Explicit completion after genuine work
After Muse actually performs an approved task, write a private JSON
file containing a nonempty result string, failed boolean and optionally
diff and test_results. Example:
{"result": "Tests completed; see report", "failed": false}

Then run:

    python3 -m muse_bridge.vm_courier complete <16-hex-task-id> --result-file /home/hatch/result.json

Completion POSTs /agent/complete only on explicit request. The inbox
file is archived to done only if server confirmation is received.
Do not claim work was done if it was not.

## Limits
- Render Free local task and OAuth state is ephemeral after redeploy.
- Changing MUSE_BRIDGE_OAUTH_APPROVAL_KEY invalidates paired tokens.
- A 401 means investigate and re-pair; do not publish any secret.
- connected_recently only proves recent authenticated HTTP polling.
- The script does not autonomously wake a Muse AI chat or execute tasks.


## Owner-owned AI agent Markdown configuration

The separate local module muse_bridge.agent_config_operator exposes six
explicit target names: hermes_soul, muse_soul, muse_agents,
muse_identity, muse_user, muse_memory. It does not modify credentials,
platform-owned system prompts, safety controls, or model weights.

A locally received Courier task must contain an explicit
AGENT_CONFIG_EDIT[muse_soul] marker for that exact target.
An ordinary development task is insufficient authority.

To read status without exposing the file contents:

    python3 -m muse_bridge.agent_config_operator --task-id TASK_ID --target muse_soul --operation status

Only after separate explicit owner approval for exact replacement text,
stage a private UTF-8 Markdown file at
~/.local/share/muse-bridge-courier/staging/replacement.md (chmod 600).
Use the SHA-256 returned by the status call:

    python3 -m muse_bridge.agent_config_operator --task-id TASK_ID --target muse_soul --operation apply --source-name replacement.md --expected-sha256 SHA256 --execute

The module creates a private backup, checks for concurrent modifications,
writes atomically, audits without logging file content, and refuses
repeat edits for the same task and target. A file write is not proof
that the hosted Muse runtime reloaded its prompts. Do not modify
actual agent instructions simply to test this feature: use unit tests
and a status-only call first.
