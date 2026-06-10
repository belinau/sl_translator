"""Slovenian Chicago notes-bibliography footnote parser.

Parses footnote definitions from markdown export of a translated book:

  [^45]:  Glej Foucault, *Discipline and Punish*, prev. Jelka Bajt, Ljubljana: Studia Humanitatis, 2004, str. 67.

Each footnote can contain MULTIPLE citations separated by `;`. Each citation
is one of these shapes (Slovenian Chicago notes):

  - Full book:           `Author, *Title*, City: Publisher, Year, str. N.`
  - Translation:         `Author, *Title*, prev. Translator, City: Publisher, Year, str. N.`
  - Article:             `Author, »Title«, *Journal*, Year, str. N-N.`
  - Book chapter:        `Author, »Title«, v: Editor (ur.), *Book*, City: Publisher, Year, str. N.`
  - Web:                 `Author, »Title«, URL, Date (dostop Date).`
  - Short form / op.cit.: `Lastname, *Short Title*, str. N.`
  - Ibid:                `*Ibid.*, str. N.` or just `*Ibid*.`

Prefix words to strip: `Glej`, `Glej tudi`, `Glej npr.`, `Prim.`, `Cit. po`,
`Po`, `Pri`, `Kot piše`.

Output: list of FootnoteParsed records, each holding the footnote_number
and one or more ParsedCitation objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .bibliography_parser import ParsedAuthor, ParsedCitation


# Markdown footnote definition: `[^N]: text...` (may span lines until next `[^M]:` or end)
_FN_DEF_RE = re.compile(r"^\[\^(\d+)\]:\s*(.*?)$", re.MULTILINE)

# Italics in markdown
_ITALIC_RE = re.compile(r"\*(?P<text>[^*\n]{2,200}?)\*")

# Slovenian quoted text: »text«
_SL_QUOTED_RE = re.compile(r"»(?P<text>[^»«\n]{2,300}?)«")
_EN_QUOTED_RE = re.compile(r"[\"“‘'](?P<text>[^\"”’'\n]{2,300}?)[\"”’']")

# Page reference at end of citation
_PAGE_TAIL_RE = re.compile(r"(?:str\.|pp?\.|p\.)\s*(?P<page>\d+(?:\s*[-–]\s*\d+)?)\s*\.?\s*$")
_PAGE_ANYWHERE_RE = re.compile(r"(?:str\.|pp?\.|p\.)\s*(?P<page>\d+(?:\s*[-–]\s*\d+)?)")

# URL detection
_URL_RE = re.compile(r"https?://[^\s,\]\)]+")

# Citation prefixes to strip
_CITE_PREFIXES = (
    "Glej tudi", "Glej npr.", "Glej na primer", "Glej", "Prim.", "Prim",
    "Cit. po", "Cit.", "Po", "Pri", "Kot piše", "Kot pravi", "Po besedah",
    "See also", "See e.g.", "See", "Cf.",
)
_CITE_PREFIX_RE = re.compile(
    r"^(?:" + "|".join(re.escape(p) for p in _CITE_PREFIXES) + r")\s+",
    re.IGNORECASE,
)

# Ibid markers
_IBID_RE = re.compile(r"^\**\s*(?:Ibid|ibidem|prav tam|loc\.\s*cit\.)\.?\s*\**\s*,?", re.IGNORECASE)

# Editor marker
_EDITOR_RE = re.compile(r"\(\s*(?:ur\.|eds?\.)\s*\)")

# Translator marker
_TRANS_RE = re.compile(
    r"\bprev(?:edel|edla|edli|edlo)?\.?\s+"
    r"(?P<name>[A-ZŠŽČĆĐÖÜÄ][a-zšžčćđöüäáéíóúýňřčëîôõùçßıłęąóżźńśćåæøïëâêûôîäöüÿ'’\-]+"
    r"(?:\s+[A-ZŠŽČĆĐÖÜÄ][a-zšžčćđöüäáéíóúýňřčëîôõùçßıłęąóżźńśćåæøïëâêûôîäöüÿ'’\-]+){0,2})",
    re.IGNORECASE,
)

# "v:" or "in:" → book chapter
_CHAPTER_MARKER_RE = re.compile(r"\b(?:v|in)\s*:\s*", re.IGNORECASE)

# Publisher city + publisher: `City: Publisher`
# Use a broader city list since SL footnotes cite from many sources
_KNOWN_CITIES_SL = (
    "Ljubljana", "Maribor", "Koper", "Celje", "Kranj",
    "Zagreb", "Beograd", "Belgrade", "Sarajevo", "Skopje", "Novi Sad",
    "London", "New York", "Cambridge", "Oxford", "Berlin", "Paris",
    "Frankfurt", "Boston", "Chicago", "Stanford", "Princeton",
    "Minneapolis", "Durham", "Madrid", "Amsterdam", "Helsinki",
    "Stockholm", "Vienna", "Wien", "München", "Munich", "Köln", "Cologne",
    "Roma", "Milano", "Milan", "Bruxelles", "Brussels", "Genève", "Geneva",
    "Langley", "Pariz", "Berlin", "Dunaj", "Praga", "Praha",
)
_CITIES_PATTERN = r"(?:" + "|".join(re.escape(c) for c in _KNOWN_CITIES_SL) + r")"
_PLACE_PUB_RE = re.compile(
    rf"(?P<city>{_CITIES_PATTERN})\s*:\s*(?P<pub>[^,;:]+?)\s*(?:,|;|$)"
)

# Year
_YEAR_RE = re.compile(r"\b(?P<year>(?:19|20)\d{2})\b")

# Author chunk at start: "Firstname Lastname" or "Firstname Middle Lastname"
_NAME_TOKEN = (
    r"(?:[A-ZŠŽČĆĐÖÜÄÁÉÍÓÚÝŇŘĚÔÕÙÇĄĘŁŃÓŚŻŹÅÆØİ]\.|"
    r"[A-ZŠŽČĆĐÖÜÄÁÉÍÓÚÝŇŘĚÔÕÙÇĄĘŁŃÓŚŻŹÅÆØİ]"
    r"[a-zšžčćđöüäáéíóúýňřčëîôõùçßıłęąóżźńśćåæøïëâêûôîäöüÿ'’\-]+)"
)
_AUTHOR_LEAD_RE = re.compile(
    rf"^(?P<name>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,4}})(?P<rest>[,.]\s+.*|\s*$)",
    re.DOTALL,
)


@dataclass
class FootnoteParsed:
    """A single footnote and its parsed citations.

    A footnote can contain multiple citations (semicolon-separated).
    Even short forms (`Ibid.`, `Lastname, op. cit.`) become a citation,
    flagged with is_shortform=True.
    """
    footnote_number: int
    raw: str
    citations: List[ParsedCitation] = field(default_factory=list)
    is_ibid: bool = False
    page_tail: Optional[str] = None      # Page ref at very end of footnote (matches user's signal)
    url_tail: Optional[str] = None       # URL at end of footnote (matches user's signal)


def parse_footnote_citation(chunk: str, is_first_in_footnote: bool = True) -> Optional[ParsedCitation]:
    """Parse a single citation chunk (one of N split by semicolons in a footnote)."""
    text = chunk.strip()
    if not text:
        return None

    # Strip citation prefix
    text = _CITE_PREFIX_RE.sub("", text)
    text = text.strip().rstrip(".")
    if not text:
        return None

    # Ibid → mark as short form, no full data
    if _IBID_RE.match(text):
        cit = ParsedCitation(raw=chunk, citation_type="ibid")
        cit.notes.append("ibid_reference")
        pm = _PAGE_ANYWHERE_RE.search(text)
        if pm:
            cit.pages = pm.group("page")
        return cit

    cit = ParsedCitation(raw=chunk)

    # URL detection
    url_match = _URL_RE.search(text)
    if url_match:
        cit.url = url_match.group(0).rstrip(",.;)]")

    # Page reference (at end is the canonical position)
    page_m = _PAGE_TAIL_RE.search(text)
    if page_m:
        cit.pages = page_m.group("page")
    else:
        page_any = _PAGE_ANYWHERE_RE.search(text)
        if page_any:
            cit.pages = page_any.group("page")

    # Italic-marked title (book or container)
    italics = _ITALIC_RE.findall(text)

    # Slovenian-quoted title (article or chapter)
    sl_quoted = _SL_QUOTED_RE.search(text)
    en_quoted = _EN_QUOTED_RE.search(text)
    quoted_title = sl_quoted.group("text") if sl_quoted else (
        en_quoted.group("text") if en_quoted else None
    )

    # Author at the start
    author_m = _AUTHOR_LEAD_RE.match(text)
    author_text = ""
    if author_m:
        author_text = author_m.group("name").strip()
        # Reject if the "author" is actually italicized or starts a title
        # (this happens when text begins with *Title*)
        if not text.startswith("*"):
            tokens = author_text.split()
            # Single-token = likely a short-form reference like "Foucault, ..."
            if len(tokens) >= 2:
                cit.authors.append(ParsedAuthor(
                    surname=tokens[-1],
                    given=" ".join(tokens[:-1]),
                ))
            elif len(tokens) == 1:
                # Short form: just lastname (op. cit. style or later mention)
                cit.authors.append(ParsedAuthor(surname=tokens[0], given=""))

    # Detect citation type
    if cit.url:
        cit.citation_type = "web-page"
    elif _CHAPTER_MARKER_RE.search(text):
        cit.citation_type = "book-chapter"
    elif quoted_title and italics:
        # Quoted (chapter/article title) + italic (container/journal)
        cit.citation_type = "journal-article"
    elif quoted_title:
        cit.citation_type = "journal-article" if cit.pages else "magazine-article"
    elif italics:
        cit.citation_type = "book"
    else:
        # Short form (lastname, page) — no title, no italics
        cit.citation_type = "ibid"
        cit.notes.append("short_form_reference")

    # Title extraction
    if quoted_title:
        cit.title = quoted_title.strip().rstrip(",;:")
    elif italics:
        # First italic block is the title for book; for chapter it's the container
        if cit.citation_type == "book-chapter":
            cit.container_title = italics[0].strip().rstrip(",;:")
            # The chapter title might be in another italic block before "v:" or in quotes
        elif cit.citation_type == "journal-article":
            cit.container_title = italics[-1].strip().rstrip(",;:")
        else:
            cit.title = italics[0].strip().rstrip(",;:")

    # Subtitle from title `: subtitle`
    if cit.title and ": " in cit.title:
        head, _, tail = cit.title.partition(": ")
        if len(head) >= 3 and len(tail) >= 3:
            cit.title = head.strip()
            cit.subtitle = tail.strip()

    # Place + Publisher
    ppm = _PLACE_PUB_RE.search(text)
    if ppm:
        cit.place = ppm.group("city").strip()
        pub = ppm.group("pub").strip().rstrip(",;:.")
        # Don't capture pure page-refs as publishers
        if pub and not pub.startswith("str.") and not pub.startswith("pp."):
            cit.publisher = pub

    # Year
    ym = _YEAR_RE.search(text)
    if ym:
        try:
            cit.year = int(ym.group("year"))
        except ValueError:
            pass

    # Translator
    tm = _TRANS_RE.search(text)
    if tm:
        cit.translator = tm.group("name").strip()
        if cit.citation_type == "book":
            cit.citation_type = "translation"

    # Editor (ur.)
    ed_m = _EDITOR_RE.search(text)
    if ed_m:
        # Editor name typically comes right before `(ur.)`
        before = text[:ed_m.start()].rstrip()
        # Find last name pattern
        name_m = re.search(
            rf"({_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,2}})\s*$",
            before,
        )
        if name_m:
            ed_name = name_m.group(1).strip()
            tokens = ed_name.split()
            if len(tokens) >= 2:
                cit.editors.append(ParsedAuthor(
                    surname=tokens[-1],
                    given=" ".join(tokens[:-1]),
                    is_editor=True,
                    role="editor",
                ))

    return cit


def parse_footnote_text(footnote_number: int, raw_text: str) -> FootnoteParsed:
    """Parse a single footnote into a FootnoteParsed.

    A footnote may contain multiple citations separated by `;`.
    """
    fn = FootnoteParsed(footnote_number=footnote_number, raw=raw_text)

    # Tail signals: page ref or URL at the very end
    tail_page = _PAGE_TAIL_RE.search(raw_text.rstrip(". "))
    if tail_page:
        fn.page_tail = tail_page.group("page")
    tail_url = _URL_RE.findall(raw_text)
    if tail_url:
        fn.url_tail = tail_url[-1].rstrip(",.;)]")

    # Ibid marker on whole footnote
    if _IBID_RE.match(raw_text.strip()):
        fn.is_ibid = True
        # Still create a citation so downstream knows this is a back-ref
        cit = ParsedCitation(raw=raw_text, citation_type="ibid")
        cit.notes.append("whole_footnote_ibid")
        if tail_page:
            cit.pages = tail_page.group("page")
        fn.citations.append(cit)
        return fn

    # Split on `;` for multiple citations
    chunks = [c.strip() for c in raw_text.split(";") if c.strip()]
    for i, chunk in enumerate(chunks):
        c = parse_footnote_citation(chunk, is_first_in_footnote=(i == 0))
        if c:
            fn.citations.append(c)
    return fn


def parse_markdown_footnotes(md_path: str) -> List[FootnoteParsed]:
    """Open a markdown export, find all `[^N]: ...` footnote definitions, parse each."""
    import unicodedata
    text = Path(md_path).read_text(encoding="utf-8")
    # The markdown export uses NFD-normalised Unicode (combining accents).
    # Our regex character classes assume NFC (single codepoints like š=U+0161).
    text = unicodedata.normalize("NFC", text)

    # Footnote definitions can span multiple lines until the next `[^M]:` or end.
    # Split the text on the footnote-def marker, keeping numbers.
    pattern = re.compile(r"^\[\^(\d+)\]:\s*(.*?)(?=^\[\^\d+\]:|\Z)", re.MULTILINE | re.DOTALL)
    footnotes: List[FootnoteParsed] = []
    for m in pattern.finditer(text):
        num = int(m.group(1))
        body = m.group(2).strip()
        if not body:
            continue
        footnotes.append(parse_footnote_text(num, body))
    return footnotes
