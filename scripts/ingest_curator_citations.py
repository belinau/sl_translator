#!/usr/bin/env python3
"""Wire curator-validated citation → container mappings into the KG.

`data/quarantine/_citation_to_container.json` is a curator-built dict:

    { "source:<cited_work_id>": ["source:<container_id>", ...], ... }

For each pair, write a `(cited_work) -[cited_in]-> (container)` edge via
`KnowledgeGraph.link_cited_in` (the ontology §3.2 authoritative writer).
Idempotent: existing edges are not duplicated.

Agents that appear in containers are connected TRANSITIVELY:
  agent --written_by--> cited_work --cited_in--> container
so wiring cited_in is sufficient — no separate agent-container edge is
required by ontology §3.2.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.knowledge_graph import KnowledgeGraph

CITATION_PATH = ROOT / "data" / "quarantine" / "_citation_to_container.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write cited_in edges to data/knowledge.db")
    args = parser.parse_args(argv)

    print("loading KG...")
    kg = KnowledgeGraph()
    print(f"  nodes: {kg.G.number_of_nodes()}, edges: {kg.G.number_of_edges()}")

    mapping = json.loads(CITATION_PATH.read_text(encoding="utf-8"))
    print(f"\nciter records: {len(mapping)}")

    pairs_total = 0
    pairs_both_in_kg = 0
    new_edges = 0
    already_present = 0
    missing_cited = 0
    missing_container = 0

    for cited_id, container_list in mapping.items():
        if not isinstance(container_list, list):
            continue
        for container_id in container_list:
            pairs_total += 1
            if not kg.G.has_node(cited_id):
                missing_cited += 1
                continue
            if not kg.G.has_node(container_id):
                missing_container += 1
                continue
            pairs_both_in_kg += 1
            if kg.G.has_edge(cited_id, container_id):
                already_present += 1
                continue
            if args.apply:
                if kg.link_cited_in(cited_id, container_id):
                    new_edges += 1
            else:
                new_edges += 1

    print()
    print(f"total (cited, container) pairs:       {pairs_total}")
    print(f"  both nodes in KG:                   {pairs_both_in_kg}")
    print(f"  edges already present (idempotent): {already_present}")
    print(f"  new edges {'written' if args.apply else 'would write'}: {new_edges}")
    print(f"  cited node missing from KG:         {missing_cited}")
    print(f"  container node missing from KG:     {missing_container}")

    if args.apply:
        kg.save()
        print(f"\nKG saved. New edge count: {kg.G.number_of_edges()}")
    else:
        print("\n(dry-run; pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
