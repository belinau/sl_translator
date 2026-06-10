"""Unit tests for the deterministic COBISS bibliography parser.

# These tests run the parser against the real export at
# ``data/personal bibliography/bibliography_export.txt`` because the parser
# is, by design, tightly coupled to that grammar.
that would silently break if the grammar drifts (entry count, key field
extraction, agent role splitting, URL splitting, etc.).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.cobiss_parser import (
    CobissAgent,
    CobissEntry,
    _parse_agent_block,
    _parse_article_tail,
    _parse_roles,
    _split_title,
    _strip_isbn,
    _strip_issn,
    _strip_urls,
    parse_cobiss_file,
)


BIB_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "personal bibliography"
    / "bibliography_export.txt"
)


@pytest.fixture(scope="module")
def entries() -> list[CobissEntry]:
    assert BIB_PATH.exists(), f"missing fixture: {BIB_PATH}"
    return parse_cobiss_file(str(BIB_PATH))


def _get(entries: list[CobissEntry], n: int) -> CobissEntry:
    for e in entries:
        if e.entry_number == n:
            return e
    raise AssertionError(f"entry #{n} not parsed")


# ---------------------------------------------------------------------------
# Top-level invariants
# ---------------------------------------------------------------------------


def test_parses_all_one_hundred_fourteen_entries(entries: list[CobissEntry]) -> None:
    """The export contains 114 sequentially-numbered records; all must round-trip."""
    assert [e.entry_number for e in entries] == list(range(1, 115))


def test_every_entry_has_cobiss_id_and_year(entries: list[CobissEntry]) -> None:
    """Every COBISS record carries an ID; either the entry or the year-marker
    must supply a year (catalogue invariant)."""
    for e in entries:
        assert e.cobiss_id, f"entry #{e.entry_number} missing cobiss_id"
        assert e.year is not None, f"entry #{e.entry_number} missing year"
        assert e.title, f"entry #{e.entry_number} missing title"


# ---------------------------------------------------------------------------
# Article entries (ISSN-bearing)
# ---------------------------------------------------------------------------


def test_simple_journal_article(entries: list[CobissEntry]) -> None:
    """Entry 1 is the canonical short article: author / title / journal /
    volume / issue / pages / ISSN / COBISS ID."""
    e = _get(entries, 1)
    assert e.agents == [CobissAgent("BELINA", "Urban", ["author"])]
    assert e.title == "Brez dotikov"
    assert e.title_en == ""
    assert e.subtitle == ""
    assert e.journal_name == "Vpogled : revija za književnost"
    assert e.journal_volume == "letn. 2"
    assert e.journal_issue == "št. 3"
    assert e.pages == "str. 57-60"
    assert e.issn == "1854-3790"
    assert e.cobiss_id == "245447168"
    assert e.year == 2006


def test_article_with_numeric_journal_name(entries: list[CobissEntry]) -> None:
    """Entry 5's journal is literally named '2000', so the boundary picker
    must not mistake the journal-name digits for the publication year."""
    e = _get(entries, 5)
    assert e.title == "Modri arzenal"
    assert e.subtitle == "potovanje deškosti"
    assert e.journal_name == "2000 : revija za krščanstvo in kulturo"
    assert e.year == 2008
    assert e.pages == "str. 230-238"


def test_article_with_freeform_issue_label(entries: list[CobissEntry]) -> None:
    """Entry 27 uses the non-standard issue label 'idba2 = idiot balkan 2',
    which sits between the date and the page range — the parser must tolerate
    arbitrary middle clauses without polluting the title."""
    e = _get(entries, 27)
    assert e.title == "Transkripcija"
    assert e.title_en == "Transkripcija ; Romeo i glok = Romeo in glok"
    assert e.journal_name == "I.d.i.o.t"
    assert e.pages == "str. 159-163"
    assert e.year == 2014


def test_article_without_issn_has_pages(entries: list[CobissEntry]) -> None:
    """Entry 44 has no ISSN but is still an article — detected via 'str.'
    in the absence of an ISBN."""
    e = _get(entries, 44)
    assert e.title == "Contain, afloat-ness/Control, muck space"
    assert e.journal_name == "VII : das Magazin der Sloweninnen und Slowenen in der Steiermark"
    assert e.pages == "str. 90-111"
    assert e.issn == ""


def test_seasonal_dates_with_bilingual_slash(entries: list[CobissEntry]) -> None:
    """Entry 39 uses 'zima/winter 2019' — slash-joined SL/EN season label."""
    e = _get(entries, 39)
    assert e.title == "Pisanje na sceni, pisanje za sceno"
    assert e.title_en == "Writing on the scene, writing for the scene"
    assert e.journal_volume == "letn. 34"
    assert e.year == 2019


def test_multi_range_pages_continuation(entries: list[CobissEntry]) -> None:
    """Entry 67 has 'str. 111-115, 116-119' — pages span multiple ranges
    separated by ', ' which must be joined back together."""
    e = _get(entries, 67)
    assert e.pages == "str. 111-115, 116-119"


def test_web_article_without_pages(entries: list[CobissEntry]) -> None:
    """Entry 40 is a web-native article: no pages, has a URL, ISSN present."""
    e = _get(entries, 40)
    assert e.title == "Upehane feministične strategije Golega življenja"
    assert e.journal_name == "Neodvisni : teritorij sodobnih scenskih umetnosti"
    assert e.pages == ""
    assert e.urls == [
        "https://www.neodvisni.art/refleksija/2019/11/"
        "upehane-feministicne-strategije-golega-zivljenja/"
    ]


# ---------------------------------------------------------------------------
# Monograph entries (ISBN-bearing)
# ---------------------------------------------------------------------------


def test_anonymous_monograph(entries: list[CobissEntry]) -> None:
    """Entry 2 has no agent — the parser must not invent one."""
    e = _get(entries, 2)
    assert e.agents == []
    assert e.title == "Redeye"
    assert e.publisher_city == "Ljubljana"
    assert e.publisher == "Republic of Slovenia Public Fund for Cultural Activities"
    assert e.year == 2006
    assert e.extent == "151 str., ilustr."
    assert e.series == "Mentor, Special edition."
    assert e.isbn == ["961-6141-40-6", "978-961-6141-40-6"]


def test_bilingual_monograph_with_series(entries: list[CobissEntry]) -> None:
    """Entry 22 has a 'Prehodi, letn. 10' series — the 'letn.' marker must
    not trick the article path when an ISBN is present."""
    e = _get(entries, 22)
    assert e.title == "Misleče telo"
    assert e.publisher_city == "Ljubljana"
    assert e.publisher == "Emanat"
    assert e.year == 2014
    assert e.extent == "287 str., ilustr."
    assert e.series == "Prehodi, letn. 10."
    assert e.journal_name == ""  # NOT misclassified as article


def test_anonymous_bracketed_city(entries: list[CobissEntry]) -> None:
    """Entry 100 uses '[Ljubljana]:' notation — brackets must be stripped
    from the city, not embedded in it."""
    e = _get(entries, 100)
    assert e.agents == []
    assert e.publisher_city == "Ljubljana"
    assert e.publisher == "Muzej in galerije mesta Ljubljane"
    assert e.year == 2024


def test_edition_extraction(entries: list[CobissEntry]) -> None:
    """Entry 32 has an edition statement '1. elektronska izd.' between title
    and publisher block."""
    e = _get(entries, 32)
    assert e.edition == "1. elektronska izd."
    assert e.title == "Cesta sestradanih"
    assert e.publisher_city == "Vnanje Gorice"
    assert e.isbn == ["978-961-7020-15-1", "978-961-7020-16-8"]


def test_english_edition_marker(entries: list[CobissEntry]) -> None:
    """Entry 108 has the English form '1st. ed.' — both languages must be
    recognised by the edition stripper."""
    e = _get(entries, 108)
    assert e.edition == "1st. ed."
    assert e.title == "Broken promises"
    assert e.publisher_city == "Reggio Emilia"


def test_bilingual_subtitle_split(entries: list[CobissEntry]) -> None:
    """Entry 28 demonstrates the canonical SL/EN bilingual structure:
    'SL title : SL subtitle = EN title : EN subtitle'."""
    e = _get(entries, 28)
    assert e.title == "Človek in mit"
    assert e.subtitle == "retrospektivna razstava"
    assert e.title_en == "The man and the myth : retrospective exhibition"


# ---------------------------------------------------------------------------
# Agent parsing
# ---------------------------------------------------------------------------


def test_multi_agent_with_mixed_roles(entries: list[CobissEntry]) -> None:
    """Entry 52 has six agents with overlapping multi-word roles including
    'author of introduction, etc.' which contains a comma — must not split
    on that internal comma."""
    e = _get(entries, 52)
    last_names = [a.last_name for a in e.agents]
    assert last_names == [
        "PETRIČ", "SMREKAR", "SPAČAL", "ŠEBJANIČ", "TRATNIK", "TREBUŠAK",
    ]
    assert e.agents[2].roles == ["artist", "photographer"]
    assert e.agents[5].roles == [
        "editor", "author of introduction, etc.", "exhibitor",
    ]


def test_agent_with_initial(entries: list[CobissEntry]) -> None:
    """Entry 88 contains 'VRHOVEC SAMBOLEC, Tao G. (artist, interviewee)'
    — the trailing 'G.' initial must be preserved in the first name and the
    role parenthesis must be consumed, not leaked into the title."""
    e = _get(entries, 88)
    assert e.agents[0] == CobissAgent(
        last_name="VRHOVEC SAMBOLEC",
        first_name="Tao G.",
        roles=["artist", "interviewee"],
    )
    assert e.agents[1] == CobissAgent(
        last_name="ANJOLI VUJIĆ",
        first_name="Mara",
        roles=["editor", "interviewer"],
    )
    assert e.title == "Brati branje"


def test_agent_with_particle(entries: list[CobissEntry]) -> None:
    """Entry 62 has 'SAMSONOW, Elisabeth von' — the lowercase particle
    'von' belongs to the first name."""
    e = _get(entries, 62)
    samsonow = next(a for a in e.agents if a.last_name == "SAMSONOW")
    assert samsonow.first_name == "Elisabeth von"
    assert samsonow.roles == ["artist"]


def test_default_role_is_author(entries: list[CobissEntry]) -> None:
    """Agents without a role parenthesis default to 'author'."""
    e = _get(entries, 39)
    assert all(a.roles == ["author"] for a in e.agents)
    assert {a.last_name for a in e.agents} == {"BREZAVŠČEK", "LOBNIK"}


def test_anonymous_entry_has_no_agents(entries: list[CobissEntry]) -> None:
    e = _get(entries, 111)
    assert e.agents == []
    assert e.title == "Na liniji pobega - možnim svetovom naproti"


# ---------------------------------------------------------------------------
# URL / ISBN / award extraction
# ---------------------------------------------------------------------------


def test_multiple_urls_separated_by_commas(entries: list[CobissEntry]) -> None:
    """Entry 91 has three URLs joined by commas across line wraps — they
    must be split back out into three distinct URLs."""
    e = _get(entries, 91)
    assert e.urls == [
        "https://sistory.github.io/Odlivanje_smrti/",
        "http://hdl.handle.net/11686/57086",
        "http://www.dlib.si/details/URN:NBN:SI:doc-3K3KMZRB",
    ]
    assert e.isbn == ["978-961-7104-26-4", "978-961-7104-27-1"]


def test_awards_attach_to_preceding_entry(entries: list[CobissEntry]) -> None:
    """Entries 13 and 31 each have an 'award:' line following them."""
    assert _get(entries, 13).awards == ["Nagrada Vilenica 2019"]
    assert _get(entries, 31).awards == ["Man Booker Prize for Fiction, 1991"]
    # No other entry inherits those awards.
    assert all(
        not e.awards for e in entries if e.entry_number not in {13, 31}
    )


# ---------------------------------------------------------------------------
# Helper functions (white-box)
# ---------------------------------------------------------------------------


def test_parse_roles_known_vocabulary() -> None:
    assert _parse_roles("artist, photographer") == ["artist", "photographer"]
    assert _parse_roles("editor, author of introduction, etc., exhibitor") == [
        "editor",
        "author of introduction, etc.",
        "exhibitor",
    ]


def test_parse_roles_collapses_internal_whitespace() -> None:
    # Wrapped line-break inside the parenthesis becomes a normal space.
    assert _parse_roles("editor,\nauthor of introduction,\netc.") == [
        "editor",
        "author of introduction, etc.",
    ]


def test_parse_agent_block_anonymous() -> None:
    agents, rest = _parse_agent_block("Some Title. Publisher")
    assert agents == []
    assert rest == "Some Title. Publisher"


def test_parse_agent_block_terminates_on_period() -> None:
    agents, rest = _parse_agent_block(
        "DOE, Jane (editor). Some Title. Publisher"
    )
    assert agents == [CobissAgent("DOE", "Jane", ["editor"])]
    assert rest == "Some Title. Publisher"


def test_split_title_bilingual() -> None:
    title, sub, en = _split_title("A : sub = B : sub_en")
    assert (title, sub, en) == ("A", "sub", "B : sub_en")


def test_split_title_no_bilingual() -> None:
    title, sub, en = _split_title("Only SL : with sub")
    assert (title, sub, en) == ("Only SL", "with sub", "")


def test_parse_article_tail_drops_decoration() -> None:
    date, letn, sht, pages = _parse_article_tail(
        "jun.-dec. 2006, letn. 2, št. 3, str. 57-60, portret"
    )
    assert (date, letn, sht, pages) == (
        "jun.-dec. 2006", "letn. 2", "št. 3", "str. 57-60",
    )


def test_parse_article_tail_pages_continuation() -> None:
    _, _, _, pages = _parse_article_tail(
        "pomlad 2021, letn. 36, št. 201/202, str. 148-152, 153-157, ilustr."
    )
    assert pages == "str. 148-152, 153-157"


def test_strip_isbn_collects_all() -> None:
    text, isbns = _strip_isbn(
        "rest ISBN 978-961-93064-4-4, ISBN 961-6141-40-6. trailing"
    )
    assert isbns == ["978-961-93064-4-4", "961-6141-40-6"]
    assert text == "rest trailing"


def test_strip_issn_single() -> None:
    text, issn = _strip_issn("foo. ISSN 1854-3790. bar")
    assert issn == "1854-3790"
    # ISSN clause + its terminating period are removed; the upstream period
    # belongs to the previous sentence and is left intact.
    assert text == "foo. bar"


def test_strip_urls_splits_on_commas() -> None:
    text, urls = _strip_urls(
        "see https://example.com/a, https://example.com/b. end"
    )
    assert urls == ["https://example.com/a", "https://example.com/b"]
    assert "https://" not in text
