from __future__ import annotations

import copy

from translate_core import comments as cm


def _seg(**kw):
    s = {"id": 0, "source": "", "target": "", "status": "pending"}
    s.update(kw)
    return s


class TestMigration:
    def test_legacy_review_comment_wrapped(self):
        s = _seg(review_comment="Old note")
        cm.migrate_legacy_segment(s)
        assert "review_comment" not in s
        assert len(s["comments"]) == 1
        c = s["comments"][0]
        assert c["text"] == "Old note"
        assert c["author"] == "reviewer"
        assert c["mutable"] is False

    def test_idempotent(self):
        s = _seg(review_comment="X")
        cm.migrate_legacy_segment(s)
        once = copy.deepcopy(s)
        cm.migrate_legacy_segment(s)
        assert s == once

    def test_empty_legacy_adds_empty_list(self):
        s = _seg()
        cm.migrate_legacy_segment(s)
        assert s["comments"] == []

    def test_existing_comments_preserved(self):
        s = _seg(comments=[{"id": "c1", "author": "translator", "round": 0,
                            "text": "t", "created_at": "", "review_id": "",
                            "mutable": True}])
        cm.migrate_legacy_segment(s)
        assert len(s["comments"]) == 1

    def test_empty_string_legacy_treated_as_no_legacy(self):
        s = _seg(review_comment="")
        cm.migrate_legacy_segment(s)
        assert "review_comment" not in s
        assert s["comments"] == []

    def test_whitespace_legacy_treated_as_no_legacy(self):
        s = _seg(review_comment="   ")
        cm.migrate_legacy_segment(s)
        assert s["comments"] == []

    def test_migrated_legacy_entry_round_zero(self):
        s = _seg(review_comment="note")
        cm.migrate_legacy_segment(s)
        assert s["comments"][0]["round"] == 0


class TestNewComment:
    def test_basic_shape(self):
        c = cm.new_comment("translator", 0, "hello")
        assert c["id"].startswith("c-")
        assert c["author"] == "translator"
        assert c["round"] == 0
        assert c["text"] == "hello"
        assert c["review_id"] == ""
        assert c["mutable"] is True
        assert c["created_at"]

    def test_blank_text_returns_empty_dict(self):
        assert cm.new_comment("translator", 0, "   ") == {}

    def test_empty_text_returns_empty_dict(self):
        assert cm.new_comment("translator", 0, "") == {}

    def test_id_unique(self):
        a = cm.new_comment("translator", 0, "a")
        b = cm.new_comment("translator", 0, "a")
        assert a["id"] != b["id"]


class TestCRUD:
    def test_add_translator_comment(self):
        s = _seg()
        c = cm.add_comment(s, "translator", 0, "Note here")
        assert c["author"] == "translator"
        assert c["round"] == 0
        assert c["text"] == "Note here"
        assert c["mutable"] is True
        assert c["review_id"] == ""
        assert len(s["comments"]) == 1

    def test_add_reviewer_comment_with_review_id(self):
        s = _seg()
        c = cm.add_comment(s, "reviewer", 1, "Fix", review_id="rev-abc")
        assert c["review_id"] == "rev-abc"
        assert c["round"] == 1

    def test_add_strips_text(self):
        s = _seg()
        c = cm.add_comment(s, "translator", 0, "  spaced  ")
        assert c["text"] == "spaced"

    def test_add_auto_migrates_legacy(self):
        s = _seg(review_comment="old")
        cm.migrate_legacy_segment(s)
        c = cm.add_comment(s, "translator", 0, "new")
        assert len(s["comments"]) == 2

    def test_add_no_comments_key(self):
        s = _seg()
        c = cm.add_comment(s, "translator", 0, "x")
        assert len(s["comments"]) == 1
        assert c["text"] == "x"

    def test_update_mutable_comment(self):
        s = _seg()
        c = cm.add_comment(s, "translator", 0, "A")
        assert cm.update_comment(s, c["id"], "B")
        assert s["comments"][0]["text"] == "B"

    def test_update_frozen_comment_rejected(self):
        s = _seg()
        c = cm.add_comment(s, "translator", 0, "A")
        cm.freeze_round(s["comments"], 0, "")
        assert not cm.update_comment(s, c["id"], "B")
        assert s["comments"][0]["text"] == "A"

    def test_delete_mutable_comment(self):
        s = _seg()
        c = cm.add_comment(s, "translator", 0, "A")
        assert cm.delete_comment(s, c["id"])
        assert s["comments"] == []

    def test_delete_frozen_comment_rejected(self):
        s = _seg()
        c = cm.add_comment(s, "translator", 0, "A")
        cm.freeze_round(s["comments"], 0, "")
        assert not cm.delete_comment(s, c["id"])
        assert len(s["comments"]) == 1

    def test_update_unknown_id_returns_false(self):
        s = _seg()
        assert not cm.update_comment(s, "nope", "x")

    def test_delete_unknown_id_returns_false(self):
        s = _seg()
        assert not cm.delete_comment(s, "nope")

    def test_update_blank_text_rejected(self):
        s = _seg()
        c = cm.add_comment(s, "translator", 0, "A")
        assert not cm.update_comment(s, c["id"], "   ")
        assert s["comments"][0]["text"] == "A"

    def test_blank_text_not_added(self):
        s = _seg()
        assert not cm.add_comment(s, "translator", 0, "   ")
        assert s["comments"] == []


class TestFreeze:
    def test_freeze_matches_round_only(self):
        comments = [
            cm.new_comment("translator", 0, "a"),
            cm.new_comment("reviewer", 1, "b", review_id="rev-1"),
        ]
        cm.freeze_round(comments, 1, "rev-1")
        assert comments[0]["mutable"] is True
        assert comments[1]["mutable"] is False

    def test_freeze_respects_review_id(self):
        comments = [
            cm.new_comment("reviewer", 1, "b", review_id="rev-1"),
            cm.new_comment("reviewer", 1, "c", review_id="rev-2"),
        ]
        cm.freeze_round(comments, 1, "rev-1")
        assert comments[0]["mutable"] is False
        assert comments[1]["mutable"] is True


class TestCloneMerge:
    def test_clone_comments_seeds_immutable_history(self):
        orig = _seg(comments=[
            cm.new_comment("translator", 0, "T1"),
            cm.new_comment("reviewer", 1, "R1", review_id="rev-old"),
        ])
        cloned = cm.clone_comments_for_review(orig["comments"], round=2, review_id="rev-new")
        assert all(c["mutable"] is False for c in cloned)
        assert len(cloned) == 2

    def test_clone_is_deep_copy(self):
        orig = [cm.new_comment("translator", 0, "T1")]
        cloned = cm.clone_comments_for_review(orig, 2, "rev-new")
        cloned[0]["text"] = "MUTATED"
        assert orig[0]["text"] == "T1"

    def test_clone_empty(self):
        assert cm.clone_comments_for_review([], 2, "rev-new") == []

    def test_merge_reviewer_comments_appends_and_freezes(self):
        orig = _seg()
        clone_seg = {
            "comments": [
                *cm.clone_comments_for_review([], 1, "rev-1"),
                cm.new_comment("reviewer", 1, "Reviewer says", review_id="rev-1"),
            ],
            "_clone_round": 1,
            "_clone_review_id": "rev-1",
        }
        cm.merge_reviewer_comments(orig, clone_seg)
        assert len(orig["comments"]) == 1
        assert orig["comments"][0]["text"] == "Reviewer says"
        assert orig["comments"][0]["mutable"] is False

    def test_merge_skips_translator_entries_in_clone(self):
        orig = _seg()
        clone_seg = {"comments": [cm.new_comment("translator", 0, "T")],
                     "_clone_round": 1, "_clone_review_id": "rev-1"}
        cm.merge_reviewer_comments(orig, clone_seg)
        assert orig["comments"] == []

    def test_merge_skips_wrong_round_entries(self):
        orig = _seg()
        clone_seg = {
            "comments": [cm.new_comment("reviewer", 2, "wrong", review_id="rev-1")],
            "_clone_round": 1,
            "_clone_review_id": "rev-1",
        }
        cm.merge_reviewer_comments(orig, clone_seg)
        assert orig["comments"] == []

    def test_merge_deep_copies(self):
        orig = _seg()
        clone_seg = {
            "comments": [cm.new_comment("reviewer", 1, "src", review_id="rev-1")],
            "_clone_round": 1,
            "_clone_review_id": "rev-1",
        }
        cm.merge_reviewer_comments(orig, clone_seg)
        orig["comments"][0]["text"] = "CHANGED"
        assert clone_seg["comments"][0]["text"] == "src"

    def test_merge_propagates_review_id(self):
        orig = _seg()
        clone_seg = {
            "comments": [cm.new_comment("reviewer", 1, "src")],
            "_clone_round": 1,
            "_clone_review_id": "rev-xyz",
        }
        cm.merge_reviewer_comments(orig, clone_seg)
        assert orig["comments"][0]["review_id"] == "rev-xyz"

    def test_merge_default_clone_round(self):
        orig = _seg()
        clone_seg = {
            "comments": [cm.new_comment("reviewer", 1, "src", review_id="rev-1")],
            # no _clone_round -> default 1
        }
        cm.merge_reviewer_comments(orig, clone_seg)
        assert len(orig["comments"]) == 1


class TestCount:
    def test_comment_count_across_segments(self):
        segs = [
            _seg(comments=[{"id": "a"}, {"id": "b"}]),
            _seg(comments=[]),
            _seg(comments=[{"id": "c"}]),
        ]
        assert cm.comment_count(segs) == 3

    def test_comment_count_migrates_missing_key(self):
        segs = [_seg(review_comment="legacy")]
        assert cm.comment_count(segs) == 1

    def test_comment_count_empty(self):
        assert cm.comment_count([]) == 0


class TestEnsureComments:
    def test_ensure_migrates(self):
        s = _seg(review_comment="legacy")
        lst = cm.ensure_comments(s)
        assert len(lst) == 1
        assert lst[0]["text"] == "legacy"

    def test_ensure_returns_same_list_object(self):
        s = _seg()
        lst = cm.ensure_comments(s)
        cm.add_comment(s, "translator", 0, "x")
        assert len(lst) == 1