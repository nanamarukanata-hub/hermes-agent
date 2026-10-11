#!/usr/bin/env python3
"""Offline validation of Codex marketplace packages and plugin-catalog MCP."""
import json
import subprocess
import sys
import tomllib
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
marketplace = json.loads((repo / ".agents/plugins/marketplace.json").read_text())
config = tomllib.loads((repo / ".codex/config.toml").read_text())
expected = {"hermes-all-skills": 90, "hermes-optional-skills": 71, "hermes-plugin-catalog": 1}
assert marketplace["name"] == "hermes-codex", marketplace
assert {x["name"] for x in marketplace["plugins"]} == set(expected)
for item in marketplace["plugins"]:
    name = item["name"]
    root = (repo / item["source"]["path"]).resolve()
    assert root.is_relative_to(repo) and root.is_dir(), root
    assert item["policy"]["installation"] == "INSTALLED_BY_DEFAULT"
    assert config["plugins"][name + "@hermes-codex"]["enabled"] is True
    plug = json.loads((root / ".codex-plugin/plugin.json").read_text())
    assert plug["name"] == name and plug["skills"] == "./skills/"
    skill_count = len(list((root / "skills").rglob("SKILL.md")))
    assert skill_count == expected[name], (name, skill_count)
    print("PASS", name, skill_count)

catalog = repo / "codex-plugins/hermes-plugin-catalog"
assert len(list((catalog / "catalog").glob("*.yaml"))) == 552
portable = json.loads((catalog / "mcp.json").read_text())
legacy = json.loads((catalog / ".mcp.json").read_text())
assert portable["mcpServers"]["hermes_plugin_catalog"]["cwd"] == "."
assert legacy["mcpServers"]["hermes_plugin_catalog"]["cwd"] == "."
server = catalog / "scripts/mcp_server.py"
requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "catalog_search", "arguments": {"query": "blender", "limit": 5}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
     "params": {"name": "catalog_runtime_status", "arguments": {}}},
]
proc = subprocess.run(
    [sys.executable, str(server)],
    input="".join(json.dumps(r) + "\n" for r in requests),
    capture_output=True, text=True, cwd=catalog, check=True, timeout=20
)
if proc.stderr:
    print(proc.stderr, file=sys.stderr)
responses = [json.loads(line) for line in proc.stdout.splitlines()]
byid = {x["id"]: x for x in responses}
assert len(byid) == 4, responses
assert "tools" in byid[1]["result"]["capabilities"]
names = {x["name"] for x in byid[2]["result"]["tools"]}
assert {"catalog_search", "catalog_inspect", "catalog_install_enable", "catalog_runtime_status", "catalog_delegate_task"} <= names
search = json.loads(byid[3]["result"]["content"][0]["text"])
assert any(item["name"] == "blender" for item in search["results"])
assert search["catalog_total"] == 551
status = json.loads(byid[4]["result"]["content"][0]["text"])
assert status["catalog_entries"] == 551
assert status["third_party_plugins_started"] is False
print("PASS MCP search and handshake; catalog =", status["catalog_entries"])
