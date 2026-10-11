# Hermes Skills Hub — Complete catalog and actual skill bundle mirroring

Goal: attempt to download **every skill indexed by upstream Hermes Skills Hub**,
including SKILL.md and bundle resources, without executing external code.

## 1. Existing Hermes features
Hermes already supports built-in and optional Skills, searching public registries,
per-Skill installation and the Skills Guard safety scanner.

## 2. Download the entire upstream metadata index
```bash
python3 scripts/export_skillhub_catalog.py --output skills-index-export
```

## 3. Download actual Skills, not merely titles or links
```bash
python -m pip install httpx==0.28.1 pyyaml==6.0.2
python scripts/mirror_all_skills_hub.py \
  --catalog skills-index-export/skills-catalog.json \
  --out skill-bundle-export --shard 0 --shards 1 --limit 0
```

To divide the entire index into 64 partitions, each partition has a deterministic
SHA-256 identifier mapping. The GitHub Actions workflow
`.github/workflows/mirror-all-skills-hub.yml` automatically attempts all 64
partitions on the specified main-branch push; the workflow can also be dispatched
manually later. It uploads a separate inert ZIP and JSONL status ledger per shard.

### What counts as success
- Actual bundle file bytes archived into ZIP under `quarantine/<identifier hash>/`.
- Each successfully archived bundle has a SHA-256 hash for every file.
- Failed records remain visible with a precise `unavailable` status.
- `downloaded / attempted` is reported per shard. Claim "all downloaded"
  **only when every shard completed and all records show downloaded**.

### Practical limits
- GitHub API rate limits, unavailable upstream repos, missing resolved paths and
  registry outages mean some items can fail.
- Maximum 20 MiB per skill, 200 files, 10 MiB per file; oversized bundles are marked failed.
- The public site's large catalogue count is *not* equivalent to entries present in
  the central index. Some registry entries cannot be resolved.
- Bundles may contain copyrighted, malicious or incompatible files. ZIP content
  is quarantined: **no downloaded code is executed or installed on production**.
- Artifacts expire after 30 days. No repository growth from third-party bundles.
- GitHub Actions has runtime and storage limits; review the workflow usage in Actions.

### Activation of all unknown Skills is not automatic
Trying to activate tens of thousands of unreviewed third-party Skills would expose
the agent and user data to supply-chain and prompt-injection risks. For intended
runtime use, inspect source, run Hermes security audit, and install vetted individual
skills. The complete export is an archival operation, not a verification of safety.
