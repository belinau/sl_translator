#!/usr/bin/env python3
"""Harvest title anchors from COBISS containers against the live TM.

For every COBISS container (translator-role entry):
  - Build a TITLE TOKEN SET from (entry.title + entry.title_en).
  - Build an AUTHOR SURNAME SET from entry.agents.
  - Scan every TM segment (per origin, sorted by t_index ascending).
  - Score each segment: (title-token overlap on src+tgt) AND (author surname
    appears somewhere in src+tgt).
  - Above threshold → that (origin, seg_idx) is a title anchor for the
    container.
  - Multiple matches in different t_index regions ARE written (the walker
    supports multi-interval containers: a book translated across several
    sessions has multiple title-page occurrences in the TM).

This delivers the *coverage* the chronological-anchor walker needs: every
container present in the TM has its title anchored at the segment where
the translator typed the title page. End-of-container = the next anchor's
seg_idx minus one (per the walker's design). No explicit end markers
needed — they fall out of complete title coverage.

Outputs:
  data/segment_title_attribution.json — merged with existing entries.
  A *.bak file is saved before any write.

Scoring rules (deterministic):
  - title tokens: alphanumeric 3+ char, NFKD-normalised lowercase, from
    `title + " " + title_en + " " + subtitle`. Stop-word ish prefixes
    ("the", "a", "an", "in", "of", "and", "v", "in", "na", "z", "od")
    excluded.
  - segment tokens: same normalisation, drawn from
    `entry["source"] + " " + entry["target"]`.
  - overlap_ratio = |title_tokens ∩ seg_tokens| / |title_tokens|.
  - author_match = any surname appears as a whole-word token in the segment.
  - PASS when overlap_ratio >= MIN_TITLE_OVERLAP and (author_match OR
    overlap_ratio == 1.0 [full title match alone is enough]).

Default thresholds chosen conservatively to avoid false anchors:
  - MIN_TITLE_OVERLAP = 0.7 (i.e. 70% of title tokens present)
  - MIN_TITLE_TOKENS = 2 (skip containers whose effective title token set
    is too small to discriminate; logged so curator can handle them).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import unicodedata
import re
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
COBISS_PATH = ROOT / "data" / "personal bibliography" / "bibliography_belina.txt"

# Tokens that don't discriminate between titles
STOP_TOKENS = frozenset({
    "the", "and", "for", "with", "from", "into", "onto", "upon", "this",
    "that", "these", "those", "their", "them", "than", "then", "but", "not",
    # Slovenian short function words / prepositions and common verbs
    "ali", "kot", "kjer", "ker", "kar", "kaj", "kdo", "kdaj",
    "tako", "tudi", "samo", "tega", "tem", "tej", "tega", "ima",
    "bil", "bila", "bili", "biti", "smo", "sta", "ste", "sva",
    "jih", "jim", "jih", "njihov",
    "njegov", "njena", "njeno", "naj",
    # one and two-letter prepositions are filtered by the length check
})

MIN_TITLE_OVERLAP = 0.7
MIN_TITLE_TOKENS = 2
TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _strip(s: str) -> str:
    n = unicodedata.normalize("NFKD", s)
    return "".join(c for c in n if not unicodedata.combining(c)).lower()


def _tokenize(text: str) -> set[str]:
    return {t for t in TOKEN_RE.findall(_strip(text)) if t not in STOP_TOKENS}


def _container_id_for_entry(entry, kg: KnowledgeGraph) -> str | None:
    """Find the KG container id for this COBISS entry, matching by the
    upstream slug formula `<author>-<title[:60]>-<year>`."""
    if not entry.agents:
        return None
    author = entry.agents[0]
    author_slug = _slug_part(f"{author.last_name} {author.first_name}".strip())
    title_slug = _slug_part(entry.title[:60] if entry.title else "")
    year_slug = str(entry.year) if entry.year else ""
    parts = [p for p in (author_slug, title_slug, year_slug) if p]
    bare = "-".join(parts)
    candidate = f"source:{bare}"
    if kg.G.has_node(candidate):
        return candidate
    return None


def _slug_part(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:80]


def harvest(tm: TranslationMemory, kg: KnowledgeGraph,
            *, verbose: bool = False) -> tuple[dict, dict]:
    """Walk every COBISS translator entry; produce
    (anchors_by_origin, stats)."""
    entries = parse_cobiss_file(str(COBISS_PATH))
    print(f"  COBISS entries: {len(entries)}")

    # Pre-build per-origin sorted segment lists keyed by their original
    # seg_idx in tm.entries (the same convention load_curator_anchors uses).
    by_origin: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for e in tm.entries:
        origin = e.get("origin")
        if origin:
            by_origin[origin].append((len(by_origin[origin]), e))
    print(f"  TM origins: {list(by_origin.keys())}")
    for o, lst in by_origin.items():
        print(f"    {o}: {len(lst)} segments")

    # Build per-segment token sets ONCE (heavy work — do it once, reuse for
    # every container).
    seg_tokens_by_origin: dict[str, list[set[str]]] = {}
    for origin, lst in by_origin.items():
        seg_tokens_by_origin[origin] = [
            _tokenize((e.get("source") or "") + " " + (e.get("target") or ""))
            for _i, e in lst
        ]

    anchors_by_origin: dict[str, dict[str, list[str]]] = defaultdict(dict)
    stats: dict = {
        "containers_total": 0,
        "containers_with_kg_id": 0,
        "containers_anchored": 0,
        "containers_no_anchor": 0,
        "containers_too_few_tokens": 0,
        "total_anchors_written": 0,
        "no_anchor_examples": [],
        "too_few_tokens_examples": [],
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

        title_blob = " ".join([
            entry.title or "",
            entry.title_en or "",
            entry.subtitle or "",
        ])
        title_tokens = _tokenize(title_blob)
        if len(title_tokens) < MIN_TITLE_TOKENS:
            stats["containers_too_few_tokens"] += 1
            if len(stats["too_few_tokens_examples"]) < 5:
                stats["too_few_tokens_examples"].append({
                    "kg_id": kg_id,
                    "title": entry.title,
                    "title_en": entry.title_en,
                    "tokens": sorted(title_tokens),
                })
            continue

        # Author surname tokens (whole-word match).
        surname_tokens: set[str] = set()
        for ag in entry.agents:
            surname_tokens |= _tokenize(ag.last_name)

        found_in_any_origin = False
        for origin, lst in by_origin.items():
            for (seg_idx, _entry), seg_tokens in zip(lst, seg_tokens_by_origin[origin]):
                if not seg_tokens:
                    continue
                overlap = len(title_tokens & seg_tokens)
                if overlap == 0:
                    continue
                ratio = overlap / len(title_tokens)
                if ratio < MIN_TITLE_OVERLAP:
                    continue
                if surname_tokens and not (surname_tokens & seg_tokens):
                    if ratio < 1.0:
                        continue
                # Record the anchor (merge with any existing list).
                slot = anchors_by_origin[origin].setdefault(str(seg_idx), [])
                if kg_id not in slot:
                    slot.append(kg_id)
                    stats["total_anchors_written"] += 1
                found_in_any_origin = True

        if found_in_any_origin:
            stats["containers_anchored"] += 1
            if verbose:
                count = sum(
                    1
                    for o, segs in anchors_by_origin.items()
                    for cids in segs.values()
                    if kg_id in cids
                )
                print(f"  ANCHORED {kg_id}  in {count} segment(s)")
        else:
            stats["containers_no_anchor"] += 1
            if len(stats["no_anchor_examples"]) < 10:
                stats["no_anchor_examples"].append({
                    "kg_id": kg_id,
                    "title": entry.title,
                    "title_en": entry.title_en,
                    "year": entry.year,
                })

    return anchors_by_origin, stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write anchors into segment_title_attribution.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    print("loading TM...")
    tm = TranslationMemory()
    print(f"  entries: {len(tm.entries)}")
    print("loading KG...")
    kg = KnowledgeGraph()
    print(f"  nodes: {kg.G.number_of_nodes()}")

    print("\nharvesting title anchors from COBISS containers...")
    new_anchors, stats = harvest(tm, kg, verbose=args.verbose)

    print()
    print("─" * 60)
    print(f"COBISS translator containers:  {stats['containers_total']}")
    print(f"  with KG node id:             {stats['containers_with_kg_id']}")
    print(f"  successfully anchored:       {stats['containers_anchored']}")
    print(f"  no anchor found:             {stats['containers_no_anchor']}")
    print(f"  title too few tokens:        {stats['containers_too_few_tokens']}")
    print(f"  total anchor records:        {stats['total_anchors_written']}")
    print()
    if stats["no_anchor_examples"]:
        print("no-anchor examples (curator may need to handle):")
        for ex in stats["no_anchor_examples"]:
            print(f"  - {ex['kg_id']}")
            print(f"      title:    {ex['title'][:70]!r}")
            print(f"      title_en: {(ex.get('title_en') or '')[:70]!r}")
            print(f"      year:     {ex['year']}")
    if stats["too_few_tokens_examples"]:
        print("\ntitle-too-short examples:")
        for ex in stats["too_few_tokens_examples"]:
            print(f"  - {ex['kg_id']}  title={ex['title']!r}  tokens={ex['tokens']}")

    if not args.apply:
        print("\n(dry-run; pass --apply to merge into segment_title_attribution.json)")
        return 0

    # Merge with existing curator-set entries.
    existing = json.loads(ATTR_PATH.read_text(encoding="utf-8"))
    shutil.copy(ATTR_PATH, str(ATTR_PATH) + ".bak")

    merged_anchors_added = 0
    for origin, seg_map in new_anchors.items():
        existing.setdefault(origin, {})
        for seg_idx_str, cids in seg_map.items():
            current = existing[origin].get(seg_idx_str)
            if current is None:
                existing[origin][seg_idx_str] = list(cids)
                merged_anchors_added += len(cids)
            elif isinstance(current, list):
                for c in cids:
                    if c not in current:
                        current.append(c)
                        merged_anchors_added += 1
            elif isinstance(current, str):
                if current not in cids:
                    existing[origin][seg_idx_str] = [current] + list(cids)
                    merged_anchors_added += len(cids)

    ATTR_PATH.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nattribution file updated: +{merged_anchors_added} cid references")
    print(f"  backup: {ATTR_PATH}.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
