"""Review lifecycle manager — pure-Python clone/merge/funnel logic.

No UI, no NiceGUI.  All functions are safe to call from the UI layer or
from background asyncio tasks.

Review clone JSON files live in ``data/reviews/{review_id}.json`` and are
full project dicts (compatible with :class:`~ui.state.WorkspaceState`)
enriched with review-specific fields:

    review_id, original_project_id, original_filename,
    status, reviewer_name,
    funnel_url, funnel_expires_at, funnel_validity_days, funnel_active,
    round_trip_count,
    segments (each carries ``original_id`` + ``reviewer_target`` +
              ``reviewer_comment`` + ``reviewer_status``)
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from translate_core import comments as cm

log = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent.parent
REVIEWS_DIR = _BASE_DIR / "data" / "reviews"
REVIEWS_DIR.mkdir(parents=True, exist_ok=True)

# Status constants
STATUS_OPEN = "open"
STATUS_REVIEWING = "reviewing"
STATUS_MERGING = "merging"
STATUS_MERGED = "merged"
STATUS_REOPEN = "reopen"
STATUS_CLOSED = "closed"

# Reviewer per-segment status
RS_PENDING = "pending"
RS_SUGGESTED = "suggested"
RS_COMMENTED = "commented"
RS_APPROVED = "approved"

# Default funnel validity in days.
DEFAULT_VALIDITY_DAYS = 3

# -----------------------------------------------------------------------
# Watchdog state (process-global)
# -----------------------------------------------------------------------
_watchdog_task: asyncio.Task[None] | None = None


# ======================================================================
# Clone creation / persistence
# ======================================================================
def create_review_clone(
    original_project: dict,
    segment_ids: list[int] | None = None,
    reviewer_name: str = "",
    validity_days: int = DEFAULT_VALIDITY_DAYS,
) -> dict:
    """Build a review-clone dict from *selected* segments of the original.

    Parameters
    ----------
    original_project
        The full project dict as returned by :func:`main.load_project`.
    segment_ids
        Original segment ``id`` values to include.  ``None`` means "all
        segments whose ``status == 'done'``".
    reviewer_name
        Optional reviewer display name (informational only).
    validity_days
        Funnel validity in days (1-30, clamped).
    """
    validity_days = max(1, min(30, validity_days))
    review_id = "rev-" + uuid.uuid4().hex[:8]
    now = datetime.now()

    segs = original_project.get("segments", [])
    clone_round = original_project.get("round_trip_count", 0) + 1
    if segment_ids is None:
        selected = [s for s in segs if s.get("status") == "done"]
    else:
        id_set = set(segment_ids)
        # Preserve the original order.
        selected = [s for s in segs if s.get("id") in id_set]

    clone_segments: list[dict] = []
    for i, s in enumerate(selected):
        clone_segments.append(
            {
                "id": i,
                "original_id": s.get("id"),
                "source": s.get("source", ""),
                "target": s.get("target", ""),
                "status": s.get("status", "pending"),
                # Preserve para-level manifest keys so the reviewer pane
                # can show heading/blockquote styling if needed.
                **{
                    k: v
                    for k, v in s.items()
                    if k
                    not in (
                        "id",
                        "source",
                        "target",
                        "status",
                        "review_comment",
                        "comments",
                    )
                },
                "reviewer_target": "",
                "comments": cm.clone_comments_for_review(
                    cm.ensure_comments(dict(s)), clone_round, review_id
                ),
                "_clone_round": clone_round,
                "_clone_review_id": review_id,
                "reviewer_status": RS_PENDING,
            }
        )

    # Copy segments_meta for matching original segments.
    meta = original_project.get("segments_meta", [])
    if segment_ids is not None and meta:
        id_set = set(segment_ids)
        meta = [m for m in meta if m.get("index") in id_set]

    clone: dict[str, Any] = {
        "round": clone_round,
        "review_id": review_id,
        "original_project_id": original_project.get("project_id")
        or original_project.get("id"),
        "original_filename": original_project.get("filename", ""),
        "lang_pair": original_project.get("lang_pair", "en->sl"),
        "pipeline": original_project.get("pipeline", "academic"),
        "project_type": original_project.get("project_type", "book_translation"),
        "house_style": original_project.get("house_style", ""),
        "created_at": now.isoformat(timespec="seconds"),
        "status": STATUS_OPEN,
        "reviewer_name": reviewer_name,
        "funnel_url": "",
        "funnel_expires_at": "",
        "funnel_validity_days": validity_days,
        "funnel_active": False,
        "round_trip_count": 0,
        "segments": clone_segments,
        "segments_meta": meta,
    }
    return clone


def _clone_path(review_id: str) -> Path:
    # Strip the "rev-" prefix for the filename but keep it simple.
    safe = review_id.replace("/", "_").replace("..", "_")
    return REVIEWS_DIR / f"{safe}.json"


def save_review(clone: dict) -> None:
    """Persist *clone* to ``data/reviews/{review_id}.json``."""
    path = _clone_path(clone["review_id"])
    path.write_text(
        json.dumps(clone, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_review(review_id: str) -> dict | None:
    path = _clone_path(review_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("load_review: corrupt clone %s", review_id)
        return None


def delete_review(review_id: str) -> None:
    path = _clone_path(review_id)
    if path.exists():
        path.unlink()


def list_reviews(original_project_id: str | None = None) -> list[dict]:
    """Return all review clones, newest-first.

    If *original_project_id* is given, filter to that project.
    """
    out: list[dict] = []
    for p in sorted(REVIEWS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if original_project_id and d.get("original_project_id") != original_project_id:
            continue
        out.append(d)
    return out


# ======================================================================
# Reviewer-side mutations
# ======================================================================
def apply_reviewer_changes(
    clone: dict,
    changes: dict[int, dict],
) -> None:
    """Apply reviewer edits to *clone* in place.

    ``changes`` maps ``clone_segment_id → {reviewer_target, reviewer_comment,
    reviewer_status}``.
    """
    seg_map = {s["id"]: s for s in clone["segments"]}
    for sid, ch in changes.items():
        seg = seg_map.get(sid)
        if seg is None:
            continue
        if "reviewer_target" in ch:
            seg["reviewer_target"] = ch["reviewer_target"]
        if "reviewer_comment" in ch:
            text = (ch["reviewer_comment"] or "").strip()
            cs = cm.ensure_comments(seg)
            cs[:] = [c for c in cs if not (
                c.get("author") == cm.AUTHOR_REVIEWER
                and c.get("round") == seg.get("_clone_round", 1)
                and c.get("mutable", False)
            )]
            if text:
                cm.add_comment(seg, cm.AUTHOR_REVIEWER,
                               seg.get("_clone_round", 1), text,
                               review_id=seg.get("_clone_review_id", ""))
        if "reviewer_status" in ch:
            seg["reviewer_status"] = ch["reviewer_status"]
        else:
            has_target = bool(seg.get("reviewer_target", "").strip())
            has_comment = any(
                c.get("author") == cm.AUTHOR_REVIEWER
                and c.get("round") == seg.get("_clone_round", 1)
                for c in cm.ensure_comments(seg)
            )
            if has_target:
                seg["reviewer_status"] = RS_SUGGESTED
            elif has_comment:
                seg["reviewer_status"] = RS_COMMENTED
            else:
                seg["reviewer_status"] = RS_PENDING
    if clone["status"] == STATUS_OPEN:
        clone["status"] = STATUS_REVIEWING


def update_review_status(clone: dict, status: str) -> None:
    clone["status"] = status


# ======================================================================
# Merge into original
# ======================================================================
def merge_review_into_original(
    original_project: dict,
    clone: dict,
    accepted_original_ids: set[int],
) -> dict:
    """Merge accepted reviewer suggestions into *original_project*.

    Returns the mutated *original_project* dict.

    For each accepted segment whose ``original_id`` is in
    *accepted_original_ids* and whose ``reviewer_target`` is non-empty,
    the original's ``target`` is replaced by the reviewer's suggestion.
    Comments are appended to the original segment's ``comments`` list as
    frozen reviewer entries (preserving history across rounds).
    """
    orig_map = {s["id"]: s for s in original_project.get("segments", [])}
    for cseg in clone["segments"]:
        oid = cseg.get("original_id")
        if oid not in accepted_original_ids:
            continue
        orig = orig_map.get(oid)
        if orig is None:
            log.warning(
                "merge: original segment %s not found (deleted?) — skipping", oid
            )
            continue
        rt = cseg.get("reviewer_target", "").strip()
        if rt:
            orig["target"] = rt
            cseg["reviewer_status"] = RS_APPROVED
        cm.migrate_legacy_segment(orig)
        cm.merge_reviewer_comments(orig, cseg)
    original_project["round_trip_count"] = (
        original_project.get("round_trip_count", 0) + 1
    )
    return original_project


# ======================================================================
# Reopen for another reviewer round
# ======================================================================
def reset_for_reopen(clone: dict) -> None:
    """Reset segments that were NOT approved so the reviewer gets a fresh
    pass on the rejected/updated ones.  Approved segments stay as-is."""
    for seg in clone["segments"]:
        if seg.get("reviewer_status") != RS_APPROVED:
            seg["reviewer_target"] = ""
            cs = cm.ensure_comments(seg)
            cs[:] = [c for c in cs if not (
                c.get("author") == cm.AUTHOR_REVIEWER
                and c.get("round") == seg.get("_clone_round", 1)
                and c.get("mutable", False)
            )]
            seg["reviewer_status"] = RS_PENDING
    clone["round_trip_count"] = clone.get("round_trip_count", 0) + 1
    clone["status"] = STATUS_REOPEN
    clone["reviewer_completed"] = False
    clone.pop("reviewer_completed_at", None)


# ======================================================================
# Tailscale funnel management
# ======================================================================
def _tailscale_available() -> bool:
    return shutil.which("tailscale") is not None


def get_machine_name() -> str:
    """Return the Tailscale DNS name for this machine (no trailing dot)."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        data = json.loads(result.stdout)
        return data.get("Self", {}).get("DNSName", "").rstrip(".")
    except Exception as e:
        log.warning("get_machine_name: %s", e)
        return ""


def start_funnel(port: int = 8080, validity_days: int = DEFAULT_VALIDITY_DAYS) -> tuple[str, str]:
    """Start a Tailscale funnel for the NiceGUI port.

    Returns ``(url, expires_at_iso)``.

    Tailscale has no built-in expiry flag, so the validity is tracked in
    the clone JSON and enforced by :func:`_start_watchdog`.
    """
    if not _tailscale_available():
        raise RuntimeError("Tailscale is not installed")
    # Single-port limitation — reset first.
    subprocess.run(["tailscale", "funnel", "reset"], capture_output=True, timeout=5)
    result = subprocess.run(
        ["tailscale", "funnel", "--bg", str(port)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tailscale funnel failed: {result.stderr.strip()}")
    machine = get_machine_name()
    url = f"https://{machine}"
    expires_at = (
        datetime.now() + timedelta(days=max(1, min(30, validity_days)))
    ).isoformat(timespec="seconds")
    _start_watchdog()
    return url, expires_at


def stop_funnel() -> None:
    """Tear down the Tailscale funnel.  Idempotent."""
    global _watchdog_task
    if _watchdog_task and not _watchdog_task.done():
        _watchdog_task.cancel()
        _watchdog_task = None
    if _tailscale_available():
        subprocess.run(["tailscale", "funnel", "reset"], capture_output=True, timeout=5)


def prolong_funnel(clone: dict, extra_days: int) -> str:
    """Extend the funnel validity by *extra_days*.

    If the funnel already expired, the new expiry is counted from *now*
    rather than from the old deadline.  Returns the new ``expires_at`` ISO
    string.
    """
    current = clone.get("funnel_expires_at")
    now = datetime.now()
    if current:
        try:
            base = max(datetime.fromisoformat(current), now)
        except Exception:
            base = now
    else:
        base = now
    new_expiry = base + timedelta(days=max(1, min(30, extra_days)))
    clone["funnel_expires_at"] = new_expiry.isoformat(timespec="seconds")
    return clone["funnel_expires_at"]


def reopen_review(clone: dict, validity_days: int = DEFAULT_VALIDITY_DAYS) -> str:
    """Re-open a merged/closed review for another reviewer round.

    Resets non-approved segments, restarts the funnel, returns the URL.
    """
    reset_for_reopen(clone)
    url, expires_at = start_funnel(8080, validity_days)
    clone["funnel_url"] = url
    clone["funnel_expires_at"] = expires_at
    clone["funnel_active"] = True
    save_review(clone)
    return url


def funnel_status() -> dict:
    """Return current Tailscale funnel status as a dict."""
    if not _tailscale_available():
        return {"active": False}
    result = subprocess.run(
        ["tailscale", "funnel", "status", "--json"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        return {"active": False}
    try:
        return json.loads(result.stdout)
    except Exception:
        return {"active": False}


# ======================================================================
# Watchdog — auto-tear-down on expiry
# ======================================================================
def _start_watchdog() -> None:
    """Start the funnel-expiry watchdog as a process-global asyncio task."""
    global _watchdog_task
    if _watchdog_task and not _watchdog_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _watchdog_task = loop.create_task(_watchdog_loop())


async def _watchdog_loop() -> None:
    """Every 60 s, check all active reviews for expired funnels.

    Single-port funnel: once we tear down, no other review is active
    either, so we break after the first expiry.
    """
    try:
        while True:
            await asyncio.sleep(60)
            for r in list_reviews():
                if not r.get("funnel_active"):
                    continue
                expires = r.get("funnel_expires_at")
                if not expires:
                    continue
                try:
                    if datetime.fromisoformat(expires) <= datetime.now():
                        stop_funnel()
                        r["funnel_active"] = False
                        save_review(r)
                        break
                except Exception:
                    continue
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning("funnel watchdog: %s", e)


__all__ = [
    "create_review_clone",
    "save_review",
    "load_review",
    "delete_review",
    "list_reviews",
    "apply_reviewer_changes",
    "update_review_status",
    "merge_review_into_original",
    "reset_for_reopen",
    "reopen_review",
    "start_funnel",
    "stop_funnel",
    "prolong_funnel",
    "funnel_status",
    "get_machine_name",
    "DEFAULT_VALIDITY_DAYS",
    "STATUS_OPEN",
    "STATUS_REVIEWING",
    "STATUS_MERGING",
    "STATUS_MERGED",
    "STATUS_REOPEN",
    "STATUS_CLOSED",
    "RS_PENDING",
    "RS_SUGGESTED",
    "RS_COMMENTED",
    "RS_APPROVED",
    "REVIEWS_DIR",
]