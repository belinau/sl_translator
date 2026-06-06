"""Phase 1B (TDD red) — language-neutral payload writes in smol builders.

Targets the blueprint §7 changes to ``_build_cited_work``: the legacy
``title_en`` / ``title_sl`` aliases must only be written when BOTH ``en``
AND ``sl`` appear in the pair. For an HR-SL pair the legacy aliases are
omitted; the canonical ``title_orig`` / ``title_translation`` +
``orig_lang`` / ``translation_lang`` carry the data.

Current behaviour (audit §3.3, smol_extractor.py:529-530):

    "title_en": title_orig if orig_lang == LANG_EN else (
        title_translation if translation_lang == LANG_EN else None
    ),
    "title_sl": title_orig if orig_lang == LANG_SL else (
        title_translation if translation_lang == LANG_SL else None
    ),

For HR-SL: ``title_en`` falls to ``None`` (orig=hr, trans=sl), then gets
dropped by the ``payload = {k: v for k, v in payload.items() if v is not None}``
filter — so the *key* is in fact absent from the final payload today.
That's a happy accident: today's test would PASS for HR-SL. We need a
test that discriminates current vs blueprint behaviour clearly.

Discriminator: ``title_sl`` for HR-SL today evaluates to
``title_translation`` (because ``translation_lang == LANG_SL``), so the
payload today INCLUDES ``title_sl="Slovenski prijevod"``. After Phase 1B
the conditional gates the entire alias-write block on both EN AND SL
being present in the pair, so ``title_sl`` is omitted for HR-SL.

The brief asserts: for HR-SL the legacy aliases must NOT appear in the
payload. That's the red test.
"""

from __future__ import annotations

from translate_core.entity_extraction.smol_extractor import _build_cited_work


# ---------------------------------------------------------------------------
# A. HR-SL pair omits legacy title_en / title_sl
# ---------------------------------------------------------------------------


def test_build_cited_work_hr_sl_omits_legacy_aliases():
    """For an HR-source / SL-target citation, ``_build_cited_work`` must NOT
    write the legacy ``title_en`` / ``title_sl`` aliases — the pair does not
    include English, so the SL/EN-shaped legacy fields have no canonical
    referent.

    Canonical ontology fields (per §2.4.2) MUST still be populated:
        title_orig, title_translation, orig_lang, translation_lang.

    Today's implementation writes ``title_sl="Slovenski prevod"`` because
    ``translation_lang == "sl"`` matches one half of the legacy condition.
    That's the discriminator.
    """
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

    assert record is not None, "_build_cited_work must produce a record"
    payload = record["payload"]

    # Canonical fields populated.
    assert payload["title_orig"] == "Izvorni naslov"
    assert payload["title_translation"] == "Slovenski prevod"
    assert payload["orig_lang"] == "hr"
    assert payload["translation_lang"] == "sl"

    # Legacy aliases MUST NOT appear in an HR-SL payload (no English in
    # the pair → no canonical referent for the SL/EN-shaped legacy fields).
    assert "title_en" not in payload, (
        f"title_en must be omitted for HR-SL pair (no EN in pair); got "
        f"payload keys {list(payload)}"
    )
    assert "title_sl" not in payload, (
        f"title_sl must be omitted for HR-SL pair; today's builder writes "
        f"title_sl=title_translation because translation_lang=='sl' "
        f"matches half the legacy condition. Got payload keys {list(payload)}"
    )
