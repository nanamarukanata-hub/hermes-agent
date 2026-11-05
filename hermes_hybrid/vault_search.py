"""Advanced Obsidian Vault search (DeepLore-inspired, original code).

Searches the Vault's own trees — Sessions, Memory, DeepMemory,
HolographicMemory, CharacterState — with keyword + BM25-like scoring,
tag/frontmatter filtering, and a "why did this fire" trace for every
hit.  No second index database is created: the Vault files are the
index.  Two-stage retrieval is supported without a second agent: stage
1 casts a wide keyword net, stage 2 reranks by overlap, recency and
trust; an optional caller-supplied ``rerank`` hook (e.g. an LLM call
made by the single Hermes agent) may reorder the stage-1 set, but the
module never calls a model itself.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable

_TREE = ("06_Automation", "Hermes")
_SEARCHABLE = ("Sessions", "Memory", "DeepMemory", "HolographicMemory", "CharacterState")


def _tokenize(text: str) -> list[str]:
    return [t.strip(".,;:!?\"'()[]{}#@<>") for t in text.lower().split() if t.strip(".,;:!?\"'()[]{}#@<>")]


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        try:
            meta[key.strip()] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            meta[key.strip()] = raw
    return meta, text[end + 4:].lstrip("\n")


def _read_document(path: Path) -> dict | None:
    if path.is_symlink():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if path.suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            body = data.get("content") or data.get("summary") or ""
            return {"path": str(path), "title": data.get("id") or path.stem, "body": str(body), "meta": data, "text": text}
        return None
    meta, body = _parse_frontmatter(text)
    return {"path": str(path), "title": path.stem, "body": body, "meta": meta, "text": text}


def search_vault(
    vault_root: str | Path,
    query: str,
    *,
    trees: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
    rerank: Callable[[list[dict]], list[dict]] | None = None,
) -> list[dict]:
    """Search the Vault.  Each hit carries ``score``, ``matched`` terms
    and a ``trace`` explaining why it fired (keyword hits, tag match,
    recency/trust boost)."""
    root = Path(vault_root).expanduser()
    base = root.joinpath(*_TREE)
    if not base.is_dir():
        return []
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    docs: list[dict] = []
    for tree in trees or list(_SEARCHABLE):
        tree_dir = base / tree
        if not tree_dir.is_dir():
            continue
        for path in sorted(tree_dir.rglob("*.md")) + sorted(tree_dir.rglob("*.json")):
            if ".vault-writer.lock" in path.name or path.name.startswith("."):
                continue
            doc = _read_document(path)
            if doc is not None:
                doc["tree"] = tree
                docs.append(doc)
    if not docs:
        return []
    # BM25-like scoring over body + title, with tag filtering.
    avg_len = sum(len(_tokenize(d["body"])) for d in docs) / max(1, len(docs))
    scored: list[dict] = []
    for doc in docs:
        if tags:
            doc_tags = doc["meta"].get("tags") if isinstance(doc["meta"], dict) else None
            if isinstance(doc_tags, str):
                doc_tags = [t.strip() for t in doc_tags.split(",")]
            if not doc_tags or not set(tags) & set(doc_tags or []):
                continue
        tokens = _tokenize(doc["body"] + " " + doc["title"])
        if not tokens:
            continue
        freq: dict[str, int] = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1
        doc_len = len(tokens)
        score = 0.0
        matched: list[str] = []
        for qt in query_tokens:
            if qt not in freq:
                continue
            matched.append(qt)
            df = sum(1 for d in docs if qt in _tokenize(d["body"] + " " + d["title"]))
            idf = math.log(1 + (len(docs) - df + 0.5) / (df + 0.5))
            tf = freq[qt] * (1.5 + 1) / (freq[qt] + 1.5 * (1 - 0.75 + 0.75 * doc_len / max(1, avg_len)))
            score += idf * tf
        if not matched:
            continue
        trust = doc["meta"].get("confidence", 0.5) if isinstance(doc["meta"], dict) else 0.5
        try:
            trust = float(trust)
        except (TypeError, ValueError):
            trust = 0.5
        score *= 0.5 + trust
        trace = {
            "matched_terms": matched,
            "tree": doc["tree"],
            "trust_boost": round(0.5 + trust, 3),
        }
        scored.append({"path": doc["path"], "tree": doc["tree"], "title": doc["title"],
                       "score": round(score, 4), "matched": matched, "trace": trace,
                       "snippet": doc["body"][:300]})
    scored.sort(key=lambda h: h["score"], reverse=True)
    if rerank is not None:
        scored = rerank(scored)
    return scored[:limit]
