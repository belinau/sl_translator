# translate_core/maska_footnote_convert.py
"""Mechanical Maska citation-format conversion for footnotes.

Applies the publisher's typographic conventions to footnote text WITHOUT
translating any prose. No LLM, no translation. Pure deterministic string
transformation.

Rules (applied in order):
  1.  English quotes "..." / "..." → Slovenian »...« (comma outside)
  2.  "in *Title*" → "v: *Title*" (chapter-in-collection with italic title)
  2b. "in Name Name (ur.)" → "v: Name Name (ur.)" (chapter with editor)
  3.  ed. / eds. → ur.
  4.  trans. → prev.
  5.  p. / pp. → str.
  6.  Ibid. → *Ibid*.
  7.  "accessed Month DD, YYYY" → "(zadnji dostop DD. MM. YYYY)" (before date rule)
  8.  "Month DD, YYYY" → "DD. MM. YYYY"
  9.  Hyphen between digits → en-dash (page ranges)
  10. "and" between author names → "in"
  10b. "; and" connecting two citations → "; in"
  11. Journal: *Journal* VOL, no. ISSUE (YEAR): PAGE → *Journal* ROMAN/ISSUE, YEAR, str. PAGE
  11b.*Journal* (YEAR): PAGE → *Journal* YEAR, str. PAGE (no volume)
  11c.*Journal* VOL (YEAR): PAGE → *Journal* ROMAN, YEAR, str. PAGE (vol only)
  12. (City: Publisher, Year) → City: Publisher, Year (remove parens)
  13. no. → št.
  14. "See also" → "Glej tudi"
  15. "See, for example" → "Glej, na primer"
  16. "See" (citation signal) → "Glej"
  17. et al. → idr.
  18. vol. → letn.
  19. *...* italic markers preserved unchanged
  20. Add "str. " before bare page numbers at end of citation
  21. Web: move URL before (zadnji dostop ...), fix spacing
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

_IN_ITALIC_RE = re.compile(r'\bin\s+(\*[A-Z])')
_IN_EDITOR_RE = re.compile(
    r'\bin\s+([A-Z][a-z]+(?:\s+[A-Z][a-z.]+)+'
    r'(?:\s*(?:,\s*|\s+and\s+)[A-Z][a-z]+(?:\s+[A-Z][a-z.]+)+)*\s*)'
    r'(?=\(ur\.\)|\*)'
)

_ED_RE = re.compile(r'\beds?\.')
_TRANS_RE = re.compile(r'\btrans\.')
_PAGE_RE = re.compile(r'\bpp?\.')
_IBID_RE = re.compile(r'(?<!\*)\bIbid\.?')

_MONTHS = {
    "January": "1", "February": "2", "March": "3", "April": "4",
    "May": "5", "June": "6", "July": "7", "August": "8",
    "September": "9", "October": "10", "November": "11", "December": "12",
}
_DATE_RE = re.compile(
    r'\b(' + "|".join(_MONTHS.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})\b'
)

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
_JOURNAL_NOVOL_PAREN_RE = re.compile(
    r'\*([^*]+)\*\s*\((\d{4})\)\s*:\s*(\d+(?:-\d+)?)'
)
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

# Add "str. " before bare page numbers at end of citation or before ". " (new sentence)
# Key: only match 1-3 digit page numbers (NOT 4-digit years, NOT dates DD. MM.).
# Match the FULL page sequence: "36, 57" → "str. 36, 57" (one prefix).
# Also match lowercase roman numerals (xi, xv, etc.) as page numbers.
# Match at: end of string ($), or before ". " (period+space = new sentence)
# Negative lookahead: don't match if followed by ". D" (date pattern like "4. 7.")
_PAGE_SEQ = r'(\d{1,3}(?:\s*[\u2013-]\s*\d{1,3})?(?:,\s*\d{1,3})*|[ivxlcdm]{1,5})'
_END = r'(?=\.?\s*$|\. (?![A-Z]\.|\d{1,2}\.))'
_BARE_PAGE_RE = re.compile(
    r'(,)\s+' + _PAGE_SEQ + _END
)
_BARE_PAGE_QUOTE_RE = re.compile(
    r'(«,)\s+' + _PAGE_SEQ + _END
)
_BARE_PAGE_IBID_RE = re.compile(
    r'(\*Ibid\*\.,)\s+' + _PAGE_SEQ + _END
)
_BARE_PAGE_AFTER_YEAR_RE = re.compile(
    r'(\d{4},)\s+' + _PAGE_SEQ + _END
)

# "; and" connecting two citations → "; in" (Slovenian conjunction)
_SEMICOLON_AND_RE = re.compile(r';\s+and\s+')

# Web: "Title«,(zadnji dostop ...), URL" → "Title«, URL (zadnji dostop ...)"
# Move URL before access date, fix spacing
_WEB_ACCESS_AFTER_RE = re.compile(
    r'«,?\s*(\(zadnji dostop [^)]+\)),?\s*(https?://\S+)'
)
# Also: ",(zadnji dostop ...), URL" without closing quote
_WEB_ACCESS_AFTER_NOQUOTE_RE = re.compile(
    r',?\s*(\(zadnji dostop [^)]+\)),?\s*(https?://\S+)'
)


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
    # 2b. "in Name Name (ur.)" → "v: Name Name (ur.)"
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
        r'(\b[A-Z][a-z]+(?:\s+[A-Z][a-z.]+)*)\s+and\s+([A-Z][a-z]+)',
        _and_repl, t,
    )

    # 10b. "; and" connecting two citations → "; in"
    t = _SEMICOLON_AND_RE.sub('; in ', t)

    # 11. Journal citation restructure:
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
    # (City) after *Journal* is KEPT — it's a newspaper distinguisher
    # (e.g. *Sunday Times* (London) vs *Sunday Times* (UK))
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

    # 20. Add "str. " before bare page numbers at end of citation
    # Apply in order: year-comma-page, Ibid-comma, quote-comma, general-comma
    # The year-comma-page pattern runs first so "2006, 36, 57" → "2006, str. 36, 57"
    # (not "str. 2006, 36, 57")
    # 20. Add "str. " before bare page numbers at end of citation or before ". "
    # Check str. presence NEAR the match position, not globally — a mid-text
    # citation's "str." at the end shouldn't block a mid-text bare page.
    def _add_str_prefix(text: str, pat: re.Pattern) -> str:
        """Apply one str. insertion, checking no str. adjacent to the match."""
        def _repl(m: re.Match) -> str:
            # Check: is "str." already within 10 chars before the match?
            before = text[max(0, m.start()-10):m.start()]
            if 'str.' in before:
                return m.group(0)
            return m.group(1) + ' str. ' + m.group(2)
        return pat.sub(_repl, text)

    for pat in (_BARE_PAGE_AFTER_YEAR_RE, _BARE_PAGE_IBID_RE, _BARE_PAGE_QUOTE_RE, _BARE_PAGE_RE):
        t = _add_str_prefix(t, pat)
    # "Title«,(zadnji dostop ...), URL" → "Title«, URL (zadnji dostop ...)"
    def _web_reorder_repl(m: re.Match) -> str:
        access_date = m.group(1)
        url = m.group(2)
        return f'«, {url} {access_date}'
    t = _WEB_ACCESS_AFTER_RE.sub(_web_reorder_repl, t)

    # Also handle without closing quote: ",(zadnji dostop ...), URL"
    def _web_reorder_noquote_repl(m: re.Match) -> str:
        access_date = m.group(1)
        url = m.group(2)
        return f', {url} {access_date}'
    # Only if there's a URL after the access date
    t = _WEB_ACCESS_AFTER_NOQUOTE_RE.sub(_web_reorder_noquote_repl, t)

    return t


__all__ = ["convert_footnote_to_maska"]