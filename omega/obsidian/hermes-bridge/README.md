# OMEGA Hermes Bridge — Obsidian desktop plugin (prototype v0.1.0)

An Obsidian **desktop-only** right sidebar for chatting with an independently running **Hermes Agent gateway**. Uses the [official Hermes OpenAI-compatible HTTP API](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server). No third-party NPM packages, build step or user notes are bundled.

## Scope

- Chat panel, transient session-only API token, optional attachment of the current Markdown note (truncated at 8,000 characters), manual **Save reply to Inbox** action.
- No automatic note collection, background indexing, agent installation, GBrain daemon, terminal bridge, or Obsidian-to-iPhone synchronization.
- No guarantee Hermes itself is read-only: Hermes has independent tool/file permissions based on gateway configuration. Start with a restricted sandbox or read-only filesystem access when evaluating.
- Credentials stay in the panel DOM only until it closes; the API key is **never** persisted in `data.json`. The gateway endpoint is saved.
- Remote connection is deliberately blocked; first validate local Windows deployment. An authenticated HTTPS relay for iOS is a separate project requiring security review.

## Configure Hermes (on Windows)

1. Install Hermes from the [official installer instructions](https://hermes-agent.nousresearch.com/docs). Configure a model provider with `hermes setup` / `hermes model`, and check it with `hermes doctor`.
2. Set environment variables in your **local** `~/.hermes/.env` (replace placeholder with a secure random key):
   ```dotenv
   API_SERVER_ENABLED=true
   API_SERVER_KEY=<YOUR_RANDOM_SECRET_NOT_FOR_GIT>
   ```
3. Start `hermes gateway`. Verify it listens on `http://127.0.0.1:8642` only. Test with curl or PowerShell from the same Windows account using the documented `/v1/chat/completions` request.
4. Back up your Vault. To install this plugin, copy **`main.js`, `manifest.json`, `styles.css`** to `<your-vault>/.obsidian/plugins/omega-hermes-bridge/` and enable **Omega Hermes Bridge** in Obsidian's Community Plugins settings.
5. Open the right sidebar using the robot ribbon icon or command palette. Paste your local `API_SERVER_KEY` into the ephemeral password field and ask a harmless test question.
6. Only after that works, open a Markdown note, opt in to attaching it, ask a question, inspect the response, and click **Save reply to Inbox** if desired. This writes a new review-tagged Markdown file to `01_Inbox/Hermes/`.

No persistent key is stored. The installed plugin is new and unverified on the user's devices; its offline static tests are not a replacement for an end-to-end Hermes/Obsidian smoke test.

## Run static tests

```bash
node tests/test_plugin.js
```

## Obsidian iOS

Hermes cannot be spawned as a process from iOS Obsidian. The current plugin deliberately sets `isDesktopOnly: true`; do not copy it to iPhone. Future iOS support requires authenticated remote HTTPS, mobile plugin testing, key storage/security design, and uptime on the host.

## Source references

- https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server
- https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration
- https://docs.obsidian.md/Plugins/Getting%20started/Build%20a%20plugin
- https://docs.obsidian.md/Plugins/Getting%20started/Mobile%20development
