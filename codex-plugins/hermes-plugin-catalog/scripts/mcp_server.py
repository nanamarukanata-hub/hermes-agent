#!/usr/bin/env python3
"""Hermes Plugin Catalog bridge for Codex. No third-party plugin is loaded at server startup.

Portable: cwd is the installed plugin root. Only reviewed metadata is read.
Installation occurs ONLY on an explicit tools/call with approved=true.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog"
VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
VERSION = "1.0.0"

def entry(name: str):
    if not VALID_NAME.fullmatch(name):
        return None
    path = CATALOG / (name + ".yaml")
    if not path.is_file() or path.name == "removed.yaml":
        return None
    text = path.read_text(encoding="utf-8")
    props = {}
    for line in text.splitlines():
        m = re.match(r"^(name|repo|sha|tier|category|requires_hermes|version):\s*(.*?)\s*$", line)
        if m:
            props[m.group(1)] = m.group(2).strip('"').strip("'")
    if props.get("name") != name or not re.fullmatch(r"[a-f0-9]{40}", props.get("sha", "")):
        return None
    return {"name": name, "metadata": props, "yaml": text}

def removed_names():
    blocked = set()
    path = CATALOG / "removed.yaml"
    if not path.is_file():
        return blocked
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*-\s*name:\s*([a-z0-9_-]+)", line)
        if m:
            blocked.add(m.group(1))
    return blocked

def all_names():
    return sorted(p.stem for p in CATALOG.glob("*.yaml")
                  if p.name != "removed.yaml" and VALID_NAME.fullmatch(p.stem))

def search(query: str = "", limit: int = 20):
    needle = query.casefold()
    limit = min(max(int(limit), 1), 100)
    blocked = removed_names()
    out = []
    for name in all_names():
        e = entry(name)
        if not e:
            continue
        if needle and needle not in name.casefold() and needle not in e["yaml"].casefold():
            continue
        out.append({"name": name, "tier": e["metadata"].get("tier"),
                    "category": e["metadata"].get("category"),
                    "repo": e["metadata"].get("repo"),
                    "blocked": name in blocked})
        if len(out) >= limit:
            break
    return {"catalog_total": len(all_names()), "results": out}

def inspect(name: str):
    e = entry(name)
    if not e:
        return {"error": "Unknown or invalid plugin name", "name": name}
    return {"name": name, "blocked": name in removed_names(), "metadata": e["metadata"],
            "declared_manifest": e["yaml"][:9000],
            "note": "Catalog listing is not an active Codex tool. Third-party auth and host runtime are separate."}

def install(name: str, approved: bool = False, enable: bool = True):
    if not approved:
        return {"status": "approval_required",
                "message": "An explicit user request to install/run this third-party code is required."}
    e = entry(name)
    if not e:
        return {"status": "invalid_name", "name": name}
    if name in removed_names():
        return {"status": "blocked", "name": name}
    binary = shutil.which("hermes")
    if not binary:
        return {"status": "runtime_unavailable", "name": name,
                "message": "The user's Hermes executable is not on PATH. GitHub changes do not install it."}
    command = [binary, "plugins", "install", name, "--enable" if enable else "--no-enable"]
    try:
        result = subprocess.run(command, input="", capture_output=True, text=True,
                                timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "name": name, "reason": str(exc)}
    return {"name": name, "status": "installed_enabled" if result.returncode == 0 and enable
            else ("installed_disabled" if result.returncode == 0 else "failed"),
            "exit_code": result.returncode,
            "output": (result.stdout + "\n" + result.stderr)[-3500:],
            "reminder": "Gateway restart and service credentials may still be required."}

TOOLS = [
 {"name": "catalog_search", "description": "Find plugins from the 551 Hermes Plugin Catalog entries without executing code.",
  "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}},
 {"name": "catalog_inspect", "description": "Inspect pinned SHA, source, capabilities, auth requirements and original YAML for a plugin.",
  "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
 {"name": "catalog_runtime_status", "description": "Check whether this Codex host can invoke the local Hermes CLI.",
  "inputSchema": {"type": "object", "properties": {}}},
 {"name": "catalog_install_enable", "description": "Install and enable one vetted catalog plugin in the LOCAL Hermes runtime. Requires explicit approved=true. This does not make its tools native Codex tools.",
  "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "approved": {"type": "boolean"}, "enable": {"type": "boolean"}}, "required": ["name", "approved"]}},
 {"name": "catalog_install_batch", "description": "Install and enable up to 10 named plugins sequentially, with explicit authorization. Report success and failures individually.",
  "inputSchema": {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}, "maxItems": 10}, "approved": {"type": "boolean"}}, "required": ["names", "approved"]}}
]

def invoke(name, arguments):
    if name == "catalog_search":
        return search(arguments.get("query", ""), arguments.get("limit", 20))
    if name == "catalog_inspect":
        return inspect(arguments.get("name", ""))
    if name == "catalog_runtime_status":
        return {"hermes_binary": shutil.which("hermes"),
                "catalog_entries": len(all_names()),
                "third_party_plugins_started": False}
    if name == "catalog_install_enable":
        return install(arguments.get("name", ""), arguments.get("approved") is True,
                       arguments.get("enable", True) is True)
    if name == "catalog_install_batch":
        names = arguments.get("names", [])
        if not isinstance(names, list) or len(names) > 10:
            return {"error": "names must be an array of <=10 entries"}
        return {"results": [install(n, arguments.get("approved") is True) for n in names]}
    return {"error": "Unknown tool"}

def respond(item):
    sys.stdout.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()

def main():
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            req = json.loads(raw)
            method = req.get("method")
            if method == "initialize":
                result = {"protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "hermes-plugin-catalog", "version": VERSION}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                args = req.get("params", {})
                output = invoke(args.get("name"), args.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
            elif method == "ping":
                result = {}
            else:
                if "id" in req:
                    respond({"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "Method not found"}})
                continue
            if "id" in req:
                respond({"jsonrpc": "2.0", "id": req["id"], "result": result})
        except Exception as exc:
            if isinstance(req, dict) and "id" in req:
                respond({"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32603, "message": str(exc)[:500]}})

if __name__ == "__main__":
    main()
