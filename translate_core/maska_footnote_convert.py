# translate_core/maska_footnote_convert.py
"""Mechanical Maska citation-format conversion for footnotes.

Applies the publisher's typographic conventions to footnote text WITHOUT
translating any prose. The footnote stays in the source language; only
the citation formatting is converted to Maska style.

No LLM, no translation. Pure deterministic string transformation.

Rules (applied in order):
  1.  English quotes "..." / "..." → Slovenian »...« (comma outside)
  2.  "in *Title*" → "v: *Title*" (chapter-in-collection, only after italic title)
  3.  ed. / eds. → ur.
  4.  trans. → prev.
  5.  p. / pp. → str.
  6.  Ibid. → *Ibid*.
  7.  "accessed Month DD, YYYY" → "(zadnji dostop DD. MM. YYYY)" (before date rule)
  8.  "Month DD, YYYY" → "DD. MM. YYYY"
  9.  Hyphen between digits → en-dash (page ranges)
  10. "and" between author names → "in" (not inside italic, not common words)
  11. Journal: *Journal* VOL, no. ISSUE (YEAR): PAGE → *Journal* ROMAN/ISSUE, YEAR, str. PAGE
  12. (City: Publisher, Year) → City: Publisher, Year (remove parens)
  13. no. → št.
  14. "See also" → "Glej tudi"
  15. "See, for example" → "Glej, na primer"
  16. "See" (citation signal) → "Glej"
  17. et al. → idr.
  18. vol. → letn.
  19. *...* italic markers preserved unchanged
"""

from __future__ import annotations

import re


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

_QUOTE_COMMA_RE = re.compile(r'\u201c([^\u201d]+),\u201d')
_QUOTE_PAIR_RE = re.compile(r'\u201c([^\u201d]+)\u201d')
_STRAIGHT_QUOTE_COMMA_RE = re.compile(r'"([^"]+),"')
_STRAIGHT_QUOTE_PAIR_RE = re.compile(r'"([^"]+)"')

# "in" → "v:" when followed by italic title (*Title*) OR by capitalized name(s)
# then italic title. Maska B: "v: avtor/urednik, *naslov dela*"
_IN_ITALIC_RE = re.compile(r'\bin\s+(\*[A-Z])')
_IN_EDITOR_RE = re.compile(r'\bin\s+([A-Z][a-z]+(?:\s+[A-Z][a-z.]+)+(?:\s*(?:,\s*|\s+and\s+)[A-Z][a-z]+(?:\s+[A-Z][a-z.]+)+)*\s*)(?=\(ur\.\)|\*)')

_ED_RE = re.compile(r'\beds?\.')
_TRANS_RE = re.compile(r'\btrans\.')
_PAGE_RE = re.compile(r'\bpp?\.')
_IBID_RE = re.compile(r'\bIbid\.?')

_MONTHS = {
    "January": "1", "February": "2", "March": "3", "April": "4",
    "May": "5", "June": "6", "July": "7", "August": "8",
    "September": "9", "October": "10", "November": "11", "December": "12",
}
_DATE_RE = re.compile(
    r'\b(' + "|".join(_MONTHS.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})\b'
)

# Consume optional surrounding parens so we don't double-wrap: "(accessed ...)" → "(zadnji dostop ...)"
_ACCESSED_RE = re.compile(
    r'\(?\s*(?:last\s+)?accessed\s+(' + "|".join(_MONTHS.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})\b\s*\)?',
    re.IGNORECASE,
)

_RANGE_RE = re.compile(r'(\d)-(\d)')

_COMMON_WORDS = {
    "The", "A", "An", "In", "For", "See", "This", "That", "These", "Those",
    "While", "When", "Although", "Even", "Most", "Many", "Some", "Such",
    "Both", "All", "No", "Not", "Nor", "To", "As", "But", "Or", "So", "Yet",
    "Still", "Only", "Just", "Each", "Every", "Here", "There", "Now", "Then",
    "Because", "Since", "If", "Whether", "About", "Against", "Between",
    "Through", "During", "Before", "After", "Above", "Below",
    "I", "We", "They", "He", "She", "It", "One", "Two", "Three",
    "First", "Last", "Next", "Chapter", "Volume", "Part", "Section",
    "Figure", "Table", "Note", "Cf", "Glej", "Ibid",
}

_JOURNAL_PAREN_RE = re.compile(
    r'\*([^*]+)\*\s+(\d+)(?:,\s*no\.\s*(\d+(?:-\d+)?))?\s*\((\d{4}(?:-\d{2,4})?)\)\s*:\s*(\d+(?:-\d+)?)'
)
_JOURNAL_NOPAREN_RE = re.compile(
    r'\*([^*]+)\*\s+(\d+)(?:,\s*no\.\s*(\d+(?:-\d+)?))?,\s*(\d{4}),\s*(\d+(?:-\d+)?)'
)
# *Journal* (YEAR): PAGE — no volume number
_JOURNAL_NOVOL_PAREN_RE = re.compile(
    r'\*([^*]+)\*\s*\((\d{4})\)\s*:\s*(\d+(?:-\d+)?)'
)
# *Journal* VOL (YEAR): PAGE — volume but no issue
_JOURNAL_VOL_ONLY_PAREN_RE = re.compile(
    r'\*([^*]+)\*\s+(\d+)\s*\((\d{4})\)\s*:\s*(\d+(?:-\d+)?)'
)

_PAREN_PUBLISHER_RE = re.compile(r'\(([A-Z][^)]+:\s*[^,]+,\s*\d{4})\)')

_NO_RE = re.compile(r'\bno\.\s*')

_SEE_ALSO_RE = re.compile(r'\bSee also\b')
_SEE_FOR_EXAMPLE_RE = re.compile(r'\bSee, for example\b')
_SEE_RE = re.compile(r'(?:(?:^|[,.]\s+)\s*)See\b(?!\s+also\b)(?!\s+, for example\b)')

_ETAL_RE = re.compile(r'\bet al\.')
_VOL_RE = re.compile(r'\bvol\.\s*', re.IGNORECASE)


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------


def _to_roman(n: int) -> str:
    """Convert integer to Roman numeral string."""
    nums = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'),
            (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'),
            (5, 'V'), (4, 'IV'), (1, 'I')]
    r = ''
    for val, sym in nums:
        while n >= val:
            r += sym
            n -= val
    return r


def convert_footnote_to_maska(text: str) -> str:
    """Apply Maska mechanical citation conversions to footnote text.

    No translation, no LLM. The text stays in the source language; only
    typographic conventions (quotes, markers, dates, dashes) are converted.
    Italic ``*...*`` markers are preserved unchanged.
    """
    t = text

    # 1. English quotes → Slovenian quotes (comma moved outside)
    t = _QUOTE_COMMA_RE.sub(r'»\1«,', t)
    t = _QUOTE_PAIR_RE.sub(r'»\1«', t)
    t = _STRAIGHT_QUOTE_COMMA_RE.sub(r'»\1«,', t)
    t = _STRAIGHT_QUOTE_PAIR_RE.sub(r'»\1«', t)

    # 2. "in *Title*" → "v: *Title*" (chapter-in-collection with italic title)
    t = _IN_ITALIC_RE.sub(r'v: \1', t)
    # 2b. "in Name Name (ur.)" or "in Name Name, *Title*" → "v: Name Name..."
    t = _IN_EDITOR_RE.sub(lambda m: f'v: {m.group(1)}', t)

    # 3. ed. / eds. → ur.
    t = _ED_RE.sub('ur.', t)

    # 4. trans. → prev.
    t = _TRANS_RE.sub('prev.', t)

    # 5. p. / pp. → str.
    t = _PAGE_RE.sub('str.', t)

    # 6. Ibid. → *Ibid*.
    t = _IBID_RE.sub('*Ibid*.', t)

    # 7. "accessed Month DD, YYYY" → "(zadnji dostop DD. MM. YYYY)"
    # MUST run before rule 8 (date conversion changes month names to numbers).
    def _accessed_repl(m: re.Match) -> str:
        month = next((v for k, v in _MONTHS.items()
                      if k.lower() == m.group(1).lower()), '?')
        return f'(zadnji dostop {m.group(2)}. {month}. {m.group(3)})'
    t = _ACCESSED_RE.sub(_accessed_repl, t)

    # 8. Date: "Month DD, YYYY" → "DD. MM. YYYY"
    def _date_repl(m: re.Match) -> str:
        return f"{m.group(2)}. {_MONTHS[m.group(1)]}. {m.group(3)}"
    t = _DATE_RE.sub(_date_repl, t)

    # 9. Page ranges: hyphen → en-dash between digits
    t = _RANGE_RE.sub(lambda m: m.group(1) + "\u2013" + m.group(2), t)

    # 10. "and" between author names → "in"
    def _and_repl(m: re.Match) -> str:
        before, after = m.group(1), m.group(2)
        if before in _COMMON_WORDS or after in _COMMON_WORDS:
            return m.group(0)
        if t[:m.start()].count('*') % 2 == 1:
            return m.group(0)  # inside italic
        return before + ' in ' + after
    t = re.sub(
        r'(\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+and\s+([A-Z][a-z]+)',
        _and_repl, t,
    )

    # 11. Journal citation restructure:
    # *Journal* VOL, no. ISSUE (YEAR): PAGE → *Journal* ROMAN/ISSUE, YEAR, str. PAGE
    def _journal_repl(m: re.Match) -> str:
        journal, vol, issue, year, page = m.groups()
        roman = _to_roman(int(vol))
        if issue:
            return f'*{journal}* {roman}/{issue}, {year}, str. {page}'
        return f'*{journal}* {roman}, {year}, str. {page}'

    def _journal_novol_repl(m: re.Match) -> str:
        journal, year, page = m.groups()
        return f'*{journal}* {year}, str. {page}'

    def _journal_vol_only_repl(m: re.Match) -> str:
        journal, vol, year, page = m.groups()
        roman = _to_roman(int(vol))
        return f'*{journal}* {roman}, {year}, str. {page}'

    t = _JOURNAL_PAREN_RE.sub(_journal_repl, t)
    t = _JOURNAL_NOPAREN_RE.sub(_journal_repl, t)
    t = _JOURNAL_VOL_ONLY_PAREN_RE.sub(_journal_vol_only_repl, t)
    t = _JOURNAL_NOVOL_PAREN_RE.sub(_journal_novol_repl, t)

    # 12. (City: Publisher, Year) → City: Publisher, Year (remove parens)
    t = _PAREN_PUBLISHER_RE.sub(r'\1', t)

    # 13. no. → št.
    t = _NO_RE.sub('št. ', t)

    # 14. "See also" → "Glej tudi"
    t = _SEE_ALSO_RE.sub('Glej tudi', t)
    # 15. "See, for example" → "Glej, na primer"
    t = _SEE_FOR_EXAMPLE_RE.sub('Glej, na primer', t)
    # 16. "See" (standalone citation signal) → "Glej"
    t = _SEE_RE.sub('Glej', t)

    # 17. et al. → idr.
    t = _ETAL_RE.sub('idr.', t)
    # 18. vol. → letn.
    t = _VOL_RE.sub('letn. ', t)

    return t


__all__ = ["convert_footnote_to_maska"]