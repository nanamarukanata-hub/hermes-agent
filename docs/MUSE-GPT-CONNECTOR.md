# Personal Muse -> GPT connector (paired VM, no Muse cookie)

## Status

**Implemented in GitHub, not yet proven end-to-end.** A Render redeploy and an
OpenAI API key are still required. This connects Muse to **GPT models through
the OpenAI Responses API**, not this individual ChatGPT conversation, and
does not use a ChatGPT Plus subscription as API credit.

This connector is independent of the legacy muse-cli personal-session cookie.
It reuses the existing one-time paired Muse Secure VM -> Render resident channel,
including the HMAC-signed agent token stored privately on the Muse VM.

    Personal Muse app
       -> authorised Muse Secure VM local command/custom-connector
       -> HTTPS POST https://muse-chatgpt-bridge.onrender.com/agent/gpt
          Authorization: Bearer <private paired agent token>
       -> Render holds OPENAI_API_KEY only in Environment
       -> https://api.openai.com/v1/responses (store=false)
       -> GPT text response returned to Muse

## Set up (once, after code is deployed)

1. In **Render > muse-chatgpt-bridge > Environment**, add
   `OPENAI_API_KEY` as a Render secret. Never commit it, send it in chat,
   put it in the Muse VM, or put it in any logs. **Do not substitute the
   Muse agent token for an OpenAI API key.**
2. Optionally set `MUSE_GPT_MODEL` to a model the API project can actually use.
   Default: `gpt-4.1-mini`. The caller cannot override this model.
   GPT API charges are separate from ChatGPT Plus; control costs in the
   OpenAI API account.
3. Deploy the tested branch to the **existing** Render service only when
   ready for the current Free-tier service restart. Current Render
   `--state /tmp/muse_bridge_state` means **OAuth registrations/tokens,
   queued work orders and results are lost on redeploy**. Provision durable
   state first if continuity matters. Do not claim one-pair-forever uptime
   while this state remains ephemeral.
4. Pair the Muse VM once using the existing `muse_pair_begin` OAuth MCP
   tool, following `docs/MUSE-RESIDENT-PERMANENT-CONNECTION.md`. The VM
   saves the resulting agent token as mode 0600:
   `/home/hatch/muse-bridge/agent-token`.
5. In the **same authenticated Muse Secure VM**:
   ```sh
   python3 /home/hatch/hermes-agent/muse_bridge/agent_courier.py \
     gpt --prompt "Please reply with OK."
   ```
   or pass a local private `--prompt-file` (or standard input).
   The CLI reads the stored paired resident token; no Muse cookie, and no
   ChatGPT session cookie or bearer is printed.
6. Integrate that *existing local command* into the personal Muse custom
   connector or approved native Muse tool interface. **Muse's native tool
   registration and activation must be executed and verified in the
   personal Muse Secure VM; GitHub changes alone cannot install the
   connector in the Muse app.** Do not invent an undocumented activation
   API. This CLI is for the personal Muse agent, not Muse Code.

## Server endpoint

`POST /agent/gpt`

Authenticated with **the paired resident agent token**, not the OpenAI
API key and not a ChatGPT OAuth token. Transport HTTPS only on Render.

Request JSON:

```json
{"prompt":"Review this design and point out the three most important problems."}
```

Success: HTTP 200, a JSON object containing `ok:true`, `answer`,
`model` and token `usage` when available.

Errors: HTTP 401 without a valid paired token, HTTP 400 for invalid or
oversized prompt, HTTP 503 when `OPENAI_API_KEY` is absent, HTTP 429 when
OpenAI rate-limits, HTTP 502 on sanitized upstream errors. Inputs limited
to 6000 characters, output limited to 1024 model tokens. No arbitrary
commands or model names can be sent by the caller. Never reveal upstream
error bodies or secret API keys.

Other existing bridge endpoints and OAuth tools stay unchanged.
`muse_gpt_status` lets an authorised ChatGPT plugin client check whether
the Render environment has an OpenAI API key (not whether billing works).

## Validation

Unit/HTTP tests (no billable calls):
`python -m unittest tests.test_muse_gpt_connector -v`

- No token and invalid token -> 401 before any model call.
- Properly paired VM token -> mock GPT adapter call.
- No Render OpenAI key -> 503.
- OpenAI 401 -> sanitized error without leaked key.
- Fixed model, bounded output and `store:false`.

To claim completed integration, run an **authenticated live test** from
Muse VM after deployment and verify the answer is returned in the
personal Muse chat. Until then, report the connector as **code ready,
not yet connected**.

Important: Pair tokens survive Render restarts only while the existing
`MUSE_BRIDGE_OAUTH_APPROVAL_KEY` remains unchanged. A natural Muse VM
restart still needs the existing Supervisor/Runtime Hook to restart
the courier/custom connector. No 5-minute AI keepalive tasks are needed.
