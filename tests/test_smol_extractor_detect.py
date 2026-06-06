"""Phase 1B (TDD red) — language-pair detection in smol_extractor.

Targets the blueprint §6 rewrite of ``_detect_source_lang`` and the
None-guard in ``format_extract_prompt``. The current implementation:

  - returns ``(LANG_EN, LANG_SL)`` for ANY origin without ``en-sl`` /
    ``sl-en`` substring — so ``big-HR-SL.tmx`` is misdetected as EN-SL
    (audit §3.3 / blueprint §6).
  - crashes-or-lies when the detector returns ``None`` because
    ``format_extract_prompt`` calls ``.upper()`` on the result
    unconditionally (blueprint Risk 3).

After Phase 1B:

  - ``_detect_source_lang`` recognises any ``[a-z]{2}-[a-z]{2}`` pair
    flanked by non-letter boundaries and returns ``(None, None)`` when
    no pair is recognised.
  - ``format_extract_prompt`` substitutes ``"??"`` for unknown language
    labels and never raises.

Every test in this module is designed to FAIL against the current code.
"""

from __future__ import annotations

import pytest

from translate_core.entity_extraction.smol_extractor import (
    _detect_source_lang,
    format_extract_prompt,
)


# ---------------------------------------------------------------------------
# A. Pair-detection: positive cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("big-HR-SL.tmx", ("hr", "sl")),
        ("2022-SL-EN.tmx", ("sl", "en")),
        ("en-sl.tmx", ("en", "sl")),
        ("DE-FR.tmx", ("de", "fr")),
        ("mglc-EN-SL.tmx", ("en", "sl")),
    ],
)
def test_detect_recognises_pair_in_filename(origin, expected):
    """Filenames embedding ``[A-Z]{2}-[A-Z]{2}`` (any case, with non-letter
    boundaries) must return the lowercase pair. Today's implementation only
    recognises ``en-sl`` / ``sl-en`` substrings; ``big-HR-SL.tmx`` and
    ``DE-FR.tmx`` fall through to the default ``(LANG_EN, LANG_SL)``.
    """
    assert _detect_source_lang(origin) == expected


# ---------------------------------------------------------------------------
# B. Pair-detection: no-match cases must return (None, None)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    ["foo.tmx", "working.tmx", "corpus.tmx"],
)
def test_detect_returns_none_on_no_match(origin):
    """A filename with no embedded ISO-639-1 pair must return ``(None, None)``
    so the caller can route the segment to review. Today's implementation
    defaults to ``("en", "sl")`` and silently mislabels the segment.
    """
    assert _detect_source_lang(origin) == (None, None), (
        f"_detect_source_lang({origin!r}) must return (None, None) when no "
        f"pair is embedded — today it defaults to ('en', 'sl') (audit §3.3)"
    )


# ---------------------------------------------------------------------------
# C. format_extract_prompt handles undetermined langs gracefully
# ---------------------------------------------------------------------------


def test_format_extract_prompt_handles_none_langs():
    """When the detector returns ``(None, None)`` (no recognisable pair in
    the origin filename), ``format_extract_prompt`` must NOT crash on the
    ``.upper()`` call (blueprint Risk 3) and the rendered prompt must
    include a ``"??"`` placeholder so the LLM knows the language is
    undetermined.

    Against the current implementation: today the detector defaults to
    ``("en", "sl")`` for ``foo.tmx``, so the prompt silently reads
    ``Segment SOURCE (EN): ...`` — the "??" assertion fails. After Phase 1B
    the detector returns ``(None, None)`` and the prompt formatter
    substitutes ``"??"`` per blueprint §6.
    """
    prompt = format_extract_prompt(
        src="some source segment",
        tgt="translated target segment",
        origin="foo.tmx",
    )

    assert isinstance(prompt, str)
    assert "??" in prompt, (
        "format_extract_prompt must mark undetermined languages with '??' "
        "in the rendered prompt when _detect_source_lang returns (None, "
        "None); current output uses the defaulted 'EN'/'SL' labels instead"
    )
