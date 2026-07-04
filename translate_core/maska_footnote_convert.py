# translate_core/maska_footnote_convert.py
"""Mechanical Maska citation-format conversion for footnotes.

Applies ALL Maska NAVODILA rules. No LLM, no translation. Pure deterministic.

Rules: quotes »...«, v: for chapters, ur./prev./str./Ibid, dates, en-dash,
and→in, journal roman numerals + no parens + str., remove (City: Publisher, Year),
no.→št., See→Glej, et al.→idr., vol.→letn., bare str. insertion, web reordering.
"""

from __future__ import annotations

import re

# Patterns
_QUOTE_COMMA_RE = re.compile(r'\u201c([^\u201d]+),\u201d')
_QUOTE_PAIR_RE = re.compile(r'\u201c([^\u201d]+)\u201d')
_STRAIGHT_QUOTE_COMMA_RE = re.compile(r'"([^"]+),"')
_STRAIGHT_QUOTE_PAIR_RE = re.compile(r'"([^"]+)"')
_IN_ITALIC_RE = re.compile(r'\bin\s+(\*[A-Z])')
_IN_EDITOR_RE = re.compile(
    r'\bin\s+([A-ZÀ-ÿ][A-Za-zÀ-ÿ.]+(?:\s+[A-ZÀ-ÿ][A-Za-zÀ-ÿ.]+)+'
    r'(?:\s*(?:,\s*|\s+and\s+)[A-ZÀ-ÿ][A-Za-zÀ-ÿ.]+(?:\s+[A-ZÀ-ÿ][A-Za-zÀ-ÿ.]+)+)*\s*)'
    r'(?=\(ur\.\)|\*)'
)
_ED_RE = re.compile(r'\beds?\.')
_TRANS_RE = re.compile(r'\btrans\.')
_PAGE_RE = re.compile(r'\bpp?\.')
_IBID_RE = re.compile(r'(?<!\*)\bIbid\.?')
_MONTHS = {"January":"1","February":"2","March":"3","April":"4","May":"5",
            "June":"6","July":"7","August":"8","September":"9","October":"10",
            "November":"11","December":"12"}
_DATE_RE = re.compile(r'\b(' + "|".join(_MONTHS.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})\b')
_ACCESSED_RE = re.compile(
    r'\(?\s*(?:last\s+)?accessed\s+(' + "|".join(_MONTHS.keys()) + r')\s+(\d{1,2}),?\s+(\d{4})\b\s*\)?',
    re.IGNORECASE)
_RANGE_RE = re.compile(r'(\d)-(\d)')
_COMMON_WORDS = {
    "The","A","An","In","For","See","This","That","These","Those","While","When",
    "Although","Even","Most","Many","Some","Such","Both","All","No","Not","Nor",
    "To","As","But","Or","So","Yet","Still","Only","Just","Each","Every",
    "Here","There","Now","Then","Because","Since","If","Whether","About",
    "Against","Between","Through","During","Before","After","Above","Below",
    "I","We","They","He","She","It","One","Two","Three","First","Last","Next",
    "Chapter","Volume","Part","Section","Figure","Table","Note","Cf","Glej","Ibid"}

# Journal patterns: handle italic/non-italic, en-dash, roman/arabic volumes, št./no./nos.
_J_NAME = r'(?:\*([^*]+)\*|(?<![A-Za-z])([A-ZÀ-ÿ][A-Za-zÀ-ÿ.&]+(?:\s+[A-ZÀ-ÿ][A-Za-zÀ-ÿ.&]+)*))'
_ISSUE = r'(?:[,\s]+(?:nos?|št)\.\s*(\d+(?:[\u2013-]\d+)?))?'
_VOL = r'(\d+|[IVXLCDM]+)'
_PAGE = r'(\d+(?:[\u2013-]\d+)?)'
_JOURNAL_PAREN_RE = re.compile(
    _J_NAME + r'\s+' + _VOL + _ISSUE + r'\s*\((\d{4}(?:-\d{2,4})?)\)\s*:\s*' + _PAGE)
_JOURNAL_ISSUE_ONLY_PAREN_RE = re.compile(
    _J_NAME + r'[,\s]+(?:nos?|št)\.\s*(\d+(?:[\u2013-]\d+)?)\s*\((\d{4})\)\s*:\s*' + _PAGE)
_JOURNAL_NOVOL_PAREN_RE = re.compile(
    _J_NAME + r'\s*\((\d{4})\)\s*:\s*' + _PAGE)
_JOURNAL_VOL_ONLY_PAREN_RE = re.compile(
    _J_NAME + r'\s+' + _VOL + r'\s*\((\d{4})\)\s*:\s*' + _PAGE)
_JOURNAL_NOPAREN_RE = re.compile(
    _J_NAME + r'\s+' + _VOL + r'(?:[,\s]+(?:nos?|št)\.\s*(\d+(?:[\u2013-]\d+)?))?,\s*(\d{4}),\s*' + _PAGE)

# Generic fallback: remove (YEAR) parens in any context
# Allow alphanumeric pages (c3, n17, 190n17) and end-of-string
_GEN_YEAR_COLON_PAGE_RE = re.compile(r'\s*\((\d{4})\)\s*:\s*((?:\d+\w*|\w+\d+)(?:[\u2013-]\w+)?)')
_GEN_YEAR_COMMA_RE = re.compile(r'\s*\((\d{4})\)\s*,')
_GEN_YEAR_SEMICOLON_RE = re.compile(r'\s*\((\d{4})\)\s*;')
_GEN_YEAR_PERIOD_RE = re.compile(r'\s*\((\d{4})\)\.(?:\s|$)')

_PAREN_PUBLISHER_RE = re.compile(r'\(([A-Z][^)]+:\s*[^,]+,\s*\d{4})\)')
_NO_RE = re.compile(r'\bnos?\.\s*')
_SEE_ALSO_RE = re.compile(r'\bSee also\b')
_SEE_FOR_EXAMPLE_RE = re.compile(r'\bSee, for example\b')
_SEE_AMONG_RE = re.compile(r'\bSee, among others\b')
_SEE_RE = re.compile(r'(?:(?<=\.\s)|(?<=,\s)|^)See\b(?!\s+also\b)(?!\s+, for example\b)(?!\s+, among\b)')
_ETAL_RE = re.compile(r'\bet al\.')
_VOL_RE = re.compile(r'\bvol\.\s*', re.IGNORECASE)

# Bare page str. insertion
_PAGE_SEQ = r'(\d{1,3}(?:\s*[\u2013-]\s*\d{1,3})?(?:,\s*\d{1,3})*|[ivxlcdm]{1,5})'
_END = r'(?=\.?\s*$|\. (?![A-Z]\.|\d{1,2}\.))'
_BARE_PAGE_RE = re.compile(r'(,)\s+' + _PAGE_SEQ + _END)
_BARE_PAGE_QUOTE_RE = re.compile(r'(«,)\s+' + _PAGE_SEQ + _END)
_BARE_PAGE_IBID_RE = re.compile(r'(\*Ibid\*\.,)\s+' + _PAGE_SEQ + _END)
_BARE_PAGE_AFTER_YEAR_RE = re.compile(r'(\d{4},)\s+' + _PAGE_SEQ + _END)

_SEMICOLON_AND_RE = re.compile(r';\s+and\s+')
_WEB_ACCESS_AFTER_RE = re.compile(r'«,?\s*(\(zadnji dostop [^)]+\)),?\s*(https?://\S+)')
_WEB_ACCESS_AFTER_NOQUOTE_RE = re.compile(r',?\s*(\(zadnji dostop [^)]+\)),?\s*(https?://\S+)')


def _to_roman(n: int) -> str:
    nums = [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),
            (50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]
    r = ''
    for val, sym in nums:
        while n >= val: r += sym; n -= val
    return r


def convert_footnote_to_maska(text: str) -> str:
    """Apply ALL Maska mechanical citation conversions. No LLM, no translation."""
    t = text

    # 1. Quotes
    t = _QUOTE_COMMA_RE.sub(r'»\1«,', t)
    t = _QUOTE_PAIR_RE.sub(r'»\1«', t)
    t = _STRAIGHT_QUOTE_COMMA_RE.sub(r'»\1«,', t)
    t = _STRAIGHT_QUOTE_PAIR_RE.sub(r'»\1«', t)

    # 2. v: for chapters
    t = _IN_ITALIC_RE.sub(r'v: \1', t)
    t = _IN_EDITOR_RE.sub(lambda m: f'v: {m.group(1)}', t)

    # 3-6. Markers
    t = _ED_RE.sub('ur.', t)
    t = _TRANS_RE.sub('prev.', t)
    t = _PAGE_RE.sub('str.', t)
    t = _IBID_RE.sub('*Ibid*.', t)

    # 7-8. Dates (accessed before general)
    def _accessed_repl(m):
        month = next((v for k, v in _MONTHS.items() if k.lower() == m.group(1).lower()), '?')
        return f'(zadnji dostop {m.group(2)}. {month}. {m.group(3)})'
    t = _ACCESSED_RE.sub(_accessed_repl, t)
    t = _DATE_RE.sub(lambda m: f"{m.group(2)}. {_MONTHS[m.group(1)]}. {m.group(3)}", t)

    # 9. En-dash
    t = _RANGE_RE.sub(lambda m: m.group(1) + "\u2013" + m.group(2), t)

    # 10. and → in (handles internal capitals, diacritics)
    def _and_repl(m):
        before, after = m.group(1), m.group(2)
        if before in _COMMON_WORDS or after in _COMMON_WORDS: return m.group(0)
        if t[:m.start()].count('*') % 2 == 1: return m.group(0)
        return before + ' in ' + after
    t = re.sub(r'(\b[A-ZÀ-ÿ][A-Za-zÀ-ÿ.]*(?:\s+[A-ZÀ-ÿ][A-Za-zÀ-ÿ.]*)*)\s+and\s+([A-ZÀ-ÿ][A-Za-zÀ-ÿ]+)', _and_repl, t)
    t = _SEMICOLON_AND_RE.sub('; in ', t)

    # 11. Journal restructure (roman numerals, remove parens, str.)
    def _jname(m):
        return m.group(1) or m.group(2)

    def _journal_repl(m):
        j = _jname(m); italic = m.group(1) is not None
        js = f'*{j}*' if italic else j
        vol = m.group(3); issue = m.group(4); year = m.group(5); page = m.group(6)
        roman = vol if not vol.isdigit() else _to_roman(int(vol))
        return f'{js} {roman}/{issue}, {year}, str. {page}' if issue else f'{js} {roman}, {year}, str. {page}'

    def _journal_issue_only_repl(m):
        j = _jname(m); italic = m.group(1) is not None
        js = f'*{j}*' if italic else j
        issue, year, page = m.group(3), m.group(4), m.group(5)
        return f'{js} {issue}, {year}, str. {page}'

    def _journal_novol_repl(m):
        j = _jname(m); italic = m.group(1) is not None
        js = f'*{j}*' if italic else j
        year, page = m.group(3), m.group(4)
        return f'{js} {year}, str. {page}'

    def _journal_vol_only_repl(m):
        j = _jname(m); italic = m.group(1) is not None
        js = f'*{j}*' if italic else j
        vol, year, page = m.group(3), m.group(4), m.group(5)
        roman = vol if not vol.isdigit() else _to_roman(int(vol))
        return f'{js} {roman}, {year}, str. {page}'

    def _journal_noparen_repl(m):
        j = _jname(m); italic = m.group(1) is not None
        js = f'*{j}*' if italic else j
        vol, issue, year, page = m.group(3), m.group(4), m.group(5), m.group(6)
        roman = vol if not vol.isdigit() else _to_roman(int(vol))
        return f'{js} {roman}/{issue}, {year}, str. {page}' if issue else f'{js} {roman}, {year}, str. {page}'

    t = _JOURNAL_PAREN_RE.sub(_journal_repl, t)
    t = _JOURNAL_ISSUE_ONLY_PAREN_RE.sub(_journal_issue_only_repl, t)
    t = _JOURNAL_VOL_ONLY_PAREN_RE.sub(_journal_vol_only_repl, t)
    t = _JOURNAL_NOVOL_PAREN_RE.sub(_journal_novol_repl, t)
    t = _JOURNAL_NOPAREN_RE.sub(_journal_noparen_repl, t)

    # 11f-g. Generic fallback: remove (YEAR) parens
    t = _GEN_YEAR_COLON_PAGE_RE.sub(r', \1, str. \2', t)
    t = _GEN_YEAR_COMMA_RE.sub(r', \1,', t)
    t = _GEN_YEAR_SEMICOLON_RE.sub(r', \1;', t)
    t = _GEN_YEAR_PERIOD_RE.sub(lambda m: f', {m.group(1)}. ' if m.group(0)[-1] == ' ' else f', {m.group(1)}.', t)

    # 12. Remove (City: Publisher, Year) parens (NOT newspaper city distinguishers)
    t = _PAREN_PUBLISHER_RE.sub(r'\1', t)

    # 13. no./nos. → št.
    t = _NO_RE.sub('št. ', t)

    # 14-16. See → Glej
    t = _SEE_ALSO_RE.sub('Glej tudi', t)
    t = _SEE_FOR_EXAMPLE_RE.sub('Glej, na primer', t)
    t = _SEE_AMONG_RE.sub('Glej, med drugimi', t)
    t = _SEE_RE.sub('Glej', t)

    # 17-18. et al.→idr., vol.→letn.
    t = _ETAL_RE.sub('idr.', t)
    t = _VOL_RE.sub('letn. ', t)

    # 20. Add str. before bare page numbers
    def _add_str(text, pat):
        def _repl(m):
            if 'str.' in text[max(0,m.start()-10):m.start()]: return m.group(0)
            return m.group(1) + ' str. ' + m.group(2)
        return pat.sub(_repl, text)
    for pat in (_BARE_PAGE_AFTER_YEAR_RE, _BARE_PAGE_IBID_RE, _BARE_PAGE_QUOTE_RE, _BARE_PAGE_RE):
        t = _add_str(t, pat)

    # 21. Web: URL before (zadnji dostop ...)
    t = _WEB_ACCESS_AFTER_RE.sub(lambda m: f'«, {m.group(2)} {m.group(1)}', t)
    t = _WEB_ACCESS_AFTER_NOQUOTE_RE.sub(lambda m: f', {m.group(2)} {m.group(1)}', t)

    return t

__all__ = ["convert_footnote_to_maska"]