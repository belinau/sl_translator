"""Shared ingestion helpers for footnote and bibliography CLI scripts.

Provides ``ensure_agent``, ``ensure_institution``, and ``cited_work_id`` —
functions that both ``ingest_book_footnotes.py`` and ``ingest_book_bibliography.py``
use identically.
"""

from __future__ import annotations

from typing import Optional

from ..entity_extraction._slug import _slugify
from ..knowledge_graph import KnowledgeGraph


def ensure_agent(kg: KnowledgeGraph, full_name: str, role: str = "author") -> str:
    """Return the agent slug, creating the node if absent."""
    aid = _slugify(full_name)
    node_key = f"agent:{aid.lower()}"
    if not kg.G.has_node(node_key):
        kg.add_agent_node(aid, name=full_name, role=role)
    return aid


def ensure_institution(
    kg: KnowledgeGraph,
    name: str,
    city: Optional[str],
    kind: str = "publisher",
) -> str:
    """Return the institution slug, creating the node if absent."""
    iid = _slugify(name)
    node_key = f"institution:{iid.lower()}"
    if not kg.G.has_node(node_key):
        kg.add_institution_node(iid, name=name, kind=kind, city=city)
    return iid


def cited_work_id(surname: str, title_part: str, year: str | None) -> str:
    """Stable id for a cited work (author-title-year slug)."""
    return _slugify(f"{surname}-{title_part}-{year or ''}")