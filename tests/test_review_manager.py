"""Unit tests for translate_core.review_manager.

Tests clone creation, merge semantics, prolong/reopen logic, and
reviewer-side mutations. No Tailscale calls (funnel functions are
mocked / skipped).
"""
from __future__ import annotations
from datetime import datetime, timedelta

import pytest

from translate_core import review_manager as rm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_project(n: int = 5, done: int = 3) -> dict:
    """Build a minimal project dict with N segments, `done` marked done."""
    segs = []
    for i in range(n):
        segs.append(
            {
                "id": i,
                "source": f"Source text {i}",
                "target": f"Target text {i}",
                "status": "done" if i < done else "pending",
            }
        )
    return {
        "project_id": "test-project-1234",
        "id": "test-project-1234",
        "filename": "test_book.pdf",
        "lang_pair": "en->sl",
        "pipeline": "academic",
        "project_type": "book_translation",
        "house_style": "maska",
        "segments": segs,
        "segments_meta": [{"type": "body_text", "index": i} for i in range(n)],
    }


@pytest.fixture
def fresh_reviews_dir(monkeypatch, tmp_path):
    """Redirect REVIEWS_DIR to a temp path so tests don't pollute data/."""
    rev_dir = tmp_path / "reviews"
    rev_dir.mkdir()
    monkeypatch.setattr(rm, "REVIEWS_DIR", rev_dir)
    # _clone_path uses the module-level REVIEWS_DIR, so patch the closure:
    monkeypatch.setattr(rm, "_clone_path", lambda rid: rev_dir / f"{rid}.json")
    return rev_dir


# ---------------------------------------------------------------------------
# Clone creation
# ---------------------------------------------------------------------------

class TestCreateReviewClone:
    def test_clone_has_correct_metadata(self, fresh_reviews_dir):
        proj = _make_project()
        clone = rm.create_review_clone(proj, validity_days=5)
        assert clone["review_id"].startswith("rev-")
        assert clone["original_project_id"] == "test-project-1234"
        assert clone["original_filename"] == "test_book.pdf"
        assert clone["lang_pair"] == "en->sl"
        assert clone["status"] == rm.STATUS_OPEN
        assert clone["funnel_active"] is False
        assert clone["funnel_validity_days"] == 5
        assert clone["round_trip_count"] == 0
        assert clone["reviewer_name"] == ""

    def test_all_done_selects_only_done(self, fresh_reviews_dir):
        proj = _make_project(n=5, done=3)
        clone = rm.create_review_clone(proj)  # None = all done
        assert len(clone["segments"]) == 3
        # original_id should be 0, 1, 2 (the done ones)
        assert [s["original_id"] for s in clone["segments"]] == [0, 1, 2]

    def test_specific_segment_ids(self, fresh_reviews_dir):
        proj = _make_project(n=5, done=3)
        clone = rm.create_review_clone(proj, segment_ids=[2, 4])
        assert len(clone["segments"]) == 2
        assert [s["original_id"] for s in clone["segments"]] == [2, 4]

    def test_clone_segments_have_review_fields(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        for seg in clone["segments"]:
            assert "reviewer_target" in seg
            assert seg["reviewer_target"] == ""
            assert "reviewer_comment" in seg
            assert seg["reviewer_comment"] == ""
            assert "reviewer_status" in seg
            assert seg["reviewer_status"] == "pending"

    def test_clone_segments_preserve_manifest_keys(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        proj["segments"][0]["heading_level"] = 1
        proj["segments"][0]["pdf_para_idx"] = 42
        clone = rm.create_review_clone(proj, segment_ids=[0])
        seg = clone["segments"][0]
        assert seg["heading_level"] == 1
        assert seg["pdf_para_idx"] == 42

    def test_validity_clamped(self, fresh_reviews_dir):
        proj = _make_project()
        clone = rm.create_review_clone(proj, validity_days=0)
        assert clone["funnel_validity_days"] == 1
        clone2 = rm.create_review_clone(proj, validity_days=99)
        assert clone2["funnel_validity_days"] == 30

    def test_segments_meta_filtered_for_specific_ids(self, fresh_reviews_dir):
        proj = _make_project(n=5, done=5)
        clone = rm.create_review_clone(proj, segment_ids=[1, 3])
        # segments_meta should only have entries for indices 1 and 3
        indices = [m["index"] for m in clone["segments_meta"]]
        assert indices == [1, 3]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load(self, fresh_reviews_dir):
        proj = _make_project()
        clone = rm.create_review_clone(proj)
        rm.save_review(clone)
        loaded = rm.load_review(clone["review_id"])
        assert loaded is not None
        assert loaded["review_id"] == clone["review_id"]
        assert len(loaded["segments"]) == 3

    def test_load_nonexistent(self, fresh_reviews_dir):
        assert rm.load_review("rev-nonexistent") is None

    def test_delete_review(self, fresh_reviews_dir):
        proj = _make_project()
        clone = rm.create_review_clone(proj)
        rm.save_review(clone)
        rm.delete_review(clone["review_id"])
        assert rm.load_review(clone["review_id"]) is None

    def test_list_reviews_filtered(self, fresh_reviews_dir):
        proj = _make_project()
        clone1 = rm.create_review_clone(proj)
        rm.save_review(clone1)
        # Different project
        proj2 = _make_project()
        proj2["project_id"] = "other-project"
        proj2["id"] = "other-project"
        clone2 = rm.create_review_clone(proj2)
        rm.save_review(clone2)

        all_revs = rm.list_reviews()
        assert len(all_revs) == 2
        filtered = rm.list_reviews(original_project_id="test-project-1234")
        assert len(filtered) == 1
        assert filtered[0]["original_project_id"] == "test-project-1234"


# ---------------------------------------------------------------------------
# Reviewer changes
# ---------------------------------------------------------------------------

class TestApplyReviewerChanges:
    def test_suggestion_sets_status(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        rm.apply_reviewer_changes(clone, {0: {"reviewer_target": "Better translation"}})
        assert clone["segments"][0]["reviewer_target"] == "Better translation"
        assert clone["segments"][0]["reviewer_status"] == "suggested"

    def test_comment_only_sets_commented(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        rm.apply_reviewer_changes(clone, {1: {"reviewer_comment": "Check this"}})
        assert clone["segments"][1]["reviewer_comment"] == "Check this"
        assert clone["segments"][1]["reviewer_status"] == "commented"

    def test_empty_changes_resets_to_pending(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        rm.apply_reviewer_changes(clone, {0: {"reviewer_target": "X", "reviewer_comment": "Y"}})
        # Now clear it
        rm.apply_reviewer_changes(clone, {0: {"reviewer_target": "", "reviewer_comment": ""}})
        assert clone["segments"][0]["reviewer_status"] == "pending"

    def test_status_transitions_to_reviewing(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        assert clone["status"] == "open"
        rm.apply_reviewer_changes(clone, {0: {"reviewer_target": "X"}})
        assert clone["status"] == "reviewing"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

class TestMerge:
    def test_merge_accepted_only(self, fresh_reviews_dir):
        proj = _make_project(n=5, done=5)
        clone = rm.create_review_clone(proj, segment_ids=[0, 1, 2])
        rm.apply_reviewer_changes(
            clone,
            {
                0: {"reviewer_target": "Improved 0"},
                1: {"reviewer_target": "Improved 1"},
                2: {"reviewer_target": "Improved 2"},
            },
        )
        # Accept only segment with original_id=1
        rm.merge_review_into_original(proj, clone, accepted_original_ids={1})
        assert proj["segments"][0]["target"] == "Target text 0"  # unchanged
        assert proj["segments"][1]["target"] == "Improved 1"
        assert proj["segments"][2]["target"] == "Target text 2"  # unchanged

    def test_merge_skips_empty_suggestions(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        # Accept all but reviewer_target is empty
        rm.merge_review_into_original(proj, clone, accepted_original_ids={0, 1, 2})
        for i in range(3):
            assert proj["segments"][i]["target"] == f"Target text {i}"

    def test_merge_preserves_comments(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        rm.apply_reviewer_changes(
            clone,
            {0: {"reviewer_target": "Better", "reviewer_comment": "Grammar fix"}},
        )
        rm.merge_review_into_original(proj, clone, accepted_original_ids={0})
        assert proj["segments"][0]["review_comment"] == "Grammar fix"

    def test_merge_orphaned_segment_skipped(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        rm.apply_reviewer_changes(clone, {0: {"reviewer_target": "X"}})
        # Delete the original segment that matches original_id=0
        proj["segments"] = [s for s in proj["segments"] if s["id"] != 0]
        # Should not crash
        rm.merge_review_into_original(proj, clone, accepted_original_ids={0})
        # Nothing changed
        assert all(s["target"] == f"Target text {s['id']}" for s in proj["segments"])

    def test_merge_comment_accumulates_across_rounds(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        # First round
        rm.apply_reviewer_changes(clone, {0: {"reviewer_target": "V1", "reviewer_comment": "C1"}})
        rm.merge_review_into_original(proj, clone, {0})
        # Second round (new clone)
        clone2 = rm.create_review_clone(proj)
        rm.apply_reviewer_changes(clone2, {0: {"reviewer_target": "V2", "reviewer_comment": "C2"}})
        rm.merge_review_into_original(proj, clone2, {0})
        assert "C1" in proj["segments"][0]["review_comment"]
        assert "C2" in proj["segments"][0]["review_comment"]


# ---------------------------------------------------------------------------
# Reopen
# ---------------------------------------------------------------------------

class TestReopen:
    def test_reset_for_reopen_clears_non_approved(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        rm.apply_reviewer_changes(
            clone,
            {
                0: {"reviewer_target": "A", "reviewer_comment": "Ca"},
                1: {"reviewer_target": "B", "reviewer_comment": "Cb"},
                2: {"reviewer_target": "C", "reviewer_comment": "Cc"},
            },
        )
        # Approve segment 0
        clone["segments"][0]["reviewer_status"] = "approved"
        rm.reset_for_reopen(clone)
        # Segment 0 (approved) should keep its data
        assert clone["segments"][0]["reviewer_target"] == "A"
        # Segments 1 and 2 should be reset
        assert clone["segments"][1]["reviewer_target"] == ""
        assert clone["segments"][1]["reviewer_status"] == "pending"
        assert clone["segments"][2]["reviewer_target"] == ""
        assert clone["segments"][2]["reviewer_status"] == "pending"

    def test_reopen_increments_round_trip(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        assert clone["round_trip_count"] == 0
        rm.reset_for_reopen(clone)
        assert clone["round_trip_count"] == 1
        assert clone["status"] == "reopen"

    def test_reopen_resets_reviewer_completed(self, fresh_reviews_dir):
        proj = _make_project(n=3, done=3)
        clone = rm.create_review_clone(proj)
        clone["reviewer_completed"] = True
        clone["reviewer_completed_at"] = "2026-07-12T12:00:00"
        rm.reset_for_reopen(clone)
        assert clone["reviewer_completed"] is False
        assert "reviewer_completed_at" not in clone


# ---------------------------------------------------------------------------
# Prolong
# ---------------------------------------------------------------------------

class TestProlong:
    def test_prolong_extends_expiry(self, fresh_reviews_dir):
        proj = _make_project()
        clone = rm.create_review_clone(proj, validity_days=3)
        base = datetime.now() + timedelta(days=3)
        clone["funnel_expires_at"] = base.isoformat(timespec="seconds")
        rm.prolong_funnel(clone, extra_days=2)
        new = datetime.fromisoformat(clone["funnel_expires_at"])
        expected = base + timedelta(days=2)
        # Allow a few seconds of slack
        assert abs((new - expected).total_seconds()) < 10

    def test_prolong_from_now_if_expired(self, fresh_reviews_dir):
        proj = _make_project()
        clone = rm.create_review_clone(proj)
        clone["funnel_expires_at"] = (datetime.now() - timedelta(days=1)).isoformat()
        rm.prolong_funnel(clone, extra_days=3)
        new = datetime.fromisoformat(clone["funnel_expires_at"])
        now = datetime.now()
        # Should be ~3 days from now, not from the expired date
        assert (new - now).total_seconds() > timedelta(days=2).total_seconds()

    def test_prolong_clamped(self, fresh_reviews_dir):
        proj = _make_project()
        clone = rm.create_review_clone(proj)
        clone["funnel_expires_at"] = datetime.now().isoformat(timespec="seconds")
        rm.prolong_funnel(clone, extra_days=99)
        new = datetime.fromisoformat(clone["funnel_expires_at"])
        assert (new - datetime.now()).days <= 30