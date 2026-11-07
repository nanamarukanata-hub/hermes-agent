"""Read official GitHub Releases and write deterministic Markdown. No code installs."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SOURCES = [
    ("Codex", "openai/codex"),
    ("Claude Code", "anthropics/claude-code"),
    ("Hermes Agent", "NousResearch/hermes-agent"),
    ("GBrain", "garrytan/gbrain"),
    ("Obsidian", "obsidianmd/obsidian-releases"),
]

def fetch_latest(repo, token=""):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "omega-release-radar/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    api = "https://api.github.com/repos/" + repo + "/releases/latest"
    with urlopen(Request(api, headers=headers), timeout=25) as response:
        data = json.load(response)
    tag = data.get("tag_name")
    published = data.get("published_at")
    url = data.get("html_url")
    expected = "https://github.com/" + repo + "/releases/tag/"
    if (not isinstance(tag, str) or not tag.strip() or
            not isinstance(url, str) or not url.startswith(expected) or
            not isinstance(published, str)):
        raise ValueError("Invalid official release metadata for " + repo)
    try:
        datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("Invalid release publication time for " + repo) from exc
    return {"tag": tag, "published": published[:10], "url": url}

def render(rows):
    lines = ["---", "type: release-radar", "status: verified-github-release-metadata", "---", "",
             "# AI Infrastructure Release Radar", "",
             "Metadata from official GitHub releases. Does not imply that an update is safe to install.", "",
             "| Project | Latest release | Published (UTC) | Source |",
             "|---|---|---|---|"]
    for name, repo, data in rows:
        tag = data["tag"].replace("|", "\\|").replace("\n", " ")
        # URL must come from an official GitHub repo in the fixed source allowlist.
        url = "https://github.com/" + repo + "/releases"
        lines.append(f"| {name} | `{tag}` | {data['published']} | [GitHub]({url}) |")
    lines += ["", "Review relevance, security and compatibility before installing anything.", ""]
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="omega/knowledge/latest-releases.md")
    args = parser.parse_args()
    rows, failures = [], []
    for name, repo in SOURCES:
        try:
            entry = fetch_latest(repo, os.environ.get("GITHUB_TOKEN", ""))
            if not entry["tag"] or not entry["url"]:
                raise ValueError("Missing release metadata")
            rows.append((name, repo, entry))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            failures.append(f"{repo}: {exc}")
    if failures:
        print("Refusing partial update: " + "; ".join(failures), file=sys.stderr)
        return 1
    contents = render(rows)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_text(encoding="utf-8") == contents:
        print("No change")
        return 0
    output.write_text(contents, encoding="utf-8")
    print(f"Updated {output}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
