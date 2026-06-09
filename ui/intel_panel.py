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


def _clean_translations(translations: list[dict]) -> list[dict]:
    """Tidy and rank the translation dicts returned by extract_entities.

    Drops empty target terms and single-word noise (e.g. 'the', 'common');
    applies the gender-inclusive preference; sorts verified-first then by
    confidence descending. NO confidence floor: humanities corpora often
    sit at 0.18-0.29 from Dice seeding, and a hard cutoff silently
    discards real curator-authored material.
    """
    out: list[dict] = []
    for tr in translations:
        if not isinstance(tr, dict):
            continue
        term = (tr.get("term") or "").strip()
        if not term:
            continue
        if term.lower() in _NOISE_UNIGRAMS and len(term.split()) == 1:
            continue
        strats = tr.get("gender_strategies") or {}
        if isinstance(strats, dict) and strats.get("underscore_inclusivity"):
            term = strats["underscore_inclusivity"]
        out.append({
            "term": term,
            "confidence": float(tr.get("confidence") or 0.0),
            "verified": bool(tr.get("verified")),
            "lineage": tr.get("lineage") or "",
            "register": tr.get("register") or "",
        })
    out.sort(key=lambda t: (not t["verified"], -t["confidence"]))
    return out[:6]


def _kg_target_vocab(
    kg, tgt_lang: str, *, min_freq: int = 1, limit: int | None = None,
) -> list[str]:
    """Surface forms of every target-language term in the KG, ranked by
    frequency desc.

    Why this exists: `_kg_query` only surfaces target terms whose source-side
    equivalent is present in the current source segment. But a translator
    routinely uses target vocabulary whose source partner sits in a
    different segment, or is implicit, or is a related concept they want
    to introduce. The ghost-text engine matches by prefix against the
    bundle's candidate list, so we ship every KG-known target surface form
    as background candidates. With this, typing the first letters of
    *any* established target term completes via ghost text — not only the
    source-aligned ones.

    Includes lemma, display_form, and variants per term so prefix matches
    work regardless of which surface the translator is moving toward.
    Drops single-word noise (e.g. function words) by the same noise list
    used elsewhere.

    A `limit` of None means "all"; with a small bilingual corpus (low
    thousands of SL terms) the JSON bundle stays well under 200KB.
    """
    if kg is None or not hasattr(kg, "G"):
        return []
    scored: list[tuple[str, int]] = []
    for _node_id, nd in kg.G.nodes(data=True):
        if nd.get("type") != "term":
            continue
        if nd.get("lang") != tgt_lang:
            continue
        freq = nd.get("frequency") or 0
        if freq < min_freq:
            continue
        surfaces: list[str] = []
        for key in ("display_form", "term"):
            v = nd.get(key)
            if isinstance(v, str) and v.strip():
                surfaces.append(v.strip())
        for v in nd.get("variants") or []:
            if isinstance(v, str) and v.strip():
                surfaces.append(v.strip())
        for s in surfaces:
            if s.lower() in _NOISE_UNIGRAMS and len(s.split()) == 1:
                continue
            scored.append((s, freq))
    scored.sort(key=lambda t: -t[1])
    seen: set[str] = set()
    out: list[str] = []
    for surface, _ in scored:
        key = surface.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(surface)
        if limit is not None and len(out) >= limit:
            break
    return out


_GENERIC_LINEAGES = frozenset({
    "performance", "general", "manual", "curatorial-exhibition", "visual-art", "",
})


def _concept_context(G, concept_id: str) -> tuple[list[str], list[str]]:
    """Theory lineages + theorist names attached to a concept, via the bridge:
    concept <-instantiates_concept- term -has_mapping-> mapping
    -> mapping.lineage (theory) + mapping -attributed_to-> agent."""
    lineages: list[str] = []
    theorists: list[str] = []
    seen_l, seen_t = set(), set()
    examined = 0
    for term, _c, ed in G.in_edges(concept_id, data=True):
        if ed.get("relation") != "instantiates_concept":
            continue
        examined += 1
        if examined > 60:  # cap traversal so the UI refresh stays snappy
            break
        for _t, mp, ed2 in G.out_edges(term, data=True):
            if ed2.get("relation") != "has_mapping":
                continue
            lin = (G.nodes[mp].get("lineage") or "").strip()
            if lin and lin.lower() not in _GENERIC_LINEAGES and lin not in seen_l:
                seen_l.add(lin); lineages.append(lin)
            for _m, ag, ed3 in G.out_edges(mp, data=True):
                if ed3.get("relation") == "attributed_to" and G.has_node(ag):
                    nm = G.nodes[ag].get("name")
                    if nm and nm not in seen_t:
                        seen_t.add(nm); theorists.append(nm)
        if len(theorists) >= 3 and len(lineages) >= 3:
            break
    return lineages[:3], theorists[:3]


def _concept_for(G, term_node_id: str) -> dict | None:
    """Return concept attrs (id, label, domain) + theory lineages/theorists."""
    if not G.has_node(term_node_id):
        return None
    for _u, v, d in G.out_edges(term_node_id, data=True):
        if d.get("relation") != "instantiates_concept":
            continue
        nd = G.nodes[v]
        if nd.get("type") != "concept":
            continue
        lineages, theorists = _concept_context(G, v)
        return {
            "id": v,
            "label": nd.get("label") or v,
            "domain": nd.get("domain") or "",
            "lineages": lineages,
            "theorists": theorists,
        }
    return None


def _kg_query(
    source: str, src_lang: str, tgt_lang: str, kg, *, max_hits: int = 8,
) -> list[dict]:
    """Strategic bilingual KG lookup for translation flow.

    Driven by the KG's own entity index — extract_entities composes
    flashtext maximal-munch over multi-word phrases, lemmas, display
    forms, variants, and gender forms, and resolves translations via the
    has_mapping -> map -> maps_to edge walk (with translates_to fallback).
    We do not re-tokenise the source here; the KG knows its own surface
    forms better than any regex would.

    Each hit carries:
      - src_term/tgt_term/src_lang/tgt_lang (bilingual core)
      - confidence/verified (curator signal)
      - alt_translations (other renderings of the same source term)
      - related (concept-sibling terms, target-lang first)
      - concept ({id, label, domain} | None) so the panel can group hits
      - n (word count of src_term) for the phrase/unigram bucket split
      - freq (corpus frequency, ranking tiebreaker)

    Phrases (n >= 2) and unigrams (n == 1) each get half of max_hits so
    the unigram bucket — the everyday workhorse of humanities translation —
    is never starved by a flood of bigram matches, and vice versa.
    """
    if kg is None or not source.strip():
        return []
    if not hasattr(kg, "extract_entities"):
        return []
    try:
        entities = kg.extract_entities(source, target_lang=tgt_lang) or []
    except Exception as e:
        print(f"[intel _kg_query] extract_entities failed: {e}")
        return []
    G = kg.G
    hits: list[dict] = []
    for ent in entities:
        term_text = (ent.get("term") or "").strip()
        if not term_text:
            continue
        # Generic single-word noise stays filtered regardless of whether
        # the curator's data accidentally carries a translation for it.
        if term_text.lower() in _NOISE_UNIGRAMS and len(term_text.split()) == 1:
            continue
        translations = _clean_translations(ent.get("translations") or [])
        if not translations:
            continue
        src_term = ent.get("display_form") or term_text
        node_lang = ent.get("lang") or src_lang
        node_id = f"term:{node_lang}:{term_text}"
        concept = _concept_for(G, node_id)
        related = _related_via_concept(
            G, node_id, preferred_lang=tgt_lang, max_siblings=6,
        )
        best = translations[0]
        hits.append({
            "id": node_id,
            "src_term": src_term,
            "src_lang": node_lang,
            "tgt_term": best["term"],
            "tgt_lang": tgt_lang,
            "confidence": best["confidence"],
            "verified": best["verified"],
            "alt_translations": translations[1:4],
            "n": len(src_term.split()),
            "freq": ent.get("frequency", 0),
            "related": related,
            "concept": concept,
        })

    # Quota split: phrases (n>=2) and unigrams (n==1) each take roughly
    # half of max_hits; whichever bucket is thin yields its slack to the
    # other so we always return up to max_hits total when material exists.
    def _rank(h: dict) -> tuple:
        return (not h["verified"], -(h["confidence"] or 0), -(h["freq"] or 0))

    phrases = sorted([h for h in hits if h["n"] >= 2], key=_rank)
    unigrams = sorted([h for h in hits if h["n"] == 1], key=_rank)
    half = max_hits // 2
    chosen = phrases[:half]
    chosen += unigrams[: max_hits - len(chosen)]
    if len(chosen) < max_hits:
        chosen += phrases[half : half + (max_hits - len(chosen))]
    if len(chosen) < max_hits:
        # Fall back: any remaining unigrams beyond the first slice.
        already = {id(h) for h in chosen}
        chosen += [h for h in unigrams if id(h) not in already][
            : max_hits - len(chosen)
        ]
    return chosen[:max_hits]


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
    # Knowledge Graph: extract_entities-driven query (see _kg_query above).
    # Hits group by concept they instantiate; orphan unigrams land in a
    # "TERMS" card at the bottom (equal visual weight — unigrams carry
    # most of the everyday translation work in humanities corpora).
    # ------------------------------------------------------------------
    def _render_hit(h: dict):
        """One bilingual hit + its alt/sibling cluster row."""
        t_term = h["tgt_term"]
        hit_row = (
            ui.row()
            .classes(
                "w-full items-center gap-2 cursor-pointer "
                "hover:bg-primary/5 rounded"
            )
            .on("click", lambda _e, t=t_term: _insert(t))
        )
        with hit_row:
            ui.label(h["src_term"]).classes("text-sm opacity-70")
            ui.label("→").classes("text-xs opacity-30")
            tgt_style = "color: var(--q-positive)" if h.get("verified") else ""
            ui.label(t_term).classes("font-bold text-sm").style(tgt_style)
            if h["verified"]:
                ui.icon("verified", size="13px").props("color=positive")
            else:
                ui.badge(
                    f"{int((h.get('confidence') or 0) * 100)}%",
                    color="primary",
                ).classes("text-[9px] px-1")

        # Concept cluster row — alt translations + concept siblings.
        # Colours encode direction:
        #   primary  = alternative renderings of the same source term
        #   positive = target-lang concept siblings (clickable to insert)
        #   positive = source-lang concept siblings (context only, dim)
        tgt_alts = [tr["term"] for tr in h.get("alt_translations", []) or []]
        tgt_sibs = [
            r for r in h.get("related", []) or []
            if r.get("lang") == h["tgt_lang"]
        ]
        src_sibs = [
            r for r in h.get("related", []) or []
            if r.get("lang") != h["tgt_lang"]
        ]
        if not (tgt_alts or tgt_sibs or src_sibs):
            return
        with ui.row().classes(
            "w-full items-center gap-1.5 flex-wrap"
        ).style("padding-left: 1.5rem"):
            ui.icon("circle", size="7px").props("color=grey-5")
            ui.label("─").classes("text-[10px]")
            for alt_t in tgt_alts[:2]:
                ui.button(
                    alt_t,
                    on_click=lambda _e, t=alt_t: _insert(t),
                ).props("flat dense rounded color=positive").classes(
                    "text-[10px] normal-case h-5 px-1.5"
                )
            for sib in tgt_sibs[:3]:
                ui.button(
                    sib["term"],
                    on_click=lambda _e, t=sib["term"]: _insert(t),
                ).props("flat dense rounded color=positive").classes(
                    "text-[10px] normal-case h-5 px-1.5"
                ).tooltip(
                    f"{sib.get('lang', '')} — {sib.get('freq', 0)}× in corpus"
                )
            for sib in src_sibs[:2]:
                ui.button(
                    sib["term"],
                    on_click=lambda _e, t=sib["term"]: _insert(t),
                ).props("flat dense rounded color=positive").classes(
                    "text-[10px] normal-case h-5 px-1.5"
                ).tooltip(
                    f"{sib.get('lang', '')} — {sib.get('freq', 0)}× in corpus"
                )

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
                    "text-xs italic opacity-90"
                )
                return
            # Group by concept (insertion-order). Orphans go to a "TERMS"
            # card rendered after all concept groups, with the same visual
            # weight — unigram terms are the workhorse of humanities
            # translation and shouldn't be hidden in a dim footer.
            by_concept: dict[str, list[dict]] = {}
            order: list[str] = []
            concept_by_id: dict[str, dict] = {}
            orphans: list[dict] = []
            for h in hits:
                c = h.get("concept")
                if c and c.get("id"):
                    cid = c["id"]
                    if cid not in by_concept:
                        by_concept[cid] = []
                        order.append(cid)
                        concept_by_id[cid] = c
                    by_concept[cid].append(h)
                else:
                    orphans.append(h)
            groups: list[tuple[dict | None, list[dict]]] = [
                (concept_by_id[cid], by_concept[cid]) for cid in order
            ]
            if orphans:
                groups.append((None, orphans))

            for concept, group_hits in groups:
                with ui.card().props("flat bordered").classes(
                    "w-full p-3 rounded-2xl"
                ):
                    with ui.row().classes(
                        "w-full items-center gap-2 mb-1"
                    ):
                        ui.icon("hub", size="13px").props("color=primary")
                        if concept is None:
                            ui.label("TERMS").classes(
                                "text-[10px] font-black tracking-[.3em] opacity-90"
                            )
                        else:
                            ui.label(
                                str(concept.get("label") or "").upper()
                            ).classes(
                                "text-[10px] font-black tracking-[.3em] opacity-90"
                            )
                            if concept.get("domain"):
                                ui.badge(
                                    concept["domain"], color="grey-5",
                                ).classes("text-[9px] px-1 ml-1")
                    if concept and (concept.get("theorists") or concept.get("lineages")):
                        with ui.row().classes(
                            "w-full items-center gap-1 flex-wrap mb-1"
                        ).style("padding-left: 1.25rem"):
                            for th in concept.get("theorists", []):
                                ui.badge(th, color="purple-4").props(
                                    "outline"
                                ).classes("text-[9px] px-1").tooltip(
                                    "thinker who uses this concept"
                                )
                            for ln in concept.get("lineages", []):
                                if ln not in concept.get("theorists", []):
                                    ui.badge(ln, color="grey-5").props(
                                        "outline"
                                    ).classes("text-[9px] px-1").tooltip("lineage")
                    with ui.column().classes("w-full gap-2"):
                        for h in group_hits:
                            _render_hit(h)

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
                    "text-xs italic opacity-90"
                )
                return
            ui.label("NEAR-EXACT MATCHES").classes(
                "text-[9px] font-black tracking-[.2em] opacity-90"
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
                        ui.label(m.get("source", "")).classes(
                            "text-[11px] italic leading-snug opacity-60"
                        ).style("white-space:normal;word-break:break-word")
                        ui.label(m.get("target", "")).classes(
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
