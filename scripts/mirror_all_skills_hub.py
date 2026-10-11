#!/usr/bin/env python3
"""Mirror ALL entries returned by the Hermes Skills Hub index to inert ZIP archives.

Each indexed entry is attempted; failures are explicitly recorded in JSONL.
No downloaded Skill files are executed or installed. Each job handles one
deterministic shard, allowing parallel GitHub Actions execution.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

# Keep hermes state isolated from real accounts.
os.environ.setdefault("HERMES_HOME", str(Path.cwd() / ".hermes-mirror-cache"))

# Ensure this repository's packages win over optional third-party 'tools' modules.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.skills_hub import (
    GitHubAuth, GitHubSource, SkillsShSource, OptionalSkillSource,
    WellKnownSkillSource, ClawHubSource, ClaudeMarketplaceSource,
    LobeHubSource, HermesIndexSource,
)

MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_BUNDLE = 200
MAX_ERROR_TEXT = 300

def clean_path(raw: str) -> str:
    if not isinstance(raw, str) or "\x00" in raw or "\\" in raw or raw.startswith("/"):
        raise ValueError("unsafe relative filename")
    p = PurePosixPath(raw)
    if any(part in ("..", "") for part in p.parts) or not p.parts:
        raise ValueError("unsafe relative filename")
    return str(p)

def shard_number(identifier: str, count: int) -> int:
    return int(hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12], 16) % count

def choose_candidates(entry: dict, src: dict, index_source):
    key = str(entry.get("source", "")).lower()
    source = {"skills.sh": "skills-sh"}.get(key, key)
    candidates = []
    # Resolve explicit repo/path even if source registry index is missing a path.
    if entry.get("resolved_github_id") or (entry.get("repo") and entry.get("path")):
        candidates.append((index_source, str(entry.get("identifier", ""))))
    if source in src:
        candidates.append((src[source], str(entry.get("identifier", ""))))
    if source == "github" and entry.get("repo") and entry.get("path"):
        candidates.append((src["github"], str(entry["repo"]).rstrip("/") + "/" + str(entry["path"]).lstrip("/")))
    # Preserve order but prevent duplicate network requests.
    seen = set()
    for obj, ident in candidates:
        token = (obj.source_id(), ident)
        if ident and token not in seen:
            seen.add(token)
            yield obj, ident

def archive_bundle(zipout, record_prefix: str, bundle):
    if not bundle.files or "SKILL.md" not in bundle.files:
        raise ValueError("missing SKILL.md")
    if len(bundle.files) > MAX_FILES_PER_BUNDLE:
        raise ValueError("too many bundled files")
    prepared = []
    total = 0
    for name, value in bundle.files.items():
        rel = clean_path(name)
        if isinstance(value, str):
            raw = value.encode("utf-8")
        elif isinstance(value, bytes):
            raw = value
        else:
            raise ValueError("unknown file content type")
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("file exceeds size limit")
        total += len(raw)
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("skill bundle exceeds size limit")
        prepared.append((rel, raw))
    hashes = {}
    for rel, raw in prepared:
        name = record_prefix + "/" + rel
        zipout.writestr(name, raw)
        hashes[rel] = hashlib.sha256(raw).hexdigest()
    return {"files": len(prepared), "bytes": total, "sha256_by_file": hashes}

def mirror_entries(entries: list, *, shard: int, shards: int, out: Path,
                   sources: dict, index_source, delay: float = 0,
                   limit: int = 0):
    out.mkdir(parents=True, exist_ok=True)
    results_file = out / f"results-{shard:03d}.jsonl"
    zip_file = out / f"skill-bundles-{shard:03d}.zip"
    all_ids = sorted(
        ((str(e.get("identifier", "")), e) for e in entries
         if isinstance(e, dict) and isinstance(e.get("identifier"), str) and e["identifier"]),
        key=lambda pair: pair[0],
    )
    selected = [(ident, item) for ident, item in all_ids if shard_number(ident, shards) == shard]
    if limit:
        selected = selected[:limit]
    counts = Counter()
    with zipfile.ZipFile(zip_file, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                         allowZip64=True) as zout, results_file.open("w", encoding="utf-8") as f:
        for number, (ident, entry) in enumerate(selected, start=1):
            prefix = "quarantine/" + hashlib.sha256(ident.encode()).hexdigest()[:24]
            record = {"identifier": ident, "source": entry.get("source"),
                      "prefix": prefix, "index": number, "status": "unavailable"}
            reasons = []
            for source, source_id in choose_candidates(entry, sources, index_source):
                try:
                    bundle = source.fetch(source_id)
                    if not bundle:
                        reasons.append(f"{source.source_id()}:not-found")
                        continue
                    record.update(archive_bundle(zout, prefix, bundle))
                    record["status"] = "downloaded"
                    record["fetched_from"] = source.source_id()
                    break
                except Exception as exc:
                    reasons.append(f"{source.source_id()}:{type(exc).__name__}:{str(exc)[:80]}")
            if record["status"] != "downloaded":
                record["errors"] = reasons[:5] or ["no supported source adapter"]
            counts[record["status"]] += 1
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            if number % 100 == 0:
                print(f"Shard {shard}/{shards}: {number}/{len(selected)} {dict(counts)}", flush=True)
            if delay:
                time.sleep(delay)
    summary = {"shard": shard, "shards": shards,
               "indexed": len(entries), "attempted": len(selected),
               "downloaded": counts["downloaded"], "unavailable": counts["unavailable"],
               "archived_bytes": zip_file.stat().st_size,
               "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
               "note": "Downloaded bundles are inert ZIP contents, NOT activated Skills."}
    (out / f"summary-{shard:03d}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", required=True, help="Exported skills-catalog.json")
    p.add_argument("--out", default="skills-hub-mirror")
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, required=True)
    p.add_argument("--delay", type=float, default=0.08)
    p.add_argument("--limit", type=int, default=0, help="0 means all entries in shard")
    args = p.parse_args()
    if args.shards < 1 or args.shard < 0 or args.shard >= args.shards or args.delay < 0 or args.limit < 0:
        p.error("invalid shard, count, delay or limit")
    payload = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    entries = payload.get("skills")
    if not isinstance(entries, list) or not entries:
        p.error("Catalog contains no skills")
    auth = GitHubAuth()
    sources = {
        "official": OptionalSkillSource(),
        "github": GitHubSource(auth),
        "skills-sh": SkillsShSource(auth),
        "well-known": WellKnownSkillSource(),
        "clawhub": ClawHubSource(),
        "claude-marketplace": ClaudeMarketplaceSource(auth),
        "lobehub": LobeHubSource(),
    }
    mirror_entries(entries, shard=args.shard, shards=args.shards, out=Path(args.out),
                   sources=sources, index_source=HermesIndexSource(auth), delay=args.delay, limit=args.limit)

if __name__ == "__main__":
    main()
