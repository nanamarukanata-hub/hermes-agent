# OMEGA official release review — 2026-11-10 JST

Status: **verified upstream public documentation; documentation-only proposal; no runtime deployment**.

## Changes since 2026-11-09 radar

### P0 — Claude Code v2.1.321 (2026-11-09 UTC)
[Official release](https://github.com/anthropics/claude-code/releases/tag/v2.1.321) fixes MCP OAuth refresh tokens and other secrets potentially being sent to a different host after redirect. It also fixes read-only Bash operations escaping sandbox prompting, wrong-directory write permission checks, and `cp`/`mv` protected-file and deny-rule enforcement. These are **security-relevant** to unattended OMEGA agents. Validate redirect-host handling and permission enforcement in an isolated test project; do not transfer real credentials into a test. The release also adds Claude Fable 5.2 as default Fable (1M context; published API prices $8 input/$40 output per million tokens, cache reads $0.20/Mtok), `claude remote-control --settings <file>`, Windows FileChanged hook fix, network-path Edit/NotebookEdit fixes, and worktree skill reappearance fixes. Do not infer entitlement or compatibility from a CLI release alone.

### P0 — GBrain v0.60.626.0 (2026-11-09 UTC)
[Official release](https://github.com/garrytan/gbrain/releases/tag/v0.60.626.0) makes `sources record-origin --remote --apply` lock down **existing** managed Git checkouts, not just future reclones. It coordinates topology/worktree locks and enforces `lfs.fetchexclude=*`, `core.hooksPath=/dev/null`, and `lfs.locksverify=false` with readback. This addresses a window in which an already-existing checkout could still fetch Git LFS content or execute hooks after source-origin registration claimed success. Never auto-run `--apply` on private sources; first inventory provenance, back up, and test with synthetic repos. Review intervening v0.60.620–625 release notes before rollout. [Releases](https://github.com/garrytan/gbrain/releases).

### No version delta
Codex remains `rust-v0.167.0` (newer alpha builds excluded), Hermes Agent remains `v0.23.0`, Obsidian GitHub remains `v1.15.0`. Obsidian's public/Catalyst channel must be checked separately.

### ChatGPT / Work / MCP / Agent Skills
[ChatGPT official release notes](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) document ChatGPT Images 2.6, GIF creation rollout, and saved reference images on **2026-11-04**; these were not captured by the five-repository release radar. Potential creative workflow benefit: consistent character references and short visual assets for music promotion, **not** a replacement for SDXL LoRA training or a guarantee of model/API access. Work/Codex plan allowance announcement is 2026-10-29; no global upgrade performed.
[MCP specification](https://modelcontextprotocol.io/specification/2026-07-28) remains 2026-07-28 and documents opt-in extensions including Tasks and Skills over MCP; verify explicit client/server negotiation. [Agent Skills specification](https://agentskills.io/specification) requires a YAML-frontmatter `SKILL.md` with name and description; current `omega-ai-updates` structure is consistent with these basics. No unreviewed third-party Skill execution.

## OMEGA impact / compatibility / licensing

- **Security first:** Claude OAuth redirect-host regression and GBrain existing-checkout hook/LFS lockdown have direct consequences for private Obsidian/MCP integration. Preserve token isolation, source ACLs, and immutable test fixtures. Review before upgrading.
- **Compatibility:** Hermes v0.23.0 gateway/session ownership remains untested against the existing Discord RP and Obsidian persistence stack. Windows drive-letter hooks and network paths deserve regression tests. No source or Skills changed today.
- **DTM, Kontakt/KSP, JUCE/VST3 and mixing:** secure provenance and reliable saved revisions are higher value than cosmetic model changes. Images 2.6 may help promotional artwork, not DSP or audio quality. Validate any model-cost assumption with account-specific entitlements.
- **Licensing:** release metadata and links are documentation, not permission to redistribute third-party code. Before integrating upstream code, inspect the relevant version's LICENSE and any hosted-model commercial terms. No upstream code copied.
- **Rollback:** revert these Markdown-only commits if the metadata proves incorrect. Production software, GBrain source topology, and private Vault remain untouched.

## Execution and limitations

Read `omega/AGENTS.md`, `omega/skills/omega-ai-updates/SKILL.md`, `omega/knowledge/latest-releases.md`, and `.github/workflows/omega-release-radar.yml` on PR #10 branch. `omega/docs/gbrain-obsidian-bridge.md` returned 404 on this branch; PR #8 design remains separate. Only public official release sources used; private Google Drive Vault was not accessed. No code/Skills/workflow changes, runtime tests, application upgrades or main merge. CI status requires independent verification. PR #10 remains draft.
