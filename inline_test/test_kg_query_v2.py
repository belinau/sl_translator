"""TDD tests for the new _kg_query path (extract_entities-based).

These exercise the contract described in the design plan:

1. extract_entities-driven lookup (flashtext maximal-munch)
2. Inflected source surface forms hit through display_form / variants
3. Hit dict carries a `concept` field when the term participates in a concept
4. No confidence floor — low-confidence mappings surface, verified ranks first
5. Quota split: phrases and unigrams each get half of max_hits
6. push_bundle injects concept-sibling target terms into candidates
7. Workspace renders concept-grouped cards + a "TERMS" group for orphans
8. Unigram with no concept still surfaces with a non-empty tgt_term

The whole point of these tests: the production code below `_kg_query` must
match the KG schema (translations live on edges, not on the term node), and
the panel must surface concepts alongside single-word terms.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from nicegui import ui

from _kg_helpers import (
    make_kg,
    seed_concept,
    seed_mapping,
    seed_pair,
    seed_term,
)
from ui import intel_panel, predictions
from ui.state import WorkspaceState


# ---------------------------------------------------------------------------
# Shared stubs (lightweight reflections of the integration-test stubs)
# ---------------------------------------------------------------------------

class StubTM:
    def lookup_fuzzy(self, *a, **kw):
        return []

    def search_concordance(self, *a, **kw):
        return []


class StubGlossary:
    def lookup_terms(self, *a, **kw):
        return []




class StubQA:
    def check_segment(self, *a, **kw):
        return []


def _parse_lang_pair(p):
    src, _, tgt = (p or "en->sl").partition("->")
    return src or "en", tgt or "sl"


def _state(segments):
    ws = {
        "project_id": "t",
        "filename": "f.docx",
        "lang_pair": "en->sl",
        "active_index": 0,
        "segments": segments,
    }
    return WorkspaceState(ws, save_callback=lambda _: None)


def _deps(kg=None):
    return {
        "tm": StubTM(),
        "glossary": StubGlossary(),
        "kg": kg,
        "qa_engine": StubQA(),
        "parse_lang_pair": _parse_lang_pair,
    }


# ===========================================================================
# Test 1 — Inflected source surface form matches via display_form / variants
# ===========================================================================

def test_inflected_source_surface_matches_via_variants():
    """When the source contains an inflected/variant surface form, the
    flashtext index registered by seed_term must catch it and the hit must
    surface with a non-empty tgt_term."""
    from ui.intel_panel import _kg_query

    kg = make_kg()
    src_id = seed_term(
        kg, "en", "imagined future",
        display_form="imagined futures",
        variants=("imagined futurity",),
        frequency=12,
    )
    tgt_id = seed_term(kg, "sl", "imaginirana prihodnost", frequency=7)
    seed_mapping(kg, src_id, tgt_id, confidence=0.4, verified=True)

    # Source uses the variant — not the lemma — and yet must be matched.
    hits = _kg_query(
        "The text discusses imagined futurity in chapter two.",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    assert hits, "variant surface form should match via flashtext index"
    h = hits[0]
    assert h["tgt_term"] == "imaginirana prihodnost"
    assert h["src_lang"] == "en"
    assert h["tgt_lang"] == "sl"


# ===========================================================================
# Test 2 — Multi-word phrase ranks above unigram (already in old contract,
# re-asserted under the new query path)
# ===========================================================================

def test_phrase_outranks_unigram_when_both_match():
    from ui.intel_panel import _kg_query

    kg = make_kg()
    # phrase
    pid, _ = seed_pair(
        kg, "en", "imagined futures", "sl", "imaginirane prihodnosti",
        confidence=0.9, verified=True, src_frequency=20,
    )
    # unigram
    uid, _ = seed_pair(
        kg, "en", "epistemology", "sl", "epistemologija",
        confidence=0.95, verified=True, src_frequency=200,
    )

    hits = _kg_query(
        "These imagined futures reshape epistemology.",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    src_terms = [h["src_term"] for h in hits]
    assert "imagined futures" in src_terms
    assert "epistemology" in src_terms
    # Phrase index should be lower than unigram index in the sorted list.
    assert src_terms.index("imagined futures") < src_terms.index("epistemology")


# ===========================================================================
# Test 3 — Hit carries `concept` (label + domain)
# ===========================================================================

def test_hit_carries_concept_label_and_domain():
    from ui.intel_panel import _kg_query

    kg = make_kg()
    src_id, _ = seed_pair(
        kg, "en", "vita activa", "sl", "delujoče življenje",
        confidence=0.5, verified=True,
    )
    seed_concept(
        kg, "human_condition", "human condition",
        domain="political philosophy",
        terms=(src_id,),
    )

    hits = _kg_query(
        "Arendt traces vita activa across three modes.",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    assert hits, "expected one hit for 'vita activa'"
    h = hits[0]
    assert h.get("concept") is not None, "hit must include concept dict"
    assert h["concept"]["label"] == "human condition"
    assert h["concept"]["domain"] == "political philosophy"


# ===========================================================================
# Test 4 — Low-confidence (≤0.3) mapping still surfaces (no floor)
# ===========================================================================

def test_low_confidence_mapping_surfaces_without_floor():
    from ui.intel_panel import _kg_query

    kg = make_kg()
    # Dice-seeded humanities pair at 0.2 — must NOT be silently dropped.
    seed_pair(
        kg, "en", "performativity", "sl", "performativnost",
        confidence=0.2, verified=False, src_frequency=8,
    )
    hits = _kg_query(
        "Butler discusses performativity in the chapter.",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    assert hits, "low-confidence mapping must survive (no 0.6 floor)"
    assert hits[0]["tgt_term"] == "performativnost"


# ===========================================================================
# Test 5 — Verified outranks higher-confidence-unverified for same source
# ===========================================================================

def test_verified_outranks_higher_confidence_unverified():
    from ui.intel_panel import _kg_query

    kg = make_kg()
    src_id = seed_term(kg, "en", "labour", frequency=50)
    a = seed_term(kg, "sl", "delo")
    b = seed_term(kg, "sl", "rabota")
    # Unverified at 0.95
    seed_mapping(kg, src_id, b, confidence=0.95, verified=False, lineage="auto")
    # Verified at 0.3 — must come first.
    seed_mapping(kg, src_id, a, confidence=0.3, verified=True, lineage="manual")

    hits = _kg_query(
        "The discussion of labour in this chapter.",
        src_lang="en", tgt_lang="sl", kg=kg,
    )
    assert hits, "expected hit for 'labour'"
    assert hits[0]["tgt_term"] == "delo", (
        f"verified mapping must win over unverified 0.95; got {hits[0]}"
    )
    assert hits[0]["verified"] is True


# ===========================================================================
# Test 6 — push_bundle injects concept-sibling target terms into candidates
# ===========================================================================

@pytest.mark.asyncio
async def test_push_bundle_injects_concept_siblings(monkeypatch):
    captured: list[str] = []

    def fake_run_javascript(code):
        captured.append(code)

    monkeypatch.setattr("ui.predictions.ui.run_javascript", fake_run_javascript)

    kg = make_kg()
    src_id, tgt_id = seed_pair(
        kg, "en", "vita activa", "sl", "delujoče življenje",
        confidence=0.6, verified=True,
    )
    sibling_id = seed_term(kg, "sl", "delovno življenje", frequency=4)
    seed_concept(
        kg, "human_condition", "human condition",
        terms=(src_id, tgt_id, sibling_id),
    )

    await predictions.push_bundle(
        textarea_id=11,
        source_text="Arendt discusses vita activa here.",
        src="en", tgt="sl",
        tm=StubTM(),
        glossary=StubGlossary(),
        kg=kg,
    )
    assert captured
    js = captured[0]
    start = js.index("setBundle(") + len("setBundle(")
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
    cands = bundle.get("candidates", [])
    assert "delujoče življenje" in cands, f"primary translation missing: {cands}"
    assert "delovno življenje" in cands, (
        f"concept-sibling target term missing from candidates: {cands}"
    )


# ===========================================================================
# Test 7 — Workspace renders concept-grouped cards
# ===========================================================================

@pytest.mark.asyncio
async def test_workspace_renders_concept_grouped_cards(user):
    kg = make_kg()
    a, _ = seed_pair(
        kg, "en", "vita activa", "sl", "delujoče življenje",
        confidence=0.6, verified=True, src_frequency=10,
    )
    b, _ = seed_pair(
        kg, "en", "labour", "sl", "delo",
        confidence=0.5, verified=True, src_frequency=20,
    )
    seed_concept(kg, "human_condition", "human condition",
                 domain="political philosophy", terms=(a, b))

    state = _state([
        {"id": 0,
         "source": "Arendt's notion of vita activa centers labour.",
         "target": "", "status": "pending"},
    ])

    @ui.page("/concept_group")
    def page():
        intel_panel.build(state, _deps(kg=kg))

    await user.open("/concept_group")
    await asyncio.sleep(0.5)
    # Concept label appears in the panel, rendered as a small uppercase
    # eyebrow consistent with the other section headers ("KNOWLEDGE GRAPH",
    # "TRANSLATION MEMORY", "GLOSSARY").
    await user.should_see("HUMAN CONDITION")
    # Both terms render under that header.
    await user.should_see("vita activa")
    await user.should_see("labour")


# ===========================================================================
# Test 8 — Unigram with no concept still surfaces (in the orphan TERMS group)
# ===========================================================================

@pytest.mark.asyncio
async def test_unigram_without_concept_surfaces_in_terms_group(user):
    kg = make_kg()
    # Unigram pair, NO concept membership.
    seed_pair(
        kg, "en", "epistemology", "sl", "epistemologija",
        confidence=0.4, verified=True, src_frequency=14,
    )
    state = _state([
        {"id": 0,
         "source": "The chapter on epistemology is short.",
         "target": "", "status": "pending"},
    ])

    @ui.page("/orphan_unigram")
    def page():
        intel_panel.build(state, _deps(kg=kg))

    await user.open("/orphan_unigram")
    await asyncio.sleep(0.5)
    # Orphan group header.
    await user.should_see("TERMS")
    # Source and target rendered.
    await user.should_see("epistemology")
    await user.should_see("epistemologija")


# ===========================================================================
# Test 9 — Quota split: phrases and unigrams both bucketed inside max_hits
# ===========================================================================

# ===========================================================================
# Test 10 — Background target-language vocab in the bundle (so the predictor
# can complete any KG-known term by prefix, not only source-aligned terms)
# ===========================================================================

@pytest.mark.asyncio
async def test_push_bundle_includes_target_vocab_pool(monkeypatch):
    """A target term whose source equivalent is NOT in the current segment
    must still surface in the bundle's candidate list so ghost text can
    complete it by prefix. This is the regression that the screenshot
    feedback exposed: typing 'intersek' should land on 'intersekcionalnost'
    even when 'intersectionality' is nowhere in the source paragraph."""
    captured: list[str] = []

    def fake_run_javascript(code):
        captured.append(code)

    monkeypatch.setattr("ui.predictions.ui.run_javascript", fake_run_javascript)

    kg = make_kg()
    # Seed a pair whose source side does NOT appear in the source segment.
    seed_pair(
        kg, "en", "intersectionality", "sl", "intersekcionalnost",
        confidence=0.95, verified=True, src_frequency=35, tgt_frequency=12,
    )
    # And a source-aligned hit so we know the regular pipeline runs.
    seed_pair(
        kg, "en", "capacity", "sl", "zmožnost",
        confidence=0.69, verified=True, src_frequency=20,
    )

    source_text = "The biopolitics of capacity already demarcate the population."
    await predictions.push_bundle(
        textarea_id=99,
        source_text=source_text,
        src="en", tgt="sl",
        tm=StubTM(),
        glossary=StubGlossary(),
        kg=kg,
    )
    assert captured
    js = captured[0]
    start = js.index("setBundle(") + len("setBundle(")
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
    cands = bundle.get("candidates") or []
    # Source-aligned hit is present.
    assert "zmožnost" in cands
    # And the off-segment KG-known term lands in the vocab pool.
    assert "intersekcionalnost" in cands, (
        "background vocab missing — predictor won't complete off-segment "
        f"KG terms. Got {len(cands)} candidates."
    )


# ===========================================================================
# Test 11 — Quota split: phrases and unigrams both bucketed inside max_hits
# ===========================================================================

def test_quota_split_keeps_both_phrases_and_unigrams():
    from ui.intel_panel import _kg_query

    kg = make_kg()
    # Six phrases
    phrases = [
        ("crip theory", "krip teorija"),
        ("queer phenomenology", "queer fenomenologija"),
        ("feminist epistemology", "feministična epistemologija"),
        ("imagined futures", "imaginirane prihodnosti"),
        ("vita activa", "delujoče življenje"),
        ("political theology", "politična teologija"),
    ]
    for s, t in phrases:
        seed_pair(kg, "en", s, "sl", t, confidence=0.7, verified=True, src_frequency=30)
    # Six unigrams
    unigrams = [
        ("epistemology", "epistemologija"),
        ("performativity", "performativnost"),
        ("subjectivity", "subjektivnost"),
        ("ideology", "ideologija"),
        ("biopolitics", "biopolitika"),
        ("hermeneutics", "hermenevtika"),
    ]
    for s, t in unigrams:
        seed_pair(kg, "en", s, "sl", t, confidence=0.7, verified=True, src_frequency=60)

    source_text = (
        "crip theory queer phenomenology feminist epistemology "
        "imagined futures vita activa political theology "
        "epistemology performativity subjectivity ideology "
        "biopolitics hermeneutics"
    )
    hits = _kg_query(
        source_text, src_lang="en", tgt_lang="sl", kg=kg, max_hits=8,
    )
    assert len(hits) == 8, f"expected 8 hits, got {len(hits)}: {[h['src_term'] for h in hits]}"
    phrase_count = sum(1 for h in hits if h["n"] >= 2)
    unigram_count = sum(1 for h in hits if h["n"] == 1)
    assert phrase_count >= 4, f"phrase bucket starved: {phrase_count}"
    assert unigram_count >= 4, f"unigram bucket starved: {unigram_count}"
