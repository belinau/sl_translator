#!/usr/bin/env python3
# scripts/normalize_lineages.py
#
# Fix the lineage-as-person-name mistake: lineages must be theoretical SCHOOLS
# / strands (Lacanian psychoanalysis, Brechtian theatre, intersectional
# feminism, post-structuralism, decolonial theory…), NOT individual names. The
# person stays on the mapping's `attributed_to` edge (untouched).
#
# Reads the smol name->school map (data/quarantine/_lineage_schools.json),
# canonicalises school casing (collapses "marxism"/"Marxism"), and applies via
# the merge_lineages factory. Idempotent.
#
# Usage:  python scripts/normalize_lineages.py [--apply]

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from translate_core.knowledge_graph import KnowledgeGraph

KG_PATH = ROOT / "data" / "knowledge.db"
SCHOOLS = ROOT / "data" / "quarantine" / "_lineage_schools.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    raw = json.loads(SCHOOLS.read_text(encoding="utf-8"))  # lineage -> school
    # Canonicalise school casing: for each casefold group pick the most frequent
    # original spelling, so we don't introduce new variants.
    freq = collections.Counter(raw.values())
    canon: dict[str, str] = {}
    for school in freq:
        key = school.casefold()
        if key not in canon or freq[school] > freq[canon[key]]:
            canon[key] = school
    lineage_to_school = {lin: canon[sch.casefold()] for lin, sch in raw.items()}

    # group lineages by target school
    by_school: dict[str, list[str]] = collections.defaultdict(list)
    for lin, sch in lineage_to_school.items():
        if lin != sch:
            by_school[sch].append(lin)

    kg = KnowledgeGraph(db_path=KG_PATH)
    kg.reload_if_changed()
    total = 0
    for school, lineages in by_school.items():
        if args.apply:
            total += kg.merge_lineages(lineages, school)
        else:
            total += sum(
                1 for _, d in kg.G.nodes(data=True)
                if d.get("type") == "translation_mapping" and d.get("lineage") in lineages
            )

    print(f"name-lineages -> schools: {len(lineage_to_school)} labels -> {len(by_school)} schools")
    print(f"mappings whose lineage is rewritten: {total}")
    print("sample:", list(by_school.items())[:6])

    if not args.apply:
        print("\n(dry run — nothing written. Re-run with --apply.)")
        return
    kg.save()
    remaining = [
        l for l in kg.get_all_lineages()
        if l not in {"performance", "general", "manual", "visual-art", "curatorial-exhibition"}
    ]
    print(f"\nApplied. distinct non-generic lineages now: {len(remaining)} (schools/strands).")


if __name__ == "__main__":
    main()
