#!/usr/bin/env python3
"""Wire curator-validated agent→container mentions onto agent nodes.

`data/quarantine/_agent_container_mapping.json` is a curator-built dict:

    { "agent:<agent_id>": ["source:<container_id>", ...], ... }

For each agent that exists in the KG, set `mention_containers` field to
the list of containers it appears in. Per ontology §2.5, agent nodes
already have an optional `mention_segments` (segment-level) field; this
adds the COARSER container-level summary that the curator built (the
two represent different granularities of the same evidence — neither
overrides the other).

For agents whose role is one of {author, artist, choreographer,
composer, director, editor, performer, dancer, translator}, ALSO wire
the appropriate ontology §3.2 edge per role:
  - author/artist/choreographer/composer/director → written_by
  - editor → edited_by
  - performer/dancer → performed_by
  - translator → translated_by

For role="agent" (the catch-all, 75% of in-KG agents) we ONLY populate
the field. The ontology does not have a generic "mentioned_in" edge;
adding one would violate the §3 "exactly 13 edge relations" invariant.
The field is queryable for downstream consumers.

Idempotent: existing edges/fields are preserved.
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

MAPPING_PATH = ROOT / "data" / "quarantine" / "_agent_container_mapping.json"

ROLE_TO_EDGE_WRITER = {
    # role -> name of KnowledgeGraph factory method
    "author":        "link_written_by",
    "artist":        "link_written_by",
    "choreographer": "link_written_by",
    "composer":      "link_written_by",
    "director":      "link_written_by",
    "editor":        "link_edited_by",
    "performer":     "link_performed_by",
    "dancer":        "link_performed_by",
    "translator":    "link_translated_by",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Mutate KG and save")
    args = parser.parse_args(argv)

    print("loading KG...")
    kg = KnowledgeGraph()
    print(f"  nodes: {kg.G.number_of_nodes()}, edges: {kg.G.number_of_edges()}")

    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    print(f"\nagent records:                       {len(mapping)}")

    agents_in_kg = 0
    fields_set = 0
    pairs_total = 0
    edges_to_write = 0
    edges_actually_written = 0
    edges_already_present = 0

    for agent_id, container_list in mapping.items():
        if not kg.G.has_node(agent_id):
            continue
        agents_in_kg += 1
        valid_containers = [
            c for c in container_list
            if isinstance(c, str)
            and c.startswith("source:")
            and kg.G.has_node(c)
        ]
        if not valid_containers:
            continue

        agent_data = kg.G.nodes[agent_id]
        existing = agent_data.get("mention_containers") or []
        merged = list(dict.fromkeys(list(existing) + valid_containers))
        if merged != existing:
            if args.apply:
                agent_data["mention_containers"] = merged
            fields_set += 1

        role = agent_data.get("role", "")
        writer_method = ROLE_TO_EDGE_WRITER.get(role)
        if writer_method:
            writer = getattr(kg, writer_method)
            for container_id in valid_containers:
                pairs_total += 1
                if kg.G.has_edge(container_id, agent_id):
                    edges_already_present += 1
                    continue
                if args.apply:
                    if writer(container_id, agent_id):
                        edges_actually_written += 1
                edges_to_write += 1

    print()
    print(f"agents from mapping that exist in KG: {agents_in_kg}")
    print(f"agents getting mention_containers populated: {fields_set}")
    print(f"role-typed (agent, container) edge pairs: {pairs_total}")
    print(f"  already present (idempotent):       {edges_already_present}")
    print(f"  new edges {'written' if args.apply else 'would write'}: "
          f"{edges_actually_written if args.apply else edges_to_write}")

    if args.apply:
        kg.save()
        print(f"\nKG saved. Edge count: {kg.G.number_of_edges()}")
    else:
        print("\n(dry-run; pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
