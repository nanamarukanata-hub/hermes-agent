#!/usr/bin/env python3
"""Mirror browse-sh Skills into inert ZIPs from explicitly whitelisted public GitHub repositories."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
ALLOWED_REPOS = {"browserbase/browse.sh", "browserbase/skills"}
MAX_SKILL_BYTES = 2 * 1024 * 1024

def safe_raw_url(link: str) -> str:
    parsed = urllib.parse.urlsplit(link)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise ValueError("untrusted source domain")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 6:
        raise ValueError("invalid source path")
    repo = "/".join(parts[:2])
    if repo not in ALLOWED_REPOS or parts[2] != "blob" or parts[-1] != "SKILL.md":
        raise ValueError("not whitelisted SKILL.md")
    if any(v in ("..", ".") or "\\" in v or "%2f" in v.lower() for v in parts):
        raise ValueError("unsafe path")
    return "https://raw.githubusercontent.com/" + "/".join(parts[:2] + parts[3:])

def fetch_text(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "HermesBrowseSkillMirror/1.0"}),
                                timeout=25) as stream:
        size = stream.headers.get("Content-Length")
        if size and int(size) > MAX_SKILL_BYTES:
            raise ValueError("file too large")
        data = stream.read(MAX_SKILL_BYTES + 1)
        if len(data) > MAX_SKILL_BYTES:
            raise ValueError("file too large")
        return data

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog",required=True)
    p.add_argument("--output",default="browse-skills-export")
    args=p.parse_args()
    index=json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    skills=[a for a in index.get("skills",[]) if a.get("source")=="browse-sh"]
    out=Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    records=[]
    with zipfile.ZipFile(out/"browse-skills.zip","w",compression=zipfile.ZIP_DEFLATED) as zipout:
        for i,skill in enumerate(skills,1):
            ident=str(skill.get("identifier",""))
            src=(skill.get("extra") or {}).get("source_url","")
            item={"identifier":ident,"source_url":src,"status":"unavailable"}
            try:
                url=safe_raw_url(src)
                raw=fetch_text(url)
                if not raw.strip() or b"\x00" in raw:
                    raise ValueError("invalid SKILL.md")
                prefix="quarantine/"+hashlib.sha256(ident.encode()).hexdigest()[:24]
                zipout.writestr(prefix+"/SKILL.md",raw)
                item.update({"status":"downloaded","bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"path":prefix+"/SKILL.md"})
            except Exception as exc:
                item["error"]=type(exc).__name__+":"+str(exc)[:180]
            records.append(item)
            if i%50==0:
                print("processed",i,"/",len(skills),flush=True)
            time.sleep(0.04)
    (out/"results.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in records),encoding="utf-8")
    summary={"total":len(skills),"downloaded":sum(x["status"]=="downloaded" for x in records),
             "unavailable":sum(x["status"]!="downloaded" for x in records),
             "completed_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
             "note":"Inert SKILL.md mirror, not installed or executed."}
    (out/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(summary),flush=True)
    if not skills:
        raise SystemExit("No browse-sh skills in catalog")
if __name__=="__main__":
    main()
