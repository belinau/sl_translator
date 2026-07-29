"""Comment schema, legacy migration, and CRUD helpers for segment comments.

A CommentEntry is a plain dict:

    {"id": "c-<hex8>", "author": "translator"|"reviewer", "round": int,
     "text": str, "created_at": "<iso seconds>", "review_id": str,
     "mutable": bool}

``round`` 0 means the translator draft; round N is the Nth review round.
All mutations are in-place on the segment dict so they compose with the
existing autosave/clone/merge flows.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime

AUTHOR_TRANSLATOR = "translator"
AUTHOR_REVIEWER = "reviewer"


def new_comment(author: str, round: int, text: str, review_id: str = "") -> dict:
    """Build a fresh mutable CommentEntry. Returns {} for blank text."""
    if not text or not text.strip():
        return {}
    return {
        "id": "c-" + uuid.uuid4().hex[:8],
        "author": author,
        "round": round,
        "text": text.strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "review_id": review_id,
        "mutable": True,
    }


def migrate_legacy_segment(seg: dict) -> None:
    """Idempotently migrate a legacy ``review_comment`` string into comments."""
    if "comments" in seg:
        return
    legacy = seg.get("review_comment")
    if legacy and legacy.strip():
        c = new_comment(AUTHOR_REVIEWER, 0, legacy)
        c["mutable"] = False
        seg["comments"] = [c]
        seg.pop("review_comment", None)
    else:
        seg.pop("review_comment", None)
        seg["comments"] = []


def ensure_comments(seg: dict) -> list[dict]:
    """Migrate if needed and return the segment's comment list (live ref)."""
    if "comments" not in seg:
        migrate_legacy_segment(seg)
    return seg["comments"]


def add_comment(seg: dict, author: str, round: int, text: str, review_id: str = "") -> dict:
    """Append a comment to the segment. Returns {} for blank text."""
    ensure_comments(seg)
    entry = new_comment(author, round, text, review_id=review_id)
    if not entry:
        return {}
    seg["comments"].append(entry)
    return entry


def update_comment(seg: dict, comment_id: str, text: str) -> bool:
    """Update a mutable comment's text. Returns False if not found/frozen/blank."""
    if not text or not text.strip():
        return False
    for c in ensure_comments(seg):
        if c["id"] == comment_id and c.get("mutable") is True:
            c["text"] = text.strip()
            return True
    return False


def delete_comment(seg: dict, comment_id: str) -> bool:
    """Delete a mutable comment. Returns False if not found/frozen."""
    comments = ensure_comments(seg)
    for i, c in enumerate(comments):
        if c["id"] == comment_id and c.get("mutable") is True:
            del comments[i]
            return True
    return False


def freeze_round(comments: list[dict], round: int, review_id: str) -> None:
    """Freeze all comments matching (round, review_id)."""
    for c in comments:
        if c.get("round") == round and c.get("review_id") == review_id:
            c["mutable"] = False


def clone_comments_for_review(orig_comments: list[dict], round: int, review_id: str) -> list[dict]:
    """Deep-copy every entry as immutable history for a review clone."""
    cloned = copy.deepcopy(orig_comments)
    for c in cloned:
        c["mutable"] = False
    return cloned


def merge_reviewer_comments(orig_seg: dict, clone_seg: dict) -> None:
    """Append the clone's reviewer-authored comments onto orig_seg, frozen."""
    clone_round = clone_seg.get("_clone_round", 1)
    review_id = clone_seg.get("_clone_review_id", "")
    orig_comments = ensure_comments(orig_seg)
    for c in clone_seg.get("comments", []):
        if c.get("author") == AUTHOR_REVIEWER and c.get("round") == clone_round:
            entry = copy.deepcopy(c)
            entry["mutable"] = False
            if review_id:
                entry["review_id"] = review_id
            orig_comments.append(entry)


def comment_count(segments: list[dict]) -> int:
    """Total number of comments across all segments."""
    return sum(len(ensure_comments(s)) for s in segments)