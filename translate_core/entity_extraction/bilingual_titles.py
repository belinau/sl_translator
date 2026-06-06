"""Bilingual title handling for cross-language entity extraction.

Real-world TM patterns the current extractor mishandles:

1. **Bracket gloss in EN source, dropped in SL target**
   EN: `Die Avantgarde im Rückspiegel [Avant-Garde in the Rear-View Mirror]`
   SL: `Avantgarda v vzvratnem ogledalu`
   → Keep title_orig (German), title_en (from bracket), title_sl (target)

2. **Paren EN gloss in SL source, dropped in SL target**
   EN: `Umetnost kot stičišče nasprotij (Art as a Meeting Point of Differences)`
   SL: `Umetnost kot stičišče nasprotij`
   → title_sl from outside paren, title_en from inside paren

3. **SL edition substitution**
   EN: `(London, New York: Routledge, 2005)`
   SL: `(Ljubljana: Studia Humanitatis, 2010)` plus translator names
   → original_pub vs slovenian_edition fields, NOT translation of same data

Functions here are pure parsing helpers — no KG side-effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# Cities + publishers used to recognise SL edition substitution
SL_EDITION_CITIES = {"Ljubljana", "Maribor", "Koper", "Celje", "Kranj"}

# A publication-info block: `(City: Publisher, YYYY)` or
# `(City1, City2: Publisher, YYYY)` or `City: Publisher, YYYY`
PUB_INFO_RE = re.compile(
    r"\(?\s*"
    r"(?P<cities>(?:[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+(?:,\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+)*))"
    r"\s*:\s*"
    r"(?P<publisher>[^,()]+?)"
    r"\s*,\s*"
    r"(?P<year>\d{4})"
    r"\s*\)?"
)

# Page-range (49-54, 49–54, 49–54)
PAGE_RANGE_RE = re.compile(r"\b(\d{1,4})\s*[-–—]\s*(\d{1,4})\b")

# Translator marker (Slovenian: "prev.", English: "trans." or "translated by")
TRANSLATOR_MARKERS = (
    "prev.", "prevedel", "prevedla", "prevedli", "prevedlo",
    "trans.", "translated by", "translator:", "tr.",
)

# Bracket gloss: `Original [English Gloss]`
BRACKET_GLOSS_RE = re.compile(
    r"^(?P<orig>[^[\]]+?)\s*\[(?P<en>[^\]]+)\](?P<rest>.*)$"
)

# Paren gloss: `Slovenian Title (English Title)` — only when the paren
# contents look like a title, not a publication block.
PAREN_GLOSS_RE = re.compile(
    r"^(?P<base>[^()]{4,200}?)\s*\((?P<gloss>[^)]{4,200})\)(?P<rest>.*)$"
)


@dataclass
class BilingualTitle:
    """Decomposition of a title segment into language layers.

    Not every field is filled for every input — only those the segment
    structure justifies.
    """
    title_orig: Optional[str] = None     # original language (de, fr, etc.) if applicable
    title_en: Optional[str] = None       # English form (translation or gloss)
    title_sl: Optional[str] = None       # Slovenian form (translation or gloss)
    original_pub: Optional[dict] = None  # {city, publisher, year}
    slovenian_edition: Optional[dict] = None  # {city, publisher, year, translator}
    notes: list = field(default_factory=list)


def _looks_like_pub_info(text: str) -> bool:
    return bool(PUB_INFO_RE.search(text))


def _extract_pub_info(text: str) -> Optional[dict]:
    m = PUB_INFO_RE.search(text)
    if not m:
        return None
    cities = [c.strip() for c in m.group("cities").split(",")]
    return {
        "city": cities[0] if cities else "",
        "all_cities": cities,
        "publisher": m.group("publisher").strip(),
        "year": int(m.group("year")),
    }


def _extract_translator(sl_text: str) -> Optional[str]:
    """Find 'prev. Name Surname' in a Slovenian biblio entry."""
    low = sl_text.lower()
    for marker in TRANSLATOR_MARKERS:
        idx = low.find(marker)
        if idx < 0:
            continue
        tail = sl_text[idx + len(marker):].strip()
        # Translator name: up to next comma, period, or paren
        m = re.match(
            r"\s*([A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+"
            r"(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+){1,3})",
            tail,
        )
        if m:
            return m.group(1).strip()
    return None


def parse_bilingual_title(en_src: str, sl_tgt: str) -> BilingualTitle:
    """Decompose an EN-source / SL-target pair into title layers + pub info.

    Designed for citation / biblio segments. For artwork or general body
    segments, fields will be sparse — caller should check which fields
    are populated.
    """
    bt = BilingualTitle()
    en = en_src.strip()
    sl = sl_tgt.strip()

    # --- Pattern: bracket gloss in EN source ---
    # "Die Avantgarde im Rückspiegel [Avant-Garde in the Rear-View Mirror]"
    en_bracket = BRACKET_GLOSS_RE.match(en)
    if en_bracket:
        orig = en_bracket.group("orig").strip().rstrip(",;:.")
        gloss = en_bracket.group("en").strip().rstrip(",;:.")
        bt.title_orig = orig
        bt.title_en = gloss
        bt.notes.append("en_bracket_gloss")

    # --- Pattern: paren EN gloss in SL source (rare, but happens) ---
    # "Umetnost kot stičišče nasprotij (Art as a Meeting Point of Differences)"
    if not bt.title_orig:
        en_paren = PAREN_GLOSS_RE.match(en)
        if en_paren:
            inner = en_paren.group("gloss").strip()
            base = en_paren.group("base").strip().rstrip(",;:.")
            # Heuristic: inner is a gloss if it doesn't look like pub info
            # and base contains diacritics or all-SL words
            if not _looks_like_pub_info(inner):
                # Disambiguate: if base has SL diacritics, base is SL+gloss is EN
                if any(c in base for c in "šžčćđŠŽČĆĐ"):
                    bt.title_sl = base
                    bt.title_en = inner
                    bt.notes.append("paren_gloss_sl_with_en")

    # --- Pub-info detection in source vs. target ---
    src_pub = _extract_pub_info(en)
    tgt_pub = _extract_pub_info(sl)

    if src_pub:
        bt.original_pub = src_pub
    if tgt_pub:
        if src_pub and tgt_pub.get("city") in SL_EDITION_CITIES and tgt_pub.get("city") != src_pub.get("city"):
            # SL edition substitution: target uses a Slovenian edition
            sl_ed = dict(tgt_pub)
            translator = _extract_translator(sl)
            if translator:
                sl_ed["translator"] = translator
            bt.slovenian_edition = sl_ed
            bt.notes.append("sl_edition_substitution")
        elif src_pub is None:
            # Target has pub info but source doesn't — treat target as primary
            bt.original_pub = tgt_pub

    # --- Title fallback: strip known noise ---
    if bt.title_en is None and not bt.title_orig and not bt.title_sl:
        # No structural markers — best-effort take of leading clause
        bt.title_en = _leading_title_clause(en)
        bt.title_sl = _leading_title_clause(sl)
    elif bt.title_en is None and not bt.title_orig:
        # Got SL only, derive EN from source
        bt.title_en = _leading_title_clause(en)
    elif bt.title_sl is None and (bt.title_en or bt.title_orig):
        bt.title_sl = _leading_title_clause(sl)

    return bt


def _leading_title_clause(text: str) -> Optional[str]:
    """Take the leading title clause from a biblio-shaped string.

    Cuts at the first publication-info paren or known publisher city.
    Returns None for empty / clearly non-title input.
    """
    if not text:
        return None
    t = text.strip()
    # Cut at first '(' that looks like pub info
    m = PUB_INFO_RE.search(t)
    if m:
        t = t[: m.start()].rstrip(" ,;.")
    # If pattern is "Author, Title, ..." take the second comma-separated chunk
    parts = [p.strip() for p in t.split(",")]
    if len(parts) >= 2:
        # Drop leading author chunk if it looks like a name (1-3 capitalised
        # tokens)
        first = parts[0]
        if re.match(r"^[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+(?:\s+[A-ZŠŽČĆĐ][a-zšžčćđöüäáéíóúýň]+){0,3}$", first):
            return ", ".join(parts[1:]).strip() or None
    return t or None


def split_concatenated_artwork(text: str) -> list[dict]:
    """Split an artwork-record segment that concatenates multiple works.

    Input: e.g. `"Wings of Silence, 2004Under the Dead Stars (Alignment), 2004Tranquil Position, 2004Oil on canvas"`
    Output: list of dicts with keys {title, year, trailing_medium}.

    Heuristic: split at year-anchor `, YYYY` boundaries. The trailing chunk
    after the last year is treated as a shared medium descriptor.
    """
    if not text:
        return []
    # Find all year-anchors
    pattern = re.compile(r"([^,]+?)\s*,\s*(\d{4}(?:[-–]\d{2,4})?)")
    records: list[dict] = []
    last_end = 0
    for m in pattern.finditer(text):
        title = m.group(1).strip()
        # Reject if title is empty or looks like a medium phrase
        if not title or len(title) < 3:
            last_end = m.end()
            continue
        # The title may contain leading capitals that ran together with
        # the previous artwork's year — strip leading chars that look like
        # the tail of "YYYY"
        title = re.sub(r"^\d{2,4}\s*", "", title)
        records.append({
            "title": title.strip(),
            "year": m.group(2),
            "trailing_medium": "",
        })
        last_end = m.end()

    # Tail after last year may be a shared medium
    tail = text[last_end:].strip(" ,.;:")
    if tail and records:
        records[-1]["trailing_medium"] = tail
        # Also assign to all records if it looks like a medium phrase
        from .segment_classifier import MEDIUM_PATTERN
        if MEDIUM_PATTERN.search(tail):
            for r in records:
                if not r["trailing_medium"]:
                    r["trailing_medium"] = tail

    return records
