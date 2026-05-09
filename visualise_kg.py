"""visualise_kg.py — Standalone KG inspector for Zen Translator.

Reads your live KG JSON at config.KG_DB_PATH and writes a self-contained
HTML file with an interactive D3 force-directed graph. No pyvis required.

Usage:
    python visualise_kg.py                        # uses config.KG_DB_PATH
    python visualise_kg.py path/to/kg.json        # explicit path
    python visualise_kg.py --limit 500            # show up to N term nodes
    python visualise_kg.py --out kg_view.html     # custom output path

The HTML file is fully self-contained (D3 bundled via CDN at generation time,
then inlined — or left as CDN if you have internet when opening the file).
Open it in any modern browser; no server needed.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import textwrap
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Visualise Zen Translator Knowledge Graph")
parser.add_argument("kg_path", nargs="?", default=None, help="Path to KG JSON file (default: config.KG_DB_PATH)")
parser.add_argument("--limit", type=int, default=300, help="Max term nodes to show (default: 300)")
parser.add_argument("--out", default="kg_inspect.html", help="Output HTML path (default: kg_inspect.html)")
parser.add_argument("--lang", default=None, help="Filter to a specific language (e.g. en, sl)")
parser.add_argument("--min-freq", type=int, default=1, help="Minimum term frequency to include (default: 1)")
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
        print("[ERROR] Could not import config. Either pass the KG JSON path as an argument,")
        print("        or run this script from your project root where config.py lives.")
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

# ---------------------------------------------------------------------------
# Filter and select nodes
# ---------------------------------------------------------------------------

# Index nodes by id
node_by_id = {n["id"]: n for n in all_nodes}

# Count node types
type_counts = Counter(n.get("type", "unknown") for n in all_nodes)
print(f"[KG Inspector] Node types: {dict(type_counts)}")

# Select term nodes meeting criteria, sorted by frequency desc
term_nodes = [
    n for n in all_nodes
    if n.get("type") == "term"
    and n.get("frequency", 1) >= args.min_freq
    and (args.lang is None or n.get("lang") == args.lang)
]
term_nodes.sort(key=lambda n: n.get("frequency", 0), reverse=True)
term_nodes = term_nodes[:args.limit]
selected_ids = {n["id"] for n in term_nodes}

# Also include concept nodes connected to selected terms
concept_nodes = []
for edge in all_edges:
    if edge.get("relation") == "instantiates_concept":
        if edge["source"] in selected_ids:
            cid = edge["target"]
            if cid in node_by_id and cid not in selected_ids:
                concept_nodes.append(node_by_id[cid])
                selected_ids.add(cid)

all_selected = term_nodes + concept_nodes
print(f"[KG Inspector] Visualising: {len(term_nodes)} terms + {len(concept_nodes)} concepts = {len(all_selected)} nodes")

# Filter edges to only those between selected nodes
selected_edges = [
    e for e in all_edges
    if e["source"] in selected_ids and e["target"] in selected_ids
]
print(f"[KG Inspector] Filtered edges: {len(selected_edges)}")

# ---------------------------------------------------------------------------
# Build graph data for D3
# ---------------------------------------------------------------------------

# Language colour mapping
LANG_COLORS = {
    "en": "#3b82f6",   # blue
    "sl": "#10b981",   # emerald
    "de": "#f59e0b",   # amber
    "fr": "#8b5cf6",   # violet
    "it": "#ef4444",   # red
}
CONCEPT_COLOR = "#64748b"  # slate

def node_color(n: dict) -> str:
    if n.get("type") == "concept":
        return CONCEPT_COLOR
    return LANG_COLORS.get(n.get("lang", ""), "#94a3b8")

def node_size(n: dict) -> float:
    freq = n.get("frequency", 1)
    # Scale: min 4, max 24, log-ish
    import math
    return max(4, min(24, 4 + math.log1p(freq) * 3))

def node_label(n: dict) -> str:
    return n.get("display_form") or n.get("term") or n.get("label") or n.get("id", "")

# Build D3-compatible nodes and links
d3_nodes = []
for n in all_selected:
    d3_nodes.append({
        "id": n["id"],
        "label": node_label(n),
        "type": n.get("type", "term"),
        "lang": n.get("lang", ""),
        "freq": n.get("frequency", 1),
        "is_phrase": n.get("is_phrase", False),
        "domain": n.get("domain", ""),
        "verified": False,  # will be updated from edges
        "color": node_color(n),
        "size": node_size(n),
    })

# Mark verified nodes (any verified translation edge touching them)
verified_ids: set = set()
for e in selected_edges:
    if e.get("relation") == "translates_to" and e.get("verified"):
        verified_ids.add(e["source"])
        verified_ids.add(e["target"])
for n in d3_nodes:
    if n["id"] in verified_ids:
        n["verified"] = True

EDGE_STYLE = {
    "translates_to":        {"color": "#94a3b8", "width": 1.5, "dashed": False},
    "instantiates_concept": {"color": "#c4b5fd", "width": 1.0, "dashed": True},
    "subclass_of":          {"color": "#fcd34d", "width": 1.0, "dashed": True},
    "appears_in_segment":   {"color": "#6ee7b7", "width": 0.8, "dashed": True},
}

d3_links = []
seen_link_pairs: set = set()
for e in selected_edges:
    pair = tuple(sorted([e["source"], e["target"]]))
    rel = e.get("relation", "related_to")
    # Deduplicate bidirectional translates_to edges for display
    if rel == "translates_to" and pair in seen_link_pairs:
        continue
    seen_link_pairs.add(pair)
    style = EDGE_STYLE.get(rel, {"color": "#cbd5e1", "width": 1.0, "dashed": False})
    d3_links.append({
        "source": e["source"],
        "target": e["target"],
        "relation": rel,
        "confidence": e.get("confidence", 0.5),
        "verified": e.get("verified", False),
        **style,
    })

# ---------------------------------------------------------------------------
# Stats for sidebar
# ---------------------------------------------------------------------------

lang_counts = Counter(n.get("lang", "?") for n in term_nodes)
phrase_count = sum(1 for n in term_nodes if n.get("is_phrase"))
verified_count = len(verified_ids)
domain_counts = Counter(n.get("domain", "") for n in all_selected if n.get("domain"))

stats = {
    "total_nodes_in_kg": len(all_nodes),
    "total_edges_in_kg": len(all_edges),
    "showing_nodes": len(all_selected),
    "showing_edges": len(d3_links),
    "term_nodes": len(term_nodes),
    "concept_nodes": len(concept_nodes),
    "phrase_nodes": phrase_count,
    "verified_nodes": verified_count,
    "lang_counts": dict(lang_counts),
    "domain_counts": dict(domain_counts.most_common(10)),
    "type_counts": dict(type_counts),
}

# ---------------------------------------------------------------------------
# HTML template
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
    --accent-verified: #fbbf24;
    --font-mono: 'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
    --font-ui: 'IBM Plex Sans', system-ui, sans-serif;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-ui);
    display: flex;
    height: 100vh;
    overflow: hidden;
  }}

  /* ---- Sidebar ---- */
  #sidebar {{
    width: 300px;
    min-width: 300px;
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    z-index: 10;
  }}

  #sidebar-header {{
    padding: 20px 20px 14px;
    border-bottom: 1px solid var(--border);
  }}

  #sidebar-header h1 {{
    font-size: 11px;
    font-weight: 800;
    letter-spacing: .25em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-bottom: 4px;
  }}

  #sidebar-header p {{
    font-size: 10px;
    color: var(--text-muted);
    font-family: var(--font-mono);
  }}

  #controls {{
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }}

  .control-group label {{
    display: block;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .15em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-bottom: 5px;
  }}

  .control-group input[type=range] {{
    width: 100%;
    accent-color: var(--accent-en);
  }}

  .control-group input[type=text] {{
    width: 100%;
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 10px;
    font-size: 12px;
    font-family: var(--font-mono);
    border-radius: 6px;
    outline: none;
  }}

  .control-group input[type=text]:focus {{
    border-color: var(--accent-en);
  }}

  .filter-pills {{
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }}

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
  .pill[data-type="verified"].active {{ background: var(--accent-verified); color: #000; }}

  #stats {{
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}

  .stat-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}

  .stat-label {{
    font-size: 10px;
    color: var(--text-muted);
    letter-spacing: .05em;
  }}

  .stat-value {{
    font-size: 11px;
    font-weight: 700;
    font-family: var(--font-mono);
    color: var(--text);
  }}

  .stat-section-label {{
    font-size: 9px;
    font-weight: 800;
    letter-spacing: .2em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin-top: 4px;
    padding-top: 8px;
    border-top: 1px solid var(--border);
  }}

  .lang-bar {{
    display: flex;
    height: 4px;
    border-radius: 2px;
    overflow: hidden;
    margin-top: 6px;
  }}

  #node-detail {{
    flex: 1;
    overflow-y: auto;
    padding: 14px 16px;
  }}

  #node-detail::-webkit-scrollbar {{ width: 4px; }}
  #node-detail::-webkit-scrollbar-track {{ background: transparent; }}
  #node-detail::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}

  .detail-empty {{
    color: var(--text-muted);
    font-size: 11px;
    font-style: italic;
    margin-top: 8px;
    line-height: 1.6;
  }}

  .detail-term {{
    font-size: 16px;
    font-weight: 800;
    color: var(--text);
    margin-bottom: 4px;
    word-break: break-word;
  }}

  .detail-meta {{
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--text-muted);
    margin-bottom: 12px;
  }}

  .detail-section {{
    font-size: 9px;
    font-weight: 800;
    letter-spacing: .2em;
    text-transform: uppercase;
    color: var(--text-muted);
    margin: 12px 0 6px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }}

  .translation-chip {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 11px;
    margin: 2px;
    font-family: var(--font-mono);
  }}

  .conf-bar {{
    height: 3px;
    border-radius: 2px;
    background: var(--border);
    margin-top: 2px;
    width: 100%;
  }}
  .conf-fill {{
    height: 100%;
    border-radius: 2px;
    background: var(--accent-sl);
  }}

  .verified-badge {{
    font-size: 8px;
    font-weight: 800;
    letter-spacing: .1em;
    background: var(--accent-verified);
    color: #000;
    padding: 1px 5px;
    border-radius: 3px;
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

  /* ---- Minimap ---- */
  #minimap {{
    position: absolute;
    bottom: 16px;
    right: 16px;
    width: 140px;
    height: 90px;
    background: rgba(17,24,39,.85);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
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
    font-family: var(--font-ui);
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

<div id="sidebar">
  <div id="sidebar-header">
    <h1>KG Inspector</h1>
    <p id="kg-path-label">Zen Translator · Knowledge Graph</p>
  </div>

  <div id="controls">
    <div class="control-group">
      <label>Search nodes</label>
      <input type="text" id="search-input" placeholder="filter by term…">
    </div>
    <div class="control-group">
      <label>Language</label>
      <div class="filter-pills" id="lang-filter">
        <span class="pill active" data-lang="all">ALL</span>
      </div>
    </div>
    <div class="control-group">
      <label>Show</label>
      <div class="filter-pills">
        <span class="pill active" id="pill-phrases" data-type="phrases">Phrases only</span>
        <span class="pill" id="pill-verified" data-type="verified">Verified ★</span>
      </div>
    </div>
    <div class="control-group">
      <label>Link strength <span id="strength-val">-30</span></label>
      <input type="range" id="strength-slider" min="-200" max="-5" value="-30" step="5">
    </div>
    <div id="btn-row">
      <button class="btn" id="btn-reheat">Reheat ↺</button>
      <button class="btn" id="btn-reset-zoom">Reset zoom</button>
      <button class="btn" id="btn-pin-all">Unpin all</button>
    </div>
  </div>

  <div id="stats"></div>

  <div id="node-detail">
    <p class="detail-empty">Click a node to inspect it.<br><br>
    Drag to reposition. Scroll to zoom.<br>
    Shift+click to pin/unpin a node.</p>
  </div>
</div>

<div id="graph-wrap">
  <svg id="graph-svg"></svg>
  <div id="tooltip"></div>
  <div id="minimap"><svg id="minimap-svg"></svg></div>
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

// ============================================================
// SIDEBAR STATS
// ============================================================
function renderStats() {{
  const el = document.getElementById('stats');
  const lc = STATS.lang_counts;
  const total = Object.values(lc).reduce((a,b)=>a+b, 0) || 1;

  const barSegs = Object.entries(lc).map(([lang, count]) => {{
    const color = LANG_COLORS[lang] || '#94a3b8';
    const pct = (count / total * 100).toFixed(1);
    return `<div style="flex:${{count}};background:${{color}}" title="${{lang}}: ${{count}}"></div>`;
  }}).join('');

  el.innerHTML = `
    <div class="stat-section-label">Graph overview</div>
    ${{statRow('Total KG nodes', STATS.total_nodes_in_kg.toLocaleString())}}
    ${{statRow('Total KG edges', STATS.total_edges_in_kg.toLocaleString())}}
    ${{statRow('Showing nodes', STATS.showing_nodes)}}
    ${{statRow('Showing edges', STATS.showing_edges)}}
    ${{statRow('Phrase nodes', STATS.phrase_nodes)}}
    ${{statRow('Verified pairs', STATS.verified_nodes)}}
    <div class="stat-section-label">By language</div>
    ${{Object.entries(lc).map(([l,c])=> statRow(l.toUpperCase(), c)).join('')}}
    <div class="lang-bar">${{barSegs}}</div>
    ${{Object.keys(STATS.domain_counts).length ? '<div class="stat-section-label">Domains</div>' + Object.entries(STATS.domain_counts).map(([d,c])=>statRow(d||'(untagged)', c)).join('') : ''}}
  `;
}}

function statRow(label, value) {{
  return `<div class="stat-row"><span class="stat-label">${{label}}</span><span class="stat-value">${{value}}</span></div>`;
}}

renderStats();

// ============================================================
// LANG FILTER PILLS (build from data)
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

// Build node/link maps
let nodes = GRAPH.nodes.map(d => ({{...d}}));
let links = GRAPH.links.map(d => ({{...d}}));

const nodeById = new Map(nodes.map(n => [n.id, n]));

// Active filters
let activeLang = 'all';
let showPhrasesOnly = true;
let showVerifiedOnly = false;
let searchQuery = '';
let strength = -30;

function filteredData() {{
  let fn = nodes.filter(n => {{
    if (n.type === 'concept') return true; // always keep concepts if connected
    if (activeLang !== 'all' && n.lang && n.lang !== activeLang) return false;
    if (showPhrasesOnly && !n.is_phrase && n.type === 'term') return false;
    if (showVerifiedOnly && !n.verified) return false;
    if (searchQuery) {{
      const q = searchQuery.toLowerCase();
      if (!n.label.toLowerCase().includes(q)) return false;
    }}
    return true;
  }});
  const fnIds = new Set(fn.map(n => n.id));
  // Remove orphaned concepts
  fn = fn.filter(n => n.type !== 'concept' || links.some(l =>
    (l.source.id || l.source) === n.id || (l.target.id || l.target) === n.id && fnIds.has(l.source.id || l.source)
  ));
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
// Arrowhead
defs.append('marker')
  .attr('id', 'arrow')
  .attr('viewBox', '0 -4 8 8')
  .attr('refX', 14).attr('refY', 0)
  .attr('markerWidth', 6).attr('markerHeight', 6)
  .attr('orient', 'auto')
  .append('path').attr('d', 'M0,-4L8,0L0,4').attr('fill', '#334155');

// Glow filter for highlighted nodes
const glow = defs.append('filter').attr('id', 'glow');
glow.append('feGaussianBlur').attr('stdDeviation', 3).attr('result', 'blur');
const feMerge = glow.append('feMerge');
feMerge.append('feMergeNode').attr('in', 'blur');
feMerge.append('feMergeNode').attr('in', 'SourceGraphic');

const g = svg.append('g').attr('class', 'graph-root');

// Zoom
const zoom = d3.zoom()
  .scaleExtent([0.05, 8])
  .on('zoom', e => {{
    g.attr('transform', e.transform);
    updateMinimap();
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
    .attr('marker-end', d => d.relation === 'translates_to' ? null : 'url(#arrow)');

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
    .attr('dy', d => d.size + 10)
    .attr('text-anchor', 'middle')
    .attr('fill', '#94a3b8')
    .attr('paint-order', 'stroke')
    .attr('stroke', '#0b0f1a')
    .attr('stroke-width', 3)
    .style('pointer-events', 'none')
    .style('display', d => d.size > 6 ? 'block' : 'none');

  // Simulation
  if (simulation) simulation.stop();

  simulation = d3.forceSimulation(fn)
    .force('link', d3.forceLink(fl).id(d => d.id).distance(d => {{
      if (d.relation === 'translates_to') return 80;
      if (d.relation === 'instantiates_concept') return 60;
      return 100;
    }}).strength(0.4))
    .force('charge', d3.forceManyBody().strength(strength))
    .force('center', d3.forceCenter(width/2, height/2))
    .force('collision', d3.forceCollide().radius(d => d.size + 4))
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

  updateMinimap();
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
  // Shift+drag: pin node
  if (event.sourceEvent && event.sourceEvent.shiftKey) {{
    // keep pinned
  }} else {{
    d.fx = null; d.fy = null;
  }}
}}

// ---- Click / hover ----
function onNodeClick(event, d) {{
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
  renderDetail(d);
  // Highlight connected nodes
  const connectedIds = new Set();
  g.selectAll('.links line').each(e => {{
    const sid = e.source.id || e.source;
    const tid = e.target.id || e.target;
    if (sid === d.id) connectedIds.add(tid);
    if (tid === d.id) connectedIds.add(sid);
  }});
  g.selectAll('.node-g circle')
    .attr('opacity', n => n.id === d.id || connectedIds.has(n.id) ? 1 : 0.2);
  g.selectAll('.links line')
    .attr('stroke-opacity', e => {{
      const sid = e.source.id || e.source;
      const tid = e.target.id || e.target;
      return (sid === d.id || tid === d.id) ? 0.9 : 0.05;
    }});
  g.selectAll('.node-g text')
    .style('display', n => (n.id === d.id || connectedIds.has(n.id)) ? 'block' : 'none');
}}

function onHover(event, d) {{
  const tt = document.getElementById('tooltip');
  tt.innerHTML = `<div class="tt-term" style="color:${{d.color}}">${{d.label}}</div>
    <div class="tt-meta">${{d.lang ? d.lang.toUpperCase() + ' · ' : ''}}freq ${{d.freq}}${{d.verified ? ' · ★ verified' : ''}}${{d.is_phrase ? ' · phrase' : ''}}</div>`;
  tt.style.opacity = 1;
  moveTooltip(event);
}}

function onHoverOut() {{
  document.getElementById('tooltip').style.opacity = 0;
}}

svg.on('mousemove', e => moveTooltip(e));
svg.on('click', function(event) {{
  if (event.target === this || event.target.tagName === 'svg') {{
    // Clicked background — deselect
    selectedNode = null;
    g.selectAll('.node-g circle').attr('opacity', 1);
    g.selectAll('.links line').attr('stroke-opacity', 0.5);
    g.selectAll('.node-g text').style('display', d => d.size > 6 ? 'block' : 'none');
    document.getElementById('node-detail').innerHTML =
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
function renderDetail(d) {{
  // Find connected links and their nodes from current simulation
  const outgoing = [], incoming = [];
  g.selectAll('.links line').each(e => {{
    const sid = e.source.id || e.source;
    const tid = e.target.id || e.target;
    if (sid === d.id && e.relation === 'translates_to') {{
      const tNode = nodeById.get(tid);
      if (tNode) outgoing.push({{node: tNode, conf: e.confidence, verified: e.verified}});
    }}
  }});

  // Sort by confidence
  outgoing.sort((a,b) => b.conf - a.conf);

  const translationsHtml = outgoing.length
    ? outgoing.map(t => `
      <div class="translation-chip">
        <span style="color:${{t.node.color}}">${{t.node.label}}</span>
        ${{t.verified ? '<span class="verified-badge">★</span>' : ''}}
        <div style="flex:1">
          <div class="conf-bar"><div class="conf-fill" style="width:${{(t.conf*100).toFixed(0)}}%;opacity:${{0.4+t.conf*0.6}}"></div></div>
        </div>
        <span style="font-size:9px;color:#64748b">${{(t.conf*100).toFixed(0)}}%</span>
      </div>`).join('')
    : '<span style="color:#475569;font-size:11px">No translations in current view</span>';

  document.getElementById('node-detail').innerHTML = `
    <div class="detail-term" style="color:${{d.color}}">${{d.label}}</div>
    <div class="detail-meta">
      ${{d.lang ? d.lang.toUpperCase() : 'concept'}}
      · freq ${{d.freq}}
      ${{d.verified ? '· <span style="color:#fbbf24">★ verified</span>' : ''}}
      ${{d.is_phrase ? '· phrase' : ''}}
      ${{d.domain ? '· ' + d.domain : ''}}
    </div>
    <div class="detail-section">Translations</div>
    ${{translationsHtml}}
    <div class="detail-section">Node ID</div>
    <div style="font-family:var(--font-mono);font-size:9px;color:#475569;word-break:break-all">${{d.id}}</div>
  `;
}}

// ============================================================
// FILTERS & CONTROLS
// ============================================================

// Add live node/edge count to stats
document.getElementById('stats').insertAdjacentHTML('beforeend', `
  <div class="stat-section-label">Current view</div>
  <div class="stat-row"><span class="stat-label">Visible nodes</span><span class="stat-value" id="stat-live-nodes">–</span></div>
  <div class="stat-row"><span class="stat-label">Visible edges</span><span class="stat-value" id="stat-live-edges">–</span></div>
`);

function rebuildGraph() {{
  buildGraph();
  selectedNode = null;
  document.getElementById('node-detail').innerHTML =
    '<p class="detail-empty">Click a node to inspect it.</p>';
}}

// Language pills
document.getElementById('lang-filter').addEventListener('click', e => {{
  const pill = e.target.closest('.pill');
  if (!pill) return;
  document.querySelectorAll('#lang-filter .pill').forEach(p => p.classList.remove('active'));
  pill.classList.add('active');
  activeLang = pill.dataset.lang;
  rebuildGraph();
}});

// Phrases toggle
document.getElementById('pill-phrases').addEventListener('click', function() {{
  showPhrasesOnly = !showPhrasesOnly;
  this.classList.toggle('active', showPhrasesOnly);
  rebuildGraph();
}});

// Verified toggle
document.getElementById('pill-verified').addEventListener('click', function() {{
  showVerifiedOnly = !showVerifiedOnly;
  this.classList.toggle('active', showVerifiedOnly);
  rebuildGraph();
}});

// Search
let searchTimeout;
document.getElementById('search-input').addEventListener('input', function() {{
  clearTimeout(searchTimeout);
  searchTimeout = setTimeout(() => {{
    searchQuery = this.value.trim();
    rebuildGraph();
  }}, 250);
}});

// Strength slider
document.getElementById('strength-slider').addEventListener('input', function() {{
  strength = +this.value;
  document.getElementById('strength-val').textContent = this.value;
  if (simulation) {{
    simulation.force('charge', d3.forceManyBody().strength(strength));
    simulation.alpha(0.3).restart();
  }}
}});

// Reheat
document.getElementById('btn-reheat').addEventListener('click', () => {{
  if (simulation) simulation.alpha(0.8).restart();
}});

// Reset zoom
document.getElementById('btn-reset-zoom').addEventListener('click', () => {{
  svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity.translate(width/2, height/2).scale(0.8));
}});

// Unpin all
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
      type: 'dot', color, label: lang.toUpperCase() + ' term'
    }})),
    {{ type: 'dot', color: '#64748b', label: 'Concept' }},
    {{ type: 'dot', color: '#fbbf24', label: '★ Verified', ring: true }},
    {{ type: 'line', color: '#94a3b8', label: 'translates_to', dashed: false }},
    {{ type: 'line', color: '#c4b5fd', label: 'instantiates', dashed: true }},
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
// MINIMAP
// ============================================================
const mmSvg = d3.select('#minimap-svg').attr('width', 140).attr('height', 90);
const mmG = mmSvg.append('g');

function updateMinimap() {{
  if (!simulation) return;
  const fn = simulation.nodes();
  if (!fn.length) return;

  const xs = fn.map(n => n.x).filter(isFinite);
  const ys = fn.map(n => n.y).filter(isFinite);
  if (!xs.length) return;

  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const rangeX = maxX - minX || 1, rangeY = maxY - minY || 1;
  const scaleX = 130 / rangeX, scaleY = 80 / rangeY;
  const sc = Math.min(scaleX, scaleY) * 0.9;

  mmG.selectAll('circle').data(fn).join('circle')
    .attr('cx', d => isFinite(d.x) ? (d.x - minX) * sc + 5 : 0)
    .attr('cy', d => isFinite(d.y) ? (d.y - minY) * sc + 5 : 0)
    .attr('r', 2)
    .attr('fill', d => d.color)
    .attr('opacity', 0.7);
}}

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
// Start centered
svg.call(zoom.transform, d3.zoomIdentity.translate(width/2, height/2).scale(0.7));
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------

out_path = pathlib.Path(args.out)
out_path.write_text(html, encoding="utf-8")
print(f"[KG Inspector] Written: {out_path.resolve()}")
print(f"[KG Inspector] Open in your browser: file://{out_path.resolve()}")
