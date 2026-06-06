"""Parse COBISS plain-text bibliography exports into structured records.

The COBISS export wraps entries at ~76 columns, separates them with blank
lines, and uses standalone four-digit lines as year markers.  Each entry
is numbered (``N. ...``) and may carry trailing ``award:`` lines.

This parser is **deterministic regex-based** — no LLM, no fuzzy matching.
The grammar covered here is exactly the one observed in the bilingual
Slovenian / Croatian / Serbian / English COBISS export at
``data/personal bibliography/bibliography_belina.txt``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CobissAgent:
    """An agent (person) extracted from a COBISS entry."""

    last_name: str
    first_name: str = ""
    roles: list[str] = field(default_factory=list)  # e.g. ["author", "editor"]


@dataclass
class CobissEntry:
    """A parsed COBISS bibliography entry."""

    entry_number: int
    raw_text: str
    agents: list[CobissAgent] = field(default_factory=list)
    title: str = ""                  # Main title (SL side if bilingual)
    title_en: str = ""               # English side if bilingual (= separated)
    subtitle: str = ""               # After first :
    edition: str = ""                # e.g. '1. izd.'
    publisher_city: str = ""
    publisher: str = ""
    year: Optional[int] = None
    extent: str = ""                 # e.g. '551 str.'
    series: str = ""                 # e.g. 'Zbirka Eho, 11'
    isbn: list[str] = field(default_factory=list)
    issn: str = ""
    cobiss_id: str = ""
    urls: list[str] = field(default_factory=list)
    awards: list[str] = field(default_factory=list)
    # Journal/magazine info (for article entries)
    journal_name: str = ""
    journal_volume: str = ""         # e.g. 'letn. 35'
    journal_issue: str = ""          # e.g. 'št. 200aa'
    pages: str = ""                  # e.g. 'str. 146-153'


# ---------------------------------------------------------------------------
# Character classes
#
# After NFC normalisation the source file uses only:
#   UPPER  ABCDEFGHIJKLMNOPQRSTUVWYZÓÖĆČŠŽ
#   LOWER  abcdefghijklmnopqrstuvwxyzáäóöüćčđšž   (plus U+02B9 ʹ)
# ---------------------------------------------------------------------------

_UPPER = "A-ZÓÖĆČŠŽ"
_LOWER = "a-záäóöüćčđšžʹ"
_ALPHA = _UPPER + _LOWER

# A LASTNAME token: capital sequence, hyphens/apostrophes allowed; optional
# multi-word ("VRHOVEC SAMBOLEC", "KLEINE-BENNE", "ANJOLI VUJIĆ").
_LASTNAME_TOKEN = (
    rf"[{_UPPER}][{_UPPER}ʹ'\-]*"
    rf"(?:\s+[{_UPPER}][{_UPPER}ʹ'\-]*)*"
)

# A first-name word: capital + tail, or a known particle, or a single
# capital letter (an initial; the trailing period is handled separately).
_FIRSTNAME_WORD = rf"(?:[{_UPPER}][{_ALPHA}'\-]*|von|de|van|der|y)"

_LASTNAME_RE = re.compile(_LASTNAME_TOKEN)
_LASTNAME_LOOKAHEAD = re.compile(rf"^{_LASTNAME_TOKEN},\s")
_FIRSTNAME_WORD_RE = re.compile(_FIRSTNAME_WORD)
_INITIAL_RE = re.compile(rf"[{_UPPER}]\.")
_PARTICLE_RE = re.compile(r"(?:von|de|van|der|y)\s")


# Known roles ordered longest-first so the compound role
# "author of introduction, etc." beats "author".
_KNOWN_ROLES: tuple[str, ...] = (
    "author of introduction, etc.",
    "curator of an exhibition",
    "interviewee",
    "interviewer",
    "illustrator",
    "translator",
    "photographer",
    "exhibitor",
    "editor",
    "author",
    "artist",
)


def _parse_roles(roles_str: str) -> list[str]:
    """Split a role-list paren-content into canonical roles."""
    s = re.sub(r"\s+", " ", roles_str).strip()
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        while i < n and s[i] in ", ":
            i += 1
        if i >= n:
            break
        matched = False
        for role in _KNOWN_ROLES:
            end = i + len(role)
            if s[i:end] == role:
                out.append(role)
                i = end
                matched = True
                break
        if not matched:
            comma = s.find(",", i)
            chunk = s[i:] if comma == -1 else s[i:comma]
            chunk = chunk.strip()
            if chunk:
                out.append(chunk)
            i = n if comma == -1 else comma
    return out


# ---------------------------------------------------------------------------
# Agent block
# ---------------------------------------------------------------------------


def _is_capital_initial(word: str) -> bool:
    """True if ``word`` is a single uppercase letter (an initial)."""
    return len(word) == 1 and word.isalpha() and word.isupper()


def _parse_agent_block(text: str) -> tuple[list[CobissAgent], str]:
    """Parse the leading agent block.

    Returns ``(agents, remaining_text)`` where ``remaining_text`` follows
    the period+space that closes the agent block.  If no agents are
    present (anonymous entry) returns ``([], text)`` unchanged.
    """
    if not _LASTNAME_LOOKAHEAD.match(text):
        return [], text

    agents: list[CobissAgent] = []
    pos = 0
    n = len(text)

    while pos < n:
        m = _LASTNAME_RE.match(text, pos)
        if not m:
            break
        last_name = m.group()
        pos = m.end()

        if text[pos:pos + 2] != ", ":
            break
        pos += 2

        first_parts: list[str] = []
        ended_with_period = False
        while pos < n:
            if text[pos] == " ":
                pos += 1
                continue
            if text[pos] == "(":
                break
            wm = _FIRSTNAME_WORD_RE.match(text, pos)
            if not wm:
                break
            word = wm.group()
            first_parts.append(word)
            pos = wm.end()

            two = text[pos:pos + 2]
            if two == ", ":
                break
            if two == ". ":
                rest = text[pos + 2:]
                is_initial = _is_capital_initial(word)
                continuation = (
                    rest.startswith("(")
                    or _INITIAL_RE.match(rest) is not None
                    or _PARTICLE_RE.match(rest) is not None
                    or _LASTNAME_LOOKAHEAD.match(rest) is not None
                )
                if is_initial:
                    first_parts[-1] = word + "."
                if is_initial and continuation:
                    pos += 2
                    if rest.startswith("(") or _LASTNAME_LOOKAHEAD.match(rest):
                        break
                    continue
                pos += 2
                ended_with_period = True
                break
            if pos < n and text[pos] == ".":
                if _is_capital_initial(word):
                    first_parts[-1] = word + "."
                pos += 1
                ended_with_period = True
                break

        first_name = " ".join(first_parts).strip()

        if pos < n and text[pos] == "(":
            close = text.find(")", pos)
            if close != -1:
                roles_str = text[pos + 1:close]
                roles = _parse_roles(roles_str) or ["author"]
                pos = close + 1
            else:
                roles = ["author"]
        else:
            roles = ["author"]

        agents.append(CobissAgent(last_name=last_name, first_name=first_name, roles=roles))

        if ended_with_period:
            return agents, text[pos:].lstrip()

        two = text[pos:pos + 2]
        if two == ", ":
            rest = text[pos + 2:]
            if _LASTNAME_LOOKAHEAD.match(rest):
                pos += 2
                continue
            pos += 2
            return agents, rest
        if two == ". ":
            return agents, text[pos + 2:].lstrip()
        if text[pos:pos + 1] == ".":
            return agents, text[pos + 1:].lstrip()
        return agents, text[pos:].lstrip()

    return agents, text[pos:].lstrip()


# ---------------------------------------------------------------------------
# Metadata strippers (COBISS ID, URLs, ISBN, ISSN)
# ---------------------------------------------------------------------------


_COBISS_ID_RE = re.compile(r"\[COBISS\.SI-ID\s+(\d+)\]")
_ISBN_RE = re.compile(r"\bISBN\s+([0-9Xx\-]+)")
_ISSN_RE = re.compile(r"\bISSN\s+([0-9Xx\-]+)")
# A URL terminates at whitespace OR a comma (COBISS comma-separates URL lists).
_URL_RE = re.compile(r"https?://[^\s,]+")


def _strip_cobiss_id(text: str) -> tuple[str, str]:
    m = _COBISS_ID_RE.search(text)
    if not m:
        return text, ""
    cobiss_id = m.group(1)
    return (text[:m.start()] + text[m.end():]).rstrip(" ."), cobiss_id


def _strip_urls(text: str) -> tuple[str, list[str]]:
    urls = _URL_RE.findall(text)
    if not urls:
        return text, []
    cleaned = [u.rstrip(".,;)") for u in urls]
    for u in sorted(urls, key=len, reverse=True):
        text = text.replace(u, "")
    text = re.sub(r"\s*,\s*,\s*", ", ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\.", ".", text)
    text = re.sub(r"\.\s*\.", ".", text)
    return text.strip(" ,."), cleaned


def _strip_isbn(text: str) -> tuple[str, list[str]]:
    isbns: list[str] = []
    while True:
        m = _ISBN_RE.search(text)
        if not m:
            break
        isbns.append(m.group(1))
        start, end = m.start(), m.end()
        if text[end:end + 2] == ", ":
            end += 2
        elif text[end:end + 2] == ". ":
            end += 2
        elif text[end:end + 1] == ".":
            end += 1
        text = text[:start] + text[end:]
    return re.sub(r"\s+", " ", text).strip(" ,."), isbns


def _strip_issn(text: str) -> tuple[str, str]:
    m = _ISSN_RE.search(text)
    if not m:
        return text, ""
    issn = m.group(1)
    start, end = m.start(), m.end()
    if text[end:end + 2] == ". ":
        end += 2
    elif text[end:end + 1] == ".":
        end += 1
    return re.sub(r"\s+", " ", text[:start] + text[end:]).strip(" ,."), issn


# ---------------------------------------------------------------------------
# Edition, publisher (monograph)
# ---------------------------------------------------------------------------


_EDITION_RE = re.compile(
    r"\.\s+("
    r"\d+(?:st|nd|rd|th)?\.\s+(?:[a-záäóöüćčđšž]+\s+)*(?:izd|ponatis|ed)\.?"
    r")\s*$",
    re.IGNORECASE,
)


def _strip_edition(title_part: str) -> tuple[str, str]:
    m = _EDITION_RE.search(title_part)
    if not m:
        return title_part, ""
    edition = m.group(1).strip()
    if not edition.endswith("."):
        edition += "."
    return title_part[:m.start()].rstrip(" ."), edition


# Publisher block (monograph): "City: Publisher, YEAR." or bracketed
# "[City: Publisher, YEAR]." — possibly with a trailing "?" inside the year
# ("[S. l.: s. n., 2023?]").  City must be ≥3 chars so we do not match
# the chapter-in-book signal "In:".
_PUBLISHER_RE = re.compile(
    rf"""
    \.\s+
    \[?
    (?P<city>[{_UPPER}S][^:\]]{{2,}}?)
    \]?
    :\s+
    (?P<publisher>.+?)
    ,\s+
    \[?(?P<year>\d{{4}})\??\]?
    \.
    """,
    re.VERBOSE,
)


def _find_publisher(text: str) -> Optional[re.Match[str]]:
    for m in _PUBLISHER_RE.finditer(text):
        return m
    return None


def _clean_publisher_value(raw: str) -> str:
    """Drop bilingual ``: = English`` suffix from a publisher string."""
    if ": =" in raw:
        raw = raw.split(": =", 1)[0].rstrip()
    return raw.strip()


# Series detector — applied to the post-publisher tail.
_SERIES_RE = re.compile(
    rf"""
    (?:^|\.\s+)
    (?P<series>
        [{_UPPER}][{_ALPHA}]*(?:\s+[{_ALPHA}][{_ALPHA}]*)*
        ,\s+
        (?:letn\.\s+\d+|knj\.\s+\d+|\d+|Special\s+edition)
    )
    \s*\.?\s*$
    """,
    re.VERBOSE,
)


def _split_extent_and_series(rest: str) -> tuple[str, str]:
    """Split the post-publisher region into ``(extent, series)``."""
    rest = rest.strip().rstrip(" .")
    if not rest:
        return "", ""

    series = ""
    sm = _SERIES_RE.search(rest)
    if sm:
        series = sm.group("series").strip().rstrip(".") + "."
        extent = rest[:sm.start()].rstrip(" .")
    else:
        extent = rest

    if extent and not extent.endswith("."):
        extent = extent + "."
    return extent, series


# ---------------------------------------------------------------------------
# Article (journal / magazine) metadata
# ---------------------------------------------------------------------------


# Slovenian and English seasonal / monthly markers.
_MONTHS = (
    "jan|feb|mar|apr|maj|jun|jul|avg|sep|sept|okt|nov|dec|"
    "januar|februar|marec|april|maj|junij|julij|avgust|"
    "september|oktober|november|december|"
    "pomlad|poletje|jesen|zima|"
    "spring|summer|autumn|fall|winter"
)

# A complete date expression at the start of an article tail:
#   * "jun.-dec. 2006"
#   * "4. nov. 2019"
#   * "pomlad 2020"
#   * "zima/winter 2019"
#   * "jesen/fall 2021"
#   * "2008"
#   * "2018/2019"
_DATE_PATTERN = (
    rf"(?:"
    rf"(?:\d+\.\s+)?(?:{_MONTHS})\.?(?:[-/](?:{_MONTHS})\.?)?\s+\d{{4}}"
    rf"|\d{{4}}(?:/\d{{4}})?"
    rf")"
)

# A `\.\s+` candidate boundary — any period+space pair in the text.
_PERIOD_SPACE_RE = re.compile(r"\.\s+")
_DATE_RE = re.compile(_DATE_PATTERN)

# Signals that the metadata block belongs to an article even when no ISSN
# was extracted (a small handful of magazine entries skip the ISSN).
_ARTICLE_HINT_RE = re.compile(r"\b(?:[Ss]tr\.|letn\.|št\.)\s")


def _find_article_boundary(text: str) -> Optional[int]:
    """Return the start index of the article date-block, or ``None``.

    Strategy: find every non-overlapping DATE match and every ``.\\s+``
    candidate.  A candidate boundary is one whose own span is *not*
    inside any DATE span (i.e. a period that lives inside ``jun.-dec.
    2006`` does not count), and whose end touches the start of a DATE.
    The rightmost such boundary wins — that gives entry 5 the correct
    journal name when the title contains a date-like sequence.
    """
    date_spans = [m.span() for m in _DATE_RE.finditer(text)]
    if not date_spans:
        return None
    date_starts = {start for start, _ in date_spans}

    def inside_date(pos: int) -> bool:
        return any(start <= pos < end for start, end in date_spans)

    chosen: Optional[int] = None
    for m in _PERIOD_SPACE_RE.finditer(text):
        if inside_date(m.start()):
            continue
        if m.end() in date_starts:
            chosen = m.end()
    return chosen


def _parse_article_tail(tail: str) -> tuple[str, str, str, str]:
    """Parse ``DATE, letn. N, št. N, str. N-N, …`` into typed pieces.

    Returns ``(date, letn, sht, pages)``.  The ``date`` is the first
    comma-separated clause.  Pages can span multiple comma-separated
    ranges (``str. 140-143, 144-147``) — those continuations are joined.
    Unrecognised clauses (``ilustr.``, ``portret``, ``[mednarodna št.] balkan``,
    ``idba2 = idiot balkan 2``) are dropped.
    """
    tail = tail.strip().rstrip(" .")
    if not tail:
        return "", "", "", ""

    parts = [p.strip() for p in tail.split(", ") if p.strip()]
    date = parts[0] if parts else ""
    letn = sht = pages = ""

    for chunk in parts[1:]:
        if re.match(r"letn\.\s+", chunk):
            letn = chunk
        elif re.match(r"št\.\s+", chunk):
            sht = chunk
        elif re.match(r"[Ss]tr\.\s+", chunk):
            pages = chunk
        elif pages and re.match(r"[\[\d]", chunk):
            # Continuation of a multi-range page expression.
            pages += ", " + chunk

    return date, letn, sht, pages


# ---------------------------------------------------------------------------
# Title parsing
# ---------------------------------------------------------------------------


def _split_title(title_part: str) -> tuple[str, str, str]:
    """Split ``Title : subtitle = English title : English subtitle``.

    Returns ``(title, subtitle, title_en)``.  Only the first ``=`` is the
    language separator; only the first ``:`` on the SL side is the
    subtitle separator.
    """
    title_part = title_part.strip().rstrip(" .")
    sl, _, en = title_part.partition(" = ")
    title, _, subtitle = sl.partition(" : ")
    return title.strip(), subtitle.strip(), en.strip()


# ---------------------------------------------------------------------------
# Entry parser
# ---------------------------------------------------------------------------


_ENTRY_NUM_RE = re.compile(r"^(\d+)\.\s+")


def _parse_entry_text(
    text: str,
    fallback_year: Optional[int],
    awards: list[str],
) -> Optional[CobissEntry]:
    raw_text = text
    m = _ENTRY_NUM_RE.match(text)
    if not m:
        return None
    entry_number = int(m.group(1))
    body = text[m.end():]

    body, cobiss_id = _strip_cobiss_id(body)
    body, urls = _strip_urls(body)
    body, isbns = _strip_isbn(body)
    body, issn = _strip_issn(body)
    body = body.strip(" .")

    agents, after_agents = _parse_agent_block(body)
    after_agents = after_agents.strip()

    title = subtitle = title_en = ""
    edition = ""
    publisher_city = publisher = ""
    extent = series = ""
    pub_year: Optional[int] = None
    journal_name = journal_volume = journal_issue = pages = ""

    # Chapter-in-book convention: title side ends with "In: container".
    chapter_split = None
    if " In: " in after_agents and not issn:
        chapter_split = after_agents.index(" In: ")

    if after_agents:
        # ISSN unambiguously marks an article.  When no ISSN is present we
        # fall back to the page-number marker — but only when the entry
        # also lacks an ISBN, otherwise series notation like
        # "Prehodi, letn. 10. ISBN …" misclassifies a monograph.
        is_article = bool(issn) or (
            not isbns and bool(_ARTICLE_HINT_RE.search(after_agents))
        )
        if chapter_split is not None:
            is_article = False  # Force chapter-in-book down the publisher path.

        if is_article:
            boundary = _find_article_boundary(after_agents)
            if boundary is not None:
                head = after_agents[:boundary].rstrip(" .")
                tail = after_agents[boundary:].strip()
                if ". " in head:
                    title_part, _, journal = head.rpartition(". ")
                else:
                    title_part, journal = "", head
                journal_name = journal.strip().rstrip(" .")
                title, subtitle, title_en = _split_title(title_part)
                date, letn, sht, page_str = _parse_article_tail(tail)
                # Date is recorded only if it resolves to an integer year.
                journal_volume, journal_issue, pages = letn, sht, page_str
                year_match = re.search(r"\b(\d{4})\b", date)
                if year_match:
                    pub_year = int(year_match.group(1))
            else:
                title, subtitle, title_en = _split_title(after_agents)
        else:
            title_segment = after_agents
            container_part = ""
            if chapter_split is not None:
                title_segment = after_agents[:chapter_split].rstrip(" .")
                container_part = after_agents[chapter_split + len(" In: "):]

            target = container_part or title_segment
            pub_match = _find_publisher(target)
            if pub_match is not None:
                title_part = title_segment if container_part else (
                    target[:pub_match.start()].rstrip(" .")
                )
                title_part, edition = _strip_edition(title_part)
                title, subtitle, title_en = _split_title(title_part)
                publisher_city = pub_match.group("city").strip().rstrip(" .[]")
                publisher = _clean_publisher_value(pub_match.group("publisher"))
                pub_year = int(pub_match.group("year"))
                rest_after_pub = target[pub_match.end():].strip()
                extent, series = _split_extent_and_series(rest_after_pub)
            else:
                title, subtitle, title_en = _split_title(after_agents)

    return CobissEntry(
        entry_number=entry_number,
        raw_text=raw_text,
        agents=agents,
        title=title,
        title_en=title_en,
        subtitle=subtitle,
        edition=edition,
        publisher_city=publisher_city,
        publisher=publisher,
        year=pub_year if pub_year is not None else fallback_year,
        extent=extent,
        series=series,
        isbn=isbns,
        issn=issn,
        cobiss_id=cobiss_id,
        urls=urls,
        awards=awards,
        journal_name=journal_name,
        journal_volume=journal_volume,
        journal_issue=journal_issue,
        pages=pages,
    )


# ---------------------------------------------------------------------------
# File loader (line joining, year tracking, block splitting)
# ---------------------------------------------------------------------------


_URL_AT_EOL_RE = re.compile(r"https?://\S+$")
_YEAR_LINE_RE = re.compile(r"^\d{4}$")
_AWARD_LINE_RE = re.compile(r"^\s*award:\s*(.+?)\s*$")


def _join_block(lines: list[str]) -> str:
    """Join wrapped lines, splicing URL-internal line breaks without a space."""
    if not lines:
        return ""
    out = [lines[0].strip()]
    for raw in lines[1:]:
        cur = raw.strip()
        prev = out[-1]
        if _URL_AT_EOL_RE.search(prev):
            out[-1] = prev + cur
        else:
            out[-1] = prev + " " + cur
    return out[0]


def parse_cobiss_file(path: str) -> list[CobissEntry]:
    """Parse a COBISS plain-text bibliography export.

    The file is iterated line by line.  Blank lines separate entries; a
    standalone four-digit line marks the year that subsequent entries
    belong to (used as a fallback when the entry itself does not encode a
    publication year).  Lines starting with ``award:`` attach to the most
    recent entry.
    """
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    raw = unicodedata.normalize("NFC", raw)

    entries: list[CobissEntry] = []
    block: list[str] = []
    awards: list[str] = []
    current_year: Optional[int] = None

    def flush() -> None:
        nonlocal block, awards
        if not block:
            block = []
            awards = []
            return
        text = re.sub(r"\s+", " ", _join_block(block)).strip()
        entry = _parse_entry_text(text, current_year, awards)
        if entry is not None:
            entries.append(entry)
        block = []
        awards = []

    for raw_line in raw.split("\n"):
        stripped = raw_line.rstrip().strip()
        if not stripped:
            flush()
            continue
        if _YEAR_LINE_RE.match(stripped):
            flush()
            current_year = int(stripped)
            continue
        award_m = _AWARD_LINE_RE.match(stripped)
        if award_m:
            awards.append(award_m.group(1).strip())
            continue
        block.append(stripped)

    flush()
    return entries


__all__ = [
    "CobissAgent",
    "CobissEntry",
    "parse_cobiss_file",
]
