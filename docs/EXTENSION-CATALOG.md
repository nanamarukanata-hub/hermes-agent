# Hermes extension survey — MCP servers / Skills / plugins

Survey date: 2026-10-26. Method: (1) inventory of what already ships
with this repository, (2) external candidates from the official MCP
Registry ecosystem and vendor-published servers, grouped by the
priority fields in the overhaul plan. Loading model: Hermes skills and
MCP toolsets are **load-on-demand** (skills are injected per session /
per command; MCP servers are configured per profile) — nothing here
requires loading everything at once, and this catalog does not install
anything by itself. Third-party code is never adopted without a
per-item check of license, maintenance, dependencies and credential
surface; the "status" column states how far each candidate was
verified in this survey.

## 1. Already in this repository (verified by local inventory)

| Asset | Count | Notes |
| --- | --- | --- |
| `skills/` + `optional-skills/` + `omega/skills` + codex packages (`SKILL.md`) | 325 files | Built-in + optional (not active by default) + hub collections |
| `plugin-catalog/*.yaml` | 552 entries | Pinned plugin catalog, searchable via the bundled catalog MCP bridge |
| Codex marketplace packages | 3 (90 bundled + 71 optional skills; 551-entry catalog package) | Enabled for trusted projects in `.codex/config.toml` |
| `plugins/` | 15 | memory providers (honcho, mem0, supermemory, holographic, retaindb, …), context engines, model providers, kanban, image_gen, observability, spotify, … |
| MCP surfaces | `mcp_serve.py` (Hermes as MCP server), `hermes_hybrid/mcp_bridge.py`, catalog `.mcp.json` bridges | Hermes both serves and consumes MCP |

Conclusion: the "large-scale extension base" already exists locally
and is the first adoption source — most capability fields in the plan
(RP/conversation skills, memory providers, model routing, coding,
browser, search) have in-repo candidates that need no new third-party
trust decision.

## 2. External MCP candidates (existence confirmed via official/vendor sources)

Legend — Status: A = official vendor server (existence confirmed in
vendor/registry material surveyed 2026-10-26); B = reference/community
server listed by the MCP registry ecosystem (confirm license + activity
before adopting). All require the operator's own credentials for the
target service; none were installed in this pass.

| Field | Candidate | Source | Status | Hermes fit |
| --- | --- | --- | --- | --- |
| Coding / GitHub | GitHub MCP Server (official, remote + local) | github/github-mcp-server | A | Direct fit for §4 GitHub integration; use a Hermes-owned token |
| Browser | Playwright MCP | microsoft/playwright-mcp | A | Browser automation toolset; local stdio |
| Web fetch/search | Fetch reference server | modelcontextprotocol/servers | B | Lightweight fetch; reference quality |
| Filesystem | Filesystem reference server | modelcontextprotocol/servers | B | Sandbox-scoped file access |
| Memory | Memory reference server (knowledge graph) | modelcontextprotocol/servers | B | Overlaps in-repo memory providers — compare before adopting |
| Database | Supabase MCP | supabase | A | Postgres/Auth/Storage tools |
| Database | Neon MCP | neon | A | Serverless Postgres |
| Knowledge | Notion MCP (hosted) | notion | A | Notes/docs; overlaps Obsidian role — optional |
| Productivity | Atlassian MCP (Jira/Confluence) | atlassian | A | Issue/docs integration |
| Productivity | Linear MCP | linear | A | Issue tracking |
| Productivity | Slack MCP | slack | A | Only if Slack is used; Discord is primary here |
| Payments | Stripe MCP | stripe | A | Not RP-relevant; skip unless needed |
| Design | Figma MCP | figma | A | Design assets; optional |
| Monitoring | Sentry MCP | sentry | A | Error tracking for Hermes itself |
| Automation | Zapier MCP / Composio gateway | zapier / composio | A | Broad app coverage; third-party gateway holds credentials — security review required |
| Colab/GPU | (none official) | — | — | No official Colab MCP exists; Hermes uses its own Colab Manager instead |
| Voice (STT/TTS) | Hermes built-in `stt`/`tts` toolsets + providers | in-repo | A | Prefer in-repo before external |
| Image | Hermes `plugins/image_gen` providers | in-repo | A | Already integrated |
| RAG / knowledge graph | In-repo: DeepMemory, vault_search, memory providers (honcho/mem0/supermemory/holographic) | in-repo | A | Already integrated; external Memory server only as comparison |

## 3. Adoption policy applied

1. In-repo first (skills, plugin catalog, memory providers).
2. Official vendor MCP servers second, per-service credentials held by
   Hermes' own config (`~/.hermes/.env`).
3. Community servers only after license/activity/dependency review.
4. Load-on-demand always: enable per profile/toolset, never globally.

## 4. Honest scope note

The plan asked for a survey at up-to-1,000 scale. The locally verified
base is 325 skill files + 552 catalog entries (≈877 catalogued items,
in-repo). The external table above adds the highest-value official
candidates per field (≈20). A line-by-line audit of a literal 1,000
external items was not performed and is not claimed; the catalog is
structured so items can be appended with the same columns as they are
evaluated.
