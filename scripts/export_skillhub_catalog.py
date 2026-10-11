#!/usr/bin/env python3
"""Export upstream Hermes Skills Hub index metadata, never executable skill files."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

INDEX_URL = "https://hermes-agent.nousresearch.com/docs/api/skills-index.json"
MAX_BYTES = 128 * 1024 * 1024

def fetch_json(url: str, max_bytes: int = MAX_BYTES) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Hermes-SkillHub-Catalog-Exporter/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        size = resp.headers.get("Content-Length")
        if size and int(size) > max_bytes:
            raise ValueError("Catalog exceeds maximum size")
        chunks, total = [], 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Catalog exceeds maximum size")
            chunks.append(chunk)
    data = json.loads(b"".join(chunks))
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise ValueError("Unexpected index format (missing skills list)")
    return data

def export_catalog(payload: dict, out_dir: Path, source_url: str) -> dict:
    skills = payload["skills"]
    if any(not isinstance(entry, dict) for entry in skills):
        raise ValueError("Invalid catalog entry")
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (out_dir / "skills-catalog.json").write_bytes(raw)
    summary = {
        "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_url": source_url,
        "index_generated_at": payload.get("generated_at"),
        "index_declared_count": payload.get("skill_count"),
        "count_loaded": len(skills),
        "distinct_identifiers": len({s.get("identifier") for s in skills if isinstance(s.get("identifier"), str)}),
        "sources": dict(sorted(Counter(str(s.get("source") or "unknown") for s in skills).items())),
        "catalog_sha256": hashlib.sha256(raw).hexdigest(),
        "catalog_bytes": len(raw),
        "note": "Only entries returned by the upstream index are exported. This cannot certify completeness across external registries. No third-party skills installed."
    }
    (out_dir / "catalog-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=INDEX_URL)
    parser.add_argument("--output", default="skillhub-export")
    parser.add_argument("--input", help="Read a local JSON index instead of downloading")
    args = parser.parse_args(argv)
    try:
        if args.input:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
                raise ValueError("Input missing skills array")
        else:
            payload = fetch_json(args.url)
        info = export_catalog(payload, Path(args.output), args.input or args.url)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        if not info["count_loaded"]:
            print("WARNING: Index contains no skills", file=sys.stderr)
            return 2
        if info["index_declared_count"] is not None and info["index_declared_count"] != info["count_loaded"]:
            print("WARNING: Index count discrepancy", file=sys.stderr)
            return 2
        return 0
    except (OSError, ValueError, TypeError, urllib.error.URLError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
