"""Translation-intelligence panel — KG, TM, and Glossary stacked vertically.

Lives directly below the editor card in the main column so every actionable
hit is one short scroll (or none) away from the cursor. No tabs: during
active translation the user wants the most relevant references — KG term
context, TM matches, glossary terms — all visible at a glance.

Layout order (closest to the editor first):
  1. KG — entities found in the source, each with translations + related
     concepts (`kg.find_neighbors`). This is the most informative section
     because it surfaces the *graph* relationships you can't see anywhere
     else.
  2. TM — fuzzy matches + concordance, click any card to insert the target.
  3. Glossary — colored buttons; placed last because glossary terms also
     surface automatically inside the editor's ghost-text predictions.

All three refresh in parallel on every segment change. No cache between
segments — translation flow needs fresh hits each time.
"""
from __future__ import annotations

import asyncio
import json
import re

from nicegui import background_tasks, ui

from .state import WorkspaceState


# Generic stop-words/noise that pollute single-word KG hits in academic text.
# Multi-word phrases never match these, so this only filters 1-grams.
_NOISE_UNIGRAMS = frozenset({
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "of", "in", "on", "at", "to", "for", "with", "from", "by", "as",
    "this", "that", "these", "those", "it", "its", "they", "their",
    "would", "could", "should", "may", "might", "can", "will",
    "not", "no", "yes", "so", "if", "then", "than", "when", "where",
    "common", "thing", "things", "way", "ways", "people", "time",
    "concept", "text", "and", "such",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stripping punctuation."""
    return [w.lower() for w in re.findall(r"[\w'\-]+", text)]


def _ngrams(words: list[str], n: int) -> list[tuple[int, str]]:
    """Return (start_index, joined_text) for every n-gram in the word list."""
    return [(i, " ".join(words[i:i + n])) for i in range(len(words) - n + 1)]


def _resolve_translations(
    G, src_node_id: str, tgt_lang: str,
) -> list[dict]:
    """Resolve translations for a source term node from the KG graph.

    The real KG stores translations as graph structure, not as a flat list
    on the node:
      - ``translates_to`` edges link src -> SL term nodes (no confidence)
      - ``has_mapping`` edges link src -> ``map:*`` mapping nodes that carry
        confidence, lineage, verified, register.
      - Each mapping node has a ``maps_to`` edge pointing to the SL term.

    We merge both sources: the mapping nodes carry confidence so they
    drive ranking.  Direct ``translates_to`` edges without a mapping are
    included as low-confidence fallbacks.
    """
    seen_sl_ids: set[str] = set()
    translations: list[dict] = []

    # 1) Collect from mapping nodes (carry confidence)
    for _u, map_id, _d in G.out_edges(src_node_id, data=True):
        if G.nodes[map_id].get("type") != "translation_mapping":
            continue
        map_nd = G.nodes[map_id]
        # Follow maps_to edge to find the target term node
        tgt_id = None
        for _mu, mv, _md in G.out_edges(map_id, data=True):
            if G.edges[map_id, mv].get("relation") == "maps_to" or (
                isinstance(_md, dict) and _md.get("relation") == "maps_to"
            ):
                tgt_id = mv
                break
        if tgt_id is None:
            # maps_to might not have explicit relation — try first out-edge
            for _mu, mv, _md in G.out_edges(map_id, data=True):
                if G.nodes[mv].get("type") == "term" and G.nodes[mv].get("lang") == tgt_lang:
                    tgt_id = mv
                    break
        if tgt_id is None or tgt_id in seen_sl_ids:
            continue
        if G.nodes[tgt_id].get("lang") != tgt_lang:
            continue
        seen_sl_ids.add(tgt_id)
        tgt_term = G.nodes[tgt_id].get("term", "")
        if not tgt_term or (tgt_term.lower() in _NOISE_UNIGRAMS and len(tgt_term.split()) == 1):
            continue
        translations.append({
            "term": tgt_term,
            "confidence": map_nd.get("confidence", 0),
            "verified": bool(map_nd.get("verified")),
            "lineage": map_nd.get("lineage", ""),
            "register": map_nd.get("register", ""),
        })

    # 2) Collect from direct translates_to edges (no confidence)
    for _u, sl_id, _d in G.out_edges(src_node_id, data=True):
        if sl_id in seen_sl_ids:
            continue
        sl_nd = G.nodes[sl_id]
        if sl_nd.get("type") != "term" or sl_nd.get("lang") != tgt_lang:
            continue
        seen_sl_ids.add(sl_id)
        tgt_term = sl_nd.get("term", "")
        if not tgt_term or (tgt_term.lower() in _NOISE_UNIGRAMS and len(tgt_term.split()) == 1):
            continue
        translations.append({
            "term": tgt_term,
            "confidence": 0.0,
            "verified": False,
            "lineage": "direct",
            "register": "",
        })

    # Sort: verified first, then confidence desc
    translations.sort(key=lambda t: (not t["verified"], -t["confidence"]))
    return translations[:6]


def _kg_query(source: str, src_lang: str, tgt_lang: str, kg) -> list[dict]:
    """Strategic bilingual KG lookup for translation flow.

    Generates 3-gram, 2-gram, and 1-gram candidates from the source and
    matches them against the KG term index. Prefers longer phrases
    (humanities terminology lives in 2- and 3-grams) over single words
    (mostly noise). Each surviving hit returns the source term + its best
    target-language translation + sibling concepts (target language first
    since they're directly actionable for writing the target text).

    Translations are resolved from the KG's graph structure:
      - ``has_mapping`` edges -> mapping nodes (carry confidence/lineage)
      - ``translates_to`` edges -> target term nodes
    """
    if kg is None or not source.strip():
        return []
    G = kg.G

    words = _tokenize(source)
    if not words:
        return []

    covered: set[int] = set()
    hits: list[dict] = []

    for n in (3, 2, 1):
        for start, phrase in _ngrams(words, n):
            if n == 1 and phrase in _NOISE_UNIGRAMS:
                continue
            if n == 1 and len(phrase) < 4:
                continue
            indices = set(range(start, start + n))
            if n < 3 and indices <= covered:
                continue
            # Look the source-side ngram up under the project's source language
            # first; fall back to en/sl because the corpus has some terms
            # mis-labelled.
            src_node_id = None
            for lang_try in (src_lang, "en", "sl"):
                cand = f"term:{lang_try}:{phrase}"
                if G.has_node(cand):
                    src_node_id = cand
                    break
            if src_node_id is None:
                continue
            src_node = G.nodes[src_node_id]

            # Resolve translations from the KG graph structure
            # (has_mapping + translates_to edges), not from node attrs.
            translations = _resolve_translations(G, src_node_id, tgt_lang)
            if not translations:
                continue

            # Best translation = the first one after the verified/confidence sort.
            best_tr = translations[0]

            # Sibling terms via the concept node, sorted so target-language
            # siblings appear first (most actionable).
            related = _related_via_concept(
                G, src_node_id, preferred_lang=tgt_lang, max_siblings=6,
            )
            hits.append({
                "id": src_node_id,
                "src_term": src_node.get("term") or phrase,
                "src_lang": src_node.get("lang") or src_lang,
                "tgt_term": best_tr["term"],
                "tgt_lang": tgt_lang,
                "confidence": best_tr["confidence"],
                "verified": best_tr["verified"],
                "alt_translations": translations[1:3],
                "n": n,
                "freq": src_node.get("frequency", 0),
                "related": related,
            })
            covered |= indices

    hits.sort(key=lambda h: (-h["n"], -(h.get("freq") or 0)))
    return hits[:5]


def _filter_translations(translations: list[dict]) -> list[dict]:
    """Keep only verified or high-confidence translations. Drops noise."""
    out = []
    for tr in translations:
        if not isinstance(tr, dict):
            continue
        term = (tr.get("term") or "").strip()
        if not term:
            continue
        if term.lower() in _NOISE_UNIGRAMS and len(term.split()) == 1:
            continue
        confidence = tr.get("confidence") or 0
        verified = bool(tr.get("verified"))
        if not (verified or confidence >= 0.6):
            continue
        # Prefer the gender-inclusive variant when the KG knows one.
        strats = tr.get("gender_strategies") or {}
        if isinstance(strats, dict) and strats.get("underscore_inclusivity"):
            term = strats["underscore_inclusivity"]
        out.append({
            "term": term,
            "confidence": confidence,
            "verified": verified,
            "lineage": tr.get("lineage") or "",
        })
    out.sort(key=lambda t: (not t["verified"], -t["confidence"]))
    return out[:4]


def _related_via_concept(
    G, node_id: str, *, preferred_lang: str | None = None, max_siblings: int = 6,
) -> list[dict]:
    """Find sibling terms — other terms that instantiate the same concept
    node. Sorted so preferred-language siblings appear first (so an EN→SL
    translator sees Slovenian options before English ones)."""
    concept_ids = []
    for _u, v, d in G.out_edges(node_id, data=True):
        if d.get("relation") == "instantiates_concept" and G.nodes[v].get("type") == "concept":
            concept_ids.append(v)
    if not concept_ids:
        return []
    siblings: list[dict] = []
    seen: set[str] = {node_id}
    for cid in concept_ids:
        for u, _v, d in G.in_edges(cid, data=True):
            if d.get("relation") != "instantiates_concept":
                continue
            if u in seen:
                continue
            seen.add(u)
            nd = G.nodes[u]
            if nd.get("type") != "term":
                continue
            term = nd.get("term") or u
            if term.lower() in _NOISE_UNIGRAMS and len(term.split()) == 1:
                continue
            siblings.append({
                "id": u,
                "term": term,
                "lang": nd.get("lang") or "",
                "freq": nd.get("frequency", 0),
            })

    if preferred_lang:
        siblings.sort(key=lambda s: (s["lang"] != preferred_lang, -s["freq"]))
    else:
        siblings.sort(key=lambda s: -s["freq"])
    return siblings[:max_siblings]


def _truncate(text: str, n: int = 90) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def build(state: WorkspaceState, deps: dict) -> dict:
    tm = deps["tm"]
    glossary = deps["glossary"]
    kg = deps["kg"]
    parse_lang_pair = deps["parse_lang_pair"]

    refs: dict = {}

    with ui.column().classes("w-full gap-3 mt-4"):
        # ---------------------------------------------------------- KG section
        with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
            with ui.row().classes("w-full items-center gap-2 mb-2"):
                ui.icon("account_tree", size="16px").props("color=primary")
                ui.label("KNOWLEDGE GRAPH").classes(
                    "text-[10px] font-black tracking-[.3em] opacity-70"
                )
            kg_container = ui.column().classes("w-full gap-2")
            refs["kg_container"] = kg_container

        # ---------------------------------------------------------- TM section
        with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
            with ui.row().classes("w-full items-center gap-2 mb-2"):
                ui.icon("memory", size="16px").props("color=primary")
                ui.label("TRANSLATION MEMORY").classes(
                    "text-[10px] font-black tracking-[.3em] opacity-70"
                )
            tm_container = ui.column().classes("w-full gap-2")
            refs["tm_container"] = tm_container

        # ----------------------------------------------------- Glossary section
        with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
            with ui.row().classes("w-full items-center gap-2 mb-2"):
                ui.icon("menu_book", size="16px").props("color=primary")
                ui.label("GLOSSARY").classes(
                    "text-[10px] font-black tracking-[.3em] opacity-70"
                )
            gl_container = ui.column().classes("w-full gap-2")
            refs["gl_container"] = gl_container

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _current_seg():
        if not state.segments:
            return None
        return state.segments[state.active_index]

    def _insert(text: str):
        """Insert text at the textarea cursor via the predictor's JS helper.
        Routes through the page's client so the JS reaches the right browser
        session from any background-task context."""
        if not text:
            return
        try:
            (state.client or ui).run_javascript(
                f"window.__sl_predictor && window.__sl_predictor.insertAtCursor({json.dumps(text)});"
            )
        except Exception as ex:
            print(f"[intel insert] {ex}")

    # ------------------------------------------------------------------
    # Knowledge Graph: strategic ngram query (see _kg_query above).
    # Returns at most 6 entities, preferring 2- and 3-grams over unigrams.
    # ------------------------------------------------------------------
    async def _refresh_kg():
        seg = _current_seg()
        if seg is None or kg is None:
            return
        idx = state.active_index
        loop = asyncio.get_running_loop()
        src_lang, tgt_lang = parse_lang_pair(state.lang_pair)
        try:
            hits = await loop.run_in_executor(
                None, _kg_query, seg["source"], src_lang, tgt_lang, kg,
            )
        except Exception as e:
            print(f"[intel KG] {e}")
            return
        if state.active_index != idx or kg_container.is_deleted:
            return
        kg_container.clear()
        with kg_container:
            if not hits:
                ui.label("No KG matches for this segment.").classes(
                    "text-xs italic opacity-60"
                )
                return
            for h in hits:
                with ui.card().props("flat bordered").classes("w-full p-2 rounded-xl"):
                    # Row 1 — bilingual hit: source LEFT → target RIGHT
                    # Gaze flow: eye lands on source term (identification)
                    # then moves right to target term (action).
                    t_term = h["tgt_term"]
                    hit_row = (
                        ui.row()
                        .classes("w-full items-center gap-2 cursor-pointer hover:bg-primary/5 rounded")
                        .on("click", lambda _e, t=t_term: _insert(t))
                    )
                    with hit_row:
                        # Source term — LEFT: identification
                        ui.label(h["src_term"]).classes("text-sm opacity-70")
                        # Directional bridge
                        ui.label("→").classes("text-xs opacity-30")
                        # Target term — RIGHT: action (visually dominant)
                        tgt_style = "color: var(--q-positive)" if h.get("verified") else ""
                        ui.label(t_term).classes(
                            "font-bold text-sm"
                        ).style(tgt_style)
                        # Trust signal at far right
                        if h["verified"]:
                            ui.icon("verified", size="13px").props("color=positive")
                        else:
                            ui.badge(
                                f"{int(h['confidence'] * 100)}%",
                                color="primary",
                            ).classes("text-[9px] px-1")

                    # Row 2 — concept cluster: related terms shown as structure
                    # Visual node (◉) = concept the hit instantiates.
                    # Indentation + connector show these terms hang off
                    # the same concept. Color encodes language direction:
                    #   positive (green) = target-lang (clickable to insert)
                    #   secondary (grey)  = source-lang (context only)
                    tgt_alts = [
                        tr["term"]
                        for tr in h.get("alt_translations", []) or []
                    ]
                    tgt_sibs = [
                        r for r in h.get("related", []) or []
                        if r.get("lang") == h["tgt_lang"]
                    ]
                    src_sibs = [
                        r for r in h.get("related", []) or []
                        if r.get("lang") != h["tgt_lang"]
                    ]
                    has_cluster = bool(tgt_alts or tgt_sibs or src_sibs)
                    if has_cluster:
                        with ui.row().classes(
                            "w-full items-center gap-1.5 flex-wrap"
                        ).style("padding-left: 1.5rem"):
                            # Concept node — visible structural element
                            ui.icon("circle", size="7px").props("color=grey-5")
                            ui.label("─").classes("text-[10px] opacity-20")
                            # Target-lang alternatives first (most actionable)
                            for alt_t in tgt_alts[:2]:
                                ui.button(
                                    alt_t,
                                    on_click=lambda _e, t=alt_t: _insert(t),
                                ).props(
                                    "flat dense rounded color=primary"
                                ).classes(
                                    "text-[10px] normal-case h-5 px-1.5"
                                )
                            for sib in tgt_sibs[:3]:
                                ui.button(
                                    sib["term"],
                                    on_click=lambda _e, t=sib["term"]: _insert(t),
                                ).props(
                                    "flat dense rounded color=positive"
                                ).classes(
                                    "text-[10px] normal-case h-5 px-1.5"
                                ).tooltip(
                                    f"{sib.get('lang', '')} — {sib.get('freq', 0)}× in corpus"
                                )
                            # Source-lang siblings last (context only, dimmer)
                            for sib in src_sibs[:2]:
                                ui.button(
                                    sib["term"],
                                    on_click=lambda _e, t=sib["term"]: _insert(t),
                                ).props(
                                    "flat dense rounded color=secondary"
                                ).classes(
                                    "text-[10px] normal-case h-5 px-1.5 opacity-60"
                                ).tooltip(
                                    f"{sib.get('lang', '')} — {sib.get('freq', 0)}× in corpus"
                                )

    # ------------------------------------------------------------------
    # Translation Memory: fuzzy + concordance
    # ------------------------------------------------------------------
    async def _refresh_tm():
        seg = _current_seg()
        if seg is None or tm is None:
            return
        idx = state.active_index
        loop = asyncio.get_running_loop()
        try:
            fuzzy = await loop.run_in_executor(
                None,
                lambda: (
                    # 95% threshold so only near-exact matches surface as
                    # actionable suggestions; lower-scoring hits clutter the
                    # translation flow without adding value.
                    tm.lookup_fuzzy(seg["source"], threshold=95.0, limit=5) or []
                ),
            )
        except Exception as e:
            print(f"[intel TM] {e}")
            return
        if state.active_index != idx or tm_container.is_deleted:
            return
        tm_container.clear()
        with tm_container:
            if not fuzzy:
                ui.label("No near-exact matches (≥95%).").classes(
                    "text-xs italic opacity-60"
                )
                return
            ui.label("NEAR-EXACT MATCHES").classes(
                "text-[9px] font-black tracking-[.2em] opacity-60"
            )
            for m in fuzzy:
                score = int(m.get("score") or 0)
                badge_color = "positive" if score >= 100 else "primary"
                with (
                    ui.card()
                    .props("flat bordered")
                    .classes(
                        "w-full rounded-xl p-3 cursor-pointer "
                        "flex-row items-center gap-3 hover:bg-primary/5"
                    )
                    .on("click", lambda _e, t=m.get("target", ""): _insert(t))
                ):
                    ui.badge(f"{score}%", color=badge_color).classes(
                        "text-[10px] font-black px-2 py-1 rounded-lg shrink-0"
                    )
                    with ui.column().classes("gap-0.5 flex-1 min-w-0"):
                        ui.label(_truncate(m.get("source", ""), 80)).classes(
                            "text-[11px] italic leading-snug opacity-60"
                        ).style("white-space:normal;word-break:break-word")
                        ui.label(_truncate(m.get("target", ""), 80)).classes(
                            "text-[13px] font-bold leading-snug"
                        ).style("white-space:normal;word-break:break-word")
                    ui.icon("content_paste", size="18px").props("color=grey-5")

    # ------------------------------------------------------------------
    # Glossary: tagged terms found in the source
    # ------------------------------------------------------------------
    async def _refresh_gl():
        seg = _current_seg()
        if seg is None or glossary is None:
            return
        idx = state.active_index
        loop = asyncio.get_running_loop()
        src, tgt = parse_lang_pair(state.lang_pair)
        try:
            hits = await loop.run_in_executor(
                None,
                lambda: glossary.lookup_terms(seg["source"], src, tgt) or [],
            )
        except Exception as e:
            print(f"[intel glossary] {e}")
            return
        if state.active_index != idx or gl_container.is_deleted:
            return
        gl_container.clear()
        with gl_container:
            if not hits:
                ui.label("No glossary terms in this segment.").classes(
                    "text-xs italic opacity-60"
                )
                return
            with ui.row().classes("w-full gap-2 items-center flex-wrap"):
                for g in hits:
                    s_term = g.get("source_term", "")
                    t_term = g.get("target_term", "")
                    chip_label = f"{_truncate(s_term, 30)} → {_truncate(t_term, 30)}" if t_term else _truncate(s_term, 30)
                    ui.button(
                        chip_label,
                        on_click=lambda _e, t=t_term: _insert(t),
                    ).props("unelevated rounded dense color=positive").classes(
                        "text-[11px] font-bold px-3 h-8 normal-case"
                    ).tooltip(
                        g.get("note") or "Click to insert"
                    )

    # ------------------------------------------------------------------
    # Refresh all sections on segment change
    # ------------------------------------------------------------------
    def _refresh_all():
        background_tasks.create(_refresh_kg(), name="intel_kg_refresh")
        background_tasks.create(_refresh_tm(), name="intel_tm_refresh")
        background_tasks.create(_refresh_gl(), name="intel_gl_refresh")

    state.subscribe("active_index", _refresh_all)

    # Initial paint — populate all three sections immediately.
    _refresh_all()

    return refs
