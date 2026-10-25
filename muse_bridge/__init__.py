"""Muse <-> ChatGPT bridge (local MCP server + task queue).

This package does NOT copy code from danny-hines/muse-code-bridge
(MIT) or bex-co/muse-code-acp (Apache-2.0).  Those projects were
investigated (see docs/MUSE-CHATGPT-BRIDGE.md) and are not usable
as-is for the stated goal: the bridge targets Codex / ChatGPT
desktop on macOS (Hermes support untested), and the ACP adapter
targets Zed / VS Code editors, not ChatGPT.  Neither provides an
iPhone ChatGPT connection, and neither runs on this Linux VM without
a Muse Code executable, which is not installed here.

Instead this package is an original, dependency-free (stdlib only)
MCP-compatible server that:

- exposes Muse task tools over HTTP JSON-RPC (tools/list, tools/call),
- stores instructions, sessions, logs, diffs and test results as
  JSON files (no second database, no GitHub Issue round-trip),
- never executes arbitrary development work by itself: a queued
  instruction is either a bounded read-only/test action (auto-allowed
  by policy) or an ``awaiting_muse`` work order that Muse executes
  and completes through the CLI, so the result becomes readable
  from the same MCP tools.

Security posture (standing boundary preserved):
- loopback by default; no public URL is issued by this package,
- Bearer token auth when configured; the token lives outside the
  repo (env ``MUSE_BRIDGE_TOKEN`` or a 0600 token file), never in
  code, logs, or commits,
- OAuth 2.1 (``muse_bridge/oauth.py``) when a public URL and an
  approval key are configured; tokens are stored only as hashes,
- commits, pushes, deploys and deletions are never auto-executed;
  they require an explicit ``muse_approve`` call and Muse review,
- only the repos listed in ``repos.yaml`` (default: hermes-agent,
  apex-6-private) are addressable, and only through local checkouts
  configured there.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
