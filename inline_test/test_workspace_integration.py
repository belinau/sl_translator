"""End-to-end integration tests for the Phase-1 workspace.

These tests use NiceGUI's `user` fixture (in-process; no real browser) and
mount the actual `segment_editor`, `segment_navigator`, and `intel_panel`
components with stub `tm`/`glossary`/`kg`/`translator` dependencies. They
would have caught the three bugs that shipped:

1. ghost overlay never updates on segment switch
2. predictor JS never receives the bundle
3. intel-panel tabs.value comparison is broken (string vs Tab object)

Anything that genuinely requires a JS runtime (the ghost-text engine in the
browser) is tested via the side-channel: we intercept `run_javascript` calls
sent by `predictions.push_bundle` and assert their payload.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from nicegui import ui

from _kg_helpers import make_kg, seed_concept, seed_mapping, seed_term
from ui import intel_panel, predictions, segment_editor, segment_navigator, settings as ui_settings
from ui.state import WorkspaceState


# ---------------------------------------------------------------------------
# Graph-wiring helper — populates BOTH the graph and the _exact_kp index
# used by extract_entities. Source terms passed in via `src_node_id` must
# already be seeded by the caller; this helper only adds target nodes +
# mapping edges.
# ---------------------------------------------------------------------------

def _wire_translations(kg, src_node_id: str, translations: list[dict],
                       tgt_lang: str = "sl") -> None:
    """Materialise translation edges off an existing source term node.

    For each entry, ensures the target term node exists (and is registered
    in the flashtext index — though only the source side is searched in
    practice) and links the source term via a mapping node carrying
    confidence/verified/lineage/register.
    """
    # Accept either a KnowledgeGraph instance or a bare DiGraph for legacy
    # call sites; the seed helpers want the wrapper.
    if hasattr(kg, "G"):
        G = kg.G
    else:  # pragma: no cover — legacy callsite
        G = kg

    for tr in translations:
        sl_term = tr["term"]
        sl_node_id = f"term:{tgt_lang}:{sl_term}"
        if not G.has_node(sl_node_id):
            if hasattr(kg, "_exact_kp"):
                seed_term(kg, tgt_lang, sl_term, frequency=0)
            else:  # pragma: no cover
                G.add_node(
                    sl_node_id, type="term", term=sl_term,
                    lang=tgt_lang, frequency=0,
                )
        if hasattr(kg, "_exact_kp"):
            seed_mapping(
                kg, src_node_id, sl_node_id,
                confidence=tr.get("confidence", 0),
                verified=tr.get("verified", False),
                lineage=tr.get("lineage", "general"),
                register=tr.get("register", "academic"),
            )
        else:  # pragma: no cover
            map_id = f"map:{src_node_id}>>{sl_node_id}:{tr.get('lineage', 'general')}"
            G.add_node(
                map_id, type="translation_mapping",
                confidence=tr.get("confidence", 0),
                verified=tr.get("verified", False),
                lineage=tr.get("lineage", ""),
                register=tr.get("register", ""),
            )
            G.add_edge(src_node_id, map_id, relation="has_mapping")
            G.add_edge(map_id, sl_node_id, relation="maps_to")


# ---------------------------------------------------------------------------
# Stub backends
# ---------------------------------------------------------------------------

class StubTM:
    def __init__(self, fuzzy=None, conc=None):
        self._fuzzy = fuzzy or []
        self._conc = conc or []

    def lookup_fuzzy(self, text, threshold=70.0, limit=5):
        return list(self._fuzzy)

    def search_concordance(self, text, top_n=5):
        return list(self._conc)


class StubGlossary:
    def __init__(self, hits=None):
        self._hits = hits or []

    def lookup_terms(self, text, src, tgt):
        return list(self._hits)


class StubKG:
    def __init__(self, entities=None):
        self._entities = entities or []

    def extract_entities(self, text, target_lang="sl"):
        return list(self._entities)

    def promote_pair(self, *a, **kw):
        return None

    def save(self):
        return None



class StubQA:
    def check_segment(self, src, tgt, glossary_hits=None):
        return []


def _parse_lang_pair(p):
    src, _, tgt = (p or "en->sl").partition("->")
    return src or "en", tgt or "sl"


def _make_state(segments=None) -> WorkspaceState:
    segments = segments or [
        {"id": 0, "source": "Hello world.", "target": "Pozdrav svet.", "status": "done"},
        {"id": 1, "source": "How are you?", "target": "", "status": "pending"},
        {"id": 2, "source": "Goodbye.", "target": "Adijo.", "status": "pending"},
    ]
    ws = {
        "project_id": "test",
        "filename": "fixture.docx",
        "lang_pair": "en->sl",
        "active_index": 0,
        "segments": segments,
    }
    return WorkspaceState(ws, save_callback=lambda _: None)


def _deps(tm=None, glossary=None, kg=None, qa=None) -> dict:
    return {
        "tm": tm or StubTM(),
        "glossary": glossary or StubGlossary(),
        "kg": kg or StubKG(),
        "qa_engine": qa or StubQA(),
        "parse_lang_pair": _parse_lang_pair,
    }


# ---------------------------------------------------------------------------
# Humanities KG fixture builder (real corpus pairs from spec)
# ---------------------------------------------------------------------------

def _build_humanities_kg():
    """Build a KG instance with real humanities term pairs, linked via
    concept nodes with instantiates_concept edges. Uses the production
    KnowledgeGraph (under conftest's fast __init__) so extract_entities
    works with the flashtext index populated by seed_term."""
    kg = make_kg()

    # --- Multi-word theoretical phrases ---
    seed_term(kg, "en", "imagined futures", frequency=42)
    _wire_translations(kg, "term:en:imagined futures", [
        {"term": "imaginirane prihodnosti", "confidence": 0.92,
         "verified": True, "lineage": "manual"}])

    seed_term(kg, "en", "compulsory able-bodiedness", frequency=15)
    _wire_translations(kg, "term:en:compulsory able-bodiedness", [
        {"term": "obvezna telesna sposobnost", "confidence": 0.88,
         "verified": True, "lineage": "manual"}])

    seed_term(kg, "en", "crip theory", frequency=28)
    _wire_translations(kg, "term:en:crip theory", [
        {"term": "krip teorija", "confidence": 0.85,
         "verified": True, "lineage": "manual"}])

    seed_term(kg, "en", "feminist epistemology", frequency=12)
    _wire_translations(kg, "term:en:feminist epistemology", [
        {"term": "feministična epistemologija", "confidence": 0.90,
         "verified": True, "lineage": "manual"}])

    seed_term(kg, "en", "queer phenomenology", frequency=8)
    _wire_translations(kg, "term:en:queer phenomenology", [
        {"term": "queer fenomenologija", "confidence": 0.82,
         "verified": False, "lineage": "auto"}])

    # --- Single-word singleton (has translation) ---
    seed_term(kg, "en", "intersectionality", frequency=35)
    _wire_translations(kg, "term:en:intersectionality", [
        {"term": "intersekcionalnost", "confidence": 0.95,
         "verified": True, "lineage": "manual"}])

    # --- Noise unigrams (filtered by the noise list in _kg_query) ---
    seed_term(kg, "en", "common", frequency=525)
    seed_term(kg, "en", "the", frequency=9999)

    # --- Concept nodes + instantiates_concept edges (incl. SL siblings) ---
    seed_concept(
        kg, "imagined_futures", "imagined futures", domain="humanities",
        terms=("term:en:imagined futures",),
    )
    seed_concept(
        kg, "crip_theory", "crip theory", domain="disability studies",
        terms=("term:en:crip theory",),
    )
    seed_concept(
        kg, "feminist_epistemology", "feminist epistemology",
        domain="feminist philosophy",
        terms=("term:en:feminist epistemology",),
    )

    # SL siblings — same concepts as their EN counterparts.
    seed_term(kg, "sl", "imaginirane prihodnosti", frequency=18)
    seed_term(kg, "sl", "imaginarne prihodnosti", frequency=12)
    seed_term(kg, "sl", "krip teorija", frequency=14)
    kg.G.add_edge("term:sl:imaginirane prihodnosti",
                  "concept:imagined_futures",
                  relation="instantiates_concept")
    kg.G.add_edge("term:sl:imaginarne prihodnosti",
                  "concept:imagined_futures",
                  relation="instantiates_concept")
    kg.G.add_edge("term:sl:krip teorija",
                  "concept:crip_theory",
                  relation="instantiates_concept")

    return kg


def _build_humanities_state():
    """Workspace state with a source segment containing real humanities terms."""
    return _make_state(segments=[
        {"id": 0,
         "source": "At first glance these contradictory imagined futures "
                   "and crip theory reshape feminist epistemology.",
         "target": "", "status": "pending"},
    ])


class ThresholdRespectingTM(StubTM):
    """TM stub that actually filters by the threshold parameter, unlike the
    base StubTM which returns all matches regardless."""
    def lookup_fuzzy(self, text, threshold=70.0, limit=5):
        return [m for m in self._fuzzy if m.get("score", 0) >= threshold]


# ---------------------------------------------------------------------------
# Bug 1: overlay sync on segment switch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initial_render_shows_target_in_overlay(user):
    state = _make_state()
    refs: dict = {}

    @ui.page("/edit")
    def page():
        ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")
        refs.update(segment_editor.build(state, _deps(), on_confirm=lambda: None))

    await user.open("/edit")
    overlay = refs["ghost_overlay"]
    assert "Pozdrav svet." in overlay.content, (
        f"overlay must mirror initial target; got {overlay.content!r}"
    )


@pytest.mark.asyncio
async def test_overlay_updates_when_active_index_changes(user):
    """This catches Bug 1: previously the overlay kept the old segment's text."""
    state = _make_state()
    refs: dict = {}

    @ui.page("/edit_switch")
    def page():
        ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")
        refs.update(segment_editor.build(state, _deps(), on_confirm=lambda: None))

    await user.open("/edit_switch")
    overlay = refs["ghost_overlay"]
    assert "Pozdrav svet." in overlay.content

    # Switch to segment 2 (has target "Adijo.") and assert overlay flips.
    state.set_active(2)
    await asyncio.sleep(0.15)
    assert "Adijo." in overlay.content, f"overlay did not switch: {overlay.content!r}"
    assert "Pozdrav svet." not in overlay.content, "previous segment leaked"

    # Back to segment 0
    state.set_active(0)
    await asyncio.sleep(0.15)
    assert "Pozdrav svet." in overlay.content, f"return-trip lost: {overlay.content!r}"
    assert "Adijo." not in overlay.content


@pytest.mark.asyncio
async def test_overlay_updates_when_state_current_target_changes_externally(user):
    """Click-to-insert from intel panel runs state.set_target which fires the
    'target' subscriber. The overlay must update from that."""
    state = _make_state()
    refs: dict = {}

    @ui.page("/edit_external")
    def page():
        ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")
        refs.update(segment_editor.build(state, _deps(), on_confirm=lambda: None))

    await user.open("/edit_external")
    overlay = refs["ghost_overlay"]
    state.set_target("appended bit")
    await asyncio.sleep(0.2)
    assert "appended bit" in overlay.content


# ---------------------------------------------------------------------------
# Bug 2: predictor JS bundle delivery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_push_bundle_emits_setBundle_call(monkeypatch):
    """Bug 2: ensure push_bundle ships a setBundle JS call (regardless of
    whether a real browser receives it). We monkeypatch ui.run_javascript and
    capture the JS string."""
    captured: list[str] = []

    def fake_run_javascript(code):
        captured.append(code)

    monkeypatch.setattr("ui.predictions.ui.run_javascript", fake_run_javascript)

    await predictions.push_bundle(
        textarea_id=42,
        source_text="Hello world.",
        src="en",
        tgt="sl",
        tm=StubTM(fuzzy=[{"source": "Hello.", "target": "Pozdrav.", "score": 95}]),
        glossary=StubGlossary(hits=[{"source_term": "world", "target_term": "svet"}]),
        kg=StubKG(),
    )
    assert captured, "push_bundle never called run_javascript"
    js = captured[0]
    assert "setBundle" in js, f"JS payload missing setBundle: {js!r}"
    assert "42" in js, "textarea_id not in JS payload"
    # Bundle should contain at least one glossary candidate (svet) and one TM target (Pozdrav.)
    assert "svet" in js
    assert "Pozdrav." in js


@pytest.mark.asyncio
async def test_push_bundle_uses_client_when_provided():
    """When a client is passed, the JS must route through client.run_javascript
    (background-task safe path)."""
    captured: list[str] = []

    class FakeClient:
        def run_javascript(self, code):
            captured.append(code)

    await predictions.push_bundle(
        textarea_id=7,
        source_text="text",
        src="en",
        tgt="sl",
        tm=StubTM(),
        glossary=StubGlossary(),
        kg=StubKG(),
        client=FakeClient(),
    )
    assert captured, "client.run_javascript was not invoked"
    assert "setBundle" in captured[0]
    assert "7" in captured[0]


# ---------------------------------------------------------------------------
# Bug 3: intel panel TM/KG/Glossary refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intel_panel_paints_tm_on_initial_load(user):
    """TM section renders near-exact matches (≥95%) on initial paint."""
    state = _make_state()
    tm = StubTM(fuzzy=[
        {"source": "Hello world.", "target": "Pozdrav svet.", "score": 98},
    ])

    @ui.page("/intel")
    def page():
        intel_panel.build(state, _deps(tm=tm))

    await user.open("/intel")
    await asyncio.sleep(0.3)
    await user.should_see("NEAR-EXACT MATCHES")
    await user.should_see("Pozdrav svet.")


@pytest.mark.asyncio
async def test_intel_panel_shows_no_match_label_for_empty_tm(user):
    state = _make_state()

    @ui.page("/intel_empty")
    def page():
        intel_panel.build(state, _deps())  # empty stubs

    await user.open("/intel_empty")
    await asyncio.sleep(0.3)
    await user.should_see("No near-exact matches")


@pytest.mark.asyncio
async def test_intel_panel_repaints_on_segment_switch(user):
    """When the active segment changes, the intel cache is invalidated and
    the active tab is repainted. Bug 3 also broke this path."""
    state = _make_state()

    class TwoSegmentTM(StubTM):
        def lookup_fuzzy(self, text, threshold=70.0, limit=5):
            if "How" in text:
                return [{"source": "How?", "target": "Kako?", "score": 96}]
            return []

    @ui.page("/intel_switch")
    def page():
        intel_panel.build(state, _deps(tm=TwoSegmentTM()))

    await user.open("/intel_switch")
    await asyncio.sleep(0.3)
    await user.should_see("No near-exact matches")

    state.set_active(1)  # segment 1: "How are you?"
    await asyncio.sleep(0.3)
    await user.should_see("Kako?")


def _kg_with_graph():
    """Build a KG containing the multi-word phrase + concept + sibling
    structure the strategic query expects."""
    kg = make_kg()
    seed_term(kg, "en", "imagined futures", frequency=42)
    _wire_translations(
        kg, "term:en:imagined futures",
        [{"term": "imaginirane prihodnosti", "confidence": 0.92,
          "verified": True, "lineage": "manual"}],
    )
    seed_concept(
        kg, "imagined_futures", "imagined futures", domain="humanities",
        terms=("term:en:imagined futures",),
    )
    seed_term(kg, "sl", "imaginarne prihodnosti", frequency=12)
    kg.G.add_edge(
        "term:sl:imaginarne prihodnosti",
        "concept:imagined_futures",
        relation="instantiates_concept",
    )

    state = _make_state(segments=[
        {"id": 0, "source": "At first glance these contradictory imagined futures matter.",
         "target": "", "status": "pending"},
    ])
    return state, kg


@pytest.mark.asyncio
async def test_intel_panel_renders_kg_entities_with_relations(user):
    """KG section shows entity term, verified translation, AND sibling
    terms via the instantiated concept — all at once, no tabs."""
    state, kg = _kg_with_graph()

    @ui.page("/intel_kg")
    def page():
        intel_panel.build(state, _deps(kg=kg))

    await user.open("/intel_kg")
    await asyncio.sleep(0.5)
    await user.should_see("imagined futures")          # source term (LEFT)
    await user.should_see("imaginirane prihodnosti")   # verified translation (RIGHT)
    await user.should_see("imaginarne prihodnosti")    # sibling via concept node


@pytest.mark.asyncio
async def test_intel_panel_renders_glossary_section(user):
    """Glossary section renders at the same time as KG and TM (no tabs)."""
    state = _make_state()
    g = StubGlossary(hits=[
        {"source_term": "hello", "target_term": "pozdrav", "note": ""},
    ])

    @ui.page("/intel_gl")
    def page():
        intel_panel.build(state, _deps(glossary=g))

    await user.open("/intel_gl")
    await asyncio.sleep(0.4)
    await user.should_see("pozdrav")


def test_kg_query_prefers_multi_word_phrases_and_filters_noise():
    """The extract_entities-driven query must:
      - surface multi-word phrases (humanities terminology lives in them),
      - skip generic stopwords ("the", "common"…) at the source side,
      - rank verified translations above unverified for the same source term,
      - reject any term without qualifying translations,
      - cap output at max_hits (default 8) to keep the panel compact.

    There is NO 0.6 confidence floor in the new query: humanities corpora
    are Dice-seeded around 0.18-0.29, so dropping low-confidence material
    silently discards the curator's work. Verified-first sorting still
    surfaces the curator-blessed translation as best.
    """
    from ui.intel_panel import _kg_query

    kg = make_kg()
    # Real multi-word humanities phrase with translations + a concept.
    seed_term(kg, "en", "imagined futures", frequency=42)
    _wire_translations(kg, "term:en:imagined futures", [
        {"term": "imaginirane prihodnosti", "confidence": 0.92,
         "verified": True, "lineage": "manual"},
        {"term": "alternativna upodobitev", "confidence": 0.3,
         "verified": False, "lineage": "auto"},
    ])
    # A common single-word hit that SHOULD be skipped by the noise filter.
    seed_term(kg, "en", "common", frequency=525)
    # A concept the multi-word phrase instantiates, plus a sibling.
    seed_concept(
        kg, "imagined_futures", "imagined futures", domain="humanities",
        terms=("term:en:imagined futures",),
    )
    seed_term(kg, "sl", "imaginarne prihodnosti", frequency=12)
    kg.G.add_edge(
        "term:sl:imaginarne prihodnosti",
        "concept:imagined_futures",
        relation="instantiates_concept",
    )

    hits = _kg_query(
        "At first glance these contradictory imagined futures have nothing in common",
        src_lang="en", tgt_lang="sl", kg=kg,
    )

    # The multi-word phrase must surface, the noisy "common" must not.
    terms = [h["src_term"] for h in hits]
    assert "imagined futures" in terms, f"missing bigram hit: {terms}"
    assert "common" not in terms, f"noise unigram should be filtered: {terms}"

    # The bigram is the only non-noise hit, so it lands first.
    assert hits[0]["src_term"] == "imagined futures"
    assert hits[0]["n"] == 2

    # Verified translation ranks above the low-confidence unverified one.
    assert hits[0]["tgt_term"] == "imaginirane prihodnosti"
    assert hits[0]["verified"] is True
    # The low-confidence rendering survives as an alternative.
    alt_terms = [t["term"] for t in hits[0].get("alt_translations") or []]
    assert "alternativna upodobitev" in alt_terms

    # Sibling via concept must surface.
    related_terms = [r["term"] for r in hits[0]["related"]]
    assert "imaginarne prihodnosti" in related_terms


def test_kg_query_caps_at_eight_hits_when_corpus_is_dense():
    """Even with many qualifying hits the strategic query caps output at
    max_hits (default 8), with the phrase/unigram quota split keeping
    neither bucket starved."""
    from ui.intel_panel import _kg_query

    kg = make_kg()
    for w in ["alpha", "beta", "gamma", "delta", "epsilon",
              "zeta", "eta", "theta", "iota", "kappa"]:
        seed_term(kg, "en", w, frequency=200)
        _wire_translations(kg, f"term:en:{w}", [
            {"term": f"{w}-sl", "confidence": 0.9,
             "verified": True}])

    hits = _kg_query(
        "alpha beta gamma delta epsilon zeta eta theta iota kappa",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    assert len(hits) <= 8, f"expected ≤8, got {len(hits)}"


@pytest.mark.asyncio
async def test_intel_panel_shows_all_three_sections_simultaneously(user):
    """The signature property of the new layout: KG, TM, and Glossary are
    all visible on the page at the same time (no tab switching)."""
    state, kg = _kg_with_graph()
    tm = StubTM(fuzzy=[
        {"source": "At first glance these contradictory imagined futures matter.",
         "target": "Na prvi pogled.", "score": 97},
    ])
    g = StubGlossary(hits=[
        {"source_term": "futures", "target_term": "prihodnosti", "note": ""},
    ])

    @ui.page("/intel_all")
    def page():
        intel_panel.build(state, _deps(kg=kg, tm=tm, glossary=g))

    await user.open("/intel_all")
    await asyncio.sleep(0.5)
    # All three section headers and at least one hit from each are present
    # simultaneously — no clicking, no tab activation needed.
    await user.should_see("KNOWLEDGE GRAPH")
    await user.should_see("TRANSLATION MEMORY")
    await user.should_see("GLOSSARY")
    await user.should_see("imagined futures")
    await user.should_see("Na prvi pogled.")
    await user.should_see("prihodnosti")


# ---------------------------------------------------------------------------
# Navigator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_navigator_lists_all_segments(user):
    state = _make_state(segments=[
        {"id": i, "source": f"src-{i}", "target": "", "status": "pending"}
        for i in range(25)
    ])

    @ui.page("/nav")
    def page():
        segment_navigator.build(state)

    await user.open("/nav")
    table = user.find(kind=ui.table).elements.pop()
    assert table.rows is not None
    assert len(table.rows) == 25, f"navigator should have 25 rows, got {len(table.rows)}"


@pytest.mark.asyncio
async def test_navigator_refreshes_rows_when_segments_notify(user):
    state = _make_state()

    @ui.page("/nav_notify")
    def page():
        segment_navigator.build(state)

    await user.open("/nav_notify")
    table = user.find(kind=ui.table).elements.pop()
    initial_rows = list(table.rows or [])
    assert any(r.get("status") != "✓" for r in initial_rows), "fixture has pending rows"

    state.mark_done(1)  # fires notify("segments")
    await asyncio.sleep(0.1)
    updated = list(table.rows or [])
    done_row = next(r for r in updated if r["id"] == 1)
    assert done_row["status"] == "✓"


# ---------------------------------------------------------------------------
# Editor wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_editor_textarea_is_bound_to_state_current(user):
    """The persistent textarea is bound via bind_value to state.current,
    which is what keeps the DOM stable across segment switches.

    set_active/set_target are the bind-aware mutation paths; raw dict writes
    aren't observed by NiceGUI's binding for plain dicts (would need a
    BindableDict)."""
    state = _make_state()
    refs: dict = {}

    @ui.page("/edit_bind")
    def page():
        ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")
        refs.update(segment_editor.build(state, _deps(), on_confirm=lambda: None))

    await user.open("/edit_bind")
    ta = refs["target_textarea"]
    assert ta.value == "Pozdrav svet."

    state.set_active(2)
    await asyncio.sleep(0.2)
    assert ta.value == "Adijo.", "textarea value did not follow active segment"

    state.set_target("via-set-target")
    await asyncio.sleep(0.2)
    assert ta.value == "via-set-target", "state.set_target should propagate to textarea"


@pytest.mark.asyncio
async def test_editor_status_badge_updates_on_done(user):
    state = _make_state()
    refs: dict = {}

    @ui.page("/edit_badge")
    def page():
        ui.add_head_html(f"<style>{ui_settings.SHARED_CSS}</style>")
        refs.update(segment_editor.build(state, _deps(), on_confirm=lambda: None))

    await user.open("/edit_badge")
    badge = refs["status_badge"]
    assert "CONFIRMED" in badge.text  # segment 0 is "done" in fixture

    state.set_active(1)
    await asyncio.sleep(0.1)
    assert "DRAFTING" in badge.text, "badge should flip to DRAFTING on pending segment"

    state.mark_done(1)
    await asyncio.sleep(0.1)
    assert "CONFIRMED" in badge.text, "badge should flip to CONFIRMED on mark_done"


# ---------------------------------------------------------------------------
# Suggestion engine (Python-side)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compute_suggestions_dedups_and_orders():
    """Note: kept as a smoke test of the compute path. This is the older
    compute_suggestions helper if still present; otherwise skip."""
    fn = getattr(predictions, "compute_suggestions", None)
    if fn is None:
        pytest.skip("compute_suggestions removed in current revision")
    out = await fn(
        source_text="Hello world.",
        src="en", tgt="sl",
        tm=StubTM(fuzzy=[{"source": "Hello.", "target": "Pozdrav.", "score": 95}]),
        glossary=StubGlossary(hits=[{"source_term": "world", "target_term": "svet"}]),
        kg=StubKG(),
    )
    targets = [item["text"] for item in out]
    assert "svet" in targets
    assert "Pozdrav." in targets


# ===========================================================================
# TDD: New feature tests (written first, implemented after)
# ===========================================================================

# ---------------------------------------------------------------------------
# TDD: _kg_query bilingual shape + filtering (pure-function tests)
# ---------------------------------------------------------------------------

def test_kg_query_prefers_humanities_phrases():
    """2-grams and 3-grams must rank above 1-gram hits when both match."""
    from ui.intel_panel import _kg_query
    kg = _build_humanities_kg()
    hits = _kg_query(
        "These imagined futures and intersectionality reshape feminist epistemology.",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    src_terms = [h["src_term"] for h in hits]
    # Bigrams must come before any singleton
    bigram_idx = [i for i, t in enumerate(src_terms) if " " in t]
    singleton_idx = [i for i, t in enumerate(src_terms) if " " not in t]
    if bigram_idx and singleton_idx:
        assert max(bigram_idx) < min(singleton_idx), (
            f"Bigrams must rank before singletons; got order: {src_terms}"
        )
    # At least one bigram must survive
    assert any(" " in t for t in src_terms), f"No bigrams found: {src_terms}"


def test_kg_query_filters_noise_unigrams_in_humanities_context():
    """Noise unigrams like 'common' and 'the' must never appear in hits."""
    from ui.intel_panel import _kg_query
    kg = _build_humanities_kg()
    hits = _kg_query(
        "The common imagined futures of intersectionality",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    src_terms = [h["src_term"] for h in hits]
    assert "common" not in src_terms, f"'common' should be filtered: {src_terms}"
    assert "the" not in src_terms, f"'the' should be filtered: {src_terms}"


def test_kg_hit_is_bilingual():
    """Each _kg_query hit must contain src_term, tgt_term, src_lang, tgt_lang,
    and verified keys — the bilingual shape from the spec. Every hit is
    guaranteed to have a tgt_term (no translation = no hit)."""
    from ui.intel_panel import _kg_query
    kg = _build_humanities_kg()
    hits = _kg_query(
        "These imagined futures reshape intersectionality.",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    assert len(hits) >= 1, "Expected at least one hit"
    h = hits[0]
    assert "src_term" in h, f"Missing src_term in hit: {list(h.keys())}"
    assert "tgt_term" in h, f"Missing tgt_term in hit: {list(h.keys())}"
    assert h["tgt_term"] is not None, "tgt_term must never be None"
    assert "src_lang" in h, f"Missing src_lang in hit: {list(h.keys())}"
    assert "tgt_lang" in h, f"Missing tgt_lang in hit: {list(h.keys())}"
    assert "verified" in h, f"Missing verified in hit: {list(h.keys())}"
    assert h["src_lang"] == "en"
    assert h["tgt_lang"] == "sl"


def test_kg_query_rejects_hits_without_translations():
    """Terms without qualifying translations must never appear as hits —
    not for 1-grams, 2-grams, or 3-grams. Showing 'source →' with no
    target is worse than showing nothing."""
    from ui.intel_panel import _kg_query

    kg = make_kg()
    # 3-gram with NO translations (e.g. a book title or person name)
    seed_term(kg, "en", "lauren berlant", frequency=30)
    # 2-gram with NO translations
    seed_term(kg, "en", "social order", frequency=80)
    # 1-gram with NO translations
    seed_term(kg, "en", "common", frequency=999)
    # 2-gram WITH translations — this one must survive
    seed_term(kg, "en", "imagined futures", frequency=42)
    _wire_translations(kg, "term:en:imagined futures", [
        {"term": "imaginirane prihodnosti", "confidence": 0.92,
         "verified": True, "lineage": "manual"},
    ])

    hits = _kg_query(
        "Lauren Berlant imagined futures in the social order common",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    src_terms = [h["src_term"] for h in hits]
    assert "lauren berlant" not in src_terms, (
        f"3-gram without translations must be filtered: {src_terms}"
    )
    assert "social order" not in src_terms, (
        f"2-gram without translations must be filtered: {src_terms}"
    )
    assert "common" not in src_terms, (
        f"1-gram without translations must be filtered: {src_terms}"
    )
    # The one term WITH translations must survive
    assert "imagined futures" in src_terms, (
        f"2-gram with translations must survive: {src_terms}"
    )
    # Every hit must have a non-None tgt_term
    for h in hits:
        assert h["tgt_term"] is not None, (
            f"Hit {h['src_term']!r} has tgt_term=None — should have been filtered"
        )


def test_kg_siblings_target_language_first():
    """Sibling terms via concept must be sorted so target-lang (sl) appears
    before source-lang (en)."""
    import networkx as nx
    from ui.intel_panel import _related_via_concept

    G = nx.DiGraph()
    G.add_node("term:en:test term", type="term", term="test term",
               lang="en", frequency=10)
    G.add_node("concept:test_concept", type="concept", label="test concept")
    G.add_edge("term:en:test term", "concept:test_concept",
               relation="instantiates_concept")
    # SL sibling (should come first when preferred_lang=sl)
    G.add_node("term:sl:testni izraz", type="term", term="testni izraz",
               lang="sl", frequency=5)
    G.add_edge("term:sl:testni izraz", "concept:test_concept",
               relation="instantiates_concept")
    # Another EN sibling (should come after)
    G.add_node("term:en:test phrase", type="term", term="test phrase",
               lang="en", frequency=20)
    G.add_edge("term:en:test phrase", "concept:test_concept",
               relation="instantiates_concept")

    siblings = _related_via_concept(G, "term:en:test term",
                                    preferred_lang="sl", max_siblings=6)
    langs = [s["lang"] for s in siblings]
    sl_idx = [i for i, l in enumerate(langs) if l == "sl"]
    en_idx = [i for i, l in enumerate(langs) if l == "en"]
    if sl_idx and en_idx:
        assert max(sl_idx) < min(en_idx), (
            f"Target-lang siblings must come first; got order: {langs}"
        )


# ---------------------------------------------------------------------------
# TDD: Intel panel UI — bilingual header row (Phase 3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intel_card_renders_bilingual_header(user):
    """KG hit card shows the bilingual header: src_term, → arrow, tgt_term.
    Uses high-level NiceGUI API only — ui.label('→') makes the arrow visible
    and testable via should_see."""
    kg = _build_humanities_kg()
    state = _build_humanities_state()

    @ui.page("/intel_bilingual")
    def page():
        intel_panel.build(state, _deps(kg=kg))

    await user.open("/intel_bilingual")
    await asyncio.sleep(0.5)
    # Bilingual header: source term + arrow + target term
    await user.should_see("imagined futures")           # src_term
    await user.should_see("→")                          # bilingual arrow
    await user.should_see("imaginirane prihodnosti")    # tgt_term


# ---------------------------------------------------------------------------
# TDD: Intel panel UI — concordance removed (Phase 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intel_concordance_is_not_shown_by_default(user):
    """Concordance section is removed from the default intel surface. Even
    when concordance data exists, the CONCORDANCE header must not appear."""
    state = _make_state()
    tm = StubTM(conc=[{"source": "some concordance line", "target": "nek tekst"}])

    @ui.page("/intel_no_conc")
    def page():
        intel_panel.build(state, _deps(tm=tm))

    await user.open("/intel_no_conc")
    await asyncio.sleep(0.5)
    # Concordance data exists but must not be rendered
    await user.should_not_see("CONCORDANCE")


# ---------------------------------------------------------------------------
# TDD: Intel panel UI — TM 95% threshold (Phase 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tm_threshold_95_excludes_low_matches(user):
    """TM section only shows near-exact matches (≥95%). A 90% match must
    result in the 'no matches' empty state."""
    state = _make_state()
    tm = ThresholdRespectingTM(
        fuzzy=[{"source": "Hello world.", "target": "Pozdrav.", "score": 90}],
    )

    @ui.page("/intel_tm_threshold")
    def page():
        intel_panel.build(state, _deps(tm=tm))

    await user.open("/intel_tm_threshold")
    await asyncio.sleep(0.5)
    await user.should_see("No near-exact matches")


# ---------------------------------------------------------------------------
# TDD: Intel panel UI — TM source/target truncated (Phase 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tm_cards_use_truncated_text(user):
    """TM card source/target lines are truncated to ~80 chars to avoid
    viewport blowout on long segments."""
    state = _make_state()
    long_src = "word " * 40  # 200 chars
    long_tgt = "beseda " * 40
    tm = StubTM(fuzzy=[
        {"source": long_src, "target": long_tgt, "score": 97},
    ])

    @ui.page("/intel_tm_trunc")
    def page():
        intel_panel.build(state, _deps(tm=tm))

    await user.open("/intel_tm_trunc")
    await asyncio.sleep(0.5)
    # The card must render (no crash). The truncation is visible via
    # the ellipsis character in the truncated text.
    await user.should_see("NEAR-EXACT MATCHES")
    await user.should_see("…")  # ellipsis from _truncate


# ---------------------------------------------------------------------------
# TDD: Intel panel UI — glossary directional chips (Phase 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_glossary_chip_shows_src_to_tgt_directionality(user):
    """Glossary chips must show directional 'src → tgt' labels, not just
    the target term alone."""
    state = _make_state()
    g = StubGlossary(hits=[
        {"source_term": "compulsory able-bodiedness",
         "target_term": "obvezna telesna sposobnost", "note": ""},
    ])

    @ui.page("/intel_glossary_dir")
    def page():
        intel_panel.build(state, _deps(glossary=g))

    await user.open("/intel_glossary_dir")
    await asyncio.sleep(0.5)
    await user.should_see("→")                              # directional arrow
    await user.should_see("obvezna telesna sposobnost")   # target term visible


# ---------------------------------------------------------------------------
# TDD: Intel panel UI — alt translations row (Phase 3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intel_panel_alt_translations_shown(user):
    """When a KG hit has multiple filtered translations, the best appears in
    the bilingual header and alternatives are shown below."""
    kg = make_kg()
    seed_term(kg, "en", "imagined futures", frequency=42)
    _wire_translations(kg, "term:en:imagined futures", [
        {"term": "imaginirane prihodnosti", "confidence": 0.92,
         "verified": True, "lineage": "manual"},
        {"term": "zamišljene prihodnosti", "confidence": 0.78,
         "verified": False, "lineage": "auto"},
    ])

    state = _build_humanities_state()

    @ui.page("/intel_alt")
    def page():
        intel_panel.build(state, _deps(kg=kg))

    await user.open("/intel_alt")
    await asyncio.sleep(0.5)
    # Best translation in the bilingual header
    await user.should_see("imaginirane prihodnosti")
    # Alternative translation below the header
    await user.should_see("zamišljene prihodnosti")


# ---------------------------------------------------------------------------
# TDD: Intel panel UI — sibling chips with target-lang highlight (Phase 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intel_panel_sibling_chips_target_lang_first(user):
    """Sibling term chips are rendered below the bilingual header. Slovenian
    siblings (target-lang) must be visible — they are the most actionable."""
    kg = _build_humanities_kg()
    state = _build_humanities_state()

    @ui.page("/intel_siblings")
    def page():
        intel_panel.build(state, _deps(kg=kg))

    await user.open("/intel_siblings")
    await asyncio.sleep(0.5)
    await user.should_see("imaginarne prihodnosti")      # SL sibling visible in cluster


# ---------------------------------------------------------------------------
# TDD: Predictor parity — push_bundle uses _kg_query (Phase 6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_push_bundle_uses_kg_query_for_candidates(monkeypatch):
    """push_bundle must consume _kg_query output for the candidates array,
    not the old kg.extract_entities API. Same source of truth as the visible
    intel cards."""
    captured: list[str] = []

    def fake_run_javascript(code):
        captured.append(code)

    monkeypatch.setattr("ui.predictions.ui.run_javascript", fake_run_javascript)

    kg = _build_humanities_kg()
    await predictions.push_bundle(
        textarea_id=42,
        source_text="These imagined futures reshape intersectionality.",
        src="en",
        tgt="sl",
        tm=StubTM(),
        glossary=StubGlossary(),
        kg=kg,
    )
    assert captured, "push_bundle never called run_javascript"
    js = captured[0]
    # Extract the JSON bundle from the JS call: setBundle({...}, id)
    start = js.index("setBundle(") + len("setBundle(")
    # Find the matching closing paren — the bundle is the first JSON arg
    depth = 0
    end = start
    for i, ch in enumerate(js[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    bundle = json.loads(js[start:end])
    # The bundle must contain KG-sourced candidates from _kg_query
    candidates = bundle.get("candidates", [])
    # At minimum, the verified translation must appear in candidates
    assert any("imaginirane" in c for c in candidates), (
        f"KG translation missing from candidates: {candidates}"
    )
