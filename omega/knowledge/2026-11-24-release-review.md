# OMEGA release review — 2026-11-24 UTC

Public upstream metadata only; no installation or runtime validation.

- [Codex 0.172.0](https://github.com/openai/codex/releases/tag/rust-v0.172.0), 2026-11-24 00:03 UTC: saved sandbox and workspace permissions on resume, cross-OS writable roots, lazy optional stdio MCP startup, per-tool approval reviewers, gRPC Code Mode and lockfile hash verification. Test Windows/Linux remote path mapping, resume permission boundaries and first-call MCP failures. The bundled documentation skill covers GPT-6.2 migration; review before adoption.
- [Claude Code 2.1.331](https://github.com/anthropics/claude-code/releases/tag/v2.1.331), 2026-11-23 21:34 UTC: fixes malformed MCP reply hangs, failed plugin updates leaving partial folders, failed rewind deleting files, and background process cleanup. Test synthetic malformed responses and interrupted updates; do not install optional plugin dependencies automatically.
- [GBrain 0.60.765.0](https://github.com/garrytan/gbrain/releases/tag/v0.60.765.0), 2026-11-23 02:47 UTC: backup checks now degrade on unreadable data, failed fetches no longer count as fresh, relayed agent messages are excluded from user speech capture, and missing PGLite stores generally fail without creating an empty store. Upstream explicitly notes an exception involving a stale corrupt lock and old owner metadata. Keep production integration gated; test missing mounts and backup/restore using synthetic data.
- Hermes 0.23.3 and Obsidian 1.15.1 unchanged in GitHub latest metadata; distribution channel caveat remains.
- [OpenAI model guidance](https://help.openai.com/en/articles/20001354-gpt-6-and-other-models-in-chatgpt) distinguishes Chat GPT-6.2 from Work/Codex Astra/Sol selectors; no entitlement or runtime change inferred. MCP and Agent Skills specification delta not independently established.

DTM: prioritize reliable project-file edits, JUCE/VST3 builds, KSP scripts and preservation of unreleased session notes; no demonstrated DSP or sound-quality improvement.

Bridge audit: PR #8 contains the missing bridge document; it describes a local SQLite FTS5 cache, not GBrain deployment. Do not merge that design into a SQLite-free architecture without redesign. PR #10 is documentation-only for this run. No external code copied; license delta not independently audited. No private vault access. Rollback: revert today's documentation commits. Runtime and CI verification pending.
