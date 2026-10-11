#!/usr/bin/env python3
"""ClaudeSkills.info read-only directory index MCP for Codex.

No network access or third-party code is loaded at server startup.
An explicit user-approved install is needed for each named Skill.
"""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index" / "catalog.json"
VALID = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
VERSION = "1.0.0"

def catalog():
    try:
        payload = json.loads(INDEX.read_text(encoding="utf-8"))
        records = payload.get("records")
        if not isinstance(records, list):
            raise ValueError("Missing records")
        return payload
    except (OSError, ValueError, TypeError):
        return {"records": [], "count": 0, "note": "No valid catalog yet. Run skills_refresh_index."}

def search(query="", limit=20):
    needle = str(query).casefold()
    k = min(max(int(limit), 1), 100)
    source = catalog()
    rows = []
    for record in source["records"]:
        if not isinstance(record, dict) or "id" not in record:
            continue
        if needle and needle not in record["id"].casefold():
            continue
        rows.append(record)
        if len(rows) >= k:
            break
    return {"indexed": len(source["records"]), "site_expected": source.get("expected_site_count"),
            "results": rows, "note": source.get("note")}

def inspect(identifier):
    if not isinstance(identifier, str) or not VALID.fullmatch(identifier):
        return {"error": "Invalid owner/repo/skill identifier"}
    match = next((x for x in catalog()["records"] if x.get("id") == identifier), None)
    if not match:
        return {"error": "Skill not found in local catalog", "identifier": identifier}
    return {**match, "github_repo": "https://github.com/" + match["repo"],
            "install_command": f"npx skills add {match['repo']} --skill {match['skill']} --agent codex --yes",
            "warning": "Source quality, license, requirements and actual GitHub subdirectory must be verified before installation."}

def status():
    obj = catalog()
    return {"indexed": len(obj["records"]), "expected": obj.get("expected_site_count"),
            "last_fetch_utc": obj.get("fetched_utc"), "node_cli": shutil.which("npx"),
            "third_party_skills_executed": False}

def install(identifier, project_path, approved=False, reviewed=False, global_install=False):
    if approved is not True or reviewed is not True:
        return {"status": "review_required",
                "message": "Explicit authorization and source review are required. Use skills_inspect first."}
    detail = inspect(identifier)
    if "error" in detail:
        return {"status": "not_in_catalog", **detail}
    root = Path(project_path).expanduser()
    if not root.is_absolute() or not root.is_dir():
        return {"status": "invalid_project", "message": "Provide an existing absolute Codex project directory."}
    npx = shutil.which("npx")
    if not npx:
        return {"status": "missing_node", "message": "npx is unavailable on the Codex host."}
    cmd = [npx, "--yes", "skills", "add", detail["repo"], "--skill",
           detail["skill"], "--agent", "codex", "--yes", "--copy"]
    if global_install:
        cmd.append("--global")
    try:
        proc = subprocess.run(cmd, cwd=root, input="", text=True,
                              capture_output=True, timeout=160, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "reason": str(exc)}
    return {"status": "install_command_succeeded" if proc.returncode == 0 else "install_failed",
            "exit_code": proc.returncode, "project_path": str(root), "identifier": identifier,
            "stdout": proc.stdout[-4500:], "stderr": proc.stderr[-1000:],
            "verification_required": "Check .agents/skills and restart Codex; script/credential requirements may remain."}

def refresh(expected=42824, max_pages=0, approved=False):
    if not approved:
        return {"status": "approval_required", "message": "Network fetch must be explicitly authorized."}
    script = ROOT / "scripts" / "indexer.py"
    if not script.is_file():
        return {"status": "unavailable", "message": "Bundled indexer not found"}
    cmd = [sys.executable, str(script), "--expected", str(expected),
           "--max-pages", str(max_pages), "--out", str(INDEX), "--workers", "4"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "error": str(exc)}
    return {"status": "complete" if result.returncode == 0 else "incomplete",
            "exit_code": result.returncode, "stdout": result.stdout[-2500:],
            "stderr": result.stderr[-1400:], **status()}

TOOLS = [
{"name":"skills_search","description":"Find entries from all indexed ClaudeSkills.info Skill listings without running third-party code.",
"inputSchema":{"type":"object","properties":{"query":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":100}}}},
{"name":"skills_inspect","description":"Show public source repo URL and exact install command for a listed Skill.",
"inputSchema":{"type":"object","properties":{"identifier":{"type":"string"}},"required":["identifier"]}},
{"name":"skills_catalog_status","description":"Get indexed count, refresh time and local Codex prerequisites.",
"inputSchema":{"type":"object","properties":{}}},
{"name":"skills_refresh_index","description":"Refresh the entire public directory metadata without downloading community code. Only with approved=true.",
"inputSchema":{"type":"object","properties":{"expected":{"type":"integer"},"max_pages":{"type":"integer"},"approved":{"type":"boolean"}},"required":["approved"]}},
{"name":"skills_install_codex","description":"Install ONE reviewed Skill from its GitHub repo using npx skills add into an existing Codex project. Explicit approved=true and reviewed=true required.",
"inputSchema":{"type":"object","properties":{"identifier":{"type":"string"},"project_path":{"type":"string"},"approved":{"type":"boolean"},"reviewed":{"type":"boolean"},"global_install":{"type":"boolean"}},"required":["identifier","project_path","approved","reviewed"]}}
]

def dispatch(name, a):
    if name == "skills_search": return search(a.get("query",""),a.get("limit",20))
    if name == "skills_inspect": return inspect(a.get("identifier"))
    if name == "skills_catalog_status": return status()
    if name == "skills_install_codex":
        return install(a.get("identifier"),a.get("project_path",""),
                       a.get("approved") is True,a.get("reviewed") is True,
                       a.get("global_install") is True)
    if name == "skills_refresh_index":
        return refresh(a.get("expected",42824),a.get("max_pages",0),a.get("approved") is True)
    return {"error":"Unknown tool"}

def respond(result): 
    sys.stdout.write(json.dumps(result,ensure_ascii=False,separators=(",",":"))+"\n");sys.stdout.flush()

def main():
    for line in sys.stdin:
        if not line.strip(): continue
        req = None
        try:
            req=json.loads(line)
            method=req.get("method")
            if method=="initialize":
                output={"protocolVersion":req.get("params",{}).get("protocolVersion","2025-06-18"),
                        "capabilities":{"tools":{"listChanged":False}},
                        "serverInfo":{"name":"claude-skills-hub","version":VERSION}}
            elif method=="tools/list":
                output={"tools":TOOLS}
            elif method=="tools/call":
                param=req.get("params",{})
                content=dispatch(param.get("name",""),param.get("arguments") or {})
                output={"content":[{"type":"text","text":json.dumps(content,ensure_ascii=False)}]}
            elif method=="ping": output={}
            else:
                if "id" in req: respond({"jsonrpc":"2.0","id":req["id"],
                   "error":{"code":-32601,"message":"Method not found"}})
                continue
            if "id" in req: respond({"jsonrpc":"2.0","id":req["id"],"result":output})
        except Exception as exc:
            if isinstance(req,dict) and "id" in req:
                respond({"jsonrpc":"2.0","id":req["id"],
                         "error":{"code":-32603,"message":str(exc)[:300]}})

if __name__=="__main__":
    main()
