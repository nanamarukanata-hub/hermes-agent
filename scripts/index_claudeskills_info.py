#!/usr/bin/env python3
"""Index EVERY public ClaudeSkills directory page into a read-only Codex catalog.

No third-party Skill source is downloaded/executed. Each index record includes
the original source page, GitHub owner/repo and unverified Skill name.
The website is dynamic; report mismatches instead of claiming 100% completion.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import Request, urlopen

BASE = "https://claudeskills.info"
MAX_HTML_BYTES = 5 * 1024 * 1024
PATH_PATTERN = re.compile(r"^/(?:ja/)?skills/([^/]+)/([^/]+)/([^/]+)/?$")
VALID = re.compile(r"^[a-zA-Z0-9_.-]{1,100}$")


def normalize_url(href: str):
    if not isinstance(href, str):
        return None
    url = urljoin(BASE + "/skills/", href)
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "claudeskills.info":
        return None
    match = PATH_PATTERN.fullmatch(parts.path)
    if not match:
        return None
    owner, repo, skill = [unquote(segment) for segment in match.groups()]
    if not all(VALID.fullmatch(t) for t in (owner, repo, skill)):
        return None
    return {
        "id": f"{owner}/{repo}/{skill}",
        "repo": f"{owner}/{repo}",
        "skill": skill,
        "source": f"{BASE}/skills/{owner}/{repo}/{skill}/",
    }


class SkillLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        kv = dict(attrs)
        if "href" in kv:
            record = normalize_url(kv["href"])
            if record:
                self.links.append(record)


def parse_page(html: str):
    p = SkillLinks()
    p.feed(html)
    unique = {x["id"]: x for x in p.links}
    return list(unique.values())


def fetch_url(url: str, timeout=25):
    req = Request(url, headers={"User-Agent": "ClaudeSkillsCodexIndex/1.0 (read-only; catalog index)", "Accept": "text/html"})
    with urlopen(req, timeout=timeout) as response:
        actual = urlsplit(response.url)
        if actual.scheme != "https" or actual.hostname != "claudeskills.info":
            raise ValueError("Unexpected redirect host")
        chunks, total = [], 0
        while True:
            part = response.read(256 * 1024)
            if not part:
                break
            total += len(part)
            if total > MAX_HTML_BYTES:
                raise ValueError("HTML page exceeds limit")
            chunks.append(part)
        return b"".join(chunks).decode("utf-8", errors="replace")


def get_page(page_number: int, *, tries=3):
    url = BASE + ("/skills/" if page_number == 1 else f"/skills/page/{page_number}/")
    err = None
    for attempt in range(tries):
        try:
            html = fetch_url(url)
            records = parse_page(html)
            if records:
                return page_number, records, None
            err = "No valid Skill links found on page (layout changed or blocked)"
        except (HTTPError, URLError, ValueError, OSError) as exc:
            err = f"{type(exc).__name__}: {str(exc)[:200]}"
        time.sleep(0.35 * (attempt + 1))
    return page_number, [], err


def build_index(expected: int, max_pages: int, workers: int):
    if not 1 <= workers <= 8:
        raise ValueError("workers must be 1..8")
    # The site publishes 50 per directory page.
    planned = max_pages or math.ceil(expected / 50)
    pages, errors = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (number, entries, error) in enumerate(pool.map(get_page, range(1, planned + 1)), start=1):
            if error:
                errors[str(number)] = error
            else:
                pages[number] = entries
            if i % 50 == 0:
                print(f"Fetched {i}/{planned} pages; {sum(map(len,pages.values()))} raw records; errors={len(errors)}", flush=True)
    combined = {}
    for number in sorted(pages):
        for rec in pages[number]:
            combined[rec["id"]] = rec
    records = sorted(combined.values(), key=lambda r: r["id"].casefold())
    return {
        "source": BASE + "/skills/",
        "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "expected_site_count": expected,
        "pages_attempted": planned,
        "pages_succeeded": len(pages),
        "pages_failed": len(errors),
        "failures": errors,
        "count": len(records),
        "records": records,
        "note": "This contains source metadata, not installed Skill code. Licenses/dependencies/actual installs are independent.",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expected", type=int, default=42824, help="Visible directory count, not the separate API-v1 count")
    p.add_argument("--max-pages", type=int, default=0, help="0 crawls the whole expected directory")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out", type=Path, default=Path("codex-plugins/claude-skills-hub/index/catalog.json"))
    p.add_argument("--min-coverage", type=float, default=0.90)
    args = p.parse_args()
    if args.expected <= 0 or args.max_pages < 0 or not 0 < args.min_coverage <= 1:
        p.error("invalid argument")
    data = build_index(args.expected, args.max_pages, args.workers)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode()).hexdigest()
    summary = {k: v for k, v in data.items() if k not in ("records", "failures")}
    summary["sha256"] = digest
    summary["coverage"] = data["count"] / data["expected_site_count"] if not args.max_pages else None
    (args.out.parent / "last-fetch-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    if data["pages_failed"]:
        print("WARNING: incomplete directory pagination", file=sys.stderr)
        return 2
    if not args.max_pages and data["count"] < args.expected * args.min_coverage:
        print("WARNING: site count differs from scraped unique Skill detail links", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
