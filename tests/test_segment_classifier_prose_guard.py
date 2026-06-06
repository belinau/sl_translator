"""Regression guard for the `_looks_like_bibliography` prose false-positive.

Before the audit fix, a single substring hit against `KNOWN_PUBLISHERS`
(line 47–65 of segment_classifier) flipped any body sentence mentioning a
publisher name to BIBLIOGRAPHY_ENTRY. That sentence was then routed through
the typed-citation pipeline, which packs every Title-Case span into one
citation against a single work — the Grom→Sujets misattribution.

These tests pin down the tightened rule:
- a body sentence with at most one (weak) structural signal is NOT biblio,
- real bibliography entries and footnotes still classify as biblio.
"""

from __future__ import annotations

from translate_core.entity_extraction.segment_classifier import (
    SegmentClass,
    _looks_like_bibliography,
    classify_segments,
)


# --- exact fixtures from the audit / rebuild plan ---------------------------

SUJETS_BODY_PROSE = (
    "Choreographic works ... include choreographies by Sylvain Huc Sujets "
    "(2018) and Sanja Nešković Peršin The Moment Before (2024), as well as "
    "Tomaž Grom's experimental film Don't Think It Will Ever Pass (2023) and "
    "Jefta van Dinther's extraordinary choreo-vocal work Unearth (2022)."
)

REAL_BIBLIO_ENTRY = (
    "Foucault, Michel. 1975. Surveiller et punir. Paris: Gallimard, p. 215."
)

REAL_FOOTNOTE = (
    "1. See Michel Foucault, Discipline and Punish "
    "(New York: Vintage, 1977), p. 215."
)

SHORT_PROSE_WITH_PUBLISHER = "This was published by Maska in 2018."


def _entry(src: str) -> dict:
    return {"source": src, "target": "", "origin": "fixture.pdf"}


# --- predicate-level assertions ---------------------------------------------


def test_sujets_body_prose_is_not_bibliography() -> None:
    """Multi-citation body prose with years but no structural biblio signal
    must NOT trip the biblio predicate (this is the Grom→Sujets root cause)."""
    assert _looks_like_bibliography(SUJETS_BODY_PROSE) is False


def test_short_prose_with_lone_publisher_name_is_not_bibliography() -> None:
    """A single KNOWN_PUBLISHERS substring hit — even paired with a bare year —
    is not enough to flip body prose to BIBLIOGRAPHY_ENTRY. This is the exact
    single-publisher-hit false positive the audit named."""
    assert _looks_like_bibliography(SHORT_PROSE_WITH_PUBLISHER) is False


def test_real_bibliography_entry_still_classifies_as_biblio() -> None:
    """`Author, F. YYYY. Title. City: Publisher, p. NN.` carries four
    structural signals (lastname-comma, year, page-ref, publisher city) and
    must still pass the biblio predicate."""
    assert _looks_like_bibliography(REAL_BIBLIO_ENTRY) is True


def test_real_footnote_still_classifies_as_biblio() -> None:
    """A numbered footnote with `Author, Title (City: Publisher, YYYY), p. NN`
    carries lastname-comma, page-ref, publisher-city, biblio-shape, year and
    publisher — far above the two-signal floor."""
    assert _looks_like_bibliography(REAL_FOOTNOTE) is True


# --- end-to-end pipeline assertions -----------------------------------------


def test_sujets_pipeline_does_not_label_body_prose_as_biblio() -> None:
    """Through the full classifier, the Sujets body sentence must land in
    BODY_TEXT (or any non-biblio class) — never BIBLIOGRAPHY_ENTRY."""
    labels = classify_segments([_entry(SUJETS_BODY_PROSE)])
    assert labels[0].klass is not SegmentClass.BIBLIOGRAPHY_ENTRY


def test_short_publisher_prose_pipeline_does_not_label_as_biblio() -> None:
    """End-to-end version of the single-publisher-hit guard."""
    labels = classify_segments([_entry(SHORT_PROSE_WITH_PUBLISHER)])
    assert labels[0].klass is not SegmentClass.BIBLIOGRAPHY_ENTRY


def test_footnote_pipeline_is_not_body_text() -> None:
    """Real footnote routes to FOOTNOTE or BIBLIOGRAPHY_ENTRY — never body."""
    labels = classify_segments([_entry(REAL_FOOTNOTE)])
    assert labels[0].klass in (
        SegmentClass.FOOTNOTE,
        SegmentClass.BIBLIOGRAPHY_ENTRY,
    )
