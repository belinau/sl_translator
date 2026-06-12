"""Tests for _ensure_project_container and _slugify_project (Step 7)."""
import pytest
from unittest.mock import MagicMock


def test_slugify_project():
    from ui.workspace import _slugify_project

    state = MagicMock()
    state.filename = "My Book (2nd ed).pdf"
    state.project_id = "abc123"
    assert _slugify_project(state) == "my-book-2nd-ed-pdf"

    # Fallback to project_id when filename is empty
    state.filename = ""
    state.project_id = "xyz789"
    assert _slugify_project(state) == "xyz789"

    # Both empty → "project"
    state.filename = ""
    state.project_id = ""
    assert _slugify_project(state) == "project"


def test_ensure_project_container_creates_node_and_edge():
    from ui.workspace import _ensure_project_container
    from translate_core.knowledge_graph import KnowledgeGraph
    import tempfile, pathlib

    tmp = tempfile.mkdtemp()
    kg = KnowledgeGraph(db_path=pathlib.Path(tmp) / "test.json")

    _ensure_project_container(kg, "my-book", "My Book", "book_translation")

    # Source node exists with project_type
    node = "source:my-book"
    assert kg.G.has_node(node)
    attrs = kg.G.nodes[node]
    assert attrs.get("project_type") == "book_translation"
    assert attrs.get("title") == "My Book"

    # Agent exists
    agent_node = "agent:urban-belina"
    assert kg.G.has_node(agent_node)

    # translated_by edge exists
    assert kg.G.has_edge(node, agent_node)
    assert kg.G.edges[node, agent_node]["relation"] == "translated_by"


def test_ensure_project_container_idempotent():
    from ui.workspace import _ensure_project_container
    from translate_core.knowledge_graph import KnowledgeGraph
    import tempfile, pathlib

    tmp = tempfile.mkdtemp()
    kg = KnowledgeGraph(db_path=pathlib.Path(tmp) / "test.json")

    _ensure_project_container(kg, "my-book", "My Book", "book_translation")
    _ensure_project_container(kg, "my-book", "My Book", "book_translation")

    # Only one node, one edge — no duplicates
    node = "source:my-book"
    in_edges = list(kg.G.in_edges(node, data=True))
    out_edges = list(kg.G.out_edges(node, data=True))
    translated_by_edges = [e for e in out_edges if e[2].get("relation") == "translated_by"]
    assert len(translated_by_edges) == 1


def test_ensure_project_container_different_project_types():
    from ui.workspace import _ensure_project_container
    from translate_core.knowledge_graph import KnowledgeGraph
    import tempfile, pathlib

    tmp = tempfile.mkdtemp()
    kg = KnowledgeGraph(db_path=pathlib.Path(tmp) / "test.json")

    _ensure_project_container(kg, "article-1", "Article One", "article_translation")
    _ensure_project_container(kg, "book-1", "Book One", "book_translation")

    assert kg.G.nodes["source:article-1"]["project_type"] == "article_translation"
    assert kg.G.nodes["source:book-1"]["project_type"] == "book_translation"