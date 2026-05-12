#!/usr/bin/env python3
"""
test_kg_integrity.py — Knowledge-graph integrity tests.

Catches regressions in the KG save/load cycle (e.g. the bug where the
edge property "source" (provenance) collided with the serialization key
"source" (from-node ID)) and other data-quality issues.

Run standalone:
    python test_kg_integrity.py
Or with unittest discovery:
    python -m unittest test_kg_integrity -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

# ---------------------------------------------------------------------------
# Stub out heavy NLP model loading before importing KnowledgeGraph.
# The KG loads spacy / classla / stanza inside __init__; patching the
# module-level flags to False avoids the multi-second import cost while
# still letting us exercise save/load logic.
# ---------------------------------------------------------------------------
import translate_core.knowledge_graph as _kg_mod

_kg_mod.HAS_SPACY = False
_kg_mod.HAS_CLASSLA = False
_kg_mod.HAS_STANZA = False

from translate_core.knowledge_graph import KnowledgeGraph

# ===================================================================
# Helpers
# ===================================================================


def _fresh_kg(tmp_path):
    """Return a KnowledgeGraph backed by a brand-new temp database."""
    kg = KnowledgeGraph(db_path=tmp_path)
    return kg


# ===================================================================
# Test suite
# ===================================================================


class TestKGIntegrity(unittest.TestCase):
    """Regression tests for the KG serialisation round-trip."""

    # ------------------------------------------------------------------
    # Per-test setup / teardown
    # ------------------------------------------------------------------

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False, prefix="kg_test_"
        )
        self._tmpfile.close()  # close so KG can open it for reading
        self.db_path = self._tmpfile.name

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    # ------------------------------------------------------------------
    # 1. Save / load round-trip integrity  (the critical regression test)
    # ------------------------------------------------------------------

    def test_save_load_roundtrip(self):
        """Nodes, edges, and all attributes survive a save→load cycle."""
        kg = _fresh_kg(self.db_path)

        # Build a small sub-graph
        cid = kg.add_concept_node("concept:test", label="test concept")
        en_id = kg.add_term_node("test", "en", concept_id=cid)
        sl_id = kg.add_term_node("testna", "sl")

        kg.link_translations(en_id, sl_id, confidence=0.92, provenance="auto")

        kg.save()

        # --- Reload into a fresh KG instance ---
        kg2 = _fresh_kg(self.db_path)

        # Nodes survive
        self.assertIn(cid, kg2.G.nodes)
        self.assertIn(en_id, kg2.G.nodes)
        self.assertIn(sl_id, kg2.G.nodes)

        # Forward edge EN→SL
        self.assertTrue(kg2.G.has_edge(en_id, sl_id))
        fwd = kg2.G.edges[en_id, sl_id]
        self.assertEqual(fwd["relation"], "translates_to")
        self.assertEqual(fwd["provenance"], "auto")
        self.assertAlmostEqual(fwd["confidence"], 0.92)

        # Reverse edge SL→EN
        self.assertTrue(kg2.G.has_edge(sl_id, en_id))
        rev = kg2.G.edges[sl_id, en_id]
        self.assertEqual(rev["relation"], "translates_to")
        self.assertEqual(rev["provenance"], "auto")
        self.assertAlmostEqual(rev["confidence"], 0.92)

        # extract_entities picks up the term + its translation
        entities = kg2.extract_entities("test", target_lang="sl")
        terms = [e["term"] for e in entities]
        self.assertIn("test", terms)
        if entities:
            self.assertTrue(len(entities[0]["translations"]) >= 1)
            self.assertEqual(entities[0]["translations"][0]["term"], "testna")

    # ------------------------------------------------------------------
    # 2. Edge provenance doesn't collide with node source
    # ------------------------------------------------------------------

    def test_edge_provenance_no_collision(self):
        """In the raw JSON, edge "source" is a valid node ID, and
        "provenance" is a separate human-readable string."""
        kg = _fresh_kg(self.db_path)

        en_id = kg.add_term_node("cat", "en")
        sl_id = kg.add_term_node("mačka", "sl")
        kg.link_translations(en_id, sl_id, provenance="auto")
        kg.save()

        raw = json.loads(open(self.db_path, encoding="utf-8").read())

        valid_prefixes = ("term:", "concept:", "coll:", "segment:", "domain:")

        for edge in raw["edges"]:
            # "source" must be a real node ID
            self.assertTrue(
                edge["source"].startswith(valid_prefixes),
                f'edge source "{edge["source"]}" does not look like a node ID',
            )
            # "target" must also be a real node ID
            self.assertTrue(
                edge["target"].startswith(valid_prefixes),
                f'edge target "{edge["target"]}" does not look like a node ID',
            )
            # provenance must exist and be a recognised value
            self.assertIn(
                edge.get("provenance"),
                ("auto", "manual"),
                "edge provenance missing or unexpected",
            )

    # ------------------------------------------------------------------
    # 3. Bidirectional translation links
    # ------------------------------------------------------------------

    def test_bidirectional_links(self):
        """link_translations creates symmetric edges with equal confidence."""
        kg = _fresh_kg(self.db_path)

        en_id = kg.add_term_node("dog", "en")
        sl_id = kg.add_term_node("pes", "sl")
        kg.link_translations(en_id, sl_id, confidence=0.85, provenance="manual")

        # Both directions exist
        self.assertTrue(kg.G.has_edge(en_id, sl_id))
        self.assertTrue(kg.G.has_edge(sl_id, en_id))

        # Same confidence
        fwd_conf = kg.G.edges[en_id, sl_id]["confidence"]
        rev_conf = kg.G.edges[sl_id, en_id]["confidence"]
        self.assertAlmostEqual(fwd_conf, rev_conf)
        self.assertAlmostEqual(fwd_conf, 0.85)

    # ------------------------------------------------------------------
    # 4. promote_pair survives save / reload
    # ------------------------------------------------------------------

    def test_promote_pair_roundtrip(self):
        """A manually promoted pair is intact after save→load."""
        kg = _fresh_kg(self.db_path)

        kg.promote_pair("test", "testna", "en", "sl")
        kg.save()

        kg2 = _fresh_kg(self.db_path)

        en_id = "term:en:test"
        sl_id = "term:sl:testna"

        self.assertTrue(kg2.G.has_node(en_id))
        self.assertTrue(kg2.G.has_node(sl_id))
        self.assertTrue(kg2.G.has_edge(en_id, sl_id))

        edge = kg2.G.edges[en_id, sl_id]
        self.assertTrue(edge.get("verified"), "promoted pair should be verified")
        self.assertEqual(edge.get("provenance"), "manual")

    # ------------------------------------------------------------------
    # 5. display_form and variants are preserved
    # ------------------------------------------------------------------

    def test_display_form_preserved(self):
        """SL term display_form and variants survive save→load."""
        kg = _fresh_kg(self.db_path)

        sl_id = kg.add_term_node(
            "lep",
            "sl",
            display_form="lepi",
        )

        # Verify the node was stored with display_form
        node = kg.G.nodes[sl_id]
        self.assertEqual(node.get("display_form"), "lepi")
        self.assertIn("lepi", node.get("variants", []))

        kg.save()

        kg2 = _fresh_kg(self.db_path)

        self.assertTrue(kg2.G.has_node(sl_id))
        node2 = kg2.G.nodes[sl_id]
        self.assertEqual(node2.get("display_form"), "lepi")
        self.assertIn("lepi", node2.get("variants", []))


# ===================================================================
if __name__ == "__main__":
    unittest.main()
