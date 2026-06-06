"""Phase 6 — container attribution.

Public surface:
    Anchor                          NamedTuple — a t_index-keyed claim that a
                                    container produces all subsequent segments
                                    of an origin until the next Anchor fires.
    AttributionResult               NamedTuple — output of attribute_segments_to_containers.
    load_curator_anchors(...)       Read data/segment_title_attribution.json.
    load_ngram_anchors(...)         Read data/segment_attribution_ngram.json.
    attribute_segments_to_containers(anchors, tm_entries)
                                    Apply anchors to tm_entries, propagating
                                    each anchor through subsequent t_indexes
                                    of the same origin until the next anchor.

Per-origin contract (cross-referenced with translate_core/tm.py):
    `tm.entries` is built by `_build_compat_entries`, which sorts each origin
    by `raw_index` before re-numbering. That means
        `[e for e in tm.entries if e["origin"] == X][seg_idx]`
    yields the entry whose `t_index` is the correct lookup key for a curator
    seg_idx in `data/segment_title_attribution.json`.

Walker invariant:
    Anchors with the same (origin, t_index) but DIFFERENT container_ids are
    treated as a conflict and the t_index is removed from the usable set.
    Anchors that agree are silently deduplicated. Indefinite propagation
    (one anchor at t=10 attributes ALL subsequent entries until the next
    anchor) is the audit §4 design.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Literal, NamedTuple

logger = logging.getLogger(__name__)


# ── Public datatypes ──────────────────────────────────────────────────────────


class Anchor(NamedTuple):
    """A single (origin, t_index) → container_id claim."""

    origin: str
    t_index: int
    container_id: str  # WITHOUT "source:" prefix
    source: Literal["curator", "ngram"]


class AttributionResult(NamedTuple):
    """Output of attribute_segments_to_containers."""

    attributed: dict[tuple[str, int], str]
    """key=(origin, t_index), value=container_id (no prefix)."""

    conflicts: list[dict]
    """One dict per (origin, t_index) collision with disagreeing container_ids.
    Shape: {"origin": str, "t_index": int,
            "container_ids": [str, ...], "sources": [str, ...]}."""

    unanchored: list[tuple[str, int]]
    """(origin, t_index) entries that lie before the first anchor for their
    origin OR that belong to an origin with no anchors at all."""

    origins_by_pair: dict[str, tuple[str | None, str | None]]
    """origin -> (source_lang, target_lang) from the FIRST tm_entries dict
    seen for that origin (no compat-swap applied here — callers pass
    actual codes via iter_chronological() OR compat-swapped via tm.entries)."""


# ── Loaders ───────────────────────────────────────────────────────────────────


def load_curator_anchors(
    path: str | Path = "data/segment_title_attribution.json",
    tm_entries: Iterable[dict] | None = None,
) -> list[Anchor]:
    """Convert curator-managed seg_idx-keyed JSON to t_index-keyed Anchors.

    Missing file returns []."""
    return _load_anchors_from_json(path, source="curator", tm_entries=tm_entries)


def load_ngram_anchors(
    path: str | Path = "data/segment_attribution_ngram.json",
    tm_entries: Iterable[dict] | None = None,
) -> list[Anchor]:
    """Convert n-gram-derived seg_idx-keyed JSON to t_index-keyed Anchors.

    Missing file returns []."""
    return _load_anchors_from_json(path, source="ngram", tm_entries=tm_entries)


def _load_anchors_from_json(
    path: str | Path,
    *,
    source: Literal["curator", "ngram"],
    tm_entries: Iterable[dict] | None,
) -> list[Anchor]:
    """Common loader: seg_idx → t_index translation via per-origin natural order."""
    p = Path(path)
    if not p.exists():
        return []

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load %s anchors from %s: %s", source, p, exc)
        return []

    # Per-origin natural order (matches what tm.entries yields after
    # _build_compat_entries' raw_index sort). Preserve insertion order from
    # the iterable.
    by_origin: dict[str, list[dict]] = defaultdict(list)
    if tm_entries is not None:
        for entry in tm_entries:
            origin = entry.get("origin", "")
            if origin:
                by_origin[origin].append(entry)

    anchors: list[Anchor] = []
    if not isinstance(raw, dict):
        logger.warning(
            "Expected dict at root of %s; got %s — skipping",
            p, type(raw).__name__,
        )
        return []

    for origin, segs in raw.items():
        entries_for_origin = by_origin.get(origin, [])
        if not isinstance(segs, dict):
            continue
        for sidx_str, value in segs.items():
            try:
                seg_idx = int(sidx_str)
            except (TypeError, ValueError):
                continue

            container_id = _pick_container_id(value)
            if container_id is None:
                continue

            if seg_idx < 0 or seg_idx >= len(entries_for_origin):
                logger.warning(
                    "%s anchors: origin=%r seg_idx=%d out of range "
                    "(have %d entries) — skipping",
                    source, origin, seg_idx, len(entries_for_origin),
                )
                continue

            t_index = entries_for_origin[seg_idx].get("t_index")
            if t_index is None:
                logger.warning(
                    "%s anchors: origin=%r seg_idx=%d entry missing t_index "
                    "— skipping",
                    source, origin, seg_idx,
                )
                continue

            anchors.append(
                Anchor(
                    origin=origin,
                    t_index=int(t_index),
                    container_id=container_id,
                    source=source,
                )
            )

    return anchors


def _pick_container_id(value: object) -> str | None:
    """Pick the first `source:`-prefixed string and strip the prefix.

    Accepted shapes:
      - ["source:foo", ...] → "foo"
      - "source:foo"        → "foo"
    Anything else returns None.
    """
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.startswith("source:"):
                return item.removeprefix("source:")
        return None
    if isinstance(value, str) and value.startswith("source:"):
        return value.removeprefix("source:")
    return None


# ── Attribution walker ────────────────────────────────────────────────────────


def attribute_segments_to_containers(
    anchors: Iterable[Anchor],
    tm_entries: Iterable[dict],
) -> AttributionResult:
    """Walk each origin's entries in t_index order, propagating each anchor.

    Algorithm:
      1. Group anchors by (origin, t_index). At each key,
         collect (container_id, source) pairs.
         - If all pairs agree on container_id, silently dedupe to one usable
           anchor with that container_id and source="curator"-or-"ngram" of
           the first contributor.
         - If they disagree, emit ONE conflict dict per (origin, t_index)
           collision and DROP every anchor at that t_index from the usable set.
      2. For each origin, walk its entries sorted by t_index ascending:
           active = None
           for entry in entries_sorted_by_t_index:
               while next_anchor and next_anchor.t_index <= entry.t_index:
                   active = next_anchor.container_id
                   next_anchor = next anchor
               if active is None:
                   unanchored.append((origin, entry.t_index))
               else:
                   attributed[(origin, entry.t_index)] = active
      3. origins_by_pair[origin] = (first_entry.source_lang,
                                    first_entry.target_lang).

    No MAX_GAP. No proximity propagation. Once an anchor fires, all subsequent
    entries for that origin inherit it until the next anchor.
    """
    materialized_entries = list(tm_entries)
    materialized_anchors = list(anchors)

    # ── 1. group anchors by (origin, t_index) for conflict detection ──
    grouped: dict[tuple[str, int], list[Anchor]] = defaultdict(list)
    for a in materialized_anchors:
        grouped[(a.origin, a.t_index)].append(a)

    conflicts: list[dict] = []
    usable_anchors: list[Anchor] = []

    for (origin, t_index), group in grouped.items():
        distinct_cids = {a.container_id for a in group}
        if len(distinct_cids) == 1:
            # Silent dedup — keep one anchor (first contributor's source).
            usable_anchors.append(group[0])
        else:
            conflicts.append(
                {
                    "origin": origin,
                    "t_index": t_index,
                    "container_ids": sorted(distinct_cids),
                    "sources": sorted({a.source for a in group}),
                }
            )
            # All anchors at this (origin, t_index) are dropped.

    # ── 2. walk entries per-origin ──
    entries_by_origin: dict[str, list[dict]] = defaultdict(list)
    for e in materialized_entries:
        origin = e.get("origin", "")
        if origin:
            entries_by_origin[origin].append(e)

    anchors_by_origin: dict[str, list[Anchor]] = defaultdict(list)
    for a in usable_anchors:
        anchors_by_origin[a.origin].append(a)
    for origin_anchors in anchors_by_origin.values():
        origin_anchors.sort(key=lambda x: x.t_index)

    attributed: dict[tuple[str, int], str] = {}
    unanchored: list[tuple[str, int]] = []

    for origin, entries_for_origin in entries_by_origin.items():
        sorted_entries = sorted(
            entries_for_origin,
            key=lambda e: e.get("t_index", 0),
        )
        anchor_iter = iter(anchors_by_origin.get(origin, []))
        next_anchor = next(anchor_iter, None)
        active: str | None = None

        for entry in sorted_entries:
            t_index = entry.get("t_index")
            if t_index is None:
                continue
            # Advance the iterator past every anchor whose t_index is <= entry.t_index.
            # `<=` is load-bearing: an anchor at t_index=0 must claim an entry at t_index=0
            # (blueprint test (d)).
            while next_anchor is not None and next_anchor.t_index <= t_index:
                active = next_anchor.container_id
                next_anchor = next(anchor_iter, None)
            if active is None:
                unanchored.append((origin, t_index))
            else:
                attributed[(origin, t_index)] = active

    # ── 3. origins_by_pair from the first entry seen per origin ──
    origins_by_pair: dict[str, tuple[str | None, str | None]] = {}
    for e in materialized_entries:
        origin = e.get("origin", "")
        if not origin or origin in origins_by_pair:
            continue
        origins_by_pair[origin] = (
            e.get("source_lang"),
            e.get("target_lang"),
        )

    return AttributionResult(
        attributed=attributed,
        conflicts=conflicts,
        unanchored=unanchored,
        origins_by_pair=origins_by_pair,
    )


__all__ = [
    "Anchor",
    "AttributionResult",
    "load_curator_anchors",
    "load_ngram_anchors",
    "attribute_segments_to_containers",
]
