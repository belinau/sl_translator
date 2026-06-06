"""Bilingual TM matching for parsed citations.

For each ParsedCitation from a translated book's bibliography, scan the
TM (both source/EN and target/SL fields) for segments referencing the
same work. Collect:

  - tm_segment_refs: list of (origin, global_idx, matched_form, lang) tuples
  - alt_publishers: extra publisher info found in TM that isn't in the
    canonical bibliography entry (e.g. SL edition with different publisher,
    translator)
  - alt_titles: alternate title forms found in TM (e.g. SL translation
    of an EN work)

Matching strategy (multiple, scored):
  1. Strong: author surname + year both appear in the same segment
  2. Strong: significant portion of title (>= 6 chars distinctive) appears
  3. Weak (used only with author surname co-occurrence): publisher name
  4. Author surname appears in `op. cit.` / `ibid.` shortened references
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .bibliography_parser import ParsedCitation


@dataclass
class TMMatch:
    origin: str
    global_idx: int
    matched_form: str
    lang: str               # "en" / "sl" / "both"
    confidence: float
    reason_codes: List[str] = field(default_factory=list)


@dataclass
class CitationWithTMRefs:
    citation: ParsedCitation
    tm_matches: List[TMMatch] = field(default_factory=list)
    alt_publishers: List[Dict] = field(default_factory=list)
    alt_titles: List[Dict] = field(default_factory=list)

    @property
    def tm_segment_refs(self) -> List[Dict]:
        return [
            {
                "origin": m.origin,
                "global_idx": m.global_idx,
                "lang": m.lang,
                "confidence": m.confidence,
                "matched_form": m.matched_form[:200],
                "reason_codes": m.reason_codes,
            }
            for m in self.tm_matches
        ]


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize_for_search(text: str) -> str:
    """Normalize text for case/diacritic-insensitive substring search."""
    return _strip_diacritics(text).lower()


def _title_keywords(title: str, min_len: int = 6) -> List[str]:
    """Pick distinctive multi-word phrases from a title for substring search.

    Returns the title itself plus a list of distinctive 2-3-word phrases.
    A short title (<min_len) becomes the only keyword.
    """
    if not title or len(title) < min_len:
        return [title] if title else []
    # Strip surrounding punctuation/quotes
    t = title.strip(" '\"‘’“”«»")
    out = [t]
    # Add a "distinctive chunk" — middle ngrams of 3+ chars
    tokens = [w for w in re.split(r"[\s,;:]+", t) if len(w) >= 4]
    if len(tokens) >= 2:
        out.append(" ".join(tokens[:3]))  # first 3 words
        if len(tokens) >= 4:
            out.append(" ".join(tokens[-2:]))  # last 2 words
    return list(dict.fromkeys(out))  # dedupe preserving order


# Page-reference / op-cit shortform: `Author 2005: 56` / `Author, op. cit.`
def _opcit_patterns(surname: str) -> List[re.Pattern]:
    """Regex patterns matching shortened references to a work whose author
    we know."""
    name = re.escape(surname)
    return [
        re.compile(rf"\b{name}\s*,\s*op\.\s*cit\.", re.IGNORECASE),
        re.compile(rf"\b{name}\s*,\s*nav\.\s*delo", re.IGNORECASE),
        re.compile(rf"\b{name}\s+\d{{4}}\s*:?\s*\d+", re.IGNORECASE),
        re.compile(rf"\({name}\s+\d{{4}}", re.IGNORECASE),
    ]


def match_citation_against_tm(
    citation: ParsedCitation,
    tm_entries: Sequence[dict],
) -> CitationWithTMRefs:
    """Scan TM entries for segments referencing the same work as `citation`.

    Returns a CitationWithTMRefs with collected TM matches + alt forms found.
    """
    surname = citation.primary_author_surname or ""
    norm_surname = _normalize_for_search(surname) if surname else None
    year = str(citation.year) if citation.year else None
    title_keywords = [_normalize_for_search(k) for k in _title_keywords(citation.title or "")]
    pub_norm = _normalize_for_search(citation.publisher) if citation.publisher else None
    opcit_pats = _opcit_patterns(surname) if surname else []

    # Pre-compile per-citation regexes ONCE (not per TM entry).
    surname_re = (
        re.compile(rf"\b{re.escape(norm_surname)}\b")
        if norm_surname and len(norm_surname) >= 4 else None
    )
    pages_re = None
    if citation.pages:
        pages = str(citation.pages).strip()
        p_first = re.split(r"[-–]", pages)[0].strip()
        pages_re = re.compile(
            rf"(?:str\.|pp?\.|\bp\.)\s*{re.escape(pages)}\b|"
            rf"(?:str\.|pp?\.|\bp\.)\s*{re.escape(p_first)}\b"
        )
    url_norm = citation.url.lower().rstrip("/.,") if citation.url else None

    result = CitationWithTMRefs(citation=citation)

    # Fast-path: if no usable surname AND no usable opcit pattern, skip entirely
    if surname_re is None and not opcit_pats:
        return result

    for global_idx, e in enumerate(tm_entries):
        src = e.get("source", "")
        tgt = e.get("target", "")
        if not (src or tgt):
            continue
        # Cheap pre-filter: surname substring presence in either field
        # (case-insensitive, no normalisation). Skip if absent.
        if surname_re is not None:
            sl = surname.lower()
            if sl not in src.lower() and sl not in tgt.lower():
                # Could still hit op.cit. with a different casing
                if not any(p.search(src) or p.search(tgt) for p in opcit_pats):
                    continue
        src_n = _normalize_for_search(src)
        tgt_n = _normalize_for_search(tgt)

        # --- Tier 1: strong match — author + year/title/publisher/page/URL ---
        for lang, field_n in (("en", src_n), ("sl", tgt_n)):
            if surname_re is None:
                continue
            if not surname_re.search(field_n):
                continue
            year_ok = year and year in field_n
            title_kw = next((k for k in title_keywords if k and k in field_n), None) if title_keywords else None
            pub_ok = pub_norm and pub_norm in field_n
            page_ok = pages_re is not None and bool(pages_re.search(field_n))
            url_ok = url_norm is not None and url_norm in field_n.lower()

            reasons = ["author_surname"]
            conf = 0.0
            if year_ok:
                reasons.append("year_match")
                conf += 0.30
            if title_kw:
                reasons.append("title_fragment")
                conf += 0.40
            if pub_ok:
                reasons.append("publisher_match")
                conf += 0.20
            if page_ok:
                reasons.append("page_tail_match")
                conf += 0.30  # strong signal per user's insight
            if url_ok:
                reasons.append("url_match")
                conf += 0.45  # near-certain match

            if not (year_ok or title_kw or pub_ok or page_ok or url_ok):
                continue
            conf = min(1.0, conf + 0.20)
            field_text = src if lang == "en" else tgt
            result.tm_matches.append(TMMatch(
                origin=e.get("origin", ""),
                global_idx=global_idx,
                matched_form=field_text,
                lang=lang,
                confidence=conf,
                reason_codes=reasons,
            ))

        # --- Tier 2: op. cit. / shortform references — author + year-or-page ---
        for pat in opcit_pats:
            if pat.search(src) or pat.search(tgt):
                result.tm_matches.append(TMMatch(
                    origin=e.get("origin", ""),
                    global_idx=global_idx,
                    matched_form=src if pat.search(src) else tgt,
                    lang="en" if pat.search(src) else "sl",
                    confidence=0.55,
                    reason_codes=["opcit_shortform"],
                ))
                break

    # Dedupe TM matches by global_idx (a single segment can match multiple ways)
    seen_idx: Dict[int, TMMatch] = {}
    for m in result.tm_matches:
        if m.global_idx not in seen_idx or m.confidence > seen_idx[m.global_idx].confidence:
            seen_idx[m.global_idx] = m
    result.tm_matches = list(seen_idx.values())

    # --- Mine alt publishers from TM matches ---
    # If we found segments where the surname appears with a DIFFERENT publisher
    # than the canonical citation, capture the alternate (often a SL edition).
    pub_re = re.compile(
        r"\b(?:Ljubljana|Maribor|Beograd|Zagreb|Sarajevo|Wien|Berlin|"
        r"London|New\s+York|Cambridge|Oxford|Paris|Boston|Chicago)\s*:\s*"
        r"(?P<pub>[A-ZŠŽČĆĐ][^,.;]+?)(?:[,.]|$)"
    )
    alt_seen: set = set()
    for m in result.tm_matches:
        text = m.matched_form
        for ppm in pub_re.finditer(text):
            pub = ppm.group("pub").strip()
            key = _normalize_for_search(pub)
            canon = _normalize_for_search(citation.publisher or "")
            if key != canon and key not in alt_seen and pub.lower() not in _normalize_for_search(text):
                alt_seen.add(key)
                # Extract city for this alt
                city_m = re.search(
                    r"\b(Ljubljana|Maribor|Beograd|Zagreb|Sarajevo|Wien|Berlin|London|"
                    r"New\s+York|Cambridge|Oxford|Paris|Boston|Chicago)\s*:\s*"
                    + re.escape(pub),
                    text,
                )
                city = city_m.group(1) if city_m else None
                # Parse year (4-digit) and translator from the same segment
                # so the dict matches §2.4.2 `slovenian_edition` shape exactly.
                year_m = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", text)
                ap_year = int(year_m.group(1)) if year_m else None
                tr_m = re.search(
                    r"(?:prev(?:edla?|edli|edel)?|prevod|"
                    r"trans(?:lated\s+by|\.))"
                    r"[:\s.]+(?P<tr>[A-Z\u0160\u017D\u010C\u0106\u0110]"
                    r"[\w'\-]+(?:\s+[A-Z\u0160\u017D\u010C\u0106\u0110][\w'\-]+){0,2})",
                    text, re.IGNORECASE | re.UNICODE,
                )
                ap_translator = tr_m.group("tr").strip() if tr_m else None
                # Canonical §2.4.2 `slovenian_edition: {publisher, city, year,
                # translator}` shape. Provenance keys dropped — no consumer
                # reads them and the downstream KG sink expects this exact dict.
                result.alt_publishers.append({
                    "publisher": pub,
                    "city": city,
                    "year": ap_year,
                    "translator": ap_translator,
                })

    return result


def match_all_citations(
    citations: Sequence[ParsedCitation],
    tm_entries: Sequence[dict],
) -> List[CitationWithTMRefs]:
    """Match each parsed citation against the TM. Returns one record per
    citation with its TM footprint."""
    out: List[CitationWithTMRefs] = []
    for c in citations:
        out.append(match_citation_against_tm(c, tm_entries))
    return out


# --------------------------------------------------------------------------
# Reporting helpers
# --------------------------------------------------------------------------

def summarize_matches(matched: Sequence[CitationWithTMRefs]) -> Dict:
    """Aggregate statistics for the report."""
    total = len(matched)
    with_any_match = sum(1 for m in matched if m.tm_matches)
    with_alt_pub = sum(1 for m in matched if m.alt_publishers)
    total_matches = sum(len(m.tm_matches) for m in matched)
    by_type: Dict[str, int] = defaultdict(int)
    matched_by_type: Dict[str, int] = defaultdict(int)
    for m in matched:
        by_type[m.citation.citation_type] += 1
        if m.tm_matches:
            matched_by_type[m.citation.citation_type] += 1
    return {
        "total_citations": total,
        "citations_with_tm_matches": with_any_match,
        "citations_with_alt_publisher": with_alt_pub,
        "total_tm_match_count": total_matches,
        "by_type": dict(by_type),
        "matched_by_type": dict(matched_by_type),
    }
