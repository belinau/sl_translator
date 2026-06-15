#!/usr/bin/env python3
"""
test_curation_editor_factories.py — Tests for the KG curation editor
factory extensions and role parity.

Verifies:
- update_concept_metadata bilingual sentinel params
- update_source_text_node translation_edition param
- AGENT_ROLES parity with validate_kg.ROLES
- _needs_target / _has_translation helpers
"""

from __future__ import annotations

import os
import tempfile
import unittest

# Stub out heavy NLP model loading
import translate_core.knowledge_graph as _kg_mod

_kg_mod.HAS_SPACY = False
_kg_mod.HAS_CLASSLA = False
_kg_mod.HAS_STANZA = False

from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.kg_review_ops import AGENT_ROLES, RECLASS_AGENT_ROLES


def _fresh_kg(tmp_path):
    """Return a KnowledgeGraph backed by a brand-new temp database."""
    return KnowledgeGraph(db_path=tmp_path)


# ===================================================================
# Test suite
# ===================================================================


class TestUpdateConceptMetadata(unittest.TestCase):
    """Bilingual sentinel params on update_concept_metadata."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.kg = _fresh_kg(os.path.join(self.tmpdir, "test.db"))
        self.cid = self.kg.add_concept_node("concept:test_concept", label="Test", domain="general")

    def tearDown(self):
        self.kg.save()

    def test_set_label_translation(self):
        """Setting label_translation updates the node, leaves label/id unchanged."""
        ok = self.kg.update_concept_metadata(
            self.cid, label_translation="preizkus"
        )
        self.assertTrue(ok)
        node = self.kg.G.nodes[self.cid]
        self.assertEqual(node["label_translation"], "preizkus")
        self.assertEqual(node["label"], "Test")  # unchanged
        self.assertEqual(node["id"], self.cid)  # id is frozen

    def test_sentinel_omitted_leaves_existing(self):
        """Omitting a sentinel param does not clobber existing values."""
        self.kg.update_concept_metadata(
            self.cid, label_translation="preizkus", orig_lang="en", translation_lang="sl"
        )
        # Now update only domain — bilingual fields must survive
        self.kg.update_concept_metadata(self.cid, domain="philosophy")
        node = self.kg.G.nodes[self.cid]
        self.assertEqual(node["label_translation"], "preizkus")
        self.assertEqual(node["orig_lang"], "en")
        self.assertEqual(node["translation_lang"], "sl")
        self.assertEqual(node["domain"], "philosophy")

    def test_sentinel_explicit_empty_clears(self):
        """Passing empty string via sentinel clears the field."""
        self.kg.update_concept_metadata(
            self.cid, label_translation="preizkus"
        )
        self.assertEqual(self.kg.G.nodes[self.cid]["label_translation"], "preizkus")
        # Clear it
        self.kg.update_concept_metadata(self.cid, label_translation="")
        self.assertEqual(self.kg.G.nodes[self.cid]["label_translation"], "")

    def test_nonexistent_concept_returns_false(self):
        """Updating a missing concept returns False."""
        ok = self.kg.update_concept_metadata("concept:nonexistent", label_translation="x")
        self.assertFalse(ok)


class TestUpdateSourceTextNode(unittest.TestCase):
    """translation_edition param on update_source_text_node."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.kg = _fresh_kg(os.path.join(self.tmpdir, "test.db"))
        self.sid = self.kg.add_source_text_node("source:test_book", "Test Book")

    def tearDown(self):
        self.kg.save()

    def test_set_translation_edition(self):
        """Setting translation_edition stores the dict."""
        te = {"publisher": "Mladinska", "city": "Ljubljana", "year": 2020, "language": "sl"}
        ok = self.kg.update_source_text_node(
            self.sid, translation_edition=te
        )
        self.assertTrue(ok)
        node = self.kg.G.nodes[self.sid]
        self.assertEqual(node["translation_edition"], te)

    def test_translation_edition_round_trip(self):
        """translation_edition survives save/load."""
        te = {"publisher": "Mladinska", "year": 2020}
        self.kg.update_source_text_node(self.sid, translation_edition=te)
        self.kg.save()
        kg2 = KnowledgeGraph(db_path=self.kg.db_path)
        node = kg2.G.nodes[self.sid]
        self.assertEqual(node["translation_edition"], te)

    def test_translation_edition_omitted_leaves_existing(self):
        """Omitting translation_edition doesn't clobber existing data."""
        te = {"publisher": "Mladinska"}
        self.kg.update_source_text_node(self.sid, translation_edition=te)
        # Update title only
        self.kg.update_source_text_node(self.sid, title="New Title")
        node = self.kg.G.nodes[self.sid]
        self.assertEqual(node["translation_edition"], te)
        self.assertEqual(node["title"], "New Title")

    def test_no_slovenian_edition_written(self):
        """When translation_edition is set, slovenian_edition is NOT written."""
        te = {"publisher": "Mladinska", "language": "sl"}
        self.kg.update_source_text_node(self.sid, translation_edition=te)
        node = self.kg.G.nodes[self.sid]
        self.assertIn("translation_edition", node)
        # slovenian_edition should not be present unless explicitly set
        self.assertNotIn("slovenian_edition", node)

    def test_bilingual_title_fields_via_sentinel(self):
        """Bilingual title fields work through the sentinel convention."""
        self.kg.update_source_text_node(
            self.sid,
            title_orig="Izvirni naslov",
            title_translation="Original Title",
            orig_lang="sl",
            translation_lang="en",
        )
        node = self.kg.G.nodes[self.sid]
        self.assertEqual(node["title_orig"], "Izvirni naslov")
        self.assertEqual(node["title_translation"], "Original Title")
        self.assertEqual(node["orig_lang"], "sl")
        self.assertEqual(node["translation_lang"], "en")


class TestRoleParity(unittest.TestCase):
    """AGENT_ROLES in kg_review_ops matches validate_kg.ROLES."""

    def test_agent_roles_matches_validate_kg(self):
        """AGENT_ROLES is the canonical 14-role set matching validate_kg.ROLES."""
        from scripts.validate_kg import ROLES
        self.assertEqual(set(AGENT_ROLES), ROLES)
        self.assertEqual(len(AGENT_ROLES), 14)

    def test_organization_not_in_agent_roles(self):
        """'organization' is an institution kind, not an agent role."""
        self.assertNotIn("organization", AGENT_ROLES)

    def test_reclass_is_alias(self):
        """RECLASS_AGENT_ROLES is the same list object as AGENT_ROLES."""
        self.assertIs(RECLASS_AGENT_ROLES, AGENT_ROLES)


class TestHelpers(unittest.TestCase):
    """_needs_target and _has_translation predicate tests."""

    def test_needs_target_true(self):
        """Concept with translation_lang set but empty label_translation needs target."""
        from ui.kg_editor.concepts import _needs_target
        c = {"type": "concept", "translation_lang": "sl", "label_translation": None}
        self.assertTrue(_needs_target(c))

    def test_needs_target_false_filled(self):
        """Concept with both translation_lang and label_translation does not need target."""
        from ui.kg_editor.concepts import _needs_target
        c = {"type": "concept", "translation_lang": "sl", "label_translation": "pojem"}
        self.assertFalse(_needs_target(c))

    def test_needs_target_false_no_lang(self):
        """Legacy concept without translation_lang is excluded."""
        from ui.kg_editor.concepts import _needs_target
        c = {"type": "concept", "label": "some concept"}
        self.assertFalse(_needs_target(c))

    def test_needs_target_false_blank_translation_lang(self):
        """Concept with empty string translation_lang is excluded."""
        from ui.kg_editor.concepts import _needs_target
        c = {"type": "concept", "translation_lang": "", "label_translation": ""}
        self.assertFalse(_needs_target(c))

    def test_has_translation_with_edition(self):
        """Source with translation_edition has translation."""
        from ui.kg_editor.sources import _has_translation
        s = {"translation_edition": {"publisher": "Mladinska"}}
        self.assertTrue(_has_translation(s, has_translator=False))

    def test_has_translation_with_title(self):
        """Source with title_translation has translation."""
        from ui.kg_editor.sources import _has_translation
        s = {"title_translation": "Prevedeni naslov"}
        self.assertTrue(_has_translation(s, has_translator=False))

    def test_has_translation_with_edge(self):
        """Source with a translated_by edge has translation."""
        from ui.kg_editor.sources import _has_translation
        s = {}
        self.assertTrue(_has_translation(s, has_translator=True))

    def test_has_translation_false(self):
        """Source with none of the signals does not have translation."""
        from ui.kg_editor.sources import _has_translation
        s = {}
        self.assertFalse(_has_translation(s, has_translator=False))


if __name__ == "__main__":
    unittest.main()