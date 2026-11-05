"""Deep memory for Hermes RP: layered, episodic, graph-aware, Vault-only.

Original implementation.  Designs were *inspired* by the projects
listed in ``docs/HERMES-DEEP-MEMORY.md`` (Smart-Memory, Chaotic
Lorebooks, Chronicler, DeepLore, BlazeTracker, Neptunia, TomoriBot,
SillyTavern-Discord-Connector); no code was copied from them, in
particular no AGPL-3.0 code was copied.  See that doc for the
per-project license table and attribution.

Canonical layout (all under ``HERMES_OBSIDIAN_VAULT``):

- ``06_Automation/Hermes/DeepMemory/memories/<id>.json``
  — one record per memory, with ``layer`` (``reflex`` | ``heuristic``
  | ``canon``), ``kind`` (fact/episode/scene/state), ``content``,
  ``entities``, ``visible_to`` (ACL: list of user/character ids that
  may recall it; empty means everyone), ``status`` (``active`` |
  ``superseded`` | ``retracted``), ``confidence``, ``created_at``,
  ``updated_at``, ``superseded_by``, ``source``.
- ``06_Automation/Hermes/DeepMemory/episodes/<id>.json``
  — sealed episode summaries (arc summaries): participants,
  location, time span, tension, witnessed_by, summary.
- ``06_Automation/Hermes/DeepMemory/scenes/<id>.json``
  — live scene state: time, location, weather, present characters,
  outfits, props, tone, tension, plus the last sealed scenes.
- ``06_Automation/Hermes/DeepMemory/graph/entities.json``
  — entity registry (name, type, aliases).
- ``06_Automation/Hermes/DeepMemory/graph/relations.json``
  — pair relations: feelings (max 6, with magnitude), knows, wants,
  status, history events.
- ``06_Automation/Hermes/DeepMemory/contradictions/<id>.json``
  — detected contradictions, with status open/resolved/retconned.

Rules enforced here (not left to prompt discretion):
- A memory with a non-empty ``visible_to`` is never returned to a
  viewer outside that list (mechanical ACL, Chronicler-style).
- Superseding a memory never deletes it: the old record stays with
  ``status=superseded`` and a pointer to its replacement.
- No second agent and no second database: retrieval is a pure
  function over Vault files, and token budgeting is deterministic
  (estimated tokens = ceil(chars / 4), documented as an estimate).
- No character or world settings are created here; all content comes
  from the caller (chat turns, user edits, or explicit API calls).
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

LAYERS = ("reflex", "heuristic", "canon")
_LAYER_PRIORITY = {"canon": 0, "heuristic": 1, "reflex": 2}
_MAGNITUDES = ("low", "medium", "high")
_NEGATION_RE = re.compile(
    r"\b(no longer|not anymore|never|was disbanded|left|moved away|"
    r"no longer trusts|does not trust|doesn't trust|ended|broke up|"
    r"quit|retired|dead|died)\b",
    re.IGNORECASE,
)


def estimate_tokens(text: str) -> int:
    """Approximate token count (chars / 4, rounded up).  An estimate."""
    return max(1, math.ceil(len(text) / 4))


def _tokenize(text: str) -> set[str]:
    return {t.strip(".,;:!?\"'()[]{}#@<>") for t in text.lower().split() if t.strip(".,;:!?\"'()[]{}#@<>")}


def _now() -> float:
    return time.time()


class DeepMemory:
    """Vault-backed deep memory store."""

    def __init__(self, vault_root: str | Path):
        self.root = Path(vault_root).expanduser()
        self.tree = self.root / "06_Automation" / "Hermes" / "DeepMemory"
        self.memories_dir = self.tree / "memories"
        self.episodes_dir = self.tree / "episodes"
        self.scenes_dir = self.tree / "scenes"
        self.graph_dir = self.tree / "graph"
        self.contradictions_dir = self.tree / "contradictions"
        for d in (self.memories_dir, self.episodes_dir, self.scenes_dir, self.graph_dir, self.contradictions_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- low-level -----------------------------------------------------
    def _atomic(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # An attacker can pre-create a predictable PID-named temp symlink.
        # mkstemp creates a fresh file with an unpredictable name and O_EXCL.
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.lexists(tmp):
                os.unlink(tmp)

    def _read_dir(self, directory: Path) -> list[dict]:
        out = []
        for p in sorted(directory.glob("*.json")):
            if p.is_symlink():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append(data)
        return out

    # -- memories ------------------------------------------------------
    def add_memory(
        self,
        content: str,
        *,
        layer: str = "heuristic",
        kind: str = "fact",
        entities: list[str] | None = None,
        visible_to: list[str] | None = None,
        confidence: float = 0.5,
        source: str = "chat",
        supersedes: str | None = None,
    ) -> dict:
        """Add a memory, checking for contradictions and supersession."""
        if layer not in LAYERS:
            raise ValueError(f"unknown layer: {layer!r}")
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")
        record = {
            "id": uuid.uuid4().hex[:16],
            "layer": layer,
            "kind": kind,
            "content": content,
            "entities": sorted({e.strip() for e in (entities or []) if e.strip()}),
            "visible_to": list(visible_to or []),
            "status": "active",
            "confidence": max(0.0, min(1.0, confidence)),
            "created_at": _now(),
            "updated_at": _now(),
            "superseded_by": None,
            "source": source,
        }
        if supersedes:
            old = self.get_memory(supersedes)
            if old is not None and old["status"] == "active":
                old["status"] = "superseded"
                old["superseded_by"] = record["id"]
                old["updated_at"] = _now()
                self._atomic(self.memories_dir / f"{old['id']}.json", old)
        self._atomic(self.memories_dir / f"{record['id']}.json", record)
        # Contradiction detection (deterministic, no LLM): same entity,
        # negation language, and no explicit supersession link.
        if not supersedes:
            for other in self._read_dir(self.memories_dir):
                if other["id"] == record["id"] or other["status"] != "active":
                    continue
                shared = set(other.get("entities", [])) & set(record["entities"])
                if not shared:
                    continue
                if _NEGATION_RE.search(record["content"]) or _NEGATION_RE.search(other["content"]):
                    self._record_contradiction(other, record, list(shared))
        return record

    def get_memory(self, memory_id: str) -> dict | None:
        p = self.memories_dir / f"{memory_id}.json"
        if not p.exists() or p.is_symlink():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _record_contradiction(self, old: dict, new: dict, shared: list[str]) -> dict:
        record = {
            "id": uuid.uuid4().hex[:16],
            "old_id": old["id"],
            "new_id": new["id"],
            "shared_entities": shared,
            "status": "open",
            "created_at": _now(),
            "resolved_at": None,
            "resolution": None,
        }
        self._atomic(self.contradictions_dir / f"{record['id']}.json", record)
        return record

    def list_contradictions(self, status: str | None = None) -> list[dict]:
        rows = self._read_dir(self.contradictions_dir)
        if status is not None:
            rows = [r for r in rows if r.get("status") == status]
        return rows

    def resolve_contradiction(self, contradiction_id: str, resolution: str, *, keep_id: str | None = None) -> dict | None:
        """Resolve a contradiction. ``resolution``: keep_new | keep_old | retcon.

        keep_new supersedes the old record; keep_old retracts the new
        one; retcon retracts both (the caller supplies the corrected
        record separately).  Nothing is deleted.
        """
        p = self.contradictions_dir / f"{contradiction_id}.json"
        if not p.exists():
            return None
        record = json.loads(p.read_text(encoding="utf-8"))
        if resolution == "keep_new" and keep_id is None:
            keep_id = record["new_id"]
        if resolution == "keep_new":
            old = self.get_memory(record["old_id"])
            if old is not None:
                old["status"] = "superseded"
                old["superseded_by"] = record["new_id"]
                old["updated_at"] = _now()
                self._atomic(self.memories_dir / f"{old['id']}.json", old)
        elif resolution == "keep_old":
            new = self.get_memory(record["new_id"])
            if new is not None:
                new["status"] = "retracted"
                new["updated_at"] = _now()
                self._atomic(self.memories_dir / f"{new['id']}.json", new)
        elif resolution == "retcon":
            for mid in (record["old_id"], record["new_id"]):
                rec = self.get_memory(mid)
                if rec is not None:
                    rec["status"] = "retracted"
                    rec["updated_at"] = _now()
                    self._atomic(self.memories_dir / f"{rec['id']}.json", rec)
        else:
            raise ValueError(f"unknown resolution: {resolution!r}")
        record["status"] = "resolved" if resolution != "retcon" else "retconned"
        record["resolution"] = resolution
        record["resolved_at"] = _now()
        self._atomic(p, record)
        return record

    def promote(self, memory_id: str, to_layer: str) -> dict | None:
        """Promote a memory up the tier ladder (reflex -> heuristic -> canon).

        Canon is durable and user-confirmed in intent: promotion to canon
        is an explicit caller action, never automatic in this module.
        """
        if to_layer not in LAYERS:
            raise ValueError(f"unknown layer: {to_layer!r}")
        rec = self.get_memory(memory_id)
        if rec is None:
            return None
        rec["layer"] = to_layer
        rec["updated_at"] = _now()
        self._atomic(self.memories_dir / f"{memory_id}.json", rec)
        return rec

    def demote(self, memory_id: str, to_layer: str = "heuristic") -> dict | None:
        return self.promote(memory_id, to_layer)

    def forget(self, memory_id: str) -> bool:
        """Retract (never delete) a memory."""
        rec = self.get_memory(memory_id)
        if rec is None:
            return False
        rec["status"] = "retracted"
        rec["updated_at"] = _now()
        self._atomic(self.memories_dir / f"{memory_id}.json", rec)
        return True

    # -- retrieval with token budget ------------------------------------
    def retrieve(
        self,
        query: str,
        *,
        budget_tokens: int = 1000,
        viewer: str | None = None,
        layers: list[str] | None = None,
        limit: int = 20,
    ) -> dict:
        """Retrieve memories within a token budget.

        Scoring: token overlap (relevance) * confidence, with layer
        priority (canon first) as the primary sort key, then score,
        then recency.  The ACL filter runs *before* ranking, so a
        viewer can never pull a secret into the ranking by query
        wording.  Returns the rendered section, tokens used, and items.
        """
        tokens = _tokenize(query)
        allowed_layers = set(layers or LAYERS)
        candidates = []
        for rec in self._read_dir(self.memories_dir):
            if rec.get("status") != "active":
                continue
            if rec.get("layer") not in allowed_layers:
                continue
            visible = rec.get("visible_to") or []
            if visible and viewer is not None and viewer not in visible:
                continue
            if visible and viewer is None:
                continue
            hay = _tokenize(rec.get("content", "")) | {e.lower() for e in rec.get("entities", [])}
            overlap = len(tokens & hay) / max(1, len(tokens)) if tokens else 0.0
            if tokens and overlap == 0:
                continue
            score = overlap * rec.get("confidence", 0.5)
            candidates.append(( _LAYER_PRIORITY[rec["layer"]], -score, -rec.get("updated_at", 0), rec))
        candidates.sort(key=lambda x: (x[0], x[1], x[2]))
        items: list[dict] = []
        used = 0
        for _prio, _neg, _time, rec in candidates[:limit]:
            text = rec["content"]
            cost = estimate_tokens(text)
            if items and used + cost > budget_tokens:
                continue
            if not items and cost > budget_tokens:
                text = text[: budget_tokens * 4]
                cost = estimate_tokens(text)
                rec = {**rec, "content": text, "truncated": True}
            items.append(rec)
            used += cost
            if used >= budget_tokens:
                break
        section = "\n".join(f"- [{r['layer']}] {r['content']}" for r in items)
        return {"section": section, "tokens_used": used, "budget_tokens": budget_tokens, "items": items}

    # -- episodes --------------------------------------------------------
    def seal_episode(
        self,
        summary: str,
        *,
        participants: list[str] | None = None,
        location: str | None = None,
        witnessed_by: list[str] | None = None,
        tension: str | None = None,
    ) -> dict:
        """Seal an episode (arc) summary.  Episodes are the long-term
        narrative unit; sealing never deletes the underlying memories."""
        record = {
            "id": uuid.uuid4().hex[:16],
            "summary": summary.strip(),
            "participants": sorted({p for p in (participants or [])}),
            "location": location,
            "witnessed_by": list(witnessed_by or []),
            "tension": tension,
            "created_at": _now(),
        }
        self._atomic(self.episodes_dir / f"{record['id']}.json", record)
        return record

    def list_episodes(self, limit: int = 20) -> list[dict]:
        rows = self._read_dir(self.episodes_dir)
        rows.sort(key=lambda r: r.get("created_at", 0), reverse=True)
        return rows[:limit]

    # -- scene continuity -------------------------------------------------
    def start_scene(self, scene_id: str, **state: Any) -> dict:
        record = {"id": scene_id, "status": "open", "created_at": _now(), "updated_at": _now(), **state}
        self._atomic(self.scenes_dir / f"{scene_id}.json", record)
        return record

    def update_scene(self, scene_id: str, **state: Any) -> dict | None:
        p = self.scenes_dir / f"{scene_id}.json"
        if not p.exists():
            return None
        record = json.loads(p.read_text(encoding="utf-8"))
        record.update(state)
        record["updated_at"] = _now()
        self._atomic(p, record)
        return record

    def end_scene(self, scene_id: str, summary: str) -> dict | None:
        """Close a scene and seal it as an episode, keeping the last
        scene summaries available for continuity context."""
        record = self.update_scene(scene_id, status="closed")
        if record is None:
            return None
        episode = self.seal_episode(
            summary,
            participants=record.get("present") or record.get("participants"),
            location=record.get("location"),
            witnessed_by=record.get("present") or record.get("participants"),
            tension=record.get("tension"),
        )
        record["episode_id"] = episode["id"]
        self._atomic(self.scenes_dir / f"{scene_id}.json", record)
        return record

    def scene_context(self, scene_id: str) -> str:
        """Compact scene-state block for prompt injection."""
        p = self.scenes_dir / f"{scene_id}.json"
        if not p.exists():
            return ""
        record = json.loads(p.read_text(encoding="utf-8"))
        lines = []
        for key in ("time", "location", "weather", "tone", "tension"):
            if record.get(key):
                lines.append(f"{key}: {record[key]}")
        present = record.get("present") or record.get("participants") or []
        if present:
            lines.append(f"present: {', '.join(present)}")
        if record.get("props"):
            lines.append(f"props: {', '.join(record['props'])}")
        return "\n".join(lines)

    def recent_scenes(self, limit: int = 5) -> list[dict]:
        rows = [r for r in self._read_dir(self.scenes_dir) if r.get("status") == "closed"]
        rows.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
        return rows[:limit]

    # -- relationship graph ----------------------------------------------
    def _graph(self, name: str, default: Any) -> Any:
        p = self.graph_dir / f"{name}.json"
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    def register_entity(self, name: str, entity_type: str = "unknown", aliases: list[str] | None = None) -> dict:
        index = self._graph("entities", {})
        key = name.strip()
        entry = index.get(key, {"name": key, "type": entity_type, "aliases": []})
        entry["type"] = entity_type
        for alias in aliases or []:
            if alias not in entry["aliases"]:
                entry["aliases"].append(alias)
        index[key] = entry
        self._atomic(self.graph_dir / "entities.json", index)
        return entry

    def add_relation(
        self,
        a: str,
        b: str,
        *,
        feelings: list[tuple[str, str]] | None = None,
        knows: list[str] | None = None,
        wants: list[str] | None = None,
        status: str | None = None,
    ) -> dict:
        """Add or update a pair relation.  Feelings are capped at 6
        descriptors (lowest magnitude dropped first), mirroring the
        Smart-Memory relationship-matrix cap without copying its code."""
        relations = self._graph("relations", {})
        key = " | ".join(sorted([a.strip(), b.strip()]))
        entry = relations.get(key, {
            "pair": sorted([a.strip(), b.strip()]),
            "feelings": [],
            "knows": [],
            "wants": [],
            "status": None,
            "history": [],
        })
        _mag_rank = {"low": 0, "medium": 1, "high": 2}
        for word, mag in feelings or []:
            if mag not in _MAGNITUDES:
                raise ValueError(f"unknown magnitude: {mag!r}")
            entry["feelings"] = [f for f in entry["feelings"] if f[0] != word]
            entry["feelings"].append([word, mag])
            entry["feelings"].sort(key=lambda f: _mag_rank[f[1]])
            entry["feelings"] = entry["feelings"][-6:]
        for field, values in (("knows", knows), ("wants", wants)):
            for v in values or []:
                if v not in entry[field]:
                    entry[field].append(v)
        if status is not None:
            entry["status"] = status
        entry["history"].append({"at": _now(), "feelings": feelings or [], "status": status})
        relations[key] = entry
        self._atomic(self.graph_dir / "relations.json", relations)
        return entry

    def get_relation(self, a: str, b: str) -> dict | None:
        relations = self._graph("relations", {})
        return relations.get(" | ".join(sorted([a.strip(), b.strip()])))

    def graph_neighbors(self, name: str) -> list[dict]:
        relations = self._graph("relations", {})
        return [entry for entry in relations.values() if name.strip() in entry.get("pair", [])]

    # -- drift audit --------------------------------------------------------
    def drift_audit(self) -> list[dict]:
        """Cross-memory drift audit: pairs with high entity overlap and
        low content overlap are recorded as open contradictions."""
        active = [r for r in self._read_dir(self.memories_dir) if r.get("status") == "active"]
        found = []
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                a, b = active[i], active[j]
                ents_a, ents_b = set(a.get("entities", [])), set(b.get("entities", []))
                if not ents_a or not ents_b:
                    continue
                overlap = len(ents_a & ents_b) / len(ents_a | ents_b)
                if overlap < 0.5:
                    continue
                words_a, words_b = _tokenize(a["content"]), _tokenize(b["content"])
                sim = len(words_a & words_b) / max(1, len(words_a | words_b))
                if sim < 0.3:
                    found.append(self._record_contradiction(a, b, sorted(ents_a & ents_b)))
        return found
