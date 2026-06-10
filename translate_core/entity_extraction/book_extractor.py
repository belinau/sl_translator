"""Book / article / humanities-essay extractor — high-recall.

Reads an origin's classified segments in document order and produces:

- translated_work records (one per detected book front-matter region; multiple
  per origin is normal — humanities TMs often concatenate many books)
- cited_work records from BIBLIOGRAPHY_ENTRY, FOOTNOTE, INLINE_CITATION,
  AND BODY_TEXT segments that carry citation signals
- agent_person records for every author / translator
- institution records for publishers
- Multi-citation segments are split on `;` so a footnote like
  `See Foo, Title (Year); also Bar, Title (Year).` yields TWO cited_works

Recall is the success metric. Confidence scoring + REVIEW tier handle
precision downstream.
"""


from __future__ import annotations


from ._slug import _slugify

import re
from typing import List, Optional

from .segment_classifier import (
    SegmentLabel,
    SegmentClass,
    KNOWN_PUBLISHERS,
    PAREN_YEAR_RE,
    ANY_YEAR_RE,
    LEADING_PREFIX_RE,
    INLINE_CITE_PAREN_RE,
)
from .name_dedup import (
    is_plausible_person_name,
    dedup_group_key,
    normalize_person_name,
    looks_like_organization,
)



# Multi-citation splitter: `;` is the safest splitter inside a citation block
SPLIT_RE = re.compile(r"\s*;\s+")

# Editor/translator markers that often precede or follow author names
ED_TRANS_PREFIXES = (
    "ed.", "eds.", "edited by", "uredil", "uredila", "ur.", "trans.",
    "translated by", "prev.", "prevedel", "prevedla",
)

# Strip ed./eds./uredil/etc. from start of "Author" field
ED_PREFIX_RE = re.compile(
    r"^(?:" + "|".join(re.escape(p) for p in ED_TRANS_PREFIXES) + r")\s+",
    re.IGNORECASE,
)

# Strip ", ed." or "ed.," or ", uredil," from middle/end of author field
ED_INFIX_RE = re.compile(
    r"\s*,?\s*(?:" + "|".join(re.escape(p) for p in ED_TRANS_PREFIXES) + r")\s*,?\s*",
    re.IGNORECASE,
)


# Primary biblio shape — permissive, matches:
#   "Lastname Firstname, Title (...)"
#   "Firstname Lastname, Title (...)"
#   "Foo and Bar, Title (...)"
BIBLIO_AUTHOR_TITLE_RE = re.compile(
    r"^(?P<author>[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+"
    r"(?:\s+(?:and|in|&)\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+)?)?"
    r"(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+){1,3})"
    r"\s*[,.]\s+"
    r"(?P<rest>.+)$",
    re.DOTALL,
)

# Lastname-first form: `Foucault, Michel. Discipline and Punish.`
# or `Foucault, M., Discipline and Punish.`
BIBLIO_LASTNAME_FIRST_RE = re.compile(
    r"^(?P<last>[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]{2,})\s*,\s+"
    r"(?P<first>[A-ZŠŽČĆĐ](?:[a-zšžčćđöüäáéíóúýň]+|\.)"
    r"(?:\s+[A-ZŠŽČĆĐ](?:[a-zšžčćđöüäáéíóúýň]+|\.))?)"
    r"\s*[,.]\s+"
    r"(?P<rest>.+)$",
    re.DOTALL,
)

# Title-first form: `Title (EN gloss)?, Author` — used in SL→EN bilingual TMs
# where the original title comes first and the author follows.
# Example: `prečne črte skrbi (The Life of Art: Transversal Lines of Care), Bojana Kunst`
BIBLIO_TITLE_FIRST_RE = re.compile(
    r"^(?P<title>[^()]{4,120}?)"
    r"(?:\s+\((?P<en_gloss>[^)]{4,120})\))?"
    r"\s*,\s+"
    r"(?P<author>[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+"
    r"(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+){1,2})"
    r"\s*\.?\s*$",
    re.DOTALL,
)

# Inline parenthetical: `(Foucault 1989)` / `(Foucault 1989: 56)`
INLINE_PAREN_CITE_RE = re.compile(
    r"\(\s*(?P<author>[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+"
    r"(?:\s+(?:and|in|&)\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+)?)"
    r"\s+(?P<year>(?:19|20)\d{2})"
    r"(?:\s*[:,]\s*(?P<page>\d+(?:[-–]\d+)?))?"
    r"\s*\)"
)

# Author-prose-paren: `as Foucault (1989) argues` / `Foucault (1989: 56)`
PROSE_PAREN_CITE_RE = re.compile(
    r"\b(?P<author>[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+"
    r"(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+){0,2})"
    r"\s*\(\s*(?P<year>(?:19|20)\d{2})"
    r"(?:\s*[:,]\s*(?P<page>\d+(?:[-–]\d+)?))?"
    r"\s*\)"
)

# Op. cit. / Ibid: refers to earlier-cited work
OPCIT_AUTHOR_RE = re.compile(
    r"\b(?P<author>[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+"
    r"(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+){0,2}),"
    r"\s+(?:op\.\s*cit\.|nav\.\s*delo|cit\.)",
    re.IGNORECASE,
)



def _origin_to_work_id(origin: str) -> str:
    base = origin.rsplit(".", 1)[0]
    return _slugify(base)


def _strip_cite_prefix(text: str) -> str:
    """Strip See / Cf / Glej / Prim / footnote-number from the start."""
    out = text
    for _ in range(3):
        new = LEADING_PREFIX_RE.sub("", out, count=1)
        if new == out:
            break
        out = new
    return out.strip()


def _strip_ed_prefix(name: str) -> str:
    """Strip 'ed.', 'eds.', 'ur.', 'prev.' from author field."""
    out = name
    for _ in range(2):
        new = ED_PREFIX_RE.sub("", out, count=1)
        if new == out:
            break
        out = new
    return out.strip()


def _clean_author(raw: str) -> Optional[str]:
    """Strip prefixes and validate. Returns None if not a plausible person."""
    s = _strip_cite_prefix(raw)
    s = _strip_ed_prefix(s)
    s = re.sub(r"\s*,?\s+(?:eds?\.|ur\.)\s*$", "", s).strip()
    s = s.strip(" ,.;:")
    if not s:
        return None
    if not is_plausible_person_name(s):
        return None
    if looks_like_organization(s):
        return None
    if _looks_like_phrase_not_name(s):
        return None
    return s


# ---------------------------------------------------------------------------
# Per-segment citation extraction
# ---------------------------------------------------------------------------

def _looks_like_cast_list(text: str) -> bool:
    """Detect comma-separated cast lists (4+ Capitalized Name pairs, no year, no publisher).

    Festival programmes have many segments like:
      `Ema Križič, Tina Benko, Liza Šimenc, Anja Mejač, Dejan Srhoj with the Friday group`
    These look like `Author, rest` to the citation parser but are credit lists.
    """
    # Count capital-leading word pairs ("Name Name")
    pairs = re.findall(
        r"\b[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+",
        text,
    )
    if len(pairs) < 4:
        return False
    if ANY_YEAR_RE.search(text):
        return False
    if re.search(r"\b(?:Maska|Routledge|MIT Press|Verso|Studia|Ljubljana:|Zagreb:|London:|New York:|Oxford:|Cambridge:)\b", text):
        return False
    # Cast lists rarely contain a colon-separated `:` title structure
    if " (" in text and re.search(r"\([12]\d{3}\)", text):
        return False
    return True


# Common adjective/verb/adverb tokens that frequently START phrase-shaped fake authors
_PHRASE_AUTHOR_FIRST_TOKENS = {
    "look", "white", "black", "red", "green", "blue", "yellow",
    "special", "meantime", "looking", "moving", "doing", "making",
    "going", "coming", "seeing", "writing", "reading", "watching",
    "art", "the", "this", "that", "those", "these", "some", "any",
    "every", "all", "each", "much", "many", "few", "several",
    "modern", "contemporary", "classical", "traditional", "old", "new",
    "from", "to", "in", "on", "at", "by", "with", "as",
    "double", "single", "triple",
    # Common abstract-noun starts of phrase-fake-authors
    "cultural", "political", "social", "economic", "historical",
    "critical", "feminist", "queer", "post-colonial", "postcolonial",
    "global", "local", "national", "international", "transnational",
    "european", "american", "asian", "african",
    "intellectual", "theoretical", "practical", "moral", "ethical",
    "performance", "performative", "discursive", "narrative",
    "media", "digital", "online", "virtual", "material",
}


def _looks_like_phrase_not_name(name: str) -> bool:
    """Reject author candidates that are obviously phrase-shaped, not personal names."""
    tokens = name.split()
    if not tokens:
        return True
    first = tokens[0].lower().rstrip(",.;:")
    if first in _PHRASE_AUTHOR_FIRST_TOKENS:
        return True
    return False


def _split_multi_citation(text: str) -> List[str]:
    """Split a footnote/biblio segment that contains multiple citations.

    Splits on `; ` only when both sides look citation-shaped (capitalised
    leading token + year/publisher hint). Conservative: returns [text] if
    no plausible split.
    """
    if "; " not in text:
        return [text]
    parts = SPLIT_RE.split(text)
    if len(parts) < 2:
        return [text]
    # Each part should look citation-y: starts with capital and contains a year
    keep = []
    for p in parts:
        p = p.strip()
        if not p or len(p) < 10:
            continue
        if not re.match(r"^[A-ZŠŽČĆĐ]", p):
            continue
        keep.append(p)
    return keep if len(keep) >= 2 else [text]


def _parse_one_citation_segment(
    src: str, tgt: str, container_work_id: str,
    klass: SegmentClass, seg_idx: int, origin: str,
) -> List[dict]:
    """Try every citation pattern on a single segment.

    Returns 0+ cited_work records. Caller is responsible for de-duping.
    """
    out: List[dict] = []

    src_clean = _strip_cite_prefix(src.strip())
    tgt_clean = _strip_cite_prefix(tgt.strip())

    # Reject cast lists (festival programmes' Name1, Name2, Name3, ... pattern)
    if _looks_like_cast_list(src_clean):
        return out

    # For BODY_TEXT segments, require AT LEAST ONE strong citation signal
    # beyond just "Name, more text" (which catches cast lists / prose mentions).
    if klass == SegmentClass.BODY_TEXT:
        has_year = bool(ANY_YEAR_RE.search(src_clean))
        has_pub_signal = (
            "Maska" in src_clean or "Routledge" in src_clean
            or ":" in src_clean[:200]
            or "(" in src_clean and ")" in src_clean
        )
        if not (has_year or has_pub_signal):
            return out

    # Split multi-citation segments (semicolon-separated)
    src_parts = _split_multi_citation(src_clean)

    for part in src_parts:
        # Match Author + rest in 4 layered attempts
        author: Optional[str] = None
        rest = ""
        title_first_form: Optional[str] = None  # SL title carried from BIBLIO_TITLE_FIRST_RE
        en_gloss_first: Optional[str] = None

        # Attempt 1: Lastname-first format
        m = BIBLIO_LASTNAME_FIRST_RE.match(part)
        if m:
            author_raw = f"{m.group('first')} {m.group('last')}".strip()
            author = _clean_author(author_raw)
            rest = m.group("rest").strip()

        # Attempt 2: Firstname Lastname format
        if not author:
            m = BIBLIO_AUTHOR_TITLE_RE.match(part)
            if m:
                author = _clean_author(m.group("author").strip())
                rest = m.group("rest").strip()

        # Attempt 3: Title-first form (SL title, Author)
        # Common in SL→EN bilingual citations: "Życie sztuki (Life of Art), Autor"
        if not author:
            m = BIBLIO_TITLE_FIRST_RE.match(part)
            if m:
                cand_author = _clean_author(m.group("author").strip())
                cand_title = m.group("title").strip().rstrip(",;:.")
                # Sanity: title can't be itself a person name (would be ambiguous);
                # it should have lowercase content and not match standalone-name pattern.
                if cand_author and cand_title and not is_plausible_person_name(cand_title):
                    author = cand_author
                    title_first_form = cand_title
                    en_gloss_first = (m.group("en_gloss") or "").strip() or None
                    rest = cand_title  # for downstream year/pub extraction

        if not author:
            continue

        # Bilingual title parsing and pub info extraction were retired with
        # the VL stack. All values default to None; regex fallbacks handle
        # year and title extraction directly.
        pub_info = None
        sl_pub = None
        title_orig = None

        # Year extraction: try multiple sources
        year = None
        ym = PAREN_YEAR_RE.search(part)
        if ym:
            try:
                year = int(ym.group(1).split("-")[0].split("–")[0])
            except ValueError:
                pass
        if not year:
            ym = ANY_YEAR_RE.search(part)
            if ym:
                try:
                    year = int(ym.group(1))
                except ValueError:
                    pass

        title_en = title_orig
        if not title_en:
            title_en = re.split(r"\s*\(", rest, maxsplit=1)[0].rstrip(",;:.")
        # Strip ed./eds./uredil from title head
        if title_en:
            title_en = ED_PREFIX_RE.sub("", title_en).strip()

        # If title came from BIBLIO_TITLE_FIRST_RE we already have it
        title_sl = title_first_form if title_first_form else None
        if not title_sl and tgt_clean:
            tgt_part = tgt_clean
            tgt_m = BIBLIO_LASTNAME_FIRST_RE.match(tgt_part) or BIBLIO_AUTHOR_TITLE_RE.match(tgt_part)
            if tgt_m:
                sl_rest = tgt_m.group("rest") if "rest" in tgt_m.groupdict() else ""
                if sl_rest:
                    title_sl = re.split(r"\s*\(", sl_rest, maxsplit=1)[0].rstrip(",;:.")
                    title_sl = ED_PREFIX_RE.sub("", title_sl).strip()

        # Apply en_gloss if available from title-first form
        if title_first_form and en_gloss_first:
            title_en = en_gloss_first

        cited_id = _slugify(f"{author}-{title_en or title_sl or 'untitled'}-{year or ''}")

        signals = {
            "has_author": True,
            "has_title": bool(title_en or title_orig or title_sl),
            "has_year": bool(year),
            "has_publisher_city": False,
            "has_publisher": False,
            "title_bilingual": bool(title_orig or (title_en and title_sl)),
            "in_biblio_cluster": klass in (SegmentClass.BIBLIOGRAPHY_ENTRY,),
        }

        out.append({
            "kind": "cited_work",
            "payload": {
                "cited_id": cited_id,
                "author": author,
                "title_en": title_en,
                "title_sl": title_sl,
                "title_orig": title_orig,
                "year": year,
                "original_pub": pub_info,
                "slovenian_edition": sl_pub,
                "container_work_id": container_work_id,
            },
            "signals": signals,
            "source": {
                "origin": origin,
                "segment_idx": seg_idx,
                "src_excerpt": src[:240],
                "tgt_excerpt": tgt[:240],
                "segment_class": klass.value,
            },
        })
    return out


def _extract_inline_citations(
    src: str, tgt: str, container_work_id: str,
    seg_idx: int, origin: str, klass: SegmentClass,
) -> List[dict]:
    """Extract inline (Author Year) and Author (Year) patterns from body text.

    Each unique (author, year) pair → 1 cited_work record (lower confidence
    because no title/publisher info from the inline form alone).
    """
    found: List[dict] = []
    seen: set = set()

    for m in INLINE_PAREN_CITE_RE.finditer(src):
        author = _clean_author(m.group("author"))
        if not author:
            continue
        year = int(m.group("year"))
        page = m.group("page")
        key = (author.lower(), year)
        if key in seen:
            continue
        seen.add(key)
        found.append(_make_inline_cited_record(
            author, year, page, container_work_id, src, tgt,
            seg_idx, origin, klass, intro="paren",
        ))

    for m in PROSE_PAREN_CITE_RE.finditer(src):
        author = _clean_author(m.group("author"))
        if not author:
            continue
        year = int(m.group("year"))
        page = m.group("page")
        key = (author.lower(), year)
        if key in seen:
            continue
        seen.add(key)
        found.append(_make_inline_cited_record(
            author, year, page, container_work_id, src, tgt,
            seg_idx, origin, klass, intro="prose",
        ))

    return found


def _make_inline_cited_record(
    author: str, year: int, page: Optional[str], container_work_id: str,
    src: str, tgt: str, seg_idx: int, origin: str, klass: SegmentClass,
    intro: str,
) -> dict:
    cited_id = _slugify(f"{author}-inline-{year}")
    return {
        "kind": "cited_work",
        "payload": {
            "cited_id": cited_id,
            "author": author,
            "title_en": None,
            "title_sl": None,
            "title_orig": None,
            "year": year,
            "page": page,
            "original_pub": None,
            "slovenian_edition": None,
            "container_work_id": container_work_id,
            "inline_intro": intro,
        },
        "signals": {
            "has_author": True,
            "has_title": False,
            "has_year": True,
            "has_publisher_city": False,
            "has_publisher": False,
            "title_bilingual": False,
            "in_biblio_cluster": False,
        },
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "src_excerpt": src[:240],
            "tgt_excerpt": tgt[:240],
            "segment_class": klass.value,
        },
    }


def _extract_footnote_opcit(
    src: str, tgt: str, container_work_id: str,
    seg_idx: int, origin: str, klass: SegmentClass,
) -> List[dict]:
    """Extract `Author, op. cit., page` references — a footnote referring back."""
    out = []
    for m in OPCIT_AUTHOR_RE.finditer(src):
        author = _clean_author(m.group("author"))
        if not author:
            continue
        cited_id = _slugify(f"{author}-opcit")
        out.append({
            "kind": "cited_work",
            "payload": {
                "cited_id": cited_id,
                "author": author,
                "title_en": None,
                "title_sl": None,
                "title_orig": None,
                "year": None,
                "original_pub": None,
                "slovenian_edition": None,
                "container_work_id": container_work_id,
                "opcit": True,
            },
            "signals": {
                "has_author": True,
                "has_title": False,
                "has_year": False,
                "has_publisher_city": False,
                "has_publisher": False,
                "title_bilingual": False,
                "in_biblio_cluster": klass == SegmentClass.FOOTNOTE,
            },
            "source": {
                "origin": origin,
                "segment_idx": seg_idx,
                "src_excerpt": src[:240],
                "tgt_excerpt": tgt[:240],
                "segment_class": klass.value,
            },
        })
    return out


# ---------------------------------------------------------------------------
# Translated work front-matter detection (multi-region)
# ---------------------------------------------------------------------------

def _find_book_metadata_clusters(
    labels: List[SegmentLabel], min_cluster: int = 2, max_gap: int = 3,
) -> List[List[int]]:
    """Return clusters of indices where book_metadata segments occur within
    `max_gap` of each other. A cluster of >= min_cluster is candidate for a
    translated_work."""
    bm_idx = [
        i for i, lbl in enumerate(labels)
        if lbl.klass == SegmentClass.BOOK_METADATA
    ]
    clusters: List[List[int]] = []
    cur: List[int] = []
    for i in bm_idx:
        if cur and i - cur[-1] <= max_gap:
            cur.append(i)
        else:
            if len(cur) >= min_cluster:
                clusters.append(cur)
            cur = [i]
    if len(cur) >= min_cluster:
        clusters.append(cur)
    return clusters


def _extract_translated_work_from_cluster(
    labels: List[SegmentLabel], cluster: List[int], origin: str, total: int,
) -> Optional[dict]:
    """From a book_metadata cluster, attempt to extract author + title.

    Pattern: cluster contains 1-2 standalone-name segments and 1-2 title-shaped
    segments. The first standalone-name is the author, the first title-shaped
    is the title.
    """
    author: Optional[str] = None
    title_label: Optional[SegmentLabel] = None
    for i in cluster:
        lbl = labels[i]
        src = lbl.src.strip().rstrip(":")
        if author is None:
            if not is_plausible_person_name(src):
                continue
            if looks_like_organization(src):
                continue
            if _looks_like_phrase_not_name(src):
                continue
            author = src
            continue
        if author and not title_label:
            if len(src) < 4:
                continue
            # Reject ":"-ending lines as titles — festival programme pattern
            if src.endswith(":") or lbl.src.strip().endswith(":"):
                continue
            if is_plausible_person_name(src):
                # If the would-be title is itself a clean person name,
                # this is most likely a credit list, not a book front matter.
                # Try treating it as a second author only if we don't have one.
                if " and " not in author.lower():
                    author = f"{author} and {src}"
                    continue
                else:
                    # Already two authors and now another name — credit list.
                    return None
            title_label = lbl
            break

    if not (author and title_label):
        return None

    # Discard cluster results in obvious festival-credit areas: if NEAR-window
    # contains many festival_credit neighbours, this is a programme not a book.
    near_lo = max(0, cluster[0] - 6)
    near_hi = min(len(labels), cluster[-1] + 7)
    festival_share = sum(
        1 for j in range(near_lo, near_hi)
        if labels[j].klass == SegmentClass.FESTIVAL_CREDIT
    ) / max(1, near_hi - near_lo)
    if festival_share > 0.25:
        return None

    # Strong positive signal: BODY_TEXT must follow the cluster
    # within 10 segments. A real translated book has prose after its title page.
    body_lo = cluster[-1] + 1
    body_hi = min(len(labels), cluster[-1] + 15)
    body_after_share = sum(
        1 for j in range(body_lo, body_hi)
        if labels[j].klass == SegmentClass.BODY_TEXT
    ) / max(1, body_hi - body_lo)
    if body_after_share < 0.20:
        return None

    # Reject if the title ends with `:` — that's a festival programme pattern
    # ("Sister Outsider:", "Plaster Casters:") not a book front matter.
    if title_label.src.strip().endswith(":"):
        return None

    # Reject if the author == title (degenerate self-reference)
    if author.lower().split(" and ")[0].strip() == title_label.src.strip().rstrip(":,.").lower():
        return None

    rel_pos = cluster[0] / max(1, total - 1)
    work_id = _slugify(f"{author}-{title_label.src[:40]}")

    # Year discovery: look in surrounding 10 segments
    year = None
    for lbl in labels[cluster[0]: min(len(labels), cluster[-1] + 10)]:
        ym = PAREN_YEAR_RE.search(lbl.src)
        if ym:
            try:
                year = int(ym.group(1).split("-")[0].split("–")[0])
            except ValueError:
                pass
            break

    # Translator discovery
    translator = None
    for lbl in labels[cluster[-1]: min(len(labels), cluster[-1] + 6)]:
        t = lbl.src.strip().lower()
        if any(t.startswith(m) for m in (
            "translated by", "prevedel", "prevedla", "prev.", "trans."
        )):
            m = re.search(
                r"(?:translated by|prevedel|prevedla|prev\.|trans\.)\s+"
                r"([A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+"
                r"(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+){1,3})",
                lbl.src, re.IGNORECASE,
            )
            if m:
                translator = m.group(1).strip()
                break

    return {
        "author": author,
        "title_en": title_label.src.strip().rstrip(",;:."),
        "title_sl": title_label.tgt.strip().rstrip(",;:."),
        "year": year,
        "translator": translator,
        "cluster_first_idx": cluster[0],
        "cluster_last_idx": cluster[-1],
        "rel_pos": rel_pos,
        "work_id": work_id,
    }


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def extract_from_book_origin(
    labels: List[SegmentLabel],
) -> List[dict]:
    records: List[dict] = []
    if not labels:
        return records

    origin = labels[0].origin
    container_work_id = _origin_to_work_id(origin)
    total = len(labels)

    # --- Auto-detected translated-work candidates: scan WHOLE origin for clusters
    # These now go straight to REVIEW tier (lower confidence) since the user's
    # feedback confirmed automatic front-matter detection over-claims in TMs
    # that are mixed-project corpora. Seeded books (manifest-driven) are the
    # authoritative source of translated_work nodes.
    clusters = _find_book_metadata_clusters(labels)
    for cluster in clusters:
        fm = _extract_translated_work_from_cluster(labels, cluster, origin, total)
        if not fm:
            continue
        records.append({
            "kind": "translated_work",
            "payload": {
                "work_id": fm["work_id"],
                "author": fm["author"],
                "title_en": fm["title_en"],
                "title_sl": fm["title_sl"],
                "year": fm["year"],
                "translator": fm["translator"],
                "origin": origin,
                "project_type": "book_translation_candidate",
            },
            "signals": {
                # Deliberately weak: auto-cluster detection should NOT
                # cross 0.85 threshold. Only seeded book finder hits direct_write.
                "has_title": bool(fm["title_en"] or fm["title_sl"]),
                "has_author": True,
                "has_year": False,
                "position_front_matter": False,
                "title_bilingual": False,
                "has_publisher": False,
            },
            "source": {
                "origin": origin,
                "segment_idx": fm["cluster_first_idx"],
                "src_excerpt": labels[fm["cluster_first_idx"]].src[:240],
                "tgt_excerpt": labels[fm["cluster_first_idx"]].tgt[:240],
                "segment_class": "book_metadata_cluster_auto",
            },
        })

    # --- Citation extraction over EVERY segment with any citation signal ---
    EXTRACT_CLASSES = {
        SegmentClass.BIBLIOGRAPHY_ENTRY,
        SegmentClass.FOOTNOTE,
        SegmentClass.INLINE_CITATION,
        SegmentClass.BODY_TEXT,
    }
    for lbl in labels:
        if lbl.klass not in EXTRACT_CLASSES:
            continue
        # Skip segments with zero citation signal to save work
        f = lbl.features
        has_signal = (
            f.get("biblio_shape") or f.get("is_footnote_shape") or
            f.get("inline_dense") or f.get("publisher_hit") or
            f.get("has_lastname_comma") or f.get("publisher_city_hit") or
            INLINE_CITE_PAREN_RE.search(lbl.src)
        )
        if not has_signal:
            continue

        # 1) Try full biblio parse (lastname-first OR firstname-lastname)
        parsed = _parse_one_citation_segment(
            lbl.src, lbl.tgt, container_work_id,
            lbl.klass, lbl.idx, origin,
        )
        for rec in parsed:
            records.append(rec)
            records.append(_agent_person_from_name(
                rec["payload"]["author"], origin, role="author",
                multi_mention=False, multi_origin=False,
            ))
            pub = rec["payload"].get("original_pub") or {}
            if pub.get("publisher"):
                records.append(_institution_from_publisher(
                    pub["publisher"], pub.get("city"), origin,
                ))
            sl_pub = rec["payload"].get("slovenian_edition") or {}
            if sl_pub.get("publisher"):
                records.append(_institution_from_publisher(
                    sl_pub["publisher"], sl_pub.get("city"), origin,
                ))

        # 2) Inline citations — only if we didn't already get a biblio parse
        # (avoids double-counting the same citation as both biblio + inline)
        if not parsed:
            for rec in _extract_inline_citations(
                lbl.src, lbl.tgt, container_work_id,
                lbl.idx, origin, lbl.klass,
            ):
                records.append(rec)
                records.append(_agent_person_from_name(
                    rec["payload"]["author"], origin, role="author",
                    multi_mention=False, multi_origin=False,
                ))

        # 3) Op.cit. / Ibid. references (always run; they're additive)
        for rec in _extract_footnote_opcit(
            lbl.src, lbl.tgt, container_work_id,
            lbl.idx, origin, lbl.klass,
        ):
            records.append(rec)
            records.append(_agent_person_from_name(
                rec["payload"]["author"], origin, role="author",
                multi_mention=False, multi_origin=False,
            ))

    return records


def _agent_person_from_name(
    name: str, origin: str, role: str,
    multi_mention: bool, multi_origin: bool,
    ner_hit: bool = False,
) -> dict:
    return {
        "kind": "agent_person",
        "payload": {
            "name": name,
            "role": role,
            "dedup_group": dedup_group_key(name),
            "norm": normalize_person_name(name),
            "origin": origin,
        },
        "signals": {
            "plausible_person_name": is_plausible_person_name(name),
            "ner_person_match": ner_hit,
            "multi_mention": multi_mention,
            "multi_origin": multi_origin,
            "role_attribution_context": role in {"author", "translator", "curator"},
        },
        "source": {
            "origin": origin,
            "segment_idx": -1,
            "src_excerpt": "",
            "tgt_excerpt": "",
        },
    }


# Tokens in institution names that signal their kind.
# Values MUST be in ontology §2.6 allowlist: publisher/gallery/museum/
# university/festival/theatre/journal/organization/sponsor/country/other.
_KIND_TOKEN_MAP = (
    (re.compile(r"\b(?:Gallery|Galleries|Galerija|Galerie|Galería)\b", re.IGNORECASE), "gallery"),
    (re.compile(r"\b(?:Museum|Muzej|Múzeum|Museo|Musée|Kunsthaus|Kunsthalle|Kunstverein)\b", re.IGNORECASE), "museum"),
    (re.compile(r"\b(?:Festival|Biennale|Bienale|Biennial)\b", re.IGNORECASE), "festival"),
    (re.compile(r"\b(?:Theatre|Theater|Gledališče|Teatro)\b", re.IGNORECASE), "theatre"),
    (re.compile(r"\b(?:University|Univerza|Universität|Université|Università)\b", re.IGNORECASE), "university"),
    # Research institutes / centres / archives / foundations → ontology
    # catch-all 'organization' (institute/centre/archive/foundation are not
    # in §2.6 allowlist).
    (re.compile(r"\b(?:Institute|Inštitut|Institut)\b", re.IGNORECASE), "organization"),
    (re.compile(r"\b(?:Press|Editions|Verlag|Editorial|Edizioni|Založba)\b", re.IGNORECASE), "publisher"),
    (re.compile(r"\b(?:Centre|Center|Centrum)\b", re.IGNORECASE), "organization"),
    (re.compile(r"\b(?:Archive|Arhiv)\b", re.IGNORECASE), "organization"),
    (re.compile(r"\b(?:Foundation|Fundacija|Fundación|Stiftung)\b", re.IGNORECASE), "organization"),
)


def _infer_institution_kind(name: str) -> str:
    for rx, kind in _KIND_TOKEN_MAP:
        if rx.search(name):
            return kind
    return "publisher"


def _institution_from_publisher(
    publisher: str, city: Optional[str], origin: str,
) -> dict:
    name = publisher.strip()
    inferred_kind = _infer_institution_kind(name)
    return {
        "kind": "institution",
        "payload": {
            "name": name,
            "kind": inferred_kind,
            "city": city,
            "origin": origin,
        },
        "signals": {
            "known_publisher": name in KNOWN_PUBLISHERS,
            "institution_pattern_match": True,
            "multi_mention": False,
            "named_institution_kind": inferred_kind != "publisher" or name in KNOWN_PUBLISHERS,
        },
        "source": {
            "origin": origin,
            "segment_idx": -1,
            "src_excerpt": "",
            "tgt_excerpt": "",
        },
    }
