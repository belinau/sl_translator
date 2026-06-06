"""Per-segment auto-classification for TM entries.

Two-pass rule-based classifier. Pass 1 assigns each segment a local label
from features; pass 2 smooths labels based on neighbour context (so a
borderline segment surrounded by confident bibliography_entry neighbours
gets pulled in).

No ML. No manual config. Purely signals derivable from segment text + its
position within its origin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Sequence


class SegmentClass(str, Enum):
    BOOK_METADATA = "book_metadata"        # front matter: author line, title line
    BODY_TEXT = "body_text"                # prose body
    INLINE_CITATION = "inline_citation"    # body text containing a cite
    BIBLIOGRAPHY_ENTRY = "bibliography_entry"
    FOOTNOTE = "footnote"                  # short structured cite (op. cit., ibid., page-only)
    ARTWORK_RECORD = "artwork_record"      # title, year, medium
    ARTIST_HEADER = "artist_header"        # ALL-CAPS artist name
    FESTIVAL_CREDIT = "festival_credit"    # "Role: Name" pairs
    EVENT_METADATA = "event_metadata"      # date/time/venue
    INSTITUTION_LINE = "institution_line"  # standalone institution mention
    NOISE = "noise"                        # page nums, fragments


# Publisher cities seen in bibliographies
PUBLISHER_CITIES = {
    "Ljubljana", "Zagreb", "Beograd", "Belgrade", "Sarajevo", "Skopje", "Maribor",
    "London", "New York", "Cambridge", "Oxford", "Berlin", "Wien", "Vienna",
    "Paris", "Frankfurt", "Boston", "Chicago", "Stanford", "Princeton",
    "Minneapolis", "Durham", "Bloomington", "Ithaca", "Cambridge, MA",
    "München", "Munich", "Köln", "Cologne", "Hamburg", "Madrid", "Barcelona",
    "Roma", "Rome", "Milano", "Milan", "Amsterdam", "Rotterdam", "Antwerp",
    "Helsinki", "Stockholm", "Copenhagen", "Oslo", "Warsaw", "Prague", "Praha",
    "Budapest", "Bratislava",
}

# Known SL + EN academic / humanities publishers
KNOWN_PUBLISHERS = {
    # SL
    "Maska", "Studia Humanitatis", "Studia humanitatis", "Cankarjeva založba",
    "Cankarjeva", "Modrijan", "Sodobnost", "Beletrina", "Mladinska knjiga",
    "DZS", "Državna založba", "Znanstvena založba", "ZRC", "ZRC SAZU", "SAZU",
    "KUD Apokalipsa", "KUD France Prešeren", "KUD", "Mihelač", "Apex",
    "Nova revija", "Pekinpah", "Sophia", "*cf.", "Krtina", "OPRO",
    "Založba /*cf.", "Založba *cf.", "Hyperion", "Hieron",
    # EN/international (humanities & art)
    "Routledge", "MIT Press", "Verso", "Polity", "Sage", "Duke University Press",
    "Stanford University Press", "Princeton University Press", "Cornell University Press",
    "Columbia University Press", "Oxford University Press", "Cambridge University Press",
    "University of Chicago Press", "University of Minnesota Press",
    "Indiana University Press", "Sternberg", "Sternberg Press", "Semiotext(e)",
    "Continuum", "Bloomsbury", "Palgrave", "Macmillan", "Penguin", "Vintage",
    "Faber", "Faber and Faber", "Zone Books", "Zone", "Black Dog Publishing",
    "Phaidon", "Thames & Hudson", "Thames and Hudson", "Hatje Cantz",
    "MoMA", "Tate", "Walker Art Center", "Whitechapel",
}

# Role keywords (case-insensitive) that mark a festival/credit line
ROLE_KEYWORDS = (
    "choreograph", "danc", "curator", "perform", "photograph", "compos",
    "direct", "translat", "author", "editor", "designer", "design",
    "illustrat", "moderat", "founder", "produc", "writer", "wrote",
    "artist", "voice coach", "lighting", "costume", "costumes", "costumography",
    "kostumograf", "scenograf", "set design", "sound design", "music",
    "glasba", "ples", "koreograf", "kurator", "izvajalec", "izvajalka",
    "asistent", "assistance", "assistant", "executive", "izvršn",
    "administrat", "production manager", "dramaturg", "režij", "režiser",
    "videograph", "video", "graphic design", "oblikovanje",
)

# Medium phrases for artworks
MEDIUM_PATTERN = re.compile(
    r"\b(?:oil on canvas|acrylic on canvas|mixed media|digital print|"
    r"olje na platnu|akril na platnu|barvni tisk|fotografija|installation|"
    r"instalacija|video|performance|sculpture|skulptura|"
    r"super 8|c-print|gelatin silver print|inkjet print|"
    r"digitiz(?:ed|ised) super 8|graphite on paper|charcoal|"
    r"graphite|ink on paper|watercolou?r)\b",
    re.IGNORECASE,
)

# A bare-year pattern like ", 2004" or " 2004" (artwork dating)
BARE_YEAR_RE = re.compile(r"(?:,|\s)\s*(\d{4})(?:[\s,)\]]|$)")
PAREN_YEAR_RE = re.compile(r"\((\d{4}(?:[-–]\d{2,4})?)\)")
# Any 19xx or 20xx year, anywhere
ANY_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
# Lastname-comma: a strong citation signal — "Foucault, ..." or "Author, ..."
LASTNAME_COMMA_RE = re.compile(
    r"\b[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]{2,}\s*,\s+[A-ZŠŽČĆĐ]"
)
# Lastname-first form: `Lastname, Firstname,` — common in bibliographies
LASTNAME_FIRST_RE = re.compile(
    r"^([A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]{2,})\s*,\s+"
    r"([A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+)?)\s*,"
)
# op./ibid./cf./prim./glej markers (footnote signals)
FOOTNOTE_MARKER_RE = re.compile(
    r"\b(?:op\.\s*cit\.|ibid\.?|ibidem|loc\.\s*cit\.|cit\.|prav tam|nav\.\s*delo|"
    r"prim\.|cf\.|glej tudi|see also)\b",
    re.IGNORECASE,
)
# Page references
PAGE_REF_RE = re.compile(r"\b(?:pp?\.\s*\d+|str\.\s*\d+\b|p\.\s*\d+\b|n\.\s*\d+\b)")
# (Author Year) parenthetical inline citation
INLINE_CITE_PAREN_RE = re.compile(
    r"\(\s*[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+"
    r"(?:\s+(?:and|in|et\s+al\.?|&)\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+)?"
    r"\s+(?:19|20)\d{2}\b"
)
# Strip leading citation prefixes
LEADING_PREFIX_RE = re.compile(
    r"^(?:[\d]+\s+)?"  # optional leading footnote number
    r"(?:See also|See e\.?g\.?|See|Cf\.?|Cf|Prim\.?|Prim|Glej tudi|Glej npr\.?|Glej|"
    r"Cit\.\s*po|Cit\.|As|Po|Pri|For an analysis,?\s+see|On|Following|"
    r"In|Po besedah|According to|Kot piše|Kot pravi)\s+",
    re.IGNORECASE,
)

# Capitalized FirstName LastName (handles Slavic diacritics)
CAP_NAME_RE = re.compile(
    r"\b([A-ZŠŽČĆĐÖÜÄ][a-zšžčćđöüäáéíóúýň]+(?:\s+[A-ZŠŽČĆĐÖÜÄ][a-zšžčćđöüäáéíóúýň]+){1,3})\b"
)

# Standalone capitalised name segment: just the name, optionally a colon
STANDALONE_NAME_RE = re.compile(
    r"^[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+){1,3}:?\s*$"
)

ALL_CAPS_NAME_RE = re.compile(
    r"^(?:[A-ZŠŽČĆĐ]{2,}\s*){1,3}[A-ZŠŽČĆĐ]{2,}\s*$"
)

# Date/time patterns for event metadata
DATE_PATTERNS = [
    re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+\d+", re.IGNORECASE),
    re.compile(r"\b(?:January|February|March|April|May|June|July|August|"
               r"September|October|November|December)\s+\d+", re.IGNORECASE),
    re.compile(r"\b(?:Pon|Tor|Sre|Čet|Pet|Sob|Ned)[a-z]*,?\s+\d+", re.IGNORECASE),
    re.compile(r"\b(?:januar|februar|marec|april|maj|junij|julij|avgust|"
               r"september|oktober|november|december)\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?:\s*(?:a\.m\.|p\.m\.|h|ure))?\b", re.IGNORECASE),
    re.compile(r"\bob\s+\d{1,2}[\.:]?\d{0,2}\b"),
]

VENUE_KEYWORDS = (
    "lobby", "avla", "foyer", "Hall", "dvorana", "gallery", "galerija",
    "Museum", "muzej", "Theatre", "Theater", "gledališče", "Studio", "Studio",
    "Park", "Park", "Center", "Centre", "Centre", "Square", "Trg",
    "Cankarjev dom", "Kino Šiška", "Tivoli", "Cinema", "Kino", "Bar", "Klub", "Club",
)

INSTITUTION_PATTERN = re.compile(
    r"\b((?:[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+(?:\s+|[-–]))+"
    r"(?:Gallery|Galleries|Galerija|Museum|Muzej|Festival|Biennale|Bienale|"
    r"University|Univerza|Institute|Inštitut|Centre|Center|Press|Theatre|Theater|"
    r"Gledališče|Academy|Akademija|School|Šola|College|Library|Knjižnica|"
    r"Archive|Arhiv|Foundation|Fundacija|Association|Asociacija|Collective|"
    r"Kolektiv|Cooperative|Productions|Publishing|Publishers|Založba))\b"
)

# Bibliography-shape indicator: presence of comma-separated structure with
# year + capitalised words. Author, Title (City: Publisher, Year), ...
BIBLIO_SHAPE_RE = re.compile(
    r"[A-ZŠŽČĆĐ][a-zšžčćđöüä]+\s+[A-ZŠŽČĆĐ][a-zšžčćđöüä]+\s*,"
    r".{5,200}?[(,]\s*\d{4}\s*[),]"
)

NOISE_RE = re.compile(r"^[\d\s\-–.,;:()xX×§\[\]/\\]*$")


@dataclass
class SegmentLabel:
    idx: int
    src: str
    tgt: str
    origin: str
    klass: SegmentClass
    confidence: float                     # 0..1, classifier's confidence
    features: dict = field(default_factory=dict)


def _has_role_keyword(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in ROLE_KEYWORDS)


def _publisher_city_hit(text: str) -> str | None:
    for c in PUBLISHER_CITIES:
        if c in text:
            # Cities should be followed by ":" or appear inside parens for biblio
            idx = text.find(c)
            after = text[idx + len(c): idx + len(c) + 3]
            if ":" in after or text[max(0, idx - 1)] == "(":
                return c
            if c in text and len(text) < 400:
                # In short segments, even a bare city often indicates biblio
                return c
    return None


def _publisher_hit(text: str) -> str | None:
    for p in KNOWN_PUBLISHERS:
        if p in text:
            return p
    return None


def _is_event_metadata(text: str) -> bool:
    hits = sum(1 for r in DATE_PATTERNS if r.search(text))
    if hits >= 1 and len(text) < 200:
        return True
    if any(v in text for v in VENUE_KEYWORDS) and len(text) < 120:
        return True
    return False


def _looks_like_artwork_record(text: str) -> bool:
    bare_year_m = BARE_YEAR_RE.search(text)
    if not bare_year_m:
        return False
    has_medium = bool(MEDIUM_PATTERN.search(text))
    if not (has_medium or len(text) < 220):
        return False
    if not re.match(r"^[A-ZŠŽČĆĐ][a-zšžčćđöüä]", text):
        return False
    words_before_year = text[:bare_year_m.start()].split()
    return len(words_before_year) <= 8


def _looks_like_bibliography(text: str) -> bool:
    """Structural citation signals — at least two required, one must be strong.

    Body prose freely mentions years and publisher names, so neither a year
    nor a bare KNOWN_PUBLISHERS substring (alone or together) is enough to
    flip a segment to BIBLIOGRAPHY_ENTRY — the typed pipeline would then
    pack every Title-Case span into a single citation. Require at least
    one structural signal (lastname-comma, page-ref, `City:` publisher
    city, biblio-shape regex, or footnote marker) AND at least two signals
    total counting weak ones (year, publisher name).
    """
    strong = 0
    if LASTNAME_COMMA_RE.search(text):
        strong += 1
    if PAGE_REF_RE.search(text):
        strong += 1
    if _publisher_city_hit(text):
        strong += 1
    if BIBLIO_SHAPE_RE.search(text):
        strong += 1
    if FOOTNOTE_MARKER_RE.search(text):
        strong += 1
    if strong == 0:
        return False
    weak = 0
    if ANY_YEAR_RE.search(text):
        weak += 1
    if _publisher_hit(text):
        weak += 1
    return strong + weak >= 2


def _looks_like_footnote(text: str) -> bool:
    """Short structured citation: op. cit., Ibid., n. 5, just page nums after lastname.

    Footnotes provide references to already-cited works. Capturing them here
    lets the extractor link them to canonical refs later (or at minimum,
    surface them as candidates for the review queue).
    """
    if len(text) > 250:
        return False
    if FOOTNOTE_MARKER_RE.search(text):
        return True
    # `Lastname, ..., p. N` short-form
    if len(text) < 150 and LASTNAME_COMMA_RE.search(text) and PAGE_REF_RE.search(text):
        return True
    # Short bare 'Foucault 1989: 56'-style
    if len(text) < 100:
        if re.search(
            r"^[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+\s+\d{4}\s*:?\s*\d+",
            text,
        ):
            return True
    return False


def _looks_like_inline_cite_dense(text: str) -> bool:
    """Body-length text containing >=1 (Author Year) parenthetical."""
    return bool(INLINE_CITE_PAREN_RE.search(text))


def _looks_like_standalone_name(text: str) -> bool:
    return bool(STANDALONE_NAME_RE.match(text.strip()))


def _classify_local(label: SegmentLabel, total: int) -> None:
    """Pass 1: assign initial class from segment-local features.
    Mutates label.klass / label.confidence / label.features."""
    src_raw = label.src
    # Strip leading citation prefixes ("See", "Glej", footnote numbers) for
    # classification purposes — they shouldn't shift a citation to body_text.
    src = LEADING_PREFIX_RE.sub("", src_raw, count=1).lstrip()
    n = len(src)
    feats = {
        "len_src": n,
        "len_tgt": len(label.tgt),
        "rel_pos": label.idx / max(1, total - 1),
    }

    if NOISE_RE.match(src) or n < 4:
        label.klass = SegmentClass.NOISE
        label.confidence = 0.95
        label.features = feats
        return

    is_short = n < 80
    is_very_short = n < 40
    is_long = n >= 200

    has_role = _has_role_keyword(src)
    biblio = _looks_like_bibliography(src)
    is_footnote = _looks_like_footnote(src)
    inline_dense = _looks_like_inline_cite_dense(src)
    pub = _publisher_hit(src)
    pub_city = _publisher_city_hit(src)
    paren_year = PAREN_YEAR_RE.search(src)
    bare_year = BARE_YEAR_RE.search(src)
    is_artwork = _looks_like_artwork_record(src)
    is_standalone_name = _looks_like_standalone_name(src)
    is_all_caps = bool(ALL_CAPS_NAME_RE.match(src.strip()))
    has_institution = bool(INSTITUTION_PATTERN.search(src))
    is_event = _is_event_metadata(src)

    feats.update({
        "has_role_kw": has_role,
        "biblio_shape": biblio,
        "is_footnote_shape": is_footnote,
        "inline_dense": inline_dense,
        "publisher_hit": pub,
        "publisher_city_hit": pub_city,
        "paren_year": bool(paren_year),
        "bare_year": bool(bare_year),
        "is_artwork_shape": is_artwork,
        "is_standalone_name": is_standalone_name,
        "is_all_caps": is_all_caps,
        "has_institution_token": has_institution,
        "is_event_meta": is_event,
        "is_short": is_short,
        "is_long": is_long,
        "has_lastname_comma": bool(LASTNAME_COMMA_RE.search(src)),
        "has_any_year": bool(ANY_YEAR_RE.search(src)),
        "has_page_ref": bool(PAGE_REF_RE.search(src)),
    })
    label.features = feats

    # --- DECISION ORDER (most specific first) ---

    # Footnote (short structured cite): higher priority than biblio_entry
    # because it's a more specific shape.
    if is_footnote and is_short:
        label.klass = SegmentClass.FOOTNOTE
        label.confidence = 0.8
        return

    # Bibliography entry: structured citation
    if biblio:
        label.klass = SegmentClass.BIBLIOGRAPHY_ENTRY
        label.confidence = 0.85 if (pub and pub_city) else 0.72
        return

    # Dense inline citations in body-length text → INLINE_CITATION
    if inline_dense and n >= 80:
        label.klass = SegmentClass.INLINE_CITATION
        label.confidence = 0.75
        return

    # Festival credit: role keyword in a short segment, often ends with colon
    if has_role and is_short:
        # But not if it's actually a long-form attribution sentence
        if src.rstrip().endswith(":") or src.count(":") >= 1:
            label.klass = SegmentClass.FESTIVAL_CREDIT
            label.confidence = 0.85
            return
        label.klass = SegmentClass.FESTIVAL_CREDIT
        label.confidence = 0.65
        return

    # Event metadata: date/time/venue
    if is_event:
        label.klass = SegmentClass.EVENT_METADATA
        label.confidence = 0.85
        return

    # Standalone person name (likely book front-matter author or artist header)
    if is_standalone_name:
        # Disambiguate: ALL CAPS → artist; Title Case → book metadata candidate
        if is_all_caps:
            label.klass = SegmentClass.ARTIST_HEADER
            label.confidence = 0.8
        else:
            label.klass = SegmentClass.BOOK_METADATA
            # Confidence higher if we're near the beginning of the origin
            label.confidence = 0.78 if feats["rel_pos"] < 0.1 else 0.55
        return

    # Artist header in all-caps (e.g. "ANA SLUGA")
    if is_all_caps and is_short:
        label.klass = SegmentClass.ARTIST_HEADER
        label.confidence = 0.78
        return

    # Artwork record: title + year + maybe medium
    if is_artwork:
        label.klass = SegmentClass.ARTWORK_RECORD
        label.confidence = 0.75
        return

    # Institution-only line
    if has_institution and is_short and not has_role:
        label.klass = SegmentClass.INSTITUTION_LINE
        label.confidence = 0.7
        return

    # Inline citation: body-length text with a citation shape inside
    if is_long and (pub_city or pub or paren_year):
        if BIBLIO_SHAPE_RE.search(src):
            label.klass = SegmentClass.INLINE_CITATION
            label.confidence = 0.72
            return

    # Body text: long prose
    if is_long:
        label.klass = SegmentClass.BODY_TEXT
        label.confidence = 0.8
        return

    # Medium segments without other signals → likely body or fragmented text
    if 80 <= n < 200:
        if paren_year and CAP_NAME_RE.search(src):
            # Often a mid-length inline cite
            label.klass = SegmentClass.INLINE_CITATION
            label.confidence = 0.5
            return
        label.klass = SegmentClass.BODY_TEXT
        label.confidence = 0.55
        return

    # Very short / short with no signal → noise candidate (rule-of-thumb)
    if is_very_short:
        label.klass = SegmentClass.NOISE
        label.confidence = 0.4
        return

    label.klass = SegmentClass.BODY_TEXT
    label.confidence = 0.3


def _smooth_pass(labels: List[SegmentLabel]) -> None:
    """Pass 2: contextual smoothing.

    Heuristics:
    - A weak biblio_entry surrounded by confident biblio_entries gets promoted.
    - Festival_credit only sticks when it appears in a cluster of >=3 in a row.
      Isolated role-keyword short segments default to body_text.
    - Standalone names following each other near origin start → book_metadata.
    - Artist headers immediately preceded by all-caps → keep both as headers.
    """
    n = len(labels)
    if n == 0:
        return

    # Build cluster runs of FESTIVAL_CREDIT
    i = 0
    while i < n:
        j = i
        while j < n and labels[j].klass == SegmentClass.FESTIVAL_CREDIT:
            j += 1
        run_len = j - i
        if run_len > 0 and run_len < 3:
            # Isolated credit-like segments — likely false positive
            for k in range(i, j):
                # Demote unless it had a role colon (strong signal)
                if not labels[k].features.get("has_role_kw"):
                    continue
                if labels[k].src.rstrip().endswith(":"):
                    # Strong: keep as festival_credit
                    continue
                # Demote to body_text
                labels[k].klass = SegmentClass.BODY_TEXT
                labels[k].confidence = max(0.3, labels[k].confidence * 0.6)
        i = max(j, i + 1)

    # Bibliography region detection: a contiguous run where >50% are biblio_entry
    # → promote borderline neighbours within that window.
    window = 8
    for centre in range(n):
        lo = max(0, centre - window)
        hi = min(n, centre + window + 1)
        biblio_count = sum(
            1 for k in range(lo, hi)
            if labels[k].klass == SegmentClass.BIBLIOGRAPHY_ENTRY
        )
        if biblio_count >= 4 and labels[centre].klass in (
            SegmentClass.BODY_TEXT, SegmentClass.NOISE
        ):
            f = labels[centre].features
            if f.get("paren_year") or f.get("bare_year") or f.get("publisher_hit"):
                labels[centre].klass = SegmentClass.BIBLIOGRAPHY_ENTRY
                labels[centre].confidence = 0.6

    # Front-matter promotion: in the first 5% of segments, standalone names
    # become book_metadata even if local confidence was modest.
    front_cutoff = max(5, int(n * 0.05))
    for k in range(min(front_cutoff, n)):
        if labels[k].klass == SegmentClass.NOISE:
            continue
        if labels[k].features.get("is_standalone_name") and labels[k].klass != SegmentClass.ARTIST_HEADER:
            labels[k].klass = SegmentClass.BOOK_METADATA
            labels[k].confidence = max(labels[k].confidence, 0.8)


def classify_segments(
    entries: Sequence[dict],
) -> List[SegmentLabel]:
    """Classify a sequence of TM entries (one origin, in document order).

    Each entry is the dict shape produced by translate_core.tm.TranslationMemory:
    {"source": str, "target": str, "origin": str, ...}
    """
    labels: List[SegmentLabel] = []
    total = len(entries)
    for i, e in enumerate(entries):
        lbl = SegmentLabel(
            idx=i,
            src=e.get("source", ""),
            tgt=e.get("target", ""),
            origin=e.get("origin", ""),
            klass=SegmentClass.NOISE,
            confidence=0.0,
        )
        _classify_local(lbl, total)
        labels.append(lbl)
    _smooth_pass(labels)
    return labels
