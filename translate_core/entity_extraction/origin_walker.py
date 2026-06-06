"""Walk one TM origin in document order, expose windows of context.

Per-origin order is preserved by translate_core.tm.TranslationMemory.entries
(within an origin, entries follow TMX file order). This walker groups all
entries by origin, applies segment classification, and exposes:

- An iterable of (label, prev_window, next_window) tuples for extractors.
- A derived `project_profile` per origin: the distribution of segment_class
  counts. Extractors use this to decide whether the origin is dominated by
  book/article translations, art catalogues, or festival programmes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Sequence, Tuple

from .segment_classifier import SegmentLabel, classify_segments


@dataclass
class OriginContext:
    origin: str
    labels: List[SegmentLabel]
    profile: Dict[str, float] = field(default_factory=dict)   # class → share
    dominant: str = ""                                        # "book", "catalogue", "festival", "mixed"


def _derive_profile(labels: List[SegmentLabel]) -> Tuple[Dict[str, float], str]:
    if not labels:
        return {}, "empty"
    counts = Counter(l.klass.value for l in labels)
    total = sum(counts.values())
    share = {k: v / total for k, v in counts.items()}

    body = share.get("body_text", 0)
    biblio = share.get("bibliography_entry", 0)
    inline_cite = share.get("inline_citation", 0)
    book_meta = share.get("book_metadata", 0)
    artwork = share.get("artwork_record", 0)
    artist_hdr = share.get("artist_header", 0)
    festival = share.get("festival_credit", 0)
    event = share.get("event_metadata", 0)

    # Heuristic dominance — order matters (most specific first)
    if festival + event > 0.35:
        # Festival programmes have lots of role: name lines and event meta
        if body < 0.25:
            return share, "festival"
    if artwork + artist_hdr > 0.20:
        # Art catalogues have many short artwork records
        return share, "catalogue"
    if body + biblio + inline_cite > 0.35 or book_meta > 0.02:
        # Books / articles / humanities essays
        return share, "book"
    if biblio > 0.10:
        return share, "book"
    return share, "mixed"


def group_by_origin(entries: Sequence[dict]) -> Dict[str, List[dict]]:
    """Group TM entries by their `origin` filename, preserving order."""
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for e in entries:
        grouped[e.get("origin", "")].append(e)
    return dict(grouped)


def walk_origin(
    origin_entries: Sequence[dict],
    *,
    window: int = 4,
) -> Iterator[Tuple[SegmentLabel, List[SegmentLabel], List[SegmentLabel]]]:
    """Yield (focus_label, prev_window, next_window) for each segment.

    Caller is responsible for filtering by SegmentClass — the walker just
    iterates in order with the requested neighbour window.
    """
    labels = classify_segments(origin_entries)
    n = len(labels)
    for i, lbl in enumerate(labels):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        yield lbl, labels[lo:i], labels[i + 1:hi]


def build_origin_contexts(entries: Sequence[dict]) -> List[OriginContext]:
    """For each distinct origin in `entries`, classify + derive a profile.

    Returned contexts are in stable origin-order (the order origins first
    appeared in `entries`).
    """
    grouped = group_by_origin(entries)
    out: List[OriginContext] = []
    for origin, items in grouped.items():
        labels = classify_segments(items)
        profile, dominant = _derive_profile(labels)
        out.append(OriginContext(
            origin=origin,
            labels=labels,
            profile=profile,
            dominant=dominant,
        ))
    return out
