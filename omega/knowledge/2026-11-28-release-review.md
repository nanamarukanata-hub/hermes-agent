# OMEGA release review — 2026-11-28 JST

## Verified upstream metadata
GBrain advanced from v0.60.772.0 to v0.60.778.0 (2026-11-27 05:27 UTC). The other four monitored latest non-prerelease tags are unchanged. See [v0.60.777.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.777.0) and [v0.60.778.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.778.0).

## Adoption assessment
- Security: v777 scopes remote background jobs to submitting credential and audience, introduces OAuth refresh-token families (migration v338), and changes new Codex setup to the full tool surface. Keep existing least-privilege configuration; test cross-member job isolation and token revocation before adoption.
- v778 rechecks ranked recall under full read policy, ranks query-only recall by relevance, and supports disabling provider-based atom extraction per source. This is not a blanket guarantee against external data transfer.
- v778 migration v339 allows unknown event dates; old records are not rewritten, and the experimental Rust engine still assigns save time. Re-extraction expires old facts rather than deleting them. Test retention and date semantics with synthetic data.
- Default model tiers and long-page retrieval behavior change; pin model choices and measure cost, context budget and recall quality. Existing explicit model choices are preserved according to upstream notes.
- Prereleases [v780](https://github.com/garrytan/gbrain/releases/tag/v0.60.780.0) and [v781](https://github.com/garrytan/gbrain/releases/tag/v0.60.781.0) address Windows upgrade recovery, backlink visibility, transcript provenance and hidden skill files. Do not treat these fixes as part of v778.
- Audio development: section-aware retrieval and CJK phrase search can help locate DSP/KSP/JUCE documentation. No measured audio quality or DSP performance improvement.

## Verification and rollback
Read AGENTS, update skill, release radar workflow and bridge design on PR #8. Bridge still uses SQLite FTS5 and is not a real GBrain integration. PR #10 prior HEAD had no returned PR workflow runs or combined statuses; CI is not verified. No runtime, code, skill, private vault or credential changes. No external code copied; tag-specific license and dependency audit remains pending. Documentation-only rollback: revert these documentation commits. Before runtime adoption, test on a disposable synthetic store and verify backup restoration; do not assume schema downgrade is safe.

Official MCP and Agent Skills references: https://modelcontextprotocol.io/extensions/skills/overview and https://agentskills.io/skill-creation/evaluating-skills . No new mandatory migration established.
