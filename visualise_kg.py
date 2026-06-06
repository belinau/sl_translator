# visualise_kg.py
#
# Generates FOUR premium, high-performance HTML5 Canvas-based visualizations.
# Optimized for portrait displays with floating spatial headers, collapsing
# physics drawers, and bottom-third metadata inspector panels.
#
#   1. kg_canvas_provenance.html   - Dense intellectual lineage (Concepts, Texts, Agents)
#   2. kg_canvas_terms.html        - Clean bilingual term dictionary mapping
#   3. kg_canvas_agents_works.html - Sleek Agent & Works Cited mapping
#   4. kg_canvas_artworks.html     - Artworks, Performances & Their Creators
#
#

import argparse
import json
import math
import pathlib
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Set, Tuple

# ---------------------------------------------------------------------------
# Setup CLI and Config Fallbacks
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Generate canvas-driven, high-performance multi-view visualizations.")
parser.add_argument("kg_path", nargs="?", default=None, help="Path to your knowledge_graph.json")
parser.add_argument("--limit", type=int, default=3000, help="Node limit raised for canvas rendering")
parser.add_argument("--min-freq", type=int, default=1, dest="min_freq", help="Minimum frequency filter for terms in View 2")
parser.add_argument("--lang", default=None, help="Filter primary terms in View 2 to a specific language (e.g. en, sl)")
parser.add_argument("--out-dir", default=".", dest="out_dir", help="Output directory for generated HTML files")
parser.add_argument("--prefix", default="kg_canvas_", help="Output filename prefix (e.g. 'kg_canvas_')")

args = parser.parse_args()

# Load DB Path
if args.kg_path:
    kg_path = pathlib.Path(args.kg_path)
else:
    try:
        import config
        kg_path = pathlib.Path(config.KG_DB_PATH)
    except ImportError:
        kg_path = pathlib.Path("data/knowledge_graph.json")

if not kg_path.exists():
    print(f"[ERROR] Database file not found at: {kg_path}", file=sys.stderr)
    sys.exit(1)

try:
    raw_data = json.loads(kg_path.read_text(encoding="utf-8"))
except Exception as e:
    print(f"[ERROR] Failed to read or parse KG: {e}", file=sys.stderr)
    sys.exit(1)

ALL_NODES = raw_data.get("nodes", [])
ALL_EDGES = raw_data.get("edges", [])
node_by_id = {n["id"]: n for n in ALL_NODES}

print(f"[KG Loader] Loaded {len(ALL_NODES)} nodes and {len(ALL_EDGES)} edges.")
def compute_physics_defaults(n_nodes: int, n_edges: int) -> Dict[str, float]:
    """Choose sane force-graph parameters from graph density.
    Heuristic: dense graphs (high edges/node) need stronger repulsion and
    weaker link strength so the layout doesn't collapse into a hairball.
    Sparse / tree-like graphs (V5 lineage→concepts) need stronger links so
    leaves orbit their hub instead of drifting away.
    """
    avg_deg = (2.0 * n_edges) / n_nodes if n_nodes else 0.0
    if avg_deg >= 20:        # V1 provenance — extremely dense
        return {"charge": -2400, "link_dist": 380, "link_str": 0.06,
                "collide": 28, "velocity": 0.55, "center": 0.04,
                "alpha_decay": 0.010}
    if avg_deg >= 10:        # V2 terms, V6 lineage-works
        return {"charge": -1600, "link_dist": 300, "link_str": 0.10,
                "collide": 24, "velocity": 0.50, "center": 0.06,
                "alpha_decay": 0.012}
    if avg_deg >= 5:         # V4 artworks
        return {"charge": -1100, "link_dist": 240, "link_str": 0.18,
                "collide": 22, "velocity": 0.45, "center": 0.08,
                "alpha_decay": 0.014}
    if avg_deg >= 2:         # V3 agents & works
        return {"charge": -800,  "link_dist": 200, "link_str": 0.30,
                "collide": 20, "velocity": 0.42, "center": 0.10,
                "alpha_decay": 0.018}
    # Tree-like / sparse — V5 lineage→concepts (single-attribution)
    return     {"charge": -1400, "link_dist": 260, "link_str": 0.45,
                "collide": 20, "velocity": 0.40, "center": 0.12,
                "alpha_decay": 0.022}
def physics_replacements(defaults: Dict[str, float]) -> Dict[str, str]:
    """Map placeholder name → string for HTML/JS template substitution."""
    return {
        "__PHYS_CHARGE__":      str(int(defaults["charge"])),
        "__PHYS_LINK_DIST__":   str(int(defaults["link_dist"])),
        "__PHYS_LINK_STR__":    f'{defaults["link_str"]:.2f}',
        "__PHYS_COLLIDE__":     str(int(defaults["collide"])),
        "__PHYS_VELOCITY__":    f'{defaults["velocity"]:.2f}',
        "__PHYS_CENTER__":      f'{defaults["center"]:.2f}',
        "__PHYS_ALPHA_DECAY__": f'{defaults["alpha_decay"]:.3f}',
    }
# Helper to prune subgraphs to stay within limits.
# Type-aware: protects under-represented node types (source_text, agent,
# institution) from being crushed by high-degree concept/term nodes in V1.
# Reserved quotas are applied first; the remainder is filled by global
# degree ranking from whatever survives.
TYPE_QUOTA_RATIOS: Dict[str, float] = {
    "source_text": 0.20,   # at least 20% of slots for bibliography nodes
    "agent":       0.15,
    "institution": 0.05,
}
def prune_subgraph(nodes: List[Dict], edges: List[Dict], max_nodes: int) -> Tuple[List[Dict], List[Dict]]:
    if len(nodes) <= max_nodes:
        return nodes, edges
    degree: Counter = Counter()
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    # Group nodes by type and sort each group by degree.
    by_type: Dict[str, List[Dict]] = {}
    for n in nodes:
        by_type.setdefault(n.get("type", "_other_"), []).append(n)
    for t in by_type:
        by_type[t].sort(key=lambda n: degree[n["id"]], reverse=True)
    keep_ids: set = set()
    keep_nodes: List[Dict] = []
    # 1. Reserve quotas for under-represented bibliography types.
    for type_name, ratio in TYPE_QUOTA_RATIOS.items():
        quota = int(max_nodes * ratio)
        for n in by_type.get(type_name, [])[:quota]:
            if n["id"] not in keep_ids:
                keep_ids.add(n["id"])
                keep_nodes.append(n)
    # 2. Fill the remainder with the highest-degree nodes across all types.
    remaining_slots = max_nodes - len(keep_nodes)
    if remaining_slots > 0:
        leftovers = sorted(
            (n for n in nodes if n["id"] not in keep_ids),
            key=lambda n: degree[n["id"]],
            reverse=True,
        )
        for n in leftovers[:remaining_slots]:
            keep_ids.add(n["id"])
            keep_nodes.append(n)
    keep_edges = [e for e in edges if e["source"] in keep_ids and e["target"] in keep_ids]
    return keep_nodes, keep_edges
PALETTE = {
    "concept": "#c084fc",            # Soft Purple
    "agent": "#fb923c",              # Warm Orange
    "source_text": "#22d3ee",        # Cyber Cyan
    "institution": "#f472b6",        # Rose Pink
    "artwork": "#a855f7",           # Purple (artworks)
    "performance": "#f43f5e",        # Rose-red (performances)
    "term": "#818cf8",              # Indigo (generic term fallback)
    "term_en": "#3b82f6",           # Ocean Blue
    "term_sl": "#10b981",           # Emerald Green
    "translation_mapping": "#a78bfa", # Violet (reified edge node)
    "lineage": "#fbbf24",            # Gold (synthesized lineage hub)
}

# Edge-relation color mapping for visual differentiation
EDGE_COLORS = {
    "written_by":       "#fb923c",   # Orange (agent-author)
    "translated_by":    "#22d3ee",   # Cyan (agent-translator)
    "edited_by":        "#fbbf24",   # Amber (agent-editor)
    "performed_by":     "#f472b6",   # Pink (agent-performer)
    "published_by":     "#34d399",   # Green (institution-publisher)
    "sl_published_by":  "#6ee7b7",   # Light green (SL publisher)
    "hosted_by":        "#a78bfa",   # Purple (institution-host)
    "cited_in":         "#94a3b8",   # Slate (citation chain)
    "appears_in":       "#cbd5e1",   # Light slate (chapter→book)
    "instantiates_concept": "#c084fc",  # Purple (term→concept)
    "translates_to":    "#ec4899",   # Pink (term↔term / projected)
    "instantiated_in":  "#38bdf8",   # Sky (mapping→text, projected)
    "attributed_to":    "#fb923c",   # Orange (mapping→agent, projected)
    "contributed_to":   "#22d3ee",   # Cyan (projected agent→text)
    # Rhizomatic concept edges
    "extends":          "#c084fc",
    "critiques":        "#e879f9",
    "redefines":        "#818cf8",
    "reappropriates":   "#f0abfc",
    "related_to":       "#d8b4fe",
    # Lineage-hub aggregation edges (V5/V6 synthetic)
    "in_lineage":        "#fbbf24",
    "work_in_lineage":   "#22d3ee",
    "agent_in_lineage":  "#fb923c",
}

# Which relations count as "special" (highlighted with particles)
SPECIAL_RELATIONS = frozenset({
    "translates_to", "written_by", "translated_by", "edited_by",
    "performed_by", "cited_in", "appears_in",
    "contributed_to", "attributed_to", "instantiated_in",
})

# ---------------------------------------------------------------------------
# Map Pre-building Index (O(E) Complexity for performance)
# ---------------------------------------------------------------------------
term_to_concept = {}
for e in ALL_EDGES:
    if e.get("relation") == "instantiates_concept":
        term_to_concept[e["source"]] = e["target"]

mapping_nodes = {n["id"]: n for n in ALL_NODES if n.get("type") == "translation_mapping"}

mapping_sources = {}
mapping_targets = {}
mapping_texts = defaultdict(list)
mapping_agents = defaultdict(list)

for e in ALL_EDGES:
    rel = e.get("relation")
    s, t = e["source"], e["target"]
    if rel == "has_mapping":
        mapping_sources[t] = s
    elif rel == "maps_to":
        mapping_targets[s] = t
    elif rel == "instantiated_in":
        mapping_texts[s].append(t)
    elif rel == "attributed_to":
        mapping_agents[s].append(t)

# ===========================================================================
# VIEW 1 PROCESSING: Provenance & Intellectual Lineage Projection
# ===========================================================================
print("[Pipeline] Processing View 1: Concepts, Agents, Texts & Institutions (With Projected Links)...")
V1_ALLOWED_TYPES = {"concept", "agent", "source_text", "institution"}
v1_nodes_raw = [n for n in ALL_NODES if n.get("type") in V1_ALLOWED_TYPES]
v1_ids = {n["id"] for n in v1_nodes_raw}

v1_edges_dict = {}

def add_v1_edge(s, t, rel, **kwargs):
    if not s or not t or s == t or s not in v1_ids or t not in v1_ids:
        return
    key = (s, t, rel)
    if key not in v1_edges_dict:
        v1_edges_dict[key] = {
            "source": s,
            "target": t,
            "relation": rel,
            **kwargs
        }

# Import all direct edges between V1 node types
BIBLIOGRAPHY_RELATIONS = {
    "written_by", "translated_by", "edited_by", "performed_by",
    "published_by", "sl_published_by", "hosted_by",
    "cited_in", "appears_in",
}
RHIZOMATIC_RELATIONS = {"extends", "critiques", "redefines", "reappropriates", "related_to"}
V1_DIRECT_RELATIONS = BIBLIOGRAPHY_RELATIONS | RHIZOMATIC_RELATIONS

for e in ALL_EDGES:
    s, t = e["source"], e["target"]
    rel = e.get("relation")
    if s in v1_ids and t in v1_ids and rel in V1_DIRECT_RELATIONS:
        add_v1_edge(s, t, rel, confidence=e.get("confidence", 0.5), verified=e.get("verified", False))

# Perform Providential Link Projection:
# Connect Concepts directly to the Works/Agents that discussed/translated their terms
for m_id, m_node in mapping_nodes.items():
    src_term = mapping_sources.get(m_id)
    tgt_term = mapping_targets.get(m_id)

    if not src_term or not tgt_term:
        continue

    cu = term_to_concept.get(src_term)
    cv = term_to_concept.get(tgt_term)

    conf = m_node.get("confidence", 0.5)
    ver = m_node.get("verified", False)
    lineage = m_node.get("lineage", "")
    # Concept to Concept (Projected Translation)
    if cu and cv:
        add_v1_edge(cu, cv, "translates_to", confidence=conf, verified=ver, lineage=lineage)
    # Concepts to Source Texts
    texts = mapping_texts.get(m_id, [])
    for text_id in texts:
        if cu: add_v1_edge(cu, text_id, "instantiated_in", confidence=conf, verified=ver, lineage=lineage)
        if cv: add_v1_edge(cv, text_id, "instantiated_in", confidence=conf, verified=ver, lineage=lineage)
    # Concepts to Translating Agents
    agents = mapping_agents.get(m_id, [])
    for agent_id in agents:
        if cu: add_v1_edge(cu, agent_id, "attributed_to", confidence=conf, verified=ver, lineage=lineage)
        if cv: add_v1_edge(cv, agent_id, "attributed_to", confidence=conf, verified=ver, lineage=lineage)

v1_edges_raw = list(v1_edges_dict.values())
v1_nodes_pruned, v1_edges_pruned = prune_subgraph(v1_nodes_raw, v1_edges_raw, args.limit)

degree_v1 = Counter()
for e in v1_edges_pruned:
    degree_v1[e["source"]] += 1
    degree_v1[e["target"]] += 1

def _v1_label(n):
    """Best display label for V1 provenance nodes."""
    ntype = n["type"]
    if ntype == "source_text":
        return n.get("title") or n.get("title_en") or n["id"]
    if ntype == "agent":
        return n.get("name") or n["id"]
    if ntype == "institution":
        return n.get("name") or n["id"]
    # concept
    return n.get("label") or n["id"]

d3_nodes_v1 = []
for n in v1_nodes_pruned:
    ntype = n["type"]
    label = _v1_label(n)
    color = PALETTE.get(ntype, "#64748b")
    size = max(6, min(22, 6 + math.log1p(degree_v1[n["id"]]) * 3.5))

    d3_nodes_v1.append({
        "id": n["id"],
        "label": label,
        "type": ntype,
        "domain": n.get("domain", ""),
        "definition": n.get("definition", ""),
        "year": n.get("year", ""),
        "role": n.get("role", ""),
        "all_roles": n.get("all_roles", []),
        "kind": n.get("kind", ""),
        "project_type": n.get("project_type", ""),
        "color": color,
        "size": size,
        "verified": n.get("verified", False),
        "connections": degree_v1[n["id"]]
    })

d3_edges_v1 = []
for e in v1_edges_pruned:
    rel = e["relation"]
    d3_edges_v1.append({
        "source": e["source"],
        "target": e["target"],
        "relation": rel,
        "confidence": e.get("confidence", 0.5),
        "verified": e.get("verified", False),
        "lineage": e.get("lineage", "")
    })
# Compute per-node lineage aggregation for V1 (which lineages connect each node)
v1_node_lineages = defaultdict(set)
for e in v1_edges_pruned:
    lin = e.get("lineage", "")
    if lin:
        v1_node_lineages[e["source"]].add(lin)
        v1_node_lineages[e["target"]].add(lin)
for n in d3_nodes_v1:
    n["lineages"] = sorted(v1_node_lineages.get(n["id"], []))
# Collect available lineages for V1 filter dropdown
available_lineages_v1 = sorted(set(e["lineage"] for e in d3_edges_v1 if e.get("lineage")))
print(f"  Result View 1: {len(d3_nodes_v1)} nodes, {len(d3_edges_v1)} edges.")
# ===========================================================================
print("[Pipeline] Processing View 2: Bilingual Term Space...")

base_terms = [
    n for n in ALL_NODES
    if n.get("type") == "term"
    and n.get("frequency", 1) >= args.min_freq
]
if args.lang:
    base_terms = [t for t in base_terms if t.get("lang") == args.lang]

base_terms = sorted(base_terms, key=lambda x: x.get("frequency", 0), reverse=True)[:args.limit]
v2_ids = {t["id"] for t in base_terms}

# Pull in concept nodes connected to these terms
adjacent_nodes = []
for e in ALL_EDGES:
    rel = e.get("relation")
    s, t = e["source"], e["target"]
    if rel == "instantiates_concept":
        if s in v2_ids and t not in v2_ids:
            if t in node_by_id:
                adjacent_nodes.append(node_by_id[t])
                v2_ids.add(t)

v2_nodes_raw = base_terms + adjacent_nodes
# Collapse translation mappings directly on term edges
v2_edges_raw = []
for m_id, m_node in mapping_nodes.items():
    src = mapping_sources.get(m_id)
    tgt = mapping_targets.get(m_id)

    if src and tgt and src in v2_ids and tgt in v2_ids:
        v2_edges_raw.append({
            "source": src,
            "target": tgt,
            "relation": "translates_to",
            "confidence": m_node.get("confidence", 0.5),
            "verified": m_node.get("verified", False),
            "lineage": m_node.get("lineage", "general"),
            "register": m_node.get("register", "academic"),
            "gloss": m_node.get("gloss", ""),
            "year": m_node.get("year", "")
        })

for e in ALL_EDGES:
    rel = e.get("relation")
    if rel in ("instantiates_concept", "translates_to") and e["source"] in v2_ids and e["target"] in v2_ids:
        v2_edges_raw.append(e)

v2_nodes_pruned, v2_edges_pruned = prune_subgraph(v2_nodes_raw, v2_edges_raw, args.limit)

degree_v2 = Counter()
for e in v2_edges_pruned:
    degree_v2[e["source"]] += 1
    degree_v2[e["target"]] += 1

d3_nodes_v2 = []
for n in v2_nodes_pruned:
    ntype = n["type"]
    label = n.get("display_form") or n.get("term") or n.get("label") or n["id"]
    if ntype == "concept":
        color = PALETTE["concept"]
    elif ntype == "term":
        color = PALETTE.get("term_en" if n.get("lang") == "en" else "term_sl", PALETTE["term"])
    else:
        color = PALETTE.get(ntype, "#64748b")
    size = max(5, min(20, 5 + math.log1p(n.get("frequency", 1)) * 3)) if ntype != "concept" else 8

    d3_nodes_v2.append({
        "id": n["id"],
        "label": label,
        "type": ntype,
        "lang": n.get("lang", ""),
        "freq": n.get("frequency", 1),
        "domain": n.get("domain", ""),
        "definition": n.get("definition", ""),
        "color": color,
        "size": size,
        "verified": n.get("verified", False),
        "connections": degree_v2[n["id"]]
    })

d3_edges_v2 = []
for e in v2_edges_pruned:
    rel = e.get("relation")
    d3_edges_v2.append({
        "source": e["source"],
        "target": e["target"],
        "relation": rel,
        "confidence": e.get("confidence", 0.5),
        "verified": e.get("verified", False),
        "lineage": e.get("lineage", ""),
        "register": e.get("register", ""),
        "gloss": e.get("gloss", ""),
        "year": e.get("year", "")
    })

print(f"  Result View 2: {len(d3_nodes_v2)} nodes, {len(d3_edges_v2)} edges.")

# ===========================================================================
# VIEW 3 PROCESSING: Agents, Works & Institutions (With Projected Connections)
# ===========================================================================
V3_ALLOWED_TYPES = {"agent", "source_text", "institution"}
v3_nodes_raw = [n for n in ALL_NODES if n.get("type") in V3_ALLOWED_TYPES]
v3_ids = {n["id"] for n in v3_nodes_raw}

v3_edges_dict = {}

def add_v3_edge(s, t, rel, **kwargs):
    if not s or not t or s == t or s not in v3_ids or t not in v3_ids:
        return
    key = (s, t, rel)
    if key not in v3_edges_dict:
        v3_edges_dict[key] = {
            "source": s,
            "target": t,
            "relation": rel,
            **kwargs
        }

# Import all direct bibliography edges between V3 node types
V3_BIBLIOGRAPHY_RELATIONS = {
    "written_by", "translated_by", "edited_by", "performed_by",
    "published_by", "sl_published_by", "hosted_by",
    "cited_in", "appears_in",
}
for e in ALL_EDGES:
    s, t = e["source"], e["target"]
    rel = e.get("relation")
    if s in v3_ids and t in v3_ids and rel in V3_BIBLIOGRAPHY_RELATIONS:
        add_v3_edge(s, t, rel, confidence=e.get("confidence", 0.5), verified=e.get("verified", False))

# Perform Agent-to-Work Link Projection:
# Links Agents directly to Source Texts they are attributed to via metadata translation mappings
for m_id, m_node in mapping_nodes.items():
    texts = mapping_texts.get(m_id, [])
    agents = mapping_agents.get(m_id, [])
    conf = m_node.get("confidence", 0.5)
    ver = m_node.get("verified", False)
    lineage = m_node.get("lineage", "")
    for agent_id in agents:
        for text_id in texts:
            add_v3_edge(agent_id, text_id, "contributed_to", confidence=conf, verified=ver, lineage=lineage)

v3_edges_raw = list(v3_edges_dict.values())
v3_nodes_pruned, v3_edges_pruned = prune_subgraph(v3_nodes_raw, v3_edges_raw, args.limit)

degree_v3 = Counter()
for e in v3_edges_pruned:
    degree_v3[e["source"]] += 1
    degree_v3[e["target"]] += 1

def _v3_label(n):
    """Best display label for V3 agents/works/institutions nodes."""
    ntype = n["type"]
    if ntype == "source_text":
        return n.get("title") or n.get("title_en") or n["id"]
    if ntype == "agent":
        return n.get("name") or n["id"]
    if ntype == "institution":
        return n.get("name") or n["id"]
    return n.get("label") or n["id"]

d3_nodes_v3 = []
for n in v3_nodes_pruned:
    ntype = n["type"]
    label = _v3_label(n)
    color = PALETTE.get(ntype, "#64748b")
    size = max(6, min(22, 6 + math.log1p(degree_v3[n["id"]]) * 3.5))

    d3_nodes_v3.append({
        "id": n["id"],
        "label": label,
        "type": ntype,
        "role": n.get("role", ""),
        "all_roles": n.get("all_roles", []),
        "kind": n.get("kind", ""),
        "year": n.get("year", ""),
        "project_type": n.get("project_type", ""),
        "color": color,
        "size": size,
        "verified": n.get("verified", False),
        "connections": degree_v3[n["id"]]
    })

d3_edges_v3 = []
for e in v3_edges_pruned:
    rel = e["relation"]
    d3_edges_v3.append({
        "source": e["source"],
        "target": e["target"],
        "relation": rel,
        "confidence": e.get("confidence", 0.5),
        "verified": e.get("verified", False),
        "lineage": e.get("lineage", "")
    })

print(f"  Result View 3: {len(d3_nodes_v3)} nodes, {len(d3_edges_v3)} edges.")

# ===========================================================================
# VIEW 4 PROCESSING: Artworks, Performances & Their Creators
# ===========================================================================
print("[Pipeline] Processing View 4: Artworks, Performances & Creators...")
V4_ALLOWED_TYPES = {"agent", "source_text", "institution"}
V4_PROJECT_TYPES = {"artwork", "performance"}
v4_nodes_raw = [
    n for n in ALL_NODES
    if n.get("type") in V4_ALLOWED_TYPES and (
        n.get("type") in ("agent", "institution") or n.get("project_type") in V4_PROJECT_TYPES
    )
]
v4_ids = {n["id"] for n in v4_nodes_raw}

v4_edges_dict = {}

def add_v4_edge(s, t, rel, **kwargs):
    if not s or not t or s == t or s not in v4_ids or t not in v4_ids:
        return
    key = (s, t, rel)
    if key not in v4_edges_dict:
        v4_edges_dict[key] = {
            "source": s,
            "target": t,
            "relation": rel,
            **kwargs
        }

V4_BIBLIOGRAPHY_RELATIONS = {
    "written_by", "translated_by", "edited_by", "performed_by",
    "published_by", "sl_published_by", "hosted_by",
    "cited_in", "appears_in",
}
for e in ALL_EDGES:
    s, t = e["source"], e["target"]
    rel = e.get("relation")
    if s in v4_ids and t in v4_ids and rel in V4_BIBLIOGRAPHY_RELATIONS:
        add_v4_edge(s, t, rel, confidence=e.get("confidence", 0.5), verified=e.get("verified", False))

# Agent-to-Work projection from translation mappings
for m_id, m_node in mapping_nodes.items():
    texts = mapping_texts.get(m_id, [])
    agents = mapping_agents.get(m_id, [])
    conf = m_node.get("confidence", 0.5)
    ver = m_node.get("verified", False)
    lineage = m_node.get("lineage", "")
    for agent_id in agents:
        for text_id in texts:
            add_v4_edge(agent_id, text_id, "contributed_to", confidence=conf, verified=ver, lineage=lineage)
# ─── Lineage layer for V4 (artworks/performances). Performance is INCLUDED here ───
# because for artworks/performances, the "performance" lineage tag is a real
# category (not just the seed default).
V4_LINEAGE_EXCLUDE = {"general", "manual", ""}   # only structural noise excluded
v4_work_lineage_votes: Dict[str, Counter] = defaultdict(Counter)
v4_agent_lineage_votes: Dict[str, Counter] = defaultdict(Counter)
v4_lineage_total: Counter = Counter()
for m_id, m_node in mapping_nodes.items():
    lin = (m_node.get("lineage") or "").strip()
    if lin in V4_LINEAGE_EXCLUDE:
        continue
    texts_for_m = mapping_texts.get(m_id, [])
    agents_for_m = mapping_agents.get(m_id, [])
    # Only count when the target is in V4 (i.e. an artwork/performance work
    # OR an agent that already participates in V4 via bibliography edges).
    relevant_texts = [t for t in texts_for_m if t in v4_ids]
    relevant_agents = [a for a in agents_for_m if a in v4_ids]
    if not (relevant_texts or relevant_agents):
        continue
    v4_lineage_total[lin] += 1
    for t in relevant_texts:
        v4_work_lineage_votes[t][lin] += 1
    for a in relevant_agents:
        v4_agent_lineage_votes[a][lin] += 1
# Synthesize lineage hub nodes into V4. Only include lineages that actually
# touch at least one V4 node, so we don't add empty hubs.
v4_active_lineages = set(v4_lineage_total.keys())
v4_lineage_hubs = {}
for lin in v4_active_lineages:
    hub_id = f"lineage:{lin}"
    v4_lineage_hubs[hub_id] = {
        "id": hub_id,
        "label": lin,
        "type": "lineage",
        "lineage": lin,
        "mapping_count": v4_lineage_total[lin],
    }
    v4_ids.add(hub_id)
v4_nodes_raw.extend(v4_lineage_hubs.values())
# Wire each lineage edge through the V4 helper (it now passes the v4_ids check).
for wid, votes in v4_work_lineage_votes.items():
    for lin, weight in votes.items():
        add_v4_edge(wid, f"lineage:{lin}", "work_in_lineage",
                    weight=weight, lineage=lin, confidence=1.0, verified=False)
for aid, votes in v4_agent_lineage_votes.items():
    for lin, weight in votes.items():
        add_v4_edge(aid, f"lineage:{lin}", "agent_in_lineage",
                    weight=weight, lineage=lin, confidence=1.0, verified=False)
v4_edges_raw = list(v4_edges_dict.values())
v4_nodes_pruned, v4_edges_pruned = prune_subgraph(v4_nodes_raw, v4_edges_raw, args.limit)
degree_v4 = Counter()
for e in v4_edges_pruned:
    degree_v4[e["source"]] += 1
    degree_v4[e["target"]] += 1

def _v4_label(n):
    """Best display label for V4 artwork/performance nodes."""
    ntype = n["type"]
    if ntype == "lineage":
        return n.get("label", "")
    if ntype == "source_text":
        return n.get("title") or n.get("title_en") or n["id"]
    if ntype == "agent":
        return n.get("name") or n["id"]
    if ntype == "institution":
        return n.get("name") or n["id"]
    return n.get("label") or n["id"]
def _v4_color(n):
    """Color artwork/performance nodes by project_type; lineage hubs are gold."""
    ntype = n.get("type", "")
    if ntype == "lineage":
        return "#fbbf24"
    pt = n.get("project_type", "")
    if ntype == "source_text":
        if pt == "artwork":
            return PALETTE["artwork"]
        if pt == "performance":
            return PALETTE["performance"]
        return PALETTE.get("source_text", "#64748b")
    return PALETTE.get(ntype, "#64748b")
d3_nodes_v4 = []
for n in v4_nodes_pruned:
    ntype = n["type"]
    label = _v4_label(n)
    color = _v4_color(n)
    if ntype == "lineage":
        size = max(14, min(48, 14 + math.log1p(n.get("mapping_count", 1)) * 6))
    else:
        size = max(6, min(22, 6 + math.log1p(degree_v4[n["id"]]) * 3.5))
    d3_nodes_v4.append({
        "id": n["id"],
        "label": label,
        "type": ntype,
        "lineage": n.get("lineage", ""),
        "mapping_count": n.get("mapping_count", 0),
        "role": n.get("role", ""),
        "all_roles": n.get("all_roles", []),
        "kind": n.get("kind", ""),
        "year": n.get("year", ""),
        "project_type": n.get("project_type", ""),
        "color": color,
        "size": size,
        "verified": n.get("verified", False),
        "connections": degree_v4[n["id"]]
    })

d3_edges_v4 = []
for e in v4_edges_pruned:
    rel = e["relation"]
    d3_edges_v4.append({
        "source": e["source"],
        "target": e["target"],
        "relation": rel,
        "confidence": e.get("confidence", 0.5),
        "verified": e.get("verified", False),
        "lineage": e.get("lineage", "")
    })

print(f"  Result View 4: {len(d3_nodes_v4)} nodes, {len(d3_edges_v4)} edges.")
# ===========================================================================
# VIEW 5 PROCESSING: Lineage → Concepts (focused provenance)
# ===========================================================================
print("[Pipeline] Processing View 5: Lineage \u2192 Concepts...")
# Default / placeholder lineages excluded from this focused view.
# Per ontology §2.3, translation_mapping nodes carry the `lineage` field —
# that is the SOLE source of lineage information in the KG. There are NO
# lineage node types and NO agent_in_lineage edges. Lineage hubs are
# synthesized at view-time only.
DEFAULT_LINEAGES = {"performance", "general", "manual", ""}
# Aggregate: which concepts associate with which non-default lineages?
# Walk mapping nodes; for each: src/tgt term → concept → lineage_vote
concept_lineage_votes: Dict[str, Counter] = defaultdict(Counter)
lineage_mapping_counts: Counter = Counter()
for m_id, m_node in mapping_nodes.items():
    lin = (m_node.get("lineage") or "").strip()
    if lin in DEFAULT_LINEAGES:
        continue
    src_term = mapping_sources.get(m_id)
    tgt_term = mapping_targets.get(m_id)
    for term_id in (src_term, tgt_term):
        if not term_id:
            continue
        cid = term_to_concept.get(term_id)
        if cid:
            concept_lineage_votes[cid][lin] += 1
    lineage_mapping_counts[lin] += 1
# Build node sets
v5_concept_ids = set(concept_lineage_votes.keys())
v5_lineage_names = set(lineage_mapping_counts.keys())
concept_nodes_v5 = {n["id"]: n for n in ALL_NODES
                    if n.get("type") == "concept" and n["id"] in v5_concept_ids}
# Synthesize lineage hub nodes at VIEW TIME (visualization-only, NOT persisted)
lineage_hub_nodes_v5 = {}
for lin, mc in lineage_mapping_counts.items():
    hub_id = f"lineage:{lin}"
    lineage_hub_nodes_v5[hub_id] = {
        "id": hub_id,
        "label": lin,
        "type": "lineage",
        "lineage": lin,
        "mapping_count": mc,
    }
# Each concept attaches to its DOMINANT lineage only (tree structure → readable).
v5_edges_raw = []
for cid, votes in concept_lineage_votes.items():
    if not votes:
        continue
    lin, weight = votes.most_common(1)[0]
    v5_edges_raw.append({
        "source": cid,
        "target": f"lineage:{lin}",
        "relation": "in_lineage",
        "lineage": lin,
        "weight": weight,
        "confidence": 1.0,
        "verified": False,
    })

v5_nodes_raw = list(concept_nodes_v5.values()) + list(lineage_hub_nodes_v5.values())
v5_nodes_pruned, v5_edges_pruned = prune_subgraph(v5_nodes_raw, v5_edges_raw, args.limit)

degree_v5: Counter = Counter()
for e in v5_edges_pruned:
    degree_v5[e["source"]] += 1
    degree_v5[e["target"]] += 1

d3_nodes_v5 = []
for n in v5_nodes_pruned:
    if n["type"] == "lineage":
        size = max(14, min(48, 14 + math.log1p(n.get("mapping_count", 1)) * 6))
        color = "#fbbf24"
        label = n.get("label", "")
    else:
        size = max(6, min(20, 6 + math.log1p(degree_v5[n["id"]]) * 3.0))
        color = PALETTE["concept"]
        label = n.get("label") or n["id"]
    d3_nodes_v5.append({
        "id": n["id"],
        "label": label,
        "type": n["type"],
        "lineage": n.get("lineage", ""),
        "mapping_count": n.get("mapping_count", 0),
        "definition": n.get("definition", ""),
        "domain": n.get("domain", ""),
        "color": color,
        "size": size,
        "connections": degree_v5[n["id"]],
    })

d3_edges_v5 = []
for e in v5_edges_pruned:
    d3_edges_v5.append({
        "source": e["source"],
        "target": e["target"],
        "relation": e["relation"],
        "lineage": e.get("lineage", ""),
        "weight": e.get("weight", 1),
        "confidence": e.get("confidence", 1.0),
        "verified": e.get("verified", False),
    })

available_lineages_v5 = sorted({n.get("label", "") for n in lineage_hub_nodes_v5.values()})
print(f"  Result View 5: {len(d3_nodes_v5)} nodes ({len(lineage_hub_nodes_v5)} lineage hubs), {len(d3_edges_v5)} edges.")
# ===========================================================================
# VIEW 6 PROCESSING: Lineage \u2192 Works + Agents
# ===========================================================================
print("[Pipeline] Processing View 6: Lineage \u2192 Works + Agents...")
# Aggregate source_text→lineage and agent→lineage from translation_mapping
# bridges per ontology §3.3. Lineage hubs are synthesized at view-time only.
work_lineage_votes: Dict[str, Counter] = defaultdict(Counter)
agent_lineage_votes: Dict[str, Counter] = defaultdict(Counter)
for m_id, m_node in mapping_nodes.items():
    lin = (m_node.get("lineage") or "").strip()
    if lin in DEFAULT_LINEAGES:
        continue
    for text_id in mapping_texts.get(m_id, []):
        work_lineage_votes[text_id][lin] += 1
    for agent_id in mapping_agents.get(m_id, []):
        agent_lineage_votes[agent_id][lin] += 1
# Real entity nodes participating in at least one lineage
v6_work_ids = set(work_lineage_votes.keys())
v6_agent_ids = set(agent_lineage_votes.keys())
v6_lineage_names = (set(lineage_mapping_counts.keys()) &
                    (set().union(*[v.keys() for v in work_lineage_votes.values()] or [set()]) |
                     set().union(*[v.keys() for v in agent_lineage_votes.values()] or [set()])))
work_nodes_v6 = {n["id"]: n for n in ALL_NODES if n["id"] in v6_work_ids}
agent_nodes_v6 = {n["id"]: n for n in ALL_NODES if n["id"] in v6_agent_ids}
# Synthesize lineage hubs at view-time (visualization-only, NOT persisted)
lineage_hub_nodes_v6 = {}
for lin in v6_lineage_names:
    hub_id = f"lineage:{lin}"
    lineage_hub_nodes_v6[hub_id] = {
        "id": hub_id,
        "label": lin,
        "type": "lineage",
        "lineage": lin,
        "mapping_count": lineage_mapping_counts.get(lin, 0),
    }
# Edges: work→lineage and agent→lineage (weighted)
v6_edges_raw = []
for wid, votes in work_lineage_votes.items():
    for lin, weight in votes.items():
        v6_edges_raw.append({
            "source": wid,
            "target": f"lineage:{lin}",
            "relation": "work_in_lineage",
            "lineage": lin,
            "weight": weight,
            "confidence": 1.0,
            "verified": False,
        })
for aid, votes in agent_lineage_votes.items():
    for lin, weight in votes.items():
        v6_edges_raw.append({
            "source": aid,
            "target": f"lineage:{lin}",
            "relation": "agent_in_lineage",
            "lineage": lin,
            "weight": weight,
            "confidence": 1.0,
            "verified": False,
        })

v6_nodes_raw = (list(work_nodes_v6.values()) + list(agent_nodes_v6.values())
                + list(lineage_hub_nodes_v6.values()))
v6_nodes_pruned, v6_edges_pruned = prune_subgraph(v6_nodes_raw, v6_edges_raw, args.limit)
degree_v6: Counter = Counter()
for e in v6_edges_pruned:
    degree_v6[e["source"]] += 1
    degree_v6[e["target"]] += 1
d3_nodes_v6 = []
for n in v6_nodes_pruned:
    ntype = n.get("type", "")
    if ntype == "lineage":
        mc = lineage_mapping_counts.get(n.get("lineage", ""), 0)
        size = max(18, min(56, 18 + math.log1p(mc) * 7))
        color = "#fbbf24"
        label = n.get("label", "")
    elif ntype == "source_text":
        size = max(7, min(24, 7 + math.log1p(degree_v6[n["id"]]) * 3.0))
        color = PALETTE["source_text"]
        label = n.get("title") or n.get("title_en") or n["id"]
    elif ntype == "agent":
        size = max(7, min(22, 7 + math.log1p(degree_v6[n["id"]]) * 3.0))
        color = PALETTE["agent"]
        label = n.get("name") or n["id"]
    else:
        size = 6
        color = "#64748b"
        label = n["id"]
    d3_nodes_v6.append({
        "id": n["id"],
        "label": label,
        "type": ntype,
        "lineage": n.get("lineage", ""),
        "mapping_count": lineage_mapping_counts.get(n.get("lineage", ""), 0),
        "project_type": n.get("project_type", ""),
        "role": n.get("role", ""),
        "all_roles": n.get("all_roles", []),
        "color": color,
        "size": size,
        "connections": degree_v6[n["id"]],
    })
d3_edges_v6 = []
for e in v6_edges_pruned:
    d3_edges_v6.append({
        "source": e["source"],
        "target": e["target"],
        "relation": e["relation"],
        "lineage": e.get("lineage", ""),
        "weight": e.get("weight", 1),
        "confidence": e.get("confidence", 1.0),
        "verified": e.get("verified", False),
    })
available_lineages_v6 = sorted({n.get("label", "") for n in lineage_hub_nodes_v6.values()})
print(f"  Result View 6: {len(d3_nodes_v6)} nodes ({len(lineage_hub_nodes_v6)} lineage hubs), {len(d3_edges_v6)} edges.")
# ---------------------------------------------------------------------------
# Dynamic D3.js Output Templates (Pure CSS/JS replacement strategy)
# ---------------------------------------------------------------------------
LEGEND_V1 = [
    {"type": "dot", "color": PALETTE["concept"], "label": "Concept"},
    {"type": "dot", "color": PALETTE["agent"], "label": "Agent (Author/Translator/...)"},
    {"type": "dot", "color": PALETTE["source_text"], "label": "Source Text"},
    {"type": "dot", "color": PALETTE["institution"], "label": "Institution"},
    {"type": "line", "color": EDGE_COLORS["written_by"], "dashed": False, "label": "Written By"},
    {"type": "line", "color": EDGE_COLORS["translated_by"], "dashed": False, "label": "Translated By"},
    {"type": "line", "color": EDGE_COLORS["published_by"], "dashed": False, "label": "Published By"},
    {"type": "line", "color": EDGE_COLORS["cited_in"], "dashed": True, "label": "Cited In"},
    {"type": "line", "color": EDGE_COLORS["translates_to"], "dashed": False, "label": "Projected Translation"},
    {"type": "line", "color": PALETTE["concept"], "dashed": True, "label": "Rhizomatic Concept Link"},
]

LEGEND_V2 = [
    {"type": "dot", "color": PALETTE["term_en"], "label": "English Term"},
    {"type": "dot", "color": PALETTE["term_sl"], "label": "Slovenian Term"},
    {"type": "dot", "color": PALETTE["concept"], "label": "Concept Node"},
    {"type": "line", "color": PALETTE["concept"], "dashed": True, "label": "Instantiates Concept"},
    {"type": "line", "color": EDGE_COLORS["translates_to"], "dashed": False, "label": "Collapsed Translation"}
]

LEGEND_V3 = [
    {"type": "dot", "color": PALETTE["agent"], "label": "Agent (Author / Translator / Editor / ...)"},
    {"type": "dot", "color": PALETTE["source_text"], "label": "Source Text (Work Cited)"},
    {"type": "dot", "color": PALETTE["institution"], "label": "Institution (Publisher / Venue / ...)"},
    {"type": "line", "color": EDGE_COLORS["written_by"], "dashed": False, "label": "Written By"},
    {"type": "line", "color": EDGE_COLORS["translated_by"], "dashed": False, "label": "Translated By"},
    {"type": "line", "color": EDGE_COLORS["published_by"], "dashed": False, "label": "Published By"},
    {"type": "line", "color": EDGE_COLORS["cited_in"], "dashed": True, "label": "Cited In / Appears In"},
]

LEGEND_V4 = [
    {"type": "dot", "color": PALETTE["artwork"], "label": "Artwork"},
    {"type": "dot", "color": PALETTE["performance"], "label": "Performance"},
    {"type": "dot", "color": PALETTE["agent"], "label": "Agent (Creator / Performer)"},
    {"type": "dot", "color": PALETTE["institution"], "label": "Institution (Publisher / Venue)"},
    {"type": "dot", "color": "#fbbf24", "label": "Lineage (intellectual tradition)"},
    {"type": "line", "color": EDGE_COLORS["written_by"], "dashed": False, "label": "Written By"},
    {"type": "line", "color": EDGE_COLORS["performed_by"], "dashed": False, "label": "Performed By"},
    {"type": "line", "color": EDGE_COLORS["published_by"], "dashed": False, "label": "Published By"},
    {"type": "line", "color": EDGE_COLORS["hosted_by"], "dashed": True, "label": "Hosted By"},
    {"type": "line", "color": EDGE_COLORS["cited_in"], "dashed": True, "label": "Cited In / Appears In"},
    {"type": "line", "color": "#fbbf24", "dashed": False, "label": "Work / Agent ∈ Lineage"},
]
LEGEND_V5 = [
    {"type": "dot", "color": "#fbbf24", "label": "Lineage (intellectual tradition)"},
    {"type": "dot", "color": PALETTE["concept"], "label": "Concept (only those tied to a specific lineage)"},
    {"type": "line", "color": "#fbbf24", "dashed": False, "label": "Concept ∈ Lineage (weight = mapping count)"},
]
LEGEND_V6 = [
    {"type": "dot", "color": "#fbbf24", "label": "Lineage (intellectual tradition)"},
    {"type": "dot", "color": PALETTE["source_text"], "label": "Translated Work"},
    {"type": "dot", "color": PALETTE["agent"], "label": "Agent (Author / Translator / ...)"},
    {"type": "line", "color": PALETTE["source_text"], "dashed": False, "label": "Work ∈ Lineage (work_in_lineage)"},
    {"type": "line", "color": PALETTE["agent"], "dashed": False, "label": "Agent ∈ Lineage (agent_in_lineage)"},
]

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>__TITLE__</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
    <style>
        :root {
            --bg-color: #040508;
            --panel-bg: rgba(13, 17, 28, 0.75);
            --panel-border: rgba(255, 255, 255, 0.08);
            --text-primary: #f3f4f6;
            --text-secondary: #a1a1aa;
            --text-muted: #52525b;
            --accent-glow: __ACCENT__;
            --font-sans: 'Inter', system-ui, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: radial-gradient(circle at center, #0c0f1d 0%, #040508 100%);
            color: var(--text-primary);
            font-family: var(--font-sans);
            height: 100vh;
            width: 100vw;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            position: relative;
        }

        .glass-panel {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 12px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.55);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
        }

        /* Portrait-Friendly Top Floating Bar */
        #topbar {
            position: absolute;
            top: 16px;
            left: 16px;
            right: 16px;
            height: 64px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 20px;
            z-index: 100;
        }
        .header-brand {
            display: flex;
            flex-direction: column;
        }
        .header-brand h1 {
            font-size: 15px;
            font-weight: 700;
            letter-spacing: -0.01em;
            color: #fff;
            margin: 0;
        }
        .header-brand p {
            font-size: 10px;
            font-weight: 500;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin: 2px 0 0 0;
        }

        .search-container {
            width: 200px;
        }
        .search-input {
            width: 100%;
            background-color: rgba(0, 0, 0, 0.4);
            border: 1px solid var(--panel-border);
            border-radius: 8px;
            padding: 8px 14px;
            color: #fff;
            font-family: var(--font-sans);
            font-size: 12px;
            outline: none;
            transition: all 0.15s ease;
        }
        .search-input:focus {
            border-color: var(--accent-glow);
            background-color: rgba(0, 0, 0, 0.6);
        }

        .stats-hud {
            display: flex;
            align-items: center;
            gap: 16px;
            font-size: 11px;
            font-family: var(--font-mono);
            color: var(--text-secondary);
        }

        /* Floating Controls & Sliding Physics Drawer */
        .controls-group {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .btn {
            background-color: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--panel-border);
            color: var(--text-primary);
            padding: 8px 14px;
            font-size: 11px;
            font-weight: 600;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .btn:hover {
            border-color: var(--accent-glow);
            background-color: rgba(255, 255, 255, 0.08);
        }

        /* Sliding Physics Drawer */
        #physics-drawer {
            position: absolute;
            top: 88px;
            right: 16px;
            width: 290px;
            padding: 20px;
            z-index: 99;
            display: none;
            flex-direction: column;
            gap: 14px;
            transition: opacity 0.2s ease;
        }
        .drawer-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .drawer-group label {
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            display: flex;
            justify-content: space-between;
        }
        .value-display {
            font-family: var(--font-mono);
            color: var(--text-secondary);
        }
        input[type="range"] {
            -webkit-appearance: none;
            appearance: none;
            width: 100%;
            background: rgba(255, 255, 255, 0.06);
            height: 4px;
            border-radius: 2px;
            outline: none;
        }
        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            appearance: none;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: var(--accent-glow);
            cursor: pointer;
        }

        /* Bottom sliding Inspector Sheet (Optimized for Portrait Screen) */
        #details-panel {
            position: absolute;
            bottom: 24px;
            left: 50%;
            transform: translateX(-50%);
            width: calc(100% - 32px);
            max-width: 640px;
            padding: 20px 24px;
            z-index: 100;
            display: none;
        }
        .detail-layout {
            display: grid;
            grid-template-columns: 1fr 180px;
            gap: 20px;
        }
        .detail-info {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .detail-title {
            font-size: 16px;
            font-weight: 700;
            color: #fff;
            margin: 0;
        }
        .detail-tag {
            font-size: 9px;
            font-family: var(--font-mono);
            background-color: rgba(255, 255, 255, 0.08);
            padding: 2px 6px;
            border-radius: 4px;
            width: fit-content;
            color: var(--text-secondary);
            letter-spacing: 0.05em;
            margin-bottom: 6px;
        }
        .detail-desc {
            font-size: 12px;
            line-height: 1.5;
            color: var(--text-secondary);
            border-left: 2px solid var(--accent-glow);
            padding-left: 10px;
            margin-top: 4px;
        }
        .detail-meta-column {
            display: flex;
            flex-direction: column;
            gap: 6px;
            font-size: 11px;
            color: var(--text-secondary);
            border-left: 1px solid var(--panel-border);
            padding-left: 16px;
        }

        #canvas-container {
            flex-grow: 1;
            width: 100%;
            height: 100%;
        }
    </style>
    <script src="https://unpkg.com/force-graph"></script>
</head>
<body>
    <div id="topbar" class="glass-panel">
        <div class="header-brand">
            <h1>Canvas Workspace</h1>
            <p>__SUBTITLE__</p>
        </div>
        <div class="search-container">
            <input type="text" id="search-bar" class="search-input" placeholder="Search dictionary...">
        </div>
        <div class="stats-hud">
            <div>Nodes: <strong style="color: #fff;" id="node-count-label">0</strong></div>
            <div>Edges: <strong style="color: #fff;" id="edge-count-label">0</strong></div>
        </div>
        <div class="controls-group">
            <button class="btn" onclick="togglePhysics()">Physics & Filter</button>
            <button class="btn" onclick="resetZoom()">Recenter</button>
            <button class="btn" onclick="releasePins()">Release Pins</button>
        </div>
    </div>

    <!-- Sliding Physics & Filter Settings Panel -->
    <div id="physics-drawer" class="glass-panel">
        <div class="drawer-group">
            <label>Min Confidence (Dice) <span class="value-display" id="conf-val">0.15</span></label>
            <input type="range" id="conf-threshold" min="0.0" max="0.90" step="0.05" value="0.15">
        </div>
        <div class="drawer-group" style="border-top: 1px solid var(--panel-border); padding-top: 12px;">
            <label>Repulsion (Charge) <span class="value-display" id="charge-val">__PHYS_CHARGE__</span></label>
            <input type="range" id="charge-force" min="-3000" max="-50" step="25" value="__PHYS_CHARGE__">
        </div>
        <div class="drawer-group">
            <label>Spacing (Distance) <span class="value-display" id="link-dist-val">__PHYS_LINK_DIST__</span></label>
            <input type="range" id="link-dist" min="30" max="600" step="10" value="__PHYS_LINK_DIST__">
        </div>
        <div class="drawer-group">
            <label>Link Strength <span class="value-display" id="link-strength-val">__PHYS_LINK_STR__</span></label>
            <input type="range" id="link-strength" min="0.02" max="1.0" step="0.02" value="__PHYS_LINK_STR__">
        </div>
        <div class="drawer-group">
            <label>Collision Padding <span class="value-display" id="collision-val">__PHYS_COLLIDE__</span></label>
            <input type="range" id="collision-force" min="4" max="60" step="1" value="__PHYS_COLLIDE__">
        </div>
        <div class="drawer-group">
            <label>Velocity Decay <span class="value-display" id="velocity-val">__PHYS_VELOCITY__</span></label>
            <input type="range" id="velocity-decay" min="0.05" max="0.95" step="0.05" value="__PHYS_VELOCITY__">
        </div>
        <div class="drawer-group">
            <label>Centering Strength <span class="value-display" id="center-val">__PHYS_CENTER__</span></label>
            <input type="range" id="center-strength" min="0.0" max="1.0" step="0.05" value="__PHYS_CENTER__">
        </div>
        <div class="drawer-group" style="border-top: 1px solid var(--panel-border); padding-top: 12px;">
            <label>Lineage Filter <span class="value-display" id="lineage-val">All</span></label>
            <select id="lineage-select" style="width: 100%; background-color: rgba(0,0,0,0.4); border: 1px solid var(--panel-border); border-radius: 8px; padding: 8px 12px; color: #fff; font-family: var(--font-sans); font-size: 12px; outline: none;">
                <option value="">All lineages</option>
            </select>
        </div>
    </div>

    <!-- Bottom sliding Inspector Sheet -->
    <div id="details-panel" class="glass-panel">
        <div class="detail-layout" id="detail-content"></div>
    </div>

    <div id="canvas-container"></div>

    <script>
        const graphData = __DATA__;
        const legendData = __LEGEND_DATA__;

        let selectedNodeId = null;
        let selectedLinkId = null;
        const connectedSet = new Set();
        let currentConf = 0.15;

        // Render Legend dynamic elements
        const legendDiv = d3.select("body").append("div").attr("id", "legend").attr("class", "glass-panel")
            .style("position", "absolute").style("bottom", "24px").style("right", "24px")
            .style("padding", "16px").style("display", "flex").style("flex-direction", "column").style("gap", "8px")
            .style("pointer-events", "none").style("z-index", "100").style("font-size", "11px");

        legendDiv.append("div").text("Legend")
            .style("font-size", "10px").style("text-transform", "uppercase").style("font-weight", "700")
            .style("letter-spacing", "0.1em").style("color", "var(--text-muted)").style("margin-bottom", "2px");

        legendData.forEach(item => {
            const row = legendDiv.append("div").style("display", "flex").style("align-items", "center").style("gap", "10px").style("color", "var(--text-secondary)");
            if (item.type === "dot") {
                row.append("div")
                   .style("width", "10px").style("height", "10px").style("border-radius", "50%")
                   .style("background-color", item.color);
            } else {
                const styleStr = item.dashed ? "border-top: 2px dashed " + item.color : "background-color: " + item.color;
                row.append("div")
                   .style("width", "18px").style("height", "2px")
                   .attr("style", styleStr);
            }
            row.append("span").text(item.label);
        });
        const container = document.getElementById('canvas-container');
        // Edge-relation colors injected from Python EDGE_COLORS
        const edgeColors = __EDGE_COLORS__;
        const specialRelations = new Set(__SPECIAL_RELATIONS__);
        const availableLineages = __LINEAGES__;
        const isSpecialRelation = (rel) => {
            return specialRelations.has(rel);
        };
        // Lineage filter state
        let currentLineage = "";
        // Initialize GPU-blended Force Graph
        const elem = ForceGraph()(container)
            // Pre-run the layout offscreen so the page renders an already-
            // settled graph instead of one mid-jiggle. cooldownTime caps the
            // total simulation runtime so it actually STOPS (no perpetual
            // micro-motion eating CPU and confusing the eye).
            .warmupTicks(200)
            .cooldownTime(4000)
            .d3AlphaMin(0.01)
            .linkLabel(d => {
                if (d.lineage) return `${d.relation} · ${d.lineage}`;
                return d.relation || 'connection';
            })
            .linkColor(d => {
                const isHighlight = selectedNodeId && (d.source.id === selectedNodeId || d.target.id === selectedNodeId);
                if (selectedNodeId) {
                    return isHighlight ? 'rgba(236, 72, 153, 0.95)' : 'rgba(255, 255, 255, 0.02)';
                }
                const relColor = edgeColors[d.relation] || 'rgba(192, 132, 252, 0.11)';
                return isSpecialRelation(d.relation) ? relColor : relColor;
            })
            .linkWidth(d => {
                const isHighlight = selectedNodeId && (d.source.id === selectedNodeId || d.target.id === selectedNodeId);
                if (selectedNodeId) {
                    return isHighlight ? 2.5 : 0.5;
                }
                return isSpecialRelation(d.relation) ? 1.0 : 0.6;
            })
            .linkDirectionalParticles(d => isSpecialRelation(d.relation) ? 2 : 0)
            .linkDirectionalParticleSpeed(d => (d.confidence || 0.5) * 0.005)
            .linkDirectionalParticleWidth(1.6)
            .linkDirectionalParticleColor(d => edgeColors[d.relation] || '#ec4899')
            .nodeCanvasObjectMode(() => 'replace')
            .nodeCanvasObject((node, ctx, globalScale) => {
                const label = node.label;
                const size = node.size;
                const isConcept = node.type === 'concept';

                let isFaded = selectedNodeId && !connectedSet.has(node.id);
                let isSelected = selectedNodeId === node.id;

                // 1. Organic pulsing selected glow halo
                if (isSelected) {
                    const pulse = 1 + Math.sin(Date.now() / 150) * 0.12;
                    ctx.beginPath();
                    ctx.arc(node.x, node.y, size + 5.5 * pulse, 0, 2 * Math.PI);
                    ctx.fillStyle = node.color + '22';
                    ctx.fill();

                    ctx.beginPath();
                    ctx.arc(node.x, node.y, size + 5.5 * pulse, 0, 2 * Math.PI);
                    ctx.strokeStyle = node.color;
                    ctx.lineWidth = 1.5;
                    ctx.stroke();
                }

                // 2. Translucent outer halo glow
                ctx.beginPath();
                ctx.arc(node.x, node.y, size + (isSelected ? 4 : 3), 0, 2 * Math.PI, false);
                ctx.fillStyle = node.color + (isFaded ? '08' : '22');
                ctx.fill();

                // 3. Central node circle drawing
                ctx.beginPath();
                ctx.arc(node.x, node.y, size, 0, 2 * Math.PI, false);
                ctx.fillStyle = node.color;
                ctx.globalAlpha = isFaded ? 0.2 : 1.0;
                ctx.fill();
                ctx.globalAlpha = 1.0;

                // 4. Subtle node boundary rings
                if (node.verified) {
                    ctx.strokeStyle = '#fbbf24';
                    ctx.lineWidth = isSelected ? 2.5 : 1.2;
                    ctx.stroke();
                } else {
                    ctx.strokeStyle = 'rgba(255,255,255,0.2)';
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }

                // 5. Precise Semantic Label Zoom Scaling
                const isImportant = isConcept || node.connections > 12;

                if (globalScale > 0.65 || (globalScale > 0.3 && isImportant)) {
                    const fontSize = isConcept ? 12 : 11;
                    ctx.font = `${isSelected ? '700' : '500'} ${fontSize}px "Inter", sans-serif`;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';

                    const textY = node.y + size + 14;

                    // Black mask stroke to prevent connection overlaps
                    ctx.strokeStyle = '#040508';
                    ctx.lineWidth = 4;
                    ctx.strokeText(label, node.x, textY);

                    const rawAlpha = globalScale < 0.65 ? (globalScale - 0.3) / 0.35 : 1.0;
                    ctx.globalAlpha = isFaded ? 0.25 : Math.max(0, Math.min(1, rawAlpha));

                    ctx.fillStyle = isConcept ? '#c084fc' : '#f3f4f6';
                    ctx.fillText(label, node.x, textY);

                    ctx.globalAlpha = 1.0; // reset
                }
            })
            // Custom rendering replaces the default node draw, which also kills
            // force-graph's automatic click hit-area. Re-declare it with extra
            // padding so nodes are easy to click and hover.
            .nodePointerAreaPaint((node, color, ctx) => {
                ctx.beginPath();
                ctx.arc(node.x, node.y, (node.size || 6) + 4, 0, 2 * Math.PI, false);
                ctx.fillStyle = color;
                ctx.fill();
            })
            .nodeRelSize(6)
            .onNodeHover(node => {
                container.style.cursor = node ? 'pointer' : 'default';
            })
            .onNodeClick(node => {
                selectedNodeId = node.id;
                selectedLinkId = null;
                connectedSet.clear();
                connectedSet.add(node.id);

                // Populate connected neighborhood set using actively filtered links
                elem.graphData().links.forEach(l => {
                    if (l.source.id === node.id) connectedSet.add(l.target.id);
                    if (l.target.id === node.id) connectedSet.add(l.source.id);
                });

                elem.refresh();

                // Build Bottom slide-up details sheet
                const panel = document.getElementById("details-panel");
                const content = document.getElementById("detail-content");
                panel.style.display = 'block';

                content.innerHTML = `
                    <div class="detail-info">
                        <div class="detail-tag">${node.type.toUpperCase()}${node.kind ? ' · ' + node.kind.toUpperCase() : ''}${node.project_type ? ' · ' + node.project_type.toUpperCase() : ''}</div>
                        <h2 class="detail-title">${node.label}</h2>
                        ${node.definition ? `<div class="detail-desc">${node.definition}</div>` : ''}
                    </div>
                    <div class="detail-meta-column">
                        ${node.lang ? `<div><strong>Language:</strong> ${node.lang.toUpperCase()}</div>` : ''}
                        ${node.freq ? `<div><strong>Frequency:</strong> ${node.freq}</div>` : ''}
                        ${node.role ? `<div><strong>Role:</strong> ${node.role}</div>` : ''}
                        ${node.all_roles && node.all_roles.length > 0 ? `<div><strong>All Roles:</strong> ${node.all_roles.join(', ')}</div>` : ''}
                        ${node.year ? `<div><strong>Year:</strong> ${node.year}</div>` : ''}
                        ${node.domain ? `<div><strong>Domain:</strong> ${node.domain}</div>` : ''}
                        ${node.lineages && node.lineages.length > 0 ? `<div><strong>Lineages:</strong> ${node.lineages.join(', ')}</div>` : ''}
                        <div><strong>Connections:</strong> ${node.connections}</div>
                `;

                elem.centerAt(node.x, node.y, 800);
                elem.zoom(2.2, 800);
            })
            .onLinkClick(link => {
                selectedNodeId = null;
                selectedLinkId = link.index;
                connectedSet.clear();
                elem.refresh();

                const panel = document.getElementById("details-panel");
                const content = document.getElementById("detail-content");
                panel.style.display = 'block';

                content.innerHTML = `
                    <div class="detail-info">
                        <div class="detail-tag">${(link.relation || 'CONNECTION').toUpperCase()}</div>
                        <h2 class="detail-title">${link.source.label} ↔ ${link.target.label}</h2>
                        ${link.gloss ? `<div class="detail-desc">${link.gloss}</div>` : ''}
                    </div>
                    <div class="detail-meta-column">
                        <div><strong>Confidence:</strong> ${(link.confidence !== undefined && typeof link.confidence === 'number') ? link.confidence.toFixed(3) : 'N/A'}</div>
                        ${link.lineage ? `<div><strong>Lineage:</strong> ${link.lineage}</div>` : ''}
                        <div><strong>Curated:</strong> ${link.verified ? '★ Yes' : 'No'}</div>
                    </div>
                `;
            })
            .onBackgroundClick(() => {
                selectedNodeId = null;
                selectedLinkId = null;
                connectedSet.clear();
                elem.refresh();

                document.getElementById("details-panel").style.display = 'none';
            });

        // ──────────────────────────────────────────────────────────────
        // Physics force layout — tuned for dense 3000-node graphs.
        //   • charge -800 (strong global repulsion)
        //   • link distance 200, strength 0.2 (weak pull so links don't
        //     drag everything into the centre)
        //   • collide radius size + 20 (clear gap between nodes)
        //   • centering force 0.1 (gentle pull to origin, NOT a clump)
        //   • velocity decay 0.4 (default damping; slider exposes it)
        // ──────────────────────────────────────────────────────────────
        elem.d3Force('charge').strength(__PHYS_CHARGE__).distanceMax(2400);
        const linkForce = elem.d3Force('link');
        const _baseDist = __PHYS_LINK_DIST__;
        const _baseStr  = __PHYS_LINK_STR__;
        linkForce.distance(d => isSpecialRelation(d.relation) ? _baseDist * 0.6 : _baseDist);
        linkForce.strength(d => isSpecialRelation(d.relation) ? Math.min(1, _baseStr * 1.75) : _baseStr);
        elem.d3Force('collide', d3.forceCollide().radius(d => d.size + __PHYS_COLLIDE__).strength(0.9));
        elem.d3Force('center', d3.forceCenter(0, 0).strength(__PHYS_CENTER__));
        elem.d3VelocityDecay(__PHYS_VELOCITY__);
        elem.d3AlphaDecay(__PHYS_ALPHA_DECAY__);
        // Slider control actions — every change calls d3ReheatSimulation()
        // so the visible layout actually responds.
        document.getElementById('charge-force').addEventListener('input', e => {
            const val = parseInt(e.target.value);
            document.getElementById('charge-val').textContent = val;
            const charge = elem.d3Force('charge');
            if (charge) charge.strength(val);
            elem.d3ReheatSimulation();
        });
        document.getElementById('link-dist').addEventListener('input', e => {
            const val = parseInt(e.target.value);
            document.getElementById('link-dist-val').textContent = val;
            const lf = elem.d3Force('link');
            if (lf) lf.distance(d => isSpecialRelation(d.relation) ? val * 0.6 : val);
            elem.d3ReheatSimulation();
        });
        document.getElementById('link-strength').addEventListener('input', e => {
            const val = parseFloat(e.target.value);
            document.getElementById('link-strength-val').textContent = val.toFixed(2);
            const lf = elem.d3Force('link');
            if (lf) lf.strength(d => isSpecialRelation(d.relation) ? Math.min(1, val * 1.75) : val);
            elem.d3ReheatSimulation();
        });
        document.getElementById('collision-force').addEventListener('input', e => {
            const val = parseInt(e.target.value);
            document.getElementById('collision-val').textContent = val;
            const collide = elem.d3Force('collide');
            if (collide) collide.radius(d => d.size + val);
            elem.d3ReheatSimulation();
        });
        document.getElementById('velocity-decay').addEventListener('input', e => {
            const val = parseFloat(e.target.value);
            document.getElementById('velocity-val').textContent = val.toFixed(2);
            elem.d3VelocityDecay(val);
            elem.d3ReheatSimulation();
        });
        document.getElementById('center-strength').addEventListener('input', e => {
            const val = parseFloat(e.target.value);
            document.getElementById('center-val').textContent = val.toFixed(2);
            const center = elem.d3Force('center');
            if (center) center.strength(val);
            elem.d3ReheatSimulation();
        });

        // Dynamic edge filter handler (Prunes hairball in real-time)
        function applyEdgeFilter() {
            const filteredLinks = graphData.links.filter(l => {
                // Confidence filter
                if (isSpecialRelation(l.relation) && l.confidence < currentConf) return false;
                // Lineage filter
                if (currentLineage && l.lineage !== currentLineage) return false;
                return true;
            });
            // Determine which nodes are still connected after filtering
            const connectedNodeIds = new Set();
            filteredLinks.forEach(l => {
                connectedNodeIds.add(l.source.id || l.source);
                connectedNodeIds.add(l.target.id || l.target);
            });
            elem.graphData({
                nodes: currentLineage ? graphData.nodes.filter(n => connectedNodeIds.has(n.id)) : graphData.nodes,
                links: filteredLinks
            });
            // Keep labels updated
            document.getElementById("node-count-label").textContent = currentLineage ? connectedNodeIds.size : graphData.nodes.length;
            document.getElementById("edge-count-label").textContent = filteredLinks.length;
        }
        document.getElementById('conf-threshold').addEventListener('input', e => {
            currentConf = parseFloat(e.target.value);
            document.getElementById('conf-val').textContent = currentConf.toFixed(2);
            applyEdgeFilter();
        });
        // Populate lineage dropdown from data
        const lineageSelect = document.getElementById('lineage-select');
        availableLineages.forEach(lin => {
            const opt = document.createElement('option');
            opt.value = lin;
            opt.textContent = lin;
            lineageSelect.appendChild(opt);
        });
        lineageSelect.addEventListener('change', e => {
            currentLineage = e.target.value;
            document.getElementById('lineage-val').textContent = currentLineage || 'All';
            applyEdgeFilter();
        });
        // Realtime search query (also searches lineages on nodes)
        d3.select("#search-bar").on("input", function() {
            const query = this.value.toLowerCase().trim();
            if (!query) {
                selectedNodeId = null;
                connectedSet.clear();
                elem.refresh();
                return;
            }
            selectedNodeId = "__SEARCH__";
            connectedSet.clear();
            graphData.nodes.forEach(n => {
                const matches = n.label.toLowerCase().includes(query) ||
                                n.type.toLowerCase().includes(query) ||
                                (n.domain && n.domain.toLowerCase().includes(query)) ||
                                (n.definition && n.definition.toLowerCase().includes(query)) ||
                                (n.lineages && n.lineages.some(l => l.toLowerCase().includes(query)));
                if (matches) {
                    connectedSet.add(n.id);
                }
            });
            elem.refresh();
        });
        // UI toggles
        function togglePhysics() {
            const drawer = document.getElementById("physics-drawer");
            drawer.style.display = drawer.style.display === 'flex' ? 'none' : 'flex';
        }
        function releasePins() {
            graphData.nodes.forEach(n => {
                n.fx = null;
                n.fy = null;
            });
            elem.d3ReheatSimulation();
        }
        function resetZoom() {
            // "Recenter" button: full overview escape-hatch
            // (fits the entire graph bounding box in the viewport).
            elem.zoomToFit(600, 60);
        }
        // Snap centering — NO animation while the simulation is still cooling.
        // Two simultaneous animations (sim + camera) is what causes the glitchy
        // feel. Computing centroid from current node positions.
        function _snapCenterOnGraph(durationMs) {
            const nodes = elem.graphData().nodes;
            if (!nodes.length) return;
            let sx = 0, sy = 0, n = 0;
            for (const node of nodes) {
                if (typeof node.x === 'number' && typeof node.y === 'number') {
                    sx += node.x; sy += node.y; n += 1;
                }
            }
            if (!n) return;
            elem.centerAt(sx / n, sy / n, durationMs || 0);
            elem.zoom(1.6, durationMs || 0);
        }
        // Initialize active view datasets on startup
        applyEdgeFilter();
        // warmupTicks(200) means the layout is mostly settled before paint.
        // Snap once shortly after to make sure positions are populated.
        requestAnimationFrame(() => _snapCenterOnGraph(0));
        // When the simulation fully stops (cooldownTime = 4s), do a smooth
        // re-center to the final settled positions. This is the ONLY animated
        // camera move on init.
        elem.onEngineStop(() => _snapCenterOnGraph(800));
    </script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Write Output Visualizations (Sequentially for all four views)
# ---------------------------------------------------------------------------
out_dir = pathlib.Path(args.out_dir)
out_dir.mkdir(parents=True, exist_ok=True)

EDGE_COLORS_JSON = json.dumps(EDGE_COLORS, ensure_ascii=False)
SPECIAL_RELATIONS_JSON = json.dumps(list(SPECIAL_RELATIONS), ensure_ascii=False)
# Per-view lineage filter dropdowns
available_lineages_v2 = sorted(set(e.get("lineage", "") for e in d3_edges_v2 if e.get("lineage")))
available_lineages_v3 = sorted(set(e.get("lineage", "") for e in d3_edges_v3 if e.get("lineage")))
available_lineages_v4 = sorted(set(e.get("lineage", "") for e in d3_edges_v4 if e.get("lineage")))
def _render_view(
    title: str,
    subtitle: str,
    accent: str,
    nodes: List[Dict],
    edges: List[Dict],
    legend: List[Dict],
    available_lineages: List[str],
) -> str:
    """Build one view's HTML with auto-tuned physics defaults."""
    defaults = compute_physics_defaults(len(nodes), len(edges))
    out = (
        HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__SUBTITLE__", subtitle)
        .replace("__ACCENT__", accent)
        .replace("__DATA__", json.dumps({"nodes": nodes, "links": edges}, ensure_ascii=False))
        .replace("__LEGEND_DATA__", json.dumps(legend, ensure_ascii=False))
        .replace("__EDGE_COLORS__", EDGE_COLORS_JSON)
        .replace("__SPECIAL_RELATIONS__", SPECIAL_RELATIONS_JSON)
        .replace("__LINEAGES__", json.dumps(available_lineages, ensure_ascii=False))
    )
    for key, value in physics_replacements(defaults).items():
        out = out.replace(key, value)
    return out
view1_html = _render_view(
    "Lineage & Provenance View",
    "Academic Texts, Citations & Translators",
    PALETTE["agent"], d3_nodes_v1, d3_edges_v1, LEGEND_V1, available_lineages_v1,
)
view2_html = _render_view(
    "Bilingual Term Space View",
    "Terms & Semantic Concept Hubs",
    PALETTE["term_en"], d3_nodes_v2, d3_edges_v2, LEGEND_V2, available_lineages_v2,
)
view3_html = _render_view(
    "Agents & Works Cited View",
    "Translators, Authors, and Academic Citations",
    PALETTE["agent"], d3_nodes_v3, d3_edges_v3, LEGEND_V3, available_lineages_v3,
)
view4_html = _render_view(
    "Artworks & Performances View",
    "Artworks, Performances & Their Creators",
    PALETTE["artwork"], d3_nodes_v4, d3_edges_v4, LEGEND_V4, available_lineages_v4,
)
view5_html = _render_view(
    "Lineage → Concepts View",
    "Concepts grouped by intellectual tradition",
    "#fbbf24", d3_nodes_v5, d3_edges_v5, LEGEND_V5, available_lineages_v5,
)
view6_html = _render_view(
    "Lineage → Works + Agents View",
    "Translated works and agents grouped by lineage (mapping-count weighted)",
    "#fbbf24", d3_nodes_v6, d3_edges_v6, LEGEND_V6, available_lineages_v6,
)
dest_v1 = out_dir / f"{args.prefix}provenance.html"
dest_v2 = out_dir / f"{args.prefix}terms.html"
dest_v3 = out_dir / f"{args.prefix}agents_works.html"
dest_v4 = out_dir / f"{args.prefix}artworks.html"
dest_v5 = out_dir / f"{args.prefix}lineage_concepts.html"
dest_v6 = out_dir / f"{args.prefix}lineage_works.html"
try:
    dest_v1.write_text(view1_html, encoding="utf-8")
    dest_v2.write_text(view2_html, encoding="utf-8")
    dest_v3.write_text(view3_html, encoding="utf-8")
    dest_v4.write_text(view4_html, encoding="utf-8")
    dest_v5.write_text(view5_html, encoding="utf-8")
    dest_v6.write_text(view6_html, encoding="utf-8")
    print(f"\n[Success] Generated SIX canvas visualizations successfully:")
    print(f"  - V1 Provenance:        {dest_v1.resolve()}")
    print(f"  - V2 Bilingual Terms:   {dest_v2.resolve()}")
    print(f"  - V3 Agents & Works:    {dest_v3.resolve()}")
    print(f"  - V4 Artworks:          {dest_v4.resolve()}")
    print(f"  - V5 Lineage→Concepts:  {dest_v5.resolve()}")
    print(f"  - V6 Lineage→Works:     {dest_v6.resolve()}")
except Exception as e:
    print(f"[ERROR] Failed to write HTML output files: {e}", file=sys.stderr)
    sys.exit(1)
