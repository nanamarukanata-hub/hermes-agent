---
name: hermes-codex-plugin-manager
description: Automatically find an appropriate entry in the entire Hermes Plugin Catalog, inspect its capabilities and install/enable it in the user's Hermes runtime if requested. Use for external integrations, MCP plugins and Hermes workflows.
---
# Hermes Plugin Catalog for Codex

This Codex project enables a local MCP server named `hermes_plugin_catalog` that knows
all 551 pinned Plugin Catalog entries (bundled under `catalog/`).

## Routing
- When the user asks for a plugin or a task that may require a Hermes integration:
  first call `catalog_search`, then `catalog_inspect` and check runtime status.
- When explicitly authorized to install a plugin, call `catalog_install_enable`
  with `approved: true`. For several selected names, use `catalog_install_batch`
  (max 10) and account for every failure.
- If the user has previously explicitly authorized installing all entries, it
  is still necessary to check name-by-name compatibility and credentials.
- The MCP bridge runs NO external third-party plugins on startup.
- Hermes plugin tools do not automatically become native Codex tools: their
  capabilities live inside the Hermes runtime unless they expose separate
  MCP endpoints that have been registered with Codex. Do not claim otherwise.
- Never invent API credentials or bypass paid services, interactive OAuth,
  host operating-system restrictions or the catalog's removed-plugin list.
- External scripts and installation may affect files, network accounts and
  security. Explain the actual action before performing irreversible operations.

## Fallback CLI
From the project root (when Codex MCP is not available):
`hermes plugins browse`, `hermes plugins search KEYWORD`,
`hermes plugins info NAME`, `hermes plugins install NAME --enable`.

If the Hermes binary is not installed on the user host, report that blocker.

## Delegation into Hermes
When Codex needs to **actually use** a Hermes-specific tool, call
`catalog_delegate_task` (with `approved: true` after user authorization).
This invokes `hermes chat -q` in the configured local Hermes runtime, where
installed plugins may be invoked. Report actual output and failures; don't
claim a specific plugin executed unless the Hermes agent's logs confirm it.
