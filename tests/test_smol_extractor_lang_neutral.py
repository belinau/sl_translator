"""Phase 4 — smol builders must emit only the neutral payload shape.

Phase 1B kept a transitional `# SUNSET: Phase 11` alias-write block in
`_build_cited_work`, `_build_artwork`, and `_build_performance` that
re-emitted `title_en` / `title_sl` whenever EN or SL appeared in the pair.
Phase 4 (blueprint §4.1) deletes those blocks: legacy aliases are now
banned in builder output for ANY language pair, including EN/SL.

Phase 4 also remaps the `slovenian_edition` sub-dict (still emitted by the
prompt; rename is Phase 11 SUNSET) to `translation_edition` with a
`language` field matching `translation_lang` (blueprint §4.2). The legacy
key `slovenian_edition` must not appear in the payload.

These tests INVERT the Phase 1B HR-SL assertions: today's expectation is
that legacy keys are absent for every pair.
"""

from __future__ import annotations

from translate_core.entity_extraction.smol_extractor import (
    _build_artwork,
    _build_cited_work,
    _build_performance,
)


# ---------------------------------------------------------------------------
# A. HR-SL pair: legacy aliases absent (regression coverage from Phase 1B)
# ---------------------------------------------------------------------------


def test_build_cited_work_hr_sl_omits_legacy_aliases():
    """For an HR/SL pair (no English in the pair) the legacy `title_en` /
    `title_sl` aliases MUST NOT appear in the payload."""
    ent = {
        "title_orig": "Izvorni naslov",
        "title_translation": "Slovenski prevod",
        "orig_lang": "hr",
        "translation_lang": "sl",
        "author": "Some Author",
    }
    record = _build_cited_work(
        ent,
        origin="big-HR-SL.tmx",
        seg_idx=0,
        container_work_id="container-id",
        src_lang="hr",
        tgt_lang="sl",
    )

    assert record is not None
    payload = record["payload"]

    assert payload["title_orig"] == "Izvorni naslov"
    assert payload["title_translation"] == "Slovenski prevod"
    assert payload["orig_lang"] == "hr"
    assert payload["translation_lang"] == "sl"
    assert "title_en" not in payload
    assert "title_sl" not in payload


# ---------------------------------------------------------------------------
# B. EN-SL pair: legacy aliases STILL absent (Phase 4 inversion)
# ---------------------------------------------------------------------------


def test_cited_work_no_legacy_keys_en_sl():
    """For an EN/SL pair the legacy aliases were emitted under Phase 1B's
    sunset block. Phase 4 deletes that block — they MUST be absent now too."""
    ent = {
        "title_orig": "Capitalist Realism",
        "title_translation": "Kapitalisticni realizem",
        "orig_lang": "en",
        "translation_lang": "sl",
        "author": "Mark Fisher",
    }
    record = _build_cited_work(
        ent,
        origin="big-EN-SL.tmx",
        seg_idx=0,
        container_work_id="container-id",
        src_lang="en",
        tgt_lang="sl",
    )

    assert record is not None
    payload = record["payload"]

    assert "title_en" not in payload, (
        f"title_en must be absent post-Phase-4 (SUNSET block deleted); "
        f"got payload keys {list(payload)}"
    )
    assert "title_sl" not in payload, (
        f"title_sl must be absent post-Phase-4 (SUNSET block deleted); "
        f"got payload keys {list(payload)}"
    )


def test_artwork_no_legacy_keys_en_sl():
    ent = {
        "title_orig": "The Garden",
        "title_translation": "Vrt",
        "orig_lang": "en",
        "translation_lang": "sl",
        "artist": "Jane Doe",
        "medium": "oil on canvas",
    }
    record = _build_artwork(
        ent,
        origin="big-EN-SL.tmx",
        seg_idx=0,
        container_work_id="catalog-id",
        src_lang="en",
        tgt_lang="sl",
    )
    assert record is not None
    payload = record["payload"]
    assert "title_en" not in payload
    assert "title_sl" not in payload


def test_performance_no_legacy_keys_en_sl():
    ent = {
        "title_orig": "Hamlet Machine",
        "title_translation": "Stroj Hamlet",
        "orig_lang": "en",
        "translation_lang": "sl",
        "creators": [{"name": "A Director", "role": "director"}],
        "performers": [{"name": "A Dancer", "role": "dancer"}],
        "performance_kind": "dance",
    }
    record = _build_performance(
        ent,
        origin="big-EN-SL.tmx",
        seg_idx=0,
        container_work_id="programme-id",
        src_lang="en",
        tgt_lang="sl",
    )
    assert record is not None
    payload = record["payload"]
    assert "title_en" not in payload
    assert "title_sl" not in payload


# ---------------------------------------------------------------------------
# C. `slovenian_edition` key absent in payload; `translation_edition` carries data
# ---------------------------------------------------------------------------


def test_cited_work_no_slovenian_edition_key():
    """The model emits `slovenian_edition` (Phase 11 prompt rename SUNSET);
    the builder must remap it to `translation_edition` and DROP the legacy key."""
    ent = {
        "title_orig": "Capitalist Realism",
        "title_translation": "Kapitalisticni realizem",
        "orig_lang": "en",
        "translation_lang": "sl",
        "author": "Mark Fisher",
        "slovenian_edition": {
            "publisher": "Maska",
            "city": "Ljubljana",
            "year": 2010,
            "translator": "Test Translator",
        },
    }
    record = _build_cited_work(
        ent,
        origin="big-EN-SL.tmx",
        seg_idx=0,
        container_work_id="container-id",
        src_lang="en",
        tgt_lang="sl",
    )

    assert record is not None
    payload = record["payload"]
    assert "slovenian_edition" not in payload
    assert "translation_edition" in payload
    te = payload["translation_edition"]
    assert te["publisher"] == "Maska"
    assert te["city"] == "Ljubljana"
    assert te["year"] == 2010
    assert te["translator"] == "Test Translator"


def test_translation_edition_has_language_field():
    """`translation_edition.language` must match `translation_lang`."""
    ent = {
        "title_orig": "Capitalist Realism",
        "title_translation": "Kapitalisticni realizem",
        "orig_lang": "en",
        "translation_lang": "sl",
        "author": "Mark Fisher",
        "slovenian_edition": {"publisher": "Maska", "translator": "Test Translator"},
    }
    record = _build_cited_work(
        ent,
        origin="big-EN-SL.tmx",
        seg_idx=0,
        container_work_id="container-id",
        src_lang="en",
        tgt_lang="sl",
    )

    assert record is not None
    payload = record["payload"]
    assert payload["translation_edition"]["language"] == payload["translation_lang"]
