"""Confirm-pipeline tests — TM upsert + KG NLP-based promote_pair.

Covers the two bugs the user reported:
  1. save_pair_to_tm wrote N duplicate <tu> blocks for N confirms of
     the same segment (no upsert).
  2. KnowledgeGraph.promote_pair stuffed the entire confirmed segment
     into the KG as a single fake concept + sentence-as-term.

The KG tests stub the Spacy/Stanza pipelines so the assertions can
focus on graph-mutation behaviour (term upsert with frequency bump,
mapping promotion with verified=True, no sentence-as-concept).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pytest

from _kg_helpers import make_kg, seed_mapping, seed_pair, seed_term


# ---------------------------------------------------------------------------
# TM upsert
# ---------------------------------------------------------------------------

@pytest.fixture
def tm_dir(tmp_path, monkeypatch):
    """Point config.TM_DIR at a temp dir and replace the in-memory TM with
    a SimpleNamespace exposing the same `entries` list main.py mutates."""
    import config as _config
    import main as _main

    monkeypatch.setattr(_config, "TM_DIR", tmp_path)
    monkeypatch.setattr(_main.config, "TM_DIR", tmp_path)
    monkeypatch.setattr(_main, "tm", SimpleNamespace(entries=[]))
    return tmp_path


def _count_tus(tm_path: Path) -> int:
    return len(re.findall(r"<tu>", tm_path.read_text(encoding="utf-8")))


def _targets(tm_path: Path) -> list[str]:
    """Return target seg contents in document order."""
    raw = tm_path.read_text(encoding="utf-8")
    return re.findall(
        r'<tuv xml:lang="sl"><seg>([^<]*)</seg></tuv>', raw,
    )


def test_save_pair_to_tm_appends_new_pair(tm_dir):
    from main import save_pair_to_tm, tm

    save_pair_to_tm("Hello world.", "Pozdrav svet.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert tm_path.exists()
    assert _count_tus(tm_path) == 1
    assert _targets(tm_path) == ["Pozdrav svet."]
    assert tm.entries == [{
        "source": "Hello world.",
        "target": "Pozdrav svet.",
        "origin": "working.tmx",
        "source_lang": "en",
        "target_lang": "sl",
    }]


def test_save_pair_to_tm_is_idempotent_on_identical_pair(tm_dir):
    """The reported bug: confirming the same segment five times wrote
    five duplicate TUs. The fix must collapse them to one."""
    from main import save_pair_to_tm, tm

    for _ in range(5):
        save_pair_to_tm("Hello world.", "Pozdrav svet.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert _count_tus(tm_path) == 1, "duplicate TUs leaked through"
    assert len(tm.entries) == 1


def test_save_pair_to_tm_rewrites_target_on_change(tm_dir):
    """Confirming the same source with a corrected target rewrites the
    existing TU's target rather than appending a stale copy."""
    from main import save_pair_to_tm, tm

    save_pair_to_tm("Hello world.", "Pozdrav.", "en->sl")
    save_pair_to_tm("Hello world.", "Pozdravljen svet.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert _count_tus(tm_path) == 1
    assert _targets(tm_path) == ["Pozdravljen svet."]
    assert len(tm.entries) == 1
    assert tm.entries[0]["target"] == "Pozdravljen svet."


def test_save_pair_to_tm_appends_distinct_sources_independently(tm_dir):
    from main import save_pair_to_tm

    save_pair_to_tm("Hello.", "Pozdrav.", "en->sl")
    save_pair_to_tm("Goodbye.", "Adijo.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert _count_tus(tm_path) == 2
    assert sorted(_targets(tm_path)) == ["Adijo.", "Pozdrav."]


def test_save_pair_to_tm_handles_xml_special_chars(tm_dir):
    """Sources containing &, <, > etc. must round-trip through the
    matcher so a re-confirm doesn't append a duplicate."""
    from main import save_pair_to_tm

    src = 'Smith & Jones <on the right>'
    save_pair_to_tm(src, "Smith in Jones.", "en->sl")
    save_pair_to_tm(src, "Smith in Jones.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert _count_tus(tm_path) == 1


# ---------------------------------------------------------------------------
# KG promote_pair — schema-respecting NLP ingestion
# ---------------------------------------------------------------------------

class _StubEnToken:
    def __init__(self, text, lemma=None, pos="NOUN", is_stop=False, is_punct=False):
        self.text = text
        self.lemma_ = (lemma or text).lower()
        self.pos_ = pos
        self.is_stop = is_stop
        self.is_punct = is_punct


class _StubEnChunk:
    def __init__(self, tokens):
        self._tokens = tokens

    def __iter__(self):
        return iter(self._tokens)

    def __getitem__(self, key):
        return self._tokens[key]


class _StubEnDoc:
    def __init__(self, tokens, chunks):
        self._tokens = tokens
        self.noun_chunks = chunks

    def __iter__(self):
        return iter(self._tokens)


class _StubEnNlp:
    """Spacy stand-in that returns deterministic noun chunks and tokens.
    Mapping from raw source text to (tokens, chunks) is supplied by the
    test via the `programs` dict."""

    def __init__(self, programs: dict):
        self.programs = programs

    def __call__(self, text: str):
        tokens, chunks = self.programs.get(text, ([], []))
        return _StubEnDoc(tokens, chunks)


class _StubSlNlp:
    """Stanza/Classla stand-in. Bypasses doc parsing by hooking the lower-
    level _get_dependency_phrases via monkeypatch in each test."""

    def __call__(self, text: str):
        return SimpleNamespace(_text=text)


def _en_program(text, *, chunks, content_tokens=()):
    """Build (tokens, chunks) for the EN stub from explicit chunk lists.

    chunks: iterable of token-text lists, e.g. [["vita", "activa"]]
    content_tokens: extra non-stop content lemmas to surface in the
                    bigram/trigram pass
    """
    tokens = []
    chunk_objs = []
    for words in chunks:
        ctoks = [_StubEnToken(w, pos="NOUN") for w in words]
        chunk_objs.append(_StubEnChunk(ctoks))
        tokens.extend(ctoks)
    for w in content_tokens:
        tokens.append(_StubEnToken(w, pos="NOUN"))
    return tokens, chunk_objs


@pytest.fixture
def nlp_kg(monkeypatch):
    """A KG with the Spacy/Stanza hooks wired to deterministic stubs."""
    kg = make_kg()
    en_programs: dict = {}
    sl_programs: dict = {}

    en = _StubEnNlp(en_programs)
    sl = _StubSlNlp()
    monkeypatch.setattr(kg, "nlp_en", en, raising=False)
    monkeypatch.setattr(kg, "nlp_sl", sl, raising=False)

    # Ensure the HAS_SPACY guard in promote_pair sees True.
    from translate_core import knowledge_graph as _kgmod
    monkeypatch.setattr(_kgmod, "HAS_SPACY", True, raising=False)

    # _get_dependency_phrases is method; intercept it to drive SL output.
    def _phrases(self, doc) -> Iterable[tuple[str, str]]:
        return list(sl_programs.get(getattr(doc, "_text", ""), []))

    monkeypatch.setattr(
        type(kg), "_get_dependency_phrases", _phrases, raising=False,
    )

    # _protect_gender_tokens / _restore_gender_tokens are passthroughs in
    # the absence of underscored forms — let them run as-is.
    return kg, en_programs, sl_programs


def test_promote_pair_does_not_create_sentence_as_concept(nlp_kg):
    """Regression: confirm must NOT create a concept whose id/label is
    the entire source sentence — the old bug."""
    kg, en_programs, sl_programs = nlp_kg
    src = "Arendt traces vita activa."
    tgt = "Arendt sledi vita activi."
    en_programs[src] = _en_program(src, chunks=[["vita", "activa"]])
    sl_programs["Arendt sledi vita activi."] = [("vita activa", "vita activi")]

    kg.promote_pair(src, tgt, "en", "sl", verified=True)

    concept_ids = [
        nid for nid, nd in kg.G.nodes(data=True) if nd.get("type") == "concept"
    ]
    assert not any(src.replace(" ", "_") in cid for cid in concept_ids), (
        f"sentence-as-concept node leaked into the KG: {concept_ids}"
    )
    # The expected concept is the term-level one.
    assert "concept:vita_activa" in concept_ids


def test_promote_pair_upserts_existing_mapping_and_marks_verified(nlp_kg):
    kg, en_programs, sl_programs = nlp_kg
    # Pre-seed an unverified low-confidence pair (Dice-seeded style).
    sid, tid = seed_pair(
        kg, "en", "vita activa", "sl", "vita activa",
        confidence=0.22, verified=False, lineage="general",
    )

    src = "Arendt traces vita activa."
    tgt = "Arendt sledi vita activa."
    en_programs[src] = _en_program(src, chunks=[["vita", "activa"]])
    sl_programs[tgt] = [("vita activa", "vita activa")]

    delta = kg.promote_pair(src, tgt, "en", "sl", verified=True)

    # Mapping node carries verified=True now.
    map_id = f"map:{sid}>>{tid}:general"
    assert kg.G.has_node(map_id)
    assert kg.G.nodes[map_id]["verified"] is True
    # Confidence was bumped (existing 0.22 + the upsert's +0.05 floor).
    assert kg.G.nodes[map_id]["confidence"] >= 0.27
    assert map_id in delta["verified"]
    assert not delta["created"], "no new mapping should be created here"


def test_promote_pair_creates_new_mapping_only_when_unambiguous(nlp_kg):
    """Single EN term + single SL term + no prior mapping → create.
    Otherwise (larger segments with cartesian risk) → skip new-pair
    creation. Existing mappings still get verified."""
    kg, en_programs, sl_programs = nlp_kg
    # No prior mapping. Unambiguous one-term-each segment.
    src = "Performativity matters."
    tgt = "Performativnost je pomembna."
    en_programs[src] = _en_program(src, chunks=[["performativity"]])
    sl_programs[tgt] = [("performativnost", "performativnost")]

    delta = kg.promote_pair(src, tgt, "en", "sl", verified=True)

    assert delta["created"], "unambiguous pair should produce a new mapping"
    sid = "term:en:performativity"
    tid = "term:sl:performativnost"
    map_id = f"map:{sid}>>{tid}:manual"
    assert kg.G.has_node(map_id)
    assert kg.G.nodes[map_id]["verified"] is True
    assert kg.G.nodes[map_id]["lineage"] == "manual"

    # Cartesian segment: 2 EN + 2 SL, no prior mappings — none should be
    # invented. Existing ones are still strengthened (covered separately).
    kg2 = make_kg()
    en_programs2: dict = {}
    sl_programs2: dict = {}
    # Re-wire stubs against kg2.
    kg2.nlp_en = _StubEnNlp(en_programs2)
    kg2.nlp_sl = _StubSlNlp()

    def _phrases2(self, doc):
        return list(sl_programs2.get(getattr(doc, "_text", ""), []))

    import translate_core.knowledge_graph as _kgmod
    _kgmod.KnowledgeGraph._get_dependency_phrases = _phrases2

    src2 = "Capacity and trace shape biopolitics."
    tgt2 = "Zmožnost in sledi oblikujeta biopolitiko."
    en_programs2[src2] = _en_program(src2, chunks=[["capacity"], ["trace"]])
    sl_programs2[tgt2] = [("zmožnost", "zmožnost"), ("sledi", "sledi")]

    delta2 = kg2.promote_pair(src2, tgt2, "en", "sl", verified=True)
    assert not delta2["created"], (
        "cartesian pairs from a single segment must not become new "
        f"mappings: {delta2['created']}"
    )


def test_promote_pair_bumps_term_frequency(nlp_kg):
    """Re-confirming a segment should bump the frequency of its terms
    via add_term_node's existing upsert path."""
    kg, en_programs, sl_programs = nlp_kg
    src = "Performativity matters."
    tgt = "Performativnost je pomembna."
    en_programs[src] = _en_program(src, chunks=[["performativity"]])
    sl_programs[tgt] = [("performativnost", "performativnost")]

    kg.promote_pair(src, tgt, "en", "sl", verified=True)
    freq_after_1 = kg.G.nodes["term:en:performativity"]["frequency"]
    kg.promote_pair(src, tgt, "en", "sl", verified=True)
    freq_after_2 = kg.G.nodes["term:en:performativity"]["frequency"]
    assert freq_after_2 > freq_after_1, "term frequency must bump on re-confirm"


def test_promote_pair_links_concept_for_promoted_pair(nlp_kg):
    """When a pair is promoted (existing or new), both terms must
    instantiate the same concept. The concept label is the EN term —
    never the sentence."""
    kg, en_programs, sl_programs = nlp_kg
    src = "Performativity matters."
    tgt = "Performativnost je pomembna."
    en_programs[src] = _en_program(src, chunks=[["performativity"]])
    sl_programs[tgt] = [("performativnost", "performativnost")]

    kg.promote_pair(src, tgt, "en", "sl", verified=True)
    cid = "concept:performativity"
    assert kg.G.has_node(cid)
    assert kg.G.nodes[cid]["label"] == "performativity"
    assert kg.G.has_edge("term:en:performativity", cid)
    assert kg.G.has_edge("term:sl:performativnost", cid)


def test_promote_pair_short_circuits_when_nlp_missing():
    """Without Spacy/Stanza available promote_pair must be a safe no-op,
    not crash the confirm flow."""
    kg = make_kg()  # conftest leaves nlp_en/nlp_sl as None
    delta = kg.promote_pair("Hello.", "Pozdrav.", "en", "sl", verified=True)
    assert delta == {"src_terms": [], "tgt_terms": [], "verified": [], "created": []}
    # No nodes created.
    assert kg.G.number_of_nodes() == 0
