"""Verify VL-extracted citation fields against the source segment text.

VL hallucinates. Yesterday's regex parsers fabricated structure from
unverified style assumptions. Both fail in different ways. This module
trusts NEITHER: it lets VL extract whatever it extracts, then drops every
field that doesn't actually appear in the segment it came from.

Rules:
  - author surname not in segment → record rejected (no author = bogus)
  - title not findable in segment (≥60% of its words present) → record rejected
  - any subsidiary field (year, container, publisher, city, pages) that
    doesn't appear in segment → set to null, keep the rest

No parsing. Only substring + diacritic-folded substring + numeric match
against the literal text the VL model saw.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    """Lower-case, strip diacritics, collapse whitespace. For substring checks
    only — never compared as identity."""
    s = _strip_diacritics(text).lower()
    return re.sub(r"\s+", " ", s).strip()


def _in_text(needle: str, text_norm: str) -> bool:
    """Is `needle` (after the same normalisation as text_norm) a substring?"""
    if not needle:
        return False
    n = _normalize(needle)
    if not n or len(n) < 2:
        return False
    return n in text_norm


def _surname_of(author_str: str) -> str:
    """Pick the most likely surname from a free-form author string.

    Lastname-first form ('Foucault, Michel') → 'Foucault'
    Firstname-first form ('Michel Foucault') → 'Foucault'
    Just initials ('M. F.') → '' (no usable surname)
    """
    s = author_str.strip().strip(",.;:")
    if not s:
        return ""
    # If a comma is present, the part before is the surname
    if "," in s:
        head = s.split(",", 1)[0].strip()
        if head and len(head) >= 2 and any(c.isalpha() for c in head):
            return head
    # Otherwise the last whitespace-token, ignoring trailing initials
    tokens = [t for t in re.split(r"\s+", s) if t]
    # Drop tokens that are initials like "M." or single chars
    full_tokens = [t for t in tokens if len(t.rstrip(".")) >= 2]
    if full_tokens:
        return full_tokens[-1]
    return ""


def _title_match_ratio(title: str, text_norm: str) -> float:
    """Fraction of significant title words (>3 chars) that appear in text_norm."""
    title_norm = _normalize(title)
    words = [w for w in re.findall(r"[a-z0-9]+", title_norm) if len(w) > 3]
    if not words:
        # Title is all very-short words; check if normalised title is in text
        return 1.0 if title_norm and title_norm in text_norm else 0.0
    matches = sum(1 for w in words if w in text_norm)
    return matches / len(words)


def _year_in_text(year, text: str) -> Optional[int]:
    """Return the year as int IF it appears in the literal text."""
    if year is None:
        return None
    try:
        if isinstance(year, list):
            year = year[0] if year else None
        y_int = int(str(year)[:4])
    except (ValueError, TypeError, IndexError):
        return None
    if 1500 <= y_int <= 2100 and str(y_int) in text:
        return y_int
    return None


def verify_citation(extracted: dict, segment_text: str) -> Optional[dict]:
    """Validate VL's citation extraction against the source segment.

    Returns a cleaned dict with only verified fields, or None if the record
    is fundamentally bogus (no verified author OR no verified title).

    Output schema (only present-and-verified fields):
        {
          "authors": ["Surname[, Firstname]", ...],   # at least one
          "title": "...",                              # verified by word overlap
          "container": "...",                          # if in text
          "year": NNNN,                                # if numeric and in text
          "publisher": "...",                          # if in text
          "city": "...",                               # if in text
          "pages": "...",                              # if in text
        }
    """
    if not isinstance(extracted, dict) or not segment_text:
        return None

    text = segment_text
    text_norm = _normalize(text)

    # ----- Authors: at least one surname must be present -----
    raw_authors = extracted.get("authors") or extracted.get("author")
    if isinstance(raw_authors, str):
        raw_authors = [raw_authors]
    if not isinstance(raw_authors, list):
        raw_authors = []

    verified_authors: list[str] = []
    for a in raw_authors:
        if not isinstance(a, str):
            continue
        a_clean = a.strip().strip(",.;:")
        if not a_clean or len(a_clean) < 3:
            continue
        surname = _surname_of(a_clean)
        if not surname:
            continue
        if _in_text(surname, text_norm):
            verified_authors.append(a_clean)

    if not verified_authors:
        return None

    # ----- Title: ≥60% of long words must appear in text -----
    raw_title = extracted.get("title")
    if isinstance(raw_title, list):
        raw_title = raw_title[0] if raw_title else None
    if not isinstance(raw_title, str) or not raw_title.strip():
        return None

    title = raw_title.strip().strip('"\'')
    # Reject ludicrously long "titles" (>80% of segment text — model copied the sentence)
    if len(title) > 0.8 * len(text) and len(text) > 60:
        return None
    if _title_match_ratio(title, text_norm) < 0.6:
        return None

    out: dict = {"authors": verified_authors, "title": title}

    # ----- Subsidiary fields: include only if present in text -----
    container = extracted.get("container")
    if isinstance(container, str) and container.strip():
        if _in_text(container.strip(), text_norm):
            out["container"] = container.strip()

    year_int = _year_in_text(extracted.get("year"), text)
    if year_int is not None:
        out["year"] = year_int

    publisher = extracted.get("publisher")
    if isinstance(publisher, str) and publisher.strip():
        if _in_text(publisher.strip(), text_norm):
            out["publisher"] = publisher.strip()

    city = extracted.get("city")
    if isinstance(city, str) and city.strip():
        if _in_text(city.strip(), text_norm):
            out["city"] = city.strip()

    pages = extracted.get("pages")
    if isinstance(pages, str) and pages.strip():
        # Pages format: '40' / '40-45' / 'pp. 40-45' / 'str. 40' — accept if any
        # number from the extracted pages appears as a page-like reference in text.
        pg_nums = re.findall(r"\d+", pages)
        if pg_nums and any(
            re.search(rf"\b{n}\b", text) for n in pg_nums
        ):
            out["pages"] = pages.strip()

    return out
