"""Forensic audit: read-only inspection of the live KG.

Answers:
  1. Distribution of source_text nodes by (kind, provenance, project_type)
  2. Orphan vs live source_text classification
  3. 19-anchor-cid existence & classification
  4. provenance=cobiss_personal evidence
"""
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.entity_extraction._slug import _slugify
from translate_core.cobiss_parser import parse_cobiss_file
from translate_core.cobiss_classifier import classify_entry

KG_PATH = ROOT / "data" / "knowledge.db"
ATTR_PATH = ROOT / "data" / "segment_title_attribution.json"
COBISS_PATH = ROOT / "data" / "personal bibliography" / "bibliography_belina.txt"

def _make_agent_id(last_name: str, first_name: str) -> str:
    parts = [last_name]
    if first_name:
        parts.append(first_name)
    return _slugify(" ".join(parts))

def _make_source_id(title: str, year, author_slug: str) -> str:
    parts = []
    if author_slug:
        parts.append(author_slug)
    if title:
        parts.append(title[:60])
    if year:
        parts.append(str(year))
    return _slugify("-".join(parts))

LIVE_RELATIONS = {
    "cited_in", "translated_by", "written_by",
    "instantiated_in", "translation_published_by",
    "published_by", "edited_by",
}

def main() -> None:
    print(f"Loading KG from {KG_PATH} ...")
    kg = KnowledgeGraph(db_path=KG_PATH)
    G = kg.G

    # --- 1. source_text distribution ---
    source_nodes = [(n, d) for n, d in G.nodes(data=True) if d.get("type") == "source_text"]
    total_sources = len(source_nodes)
    print(f"\nTotal source_text nodes: {total_sources}")

    kind_counter: Counter = Counter()
    prov_counter: Counter = Counter()
    ptype_counter: Counter = Counter()
    kind_x_prov: Counter = Counter()
    for _, d in source_nodes:
        k = d.get("kind")
        p = d.get("provenance")
        pt = d.get("project_type")
        kind_counter[k] += 1
        prov_counter[p] += 1
        ptype_counter[pt] += 1
        kind_x_prov[(k, p)] += 1

    print("\n--- kind distribution ---")
    for k, c in kind_counter.most_common():
        print(f"  kind={k!r:30s}  count={c}")
    print("\n--- provenance distribution ---")
    for p, c in prov_counter.most_common():
        print(f"  provenance={p!r:30s}  count={c}")
    print("\n--- project_type distribution (top 20) ---")
    for pt, c in ptype_counter.most_common(20):
        print(f"  project_type={pt!r:30s}  count={c}")
    print("\n--- kind x provenance ---")
    for (k, p), c in sorted(kind_x_prov.items(), key=lambda x: -x[1])[:20]:
        print(f"  kind={k!r:25s}  prov={p!r:25s}  count={c}")

    # --- 2. orphan vs live ---
    live_count = 0
    orphan_count = 0
    live_samples = []
    orphan_samples = []
    relation_edge_counter: Counter = Counter()
    for nid, d in source_nodes:
        in_edges = list(G.in_edges(nid, data=True))
        out_edges = list(G.out_edges(nid, data=True))
        live = False
        for _, _, ed in in_edges:
            rel = ed.get("relation")
            if rel in LIVE_RELATIONS:
                live = True
                relation_edge_counter[("in", rel)] += 1
        for _, _, ed in out_edges:
            rel = ed.get("relation")
            if rel in LIVE_RELATIONS:
                live = True
                relation_edge_counter[("out", rel)] += 1
        if live:
            live_count += 1
            if len(live_samples) < 5:
                live_samples.append((nid, d.get("title") or d.get("title_orig") or d.get("title_translation") or "(no title)"))
        else:
            orphan_count += 1
            if len(orphan_samples) < 5:
                orphan_samples.append((nid, d.get("title") or d.get("title_orig") or d.get("title_translation") or "(no title)"))

    print("\n--- orphan vs live ---")
    print(f"  live source_text:   {live_count}")
    print(f"  orphan source_text: {orphan_count}")
    print("  edges by relation (live-class only):")
    for (direction, rel), c in sorted(relation_edge_counter.items(), key=lambda x: -x[1]):
        print(f"    {direction:3s} {rel:30s} {c}")
    print("  live samples:")
    for nid, t in live_samples:
        print(f"    {nid} :: {t[:70]}")
    print("  orphan samples:")
    for nid, t in orphan_samples:
        print(f"    {nid} :: {t[:70]}")

    # --- 3. 19-anchor-cid forensics ---
    with open(ATTR_PATH, "r", encoding="utf-8") as fh:
        attr_data = json.load(fh)
    anchor_cids = set()
    for idx_map in attr_data.values():
        for cid_list in idx_map.values():
            for c in cid_list:
                anchor_cids.add(c)
    print(f"\nDistinct anchor cids in attribution JSON: {len(anchor_cids)}")

    # Build COBISS slug set
    entries = parse_cobiss_file(str(COBISS_PATH))
    cobiss_slugs = set()
    cobiss_slug_to_entry = {}
    for e in entries:
        ptype, belina_role = classify_entry(e)
        primary_author_slug = ""
        if e.agents:
            primary_author_slug = _make_agent_id(e.agents[0].last_name, e.agents[0].first_name)
        sid = _make_source_id(e.title, e.year, primary_author_slug)
        node_id = f"source:{sid}"
        cobiss_slugs.add(node_id)
        cobiss_slug_to_entry[node_id] = (e.entry_number, e.title[:60], ptype, belina_role)

    # Classify each anchor cid
    print("\n--- Anchor cid table ---")
    print(f"{'cid':<70} {'in_COBISS':<10} {'in_KG':<10} classification")
    rows = []
    for cid in sorted(anchor_cids):
        in_cobiss = cid in cobiss_slugs
        in_kg = G.has_node(cid)
        if in_cobiss and in_kg:
            cls = "cobiss-matched"
        elif in_cobiss and not in_kg:
            cls = "cobiss-not-ingested"
        elif not in_cobiss and in_kg:
            cls = "kg-orphan-non-cobiss"
        else:
            cls = "curator-extra"
        rows.append((cid, in_cobiss, in_kg, cls))
        print(f"{cid:<70} {str(in_cobiss):<10} {str(in_kg):<10} {cls}")

    # --- 4. cobiss_personal provenance evidence ---
    cobiss_prov_nodes = []
    for n, d in G.nodes(data=True):
        if d.get("provenance") == "cobiss_personal":
            cobiss_prov_nodes.append((n, d.get("type")))
    print(f"\nNodes with provenance=cobiss_personal: {len(cobiss_prov_nodes)}")
    type_counter = Counter(t for _, t in cobiss_prov_nodes)
    for t, c in type_counter.most_common():
        print(f"  type={t!r:30s}  count={c}")

    # --- 5. spot-check COBISS slug alignment for 6 anchor-shaped cids ---
    target_cids = [
        "source:bester-vid-za-slavo",
        "source:pregl-arjan-na-platnu",
        "source:dizevic-alma-zivljenja-neznanih-cuvajev",
        "source:gioti-nefeli-kaj-se-je-zgodilo-s-prakso",
        "source:simicic-mihanovic-zrinka-somahut",
        "source:walker-adin-prodaja-disciplini",
    ]
    print("\n--- COBISS slug spot-check ---")
    matched = 0
    for tc in target_cids:
        if tc in cobiss_slug_to_entry:
            n, title, ptype, role = cobiss_slug_to_entry[tc]
            print(f"  MATCH {tc:<60}  COBISS#{n} ptype={ptype} role={role} title={title!r}")
            matched += 1
        else:
            print(f"  MISS  {tc}")
    print(f"  Matched {matched}/{len(target_cids)}")

    # --- 6. KG existence summary for 19 cids ---
    existing_in_kg = [c for c, _, ik, _ in rows if ik]
    print(f"\n--- {len(existing_in_kg)} anchor cids that DO exist as KG nodes ---")
    for cid in existing_in_kg:
        d = G.nodes[cid]
        print(f"  {cid}")
        print(f"    type={d.get('type')} kind={d.get('kind')} provenance={d.get('provenance')} project_type={d.get('project_type')}")
        print(f"    title={d.get('title')!r} title_orig={d.get('title_orig')!r} title_translation={d.get('title_translation')!r}")


if __name__ == "__main__":
    main()
