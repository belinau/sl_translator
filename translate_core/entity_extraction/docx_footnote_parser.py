"""Extract footnotes from word/footnotes.xml inside a .docx, then parse each
as a citation. Designed for English Chicago notes-bibliography style (Kunst).

Each footnote in a Word doc has a numeric id. After skipping the standard
separator/continuation entries (id ≤ 0), the remaining ids correspond to
the visible footnote numbers IF the doc never deletes/reorders footnotes.

Returns FootnoteParsed records sharing the same shape as the Slovenian
markdown footnote parser so downstream code can stay unified.
"""

from __future__ import annotations

import unicodedata
import zipfile
from typing import List

try:
    # defusedxml hardens against XXE / billion-laughs attacks
    from defusedxml import ElementTree as ET
except ImportError:
    from xml.etree import ElementTree as ET

from .footnote_parser import (
    FootnoteParsed,
    parse_footnote_citation,
    _PAGE_TAIL_RE,
    _URL_RE,
    _IBID_RE,
)
from .bibliography_parser import ParsedCitation


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _extract_footnote_text(fn_elem) -> str:
    """Join all <w:t> text nodes within a footnote element, preserving
    italics as markdown asterisks (so existing parser detects them)."""
    out_parts: List[str] = []
    # Walk paragraphs in footnote
    for p in fn_elem.findall(f"{{{W_NS}}}p"):
        para_text: List[str] = []
        for r in p.findall(f"{{{W_NS}}}r"):
            # Detect italics in run properties
            italic = False
            rpr = r.find(f"{{{W_NS}}}rPr")
            if rpr is not None and rpr.find(f"{{{W_NS}}}i") is not None:
                italic = True
            for t in r.findall(f"{{{W_NS}}}t"):
                text = t.text or ""
                if italic and text.strip():
                    para_text.append(f"*{text}*")
                else:
                    para_text.append(text)
        out_parts.append("".join(para_text))
    return "\n".join(out_parts).strip()


def parse_docx_footnotes(docx_path: str) -> List[FootnoteParsed]:
    """Open the docx zip, parse word/footnotes.xml, return parsed footnotes
    ordered by footnote id (which is the visible footnote number)."""
    with zipfile.ZipFile(docx_path) as z:
        try:
            xml_bytes = z.read("word/footnotes.xml")
        except KeyError:
            return []

    xml_text = unicodedata.normalize("NFC", xml_bytes.decode("utf-8"))
    root = ET.fromstring(xml_text)

    out: List[FootnoteParsed] = []
    for fn in root.findall(f"{{{W_NS}}}footnote"):
        fid_str = fn.get(f"{{{W_NS}}}id", "0")
        try:
            fid = int(fid_str)
        except ValueError:
            continue
        if fid <= 0:
            # 0 = separator, -1 = continuation, skip
            continue
        raw = _extract_footnote_text(fn)
        if not raw:
            continue
        out.append(_parse_one_docx_footnote(fid, raw))
    return out


def _parse_one_docx_footnote(fid: int, raw: str) -> FootnoteParsed:
    fn = FootnoteParsed(footnote_number=fid, raw=raw)

    # Tail signals
    tail_page = _PAGE_TAIL_RE.search(raw.rstrip(". "))
    if tail_page:
        fn.page_tail = tail_page.group("page")
    tail_urls = _URL_RE.findall(raw)
    if tail_urls:
        fn.url_tail = tail_urls[-1].rstrip(",.;)]")

    # Pure Ibid
    if _IBID_RE.match(raw.strip()):
        fn.is_ibid = True
        cit = ParsedCitation(raw=raw, citation_type="ibid")
        cit.notes.append("whole_footnote_ibid")
        if tail_page:
            cit.pages = tail_page.group("page")
        fn.citations.append(cit)
        return fn

    # Split on `;` for multi-citation footnotes
    chunks = [c.strip() for c in raw.split(";") if c.strip()]
    for chunk in chunks:
        c = parse_footnote_citation(chunk)
        if c:
            fn.citations.append(c)
    return fn
