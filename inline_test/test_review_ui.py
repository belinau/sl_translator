"""Integration tests for the review feature.

Tests pure-logic lifecycle (clone → review → merge → reopen) and
verifies that the review UI modules import cleanly (catching
SyntaxError, ImportError, and the class of bugs that shipped before
in the kg_editor modules).

Full page-rendering tests would require the NiceGUI user fixture to
start from main.py, but the monkeypatch on app_state only affects the
test process — see inline_test/test_kg_editor_smoke.py for the same
limitation.
"""
from __future__ import annotations


import pytest

from translate_core import review_manager as rm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_reviews_dir(monkeypatch, tmp_path):
    """Redirect REVIEWS_DIR to temp so tests don't pollute data/."""
    rev_dir = tmp_path / "reviews"
    rev_dir.mkdir()
    monkeypatch.setattr(rm, "REVIEWS_DIR", rev_dir)
    monkeypatch.setattr(rm, "_clone_path", lambda rid: rev_dir / f"{rid}.json")
    return rev_dir


@pytest.fixture
def sample_project():
    return {
        "project_id": "aabbccdd1234",
        "id": "aabbccdd1234",
        "filename": "test_book.pdf",
        "lang_pair": "en->sl",
        "pipeline": "academic",
        "project_type": "book_translation",
        "house_style": "maska",
        "segments": [
            {"id": 0, "source": "Hello world.", "target": "Pozdrav svet.", "status": "done"},
            {"id": 1, "source": "How are you?", "target": "Kako si?", "status": "done"},
            {"id": 2, "source": "Goodbye.", "target": "Adijo.", "status": "pending"},
        ],
        "segments_meta": [
            {"type": "body_text", "index": 0},
            {"type": "body_text", "index": 1},
            {"type": "body_text", "index": 2},
        ],
    }


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_manager_clone_and_merge_roundtrip(fresh_reviews_dir, sample_project):
    """Full lifecycle: create clone → apply reviewer changes → merge back."""
    # 1. Create clone with all done segments
    clone = rm.create_review_clone(sample_project)
    assert len(clone["segments"]) == 2  # only done segments
    rm.save_review(clone)

    # 2. Verify it loads
    loaded = rm.load_review(clone["review_id"])
    assert loaded is not None
    assert len(loaded["segments"]) == 2

    # 3. Apply reviewer suggestions
    rm.apply_reviewer_changes(
        clone,
        {
            0: {"reviewer_target": "Pozdravljen svet.", "reviewer_comment": "More formal"},
            1: {"reviewer_target": "Kako ste?", "reviewer_comment": "Formal address"},
        },
    )
    assert clone["segments"][0]["reviewer_status"] == "suggested"

    # 4. Merge — accept only segment 0 (original_id=0)
    rm.merge_review_into_original(sample_project, clone, accepted_original_ids={0})
    assert sample_project["segments"][0]["target"] == "Pozdravljen svet."
    assert sample_project["segments"][0]["review_comment"] == "More formal"
    # Segment 1 should be unchanged
    assert sample_project["segments"][1]["target"] == "Kako si?"

    # 5. Delete review
    rm.delete_review(clone["review_id"])
    assert rm.load_review(clone["review_id"]) is None


@pytest.mark.asyncio
async def test_clone_preserves_segment_order(fresh_reviews_dir, sample_project):
    """Clone segments should preserve the original ordering."""
    clone = rm.create_review_clone(sample_project, segment_ids=[2, 0])
    assert [s["original_id"] for s in clone["segments"]] == [0, 2]


@pytest.mark.asyncio
async def test_reopen_resets_only_rejected(fresh_reviews_dir, sample_project):
    """After reopen, only non-approved segments reset."""
    clone = rm.create_review_clone(sample_project)
    rm.apply_reviewer_changes(
        clone,
        {
            0: {"reviewer_target": "A"},
            1: {"reviewer_target": "B"},
        },
    )
    clone["segments"][0]["reviewer_status"] = "approved"
    rm.reset_for_reopen(clone)
    assert clone["segments"][0]["reviewer_target"] == "A"
    assert clone["segments"][1]["reviewer_target"] == ""
    assert clone["segments"][1]["reviewer_status"] == "pending"
    assert clone["round_trip_count"] == 1


@pytest.mark.asyncio
async def test_multi_round_merge(fresh_reviews_dir, sample_project):
    """Two-round review: merge round 1, reopen, merge round 2."""
    # Round 1
    clone = rm.create_review_clone(sample_project)
    rm.apply_reviewer_changes(clone, {0: {"reviewer_target": "V1"}})
    rm.merge_review_into_original(sample_project, clone, {0})
    assert sample_project["segments"][0]["target"] == "V1"

    # Reopen
    rm.reset_for_reopen(clone)
    assert clone["round_trip_count"] == 1
    assert clone["segments"][0]["reviewer_target"] == "V1"  # approved, kept

    # Round 2: reviewer suggests a new version for segment 1
    rm.apply_reviewer_changes(clone, {1: {"reviewer_target": "V2"}})
    rm.merge_review_into_original(sample_project, clone, {1})
    assert sample_project["segments"][1]["target"] == "V2"


@pytest.mark.asyncio
async def test_prolong_before_expiry(fresh_reviews_dir, sample_project):
    """Prolong extends the deadline from the current expiry."""
    from datetime import datetime, timedelta

    clone = rm.create_review_clone(sample_project, validity_days=3)
    base = datetime.now() + timedelta(days=3)
    clone["funnel_expires_at"] = base.isoformat(timespec="seconds")
    rm.prolong_funnel(clone, extra_days=2)
    new = datetime.fromisoformat(clone["funnel_expires_at"])
    expected = base + timedelta(days=2)
    assert abs((new - expected).total_seconds()) < 10


@pytest.mark.asyncio
async def test_prolong_after_expiry(fresh_reviews_dir, sample_project):
    """Prolong from now if the funnel already expired."""
    from datetime import datetime, timedelta

    clone = rm.create_review_clone(sample_project)
    clone["funnel_expires_at"] = (datetime.now() - timedelta(days=1)).isoformat()
    rm.prolong_funnel(clone, extra_days=3)
    new = datetime.fromisoformat(clone["funnel_expires_at"])
    assert (new - datetime.now()).total_seconds() > timedelta(days=2).total_seconds()


# ---------------------------------------------------------------------------
# Module import smoke tests
# ---------------------------------------------------------------------------

def test_review_manager_imports_cleanly():
    """translate_core.review_manager has no SyntaxError or ImportError."""
    import importlib
    importlib.import_module("translate_core.review_manager")


def test_ui_review_imports_cleanly():
    """ui.review has no SyntaxError or ImportError."""
    import importlib
    importlib.import_module("ui.review")