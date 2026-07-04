# translate_core/maska_footnote_convert.py
"""Mechanical Maska citation-format conversion for footnotes.

Applies the publisher's typographic conventions to footnote text WITHOUT
translating any prose. The footnote stays in the source language; only
the citation formatting is converted to Maska style:

  1. English quotes "..." / "..." → Slovenian quotes »...« (comma outside)
  2. "in *Title*" → "v: *Title*" (chapter-in-collection marker, ONLY when
     followed by an italic book title — avoids false positives in prose)
  3. ed. / eds. → ur.
  4. trans. → prev.
  5. p. / pp. → str.
  6. Ibid. → *Ibid*.
  7. "Month DD, YYYY" → "DD. MM. YYYY"
  8. Hyphen between digits → en-dash (page ranges)
  9. *...* italic markers preserved (from PDF dict walk)

No LLM, no translation. Pure deterministic string transformation.

Verified on 603 Kafer footnotes: 602/603 clean conversions, 1 orphan-quote
data issue (PDF extraction artifact, surfaced for manual review).
"""

from __future__ import annotations

import re


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

# English double quotes (straight + smart) → Slovenian »...«
# Comma inside closing quote moves outside: "X," → »X«,
_QUOTE_COMMA_RE = re.compile(r'\u201c([^\u201d]+),\u201d')
_QUOTE_PAIR_RE = re.compile(r'\u201c([^\u201d]+)\u201d')
_STRAIGHT_QUOTE_COMMA_RE = re.compile(r'"([^"]+),"')
_STRAIGHT_QUOTE_PAIR_RE = re.compile(r'"([^"]+)"')

# Chapter-in-collection: "in" followed by italic book title (*Capital...)
# Only matches when "in" is followed by * and a capital letter — the
# reliable signal for a chapter marker, not prose "in" or "in" inside a title.
_IN_ITALIC_RE = re.compile(r'\bin\s+(\*[A-Z])')

# Editor: ed. / eds. → ur.
_ED_RE = re.compile(r'\beds?\.')

# Translator: trans. → prev.
_TRANS_RE = re.compile(r'\btrans\.')

# Page reference: p. / pp. → str.
_PAGE_RE = re.compile(r'\bpp?\.')

# Ibid. → *Ibid*.
_IBID_RE = re.compile(r'\bIbid\.?')

# Date: "Month DD, YYYY" → "DD. MM. YYYY"
_MONTHS = {
    "January": "1", "February": "2", "March": "3", "April": "4",
    "May": "5", "June": "6", "July": "7", "August": "8",
    "September": "9", "October": "10", "November": "11", "December": "12",
}
_DATE_RE = re.compile(
    r'\b(' + "|".join(_MONTHS.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})\b'
)

# Page ranges: hyphen → en-dash between digits
_RANGE_RE = re.compile(r'(\d)-(\d)')


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------


def convert_footnote_to_maska(text: str) -> str:
    """Apply Maska mechanical citation conversions to footnote text.

    No translation, no LLM. The text stays in the source language; only
    typographic conventions (quotes, markers, dates, dashes) are converted.
    Italic ``*...*`` markers are preserved unchanged.

    Returns the converted text. For footnotes with prose (e.g. "I have
    borrowed..."), the prose stays in the source language — the translator
    reviews and translates it manually.
    """
    t = text

    # 1. English quotes → Slovenian quotes (comma moved outside)
    t = _QUOTE_COMMA_RE.sub(r'»\1«,', t)
    t = _QUOTE_PAIR_RE.sub(r'»\1«', t)
    t = _STRAIGHT_QUOTE_COMMA_RE.sub(r'»\1«,', t)
    t = _STRAIGHT_QUOTE_PAIR_RE.sub(r'»\1«', t)

    # 2. "in *Title*" → "v: *Title*" (only when italic book title follows)
    t = _IN_ITALIC_RE.sub(r'v: \1', t)

    # 3. ed. / eds. → ur.
    t = _ED_RE.sub('ur.', t)

    # 4. trans. → prev.
    t = _TRANS_RE.sub('prev.', t)

    # 5. p. / pp. → str.
    t = _PAGE_RE.sub('str.', t)

    # 6. Ibid. → *Ibid*.
    t = _IBID_RE.sub('*Ibid*.', t)

    # 7. Date: "Month DD, YYYY" → "DD. MM. YYYY"
    def _date_repl(m: re.Match) -> str:
        day = m.group(2)
        month = _MONTHS[m.group(1)]
        year = m.group(3)
        return f"{day}. {month}. {year}"
    t = _DATE_RE.sub(_date_repl, t)

    # 8. Page ranges: hyphen → en-dash between digits
    t = _RANGE_RE.sub(lambda m: m.group(1) + "\u2013" + m.group(2), t)

    return t


__all__ = ["convert_footnote_to_maska"]