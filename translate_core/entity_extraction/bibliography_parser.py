"""Style-aware bibliography parser.

Handles the Slovenian author-date Chicago variant used in Bojana Kunst's
'Životnje umetnosti' bibliography:

  Author Lastname, Firstname[, Firstname2], YEAR: rest.

Where `rest` shape varies by citation type:
  - Book:           Title, Place[, Place2]: Publisher.
  - Translation:    Title, trans. Translator, Place: Publisher.
  - Book chapter:   'Title,' in: Editor (ed.), Book, Place: Publisher.
  - Journal article: 'Title,' Journal vol/issue [(sub-issue)], [pp.] N-N.
  - Magazine art.:  'Title,' Magazine vol/issue.
  - Web page:       Title, URL, Org, Place, Year [(accessed Date)].
  - Lecture/talk:   'Title,' a lecture at ..., Date.
  - Unpublished:    'Title,' unpublished[, Place, Year].

Multiple authors separated by `and` (or `in` in Slovenian). Editors marked
by `(ed.)` or `(eds.)`.

Returns ParsedCitation dataclasses — no KG side-effects. The bilingual TM
matcher and ingester live in separate modules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------

CITATION_TYPES = (
    "book", "book-chapter", "journal-article", "magazine-article",
    "web-page", "unpublished-paper", "interview", "exhibition-catalogue",
    "lecture", "report", "thesis", "documentary", "blog-post",
    "translation",  # marker: original of foreign work, SL/EN translation cited
)


@dataclass
class ParsedAuthor:
    surname: str
    given: str
    is_editor: bool = False
    role: str = "author"   # "author" | "editor" | "translator" | "interviewer"

    @property
    def full_name(self) -> str:
        return f"{self.given} {self.surname}".strip()


@dataclass
class ParsedCitation:
    raw: str
    authors: List[ParsedAuthor] = field(default_factory=list)
    year: Optional[int] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    container_title: Optional[str] = None   # journal name, book name (for chapter), magazine, etc.
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    place: Optional[str] = None              # primary publisher city
    places: List[str] = field(default_factory=list)  # all cities
    publisher: Optional[str] = None
    translator: Optional[str] = None
    editors: List[ParsedAuthor] = field(default_factory=list)
    url: Optional[str] = None
    accessed: Optional[str] = None
    citation_type: str = "book"
    notes: List[str] = field(default_factory=list)

    @property
    def primary_author_surname(self) -> Optional[str]:
        return self.authors[0].surname if self.authors else None

    @property
    def display_title(self) -> str:
        t = self.title or "(untitled)"
        if self.subtitle:
            t = f"{t}: {self.subtitle}"
        return t


# --------------------------------------------------------------------------
# Author parsing
# --------------------------------------------------------------------------

# `Lastname, Firstname` form, possibly multi-part lastname like "Russell Hochschild"
# International char support: Slavic, Turkish (ı, İ), Polish (ł, ę, ą, ó, ż, ź),
# German (ä, ö, ü, ß), Romance accents, Czech/Slovak (č, ř, ď, ť, ň), Hungarian (ő, ű)
_NAME_TOKEN = (
    r"(?:[A-ZŠŽČĆĐÖÜÄÁÉÍÓÚÝŇŘĚÔÕÙÇĄĘŁŃÓŚŻŹÅÆØİ]"
    r"\.|"
    r"[A-ZŠŽČĆĐÖÜÄÁÉÍÓÚÝŇŘĚÔÕÙÇĄĘŁŃÓŚŻŹÅÆØİ]"
    r"[a-zšžčćđöüäáéíóúýňřčëîôõùçßıłęąóżźńśćåæøïëâêûôîäöüÿ'’\-]+)"
)
_NAME_TOKENS = rf"{_NAME_TOKEN}(?:\s+{_NAME_TOKEN})*"

_AUTHOR_LASTFIRST_RE = re.compile(
    rf"^(?P<last>{_NAME_TOKENS})\s*,\s+(?P<first>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN})*)\s*$"
)

# Separator: `and` (EN) or `in` (SL meaning 'and') used between authors
_AUTHOR_SEP_RE = re.compile(r"\s+(?:and|in)\s+", re.IGNORECASE)

_ED_MARKER_RE = re.compile(r"\s*\((?:eds?\.|ur\.)\)\s*$")


def parse_authors(text: str) -> List[ParsedAuthor]:
    """Parse the author segment of a citation entry.

    Examples:
      `Ahmed, Sara`
      `Adolphs, Stephan and Karakayalı, Serhat`
      `Sharp, Hasana and Taylor, Chloë (eds.)`
      `Russell Hochschild, Arlie`
      `Tuck, Eve, in K. Wayne Yang`
    """
    text = text.strip()
    is_editors = False
    if _ED_MARKER_RE.search(text):
        is_editors = True
        text = _ED_MARKER_RE.sub("", text)

    out: List[ParsedAuthor] = []

    # Split on " and " / " in " — but the SL `in` is also a preposition,
    # so prefer English "and" when ambiguous. Heuristic: split, then verify
    # each chunk looks like `Lastname, Firstname`.
    raw_chunks = _AUTHOR_SEP_RE.split(text)
    chunks: List[str] = []
    for chunk in raw_chunks:
        chunks.append(chunk.strip())

    # If the rejoin doesn't yield N>=2 valid `Lastname, Firstname` forms,
    # treat as one author.
    parsed_chunks: List[Optional[ParsedAuthor]] = []
    for c in chunks:
        m = _AUTHOR_LASTFIRST_RE.match(c)
        if m:
            parsed_chunks.append(ParsedAuthor(
                surname=m.group("last").strip(),
                given=m.group("first").strip(),
                is_editor=is_editors,
                role="editor" if is_editors else "author",
            ))
        else:
            parsed_chunks.append(None)

    # If all chunks parsed, use them
    if all(p is not None for p in parsed_chunks) and parsed_chunks:
        return [p for p in parsed_chunks if p]

    # Otherwise, fall back to single-author parse on whole text
    m = _AUTHOR_LASTFIRST_RE.match(text)
    if m:
        return [ParsedAuthor(
            surname=m.group("last").strip(),
            given=m.group("first").strip(),
            is_editor=is_editors,
            role="editor" if is_editors else "author",
        )]

    # Last-ditch: keep whatever did parse + flag the failure
    # If even one parsed correctly, return it + note unmatched chunks
    valid = [p for p in parsed_chunks if p]
    if valid:
        return valid

    # Otherwise: take first `Lastname, Firstname` we can find
    m = re.search(rf"({_NAME_TOKEN}),\s+({_NAME_TOKEN})", text)
    if m:
        return [ParsedAuthor(
            surname=m.group(1).strip(),
            given=m.group(2).strip(),
            is_editor=is_editors,
            role="editor" if is_editors else "author",
        )]

    # Truly last-ditch: store whole text as surname so we don't lose data
    return [ParsedAuthor(
        surname=text.strip(),
        given="",
        is_editor=is_editors,
        role="editor" if is_editors else "author",
    )]


# --------------------------------------------------------------------------
# Body parsing
# --------------------------------------------------------------------------

# The author-year-colon prefix: `Lastname, Firstname[, Firstname2[, ...] [and/in Lastname, Firstname]]+, YEAR[a-z]?:` then body
# We match this with re.search not match because some entries have prefix variations.

# A cleaner approach: try to find `, YYYY[a-z]?\s*:` to split prefix from body.
_PREFIX_YEAR_RE = re.compile(
    r"^(?P<authors>.+?)\s*,\s*(?P<year>(?:19|20)\d{2})(?P<suffix>[a-z])?\s*:\s+(?P<body>.+)$",
    re.DOTALL,
)

# URL detection
_URL_RE = re.compile(r"https?://[^\s,]+")

# Page range (require explicit pp./str./p. prefix OR a clear digit-dash-digit form)
_PAGES_RE = re.compile(
    r"(?:^|\b)(?:pp?\.|str\.)\s*(?P<range>\d+(?:\s*[-–]\s*\d+)?)\b|"
    r"\b(?P<dashed>\d{1,4}[-–]\d{1,4})\b(?:\s*[.,]|\s*$)"
)

# Translator: "trans. Name Surname" or "translated by Name Surname"
_TRANSLATOR_RE = re.compile(
    rf"\btrans(?:lated\s+by|\.)\s+(?P<name>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,2}})"
)

# Editor inside body: "Editor Name (ed.)," or "Editor Name and Editor (eds.),"
_INLINE_EDITOR_RE = re.compile(
    rf"\b(?P<name>{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,3}})"
    r"\s*\((?:ed\.|eds\.|ur\.)\)"
)

# Title in single or curly quotes (article/chapter title)
_QUOTED_TITLE_RE = re.compile(
    r"['‘‘‹„\"](?P<title>[^'’’›‘\"]{4,200}?)['’’›‘\"]"
)

# Web-page indicators
_WEB_INDICATORS = ("https://", "http://")

# Lecture/conference indicators
_LECTURE_INDICATORS = (
    "a lecture at", "lecture at", "talk at", "presentation at",
    "predavanje na", "predstavitev na", "konferenc",
)

# Unpublished indicators
_UNPUB_INDICATORS = ("unpublished", "neobjavljen", "tipkopis", "manuscript")

# City list pattern — 1-3 capitalised words separated by commas
_CITY = rf"{_NAME_TOKEN}(?:\s+{_NAME_TOKEN})*"
_CITY_LIST = rf"{_CITY}(?:\s*,\s*{_CITY})*"

# Trailing-place-publisher pattern: `..., City1[, City2]: Publisher.` at end of body.
# Limit places to max 3 cities — multi-city imprints (4+) are rare; trading
# off recall on those for better precision on titles with subtitle words.
_TRAILING_PUB_RE = re.compile(
    rf",\s*(?P<places>{_CITY}(?:\s*,\s*{_CITY}){{0,2}})\s*:\s*"
    rf"(?P<pub>[^:]+?)\s*\.?\s*$"
)

# Trailing page reference: ", p. 56" or ", pp. 56-78" or ", str. 56"
_TRAILING_PAGE_RE = re.compile(
    r",\s*(?:pp?\.|str\.)\s*\d+(?:\s*[-–]\s*\d+)?\s*\.?\s*$"
)

# Legacy: place: publisher pattern for embedded matches (chapter case)
_PLACE_PUB_RE = re.compile(
    rf"(?P<places>{_CITY_LIST})\s*:\s*(?P<pub>[^.]+?)(?:\.|$)"
)


def parse_body(body: str, citation: ParsedCitation) -> None:
    """Populate citation fields from the body after the author-year prefix.

    Mutates `citation` in place. Detects citation_type as a side-effect.
    """
    body = body.strip().rstrip(".").strip()

    # Strip trailing page ref (`, p. 281` / `, pp. 10-19` / `, str. 56`)
    # so it doesn't leak into the publisher field.
    page_match = _TRAILING_PAGE_RE.search(body)
    if page_match:
        # Capture pages before stripping
        pg = re.search(r"(?:pp?\.|str\.)\s*(\d+(?:\s*[-–]\s*\d+)?)", page_match.group(0))
        if pg:
            citation.pages = pg.group(1)
        body = body[:page_match.start()].rstrip(" .")

    # URL → web page
    url_match = _URL_RE.search(body)
    if url_match:
        citation.url = url_match.group(0).rstrip(",;.")
        citation.citation_type = "web-page"

    # Translator marker — strong signal
    tm = _TRANSLATOR_RE.search(body)
    if tm:
        citation.translator = tm.group("name").strip()
        if citation.citation_type == "book":
            citation.citation_type = "translation"

    # Quoted title → article or chapter
    qm = _QUOTED_TITLE_RE.search(body)
    has_quoted_title = qm is not None
    if qm:
        citation.title = qm.group("title").strip().rstrip(",;:")
        # Track what came after the quoted title
        after_title = body[qm.end():].strip(" ,.;:")
    else:
        after_title = body

    # Detect lecture / unpublished
    low = body.lower()
    if any(k in low for k in _UNPUB_INDICATORS):
        citation.citation_type = "unpublished-paper"
    elif any(k in low for k in _LECTURE_INDICATORS):
        citation.citation_type = "lecture"
    elif " in: " in body or " v: " in body:
        citation.citation_type = "book-chapter"
    elif has_quoted_title:
        # Heuristic: quoted title + journal-vol form
        # `Social Text 79/22 (2), pp. 117-139` style
        if re.search(r"\b[A-Z][^,]+\s+\d+/\d+", after_title) or "journal" in low:
            citation.citation_type = "journal-article"
        elif re.search(r"\b[A-Z][^,]+\s+\d+", after_title):
            citation.citation_type = "magazine-article"
        elif "blog" in low:
            citation.citation_type = "blog-post"

    # Detect book chapter editor + container
    if citation.citation_type == "book-chapter":
        em = _INLINE_EDITOR_RE.search(body)
        if em:
            edn = em.group("name").strip()
            tokens = edn.split()
            if len(tokens) >= 2:
                citation.editors.append(ParsedAuthor(
                    surname=tokens[-1],
                    given=" ".join(tokens[:-1]),
                    is_editor=True,
                    role="editor",
                ))
        # Container title: the part after `in: Editor (ed.),`
        cmatch = re.search(
            r"(?:in:|v:)\s*[^,]+?\([^)]+\)\s*,\s*(?P<container>[^,]+?)\s*,\s*[A-Z]",
            body,
        )
        if cmatch:
            citation.container_title = cmatch.group("container").strip().rstrip(",;:")

    # Volume/issue extraction for journals: `Journal 79/22 (2), pp. X-Y` or `Journal vol/issue`
    if citation.citation_type in ("journal-article", "magazine-article"):
        vm = re.search(
            r"(?P<journal>[A-Z][^,]+?)\s+(?P<vol>\d+(?:[.-]\d+)?)\s*/\s*(?P<issue>\d+[a-z]?)(?:\s*\((?P<sub>[^)]+)\))?",
            after_title,
        )
        if vm:
            citation.container_title = vm.group("journal").strip(",;:")
            citation.volume = vm.group("vol")
            citation.issue = vm.group("issue")
            if vm.group("sub"):
                citation.notes.append(f"sub-issue: {vm.group('sub')}")

    # Pages — require explicit "pp." / "str." / clear range marker
    for pm in _PAGES_RE.finditer(after_title):
        rng = pm.group("range") or pm.group("dashed")
        if rng:
            citation.pages = rng
            break

    # Strip the trailing-publisher block FIRST (it's always at the right end),
    # then everything left is title (plus possibly translator).
    body_no_pub = body
    if citation.citation_type in ("book", "book-chapter", "translation"):
        # Find ALL `, City[, City2]: Publisher` matches and take the RIGHTMOST.
        candidates = list(_TRAILING_PUB_RE.finditer(body))
        # Filter to those that end at body end (allowing trailing `.`)
        candidates = [
            m for m in candidates
            if m.end() >= len(body.rstrip(" ."))
        ]
        if candidates:
            pm = candidates[-1]
            places_raw = pm.group("places").strip()
            pub = pm.group("pub").strip()
            if (len(pub) < 120
                and "/" not in pub
                and " in " not in pub.lower()
                and not pub.startswith("p.")
                and not pub.startswith("pp.")):
                citation.places = [p.strip() for p in places_raw.split(",")]
                citation.place = citation.places[0] if citation.places else None
                citation.publisher = pub.rstrip(",;:. ")
                body_no_pub = body[:pm.start()].rstrip(" ,.")

    # Title for non-quoted forms (books, translations, web-pages)
    if not citation.title:
        title_text = body_no_pub
        # Strip URL block if present (web-page case)
        if url_match:
            title_text = title_text[:url_match.start()].rstrip(" ,.")
        # Strip translator clause (`, trans. Name,`)
        tm2 = _TRANSLATOR_RE.search(title_text)
        if tm2:
            title_text = title_text[:tm2.start()].rstrip(" ,.") + title_text[tm2.end():].rstrip()
        citation.title = title_text.strip().rstrip(",;:.")

    # Split title into title / subtitle on first `:` if applicable
    if citation.title and ": " in citation.title:
        head, _, tail = citation.title.partition(": ")
        # Heuristic: only treat as subtitle if both halves are non-trivial
        if len(head) >= 3 and len(tail) >= 3:
            citation.title = head.strip()
            citation.subtitle = tail.strip()


def parse_bibliography_entry(text: str) -> Optional[ParsedCitation]:
    """Parse a single bibliography paragraph into a structured ParsedCitation.

    Returns None if the text doesn't look like a citation.
    """
    text = text.strip()
    if not text or len(text) < 12:
        return None

    m = _PREFIX_YEAR_RE.match(text)
    if not m:
        return None

    authors_raw = m.group("authors").strip()
    year = int(m.group("year"))
    body = m.group("body").strip()

    cit = ParsedCitation(raw=text, year=year)
    cit.authors = parse_authors(authors_raw)
    parse_body(body, cit)
    return cit


# --------------------------------------------------------------------------
# Whole-document parsing
# --------------------------------------------------------------------------

BIBLIO_HEADER_VARIANTS = {
    "list of references", "references", "bibliography", "bibliografija",
    "literatura", "literature cited", "works cited",
}


def parse_docx_bibliography(docx_path: str) -> List[ParsedCitation]:
    """Open a .docx, locate the bibliography header, parse entries.

    Returns the list of parsed citations. Skips entries that fail to parse
    (logged via the .notes field of nothing — caller can re-scan).
    """
    import docx

    doc = docx.Document(docx_path)
    paragraphs = [p.text.strip() for p in doc.paragraphs]

    # Find the bibliography start
    start = None
    for i, p in enumerate(paragraphs):
        if p.lower() in BIBLIO_HEADER_VARIANTS:
            start = i + 1
            break

    if start is None:
        # Fallback: scan for the first paragraph that matches `Lastname, Firstname, YYYY:`
        for i, p in enumerate(paragraphs):
            if _PREFIX_YEAR_RE.match(p) and i > 50:
                start = i
                break

    if start is None:
        return []

    citations: List[ParsedCitation] = []
    last_cit: Optional[ParsedCitation] = None
    for i in range(start, len(paragraphs)):
        para = paragraphs[i].strip()
        if not para:
            continue
        cit = parse_bibliography_entry(para)
        if cit is None:
            # Might be a continuation of the previous entry
            if last_cit and len(para) < 250:
                last_cit.raw += " " + para
                # Re-parse with extended body
                merged = parse_bibliography_entry(last_cit.raw)
                if merged:
                    last_cit_idx = citations.index(last_cit)
                    citations[last_cit_idx] = merged
                    last_cit = merged
            continue
        citations.append(cit)
        last_cit = cit

    return citations
