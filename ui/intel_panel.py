"""Translation-intelligence panel — TM, Glossary, and KG sections mounted
into caller-provided slots (or a single fallback column).

TM + Glossary live above the editor; KG lives below. The caller provides
two container elements (`tm_gl_slot` and `kg_slot`) that the panel populates
via context re-entry. When called without slots (all existing tests), a
single fallback column holds all three sections in document order.

All three refresh in parallel on every segment change. No cache between
segments — translation flow needs fresh hits each time.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from nicegui import background_tasks, run, ui

from .state import WorkspaceState


# Generic stop-words/noise that pollute single-word KG hits in academic text.
# Multi-word phrases never match these, so this only filters 1-grams.
# Minimum confidence for unigram hits.  Phrases (n>=2) have no floor —
# they are almost always curated multi-word terms.  Unigrams below this
# are almost always Dice-seeded noise (function words, common verbs, etc.).
_UNIGRAM_MIN_CONFIDENCE = 0.50

_NOISE_UNIGRAMS = frozenset({
    # function words
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did",
    "of", "in", "on", "at", "to", "for", "with", "from", "by", "as",
    "this", "that", "these", "those", "it", "its", "they", "their",
    "would", "could", "should", "may", "might", "can", "will",
    "not", "no", "yes", "so", "if", "then", "than", "when", "where",
    "which", "who", "whom", "what", "whose", "how", "why",
    "i", "you", "he", "she", "we", "him", "her", "us", "my", "your",
    "his", "our", "me", "him", "them",
    # common nouns that are translation noise in humanities corpora
    "common", "thing", "things", "way", "ways", "people", "time",
    "concept", "text", "such", "one", "need", "years", "hands",
    "thoughts", "answers", "months", "days", "weeks", "parts",
    "side", "sides", "case", "cases", "point", "points", "line",
    "lines", "word", "words", "name", "names", "place", "places",
    "work", "works", "form", "forms", "body", "bodies", "head",
    "fact", "facts", "sort", "kind", "kinds", "type", "types",
    "matter", "sense", "senses", "field", "fields", "area", "areas",
    "level", "levels", "process", "processes", "term", "terms",
    "issue", "issues", "question", "questions", "problem", "problems",
    "reason", "reasons", "result", "results", "effect", "effects",
    "change", "changes", "role", "roles", "part", "parts",
    "story", "stories", "book", "books", "page", "pages",
    "first", "second", "third", "last", "next", "new", "old",
    "good", "bad", "great", "small", "large", "big", "little",
    "same", "different", "other", "another", "every", "each",
    "all", "some", "any", "few", "many", "much", "more", "most",
    "less", "least", "own", "own", "whole", "half",
    "here", "there", "now", "then", "always", "never", "often",
    "sometimes", "again", "still", "already", "yet", "just",
    "only", "even", "also", "too", "very", "quite", "rather",
    "between", "among", "through", "during", "before", "after",
    "above", "below", "up", "down", "out", "off", "over", "under",
    "again", "further", "once", "twice",
    "make", "makes", "made", "making", "take", "takes", "took",
    "taking", "get", "got", "getting", "give", "gave", "giving",
    "go", "goes", "went", "going", "come", "came", "coming",
    "see", "saw", "seeing", "know", "knew", "knowing",
    "think", "thought", "thinking", "say", "said", "saying",
    "tell", "told", "telling", "ask", "asked", "asking",
    "find", "found", "finding", "use", "used", "using",
    "seem", "seemed", "seeming", "become", "became",
    "leave", "left", "leaving", "feel", "felt", "feeling",
    "try", "tried", "trying", "let", "let", "lets", "letting",
    "begin", "began", "beginning", "keep", "kept", "keeping",
    "want", "wanted", "wanting", "mean", "meant", "meaning",
    "show", "showed", "showing", "put", "putting", "bring",
    "brought", "bringing", "play", "played", "playing",
    "read", "reading", "write", "wrote", "writing",
    "live", "lived", "living", "sit", "sat", "sitting",
    "stand", "stood", "standing", "turn", "turned", "turning",
    "look", "looked", "looking", "seem", "seemed", "seeming",
    "happen", "happened", "happening", "hold", "held", "holding",
    "open", "opened", "opening", "close", "closed", "closing",
    "start", "started", "starting", "stop", "stopped", "stopping",
    "talk", "talked", "talking", "hear", "heard", "hearing",
    "remember", "remembered", "forget", "forgot", "forgetting",
    "believe", "believed", "believing", "consider", "considered",
    "perhaps", "maybe", "might", "must", "shall",
    "both", "either", "neither", "whether", "upon",
    "while", "though", "although", "unless", "since", "because",
    "about", "around", "against", "within", "without", "across",
    "toward", "towards", "amongst", "amidst",
    "someone", "something", "anything", "nothing", "everything",
    "anyone", "everyone", "somehow", "somewhere", "nowhere",
    "anywhere", "everywhere",
    # common SL equivalents that leak as unigram hits
    "je", "v", "na", "za", "iz", "od", "do", "pri", "o", "s", "z",
    "in", "ali", "toda", "kot", "da", "ki", "bil", "bila", "bilo",
    "so", "sem", "bo", "bi", "naj", "ne", "ni", "niso",
    "ta", "to", "ti", "te", "tisti", "tista", "tisto",
    "njegov", "njena", "njihov", "moj", "tvoj",
    "tudi", "še", "že", "le", "tudi", "spet", "vedno", "nikoli",
    "zdaj", "potem", "tukaj", "tam", "zmeraj",
})


def _clean_translations(translations: list[dict]) -> list[dict]:
    """Tidy and rank the translation dicts returned by extract_entities.

    Drops empty target terms and single-word noise (e.g. 'the', 'common');
    applies the gender-inclusive preference; sorts verified-first then by
    confidence descending. NO confidence floor: humanities corpora often
    sit at 0.18-0.29 from Dice seeding, and a hard cutoff silently
    discards real curator-authored material.

    Carries through mapping-level metadata (lineage, register, gloss,
    sources, agents, mapping_id) so the hit rendering can surface the
    theoretical school, curator note, work provenance, and translator
    attribution per ontology §2.3 and §3.3.
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
            "gloss": (tr.get("gloss") or "").strip(),
            "sources": tr.get("sources") or [],
            "agents": tr.get("agents") or [],
            "mapping_id": tr.get("mapping_id") or "",
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


def _concept_context(G, concept_id: str) -> tuple[list[str], list[str], list[str]]:
    """Theory lineages + theorist names + container provenance for a concept.
    Theorists: the concept's OWN attributed_to edges (originating theorist).
    Lineages: via mapping.lineage from term→mapping bridge (filtered generic).
    Containers: via mapping.instantiated_in→source_text (where the translator
    encountered this term).  The translator is NEVER a theorist.
    """
    theorists: list[str] = []
    lineages: list[str] = []
    containers: list[str] = []
    seen_t, seen_l, seen_c = set(), set(), set()
    # --- Theorists: concept's own attributed_to out-edges ---
    for _c, ag, ed in G.out_edges(concept_id, data=True):
        if ed.get("relation") == "attributed_to" and G.has_node(ag):
            nm = G.nodes[ag].get("name")
            if nm and nm not in seen_t:
                seen_t.add(nm); theorists.append(nm)
        if len(theorists) >= 3:
            break
    # --- Lineages + containers: via term→mapping bridge ---
    examined = 0
    for term, _c, ed in G.in_edges(concept_id, data=True):
        if ed.get("relation") != "instantiates_concept":
            continue
        examined += 1
        if examined > 60:
            break
        for _t, mp, ed2 in G.out_edges(term, data=True):
            if ed2.get("relation") != "has_mapping":
                continue
            lin = (G.nodes[mp].get("lineage") or "").strip()
            if lin and lin.lower() not in _GENERIC_LINEAGES and lin not in seen_l:
                seen_l.add(lin); lineages.append(lin)
            # Container provenance: mapping -[instantiated_in]-> source_text
            for _m, st, ed3 in G.out_edges(mp, data=True):
                if ed3.get("relation") == "instantiated_in" and G.has_node(st):
                    title = (G.nodes[st].get("title") or "").strip()
                    if title and title not in seen_c:
                        seen_c.add(title); containers.append(title)
            if len(lineages) >= 3 and len(containers) >= 3:
                break
        if len(lineages) >= 3 and len(containers) >= 3:
            break
    return lineages[:3], theorists[:3], containers[:3]


def _concept_for(G, term_node_id: str) -> dict | None:
    """Return concept attrs + theory context + rhizomatic related concepts.

    Per ontology §2.2: concepts are language-independent meanings.
    Per ontology §3.4: concept-to-concept edges (extends, critiques,
    redefines, reappropriates, related_to) form the rhizomatic network.
    We surface these so the translator sees conceptual relationships,
    not just term-translation pairs.
    """
    if not G.has_node(term_node_id):
        return None
    for _u, v, d in G.out_edges(term_node_id, data=True):
        if d.get("relation") != "instantiates_concept":
            continue
        nd = G.nodes[v]
        if nd.get("type") != "concept":
            continue
        lineages, theorists, containers = _concept_context(G, v)
        # Rhizomatic concept-to-concept edges (§3.4)
        related_concepts: list[dict] = []
        seen: set[str] = set()
        for _s, tgt, ed in G.out_edges(v, data=True):
            rel = ed.get("relation")
            if rel not in ("extends", "critiques", "redefines", "reappropriates", "related_to"):
                continue
            tgt_nd = G.nodes.get(tgt, {})
            if tgt_nd.get("type") != "concept":
                continue
            label = tgt_nd.get("label") or tgt
            if label in seen:
                continue
            seen.add(label)
            related_concepts.append({
                "label": label,
                "relation": rel,
                "domain": tgt_nd.get("domain") or "",
            })
            if len(related_concepts) >= 6:
                break
        return {
            "id": v,
            "label": nd.get("label") or v,
            "domain": nd.get("domain") or "",
            "definition": (nd.get("definition") or "").strip(),
            "label_translation": (nd.get("label_translation") or "").strip(),
            "lineages": lineages,
            "theorists": theorists,
            "containers": containers,
            "related_concepts": related_concepts,
        }
    return None


def _kg_query(
    source: str, src_lang: str, tgt_lang: str, kg, *, max_hits: int = 8,
) -> list[dict]:
    """Concept-first bilingual KG lookup for humanities translation.

    Prioritises phrases and concept-linked terms over noise unigrams.
    Unigrams below _UNIGRAM_MIN_CONFIDENCE are filtered. Phrases get 60%
    of the hit quota because they carry the most conceptual weight.
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
        n_words = len(src_term.split())
        best = translations[0]

        # Noise filtering is via the expanded _NOISE_UNIGRAMS set above.
        # No confidence floor — verified and low-confidence unigrams both
        # pass.  The noise list handles function words and common vocabulary
        # that would otherwise drown conceptual hits.  Per ontology §2.1,
        # terms are surface forms; concepts are language-independent meanings.
        # We surface both, grouped by concept.

        concept = _concept_for(G, node_id)
        related = _related_via_concept(
            G, node_id, preferred_lang=tgt_lang, max_siblings=6,
        )
        hits.append({
            "id": node_id,
            "src_term": src_term,
            "src_lang": node_lang,
            "tgt_term": best["term"],
            "tgt_lang": tgt_lang,
            "confidence": best["confidence"],
            "verified": best["verified"],
            "alt_translations": translations[1:4],
            "n": n_words,
            "freq": ent.get("frequency", 0),
            "related": related,
            "concept": concept,
            # Mapping-level metadata (ontology §2.3, §3.3)
            "lineage": best.get("lineage", ""),
            "gloss": best.get("gloss", ""),
            "sources": best.get("sources", []),
            "agents": best.get("agents", []),
            # Entity-level data (ontology §2.1)
            "variants": ent.get("variants") or [],
            "display_form": ent.get("display_form") or "",
        })

    # Concept-first ranking: phrases with concepts > phrases without >
    # unigrams with concepts > unigrams without. Within each tier,
    # verified-first, then confidence, then frequency.
    def _rank(h: dict) -> tuple:
        has_concept = h.get("concept") is not None
        return (
            not h["verified"],
            -(h["confidence"] or 0),
            0 if has_concept else 1,  # concept-linked first
            -(h["freq"] or 0),
        )

    phrases = sorted([h for h in hits if h["n"] >= 2], key=_rank)
    unigrams = sorted([h for h in hits if h["n"] == 1], key=_rank)
    # Phrases get 60% of slots — they carry the most conceptual weight.
    # Unigrams fill the rest.
    phrase_quota = int(max_hits * 0.6)
    chosen = phrases[:phrase_quota]
    chosen += unigrams[: max_hits - len(chosen)]
    if len(chosen) < max_hits:
        chosen += phrases[phrase_quota : phrase_quota + (max_hits - len(chosen))]
    if len(chosen) < max_hits:
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
def _render_hit_fn(h: dict, _insert: Callable[[str], None]) -> None:
    """One bilingual hit with mapping metadata — lineage, gloss, frequency,
    variants, source provenance, and translator attribution.

    Module-level so both intel_panel.build() and the reviewer page can
    reuse the exact same rendering logic.
    """
    t_term = h["tgt_term"]
    tgt_alts = h.get("alt_translations") or []
    tgt_sibs = [
        r for r in h.get("related", []) or []
        if r.get("lang") == h["tgt_lang"]
    ]
    src_sibs = [
        r for r in h.get("related", []) or []
        if r.get("lang") != h["tgt_lang"]
    ]

    # ── Main hit row: src → tgt with confidence/verified ──
    with ui.row().classes("w-full items-center gap-x-1.5 gap-y-0.5 flex-wrap"):
        with ui.row().classes(
            "items-center gap-2 no-wrap cursor-pointer hover:bg-primary/5 rounded"
        ).on("click", lambda _e, t=t_term: _insert(t)):
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
        # Lineage badge (theoretical school — ontology §2.3)
        lineage = h.get("lineage") or ""
        if lineage and lineage.lower() not in ("general", "glossary", ""):
            ui.badge(lineage, color="blue-grey-4").props("outline").classes(
                "text-[8px] px-1 normal-case"
            ).tooltip("theoretical lineage of this translation")
        # Frequency (corpus attestation — ontology §2.1)
        freq = h.get("freq") or 0
        if freq:
            ui.label(f"×{freq}").classes(
                "text-[8px] tabular-nums opacity-40"
            ).tooltip("times seen in corpus")
        # Translator attribution (ontology §3.3)
        agents = h.get("agents") or []
        if agents and h.get("verified"):
            for ag in agents[:1]:
                if isinstance(ag, str) and ag.strip():
                    ui.label(f"↪ {ag}").classes(
                        "text-[8px] opacity-40"
                    ).tooltip("verified by")

    # ── Alt translations with lineage labels ──
    if tgt_alts or tgt_sibs or src_sibs:
        with ui.row().classes("w-full items-center gap-x-1 gap-y-0.5 flex-wrap pl-4"):
            for alt in tgt_alts[:3]:
                alt_term = alt.get("term", "") if isinstance(alt, dict) else str(alt)
                if not alt_term:
                    continue
                alt_lin = alt.get("lineage", "") if isinstance(alt, dict) else ""
                tooltip_parts = []
                if alt_lin and alt_lin.lower() not in ("general", "glossary", ""):
                    tooltip_parts.append(f"lineage: {alt_lin}")
                if isinstance(alt, dict) and alt.get("verified"):
                    tooltip_parts.append("verified")
                ui.button(
                    alt_term,
                    on_click=lambda _e, t=alt_term: _insert(t),
                ).props("flat dense rounded color=positive").classes(
                    "text-[10px] normal-case h-5 px-1.5"
                ).tooltip(" | ".join(tooltip_parts) if tooltip_parts else alt_term)
                # Show lineage on alt if different from main
                if isinstance(alt, dict) and alt_lin and alt_lin.lower() not in ("general", "glossary", lineage.lower(), ""):
                    ui.label(alt_lin).classes(
                        "text-[7px] opacity-40"
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

    # ── Gloss (curator note — ontology §2.3) ──
    gloss = h.get("gloss") or ""
    if gloss:
        ui.label(gloss).classes(
            "text-[9px] leading-relaxed italic opacity-50 pl-4 mt-0.5"
        ).tooltip("curator's translation note")

    # ── Source provenance (instantiated_in — ontology §3.3) ──
    sources = h.get("sources") or []
    if sources:
        source_str = " · ".join(str(s) for s in sources[:2] if s)
        if source_str:
            ui.label(f"↳ {source_str}").classes(
                "text-[8px] opacity-40 pl-4"
            ).tooltip("attested in this work")

    # ── Term variants (ontology §2.1) ──
    variants = h.get("variants") or []
    if variants:
        var_str = ", ".join(variants[:3])
        ui.label(f"also: {var_str}").classes(
            "text-[8px] opacity-30 pl-4"
        ).tooltip("known surface form variants")

def build(
    state: WorkspaceState,
    deps: dict,
    *,
    tm_gl_slot: ui.element | None = None,
    kg_slot: ui.element | None = None,
) -> dict:
    tm = deps["tm"]
    glossary = deps["glossary"]
    kg = deps["kg"]
    parse_lang_pair = deps["parse_lang_pair"]

    refs: dict = {}

    if tm_gl_slot is None or kg_slot is None:
        fallback = ui.column().classes("w-full gap-2")
        tm_gl_slot = tm_gl_slot or fallback
        kg_slot = kg_slot or fallback

    with tm_gl_slot:
        with ui.column().classes("w-full gap-2"):
            # ---------------------------------------------------- TM section
            with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    ui.icon("memory", size="16px").props("color=primary")
                    ui.label("TRANSLATION MEMORY").classes(
                        "text-[10px] font-black tracking-[.3em] opacity-70"
                    )
                tm_container = ui.column().classes("w-full gap-2")
                refs["tm_container"] = tm_container

            # ---------------------------------------------- Glossary section
            with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    ui.icon("menu_book", size="16px").props("color=primary")
                    ui.label("GLOSSARY").classes(
                        "text-[10px] font-black tracking-[.3em] opacity-70"
                    )
                gl_container = ui.column().classes("w-full gap-2")
                refs["gl_container"] = gl_container

    with kg_slot:
        with ui.column().classes("w-full gap-2"):
            # ---------------------------------------------------- KG section
            with ui.card().props("flat bordered").classes("w-full p-4 rounded-2xl"):
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    ui.icon("account_tree", size="16px").props("color=primary")
                    ui.label("KNOWLEDGE GRAPH").classes(
                        "text-[10px] font-black tracking-[.3em] opacity-70"
                    )
                kg_container = ui.column().classes("w-full gap-2")
                refs["kg_container"] = kg_container
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
        _render_hit_fn(h, _insert)


    async def _refresh_kg():
        seg = _current_seg()
        if seg is None or kg is None:
            return
        idx = state.active_index
        src_lang, tgt_lang = parse_lang_pair(state.lang_pair)
        try:
            hits = await run.io_bound(
                _kg_query, seg["source"], src_lang, tgt_lang, kg,
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
                        "w-full items-center gap-x-2 gap-y-1 mb-1 flex-wrap"
                    ):
                        ui.icon("hub", size="13px").props("color=primary")
                        if concept is None:
                            ui.label("TERMS").classes(
                                "text-[10px] font-black tracking-[.3em] opacity-90"
                            )
                        else:
                            # Bilingual concept label — the central organizing unit
                            label = str(concept.get("label") or "").upper()
                            label_tr = concept.get("label_translation") or ""
                            if label_tr:
                                ui.label(f"{label} · {label_tr}").classes(
                                    "text-[10px] font-black tracking-[.2em] opacity-90"
                                )
                            else:
                                ui.label(label).classes(
                                    "text-[10px] font-black tracking-[.3em] opacity-90"
                                )
                            if concept.get("domain"):
                                ui.badge(
                                    concept["domain"], color="grey-5",
                                ).classes("text-[9px] px-1")
                    # Concept definition (if curated)
                    if concept and concept.get("definition"):
                        ui.label(concept["definition"]).classes(
                            "text-[10px] leading-relaxed opacity-70 mb-1"
                        )
                    # Theorists + lineages + containers as badges
                    if concept:
                        with ui.row().classes("w-full gap-1 flex-wrap mb-1"):
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
                            for ct in concept.get("containers", []):
                                ui.badge(ct, color="teal-5").props(
                                    "outline"
                                ).classes("text-[9px] px-1").tooltip(
                                    "you translated this term in this work"
                                )
                        # Related concepts (rhizomatic network — §3.4)
                        rel_concepts = concept.get("related_concepts", [])
                        if rel_concepts:
                            with ui.row().classes("w-full gap-1 flex-wrap mb-1"):
                                ui.label("→").classes(
                                    "text-[9px] opacity-40 shrink-0"
                                )
                                for rc in rel_concepts:
                                    rel_label = rc.get("label", "")
                                    rel_type = rc.get("relation", "related_to")
                                    tooltip_text = {
                                        "extends": "extends this concept",
                                        "critiques": "critiques this concept",
                                        "redefines": "redefines this concept",
                                        "reappropriates": "reappropriates this concept",
                                        "related_to": "related concept",
                                    }.get(rel_type, "related concept")
                                    ui.badge(
                                        rel_label, color="blue-grey-4",
                                    ).props("outline").classes(
                                        "text-[9px] px-1"
                                    ).tooltip(tooltip_text)
                    with ui.column().classes("w-full gap-1"):
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
        src, tgt = parse_lang_pair(state.lang_pair)
        try:
            # Canonical CAT-tool lookup: whole-segment fuzzy + concordance.
            # Both calls hop to NiceGUI's thread pool via run.io_bound (the
            # high-level API) — main event loop stays responsive. Both are
            # oriented to the project's (src, tgt) so an sl->en project
            # gets SL source / EN target hits.
            fuzzy = await run.io_bound(
                tm.lookup_fuzzy, seg["source"], src, tgt, threshold=75.0, limit=5
            ) or []
            concord = await run.io_bound(
                tm.search_concordance, seg["source"], src, tgt, top_n=3
            ) or []
        except Exception as e:
            print(f"[intel TM] {e}")
            return
        if state.active_index != idx or tm_container.is_deleted:
            return
        # Dedup: a fuzzy hit shouldn't also appear in concordance.
        fuzzy_keys = {(m.get("source", ""), m.get("target", "")) for m in fuzzy}
        concord = [
            c for c in concord
            if (c.get("source", ""), c.get("target", "")) not in fuzzy_keys
        ]
        near = [m for m in fuzzy if (m.get("score") or 0) >= 95]
        partial = [m for m in fuzzy if (m.get("score") or 0) < 95]

        def _render_tm_match(m: dict) -> None:
            score = int(m.get("score") or 0)
            badge_color = (
                "positive" if score >= 100
                else "primary" if score >= 95
                else "warning"
            )
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

        tm_container.clear()
        with tm_container:
            if not fuzzy:
                ui.label("No fuzzy matches (≥75%).").classes(
                    "text-xs italic opacity-90"
                )
            if near:
                ui.label("NEAR-EXACT MATCHES").classes(
                    "text-[9px] font-black tracking-[.2em] opacity-90"
                )
                for m in near:
                    _render_tm_match(m)
            if partial:
                ui.label("FUZZY MATCHES").classes(
                    "text-[9px] font-black tracking-[.2em] opacity-90 mt-2"
                )
                for m in partial:
                    _render_tm_match(m)
            if concord:
                ui.label("CONCORDANCE").classes(
                    "text-[9px] font-black tracking-[.2em] opacity-90 mt-2"
                )
                for c in concord:
                    with (
                        ui.card()
                        .props("flat bordered")
                        .classes(
                            "w-full rounded-xl p-3 cursor-pointer "
                            "flex-row items-center gap-3 hover:bg-primary/5"
                        )
                        .on("click", lambda _e, t=c.get("target", ""): _insert(t))
                    ):
                        with ui.column().classes("gap-0.5 flex-1 min-w-0"):
                            ui.label(c.get("source", "")).classes(
                                "text-[11px] italic leading-snug opacity-60"
                            ).style("white-space:normal;word-break:break-word")
                            ui.label(c.get("target", "")).classes(
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
        src, tgt = parse_lang_pair(state.lang_pair)
        try:
            hits = await run.io_bound(
                glossary.lookup_terms, seg["source"], src, tgt,
            ) or []
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
        # create_lazy (NiceGUI documented API) coalesces refresh storms:
        # while one refresh runs, only the newest queued one survives.
        # Task names are global per process, so suffix with the state id —
        # two open workspaces must not cancel each other's refreshes.
        sid = id(state)
        background_tasks.create_lazy(_refresh_kg(), name=f"intel_kg_refresh_{sid}")
        background_tasks.create_lazy(_refresh_tm(), name=f"intel_tm_refresh_{sid}")
        background_tasks.create_lazy(_refresh_gl(), name=f"intel_gl_refresh_{sid}")

    state.subscribe("active_index", _refresh_all)

    # Initial paint — populate all three sections immediately.
    _refresh_all()

    return refs
