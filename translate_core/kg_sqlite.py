"""SQLite persistence layer for the KnowledgeGraph.

Replaces the 50 MB JSON read/write cycle with incremental, indexed
row-level operations.  The in-memory ``nx.DiGraph`` remains the query
layer; this module is the *only* code that touches SQL directly.

Schema
------
``nodes``        one row per graph node; ``attrs`` is a JSON blob.
``edges``        one row per graph edge; ``attrs`` carries ``relation`` + metadata.
``term_keywords`` mirrors the flashtext KeywordProcessor (exact + normalised).
``kg_meta``      single-row metadata (schema version, write counter).

All writes go through ``KGStore`` methods that accept plain Python dicts —
callers never see SQL.  WAL journal mode allows concurrent readers (editor
+ maintenance scripts) and one writer at a time.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import logging
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

_SCHEMA_VERSION = "1"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id    TEXT PRIMARY KEY,
    type  TEXT NOT NULL DEFAULT '',
    attrs TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);

CREATE TABLE IF NOT EXISTS edges (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    attrs  TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (source, target)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);

CREATE TABLE IF NOT EXISTS term_keywords (
    keyword    TEXT NOT NULL,
    term_id    TEXT NOT NULL,
    normalized INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (keyword, term_id, normalized)
);
CREATE INDEX IF NOT EXISTS idx_term_keywords_keyword ON term_keywords(keyword);

CREATE TABLE IF NOT EXISTS kg_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class KGStore:
    """Low-level SQLite CRUD for the knowledge graph."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage transactions explicitly
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO kg_meta (key, value) VALUES ('schema_version', ?)",
            (_SCHEMA_VERSION,),
        )

    # ------------------------------------------------------------------
    # Node CRUD
    # ------------------------------------------------------------------
    def upsert_node(self, node_id: str, node_type: str, attrs: dict) -> None:
        """Insert or replace a node.  ``attrs`` must NOT contain ``id`` or ``type``
        (they live in dedicated columns)."""
        clean = {k: v for k, v in attrs.items() if k not in ("id", "type")}
        self._conn.execute(
            "INSERT OR REPLACE INTO nodes (id, type, attrs) VALUES (?, ?, ?)",
            (node_id, node_type, json.dumps(clean, ensure_ascii=False, default=str)),
        )

    def update_node_attrs(self, node_id: str, updates: dict) -> None:
        """Merge ``updates`` into an existing node's attrs JSON."""
        row = self._conn.execute(
            "SELECT attrs FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return
        current = json.loads(row[0])
        current.update(updates)
        clean = {k: v for k, v in current.items() if k not in ("id", "type")}
        self._conn.execute(
            "UPDATE nodes SET attrs = ? WHERE id = ?",
            (json.dumps(clean, ensure_ascii=False, default=str), node_id),
        )

    def has_node(self, node_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM nodes WHERE id = ?", (node_id,)
        ).fetchone() is not None

    def get_node(self, node_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT type, attrs FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[1])
        data["id"] = node_id
        data["type"] = row[0]
        return data

    def delete_node(self, node_id: str) -> None:
        """Delete a node and all its edges (explicit cascade — WAL mode
        sometimes drops FK enforcement across executescript boundaries)."""
        self._conn.execute("DELETE FROM edges WHERE source = ? OR target = ?", (node_id, node_id))
        self._conn.execute("DELETE FROM term_keywords WHERE term_id = ?", (node_id,))
        self._conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))

    def iter_nodes(self) -> Iterator[dict]:
        """Yield every node as a dict with ``id`` and ``type`` keys."""
        for node_id, node_type, attrs_json in self._conn.execute(
            "SELECT id, type, attrs FROM nodes"
        ):
            data = json.loads(attrs_json)
            data["id"] = node_id
            data["type"] = node_type
            yield data

    def iter_nodes_by_type(self, node_type: str) -> Iterator[dict]:
        for node_id, attrs_json in self._conn.execute(
            "SELECT id, attrs FROM nodes WHERE type = ?", (node_type,)
        ):
            data = json.loads(attrs_json)
            data["id"] = node_id
            data["type"] = node_type
            yield data

    def count_nodes(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    # ------------------------------------------------------------------
    # Edge CRUD
    # ------------------------------------------------------------------
    def upsert_edge(self, source: str, target: str, attrs: dict) -> None:
        clean = {k: v for k, v in attrs.items() if k not in ("source", "target")}
        self._conn.execute(
            "INSERT OR REPLACE INTO edges (source, target, attrs) VALUES (?, ?, ?)",
            (source, target, json.dumps(clean, ensure_ascii=False, default=str)),
        )

    def has_edge(self, source: str, target: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM edges WHERE source = ? AND target = ?", (source, target)
        ).fetchone() is not None

    def delete_edge(self, source: str, target: str) -> None:
        self._conn.execute(
            "DELETE FROM edges WHERE source = ? AND target = ?", (source, target)
        )

    def iter_edges(self) -> Iterator[dict]:
        for source, target, attrs_json in self._conn.execute(
            "SELECT source, target, attrs FROM edges"
        ):
            data = json.loads(attrs_json)
            data["source"] = source
            data["target"] = target
            yield data

    def out_edges(self, source: str) -> list[tuple[str, dict]]:
        """Return [(target, attrs)] for all outgoing edges."""
        result = []
        for target, attrs_json in self._conn.execute(
            "SELECT target, attrs FROM edges WHERE source = ?", (source,)
        ):
            data = json.loads(attrs_json)
            result.append((target, data))
        return result

    def in_edges(self, target: str) -> list[tuple[str, dict]]:
        """Return [(source, attrs)] for all incoming edges."""
        result = []
        for source, attrs_json in self._conn.execute(
            "SELECT source, attrs FROM edges WHERE target = ?", (target,)
        ):
            data = json.loads(attrs_json)
            result.append((source, data))
        return result

    def count_edges(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]

    # ------------------------------------------------------------------
    # Term keyword index (replaces flashtext KeywordProcessor persistence)
    # ------------------------------------------------------------------
    def upsert_term_keyword(self, keyword: str, term_id: str, normalized: bool = False) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO term_keywords (keyword, term_id, normalized) VALUES (?, ?, ?)",
            (keyword.lower(), term_id, 1 if normalized else 0),
        )

    def delete_term_keywords(self, term_id: str) -> None:
        self._conn.execute(
            "DELETE FROM term_keywords WHERE term_id = ?", (term_id,)
        )

    def lookup_term_keywords(self, text: str) -> list[str]:
        """Return term_ids whose keyword appears in *text* (case-insensitive)."""
        # Exact match
        rows = self._conn.execute(
            "SELECT DISTINCT term_id FROM term_keywords WHERE keyword = ? AND normalized = 0",
            (text.lower(),),
        ).fetchall()
        if rows:
            return [r[0] for r in rows]
        # Normalised (diacritic-stripped) fallback handled by caller
        return []

    def iter_term_keywords(self, term_id: str) -> list[tuple[str, bool]]:
        """Return [(keyword, normalized)] for a term node."""
        rows = self._conn.execute(
            "SELECT keyword, normalized FROM term_keywords WHERE term_id = ?",
            (term_id,),
        ).fetchall()
        return [(r[0], bool(r[1])) for r in rows]

    def rebuild_term_keywords_from_nodes(self) -> None:
        """Rebuild the term_keywords table from term-type nodes."""
        self._conn.execute("DELETE FROM term_keywords")
        for node in self.iter_nodes_by_type("term"):
            term_id = node["id"]
            term = node.get("term", "")
            if term:
                self.upsert_term_keyword(term, term_id)
            display = node.get("display_form", "")
            if display and display.lower() != term.lower():
                self.upsert_term_keyword(display, term_id)
            for variant in node.get("variants", []):
                if variant:
                    self.upsert_term_keyword(variant, term_id)
            for strat_val in (node.get("gender_strategies") or {}).values():
                if strat_val:
                    self.upsert_term_keyword(strat_val, term_id)

    # ------------------------------------------------------------------
    # Bulk / migration
    # ------------------------------------------------------------------
    def bulk_load(
        self,
        nodes: list[dict],
        edges: list[dict],
    ) -> None:
        """Replace the entire database with *nodes* and *edges*.

        Used by the one-time JSON → SQLite migration.
        """
        self._conn.execute("BEGIN")
        try:
            self._conn.execute("DELETE FROM nodes")
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM term_keywords")
            for node in nodes:
                node_id = node.get("id", "")
                node_type = node.get("type", "")
                clean = {k: v for k, v in node.items() if k not in ("id", "type")}
                self._conn.execute(
                    "INSERT OR REPLACE INTO nodes (id, type, attrs) VALUES (?, ?, ?)",
                    (node_id, node_type, json.dumps(clean, ensure_ascii=False, default=str)),
                )
            for edge in edges:
                source = edge.get("source", "")
                target = edge.get("target", "")
                clean = {k: v for k, v in edge.items() if k not in ("source", "target")}
                self._conn.execute(
                    "INSERT OR REPLACE INTO edges (source, target, attrs) VALUES (?, ?, ?)",
                    (source, target, json.dumps(clean, ensure_ascii=False, default=str)),
                )
            self.rebuild_term_keywords_from_nodes()
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # JSON → SQLite migration
    # ------------------------------------------------------------------
    @staticmethod
    def migrate_from_json(json_path: Path | str, sqlite_path: Path | str) -> None:
        """Read the old JSON KG and write it into a SQLite database.

        The JSON file is renamed to ``<path>.legacy`` afterwards.
        """
        json_path = Path(json_path)
        sqlite_path = Path(sqlite_path)
        log.info("Migrating KG from JSON (%s) to SQLite (%s)", json_path, sqlite_path)
        raw = json.loads(json_path.read_text(encoding="utf-8"))
        nodes = raw.get("nodes", [])
        edges = raw.get("edges", [])

        # Write to a temp file first, then rename — atomic migration.
        fd, tmp = tempfile.mkstemp(
            dir=str(sqlite_path.parent), suffix=".sqlite.tmp"
        )
        os.close(fd)
        store = KGStore(tmp)
        try:
            store.bulk_load(nodes, edges)
            store.close()
        except Exception:
            store.close()
            os.unlink(tmp)
            raise

        # Backup the old JSON, move the SQLite into place.
        legacy = json_path.with_suffix(json_path.suffix + ".legacy")
        os.replace(str(json_path), str(legacy))
        os.replace(tmp, str(sqlite_path))
        log.info(
            "Migration complete: %d nodes, %d edges.  Old JSON backed up at %s",
            len(nodes), len(edges), legacy,
        )