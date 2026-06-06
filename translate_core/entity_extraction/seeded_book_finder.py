"""Title-anchored translated_work detection.

Approach (per user's feedback): the TMs don't have clean book front-matter.
A book's sentences are scattered across thousands of segments mixed with
other content. But each translated book has a TITLE that — when it appears
in the TM as a header / chapter marker / standalone segment — anchors the
book.

This finder loads `data/translated_works_seeds.json` (a manifest the user
populates with their known translated works) and:

1. For each book, searches all TM entries for title-pattern matches.
2. The earliest match in each origin is the "anchor" segment.
3. Claims the next `segment_window` segments after the anchor as part of
   that book — produces a translated_work record with the segment range.
4. Citations that fall inside a claimed window get `cited_in` linkage to
   the seeded book (not the generic origin slug).

Replaces auto-detected front-matter clusters as the source of
translated_work entities. Cluster detection still runs but its records
are demoted to REVIEW tier unless they match a seed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

DEFAULT_SEEDS_PATH = Path("data/translated_works_seeds.json")
DEFAULT_WINDOW = 150


@dataclass
class BookClaim:
    """A book has been anchored in the TM.

    `segment_ranges` is a list of (global_start_idx, global_end_idx) tuples —
    inclusive of both ends. Any segment with global_idx falling inside ANY
    range is treated as part of this book by downstream extractors.
    """
    work_id: str
    author: str
    title_orig: str
    title_translation: str
    orig_lang: str
    translation_lang: str
    translator: Optional[str]
    year: Optional[int]
    publisher: Optional[str]
    publisher_city: Optional[str]
    origin: str               # The origin where the FIRST title match was found (for display)
    anchor_idx: int           # First title-match global idx (for display)
    segment_ranges: List[tuple] = field(default_factory=list)  # [(start, end), ...]
    matched_pattern: str = ""
    matched_text_excerpt: str = ""
    project_type: str = "book_translation"

    def contains(self, global_idx: int) -> bool:
        for start, end in self.segment_ranges:
            if start <= global_idx <= end:
                return True
        return False

    @property
    def total_segments_in_range(self) -> int:
        return sum(end - start + 1 for start, end in self.segment_ranges)


@dataclass
class SeedManifest:
    works: List[dict] = field(default_factory=list)
    default_window: int = DEFAULT_WINDOW

    @classmethod
    def load(cls, path: Path = DEFAULT_SEEDS_PATH) -> "SeedManifest":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            works=data.get("works", []),
            default_window=int(data.get("_segment_window_default", DEFAULT_WINDOW)),
        )


def _segment_contains_pattern(seg_src: str, seg_tgt: str, pattern: str) -> Optional[str]:
    """Case-insensitive search; returns matched excerpt or None.

    Checks BOTH the source and target field — the user's TM is normalized
    to EN-source/SL-target, but a SL-original book's title (e.g.
    "Življenje umetnosti") appears in the SL target field while its EN
    translation ("The Life of Art") appears in source.
    """
    p_low = pattern.lower()
    for field in (seg_src, seg_tgt):
        if not field:
            continue
        idx = field.lower().find(p_low)
        if idx >= 0:
            lo = max(0, idx - 40)
            hi = min(len(field), idx + len(pattern) + 80)
            return field[lo:hi]
    return None


def find_book_anchors(
    entries: Sequence[dict],
    manifest: SeedManifest,
) -> List[BookClaim]:
    """For each seeded work, return one BookClaim.

    The claim contains:
    - segment_ranges from manifest (if provided), OR
    - a single fallback range derived from the first title-pattern match
      using `segment_window` (legacy behaviour).
    """
    claims: List[BookClaim] = []
    for work in manifest.works:
        patterns: List[str] = list(work.get("title_search_patterns", []))
        manifest_ranges = work.get("segment_ranges") or []
        # Find first title-match for display purposes (anchor_idx, origin)
        anchor_idx: Optional[int] = None
        anchor_origin: Optional[str] = None
        anchor_pattern: Optional[str] = None
        anchor_excerpt: Optional[str] = None
        for global_idx, e in enumerate(entries):
            for p in patterns:
                excerpt = _segment_contains_pattern(e["source"], e["target"], p)
                if excerpt:
                    anchor_idx = global_idx
                    anchor_origin = e.get("origin", "")
                    anchor_pattern = p
                    anchor_excerpt = excerpt
                    break
            if anchor_idx is not None:
                break

        # Build segment_ranges
        ranges: List[tuple] = []
        if manifest_ranges:
            for r in manifest_ranges:
                if isinstance(r, list) and len(r) == 2:
                    ranges.append((int(r[0]), int(r[1])))
        elif anchor_idx is not None:
            # Fallback: window-based single range from anchor
            window = int(work.get("segment_window") or manifest.default_window)
            ranges.append((anchor_idx, anchor_idx + window))

        if not ranges and anchor_idx is None:
            # Book not found at all
            continue

        year = work.get("year")
        try:
            year_i = int(year) if year is not None else None
        except (TypeError, ValueError):
            year_i = None

        claims.append(BookClaim(
            work_id=work["work_id"],
            author=work.get("author", ""),
            title_orig=work.get("title_orig", ""),
            title_translation=work.get("title_translation", ""),
            orig_lang=work.get("orig_lang", "sl"),
            translation_lang=work.get("translation_lang", "en"),
            translator=work.get("translator"),
            year=year_i,
            publisher=work.get("publisher"),
            publisher_city=work.get("publisher_city"),
            origin=anchor_origin or "",
            anchor_idx=anchor_idx if anchor_idx is not None else (ranges[0][0] if ranges else 0),
            segment_ranges=ranges,
            matched_pattern=anchor_pattern or "",
            matched_text_excerpt=anchor_excerpt or "",
            project_type=work.get("project_type", "book_translation"),
        ))
    return claims


def claim_overlaps_segment(claim: BookClaim, global_idx: int, origin: str) -> bool:
    # We no longer constrain by origin here — ranges are global, and the user
    # may have books spread across multiple TM files.
    return claim.contains(global_idx)


def claim_for_segment(
    claims: Sequence[BookClaim], global_idx: int, origin: str,
) -> Optional[BookClaim]:
    """Return the FIRST claim that contains the given global_idx."""
    for c in claims:
        if c.contains(global_idx):
            return c
    return None


def book_claims_to_records(claims: Sequence[BookClaim]) -> List[dict]:
    """Turn BookClaim objects into ingest-ready translated_work records."""
    out: List[dict] = []
    for c in claims:
        out.append({
            "kind": "translated_work",
            "payload": {
                "work_id": c.work_id,
                "author": c.author,
                "title_en": c.title_translation if c.translation_lang == "en" else c.title_orig,
                "title_sl": c.title_translation if c.translation_lang == "sl" else c.title_orig,
                "title_orig": c.title_orig,
                "title_translation": c.title_translation,
                "orig_lang": c.orig_lang,
                "translation_lang": c.translation_lang,
                "translator": c.translator,
                "year": c.year,
                "publisher": c.publisher,
                "publisher_city": c.publisher_city,
                "origin": c.origin,
                "anchor_idx": c.anchor_idx,
                "segment_ranges": [list(r) for r in c.segment_ranges],
                "total_segments_in_range": c.total_segments_in_range,
                "matched_pattern": c.matched_pattern,
                "project_type": c.project_type,
            },
            # Seeded works are user-confirmed — direct-write tier
            "signals": {
                "has_title": True,
                "has_author": True,
                "has_year": c.year is not None,
                "position_front_matter": False,
                "title_bilingual": True,
                "has_publisher": c.publisher is not None,
                "seeded": True,
            },
            "source": {
                "origin": c.origin,
                "segment_idx": c.anchor_idx,
                "src_excerpt": c.matched_text_excerpt[:240],
                "tgt_excerpt": "",
                "segment_class": "seeded_anchor",
            },
        })
    return out
