# tests/test_kg_editor_browser.py
#
# Tests for the unified KG browser page and update_institution_node.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.knowledge_graph import KnowledgeGraph


class TestUpdateInstitutionNode:
    def test_update_name(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        kg.add_institution_node("maska", "Maska", kind="publisher")
        assert kg.update_institution_node("institution:maska", name="Maska Publishers")
        node = kg.G.nodes["institution:maska"]
        assert node["name"] == "Maska Publishers"
        assert node["kind"] == "publisher"

    def test_update_kind(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        kg.add_institution_node("tate", "Tate Modern", kind="museum")
        assert kg.update_institution_node("institution:tate", kind="gallery")
        assert kg.G.nodes["institution:tate"]["kind"] == "gallery"

    def test_update_city(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        kg.add_institution_node("routledge", "Routledge")
        assert kg.update_institution_node("institution:routledge", city="London")
        assert kg.G.nodes["institution:routledge"]["city"] == "London"

    def test_nonexistent_returns_false(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        assert not kg.update_institution_node("institution:nonexistent", name="X")

    def test_update_all_fields_at_once(self, tmp_path):
        kg = KnowledgeGraph(db_path=tmp_path / "test.db")
        kg.add_institution_node("maska", "Maska", kind="publisher")
        assert kg.update_institution_node(
            "institution:maska", name="Maska Založba", kind="publisher", city="Maribor"
        )
        node = kg.G.nodes["institution:maska"]
        assert node["name"] == "Maska Založba"
        assert node["kind"] == "publisher"
        assert node["city"] == "Maribor"

    def test_persist_survives_reload(self, tmp_path):
        db = tmp_path / "test.db"
        kg = KnowledgeGraph(db_path=db)
        kg.add_institution_node("maska", "Maska", kind="publisher")
        kg.update_institution_node("institution:maska", city="Maribor")
        del kg
        kg2 = KnowledgeGraph(db_path=db)
        assert kg2.G.nodes["institution:maska"]["city"] == "Maribor"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])