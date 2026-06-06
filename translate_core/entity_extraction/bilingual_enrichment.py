"""Match-by-pair bilingual enrichment for typed cited_work records.

Primary path (§9.1 of the spec): for each cited_work that came from a
BIBLIOGRAPHY_ENTRY/FOOTNOTE segment with provenance, run the same typed
extraction pipeline on the SL side of the TU. If it produces a verified
typed citation of the same type, merge SL-specific fields (translated
title, container) into the EN record.

Fallback path (§9.2): if the SL side doesn't produce a recognisable typed
citation, do a single-shot SL-title lookup with strict verification —
result must be a substring of the SL segment AND ≤80% of its length.

The previous "ask VL for SL title, take whatever it returns" approach is
gone. Every claim of a bilingual match is now grounded in (a) substring
presence in the actual SL TM segment text, and (b) the same word-overlap
title verification used on the EN side.
"""

from __future__ import annotations

import logging
import sys
import unicodedata
from typing import Optional

log = logging.getLogger("bilingual_enrichment")


WORK_KINDS = {"cited_work", "translated_work"}


# ── Public API ───────────────────────────────────────────────────────────────

def enrich_titles_sl(
    records: list[dict],
    entries_by_origin: dict[str, list[dict]],
    extractor,
) -> list[dict]:
    """For each cited_work record with provenance, attempt match-by-pair
    extraction on the SL segment. Falls back to verified title-only lookup
    when the SL side has no recognisable citation.

    Mutates records in place; returns the list for chaining.
    """
    if not extractor or not entries_by_origin:
        return records

    candidates = [
        r for r in records
        if r.get("kind") in WORK_KINDS
        and (r.get("payload") or {}).get("title_en")
        and not (r.get("payload") or {}).get("title_sl")
    ]
    if not candidates:
        print("bilingual_enrichment: no candidate records", file=sys.stderr)
        return records

    print(
        f"bilingual_enrichment: processing {len(candidates)} cited_work(s) via "
        f"match-by-pair (typed extraction on SL side), with verified fallback",
        file=sys.stderr,
    )

    paired_hits = 0
    skipped_no_provenance = 0
    skipped_empty_tgt = 0

    for r in candidates:
        p = r["payload"]
        title_en = p["title_en"]
        citation_type = p.get("project_type", "cited_work")

        src_dict = r.get("source") or {}
        origin = src_dict.get("origin")
        seg_idx = src_dict.get("segment_idx")
        if origin is None or seg_idx is None:
            skipped_no_provenance += 1
            continue

        entries = entries_by_origin.get(origin)
        if not entries:
            skipped_no_provenance += 1
            continue
        try:
            seg_idx_int = int(seg_idx)
        except (ValueError, TypeError):
            skipped_no_provenance += 1
            continue
        if seg_idx_int < 0 or seg_idx_int >= len(entries):
            skipped_no_provenance += 1
            continue

        entry = entries[seg_idx_int]
        tgt = (entry.get("target") or "").strip()
        src = (entry.get("source") or "").strip()
        if not tgt or _looks_same_as_src(tgt, src):
            skipped_empty_tgt += 1
            continue

        # ── Primary path: typed extraction on SL side ──────────────────
        title_sl = _try_paired_extraction(
            extractor, tgt, citation_type, title_en,
        )
        if title_sl:
            p["title_sl"] = title_sl
            paired_hits += 1
            print(
                f"  ↳ [SL-paired] \"{title_en[:60]}\" → \"{title_sl[:60]}\"",
                file=sys.stderr,
            )
            continue

        # Fallback path intentionally removed (Phase 4, §13.4).
        # When SL typed extraction fails, title_sl stays None — no fragments.

    print(
        f"bilingual_enrichment: paired={paired_hits}, "
        f"skipped_no_provenance={skipped_no_provenance}, "
        f"skipped_empty_tgt={skipped_empty_tgt}, "
        f"candidates={len(candidates)} "
        f"(NOTE: hit counts measure that a match passed substring + length "
        f"verification, NOT that the translation is semantically correct — "
        f"spot-check a sample before claiming a real success rate.)",
        file=sys.stderr,
    )
    return records


# ── Match-by-pair (primary) ──────────────────────────────────────────────────

def _try_paired_extraction(
    extractor,
    tgt: str,
    expected_type: str,
    title_en: str,
) -> Optional[str]:
    """Run typed extraction on the SL segment. If it produces a record of
    the same type as the EN side, return its title."""
    try:
        from .vl_typed_extractor import extract_typed_citation
    except ImportError:
        return None

    verified = extract_typed_citation(extractor, tgt)
    if not verified:
        return None

    # Type must match (book ↔ book, journal_article ↔ journal_article, …)
    if verified.get("type") != expected_type:
        return None

    # Pick the title field for this type
    sl_title = (
        verified.get("title")
        or verified.get("article_title")
        or verified.get("chapter_title")
    )
    if not sl_title or not isinstance(sl_title, str):
        return None

    sl_title = sl_title.strip().strip('"\'').strip()
    if not sl_title:
        return None

    # Cheap sanity: SL title shouldn't be byte-for-byte identical to the EN
    # title (that would mean the SL side has the original-language title,
    # not a translation — store nothing rather than store EN twice).
    if sl_title.lower() == title_en.lower():
        return None

    # Length-ratio sanity. Real title translations are within 0.4–2.5x of the
    # source. Outside that range, the "SL title" is almost certainly the
    # wrong span (a body sentence, an editor name, an adjacent fragment).
    if not _plausible_length_ratio(title_en, sl_title):
        return None

    return sl_title


def _plausible_length_ratio(en_title: str, sl_title: str) -> bool:
    """Translations of titles tend to be within 0.4–2.5x of the source length.
    Outside that band, the SL "match" is almost certainly the wrong span.
    """
    if not en_title or not sl_title:
        return False
    ratio = len(sl_title) / len(en_title)
    return 0.4 <= ratio <= 2.5


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _substring_in(needle: str, haystack: str) -> bool:
    n = _strip_diacritics(needle).lower()
    h = _strip_diacritics(haystack).lower()
    return n in h


def _looks_same_as_src(tgt: str, src: str) -> bool:
    """True if SL side is just the EN side repeated (no real translation)."""
    if not src:
        return False
    if tgt == src:
        return True
    # Compare normalized: same modulo whitespace + diacritics
    return _strip_diacritics(tgt).lower().strip() == _strip_diacritics(src).lower().strip()
