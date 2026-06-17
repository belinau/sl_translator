"""Always-on KG + TM lookup under the segments pane.

A search input that is visible at all times (no click-to-open) with results that
flow inline below it. Type a query, press Enter, and see:
  - KG term matches with all translation candidates, lineage, confidence, register
  - Concepts the matched terms instantiate (label, domain, definition)
  - Direct concept label matches (when query hits a concept label directly)
  - TM concordance segments (full text)
Click a result to insert it at the cursor.
"""
from __future__ import annotations

import asyncio
import json

from nicegui import background_tasks, ui

from .state import WorkspaceState


def build(state: WorkspaceState, deps: dict) -> dict:
    kg = deps.get("kg")
    tm = deps.get("tm")
    parse_lang_pair = deps["parse_lang_pair"]

    def _insert(text: str) -> None:
        if not text:
            return
        try:
            (state.client or ui).run_javascript(
                f"window.__sl_predictor && window.__sl_predictor.insertAtCursor({json.dumps(text)});"
            )
        except Exception as ex:
            print(f"[search insert] {ex}")

    def _get_concepts_for_term(term_id: str) -> list[dict]:
        """Walk term -[instantiates_concept]-> concept and return concept node dicts."""
        if not kg:
            return []
        concepts = []
        for _, concept_id, edata in kg.G.out_edges(term_id, data=True):
            if edata.get("relation") != "instantiates_concept":
                continue
            c = kg.G.nodes.get(concept_id, {})
            if c.get("type") == "concept":
                concepts.append(dict(c))
        return concepts

    def _search_concepts_by_label(query: str) -> list[dict]:
        """Find concept nodes whose label contains the query string (case-insensitive)."""
        if not kg:
            return []
        q = query.lower()
        results = []
        for node_id, data in kg.G.nodes(data=True):
            if data.get("type") != "concept":
                continue
            label = data.get("label", "").lower()
            domain = data.get("domain", "").lower()
            if q in label or q in domain:
                # Collect all terms that instantiate this concept
                en_terms = []
                sl_terms = []
                for term_id in kg.G.predecessors(node_id):
                    edata = kg.G.edges.get((term_id, node_id), {})
                    if edata.get("relation") != "instantiates_concept":
                        continue
                    t = kg.G.nodes.get(term_id, {})
                    if t.get("type") != "term":
                        continue
                    surface = t.get("display_form") or t.get("term", "")
                    if t.get("lang") == "en":
                        en_terms.append(surface)
                    elif t.get("lang") == "sl":
                        sl_terms.append(surface)
                results.append({
                    **data,
                    "_en_terms": en_terms,
                    "_sl_terms": sl_terms,
                })
        return results

    refs: dict = {}
    run_token = {"v": 0}
    with ui.column().classes("w-full gap-1 mt-3"):
        ui.label("SEARCH KG & TM").classes(
            "text-[9px] font-black tracking-[.2em] opacity-60"
        )
        query_input = (
            ui.input(placeholder="Search terms / concepts / segments — press Enter")
            .props("dense outlined clearable")
            .classes("w-full")
        )
        results = ui.column().classes("w-full gap-1")
        refs["query_input"] = query_input
        refs["results"] = results

        async def _run() -> None:
            q = (query_input.value or "").strip()
            run_token["v"] += 1
            my_token = run_token["v"]
            results.clear()
            if not q:
                return

            with results:
                ui.spinner(size="sm").classes("self-center").style("margin: 0.5rem 0")

            _src_lang, tgt_lang = parse_lang_pair(state.lang_pair)
            loop = asyncio.get_running_loop()

            # ── KG: term keyword search ──────────────────────────────────────
            try:
                kg_hits = await loop.run_in_executor(
                    None,
                    lambda: kg.extract_entities(q, target_lang=tgt_lang) if kg else [],
                )
            except Exception as e:
                kg_hits = []
                print(f"[search kg terms] {e}")

            # ── KG: concept label search ─────────────────────────────────────
            try:
                concept_hits = await loop.run_in_executor(
                    None, lambda: _search_concepts_by_label(q)
                )
            except Exception as e:
                concept_hits = []
                print(f"[search kg concepts] {e}")

            # ── TM: concordance ──────────────────────────────────────────────
            try:
                tm_hits = await loop.run_in_executor(
                    None, lambda: tm.search_concordance(q, top_n=25) if tm else []
                )
            except Exception as e:
                tm_hits = []
                print(f"[search tm] {e}")

            # ── Render ───────────────────────────────────────────────────────
            if my_token != run_token["v"] or results.is_deleted:
                return
            results.clear()
            with results:
                # ── Term hits ────────────────────────────────────────────────
                if kg_hits:
                    ui.label("KG TERMS").classes(
                        "text-[9px] font-black tracking-[.2em] opacity-60 mt-1"
                    )
                    for h in kg_hits:
                        translations: list[dict] = h.get("translations") or []
                        term_label = h.get("display_form") or h.get("term", "")
                        term_concepts = _get_concepts_for_term(h.get("id", ""))

                        with ui.card().props("flat bordered").classes(
                            "w-full rounded-lg p-2 gap-1"
                        ):
                            # Source term header
                            ui.label(term_label).classes(
                                "text-xs font-bold opacity-80"
                            )

                            # Concepts this term belongs to
                            if term_concepts:
                                concept_badges = "  ".join(
                                    f"[{c.get('label', '')} · {c.get('domain', '')}]"
                                    for c in term_concepts
                                )
                                ui.label(concept_badges).classes(
                                    "text-[10px] opacity-50 italic"
                                ).style("white-space:normal;word-break:break-word")

                            # Each translation candidate
                            if translations:
                                for t in translations:
                                    tgt_text = t.get("term", "")
                                    lineage = t.get("lineage", "general")
                                    register = t.get("register", "")
                                    confidence = t.get("confidence", 0.0)
                                    verified = t.get("verified", False)
                                    gloss = t.get("gloss") or ""
                                    sources = [s for s in (t.get("sources") or []) if s]
                                    agents = [a for a in (t.get("agents") or []) if a]

                                    meta_parts = [lineage]
                                    if register:
                                        meta_parts.append(register)
                                    meta_parts.append(f"{confidence:.0%}")
                                    if verified:
                                        meta_parts.append("✓")
                                    if agents:
                                        meta_parts.append(" / ".join(agents))
                                    if sources:
                                        meta_parts.append(
                                            " · ".join(s for s in sources[:2] if s)
                                        )
                                    meta_str = "  ·  ".join(meta_parts)

                                    with ui.row().classes(
                                        "w-full items-start gap-1 cursor-pointer "
                                        "hover:bg-primary/5 rounded px-1 py-0.5"
                                    ).on("click", lambda _e, t=tgt_text: _insert(t)):
                                        ui.label("→").classes("text-[10px] opacity-30 mt-0.5")
                                        with ui.column().classes("gap-0"):
                                            ui.label(tgt_text).classes(
                                                "text-xs font-semibold"
                                            )
                                            ui.label(meta_str).classes(
                                                "text-[10px] opacity-50"
                                            ).style("white-space:normal;word-break:break-word")
                                            if gloss:
                                                ui.label(gloss).classes(
                                                    "text-[10px] italic opacity-40"
                                                ).style("white-space:normal;word-break:break-word")
                            else:
                                ui.label("no translations in KG").classes(
                                    "text-[10px] italic opacity-40"
                                )

                # ── Concept hits ─────────────────────────────────────────────
                # Deduplicate: skip concepts already surfaced via term hits above
                shown_concept_ids = set()
                if kg_hits:
                    for h in kg_hits:
                        for c in _get_concepts_for_term(h.get("id", "")):
                            shown_concept_ids.add(c.get("id", ""))

                fresh_concepts = [
                    c for c in concept_hits
                    if c.get("id", "") not in shown_concept_ids
                ]

                if fresh_concepts:
                    ui.label("KG CONCEPTS").classes(
                        "text-[9px] font-black tracking-[.2em] opacity-60 mt-2"
                    )
                    for c in fresh_concepts:
                        label = c.get("label", "")
                        domain = c.get("domain", "")
                        definition = c.get("definition", "")
                        en_terms = c.get("_en_terms", [])
                        sl_terms = c.get("_sl_terms", [])

                        with ui.card().props("flat bordered").classes(
                            "w-full rounded-lg p-2 gap-1"
                        ):
                            with ui.row().classes("items-baseline gap-2"):
                                ui.label(label).classes("text-xs font-bold")
                                if domain:
                                    ui.label(domain).classes(
                                        "text-[10px] opacity-50 italic"
                                    )
                            if definition:
                                ui.label(definition).classes(
                                    "text-[11px] opacity-60 leading-snug"
                                ).style("white-space:normal;word-break:break-word")
                            if en_terms or sl_terms:
                                with ui.row().classes("gap-1 flex-wrap mt-0.5"):
                                    for t in en_terms[:6]:
                                        ui.label(t).classes(
                                            "text-[10px] bg-blue-50 px-1 rounded opacity-70"
                                        )
                                    for t in sl_terms[:6]:
                                        ui.label(t).classes(
                                            "text-[10px] bg-green-50 px-1 rounded opacity-70"
                                        )

                # ── TM hits ──────────────────────────────────────────────────
                if tm_hits:
                    ui.label("TM SEGMENTS").classes(
                        "text-[9px] font-black tracking-[.2em] opacity-60 mt-2"
                    )
                    for m in tm_hits:
                        with ui.card().props("flat bordered").classes(
                            "w-full rounded-lg p-2 cursor-pointer hover:bg-primary/5"
                        ).on("click", lambda _e, t=m.get("target", ""): _insert(t)):
                            ui.label(m.get("source", "")).classes(
                                "text-[11px] italic leading-snug opacity-60"
                            ).style("white-space:normal;word-break:break-word")
                            ui.label(m.get("target", "")).classes(
                                "text-[12px] font-medium leading-snug"
                            ).style("white-space:normal;word-break:break-word")

                if not kg_hits and not fresh_concepts and not tm_hits:
                    ui.label("No KG or TM matches.").classes(
                        "text-xs italic opacity-60"
                    )

        query_input.on(
            "keydown.enter",
            lambda _e: background_tasks.create(_run(), name="kg_search"),
        )

    return refs
