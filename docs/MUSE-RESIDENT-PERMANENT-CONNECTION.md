# Muse ↔ ChatGPT resident channel (one pairing, automatic reconnection)

**Status: protocol implemented in GitHub; Render deploy and actual Muse VM installation are separate acceptance steps.** This is a **cookie-free alternative** to `nikships/muse-cli`. It does not impersonate or bypass the personal Muse app login.

## What the user actually wants

After the first connection:

- In the iPhone ChatGPT app: submit a Muse work order.
- The **existing** Muse Secure VM's always-on, non-AI courier maintains **outbound HTTPS long polling** to the Render Bridge. The courier reconnects automatically after network interruptions and runtime restarts.
- Authorized work orders are saved exactly once in `/home/hatch/muse-bridge/inbox` on Muse's persistent volume.
- Muse runs an **event-triggered** work handler for *new* tasks using its actual supported local platform interface, writes results to `/home/hatch/muse-bridge/outbox/<task_id>.json`, and the courier returns them to Render.
- No Muse account Cookie, no new 5-minute AI tasks, no manually pasting work-order links each time, no public inbound VM port, and no GitHub Issues as a message bus.

**Important limitation:** `agent_courier.py` is a **delivery client, not an agent activation API**. The *Muse Secure VM must wire the existing native event/hook interface* to the inbox. This has NOT been done or verified by this GitHub/Render-only workspace. The courier does not call `wake` itself and **does not automatically execute instructions**.

## Architecture

```
 iPhone ChatGPT
   → OAuth-protected POST /mcp, muse_send_instruction + explicit muse_approve
     → Render in-memory/on-filesystem approved queue
       ← long-poll GET /agent/next?wait=20 (Muse VM courier)
         → persistent Muse inbox JSON
           → Muse-native on-new-message activation [NOT YET WIRED]
             → Muse execution / tests / changes
               → persistent Muse outbox JSON
                 → POST /agent/complete (courier)
                   → ChatGPT muse_get_status / muse_get_result
```

## One-time pairing

1. Confirm the Plugin OAuth connection on the iPhone. Invoke `muse_pair_begin` (an authenticated MCP tool) exactly once. It returns a high-entropy code with 10-minute expiry. **Do not share the code publicly.**
2. Open the personal Muse app and tell Muse to use the approved checked-in `muse_bridge/agent_courier.py` in its existing Secure VM. In Muse VM, save the temporary pairing code into a local private file (`chmod 600`) rather than putting it on the command line.
3. With that code file, the Muse VM performs:
   ```sh
   python3 /home/hatch/hermes-agent/muse_bridge/agent_courier.py pair \
       --code-file /home/hatch/muse-bridge/pair-code
   python3 /home/hatch/hermes-agent/muse_bridge/agent_courier.py check
   ```
   After pairing **remove the temporary pair-code file**. The client stores the real bearer credential only in `/home/hatch/muse-bridge/agent-token` mode 0600.
4. Ask Muse to **attach the courier launch to the existing `muse-console-keeper` VM Runtime Hook or existing Supervisor**, using its supported update method, without deleting/replacing existing console management. No new periodic AI task. Start exactly one detached resident `python3 .../agent_courier.py serve` process on boot, track PID and failures, and validate after a natural VM rebuild.
5. Muse must also implement a *new-work event dispatch* via an authorized local Muse interface that does not create a periodic AI task; it should use the task ID as idempotency key. Do not improvise undocumented Muse internal APIs or run a shell command from untrusted work-order text.

The credential is HMAC-verified on Render using a separate key derived from the existing `MUSE_BRIDGE_OAUTH_APPROVAL_KEY`. The agent token remains valid across Render service restarts as long as that approval key remains unchanged. Changing the key revokes agent tokens and forces pairing again. The short pairing code itself is single-use and never stored after exchange. No Muse Cookie is needed.

## Callback payload

After Muse actually executes and verifies an order, it writes the following to `/home/hatch/muse-bridge/outbox/<task_id>.json`, with private file permissions. The courier transmits automatically. This example is a **format illustration, not proof of completion**.

```json
{
  "task_id": "<16-character ID from inbound order>",
  "result": "Actual execution summary with verifiable references",
  "failed": false,
  "diff": "Optional verified diff",
  "test_results": "Optional exact test command and results"
}
```

The Render endpoint validates the caller's bearer token, task ID, approved/leased state, type and size, then consumes completion exactly once. Retries redeliver the same task ID, and Muse must deduplicate execution by task ID. The courier never does shell execution on server-controlled strings.

## Why Render Free does **not** meet the user's permanence requirement yet

The connected Render service uses `--state /tmp/muse_bridge_state` on the Free web tier. Its OAuth client registrations, refresh/access-token hashes, queued orders and replies **disappear on redeploy/restart/sleep**. Pairing tokens survive because their signature is verified with a persistent Render environment secret, but OAuth state and pending orders **do not**. Consequently, the current build must not be sold as "connect once forever".

Durable backing store is REQUIRED for a real one-time setup:

- Preferred minimal implementation: upgrade the existing Render instance to a plan that supports an attached persistent Disk; mount the disk and set `--state` to that mount before completing the first pairing. **This is a paid Render change, not executed without owner approval.**
- Or implement an external durable database (such as a paid Render Postgres instance or another backed-up database) for both OAuth and task/session data. No Free Render Postgres database is permanent (30-day expiration), and Free Render Key Value drops all data on restart, so neither can honestly provide continuous durability.

References: https://render.com/docs/free and https://render.com/docs/key-value.

## Acceptance tests (must be observed, not assumed)

- Render `/health` advertises `muse_pair_begin`, `muse_agent_status`; existing OAuth still active.
- Before pairing: GET `/agent/next` and POST `/agent/complete` return 401, not an instruction.
- Authenticated ChatGPT calls `muse_pair_begin`, Muse VM exchanges one-time code and stores token privately.
- Muse VM starts and `muse_agent_status.connected_recently` becomes true.
- ChatGPT creates and approves a test task. It arrives in the persistent Muse inbox without pasted links.
- Muse's actual event-driven handler processes it exactly once, then courier sends result and ChatGPT reads it.
- Muse VM naturally regenerates: existing `muse-console-keeper` restores courier with no new AI scheduled tasks; token and inbox survive, reconnection is automatic.
- Render restarts: **durable OAuth and queue storage must be provisioned and verified first** for an unbroken logged-in ChatGPT connection.

Nothing in the checked-in code or a green `/health` response is proof that the Muse VM is connected or can autonomously activate its agent.
