#!/usr/bin/env python3
"""Title-anchor harvester — STRICT one-anchor-per-(container, origin).

For each COBISS translator container, write AT MOST ONE anchor per TMX
origin file. The anchor is the FIRST chronological segment matching one
of these strict signals:

  A. Author byline pattern: segment whose normalised text is essentially
     "<firstname> <surname>" alone (≥60% of the segment is the author's
     full name). This is what the translator types as the article's
     by-line at the start of the body.

  B. Title-page pattern: segment whose normalised text is essentially
     the title alone (≥60% of the segment is the title phrase). This
     catches title-page lines like "Za slavo" or "For Glory".

Both passes are anchored at the FIRST chronological hit in each origin.
A container can therefore have AT MOST one anchor per origin file —
multi-interval propagation across sessions is handled by the walker
when other containers fire their anchors in between.

NGRAM-derived anchors (from build_segment_attribution.py for the books
with source MD files: kunst, zaloznik, okri) are PRESERVED — they are
the gold standard for the books where we have the source text.

No regex; no thresholds beyond the 60% dominance rule for the strict
signal.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.tm import TranslationMemory
from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.cobiss_parser import parse_cobiss_file
from translate_core.cobiss_classifier import classify_entry

ATTR_PATH = ROOT / "data" / "segment_title_attribution.json"
COBISS_PATH = ROOT / "data" / "personal bibliography" / "bibliography_export.txt"

DOMINANCE = 0.6   # signal phrase must be ≥60% of segment length
MIN_PHRASE_LEN = 4  # minimum normalised length of a discriminating phrase
# Containers whose anchors came from the ngram matcher
# (preserve their existing anchors verbatim).
NGRAM_PROTECTED_PREFIXES = (
    "source:kunst-zivljenje-umetnosti",
    "source:zaloznik-jasmina-zavzemanje-prostora-2024",
    "source:okri-ben-cesta-sestradanih-2016",
)


def _phrase_normalise(text: str) -> str:
    """NFKD-strip, lowercase, replace non-alphanumeric with space, collapse."""
    n = unicodedata.normalize("NFKD", text)
    n = "".join(c for c in n if not unicodedata.combining(c)).lower()
    cleaned = "".join(c if c.isalnum() else " " for c in n)
    return " ".join(cleaned.split())


def _title_core(title: str) -> str:
    """Strip COBISS metadata after first `:`/`;`/` = `."""
    if not title:
        return ""
    s = title
    for sep in (" = ", ":", ";"):
        i = s.find(sep)
        if i > 0:
            s = s[:i]
    return s.strip(" ,.-")


def _slug_part(s: str) -> str:
    """NFKD-strip + lowercase + replace non-alphanumeric with '-'."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    interim = "".join(c if (c.isalnum() and c.isascii()) else "-" for c in s)
    parts = [p for p in interim.split("-") if p]
    return "-".join(parts)[:80]


def _container_id_for_entry(entry, kg: KnowledgeGraph) -> str | None:
    """Find canonical KG container id (year-suffixed slug)."""
    if not entry.agents:
        return None
    author = entry.agents[0]
    author_slug = _slug_part(f"{author.last_name} {author.first_name}".strip())
    title_slug = _slug_part(entry.title[:60] if entry.title else "")
    year_slug = str(entry.year) if entry.year else ""
    parts = [p for p in (author_slug, title_slug, year_slug) if p]
    candidate = f"source:{'-'.join(parts)}"
    if kg.G.has_node(candidate):
        return candidate
    return None


def _first_anchor(
    origin_segments: list[tuple[int, dict, str, str]],
    needles: list[str],
) -> int | None:
    """Return seg_idx of the FIRST segment where ANY needle dominates ≥60%
    of EITHER the source-side OR target-side normalised text. Bilingual
    title-page segments score independently on each side, avoiding the
    spurious 50% dominance that combining-both-sides would produce."""
    for seg_idx, _entry, src_phrase, tgt_phrase in origin_segments:
        for side in (src_phrase, tgt_phrase):
            side_len = len(side)
            if side_len == 0:
                continue
            for needle in needles:
                if needle in side and len(needle) / side_len >= DOMINANCE:
                    return seg_idx
    return None


def harvest(tm: TranslationMemory, kg: KnowledgeGraph) -> tuple[dict, dict]:
    entries = parse_cobiss_file(str(COBISS_PATH))

    # Per-origin natural order. For each segment store TWO normalised
    # phrases: source-side and target-side. Dominance is checked against
    # whichever side the needle matches (so a bilingual title-page segment
    # with the title on BOTH sides scores high on each side independently).
    by_origin: dict[str, list[tuple[int, dict, str, str]]] = defaultdict(list)
    for e in tm.entries:
        origin = e.get("origin")
        if not origin:
            continue
        src_phrase = _phrase_normalise(e.get("source") or "")
        tgt_phrase = _phrase_normalise(e.get("target") or "")
        seg_idx = len(by_origin[origin])
        by_origin[origin].append((seg_idx, e, src_phrase, tgt_phrase))

    anchors_by_origin: dict[str, dict[str, list[str]]] = defaultdict(dict)
    stats = {
        "containers_total": 0,
        "containers_with_kg_id": 0,
        "containers_anchored_anywhere": 0,
        "containers_no_anchor": 0,
        "anchor_records_written": 0,
        "no_anchor_examples": [],
    }

    for entry in entries:
        _ptype, role = classify_entry(entry)
        if role != "translator":
            continue
        stats["containers_total"] += 1

        kg_id = _container_id_for_entry(entry, kg)
        if not kg_id:
            continue
        stats["containers_with_kg_id"] += 1

        # AUTHOR-role agents only
        author_full_phrases: list[str] = []
        for ag in entry.agents:
            if "author" in (ag.roles or []) or not ag.roles:
                first = _phrase_normalise(ag.first_name)
                last = _phrase_normalise(ag.last_name)
                if first and last:
                    author_full_phrases.append(first + " " + last)
                    author_full_phrases.append(last + " " + first)

        # Title phrases - core SL + core EN
        title_phrases = []
        for raw in (entry.title or "", entry.title_en or ""):
            core = _title_core(raw)
            norm = _phrase_normalise(core)
            if len(norm) >= MIN_PHRASE_LEN:
                title_phrases.append(norm)

        if not author_full_phrases and not title_phrases:
            continue

        any_origin = False
        for origin, segs in by_origin.items():
            # Try byline (author full name dominates), then title page
            seg_idx = _first_anchor(segs, author_full_phrases)
            if seg_idx is None:
                seg_idx = _first_anchor(segs, title_phrases)
            if seg_idx is None:
                continue
            slot = anchors_by_origin[origin].setdefault(str(seg_idx), [])
            if kg_id not in slot:
                slot.append(kg_id)
                stats["anchor_records_written"] += 1
            any_origin = True

        if any_origin:
            stats["containers_anchored_anywhere"] += 1
        else:
            stats["containers_no_anchor"] += 1
            if len(stats["no_anchor_examples"]) < 10:
                stats["no_anchor_examples"].append({
                    "kg_id": kg_id,
                    "title": entry.title,
                    "year": entry.year,
                })

    return anchors_by_origin, stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write harvested anchors into "
                             "data/segment_title_attribution.json. "
                             "Existing ngram-derived anchors for the 3 "
                             "books with source MD files are preserved.")
    args = parser.parse_args(argv)

    print("loading TM...")
    tm = TranslationMemory()
    print(f"  entries: {len(tm.entries)}")
    print("loading KG...")
    kg = KnowledgeGraph()
    print(f"  nodes: {kg.G.number_of_nodes()}")

    print("\nharvesting STRICT one-per-origin anchors...")
    harvested, stats = harvest(tm, kg)

    print()
    print(f"COBISS translator containers:       {stats['containers_total']}")
    print(f"  with KG node id:                  {stats['containers_with_kg_id']}")
    print(f"  successfully anchored:            {stats['containers_anchored_anywhere']}")
    print(f"  no anchor:                        {stats['containers_no_anchor']}")
    print(f"  total anchor records (≤1 per origin per container): "
          f"{stats['anchor_records_written']}")
    if stats["no_anchor_examples"]:
        print("\nno-anchor examples:")
        for ex in stats["no_anchor_examples"]:
            print(f"  - {ex['kg_id']}  title={ex['title']!r}  year={ex['year']}")

    if not args.apply:
        print("\n(dry-run; pass --apply to merge into segment_title_attribution.json)")
        return 0

    # MERGE strategy:
    #   - Load existing file
    #   - Preserve anchors for NGRAM_PROTECTED_PREFIXES verbatim
    #   - Drop OTHER existing anchors (they came from my prior bad harvester
    #     runs and are over-dense title-token false positives)
    #   - Add the new strict one-per-origin anchors
    existing = json.loads(ATTR_PATH.read_text(encoding="utf-8"))
    shutil.copy(ATTR_PATH, str(ATTR_PATH) + ".pre-strict.bak")

    cleaned: dict[str, dict[str, list[str]]] = {}
    preserved = 0
    dropped = 0
    for origin, seg_map in existing.items():
        cleaned.setdefault(origin, {})
        for seg_idx_str, val in seg_map.items():
            cids = val if isinstance(val, list) else [val]
            keep = [
                c for c in cids
                if isinstance(c, str)
                and c.startswith("source:")
                and any(c.startswith(p) for p in NGRAM_PROTECTED_PREFIXES)
            ]
            dropped += len(cids) - len(keep)
            preserved += len(keep)
            if keep:
                cleaned[origin][seg_idx_str] = keep

    # Merge strict anchors
    added = 0
    for origin, seg_map in harvested.items():
        cleaned.setdefault(origin, {})
        for seg_idx_str, cids in seg_map.items():
            existing_at = cleaned[origin].get(seg_idx_str, [])
            for c in cids:
                if c not in existing_at:
                    existing_at.append(c)
                    added += 1
            cleaned[origin][seg_idx_str] = existing_at

    # Remove empty origin entries
    cleaned = {o: m for o, m in cleaned.items() if m}

    ATTR_PATH.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\npreserved (ngram-protected): {preserved} cid refs")
    print(f"dropped (over-dense title-phrase): {dropped} cid refs")
    print(f"added (strict one-per-origin): {added} cid refs")
    print(f"backup: {ATTR_PATH}.pre-strict.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
