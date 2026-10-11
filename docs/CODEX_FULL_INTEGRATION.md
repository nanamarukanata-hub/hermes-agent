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

## Using enabled Hermes plugins from Codex

The bridge also exposes `catalog_delegate_task(prompt, approved=true)`. When a
Hermes binary is present and configured, this runs `hermes chat -q PROMPT`
inside that user's Hermes environment. Hermes may then invoke its enabled
plugins in its own agent session, so Codex can receive the answer through MCP.
The actual invocation of any particular plugin must be verified in Hermes logs.
Authorization, configured model credentials, provider login and compatible
plugin runtime are prerequisites. Without them, the tool returns an explicit
error; it does not pretend the 551 entries are native Codex tools.

This tool is not called automatically at MCP startup. An explicit Codex tool
call with `approved=true` is required to cross into Hermes execution.

## ClaudeSkills.info directory

The `claude-skills-hub` Codex marketplace plugin adds an MCP endpoint with
directory search, inspection, index refresh, and user-approved Codex installs.
The public web directory was showing 42,824 indexed Skills on 2026-10-11.

`scripts/index_claudeskills_info.py` scans all directory result pages (rather
than using the smaller `/api/v1/meta` count, which represents a different
catalog and may be stale). The GitHub Actions workflow indexes all publicly
listed skill detail URLs into `codex-plugins/claude-skills-hub/index/catalog.json`.

Catalog entries are not actual `SKILL.md` content. The MCP allows use of
`npx skills add owner/repo --skill skillname --agent codex --yes --copy` only
after authorization and source review. When dependencies or licensing prohibit
reuse, the Skill must not be silently marked installed.

The personal Codex installation and credentials do not exist in this GitHub
repository. Codex must load the repo's Marketplace and its MCP server.
