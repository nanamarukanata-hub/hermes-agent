#!/usr/bin/env python3
"""Verify published full ClaudeSkills.info JSON and Codex MCP tools from GitHub checkout."""
import json
import subprocess
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
plugin=root/"codex-plugins/claude-skills-hub"
index=json.loads((plugin/"index/catalog.json").read_text(encoding="utf-8"))
expected=index["expected_site_count"]
records=index["records"]
assert expected>42000 and len(records)>expected*.99, (len(records),expected)
assert len(records)==index["count"],(len(records),index["count"])
ids=[item["id"] for item in records]
assert len(ids)==len(set(ids)), "Duplicate Skill identifiers"
known="vercel-labs/skills/find-skills"
assert known in ids, known
assert all(x["source"].startswith("https://claudeskills.info/skills/") for x in records)
print("INDEX PASS",len(records),"of",expected,"expected, fetched",index["fetched_utc"])

requests=[
 {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}},
 {"jsonrpc":"2.0","id":2,"method":"tools/list"},
 {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"skills_search","arguments":{"query":"vercel-labs/skills/find-skills","limit":2}}},
 {"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"skills_inspect","arguments":{"identifier":known}}},
 {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"skills_install_codex","arguments":{"identifier":known,"project_path":"/tmp","approved":False,"reviewed":False}}}
]
process=subprocess.run([sys.executable,str(plugin/"scripts/mcp_server.py")],cwd=plugin,
 input="".join(json.dumps(x)+"\n" for x in requests),text=True,
 capture_output=True,timeout=30,check=True)
output={x["id"]:x for x in map(json.loads,process.stdout.splitlines())}
assert len(output)==5,output
result=lambda i:json.loads(output[i]["result"]["content"][0]["text"])
assert result(3)["results"][0]["id"]==known, result(3)
assert result(4)["repo"]=="vercel-labs/skills",result(4)
assert result(5)["status"]=="review_required",result(5)
print("MCP PASS: real search, inspection, user-permission gate")
