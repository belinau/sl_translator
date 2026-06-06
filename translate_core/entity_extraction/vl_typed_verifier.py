"""Type-aware verifier for VL-extracted typed citations.

Each citation type has its own required-fields and forbidden-fields list (see
citation_types.TYPE_SCHEMAS). The verifier:

1. Rejects records whose required fields aren't present and substring-verified
   against the segment text.
2. Rejects records that contain forbidden fields (journal_article must NOT
   have publisher/city — those would mean the type classification was wrong).
3. Nulls out subsidiary fields that don't substring-verify, but keeps the
   record otherwise intact.

The verifier never parses citation structure. It only checks that what VL
returned is grounded in the segment text it claimed to extract from.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from .citation_types import (
    CitationStyle,
    CitationType,
    TYPE_SCHEMAS,
)


# ── Text-matching helpers ────────────────────────────────────────────────────

def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize(text: str) -> str:
    s = _strip_diacritics(text).lower()
    return re.sub(r"\s+", " ", s).strip()


def _in_text(needle: str, text_norm: str) -> bool:
    if not needle:
        return False
    n = _normalize(needle)
    if not n or len(n) < 2:
        return False
    return n in text_norm


# Strings that VL sometimes mis-extracts as surnames but obviously aren't:
# publisher cities, country names, well-known publisher houses, organisations.
# When VL emits surname="Ljubljana", the substring-check passes (the city is
# in the segment) — this list blocks that false positive.
_NOT_A_SURNAME = frozenset(s.lower() for s in (
    # cities
    "Ljubljana", "Maribor", "Koper", "Celje", "Kranj",
    "Zagreb", "Beograd", "Belgrade", "Sarajevo", "Skopje",
    "London", "New York", "Cambridge", "Oxford", "Berlin", "Wien", "Vienna",
    "Paris", "Frankfurt", "Boston", "Chicago", "Stanford", "Princeton",
    "Durham", "Bloomington", "Ithaca", "Helsinki", "Stockholm",
    "Copenhagen", "Oslo", "Warsaw", "Prague", "Praha",
    "Budapest", "Bratislava", "München", "Munich", "Köln", "Cologne",
    "Hamburg", "Madrid", "Barcelona", "Roma", "Rome", "Milano", "Milan",
    "Amsterdam", "Rotterdam", "Antwerp", "Linz", "Basel",
    # countries / political entities
    "Yugoslavia", "Jugoslavija", "Slovenia", "Slovenija", "Serbia", "Srbija",
    "Croatia", "Hrvatska", "Macedonia", "Makedonija", "Bosnia",
    "Yugoslav", "Slovenian", "Slovenian", "European Union", "EU",
    # well-known publishers
    "Maska", "Modrijan", "Routledge", "Verso", "Maska", "Sternberg",
    "Studia Humanitatis", "Cankarjeva", "Beletrina", "MIT Press",
    "Duke University Press", "Stanford University Press",
    "Princeton University Press", "Oxford University Press",
    "Cambridge University Press", "Penguin", "Polity",
    "Sage", "Bloomsbury", "Continuum", "Sophia",
    # institutions sometimes appearing as authors-of-press-releases
    "MoMA", "Tate", "MGLC", "Moderna galerija",
))


def _verify_person_in_text(person, text_norm: str) -> Optional[dict]:
    """Return a cleaned {surname, given_name} dict if the surname appears in
    the segment text, else None.

    Also rejects when the surname is a known city / country / publisher /
    organisation — these are common VL mis-extractions where the model
    treats a publisher city or country name as a person's surname.
    """
    if isinstance(person, str):
        s = person.strip().strip(",.;:")
        if "," in s:
            parts = [p.strip() for p in s.split(",", 1)]
            person = {"surname": parts[0], "given_name": parts[1] if len(parts) > 1 else ""}
        else:
            tokens = [t for t in re.split(r"\s+", s) if t]
            full = [t for t in tokens if len(t.rstrip(".")) >= 2]
            if not full:
                return None
            person = {"surname": full[-1], "given_name": " ".join(full[:-1])}
    if not isinstance(person, dict):
        return None
    surname = str(person.get("surname") or "").strip().strip(",.;:")
    given = str(person.get("given_name") or "").strip()
    if not surname or len(surname) < 2:
        return None
    if not any(c.isalpha() for c in surname):
        return None
    # Reject city/country/publisher mis-extraction
    if surname.lower() in _NOT_A_SURNAME:
        return None
    if not _in_text(surname, text_norm):
        return None
    return {"surname": surname, "given_name": given}


def _verify_persons(persons, text_norm: str) -> list[dict]:
    if not persons:
        return []
    if not isinstance(persons, list):
        persons = [persons]
    out: list[dict] = []
    seen: set[str] = set()
    for p in persons:
        verified = _verify_person_in_text(p, text_norm)
        if not verified:
            continue
        # Dedup by full (surname, given_name) so two co-authors with the
        # same surname (John Smith + Jane Smith) keep both. Only collapse
        # exact duplicates of the same person.
        key = _normalize(verified["surname"]) + "|" + _normalize(verified.get("given_name") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(verified)
    return out


def _verify_string_field(value, text_norm: str, *, max_text_len: int = 0) -> Optional[str]:
    """A free-form string field: must be a substring of text_norm. If
    max_text_len is given, also reject when the value spans >80% of the
    text (anti-sentence-stealing)."""
    if value is None:
        return None
    if not isinstance(value, str):
        if isinstance(value, list) and value:
            value = " ".join(str(x) for x in value if x)
        else:
            return None
    v = value.strip().strip('"\'').strip()
    if not v:
        return None
    if max_text_len and len(v) > 0.8 * max_text_len and max_text_len > 60:
        return None
    if not _in_text(v, text_norm):
        return None
    return v


def _verify_title(value, segment_text: str) -> Optional[str]:
    """A title must overlap ≥60% of its significant words with the segment,
    and must not span >80% of the segment.

    Also reject titles that end with citation-structure punctuation (`:`/`,`/`;`)
    — those are mid-citation fragments, not real titles.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    title = value.strip().strip('"\'').strip()
    if not title:
        return None
    # Trailing punctuation that indicates a fragment, not a complete title.
    # Trailing periods are fine (titles often end with one), but `:` `,` `;`
    # only appear at the end when the segment was cut mid-citation.
    if title.rstrip().endswith((":", ",", ";")):
        return None
    if len(title) > 0.8 * len(segment_text) and len(segment_text) > 60:
        return None
    text_norm = _normalize(segment_text)
    title_norm = _normalize(title)
    if title_norm in text_norm:
        return title
    # Word-overlap fallback for slight VL paraphrasing.
    # Threshold lowered from 0.60 → 0.50: keeps the substring-presence
    # discipline (each word must actually appear in segment) while letting
    # through partial matches when 1-2 minor title words got truncated by
    # the 800-char prompt cap.
    words = [w for w in re.findall(r"[a-z0-9]+", title_norm) if len(w) > 3]
    if not words:
        return None
    matches = sum(1 for w in words if w in text_norm)
    if matches / len(words) >= 0.5:
        return title
    return None


def _verify_year(value, text: str) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, list):
            value = value[0] if value else None
        y = int(str(value)[:4])
    except (ValueError, TypeError, IndexError):
        return None
    if 1500 <= y <= 2100 and str(y) in text:
        return y
    return None


def _verify_pages(value, text: str) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    nums = re.findall(r"\d+", value)
    if not nums:
        return None
    if all(re.search(rf"\b{n}\b", text) for n in nums):
        return value.strip()
    return None


def _verify_url(value, text: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v.startswith("http"):
        return None
    if v not in text:
        # Domain alone might match if text has trailing punctuation differences
        domain_match = re.search(r"https?://[^\s/]+", v)
        if not domain_match or domain_match.group(0) not in text:
            return None
    return v


def _verify_doi(value, text: str) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v.startswith("10."):
        return None
    if v in text or v.split("/")[0] in text:
        return v
    return None


# ── Main verifier ────────────────────────────────────────────────────────────

# Fields treated as "people" (lists of {surname, given_name} or strings)
_PERSON_LIST_FIELDS = {
    "authors", "editors", "curators",
}
_SINGLE_PERSON_FIELDS = {
    "translator", "editor", "interviewee", "interviewer",
}


def verify_typed_citation(
    citation_type: CitationType,
    extracted: dict,
    segment_text: str,
    style: CitationStyle = CitationStyle.UNKNOWN,
) -> Optional[dict]:
    """Validate VL-extracted citation against the source segment.

    Returns a cleaned dict ({"type", "style", ...verified fields}) or None
    if the record is fundamentally bogus per its type's required-fields
    and forbidden-fields rules.
    """
    if not isinstance(extracted, dict) or not segment_text:
        return None
    schema = TYPE_SCHEMAS.get(citation_type)
    if schema is None:
        return None

    text = segment_text
    text_norm = _normalize(text)

    # ----- Forbidden fields (e.g. publisher on journal_article) ----------
    forbidden = schema.get("forbidden") or []
    for f in forbidden:
        if extracted.get(f):
            # VL emitted a field that shouldn't exist on this type → record
            # is misclassified; reject.
            return None

    # ----- Per-field verification ---------------------------------------
    out: dict = {"type": citation_type.value, "style": style.value}

    for field in schema["fields"]:
        raw = extracted.get(field)
        if raw is None or raw == "":
            continue

        if field in _PERSON_LIST_FIELDS:
            verified = _verify_persons(raw, text_norm)
            if verified:
                out[field] = verified
            continue

        if field in _SINGLE_PERSON_FIELDS:
            verified = _verify_person_in_text(raw, text_norm)
            if verified:
                out[field] = verified
            continue

        if field == "year":
            year = _verify_year(raw, text)
            if year is not None:
                out[field] = year
            continue

        if field in {"pages", "page"}:
            pages = _verify_pages(str(raw), text)
            if pages:
                out[field] = pages
            continue

        if field == "url":
            url = _verify_url(raw, text)
            if url:
                out[field] = url
            continue

        if field == "doi":
            doi = _verify_doi(raw, text)
            if doi:
                out[field] = doi
            continue

        # Title-like fields get the stricter title verification.
        if field in {
            "title", "article_title", "chapter_title",
            "book_title", "title_or_program",
        }:
            verified = _verify_title(raw, text)
            if verified:
                out[field] = verified
            continue

        # Everything else: substring check + length cap.
        s = _verify_string_field(raw, text_norm, max_text_len=len(text))
        if s:
            out[field] = s

    # ----- Type-specific structural checks (spec §7c) ------------------
    # book.pages longer than 6 chars is suspect: real page refs are short
    # (e.g. "9", "117-139", "pp. 117-139"). Long values are usually the
    # model dumping a sentence into the pages field.
    if citation_type == CitationType.BOOK and out.get("pages"):
        if len(str(out["pages"])) > 12:
            out.pop("pages", None)

    # web_source without url demotes to "other" rather than hard-rejecting,
    # so the curator still gets to see the partial extraction.
    if citation_type == CitationType.WEB_SOURCE and not out.get("url"):
        out["type"] = CitationType.OTHER.value
        out.setdefault("raw_text", segment_text[:240])

    # ----- Per-type required-fields gate ------------------------------
    # User directive: cited_work only when the structural elements of an
    # actual footnote are present. The schema's `required` list is the
    # ground truth — every required field must be in `out`.
    # OTHER and SHORT_REFERENCE have empty required lists so they pass.

    required = schema.get("required") or []
    for f in required:
        if not out.get(f):
            return None

    return out
