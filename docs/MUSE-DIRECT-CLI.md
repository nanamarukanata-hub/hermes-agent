# Direct ChatGPT Bridge -> personal Muse agent

**State:** code staged on the development branch; not live or authenticated.
This targets the **personal Muse at muse.ai and its Secure VM**, not Meta's
separate **Muse Code** coding CLI. No GitHub Issues, scheduled AI tasks, VM
`wake`, or changes to existing Muse Supervisor / Runtime Hook.

## Route

iPhone ChatGPT -> private plugin -> OAuth-protected Render `/mcp`
-> `muse_dispatch_approved` -> `nikships/muse-cli` -> muse.ai
-> personal Muse Secure VM. The Bridge's OAuth authorization and the
personal Muse login **are distinct credentials**.

## Render deployment changes (dedicated service only)

Service: `muse-chatgpt-bridge`; repo `nanamarukanata-hub/hermes-agent`;
branch `feat/muse-chatgpt-bridge-20261025`.
**Do not modify the APEX-6 service.** The service has auto-deploy disabled.

1. Change build command to:
   ```sh
   python -m pip install -r muse_bridge/requirements-muse.txt && python -m compileall -q muse_bridge
   ```
2. Preserve the existing start command, public URL and Bridge OAuth approval
   key. Do not disable OAuth.
3. To enable *real* Muse authentication, set a new **secret environment
   variable** `MUSE_BRIDGE_MUSE_COOKIES` directly in Render's secret setting.
   It must be the personal muse.ai cookie line containing `hatch_sess`.
   **Never** paste it into ChatGPT, GitHub, an Issue, PR, log, or message.
   Muse does not currently provide a documented official OAuth route for
   this CLI; it uses an internal protocol and cookies that may expire.
4. The startup code creates `~/.config/muse-cli/cookies.txt` mode 0600
   from the environment secret and removes the variable from its Python
   process environment. This file is ephemeral on Render, so Render must
   retain the secret env setting across restarts.
5. Deploy the branch explicitly and check `/health`. No Muse operation is
   verified by `/health` alone. Then call **`muse_remote_status`**. Only
   an authenticated, successful status response verifies contact with Muse.
   If it returns `MUSE_CLI_NOT_INSTALLED` or
   `MUSE_AUTH_NOT_CONFIGURED`, stop and fix installation/authentication.
   If it returns `MUSE_CLI_COMMAND_FAILED`, inspect the Muse login
   **privately**, without publishing the CLI's raw stderr.

## MCP workflow

1. `muse_remote_status` (read-only).
2. `muse_send_instruction` with `repo: hermes-agent` and a development
   instruction. For change requests this returns `needs_approval`.
3. `muse_approve` with `decision: approve`.
4. `muse_dispatch_approved` with the returned `task_id` dispatches exactly
   that approved request **once** using `muse-cli send --wait 15`.
5. Read `muse_get_status` and `muse_get_result`. Dispatch sets
   `awaiting_muse`; a Muse chat reply is **not proof of code change**.
   Actual code changes must be verified in the relevant checkout/commit.

For missing binary or cookies, the task remains `approved` for a later
attempt. A transport timeout can mean Muse received the instruction;
it becomes `delivery_unknown` to avoid duplicate work. Never auto-retry
delivery-unknown tasks without checking Muse's history independently.

## Operational limitations

- Render Free uses an ephemeral filesystem; OAuth clients/tokens and task
  records stored under `/tmp/muse_bridge_state` vanish on sleep, restart,
  or redeploy. Fix persistence before relying on task history or sessions.
- `muse-cli` is a third-party tool using an **unversioned reverse-engineered
  Muse gateway**. Calls can fail after Muse updates, and its cookie grants
  access to the personal Muse account. Prefer restricting access to the
  dedicated private Render service; rotate credentials if compromised.
- The Bridge does **not** execute on the Muse VM itself. It sends instructions
  to the Muse personal agent; the agent's own permissions and approvals still
  apply. No guarantees of task completion or uninterrupted VM uptime.
- Existing operational approval policy is unchanged; no RP-specific lore
  filters are added.
- There is no confirmation of **iPhone -> OAuth -> MCP -> Muse** until a
  real `muse_remote_status` and a harmless end-to-end send succeed.

## Offline tests

```sh
python -m unittest -v tests.test_muse_cli_adapter tests.test_muse_cli_dispatch
```

These mock CLI interaction and never connect to a Muse account. Do not treat
mock success as a live dispatch.
