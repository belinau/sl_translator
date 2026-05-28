# visualise_kg.py
#
# Standalone D3.js Force Graph Visualizer updated for KG v21 ontology
# Supports Terms, Concepts, Translation Mappings, Agents & Source Texts.
#

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from collections import Counter

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Visualise Zen Translator Knowledge Graph")
parser.add_argument(
    "kg_path",
    nargs="?",
    default=None,
    help="Path to KG JSON file (default: config.KG_DB_PATH)",
)
parser.add_argument(
    "--limit", type=int, default=300, help="Max term nodes to show (default: 300)"
)
parser.add_argument(
    "--out",
    default="kg_inspect.html",
    help="Output HTML path (default: kg_inspect.html)",
)
parser.add_argument(
    "--lang", default=None, help="Filter to a specific language (e.g. en, sl)"
)
parser.add_argument(
    "--min-freq",
    type=int,
    default=1,
    help="Minimum term frequency to include (default: 1)",
)
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Resolve KG path
# ---------------------------------------------------------------------------
if args.kg_path:
    kg_path = pathlib.Path(args.kg_path)
else:
    try:
        import config

        kg_path = pathlib.Path(config.KG_DB_PATH)
    except ImportError:
        print(
            "[ERROR] Could not import config. Either pass the KG JSON path as an argument,"
        )
        print(
            "        or run this script from your project root where config.py lives."
        )
        print("        Usage: python visualise_kg.py path/to/knowledge_graph.json")
        sys.exit(1)

if not kg_path.exists():
    print(f"[ERROR] KG file not found: {kg_path}")
    sys.exit(1)

print(f"[KG Inspector] Loading: {kg_path}")
raw = json.loads(kg_path.read_text(encoding="utf-8"))
all_nodes = raw.get("nodes", [])
all_edges = raw.get("edges", [])
print(f"[KG Inspector] Raw: {len(all_nodes)} nodes, {len(all_edges)} edges")

# Index nodes by id
node_by_id = {n["id"]: n for n in all_nodes}

# Count node types
type_counts = Counter(n.get("type", "unknown") for n in all_nodes)
print(f"[KG Inspector] Node types: {dict(type_counts)}")

# ---------------------------------------------------------------------------
# Filter and select nodes (Including new ontology structures)
# ---------------------------------------------------------------------------
# Select term nodes meeting criteria, sorted by frequency desc
term_nodes = [
    n
    for n in all_nodes
    if n.get("type") == "term"
    and n.get("frequency", 1) >= args.min_freq
    and (args.lang is None or n.get("lang") == args.lang)
]
term_nodes.sort(key=lambda n: n.get("frequency", 0), reverse=True)
term_nodes = term_nodes[: args.limit]
selected_ids = {n["id"] for n in term_nodes}

# Harvest connected translation mapping nodes, agents, source texts & concepts
mapping_nodes = []
agent_nodes = []
source_nodes = []
concept_nodes = []

# First pass: Get connected concepts and mappings
for edge in all_edges:
    src, tgt = edge["source"], edge["target"]
    rel = edge.get("relation")

    if rel == "instantiates_concept" and src in selected_ids:
        if tgt in node_by_id and tgt not in selected_ids:
            concept_nodes.append(node_by_id[tgt])
            selected_ids.add(tgt)

    elif rel == "has_mapping" and src in selected_ids:
        if tgt in node_by_id and tgt not in selected_ids:
            mapping_nodes.append(node_by_id[tgt])
            selected_ids.add(tgt)

# Second pass: Pull elements connected to selected mappings
for edge in all_edges:
    src, tgt = edge["source"], edge["target"]
    rel = edge.get("relation")

    if src in selected_ids and node_by_id[src].get("type") == "translation_mapping":
        if rel == "maps_to" and tgt in node_by_id and tgt not in selected_ids:
            # Pull in target term linked to this mapping
            term_nodes.append(node_by_id[tgt])
            selected_ids.add(tgt)
        elif rel == "instantiated_in" and tgt in node_by_id and tgt not in selected_ids:
            source_nodes.append(node_by_id[tgt])
            selected_ids.add(tgt)
        elif rel == "attributed_to" and tgt in node_by_id and tgt not in selected_ids:
            agent_nodes.append(node_by_id[tgt])
            selected_ids.add(tgt)

all_selected = term_nodes + concept_nodes + mapping_nodes + agent_nodes + source_nodes
print(
    f"[KG Inspector] Visualising: {len(term_nodes)} terms, {len(concept_nodes)} concepts, {len(mapping_nodes)} mappings, {len(agent_nodes)} agents, {len(source_nodes)} sources"
)

# Filter edges to only those between selected nodes
selected_edges = [
    e for e in all_edges if e["source"] in selected_ids and e["target"] in selected_ids
]
print(f"[KG Inspector] Filtered edges: {len(selected_edges)}")

# ---------------------------------------------------------------------------
# Build Graph Data for D3
# ---------------------------------------------------------------------------
# Palette colors
LANG_COLORS = {
    "en": "#3b82f6",  # blue
    "sl": "#10b981",  # emerald
}
CONCEPT_COLOR = "#64748b"  # slate
MAPPING_COLOR = "#ec4899"  # pink/magenta (lineage bridges)
AGENT_COLOR = "#f97316"  # orange
SOURCE_COLOR = "#06b6d4"  # teal


def node_color(n: dict) -> str:
    ntype = n.get("type")
    if ntype == "concept":
        return CONCEPT_COLOR
    if ntype == "translation_mapping":
        return MAPPING_COLOR
    if ntype == "agent":
        return AGENT_COLOR
    if ntype == "source_text":
        return SOURCE_COLOR
    return LANG_COLORS.get(n.get("lang", ""), "#94a3b8")


def node_size(n: dict) -> float:
    ntype = n.get("type")
    if ntype == "term":
        freq = n.get("frequency", 1)
        return max(5, min(22, 5 + math.log1p(freq) * 3))
    if ntype == "concept":
        return 12
    if ntype == "translation_mapping":
        return 8
    if ntype == "agent":
        return 10
    if ntype == "source_text":
        return 11
    return 8


def node_label(n: dict) -> str:
    return (
        n.get("display_form")
        or n.get("term")
        or n.get("label")
        or n.get("name")
        or n.get("title")
        or n.get("id", "")
    )


d3_nodes = []
for n in all_selected:
    d3_nodes.append(
        {
            "id": n["id"],
            "label": node_label(n),
            "type": n.get("type", "term"),
            "lang": n.get("lang", ""),
            "freq": n.get("frequency", 1),
            "is_phrase": n.get("is_phrase", False),
            "domain": n.get("domain", ""),
            "lineage": n.get("lineage", ""),
            "register": n.get("register", ""),
            "gloss": n.get("gloss", ""),
            "confidence": n.get("confidence", 1.0),
            "role": n.get("role", ""),
            "year": n.get("year", ""),
            "verified": n.get("verified", False),
            "color": node_color(n),
            "size": node_size(n),
        }
    )

EDGE_STYLE = {
    "translates_to": {
        "color": "#475569",
        "width": 0.8,
        "dashed": False,
    },  # legacy fallback edge
    "has_mapping": {"color": "#ec4899", "width": 1.0, "dashed": True},
    "maps_to": {"color": "#ec4899", "width": 1.5, "dashed": False},
    "instantiated_in": {"color": "#06b6d4", "width": 0.8, "dashed": True},
    "attributed_to": {"color": "#f97316", "width": 0.8, "dashed": True},
    "instantiates_concept": {"color": "#94a3b8", "width": 1.0, "dashed": True},
    "subclass_of": {"color": "#fcd34d", "width": 1.0, "dashed": True},
}

d3_links = []
seen_link_pairs = set()

for e in selected_edges:
    rel = e.get("relation", "related_to")
    pair = tuple(sorted([e["source"], e["target"]]))

    # Deduplicate legacy translates_to if we already mapped them
    if rel == "translates_to" and pair in seen_link_pairs:
        continue
    seen_link_pairs.add(pair)

    style = EDGE_STYLE.get(rel, {"color": "#cbd5e1", "width": 1.0, "dashed": False})
    d3_links.append(
        {
            "source": e["source"],
            "target": e["target"],
            "relation": rel,
            "confidence": e.get("confidence", 0.5),
            "verified": e.get("verified", False),
            **style,
        }
    )

# ---------------------------------------------------------------------------
# Stats for sidebar
# ---------------------------------------------------------------------------
stats = {
    "total_nodes_in_kg": len(all_nodes),
    "total_edges_in_kg": len(all_edges),
    "showing_nodes": len(all_selected),
    "showing_edges": len(d3_links),
    "term_nodes": len(term_nodes),
    "concept_nodes": len(concept_nodes),
    "mapping_nodes": len(mapping_nodes),
    "agent_nodes": len(agent_nodes),
    "source_nodes": len(source_nodes),
}

# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------
graph_data_json = json.dumps({"nodes": d3_nodes, "links": d3_links}, ensure_ascii=False)
stats_json = json.dumps(stats, ensure_ascii=False)
lang_colors_json = json.dumps(LANG_COLORS, ensure_ascii=False)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KG Inspector — Zen Translator</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<style>
  :root {{
    --bg: #0b0f1a;
    --surface: #111827;
    --surface2: #1e2535;
    --border: #1e293b;
    --text: #e2e8f0;
    --text-dim: #64748b;
    --text-muted: #334155;
    --accent-en: #3b82f6;
    --accent-sl: #10b981;
    --accent-concept: #64748b;
    --accent-mapping: #ec4899;
    --accent-agent: #f97316;
    --accent-source: #06b6d4;
    --font-mono: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
    --font-ui: 'IBM Plex Sans', system-ui, sans-serif;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-ui);
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
  }}

  /* ---- Top Bar ---- */
  #topbar {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 8px 20px;
    flex-shrink: 0;
    z-index: 10;
  }}

  #topbar-title {{
    display: flex;
    flex-direction: column;
    margin-right: 8px;
    flex-shrink: 0;
  }}

  #topbar-title h1 {{
    font-size: 11px;
    font-weight: 800;
    letter-spacing: .25em;
    text-transform: uppercase;
    color: var(--text-dim);
    line-height: 1.2;
  }}

  #topbar-title p {{
    font-size: 9px;
    color: var(--text-muted);
    font-family: var(--font-mono);
  }}

  .topbar-sep {{
    width: 1px;
    height: 28px;
    background: var(--border);
    flex-shrink: 0;
  }}

  .topbar-group {{
    display: flex;
    align-items: center;
    gap: 6px;
  }}

  .topbar-group label {{
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .12em;
    text-transform: uppercase;
    color: var(--text-muted);
    white-space: nowrap;
  }}

  #search-input {{
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 5px 10px;
    font-size: 12px;
    font-family: var(--font-mono);
    border-radius: 6px;
    outline: none;
    width: 180px;
  }}
  #search-input:focus {{ border-color: var(--accent-en); }}

  .filter-pills {{
    display: flex;
    flex-wrap: nowrap;
    gap: 4px;
  }}

  #strength-slider {{
    width: 80px;
    accent-color: var(--accent-en);
  }}

  #stat-bar {{
    display: flex;
    gap: 14px;
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--text-dim);
    margin-left: auto;
    flex-shrink: 0;
  }}

  .stat-chip {{
    display: flex;
    align-items: center;
    gap: 4px;
  }}

  .stat-chip .stat-label {{ color: var(--text-muted); }}
  .stat-chip .stat-value {{ color: var(--text); font-weight: 700; }}

  /* ---- Detail Panel (collapsible, below topbar) ---- */
  #detail-bar {{
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    max-height: 120px;
    overflow-y: auto;
    padding: 8px 20px;
    flex-shrink: 0;
  }}

  #detail-bar:empty {{ display: none; }}

  #detail-bar::-webkit-scrollbar {{ width: 4px; }}
  #detail-bar::-webkit-scrollbar-track {{ background: transparent; }}
  #detail-bar::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}

  .detail-term {{
    font-size: 14px;
    font-weight: 800;
    color: var(--text);
    display: inline;
  }}

  .detail-meta {{
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--text-dim);
    display: inline;
    margin-left: 8px;
  }}

  .detail-section {{
    font-size: 9px;
    font-weight: 800;
    letter-spacing: .2em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin: 6px 0 4px;
  }}

  .translation-chip {{
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 11px;
    display: inline-block;
    margin: 2px 4px 2px 0;
  }}

  .conf-bar {{
    height: 3px;
    border-radius: 2px;
    background: var(--border);
    margin-top: 3px;
    width: 60px;
    display: inline-block;
    vertical-align: middle;
  }}
  .conf-fill {{
    height: 100%;
    border-radius: 2px;
    background: var(--accent-sl);
  }}

  .verified-badge {{
    font-size: 8px;
    font-weight: 800;
    background: #fbbf24;
    color: #000;
    padding: 1px 4px;
    border-radius: 3px;
    margin-left: 4px;
  }}

  /* ---- Canvas ---- */
  #graph-wrap {{
    flex: 1;
    position: relative;
    overflow: hidden;
  }}

  #graph-wrap svg {{
    width: 100%;
    height: 100%;
  }}

  /* ---- Tooltip ---- */
  #tooltip {{
    position: absolute;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 11px;
    pointer-events: none;
    opacity: 0;
    transition: opacity .1s;
    max-width: 220px;
    z-index: 100;
    box-shadow: 0 8px 24px rgba(0,0,0,.5);
  }}

  #tooltip .tt-term {{
    font-weight: 800;
    font-size: 13px;
    margin-bottom: 2px;
  }}

  #tooltip .tt-meta {{
    color: var(--text-dim);
    font-family: var(--font-mono);
    font-size: 9px;
  }}

  /* ---- Legend ---- */
  #legend {{
    position: absolute;
    top: 16px;
    right: 16px;
    background: rgba(17,24,39,.9);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 10px;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }}

  .legend-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--text-dim);
  }}

  .legend-dot {{
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
  }}

  .legend-line {{
    width: 18px;
    height: 2px;
    flex-shrink: 0;
  }}

  /* Buttons */
  .pill {{
    font-size: 10px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 999px;
    border: 1px solid var(--border);
    cursor: pointer;
    transition: all .15s;
    background: var(--surface2);
    color: var(--text-dim);
    letter-spacing: .05em;
  }}

  .pill.active {{
    color: #fff;
    border-color: transparent;
  }}

  .pill[data-lang="en"].active {{ background: var(--accent-en); }}
  .pill[data-lang="sl"].active {{ background: var(--accent-sl); }}
  .pill[data-lang="all"].active {{ background: #475569; }}
  .pill[data-type="phrases"].active {{ background: #7c3aed; }}

  .btn {{
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 5px 12px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .08em;
    border-radius: 6px;
    cursor: pointer;
    transition: all .15s;
  }}
  .btn:hover {{
    border-color: var(--accent-en);
    color: var(--accent-en);
  }}

  #btn-row {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }}
</style>
</head>
<body>

<div id="topbar">
  <div id="topbar-title">
    <h1>KG Inspector</h1>
    <p>Zen Translator</p>
  </div>
  <div class="topbar-sep"></div>
  <div class="topbar-group">
    <label>Search</label>
    <input type="text" id="search-input" placeholder="filter by term…">
  </div>
  <div class="topbar-group">
    <label>Lang</label>
    <div class="filter-pills" id="lang-filter">
      <span class="pill active" data-lang="all">ALL</span>
    </div>
  </div>
  <div class="topbar-group">
    <label>Phrases</label>
    <div class="filter-pills">
      <span class="pill active" id="pill-phrases" data-type="phrases">ON</span>
    </div>
  </div>
  <div class="topbar-group">
    <label>Strength <span id="strength-val">-30</span></label>
    <input type="range" id="strength-slider" min="-200" max="-5" value="-30" step="5">
  </div>
  <div id="btn-row">
    <button class="btn" id="btn-reheat">Reheat</button>
    <button class="btn" id="btn-reset-zoom">Fit</button>
    <button class="btn" id="btn-pin-all">Unpin</button>
  </div>
  <div class="topbar-sep"></div>
  <div id="stat-bar">
    <div class="stat-chip"><span class="stat-label">Nodes</span> <span class="stat-value" id="stat-live-nodes">–</span></div>
    <div class="stat-chip"><span class="stat-label">Edges</span> <span class="stat-value" id="stat-live-edges">–</span></div>
    <div class="stat-chip"><span class="stat-label">Total</span> <span class="stat-value">{len(all_selected)} nodes / {len(selected_edges)} edges</span></div>
  </div>
</div>

<div id="detail-bar">
  <p class="detail-empty">Click a node to inspect it. Drag to reposition. Scroll to zoom. Shift+click to pin.</p>
</div>

<div id="graph-wrap">
  <svg id="graph-svg"></svg>
  <div id="tooltip"></div>
  <div id="legend"></div>
</div>

<script>
// ============================================================
// DATA
// ============================================================
const GRAPH = {graph_data_json};
const STATS = {stats_json};
const LANG_COLORS = {lang_colors_json};
const CONCEPT_COLOR = "#64748b";
const MAPPING_COLOR = "#ec4899";
const AGENT_COLOR = "#f97316";
const SOURCE_COLOR = "#06b6d4";

// ============================================================
// TOP BAR STATS (static, from initial load)
// ============================================================
// Stats are rendered inline in the HTML above.
// Live node/edge counts are updated by buildGraph().

// ============================================================
// LANG FILTER PILLS
// ============================================================
const langs = [...new Set(GRAPH.nodes.map(n=>n.lang).filter(Boolean))];
const langFilter = document.getElementById('lang-filter');
langs.forEach(lang => {{
  const pill = document.createElement('span');
  pill.className = 'pill';
  pill.dataset.lang = lang;
  pill.textContent = lang.toUpperCase();
  langFilter.appendChild(pill);
}});

// ============================================================
// FORCE GRAPH
// ============================================================
const svg = d3.select('#graph-svg');
const wrap = document.getElementById('graph-wrap');

let width = wrap.clientWidth, height = wrap.clientHeight;

let nodes = GRAPH.nodes.map(d => ({{...d}}));
let links = GRAPH.links.map(d => ({{...d}}));

const nodeById = new Map(nodes.map(n => [n.id, n]));

let activeLang = 'all';
let showPhrasesOnly = true;
let searchQuery = '';
let strength = -30;

function filteredData() {{
  let fn = nodes.filter(n => {{
    if (n.type !== 'term') return true; // keep non-term nodes (mappings, concepts, agents, sources)
    if (activeLang !== 'all' && n.lang && n.lang !== activeLang) return false;
    if (showPhrasesOnly && !n.is_phrase) return false;
    if (searchQuery) {{
      const q = searchQuery.toLowerCase();
      if (!n.label.toLowerCase().includes(q)) return false;
    }}
    return true;
  }});

  const fnIds = new Set(fn.map(n => n.id));

  // Prune any now-orphaned mappings, agents, sources, or concepts
  fn = fn.filter(n => {{
    if (n.type === 'term') return true;
    // Check if connected to at least one remaining term
    return links.some(l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      return (sid === n.id && fnIds.has(tid)) || (tid === n.id && fnIds.has(sid));
    }});
  }});

  const finalIds = new Set(fn.map(n => n.id));
  const fl = links.filter(l => {{
    const sid = l.source.id || l.source;
    const tid = l.target.id || l.target;
    return finalIds.has(sid) && finalIds.has(tid);
  }});

  return {{ nodes: fn, links: fl }};
}}

// ---- SVG setup ----
const defs = svg.append('defs');
defs.append('marker')
  .attr('id', 'arrow')
  .attr('viewBox', '0 -4 8 8')
  .attr('refX', 14).attr('refY', 0)
  .attr('markerWidth', 6).attr('markerHeight', 6)
  .attr('orient', 'auto')
  .append('path').attr('d', 'M0,-4L8,0L0,4').attr('fill', '#334155');

const g = svg.append('g').attr('class', 'graph-root');

const zoom = d3.zoom()
  .scaleExtent([0.05, 8])
  .on('zoom', e => {{
    g.attr('transform', e.transform);
  }});
svg.call(zoom);

let linkSel, nodeSel, labelSel;
let simulation;
let selectedNode = null;

function buildGraph() {{
  const {{nodes: fn, links: fl}} = filteredData();

  g.selectAll('*').remove();

  // Links
  linkSel = g.append('g').attr('class', 'links')
    .selectAll('line').data(fl).join('line')
    .attr('stroke', d => d.color || '#334155')
    .attr('stroke-width', d => d.width || 1)
    .attr('stroke-opacity', 0.5)
    .attr('stroke-dasharray', d => d.dashed ? '4 3' : null)
    .attr('marker-end', d => d.relation === 'maps_to' ? 'url(#arrow)' : null);

  // Nodes
  const nodeG = g.append('g').attr('class', 'nodes')
    .selectAll('g').data(fn, d => d.id).join('g')
    .attr('class', 'node-g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', dragStart)
      .on('drag', dragged)
      .on('end', dragEnd))
    .on('click', onNodeClick)
    .on('mouseover', onHover)
    .on('mouseout', onHoverOut);

  nodeSel = nodeG.append('circle')
    .attr('r', d => d.size)
    .attr('fill', d => d.color)
    .attr('stroke', d => d.verified ? '#fbbf24' : 'rgba(255,255,255,.08)')
    .attr('stroke-width', d => d.verified ? 2 : 1);

  labelSel = nodeG.append('text')
    .text(d => d.label)
    .attr('font-size', d => Math.max(8, Math.min(13, d.size * 0.9)))
    .attr('dy', d => d.size + 11)
    .attr('text-anchor', 'middle')
    .attr('fill', '#94a3b8')
    .attr('paint-order', 'stroke')
    .attr('stroke', '#0b0f1a')
    .attr('stroke-width', 3)
    .style('pointer-events', 'none')
    .style('display', d => d.size > 6 ? 'block' : 'none');

  if (simulation) simulation.stop();

  simulation = d3.forceSimulation(fn)
    .force('link', d3.forceLink(fl).id(d => d.id).distance(d => {{
      if (d.relation === 'maps_to' || d.relation === 'has_mapping') return 45;
      return 80;
    }}).strength(0.4))
    .force('charge', d3.forceManyBody().strength(strength))
    .force('center', d3.forceCenter(width/2, height/2))
    .force('collision', d3.forceCollide().radius(d => d.size + 5))
    .on('tick', ticked);

  document.getElementById('stat-live-nodes').textContent = fn.length;
  document.getElementById('stat-live-edges').textContent = fl.length;
}}

function ticked() {{
  linkSel
    .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
    .attr('x2', d => d.target.x).attr('y2', d => d.target.y);

  g.selectAll('.node-g')
    .attr('transform', d => `translate(${{d.x}},${{d.y}})`);
}}

// ---- Drag ----
function dragStart(event, d) {{
  if (!event.active) simulation.alphaTarget(0.3).restart();
  d.fx = d.x; d.fy = d.y;
}}
function dragged(event, d) {{
  d.fx = event.x; d.fy = event.y;
}}
function dragEnd(event, d) {{
  if (!event.active) simulation.alphaTarget(0);
  if (event.sourceEvent && event.sourceEvent.shiftKey) {{
    // shift-drag pins node
  }} else {{
    d.fx = null; d.fy = null;
  }}
}}

// ---- Click / hover ----
function onNodeClick(event, d) {{
  const fn = filteredData().nodes;
  const fl = filteredData().links;

  if (event.shiftKey) {{
    if (d.fx !== null && d.fx !== undefined) {{
      d.fx = null; d.fy = null;
      d3.select(this).select('circle').attr('stroke', d.verified ? '#fbbf24' : 'rgba(255,255,255,.08)');
    }} else {{
      d.fx = d.x; d.fy = d.y;
      d3.select(this).select('circle').attr('stroke', '#f472b6').attr('stroke-width', 2.5);
    }}
    return;
  }}

  selectedNode = d;
  renderDetail(d, fn, fl);

  // Highlight connection web
  const connectedIds = new Set();
  g.selectAll('.links line').each(e => {{
    const sid = e.source.id || e.source;
    const tid = e.target.id || e.target;
    if (sid === d.id) connectedIds.add(tid);
    if (tid === d.id) connectedIds.add(sid);
  }});

  g.selectAll('.node-g circle')
    .attr('opacity', n => n.id === d.id || connectedIds.has(n.id) ? 1 : 0.15);
  g.selectAll('.links line')
    .attr('stroke-opacity', e => {{
      const sid = e.source.id || e.source;
      const tid = e.target.id || e.target;
      return (sid === d.id || tid === d.id) ? 0.9 : 0.04;
    }});
  g.selectAll('.node-g text')
    .style('display', n => (n.id === d.id || connectedIds.has(n.id)) ? 'block' : 'none');
}}

function onHover(event, d) {{
  const tt = document.getElementById('tooltip');
  tt.innerHTML = `<div class="tt-term" style="color:${{d.color}}">${{d.label}}</div>
    <div class="tt-meta">${{d.type.toUpperCase()}}${{d.verified ? ' · ★ verified' : ''}}</div>`;
  tt.style.opacity = 1;
  moveTooltip(event);
}}

function onHoverOut() {{
  document.getElementById('tooltip').style.opacity = 0;
}}

svg.on('mousemove', e => moveTooltip(e));
svg.on('click', function(event) {{
  if (event.target === this || event.target.tagName === 'svg') {{
    selectedNode = null;
    g.selectAll('.node-g circle').attr('opacity', 1);
    g.selectAll('.links line').attr('stroke-opacity', 0.5);
    g.selectAll('.node-g text').style('display', d => d.size > 6 ? 'block' : 'none');
    document.getElementById('detail-bar').innerHTML =
      '<p class="detail-empty">Click a node to inspect it.</p>';
  }}
}});

function moveTooltip(event) {{
  const tt = document.getElementById('tooltip');
  const x = event.clientX, y = event.clientY;
  tt.style.left = (x + 14) + 'px';
  tt.style.top  = (y - 10) + 'px';
}}

// ---- Node detail panel ----
function renderDetail(d, fn, fl) {{
  const el = document.getElementById('detail-bar');

  if (d.type === 'term') {{
    const mappings = [];

    // Scan links connected to this term to trace full translation path through mappings
    fl.forEach(l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      if (sid === d.id && l.relation === 'has_mapping') {{
        const mapNode = fn.find(n => n.id === tid);
        if (mapNode) {{
          fl.forEach(l2 => {{
            const s2 = l2.source.id || l2.source;
            const t2 = l2.target.id || l2.target;
            if (s2 === mapNode.id && l2.relation === 'maps_to') {{
              const tgtNode = fn.find(n => n.id === t2);
              if (tgtNode) {{
                mappings.push({{
                  term: tgtNode.label,
                  color: tgtNode.color,
                  lineage: mapNode.lineage || 'general',
                  gloss: mapNode.gloss || '',
                  conf: mapNode.confidence || 0.5,
                  verified: mapNode.verified
                }});
              }}
            }}
          }});
        }}
      }}
    }});

    // Fallback legacy translates_to check
    fl.forEach(l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      if (sid === d.id && l.relation === 'translates_to') {{
        const tgtNode = fn.find(n => n.id === tid);
        if (tgtNode && !mappings.some(m => m.term === tgtNode.label)) {{
          mappings.push({{
            term: tgtNode.label,
            color: tgtNode.color,
            lineage: 'legacy',
            gloss: '',
            conf: l.confidence || 0.5,
            verified: l.verified
          }});
        }}
      }}
    }});

    mappings.sort((a,b) => b.conf - a.conf);

    const transHtml = mappings.length
      ? mappings.map(m => `
        <div class="translation-chip">
          <span style="color:${{m.color}}">${{m.term}}</span>
          ${{m.verified ? '<span class="verified-badge">★</span>' : ''}}
          <div style="font-size:9px;color:var(--text-dim);margin-top:2px;">
            Lineage: <em>${{m.lineage}}</em> ${{m.gloss ? ' · Note: ' + m.gloss : ''}}
          </div>
          <div class="conf-bar"><div class="conf-fill" style="width:${{(m.conf*100).toFixed(0)}}%"></div></div>
        </div>`).join('')
      : '<div style="color:var(--text-muted);font-size:11px;font-style:italic">No translation mappings visible</div>';

    el.innerHTML = `
      <div class="detail-term" style="color:${{d.color}}">${{d.label}}</div>
      <div class="detail-meta">TERM · ${{d.lang.toUpperCase()}} · freq ${{d.freq}} ${{d.is_phrase ? '· phrase' : ''}}</div>
      <div class="detail-section">Contextual Translations</div>
      ${{transHtml}}
      <div class="detail-section">Node ID</div>
      <div style="font-family:var(--font-mono);font-size:9px;color:var(--text-muted);word-break:break-all">${{d.id}}</div>
    `;
  }}
  else if (d.type === 'translation_mapping') {{
    let sourceTerm = "(unknown)";
    let targetTerm = "(unknown)";
    let agents = [];
    let sources = [];

    fl.forEach(l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      if (tid === d.id && l.relation === 'has_mapping') {{
        const sNode = fn.find(n => n.id === sid);
        if (sNode) sourceTerm = sNode.label;
      }}
      if (sid === d.id && l.relation === 'maps_to') {{
        const tNode = fn.find(n => n.id === tid);
        if (tNode) targetTerm = tNode.label;
      }}
      if (sid === d.id && l.relation === 'instantiated_in') {{
        const srcNode = fn.find(n => n.id === tid);
        if (srcNode) sources.push(srcNode.label);
      }}
      if (sid === d.id && l.relation === 'attributed_to') {{
        const agNode = fn.find(n => n.id === tid);
        if (agNode) agents.push(agNode.label);
      }}
    }});

    const lineage = d.lineage || "general";
    const register = d.register || "academic";
    const gloss = d.gloss || "(no note)";
    const conf = d.confidence || 0.5;

    el.innerHTML = `
      <div class="detail-term" style="color:${{d.color}}">Mapping Context</div>
      <div class="detail-meta">TRANSLATION MAPPING ${{d.verified ? '· <span style="color:#fbbf24">★ verified</span>' : ''}}</div>

      <div style="font-size:12px;margin-bottom:12px;">
        <strong>${{sourceTerm}}</strong> ➔ <strong>${{targetTerm}}</strong>
      </div>

      <div class="detail-section">Lineage Parameters</div>
      <div style="font-size:11px;display:flex;flex-direction:column;gap:4px;">
        <div>• Lineage: <em>${{lineage}}</em></div>
        <div>• Register: <code>${{register}}</code></div>
        <div>• Note: <span style="color:var(--text-dim)">${{gloss}}</span></div>
        <div>• Confidence: <code>${{(conf*100).toFixed(0)}}%</code></div>
      </div>

      ${{agents.length ? '<div class="detail-section">Attributed Authors/Translators</div>' + agents.map(a => `<div>• ${{a}}</div>`).join('') : ''}}
      ${{sources.length ? '<div class="detail-section">Published In</div>' + sources.map(s => `<div>• <em>${{s}}</em></div>`).join('') : ''}}

      <div class="detail-section">Node ID</div>
      <div style="font-family:var(--font-mono);font-size:9px;color:var(--text-muted);word-break:break-all">${{d.id}}</div>
    `;
  }}
  else if (d.type === 'agent') {{
    const linkedMaps = [];
    fl.forEach(l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      if (tid === d.id && l.relation === 'attributed_to') {{
        linkedMaps.push(sid);
      }}
    }});

    el.innerHTML = `
      <div class="detail-term" style="color:${{d.color}}">${{d.label}}</div>
      <div class="detail-meta">AGENT · ${{d.role || 'author/translator'}}</div>

      <div class="detail-section">Linked Translations (${{linkedMaps.length}})</div>
      ${{linkedMaps.length ? '<div style="font-size:11px;">' + linkedMaps.map(m => `<div>• <code>${{m}}</code></div>`).join('') + '</div>' : '<div style="color:var(--text-muted);font-style:italic">No active mappings shown</div>'}}

      <div class="detail-section">Node ID</div>
      <div style="font-family:var(--font-mono);font-size:9px;color:var(--text-muted);word-break:break-all">${{d.id}}</div>
    `;
  }}
  else if (d.type === 'source_text') {{
    const linkedMaps = [];
    fl.forEach(l => {{
      const sid = l.source.id || l.source;
      const tid = l.target.id || l.target;
      if (tid === d.id && l.relation === 'instantiated_in') {{
        linkedMaps.push(sid);
      }}
    }});

    el.innerHTML = `
      <div class="detail-term" style="color:${{d.color}}"><em>${{d.label}}</em></div>
      <div class="detail-meta">SOURCE TEXT ${{d.year ? '· ' + d.year : ''}}</div>

      <div class="detail-section">Linked Translations (${{linkedMaps.length}})</div>
      ${{linkedMaps.length ? '<div style="font-size:11px;">' + linkedMaps.map(m => `<div>• <code>${{m}}</code></div>`).join('') + '</div>' : '<div style="color:var(--text-muted);font-style:italic">No active mappings shown</div>'}}

      <div class="detail-section">Node ID</div>
      <div style="font-family:var(--font-mono);font-size:9px;color:var(--text-muted);word-break:break-all">${{d.id}}</div>
    `;
  }}
  else if (d.type === 'concept') {{
    el.innerHTML = `
      <div class="detail-term" style="color:${{d.color}}">${{d.label}}</div>
      <div class="detail-meta">CONCEPT ${{d.domain ? '· ' + d.domain : ''}}</div>
      <p style="font-size:11px;line-height:1.4;margin-top:6px;color:var(--text-dim)">${{d.definition || '(No definition provided)'}}</p>
      <div class="detail-section">Node ID</div>
      <div style="font-family:var(--font-mono);font-size:9px;color:var(--text-muted);word-break:break-all">${{d.id}}</div>
    `;
  }}
}}

// ============================================================
// FILTERS & CONTROLS
// ============================================================

function rebuildGraph() {{
  buildGraph();
  selectedNode = null;
  document.getElementById('detail-bar').innerHTML =
    '<p class="detail-empty">Click a node to inspect it.</p>';
}}

document.getElementById('lang-filter').addEventListener('click', e => {{
  const pill = e.target.closest('.pill');
  if (!pill) return;
  document.querySelectorAll('#lang-filter .pill').forEach(p => p.classList.remove('active'));
  pill.classList.add('active');
  activeLang = pill.dataset.lang;
  rebuildGraph();
}});

document.getElementById('pill-phrases').addEventListener('click', function() {{
  showPhrasesOnly = !showPhrasesOnly;
  this.classList.toggle('active', showPhrasesOnly);
  rebuildGraph();
}});

let searchTimeout;
document.getElementById('search-input').addEventListener('input', function() {{
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {{
    searchQuery = this.value.trim();
    rebuildGraph();
  }}, 250);
}});

document.getElementById('strength-slider').addEventListener('input', function() {{
  strength = +this.value;
  document.getElementById('strength-val').textContent = this.value;
  if (simulation) {{
    simulation.force('charge', d3.forceManyBody().strength(strength));
    simulation.alpha(0.3).restart();
  }}
}});

document.getElementById('btn-reheat').addEventListener('click', () => {{
  if (simulation) simulation.alpha(0.8).restart();
}});

document.getElementById('btn-reset-zoom').addEventListener('click', () => {{
  svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity.translate(width/2, height/2).scale(0.7));
}});

document.getElementById('btn-pin-all').addEventListener('click', () => {{
  nodes.forEach(n => {{ n.fx = null; n.fy = null; }});
  g.selectAll('.node-g circle')
    .attr('stroke', d => d.verified ? '#fbbf24' : 'rgba(255,255,255,.08)')
    .attr('stroke-width', d => d.verified ? 2 : 1);
  if (simulation) simulation.alpha(0.5).restart();
}});

// ============================================================
// LEGEND
// ============================================================
function buildLegend() {{
  const el = document.getElementById('legend');
  const items = [
    ...Object.entries(LANG_COLORS).map(([lang, color]) => ({{
      type: 'dot', color, label: lang.toUpperCase() + ' Term'
    }})),
    {{ type: 'dot', color: '#64748b', label: 'Concept' }},
    {{ type: 'dot', color: '#ec4899', label: 'Lineage Bridge' }},
    {{ type: 'dot', color: '#f97316', label: 'Translator/Author' }},
    {{ type: 'dot', color: '#06b6d4', label: 'Source Text/Book' }},
    {{ type: 'line', color: '#ec4899', label: 'has_mapping', dashed: true }},
    {{ type: 'line', color: '#ec4899', label: 'maps_to', dashed: false }},
  ];

  el.innerHTML = items.map(item => {{
    if (item.type === 'dot') {{
      const ring = item.ring ? `outline: 2px solid ${{item.color}}; outline-offset: 1px; background: transparent;` : `background:${{item.color}}`;
      return `<div class="legend-row"><div class="legend-dot" style="${{ring}}"></div><span>${{item.label}}</span></div>`;
    }} else {{
      const dashed = item.dashed ? `background: repeating-linear-gradient(90deg,${{item.color}} 0,${{item.color}} 4px,transparent 4px,transparent 7px)` : `background:${{item.color}}`;
      return `<div class="legend-row"><div class="legend-line" style="${{dashed}}"></div><span>${{item.label}}</span></div>`;
    }}
  }}).join('');
}}
buildLegend();

// ============================================================
// RESIZE
// ============================================================
window.addEventListener('resize', () => {{
  width = wrap.clientWidth;
  height = wrap.clientHeight;
  if (simulation) simulation.force('center', d3.forceCenter(width/2, height/2)).alpha(0.1).restart();
}});

// ============================================================
// INIT
// ============================================================
buildGraph();
svg.call(zoom.transform, d3.zoomIdentity.translate(width/2, height/2).scale(0.65));
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
out_path = pathlib.Path(args.out)
out_path.write_text(html, encoding="utf-8")
print(f"[KG Inspector] Written: {out_path.resolve()}")
print(f"[KG Inspector] Open in your browser: file://{out_path.resolve()}")
