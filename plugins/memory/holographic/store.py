"""
Vault-backed fact store with entity resolution and trust scoring.
Single-user Hermes memory store plugin.

Replaces the former SQLite/FTS5 backend with Obsidian Vault format:
one JSON file per fact under
``06_Automation/Hermes/HolographicMemory/facts/<fact_id>.json``
(plus ``banks/<name>.json`` and ``entities.json`` in the same tree),
written atomically and serialised with a per-directory flock file.
No SQLite is used.  Keyword search replaces FTS5 MATCH with token
overlap scoring (same public API, same ordering contract: most
relevant first, then trust descending); HRR vectors are stored as
base64 in the fact file instead of a BLOB column.

``HERMES_OBSIDIAN_VAULT`` is required. ``db_path=None`` uses the
Vault's standard HolographicMemory directory. Explicit paths must stay
inside that Vault; legacy ``*.db`` paths are rejected rather than
silently creating a second persistence location.
"""

import base64
import fcntl
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import holographic as hrr
except ImportError:
    import holographic as hrr  # type: ignore[no-redef]

# Trust adjustment constants
_HELPFUL_DELTA = 0.05
_UNHELPFUL_DELTA = -0.10
_TRUST_MIN = 0.0
_TRUST_MAX = 1.0

# Entity extraction patterns
_RE_CAPITALIZED = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_RE_DOUBLE_QUOTE = re.compile(r'"([^"]+)"')
_RE_SINGLE_QUOTE = re.compile(r"'([^']+)'")
_RE_AKA = re.compile(
    r"(\w+(?:\s+\w+)*)\s+(?:aka|also known as)\s+(\w+(?:\s+\w+)*)",
    re.IGNORECASE,
)


def _clamp_trust(value: float) -> float:
    return max(_TRUST_MIN, min(_TRUST_MAX, value))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class MemoryStore:
    """Vault-backed fact store with entity resolution and trust scoring."""

    def __init__(
        self,
        db_path: "str | Path | None" = None,
        default_trust: float = 0.5,
        hrr_dim: int = 1024,
    ) -> None:
        vault_env = os.environ.get("HERMES_OBSIDIAN_VAULT", "").strip()
        if not vault_env:
            raise RuntimeError(
                "HERMES_OBSIDIAN_VAULT is required for holographic memory; "
                "refusing to persist outside the Vault"
            )
        vault = Path(vault_env).expanduser().resolve(strict=False)
        if db_path is None:
            root = vault / "06_Automation" / "Hermes" / "HolographicMemory"
        else:
            configured = Path(db_path).expanduser()
            if configured.suffix.lower() == ".db":
                raise ValueError(
                    "Legacy SQLite db_path is not supported; configure a "
                    "directory inside HERMES_OBSIDIAN_VAULT instead"
                )
            root = configured.resolve(strict=False)
            if root == vault or not root.is_relative_to(vault):
                raise ValueError(
                    "Holographic memory db_path must be a directory inside "
                    "HERMES_OBSIDIAN_VAULT"
                )
        self.db_path = Path(root)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.facts_dir = self.db_path / "facts"
        self.banks_dir = self.db_path / "banks"
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        self.banks_dir.mkdir(parents=True, exist_ok=True)
        self.default_trust = _clamp_trust(default_trust)
        self.hrr_dim = hrr_dim
        self._hrr_available = hrr._HAS_NUMPY
        self._lock = threading.RLock()
        self._lock_depth = 0
        self._lock_file = self.db_path / ".vault-writer.lock"

    # -- internal file helpers ---------------------------------------
    @contextmanager
    def _locked(self):
        """Serialize read-modify-write transactions across threads and processes.

        Nested calls reuse the outer flock, avoiding a deadlock when add_fact
        rebuilds HRR vectors and category banks.
        """
        with self._lock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return

            with open(self._lock_file, "a+", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _fact_path(self, fact_id: int) -> Path:
        return self.facts_dir / f"{int(fact_id)}.json"

    def _read_fact(self, fact_id: int) -> dict | None:
        p = self._fact_path(fact_id)
        if not p.exists() or p.is_symlink():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _write_fact(self, fact: dict) -> None:
        _atomic_write_json(self._fact_path(fact["fact_id"]), fact)

    def _all_facts(self) -> list[dict]:
        facts = []
        for p in sorted(self.facts_dir.glob("*.json")):
            if p.is_symlink():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                facts.append(data)
        return facts

    def _next_fact_id(self) -> int:
        current = 0
        for fact in self._all_facts():
            current = max(current, int(fact.get("fact_id", 0)))
        return current + 1

    def _public(self, fact: dict, include_vector: bool = False) -> dict:
        out = {
            "fact_id": fact["fact_id"],
            "content": fact["content"],
            "category": fact.get("category", "general"),
            "tags": fact.get("tags", ""),
            "trust_score": fact.get("trust_score", self.default_trust),
            "retrieval_count": fact.get("retrieval_count", 0),
            "helpful_count": fact.get("helpful_count", 0),
            "created_at": fact.get("created_at"),
            "updated_at": fact.get("updated_at"),
        }
        if include_vector and fact.get("hrr_vector_b64"):
            try:
                out["hrr_vector"] = base64.b64decode(fact["hrr_vector_b64"])
            except (ValueError, TypeError):
                pass
        return out

    # -- entities ------------------------------------------------------
    def _entities_index(self) -> dict:
        p = self.db_path / "entities.json"
        if not p.exists():
            return {"next_id": 1, "by_name": {}}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"next_id": 1, "by_name": {}}
        return data if isinstance(data, dict) and "by_name" in data else {"next_id": 1, "by_name": {}}

    def _save_entities_index(self, index: dict) -> None:
        _atomic_write_json(self.db_path / "entities.json", index)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_fact(
        self,
        content: str,
        category: str = "general",
        tags: str = "",
    ) -> int:
        """Insert a fact and return its fact_id.

        Deduplicates by content. On duplicate, returns the existing
        fact_id without modifying the row. Extracts entities from
        the content and links them to the fact.
        """
        with self._locked():
            content = content.strip()
            if not content:
                raise ValueError("content must not be empty")

            for existing in self._all_facts():
                if existing.get("content") == content:
                    return int(existing["fact_id"])

            fact_id = self._next_fact_id()
            now = _now_iso()
            fact = {
                "fact_id": fact_id,
                "content": content,
                "category": category,
                "tags": tags,
                "trust_score": self.default_trust,
                "retrieval_count": 0,
                "helpful_count": 0,
                "created_at": now,
                "updated_at": now,
                "entity_ids": [],
                "hrr_vector_b64": None,
            }
            entity_ids = []
            for name in self._extract_entities(content):
                entity_ids.append(self._resolve_entity(name))
            fact["entity_ids"] = entity_ids
            self._write_fact(fact)

            # Compute HRR vector after entity linking
            self._compute_hrr_vector(fact_id, content)
            self._rebuild_bank(category)

            return fact_id

    def search_facts(
        self,
        query: str,
        category: str | None = None,
        min_trust: float = 0.3,
        limit: int = 10,
    ) -> list[dict]:
        """Keyword search over facts.

        Replaces FTS5 ranking with token-overlap scoring, ordered by
        relevance then trust_score descending. Also increments
        retrieval_count for matched facts.
        """
        with self._locked():
            query = query.strip()
            if not query:
                return []
            candidates = self.keyword_candidates(query, category, min_trust, limit * 3)
            results = []
            for fact in candidates[:limit]:
                stored = self._read_fact(fact["fact_id"])
                if stored is not None:
                    stored["retrieval_count"] = stored.get("retrieval_count", 0) + 1
                    self._write_fact(stored)
                    results.append(self._public(stored))
            return results

    def keyword_candidates(
        self,
        query: str,
        category: str | None = None,
        min_trust: float = 0.3,
        limit: int = 30,
    ) -> list[dict]:
        """Keyword candidates for retrieval, with a normalised fts_rank.

        Token overlap over content + tags, normalised to [0, 1] by the
        best match in the candidate set (mirrors the old FTS5 rank
        normalisation in retrieval.py).
        """
        tokens = {t.strip(".,;:!?\"'()[]{}#@<>") for t in query.lower().split() if t.strip(".,;:!?\"'()[]{}#@<>")}
        if not tokens:
            return []
        scored = []
        for fact in self._all_facts():
            if fact.get("trust_score", 0) < min_trust:
                continue
            if category is not None and fact.get("category") != category:
                continue
            hay = f"{fact.get('content', '')} {fact.get('tags', '')}".lower()
            hits = sum(1 for t in tokens if t in hay)
            if hits == 0:
                continue
            scored.append((hits, fact.get("trust_score", 0), fact))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        scored = scored[:limit]
        best = scored[0][0] if scored else 1
        out = []
        for hits, _trust, fact in scored:
            pub = self._public(fact, include_vector=True)
            pub["fts_rank"] = hits / best
            out.append(pub)
        return out

    def facts_with_vectors(self, category: str | None = None) -> list[dict]:
        """Facts that have an HRR vector, with the vector as bytes."""
        out = []
        for fact in self._all_facts():
            if not fact.get("hrr_vector_b64"):
                continue
            if category is not None and fact.get("category") != category:
                continue
            out.append(self._public(fact, include_vector=True))
        return out

    def get_bank_vector(self, bank_name: str) -> bytes | None:
        """Return a memory bank's vector bytes, or None."""
        p = self.banks_dir / f"{bank_name.replace(':', '_')}.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        b64 = data.get("vector_b64") if isinstance(data, dict) else None
        if not b64:
            return None
        try:
            return base64.b64decode(b64)
        except (ValueError, TypeError):
            return None

    def fact_entity_names(self, fact_id: int) -> list[str]:
        """Entity names linked to a fact."""
        fact = self._read_fact(fact_id)
        if fact is None:
            return []
        index = self._entities_index()
        by_id = {v: k for k, v in index.get("by_name", {}).items()}
        return [by_id[i] for i in fact.get("entity_ids", []) if i in by_id]

    def update_fact(
        self,
        fact_id: int,
        content: str | None = None,
        trust_delta: float | None = None,
        tags: str | None = None,
        category: str | None = None,
    ) -> bool:
        """Partially update a fact. Trust is clamped to [0, 1].

        Returns True if the fact existed, False otherwise.
        """
        with self._locked():
            fact = self._read_fact(fact_id)
            if fact is None:
                return False
            if content is not None:
                fact["content"] = content.strip()
            if tags is not None:
                fact["tags"] = tags
            if category is not None:
                fact["category"] = category
            if trust_delta is not None:
                fact["trust_score"] = _clamp_trust(fact.get("trust_score", 0.5) + trust_delta)
            fact["updated_at"] = _now_iso()
            if content is not None:
                fact["entity_ids"] = [self._resolve_entity(n) for n in self._extract_entities(fact["content"])]
            self._write_fact(fact)
            if content is not None:
                self._compute_hrr_vector(fact_id, fact["content"])
            self._rebuild_bank(category or fact.get("category", "general"))
            return True

    def remove_fact(self, fact_id: int) -> bool:
        """Delete a fact and its entity links. Returns True if it existed."""
        with self._locked():
            fact = self._read_fact(fact_id)
            if fact is None:
                return False
            category = fact.get("category", "general")
            self._fact_path(fact_id).unlink(missing_ok=True)
            self._rebuild_bank(category)
            return True

    def list_facts(
        self,
        category: str | None = None,
        min_trust: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        """Browse facts ordered by trust_score descending."""
        with self._locked():
            facts = [
                f for f in self._all_facts()
                if f.get("trust_score", 0) >= min_trust
                and (category is None or f.get("category") == category)
            ]
            facts.sort(key=lambda f: f.get("trust_score", 0), reverse=True)
            return [self._public(f) for f in facts[:limit]]

    def record_feedback(self, fact_id: int, helpful: bool) -> dict:
        """Record user feedback and adjust trust asymmetrically.

        helpful=True  -> trust += 0.05, helpful_count += 1
        helpful=False -> trust -= 0.10

        Returns a dict with fact_id, old_trust, new_trust, helpful_count.
        Raises KeyError if fact_id does not exist.
        """
        with self._locked():
            fact = self._read_fact(fact_id)
            if fact is None:
                raise KeyError(f"fact_id {fact_id} not found")
            old_trust = fact.get("trust_score", 0.5)
            delta = _HELPFUL_DELTA if helpful else _UNHELPFUL_DELTA
            new_trust = _clamp_trust(old_trust + delta)
            helpful_increment = 1 if helpful else 0
            fact["trust_score"] = new_trust
            fact["helpful_count"] = fact.get("helpful_count", 0) + helpful_increment
            fact["updated_at"] = _now_iso()
            self._write_fact(fact)
            return {
                "fact_id": fact_id,
                "old_trust": old_trust,
                "new_trust": new_trust,
                "helpful_count": fact["helpful_count"],
            }

    # ------------------------------------------------------------------
    # Entity helpers
    # ------------------------------------------------------------------

    def _extract_entities(self, text: str) -> list[str]:
        """Extract entity candidates from text using simple regex rules.

        Rules applied (in order):
        1. Capitalized multi-word phrases  e.g. "John Doe"
        2. Double-quoted terms             e.g. "Python"
        3. Single-quoted terms             e.g. 'pytest'
        4. AKA patterns                    e.g. "Guido aka BDFL" -> two entities

        Returns a deduplicated list preserving first-seen order.
        """
        seen: set[str] = set()
        candidates: list[str] = []

        def _add(name: str) -> None:
            stripped = name.strip()
            if stripped and stripped.lower() not in seen:
                seen.add(stripped.lower())
                candidates.append(stripped)

        for m in _RE_CAPITALIZED.finditer(text):
            _add(m.group(1))
        for m in _RE_DOUBLE_QUOTE.finditer(text):
            _add(m.group(1))
        for m in _RE_SINGLE_QUOTE.finditer(text):
            _add(m.group(1))
        for m in _RE_AKA.finditer(text):
            _add(m.group(1))
            _add(m.group(2))
        return candidates

    def _resolve_entity(self, name: str) -> int:
        """Find an existing entity by name (case-insensitive) or create one.

        Returns the entity_id.
        """
        index = self._entities_index()
        by_name = index.get("by_name", {})
        key = name.strip()
        for existing_name, eid in by_name.items():
            if existing_name.lower() == key.lower():
                return int(eid)
        eid = int(index.get("next_id", 1))
        by_name[key] = eid
        index["next_id"] = eid + 1
        index["by_name"] = by_name
        self._save_entities_index(index)
        return eid

    def _link_fact_entity(self, fact_id: int, entity_id: int) -> None:
        """Link an entity to a fact, silently ignore if already linked."""
        fact = self._read_fact(fact_id)
        if fact is None:
            return
        ids = fact.get("entity_ids", [])
        if entity_id not in ids:
            ids.append(entity_id)
            fact["entity_ids"] = ids
            self._write_fact(fact)

    def _compute_hrr_vector(self, fact_id: int, content: str) -> None:
        """Compute and store HRR vector for a fact. No-op if numpy unavailable."""
        with self._locked():
            if not self._hrr_available:
                return
            entities = self.fact_entity_names(fact_id)
            vector = hrr.encode_fact(content, entities, self.hrr_dim)
            fact = self._read_fact(fact_id)
            if fact is None:
                return
            fact["hrr_vector_b64"] = base64.b64encode(hrr.phases_to_bytes(vector)).decode("ascii")
            self._write_fact(fact)

    def _rebuild_bank(self, category: str) -> None:
        """Full rebuild of a category's memory bank from all its fact vectors."""
        with self._locked():
            if not self._hrr_available:
                return
            bank_name = f"cat:{category}"
            bank_path = self.banks_dir / f"{bank_name.replace(':', '_')}.json"
            vectors = []
            for fact in self._all_facts():
                if fact.get("category") != category:
                    continue
                b64 = fact.get("hrr_vector_b64")
                if b64:
                    try:
                        vectors.append(hrr.bytes_to_phases(base64.b64decode(b64)))
                    except (ValueError, TypeError):
                        continue
            if not vectors:
                bank_path.unlink(missing_ok=True)
                return
            bank_vector = hrr.bundle(*vectors)
            hrr.snr_estimate(self.hrr_dim, len(vectors))
            _atomic_write_json(bank_path, {
                "bank_name": bank_name,
                "vector_b64": base64.b64encode(hrr.phases_to_bytes(bank_vector)).decode("ascii"),
                "dim": self.hrr_dim,
                "fact_count": len(vectors),
                "updated_at": _now_iso(),
            })

    def rebuild_all_vectors(self, dim: int | None = None) -> int:
        """Recompute all HRR vectors + banks from text. For recovery/migration.

        Returns the number of facts processed.
        """
        with self._locked():
            if not self._hrr_available:
                return 0
            if dim is not None:
                self.hrr_dim = dim
            facts = self._all_facts()
            categories: set[str] = set()
            for fact in facts:
                self._compute_hrr_vector(fact["fact_id"], fact["content"])
                categories.add(fact.get("category", "general"))
            for category in categories:
                self._rebuild_bank(category)
            return len(facts)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: dict) -> dict:
        """Convert a stored fact dict to a plain public dict."""
        return self._public(row)

    def close(self) -> None:
        """Close the store (no connection to close in Vault mode)."""
        return None

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
