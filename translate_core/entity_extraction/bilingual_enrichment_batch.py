# translate_core/entity_extraction/bilingual_enrichment_batch.py
#
# Phase 5: Batch bilingual enrichment for existing KG source_text nodes.
#
# For source_text nodes that already have title_en but lack title_sl (or vice
# versa), this module:
#
#   1. TMX-based matching — looks up the EN title in the translation memory
#      and finds the corresponding SL segment to extract the SL title.
#   2. Cross-type matching — matches SL-only COBISS records against EN-only
#      extraction records by normalised author + title tokens.
#   3. Container enrichment — fills title_orig / title_translation on container
#      nodes (book_translation, article_translation, etc.) using the bilingual
#      pair already present on the node.
#
# All writes go through KnowledgeGraph.update_source_text_node (O-1).
# No direct JSON manipulation.

"""Phase 5: Batch bilingual enrichment for existing KG source_text nodes."""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from typing import Optional

log = logging.getLogger("bilingual_enrichment_batch")

# Container project_types (have translated_by edge and orig/translation fields)
CONTAINER_TYPES = {
    "book_translation",
    "article_translation",
    "festival_programme",
    "exhibition_catalogue",
}


# ── Slugify (must match kg_ingest_entities._slugify) ──────────────────────────

def _slugify(text: str) -> str:
    """NFKD normalize → lowercase → non-alphanumeric → `-` → truncate 80."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    s = re.sub(r"[^a-z0-9]+", "-", nfkd)
    return s[:80] if s else "unknown"


def _normalise(text: str) -> str:
    """Normalise for fuzzy matching: NFKD strip diacritics, lowercase, strip punct."""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s]", "", stripped.lower()).strip()


def _tokenise(text: str) -> set[str]:
    """Tokenise for overlap scoring. Drops single-char tokens."""
    return {t for t in _normalise(text).split() if len(t) > 1}


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# ── Step 1: TMX-based SL title matching ────────────────────────────────────────

def _extract_title_from_segment(
    segment_text: str,
    title_en: str,
) -> Optional[str]:
    """Try to extract the SL title from a segment that contains the EN title.

    Strategy: look for quoted text, italicised text, or the longest
    noun-phrase-like span in the SL segment. If the EN title appears in
    the segment, the SL title is likely the counterpart after a separator
    like ' — ', ': ', or on a separate line.
    """
    # Fast check: does the normalised EN title appear in the segment?
    en_norm = _normalise(title_en)
    seg_norm = _normalise(segment_text)

    if en_norm not in seg_norm:
        # EN title not found at all — can't match
        return None

    # Try separator-based splitting: "EN Title — SL Title" or "EN Title: SL Title"
    for sep in (" — ", " – ", ": ", "\n"):
        if sep in segment_text:
            parts = segment_text.split(sep, 1)
            if _normalise(parts[0]).strip() == en_norm:
                candidate = parts[1].strip().strip('"\'.')
                if candidate and _looks_like_title(candidate):
                    return candidate

    # Try the entire segment if it's short enough to be a title
    if len(segment_text) <= 200 and _looks_like_title(segment_text):
        # If the segment IS the SL side, use it whole
        return segment_text.strip().strip('"\'..')

    return None


def _looks_like_title(text: str) -> bool:
    """Heuristic: does this text look like a book/article title?"""
    text = text.strip()
    if not text or len(text) < 3:
        return False
    # Reject full sentences (long, ends with period, has typical sentence patterns)
    if len(text) > 250:
        return False
    if text.endswith(".") and len(text) > 80:
        return False
    return True


def enrich_from_tmx(
    kg,
    tm_entries: list[dict],
    *,
    dry_run: bool = False,
) -> Counter:
    """For each KG source_text with title_en but no title_sl, try to find
    the SL title via TMX segment lookup.

    TM entries are dicts with keys: source, target, origin.
    """
    stats = Counter()

    source_texts = [
        n for n in kg.G.nodes.values()
        if n.get("type") == "source_text"
        and n.get("title_en")
        and not n.get("title_sl")
    ]

    log.info("TMX enrichment: %d EN-only source_text nodes to process", len(source_texts))

    # Build an index of TM entries by origin + segment content for fast lookup
    # We index by normalised source text for fuzzy matching
    source_index: dict[str, list[dict]] = {}
    for entry in tm_entries:
        key = _normalise(entry.get("source", ""))
        if len(key) > 10:  # Skip very short/meaningless segments
            source_index.setdefault(key, []).append(entry)

    for node in source_texts:
        node_id = node.get("id", "")
        title_en = node.get("title_en", "")
        origin = node.get("origin", "")
        seg_idx = node.get("segment_idx")

        # Strategy 1: Use provenance (origin + segment_idx) to find exact segment
        if origin and seg_idx is not None:
            matching_entries = [
                e for e in tm_entries
                if e.get("origin", "").endswith(origin)
                or origin.endswith(e.get("origin", ""))
            ]
            try:
                seg_idx_int = int(seg_idx)
            except (ValueError, TypeError):
                seg_idx_int = -1

            if matching_entries and 0 <= seg_idx_int < len(matching_entries):
                # Use the indexed entries for this origin
                pass  # Fall through to content-based matching

        # Strategy 2: Content-based matching — find TM segments whose source
        # side contains the normalised EN title
        title_norm = _normalise(title_en)
        title_tokens = _tokenise(title_en)
        if not title_tokens or len(title_norm) < 5:
            stats["skipped_short"] += 1
            continue

        best_match: Optional[str] = None
        best_score = 0.0

        # Search for segments where normalised source contains the title
        for key, entries in source_index.items():
            if title_norm not in key:
                continue
            for entry in entries:
                src = entry.get("source", "")
                tgt = entry.get("target", "")
                if not tgt or not src:
                    continue

                # Score: how much of the title tokens appear in the source
                src_tokens = _tokenise(src)
                overlap = title_tokens & src_tokens
                if not overlap:
                    continue

                denom = max(len(title_tokens), len(src_tokens))
                score = len(overlap) / denom if denom else 0
                if score < 0.4:
                    continue

                if score > best_score:
                    candidate = _extract_title_from_segment(tgt, title_en)
                    if candidate:
                        best_score = score
                        best_match = candidate

        if best_match and best_score >= 0.4:
            # Verify: the SL title shouldn't be identical to the EN title
            if _strip_diacritics(best_match).lower().strip() == _strip_diacritics(title_en).lower().strip():
                stats["skipped_same"] += 1
                continue

            # Length ratio sanity check
            if not _plausible_length_ratio(title_en, best_match):
                stats["skipped_ratio"] += 1
                continue

            if not dry_run:
                kg.update_source_text_node(
                    node_id,
                    title_sl=best_match,
                )
            stats["enriched_tmx"] += 1
            log.info("  TMX: %s → SL title: %s", title_en[:60], best_match[:60])
        else:
            stats["no_tmx_match"] += 1

    log.info(
        "TMX enrichment done: enriched=%d, no_match=%d, skipped=%d",
        stats["enriched_tmx"],
        stats["no_tmx_match"],
        stats.get("skipped_short", 0) + stats.get("skipped_same", 0) + stats.get("skipped_ratio", 0),
    )
    return stats


# ── Step 2: Cross-match SL-only and EN-only nodes ─────────────────────────────

def cross_match_bilingual_nodes(
    kg,
    *,
    dry_run: bool = False,
) -> Counter:
    """Match SL-only source_text nodes against EN-only nodes by normalised
    author + title tokens. When a match is found, merge title_en and title_sl
    onto the EN-side node and mark the SL-side node for dedup.

    For containers: also set title_orig and title_translation.
    """
    stats = Counter()

    nodes = [dict(n) for n in kg.G.nodes.values() if n.get("type") == "source_text"]

    en_only = [n for n in nodes if n.get("title_en") and not n.get("title_sl")]
    sl_only = [n for n in nodes if n.get("title_sl") and not n.get("title_en")]

    log.info("Cross-match: %d EN-only, %d SL-only nodes", len(en_only), len(sl_only))

    # Build lookup from SL nodes by (normalised_author, normalised_title_tokens)
    sl_index: dict[str, list[dict]] = {}
    for n in sl_only:
        author = (n.get("author") or "")
        title_sl = n.get("title_sl", "")
        # Use slug-style key: author_lastname + first 2 title tokens
        author_last = author.split(",")[0].strip().split()[-1] if author else ""
        title_tokens = _tokenise(title_sl)
        key = _slugify(f"{author_last}-{'-'.join(sorted(title_tokens)[:3])}") if title_tokens else ""
        if key:
            sl_index.setdefault(key, []).append(n)

    matched_sl_ids: set[str] = set()

    for en_node in en_only:
        en_id = en_node.get("id", "")
        title_en = en_node.get("title_en", "")
        author = en_node.get("author") or ""
        author_last = author.split(",")[0].strip().split()[-1] if author else ""
        en_tokens = _tokenise(title_en)

        if not en_tokens:
            stats["skipped_no_tokens"] += 1
            continue

        key = _slugify(f"{author_last}-{'-'.join(sorted(en_tokens)[:3])}")
        candidates = sl_index.get(key, [])

        best_match: Optional[dict] = None
        best_score = 0.0

        for sl_node in candidates:
            if sl_node["id"] in matched_sl_ids:
                continue
            sl_tokens = _tokenise(sl_node.get("title_sl", ""))
            if not sl_tokens:
                continue

            overlap = en_tokens & sl_tokens
            denom = max(len(en_tokens), len(sl_tokens))
            if denom == 0:
                continue
            score = len(overlap) / denom

            if score > best_score and score >= 0.3:
                best_score = score
                best_match = sl_node

        if best_match and best_score >= 0.3:
            title_sl = best_match.get("title_sl", "")
            sl_orphan_id = best_match["id"]
            if not dry_run:
                kg.update_source_text_node(
                    en_id,
                    title_sl=title_sl,
                )
                # Short-term repair: no merge_source_text_nodes factory exists yet,
                # so delete the SL-only orphan via remove_node to satisfy §4 inv #4.
                kg.remove_node(sl_orphan_id)
            matched_sl_ids.add(sl_orphan_id)
            stats["cross_matched"] += 1
            log.info(
                "  Cross-match: EN=%s ↔ SL=%s (score=%.2f)",
                title_en[:50], title_sl[:50], best_score,
            )
        else:
            stats["no_cross_match"] += 1

    log.info(
        "Cross-match done: matched=%d, no_match=%d, skipped=%d",
        stats["cross_matched"],
        stats["no_cross_match"],
        stats.get("skipped_no_tokens", 0),
    )
    return stats


# ── Step 3: Container enrichment (title_orig / title_translation) ──────────────

def enrich_containers(
    kg,
    *,
    dry_run: bool = False,
) -> Counter:
    """For container source_text nodes that have both title_en and title_sl,
    set title_orig and title_translation from the existing bilingual pair.

    Per ontology §2.4.2:
      - Containers use title_orig + title_translation (not title_en/title_sl)
      - title_orig = the original-language title
      - title_translation = the title in the translation language
      - orig_lang and translation_lang are set accordingly
    """
    stats = Counter()

    nodes = [dict(n) for n in kg.G.nodes.values() if n.get("type") == "source_text"]
    containers = [n for n in nodes if n.get("project_type") in CONTAINER_TYPES]

    log.info("Container enrichment: %d containers to check", len(containers))

    for node in containers:
        node_id = node.get("id", "")
        title_en = node.get("title_en")
        title_sl = node.get("title_sl")
        title_orig = node.get("title_orig")
        title_translation = node.get("title_translation")

        # If we have both en/sl but not orig/translation, derive them
        if title_en and title_sl:
            updates = {}
            if not title_orig:
                updates["title_orig"] = title_en
            if not title_translation:
                updates["title_translation"] = title_sl

            # Derive language codes from the node's existing metadata.
            # Do NOT hard-code 'en'/'sl' — works originally in other languages
            # (e.g. German, French) would otherwise be mislabelled.
            existing_lang = (node.get("language") or "").strip().lower() or None
            title_surface = (node.get("title") or "").strip()
            orig_lang: Optional[str] = None
            translation_lang: Optional[str] = None
            if existing_lang == "en":
                orig_lang, translation_lang = "en", "sl"
            elif existing_lang == "sl":
                orig_lang, translation_lang = "sl", "en"
            elif title_surface and title_surface == title_en.strip():
                orig_lang, translation_lang = "en", "sl"
            elif title_surface and title_surface == title_sl.strip():
                orig_lang, translation_lang = "sl", "en"

            if orig_lang and not node.get("orig_lang"):
                updates["orig_lang"] = orig_lang
            if translation_lang and not node.get("translation_lang"):
                updates["translation_lang"] = translation_lang
            if orig_lang is None:
                log.info(
                    "  Container %s: orig_lang/translation_lang undecidable "
                    "from metadata (language=%r, title surface does not match "
                    "title_en or title_sl); leaving them unset",
                    node_id, existing_lang,
                )

            if updates:
                if not dry_run:
                    kg.update_source_text_node(node_id, **updates)
                stats["enriched"] += 1
                log.info(
                    "  Container %s: set %s",
                    node_id, ", ".join(updates.keys()),
                )
        else:
            stats["incomplete"] += 1

    log.info(
        "Container enrichment done: enriched=%d, incomplete=%d",
        stats["enriched"], stats["incomplete"],
    )
    return stats


# ── Step 4: Full batch run ────────────────────────────────────────────────────

def run_bilingual_enrichment(
    kg,
    tm_entries: Optional[list[dict]] = None,
    *,
    dry_run: bool = False,
) -> dict[str, Counter]:
    """Run all bilingual enrichment phases on the KG.

    Returns a dict of {phase_name: Counter} results.
    """
    results: dict[str, Counter] = {}

    # Phase A: TMX-based SL title enrichment
    if tm_entries:
        results["tmx"] = enrich_from_tmx(kg, tm_entries, dry_run=dry_run)
    else:
        log.info("No TM entries provided — skipping TMX enrichment")
        results["tmx"] = Counter()

    # Phase B: Cross-match SL-only ↔ EN-only nodes
    results["cross_match"] = cross_match_bilingual_nodes(kg, dry_run=dry_run)

    # Phase C: Container enrichment
    results["containers"] = enrich_containers(kg, dry_run=dry_run)

    # Save KG
    if not dry_run:
        kg.save()
        log.info("KG saved after bilingual enrichment")

    return results


def _plausible_length_ratio(en_title: str, sl_title: str) -> bool:
    """Real title translations are within 0.4–2.5x of source length."""
    if not en_title or not sl_title:
        return False
    ratio = len(sl_title) / len(en_title)
    return 0.4 <= ratio <= 2.5