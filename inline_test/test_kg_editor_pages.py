"""Tests for the KG editor page logic (pure functions from kg_review_ops)."""
from __future__ import annotations

import json

from translate_core.kg_review_ops import (
    record_matches_text,
    record_label,
    review_slugify,
    commit_record,
    candidate_texts,
    drop_from_queue,
    load_kg_review,
    drop_kg_review,
)


# ---------------------------------------------------------------------------
# record_matches_text
# ---------------------------------------------------------------------------

class TestRecordMatchesText:
    def test_match_on_name(self):
        r = {"kind": "agent_person", "payload": {"name": "Donna Haraway"}}
        assert record_matches_text(r, "haraway")

    def test_match_on_title_orig(self):
        r = {"kind": "cited_work", "payload": {"title_orig": "A Cyborg Manifesto"}}
        assert record_matches_text(r, "cyborg")

    def test_no_match(self):
        r = {"kind": "agent_person", "payload": {"name": "Donna Haraway"}}
        assert not record_matches_text(r, "butler")


# ---------------------------------------------------------------------------
# record_label
# ---------------------------------------------------------------------------

class TestRecordLabel:
    def test_cited_work_label(self):
        r = {"kind": "cited_work", "payload": {"author": "Haraway", "title_orig": "Cyborg", "year": "1991"}}
        label = record_label(r)
        assert "Haraway" in label
        assert "Cyborg" in label

    def test_agent_person_label(self):
        r = {"kind": "agent_person", "payload": {"name": "Judith Butler", "mention_count": 5, "dedup_group": "butler"}}
        label = record_label(r)
        assert "Judith Butler" in label

    def test_institution_label(self):
        r = {"kind": "institution", "payload": {"name": "MIT Press", "kind": "publisher", "city": "Cambridge"}}
        label = record_label(r)
        assert "MIT Press" in label


# ---------------------------------------------------------------------------
# review_slugify
# ---------------------------------------------------------------------------

class TestReviewSlugify:
    def test_basic(self):
        assert review_slugify("Donna Haraway") == "donna-haraway"

    def test_diacritics(self):
        assert review_slugify("José Roca") == "jose-roca"

    def test_empty_fallback(self):
        assert review_slugify("") == "unknown"


# ---------------------------------------------------------------------------
# candidate_texts
# ---------------------------------------------------------------------------

class TestCandidateTexts:
    def test_agent_candidate(self):
        r = {"kind": "agent_person", "payload": {"name": "Haraway"}}
        c = candidate_texts(r)
        assert c["name"] == "Haraway"
        assert c["primary"] == "Haraway"

    def test_work_candidate(self):
        r = {"kind": "cited_work", "payload": {"title_orig": "Cyborg", "author": "Haraway", "year": "1991"}}
        c = candidate_texts(r)
        assert c["title_orig"] == "Cyborg"
        assert c["author"] == "Haraway"


# ---------------------------------------------------------------------------
# drop_from_queue / drop_kg_review (file I/O)
# ---------------------------------------------------------------------------

class TestQueueIO:
    def test_drop_from_queue(self, tmp_path):
        records = [
            {"id": "r1", "kind": "agent_person", "payload": {"name": "A"}},
            {"id": "r2", "kind": "agent_person", "payload": {"name": "B"}},
        ]
        queue_path = tmp_path / "review.json"
        queue_path.write_text(json.dumps(records), encoding="utf-8")

        # Override the module-level path
        import translate_core.kg_review_ops as ops
        orig = ops.REVIEW_PATH
        ops.REVIEW_PATH = queue_path
        try:
            drop_from_queue(records, records[0])
            remaining = json.loads(queue_path.read_text(encoding="utf-8"))
            assert len(remaining) == 1
            assert remaining[0]["id"] == "r2"
        finally:
            ops.REVIEW_PATH = orig

    def test_drop_kg_review(self, tmp_path):
        items = [
            {"id": "node:1", "type": "agent", "name": "A"},
            {"id": "node:2", "type": "agent", "name": "B"},
        ]
        review_path = tmp_path / "kg_review.json"
        review_path.write_text(json.dumps(items), encoding="utf-8")
        dismissed_path = tmp_path / "kg_review_dismissed.json"

        import translate_core.kg_review_ops as ops
        orig_review = ops.KG_REVIEW_PATH
        orig_dismissed = ops.KG_DISMISSED_PATH
        ops.KG_REVIEW_PATH = review_path
        ops.KG_DISMISSED_PATH = dismissed_path
        try:
            drop_kg_review(items, items[0], dismiss=True)
            remaining = json.loads(review_path.read_text(encoding="utf-8"))
            assert len(remaining) == 1
            assert remaining[0]["id"] == "node:2"
            # Dismissed set should contain node:1
            dismissed = json.loads(dismissed_path.read_text(encoding="utf-8"))
            assert "node:1" in dismissed
        finally:
            ops.KG_REVIEW_PATH = orig_review
            ops.KG_DISMISSED_PATH = orig_dismissed

    def test_load_kg_review_missing_file(self, tmp_path):
        import translate_core.kg_review_ops as ops
        orig = ops.KG_REVIEW_PATH
        ops.KG_REVIEW_PATH = tmp_path / "nonexistent.json"
        try:
            result = load_kg_review()
            assert result == []
        finally:
            ops.KG_REVIEW_PATH = orig


# ---------------------------------------------------------------------------
# commit_record (with a mock KG)
# ---------------------------------------------------------------------------

class TestCommitRecord:
    def test_commit_agent_person(self):
        from translate_core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()

        r = {
            "kind": "agent_person",
            "payload": {
                "name": "Donna Haraway",
                "role": "author",
                "dedup_group": "haraway",
                "alt_spellings": ["D. Haraway"],
                "all_roles": ["author"],
                "mention_count": 3,
            },
            "confidence": 0.9,
        }
        commit_record(kg, r)
        # Should have created an agent node
        assert any("haraway" in n for n in kg.G.nodes)

    def test_commit_cited_work(self):
        from translate_core.knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph()

        r = {
            "kind": "cited_work",
            "payload": {
                "cited_id": "cyborg-manifesto",
                "title_orig": "A Cyborg Manifesto",
                "author": "Haraway",
                "year": "1991",
            },
            "confidence": 0.8,
        }
        commit_record(kg, r)
        # Should have created source + agent nodes
        assert any("cyborg" in n for n in kg.G.nodes)