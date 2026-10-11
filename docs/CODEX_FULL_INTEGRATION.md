# Codex full Hermes integration

`codex-plugins/hermes-all-skills` contains 90 bundled SKILL.md directories.
`codex-plugins/hermes-optional-skills` contains 71 optional ones.
`codex-plugins/hermes-plugin-catalog` ships the complete 551-entry official
Plugin Catalog and a zero-dependency, local MCP bridge (reads YAML metadata only
at startup; plugins are not imported or executed).

The marketplace file `.agents/plugins/marketplace.json` sets all 3 packages to
`INSTALLED_BY_DEFAULT`. `.codex/config.toml` enables all 3 when Codex trusts
this repository.

The plugin MCP tool `catalog_install_enable` requires `approved: true` and a
local `hermes` binary. Installing a Hermes plugin affects Hermes, not native
Codex tools, and does not supply API keys or OAuth grants.

Run from the project directory:

```shell
codex plugin marketplace add nanamarukanata-hub/hermes-agent --ref main
codex plugin marketplace list
# Restart a trusted Codex desktop/CLI session if needed.
```

The local plugin bridge uses `cwd: "."` so the embedded Python server
runs from its installed plugin root instead of relying on environment-variable
expansion of ${PLUGIN_ROOT} in MCP arguments. Python 3 must be on PATH.

`551 catalog entries != 551 working native Codex integrations`.
External Hermes plugins vary widely in layout, host requirements, auth, cost,
and compatibility; non-Python / portable plugin formats require an appropriate
Hermes version. No auto-execution of third-party code occurs on startup.
