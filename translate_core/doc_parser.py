# translate_core/doc_parser.py
#
# Advanced Document Parser supporting:
# 1. Pre-translation style restructuring (Endnote to Footnote normalization)
# 2. House-style enumeration remapping
# 3. Post-translation DOCX compilation with native bottom-of-page Word Footnotes
# 4. Academic DOCX parsing with footnote/endnote extraction
# 5. Emphasis-aware DOCX export (italic/bold from markdown markup)

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Tuple

from markitdown import MarkItDown

# python-docx imports
try:
    import docx
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.section import WD_SECTION
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    HAS_DOCX = True
except ImportError:
    docx = None  # type: ignore[assignment]
    Inches = Pt = WD_ALIGN_PARAGRAPH = WD_SECTION = OxmlElement = qn = None  # type: ignore[assignment]
    HAS_DOCX = False


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_FOOTNOTES_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
)
_FOOTNOTES_CT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
)

# Emphasis regex for markdown *...* / **...** / ***...*** in compiled output.
_MD_EMPHASIS_RE = re.compile(
    r"(\*\*\*[^*\n]+\*\*\*|\*\*[^*\n]+\*\*|\*[^*\n]+\*)"
)


# Legacy typography defaults (pre-Maska). Used when no segments/manifest is
# provided so existing md_text-only exports don't change appearance.
_LEGACY_TYPOGRAPHY = {
    "body_font": "Georgia",
    "body_size_pt": 11,
    "line_spacing": 1.25,
    "margins_in": 1.2,
    "space_after_pt": 8,
    "h1_size_pt": 18,
    "h2_size_pt": 13,
    "blockquote_size_pt": 11,
    "blockquote_line_spacing": 1.0,
    "blockquote_indent_in": 0.5,
}


def _apply_page_setup(doc, typo: dict) -> None:
    """Set page size + margins on every section of doc.
    Maska: A4, 1-inch margins all sides. Legacy: Letter, 1.2-inch margins."""
    assert docx is not None and Inches is not None
    margins = typo.get("margins_in", 1.2)
    page_size = typo.get("page_size", "Letter")
    for section in doc.sections:
        section.top_margin = Inches(margins)
        section.bottom_margin = Inches(margins)
        section.left_margin = Inches(margins)
        section.right_margin = Inches(margins)
        if page_size == "A4":
            # A4: 210mm x 297mm = 8.27in x 11.69in
            section.page_width = Inches(8.27)
            section.page_height = Inches(11.69)
        else:
            # Letter: 8.5in x 11in (python-docx default)
            section.page_width = Inches(8.5)
            section.page_height = Inches(11)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_footnotes_xml(footnotes: List[Tuple[int, str]]) -> bytes:
    """Build the contents of word/footnotes.xml.

    Each footnote text is split on markdown emphasis markers so that
    *italic*, **bold**, and ***bold-italic*** render as real Word runs.
    """
    parts: List[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<w:footnotes xmlns:w="{_W_NS}">',
        '<w:footnote w:type="separator" w:id="-1">'
        '<w:p><w:r><w:separator/></w:r></w:p></w:footnote>',
        '<w:footnote w:type="continuationSeparator" w:id="0">'
        '<w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>',
    ]
    for fn_id, text in footnotes:
        fn_parts = [f'<w:footnote w:id="{fn_id}">']
        fn_parts.append('<w:p>')
        fn_parts.append('<w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr>')
        fn_parts.append('<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr>')
        fn_parts.append('<w:footnoteRef/></w:r>')
        spans = _MD_EMPHASIS_RE.split(text)
        if spans == [text]:
            fn_parts.append(
                f'<w:r><w:t xml:space="preserve"> {_xml_escape(text)}</w:t></w:r>'
            )
        else:
            fn_parts.append('<w:r><w:t xml:space="preserve"> </w:t></w:r>')
            for span in spans:
                if not span:
                    continue
                if span.startswith("***") and span.endswith("***"):
                    inner = span[3:-3]
                    fn_parts.append(
                        f'<w:r><w:rPr><w:b/><w:i/></w:rPr>'
                        f'<w:t xml:space="preserve">{_xml_escape(inner)}</w:t></w:r>'
                    )
                elif span.startswith("**") and span.endswith("**"):
                    inner = span[2:-2]
                    fn_parts.append(
                        f'<w:r><w:rPr><w:b/></w:rPr>'
                        f'<w:t xml:space="preserve">{_xml_escape(inner)}</w:t></w:r>'
                    )
                elif span.startswith("*") and span.endswith("*") and not span.startswith("**"):
                    inner = span[1:-1]
                    fn_parts.append(
                        f'<w:r><w:rPr><w:i/></w:rPr>'
                        f'<w:t xml:space="preserve">{_xml_escape(inner)}</w:t></w:r>'
                    )
                else:
                    fn_parts.append(
                        f'<w:r><w:t xml:space="preserve">{_xml_escape(span)}</w:t></w:r>'
                    )
        fn_parts.append('</w:p>')
        fn_parts.append('</w:footnote>')
        parts.extend(fn_parts)
    parts.append("</w:footnotes>")
    return "".join(parts).encode("utf-8")


def _inject_footnotes_part(
    docx_path: Path, footnotes: List[Tuple[int, str]],
    typo: dict | None = None,
) -> None:
    """Post-process a saved .docx to add a footnotes.xml part, its
    relationship in word/_rels/document.xml.rels, and the Content_Types
    override. python-docx 1.x does not expose this through its API."""
    with zipfile.ZipFile(docx_path, "r") as zf:
        files = {name: zf.read(name) for name in zf.namelist()}

    files["word/footnotes.xml"] = _build_footnotes_xml(footnotes)

    rels_path = "word/_rels/document.xml.rels"
    rels_xml = files[rels_path].decode("utf-8")
    used_ids = set(re.findall(r'Id="(rId\d+)"', rels_xml))
    n = 1
    while f"rId{n}" in used_ids:
        n += 1
    fn_rel_id = f"rId{n}"
    rels_xml = rels_xml.replace(
        "</Relationships>",
        '<Relationship Id="' + fn_rel_id + '" Type="' + _FOOTNOTES_REL_TYPE + '" '
        'Target="footnotes.xml"/></Relationships>',
    )
    files[rels_path] = rels_xml.encode("utf-8")

    ct_path = "[Content_Types].xml"
    ct_xml = files[ct_path].decode("utf-8")
    if "footnotes+xml" not in ct_xml:
        ct_xml = ct_xml.replace(
            "</Types>",
            '<Override PartName="/word/footnotes.xml" '
            'ContentType="' + _FOOTNOTES_CT + '"/></Types>',
        )
        files[ct_path] = ct_xml.encode("utf-8")

    # Inject FootnoteText + FootnoteReference styles into styles.xml so
    # footnote numbers render as superscript and footnote paragraphs use
    # the correct font size/spacing from the publisher typography profile.
    styles_path = "word/styles.xml"
    if styles_path in files:
        styles_xml = files[styles_path].decode("utf-8")
        if "FootnoteReference" not in styles_xml:
            fn_size = (typo or {}).get("footnote_size_pt", 10)
            fn_spacing = (typo or {}).get("footnote_line_spacing", 1.0)
            fn_size_twips = int(fn_size * 2)  # half-points → twips isn't right;
            # Word uses w:sz in half-points, so 10pt → w:sz="20"
            fn_size_hp = int(fn_size * 2)
            # FootnoteText: paragraph style for footnote body text.
            fn_text_style = (
                '<w:style w:type="paragraph" w:styleId="FootnoteText">'
                '<w:name w:val="footnote text"/>'
                '<w:basedOn w:val="Normal"/>'
                '<w:pPr><w:spacing w:line="' + str(int(fn_spacing * 240)) + '"'
                ' w:lineRule="auto" w:after="0"/></w:pPr>'
                '<w:rPr><w:sz w:val="' + str(fn_size_hp) + '"/>'
                '<w:szCs w:val="' + str(fn_size_hp) + '"/></w:rPr>'
                '</w:style>'
            )
            # FootnoteReference: character style with superscript vertAlign.
            fn_ref_style = (
                '<w:style w:type="character" w:styleId="FootnoteReference">'
                '<w:name w:val="footnote reference"/>'
                '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
                '</w:style>'
            )
            styles_xml = styles_xml.replace(
                "</w:styles>", fn_text_style + fn_ref_style + "</w:styles>"
            )
            files[styles_path] = styles_xml.encode("utf-8")
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)





def footnote_alignment_report(segments: list) -> dict:
    """Count footnote defs vs inline refs in segments.

    Returns dict with keys: defs, refs, aligned (bool),
    missing_def_numbers, unreferenced_def_numbers.
    """
    def_nums: set = set()
    ref_nums: set = set()
    for seg in segments:
        src = seg.get("source", "")
        if src.lstrip().startswith("[^"):
            m = re.match(r"^\[\^(\w+)\]:", src.lstrip())
            if m:
                def_nums.add(m.group(1))
        else:
            for rm in re.finditer(r"\[\^(\w+)\]", src):
                ref_nums.add(rm.group(1))
    missing = sorted(ref_nums - def_nums, key=lambda x: int(x) if x.isdigit() else 0)
    unreferenced = sorted(def_nums - ref_nums, key=lambda x: int(x) if x.isdigit() else 0)
    return {
        "defs": len(def_nums),
        "refs": len(ref_nums),
        "aligned": len(def_nums) == len(ref_nums) and not missing,
        "missing_def_numbers": missing,
        "unreferenced_def_numbers": unreferenced,
    }


class DocumentParser:
    """Wrapper around MarkItDown to convert, normalize, and compile
    styled book-like documents."""

    def __init__(self, enable_plugins: bool = False):
        self.md = MarkItDown(enable_plugins=enable_plugins)
        self._last_vl_result = None
        self.last_footnote_report: dict | None = None
        self.last_reflow_report: dict | None = None

    # ===================================================================
    # STAGE 1: Pre-Translation Normalization (Ingestion)
    # ===================================================================

    # ── PDF line reflow ────────────────────────────────────────────────
    # PDF extraction emits each visual line separately, frequently with a
    # blank line between wrapped lines of ONE sentence. Downstream
    # segmentation treats blank lines as paragraph breaks, so without
    # reflow a translator gets sentence fragments as segments. Reflow
    # rejoins wrapped lines into sentence units and drops page-layout
    # debris (bare page numbers, running headers) that publishers
    # regenerate anyway.

    # Bare page number: "179"
    _PAGE_NUM_LINE_RE = re.compile(r"^\s*\d{1,4}\s*$")
    # Running header: "180  |  Notes to Pages 4-7"  /  "Notes ...  |  181"
    _RUNNING_HEADER_RE = re.compile(
        r"^\s*\d{1,4}\s*\|.{0,80}$|^.{0,80}\|\s*\d{1,4}\s*$"
    )
    # Lines that must always start their own unit.
    _STRUCTURAL_LINE_RE = re.compile(
        r"^\s*(?:#+\s|>\s|[-*+]\s|\d{1,3}[.)]\s|\[\^\w+\]:)"
    )
    # Footnote-marker digits glued after terminal punctuation ("future.\u201d1")
    _TRAILING_FN_DIGITS_RE = re.compile(
        r"([.!?:;\"\u201d\u00bb\u2019\u2026])\d{1,3}$"
    )
    _TERMINAL_CHARS = ".!?:;\"\u201d\u00bb\u2019\u2026"

    def _reflow_pdf_text(self, text: str) -> str:
        """Rejoin PDF-wrapped lines into sentence units.

        Join decisions are made against the LAST PHYSICAL LINE appended to
        a unit, never the accreted unit — otherwise one join makes the
        unit "long" and it greedily swallows whole regions (copyright
        pages, TOCs) that have no terminal punctuation. Prose wraps are
        full-width lines; title/TOC/copyright rows are short lines, and a
        short line never continues into the next one unless it ends with
        an explicit continuation mark (hyphen, comma, semicolon).
        Structural lines (headings, note rows, [^N]: defs, list items)
        always start a new unit; page numbers and running headers are
        dropped and counted in ``last_reflow_report``.
        """
        dropped = 0
        units: List[str] = []
        last_line = ""  # last physical line appended to units[-1]

        def _ends_sentence(s: str) -> bool:
            s = self._TRAILING_FN_DIGITS_RE.sub(r"\1", s.rstrip())
            return bool(s) and s[-1] in self._TERMINAL_CHARS

        # TOC / index rows: short lines ending in a page locator (arabic
        # digits or roman numerals). Self-terminated — never joined onto,
        # so the TOC and index keep one row per entry.
        _locator_tail = re.compile(
            r"\s(?:\d{1,4}|[ivxlcdm]{1,7})\s*$", re.IGNORECASE
        )

        def _self_terminated(s: str) -> bool:
            return len(s) < 90 and bool(_locator_tail.search(s))

        for raw in text.splitlines():
            s = raw.strip()
            if not s:
                continue
            if self._PAGE_NUM_LINE_RE.match(s) or self._RUNNING_HEADER_RE.match(s):
                dropped += 1
                continue
            if self._STRUCTURAL_LINE_RE.match(raw) or not units:
                units.append(s)
                last_line = s
                continue
            if _self_terminated(last_line):
                units.append(s)
                last_line = s
                continue
            # Continuation marks join regardless of line width.
            if last_line.endswith("-") and last_line[-2:-1].isalpha() and s[:1].isalpha():
                units[-1] = units[-1][:-1] + s  # "medi-" + "cal" -> "medical"
            elif last_line.endswith((",", ";")):
                units[-1] = units[-1] + " " + s
            # Otherwise only a full-width line that ends mid-sentence
            # continues — short lines are headings/TOC/title rows.
            elif len(last_line) >= 60 and not _ends_sentence(last_line):
                units[-1] = units[-1] + " " + s
            else:
                units.append(s)
                last_line = s
                continue
            last_line = s

        self.last_reflow_report = {
            "dropped_page_artifacts": dropped,
            "units": len(units),
        }
        return "\n\n".join(units)

    def preprocess_source_style(
        self,
        raw_markdown: str,
        remap_lists: bool = True,
        list_style: str = "alphabetical",
        convert_endnotes: bool = True,  # kept for backward compat; no-op
    ) -> str:
        """Normalize the source text structure BEFORE translation.

        Calls _renumber_footnotes then optionally remaps list lines.
        The convert_endnotes param is kept for backward compatibility.
        """
        text, report = self._renumber_footnotes(raw_markdown)
        self.last_footnote_report = report

        if remap_lists:
            text = "\n".join(
                self._remap_list_line(line, list_style)
                for line in text.splitlines()
            )
        return text

    # Candidate body markers, tried left-to-right on each line:
    #   1. existing inline [^N] refs
    #   2. bracketed [N]
    #   3. bare digits PyMuPDF leaves where superscripts were (no preceding
    #      space; attached to a word or closing-punctuation character).
    # The superscript-flattening artifact is specific to PDF text extraction,
    # so its shape lives here; citation-content decisions are delegated to
    # entity_extraction.segment_classifier below.
    _BODY_CAND_RE = re.compile(
        r"\[\^(\d{1,3})\]"
        r"|\[(\d{1,3})\]"
        r"|(?<!\dn)(?<=[a-zA-Z\".,;:!?)\]\u201d\u2019\u00bb])(\d{1,3})"
        r"(?=\s|[.,;:!?\"\)\]\u201d\u2019\u00bb](?:\s|$)|$)"
    )

    def _renumber_footnotes(self, text: str) -> tuple:
        """Renumber footnote refs/defs via numeric-ladder detection.

        No header lexicon: a notes section is recognised as a *ladder* — a
        maximal run of number-prefixed rows whose numbers ascend 1, 2, 3, …
        within a bounded line-gap. That signal is arithmetic and language-
        independent ("Notes", "Endnotes", "Opombe", "Anmerkungen" all look
        identical to it). Content classification is delegated to the
        existing citation pipeline (segment_classifier shape detectors),
        and genuinely ambiguous ladders are referred to the live smol
        model; when smol is unavailable they are left unconverted and
        surfaced in the report — never silently guessed.

        Three input classes:
          (a) ladders found: promote rows to [^G]: with global numbering,
              then accept body markers (bare digits / [N] / [^N]) by
              per-chapter sequence expectation;
          (b) [^N]:-marked defs, no ladders: validated label-map
              renumbering only;
          (c) neither: convert nothing.

        Returns (processed_text, report_dict).
        """
        from .entity_extraction.segment_classifier import (
            ANY_YEAR_RE,
            PAGE_REF_RE,
            _looks_like_bibliography,
            _looks_like_footnote,
        )

        lines = text.splitlines()
        rejected_def_rows = 0
        rejected_body_candidates = 0
        ambiguous_blocks = 0

        # ── Tokenize: number-prefixed rows anywhere in the document ──
        row_re = re.compile(r"^\s*(\d{1,3})[.)]?\s+(\S.*)$")
        numbered: List[Tuple[int, int, str]] = []  # (line_idx, num, content)
        for i, line in enumerate(lines):
            m = row_re.match(line)
            if m:
                numbered.append((i, int(m.group(1)), m.group(2)))

        # ── Build ladders: chains of ascending numbers with bounded gaps ──
        # A ladder starts at an unclaimed row numbered 1 and greedily
        # consumes the next row equal to its expectation. The gap bound
        # tolerates multi-line note continuations and running headers but
        # stops a ladder from chaining across unrelated document regions.
        MAX_GAP_LINES = 80
        claimed: set = set()
        ladders: List[List[Tuple[int, int, str]]] = []
        for k, (i, num, content) in enumerate(numbered):
            if i in claimed or num != 1:
                continue
            ladder = [(i, num, content)]
            claimed.add(i)
            expected = 2
            last_line = i
            for j in range(k + 1, len(numbered)):
                ij, nj, cj = numbered[j]
                if ij in claimed:
                    continue
                if ij - last_line > MAX_GAP_LINES:
                    break
                if nj == expected:
                    ladder.append((ij, nj, cj))
                    claimed.add(ij)
                    expected += 1
                    last_line = ij
            ladders.append(ladder)

        # ── Classify ladders: citation pipeline first, smol for doubt ──
        numbered_lines = {li for li, _, _ in numbered}

        def _full_row(li: int, content: str) -> str:
            """Row text including wrapped continuation lines — years and
            page refs usually sit on the continuation, not the first line."""
            parts = [content]
            for j in range(li + 1, min(li + 6, len(lines))):
                if j in numbered_lines:
                    break
                s = lines[j].strip()
                if s:
                    parts.append(s)
            return " ".join(parts)

        def _citation_vote(ld: List[Tuple[int, int, str]]) -> float:
            hits = 0
            for li, _, c in ld:
                fc = _full_row(li, c)
                if (
                    _looks_like_bibliography(fc)
                    or _looks_like_footnote(fc)
                    or ANY_YEAR_RE.search(fc)
                    or PAGE_REF_RE.search(fc)
                ):
                    hits += 1
            return hits / len(ld)

        accepted_ladders: List[List[Tuple[int, int, str]]] = []
        for ld in ladders:
            if len(ld) == 1:
                # isolated "1." rows: wrapped bibliography volume numbers,
                # single-item lists — never notes; drop without ceremony.
                for (li, _, _) in ld:
                    claimed.discard(li)
                continue
            v = _citation_vote(ld)
            if len(ld) >= 4 and v >= 0.3:
                accepted_ladders.append(ld)
                continue
            if len(ld) >= 4 and v < 0.1:
                # long ladder, clearly not citations: a real numbered list
                # (checklists, TOC debris). Deterministic reject.
                for (li, _, _) in ld:
                    claimed.discard(li)
                continue
            # Ambiguous: short ladder, or mid-range vote. Ask smol.
            from .entity_extraction.smol_client import classify_numbered_block
            verdict = classify_numbered_block(
                [f"{n}. {_full_row(li, c)}" for li, n, c in ld[:3]]
            )
            if verdict == "footnotes":
                accepted_ladders.append(ld)
            else:
                ambiguous_blocks += 1
                for (li, _, _) in ld:
                    claimed.discard(li)

        chapter_blocks = [len(ld) for ld in accepted_ladders]
        total_defs = sum(chapter_blocks)
        has_defs = bool(re.search(r"^\s*\[\^(\w+)\]:", text, re.MULTILINE))

        # ── Class (c): no ladders and no existing defs ──
        if total_defs == 0 and not has_defs:
            return text, {
                "defs": 0, "refs": 0, "blocks": [],
                "aligned": True,
                "rejected_def_rows": rejected_def_rows,
                "rejected_body_candidates": 0,
                "ambiguous_blocks": ambiguous_blocks,
            }

        processed_lines: List[str] = list(lines)
        inline_ref_re = re.compile(r"\[\^(\w+)\]")

        if total_defs > 0:
            # ── Class (a): promote ladder rows with global numbering ──
            notes_line = [False] * len(lines)
            global_def = 0
            for ld in accepted_ladders:
                first, last = ld[0][0], ld[-1][0]
                for li in range(first, min(last + 1, len(lines))):
                    notes_line[li] = True
                for (li, _, content) in ld:
                    global_def += 1
                    processed_lines[li] = f"[^{global_def}]: {content}"
            # Number-prefixed rows inside ladder spans that did not chain
            # (running headers, page artifacts) are the rejected def rows.
            for (li, _, _) in numbered:
                if notes_line[li] and li not in claimed:
                    rejected_def_rows += 1

            # ── Body acceptance by per-chapter expectation ──
            expected_ref = 1
            block_idx = 0
            offset = 0
            for i, line in enumerate(processed_lines):
                if notes_line[i] or block_idx >= len(chapter_blocks):
                    continue
                out = []
                last = 0
                for cm in self._BODY_CAND_RE.finditer(line):
                    if block_idx >= len(chapter_blocks):
                        break
                    num_str = cm.group(1) or cm.group(2) or cm.group(3)
                    # canonical page-ref shapes (p./pp./str./n.) are never markers
                    if cm.group(3) and PAGE_REF_RE.search(
                        line[max(0, cm.start() - 8): cm.end()]
                    ):
                        continue
                    if int(num_str) == expected_ref:
                        out.append(line[last:cm.start()])
                        out.append(f"[^{offset + expected_ref}]")
                        last = cm.end()
                        expected_ref += 1
                        if expected_ref > chapter_blocks[block_idx]:
                            offset += chapter_blocks[block_idx]
                            block_idx += 1
                            expected_ref = 1
                    else:
                        rejected_body_candidates += 1
                if out:
                    out.append(line[last:])
                    processed_lines[i] = "".join(out)

            final_lines = processed_lines
            global_counter = global_def
        else:
            # ── Class (b): label-map renumbering of existing defs/refs ──
            def_line_re = re.compile(r"^(\s*)\[\^(\w+)\]:\s+(.*)$")
            global_counter = 0
            def_map: Dict[str, str] = {}
            output_lines: List[str] = []
            for line in processed_lines:
                m = def_line_re.match(line)
                if m:
                    global_counter += 1
                    def_map[m.group(2)] = str(global_counter)
                    output_lines.append(f"[^{global_counter}]: {m.group(3)}")
                else:
                    output_lines.append(line)

            unknown_refs = [0]
            final_lines = []
            for line in output_lines:
                if def_line_re.match(line):
                    final_lines.append(line)
                    continue

                def _ref_replace(m: re.Match) -> str:
                    old = m.group(1)
                    if old in def_map:
                        return f"[^{def_map[old]}]"
                    unknown_refs[0] += 1
                    return m.group(0)
                final_lines.append(inline_ref_re.sub(_ref_replace, line))
            rejected_body_candidates += unknown_refs[0]

        def_line_check = re.compile(r"^\s*\[\^(\w+)\]:")
        total_ref_count = 0
        for line in final_lines:
            if not def_line_check.match(line):
                total_ref_count += len(inline_ref_re.findall(line))

        return "\n".join(final_lines), {
            "defs": global_counter,
            "refs": total_ref_count,
            "blocks": chapter_blocks,
            "aligned": global_counter == total_ref_count,
            "rejected_def_rows": rejected_def_rows,
            "rejected_body_candidates": rejected_body_candidates,
            "ambiguous_blocks": ambiguous_blocks,
        }

    def _remap_list_line(self, line: str, target_style: str) -> str:
        """Detects standard hierarchical outline enumerations and
        maps them to preferred house styles."""
        match = re.match(r"^(\s*)(\d+(\.\d+)+)\.?\s+(.*)$", line)
        if match:
            indent, outline, _, content = match.groups()
            depth = outline.count(".")
            if target_style == "alphabetical":
                if depth == 1:
                    letter = chr(96 + int(outline.split(".")[-1]))
                    return f"{indent}{letter}) {content}"
                elif depth >= 2:
                    return f"{indent}- {content}"
        return line

    def to_markdown(
        self,
        source: Path,
        preprocess: bool = True,
        list_style: str = "alphabetical",
    ) -> str:
        """Convert a local book file to Markdown."""
        md, _ = self.to_markdown_with_meta(
            source,
            preprocess=preprocess,
            list_style=list_style,
        )
        return md

    def _pdf_dict_to_markdown_text(self, source: Path) -> str:
        """Extract text from a PDF preserving italic/bold formatting as markdown.

        Walks fitz get_text("dict") spans — the PDF equivalent of what
        docx_to_markdown does for DOCX XML. Emits *italic*, **bold**,
        ***bold-italic*** markers around spans whose font flags indicate
        emphasis, so downstream reflow/renumber and DOCX export see the
        same markdown emphasis the DOCX path produces.

        Hyphenated line-breaks inside an italic span ("Encoun-" + "ters")
        are joined during the walk so the emphasis marker wraps the whole
        word: *Encounters with Strangers*.

        Line structure (one physical line per fitz line) is preserved —
        verified identical to get_text() output so _reflow_pdf_text and
        _renumber_footnotes work unchanged.
        """
        try:
            import fitz
        except ImportError:
            return ""
        doc = fitz.open(str(source))
        lines: List[str] = []
        for page in doc:
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if not isinstance(block, dict) or block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    if not isinstance(line, dict):
                        continue
                    # Collect spans for this line, tracking italic/bold flags.
                    raw_spans: List[Tuple[str, bool, bool]] = []
                    for span in line.get("spans", []):
                        if not isinstance(span, dict):
                            continue
                        txt = span.get("text", "")
                        if not txt:
                            continue
                        flags = span.get("flags", 0)
                        is_italic = bool(flags & 2)
                        is_bold = bool(flags & 16)
                        raw_spans.append((txt, is_italic, is_bold))
                    if not raw_spans:
                        continue
                    # Join hyphen-broken italic continuations: an italic span
                    # ending "X-" followed (after whitespace) by an italic span
                    # starting with a letter → "XY", one merged span.
                    merged: List[Tuple[str, bool, bool]] = []
                    si = 0
                    while si < len(raw_spans):
                        txt, it, bd = raw_spans[si]
                        if it and len(txt) >= 2 and txt.endswith("-") and txt[-2].isalpha():
                            # Look ahead past whitespace-only spans for next italic.
                            sj = si + 1
                            while sj < len(raw_spans) and not raw_spans[sj][0].strip():
                                sj += 1
                            if (sj < len(raw_spans) and raw_spans[sj][1]
                                    and raw_spans[sj][0][:1].isalpha()):
                                joined = txt[:-1] + raw_spans[sj][0]
                                merged.append((joined, True, raw_spans[sj][2] and bd))
                                si = sj + 1
                                continue
                        merged.append((txt, it, bd))
                        si += 1
                    # Emit markdown emphasis markers around each span.
                    line_text = ""
                    for txt, it, bd in merged:
                        if it and bd:
                            line_text += f"***{txt}***"
                        elif bd:
                            line_text += f"**{txt}**"
                        elif it:
                            line_text += f"*{txt}*"
                        else:
                            line_text += txt
                    if line_text.strip():
                        lines.append(line_text)
        doc.close()
        return "\n".join(lines)
    def to_markdown_with_meta(
        self,
        source: Path,
        preprocess: bool = True,
        list_style: str = "alphabetical",
    ) -> Tuple[str, list]:
        """Convert a local book file to Markdown, returning (markdown, segments_meta).

        ``segments_meta`` is intentionally empty here; callers (import_book,
        main.py) run ``book_outline.build_segments_meta`` after segment splitting.
        """
        # Academic DOCX: route to the zipfile-based parser (python-docx +
        # endnotes→footnotes → [^N] defs). MarkItDown's DocxConverter needs
        # the uninstalled optional `mammoth`; docx_to_markdown is the same
        # parser the academic import uses raw (main._parse_academic_docx).
        if source.suffix.lower() == ".docx":
            self._last_vl_result = None
            return self.docx_to_markdown(source), []
        raw_text = self.md.convert(str(source)).text_content or ""
        if source.suffix.lower() == ".pdf":
            try:
                import fitz
                raw_text = self._pdf_dict_to_markdown_text(source)
                if not raw_text:
                    # Fallback: plain get_text if dict walk yields nothing
                    # (shouldn't happen, but never break import on a PDF).
                    fitz_doc = fitz.open(str(source))
                    raw_text = "\n\n".join(fitz_doc[i].get_text() for i in range(len(fitz_doc)))
                    fitz_doc.close()
            except ImportError:
                pass
            raw_text = self._reflow_pdf_text(raw_text)
        if preprocess:
            raw_text = self.preprocess_source_style(
                raw_text, remap_lists=True, list_style=list_style, convert_endnotes=True
            )
        self._last_vl_result = None
        return raw_text, []

    # ===================================================================
    # STAGE 1b: Academic DOCX Parsing
    # ===================================================================

    def docx_to_markdown(self, source: Path) -> str:
        """Parse an academic DOCX into markdown with [^N] footnote refs/defs.

        Walks the DOCX XML body in order, extracting headings, bold/italic
        runs, and footnote/endnote references. Endnotes become footnotes.
        Defs are appended after the body. Does NOT run preprocess_source_style.
        """
        import zipfile as zf_module

        with zf_module.ZipFile(source, "r") as zf:
            names = zf.namelist()

            fn_map: Dict[Tuple[str, str], str] = {}
            for part_name, ns_prefix in [
                ("word/footnotes.xml", "fn"),
                ("word/endnotes.xml", "en"),
            ]:
                if part_name in names:
                    tree = ET.parse(zf.open(part_name))
                    root = tree.getroot()
                    tag = f"{{{_W_NS}}}footnote" if "footnote" in part_name else f"{{{_W_NS}}}endnote"
                    for fn_el in root.findall(tag):
                        el_type = fn_el.get(f"{{{_W_NS}}}type")
                        if el_type in ("separator", "continuationSeparator"):
                            continue
                        fn_id = fn_el.get(f"{{{_W_NS}}}id")
                        if fn_id is None:
                            continue
                        text = " ".join(
                            t.text for t in fn_el.iter(f"{{{_W_NS}}}t") if t.text
                        ).strip()
                        fn_map[(ns_prefix, fn_id)] = text

            tree = ET.parse(zf.open("word/document.xml"))
            root = tree.getroot()

        body = root.find(f"{{{_W_NS}}}body")
        if body is None:
            return ""

        paras = body.findall(f"{{{_W_NS}}}p")
        ref_order: List[Tuple[str, str]] = []
        note_counter = 0
        ref_map: Dict[Tuple[str, str], str] = {}

        md_lines: List[str] = []

        for para in paras:
            pPr = para.find(f"{{{_W_NS}}}pPr")
            style_val = None
            if pPr is not None:
                pStyle = pPr.find(f"{{{_W_NS}}}pStyle")
                if pStyle is not None:
                    style_val = pStyle.get(f"{{{_W_NS}}}val", "")

            heading_prefix = ""
            if style_val:
                sv_lower = style_val.lower()
                if sv_lower in ("heading1", "heading 1"):
                    heading_prefix = "# "
                elif sv_lower in ("heading2", "heading 2"):
                    heading_prefix = "## "
                elif sv_lower.startswith("heading"):
                    heading_prefix = "### "

            runs = para.findall(f"{{{_W_NS}}}r")
            para_text_parts: List[str] = []

            for run in runs:
                rPr = run.find(f"{{{_W_NS}}}rPr")
                is_bold = rPr is not None and rPr.find(f"{{{_W_NS}}}b") is not None
                is_italic = rPr is not None and rPr.find(f"{{{_W_NS}}}i") is not None

                fn_ref = run.find(f"{{{_W_NS}}}footnoteReference")
                en_ref = run.find(f"{{{_W_NS}}}endnoteReference")
                if fn_ref is not None:
                    oid = fn_ref.get(f"{{{_W_NS}}}id")
                    if oid is not None:
                        key = ("fn", oid)
                        if key not in ref_map:
                            note_counter += 1
                            ref_map[key] = str(note_counter)
                            ref_order.append(key)
                        para_text_parts.append(f"[^{ref_map[key]}]")
                    continue
                if en_ref is not None:
                    oid = en_ref.get(f"{{{_W_NS}}}id")
                    if oid is not None:
                        key = ("en", oid)
                        if key not in ref_map:
                            note_counter += 1
                            ref_map[key] = str(note_counter)
                            ref_order.append(key)
                        para_text_parts.append(f"[^{ref_map[key]}]")
                    continue

                t_el = run.find(f"{{{_W_NS}}}t")
                tab_el = run.find(f"{{{_W_NS}}}tab")
                br_el = run.find(f"{{{_W_NS}}}br")

                text = ""
                if t_el is not None and t_el.text:
                    text = t_el.text
                if tab_el is not None:
                    text += "\t"
                if br_el is not None:
                    text += " "

                if not text:
                    continue

                if is_bold and is_italic:
                    para_text_parts.append(f"***{text}***")
                elif is_bold:
                    para_text_parts.append(f"**{text}**")
                elif is_italic:
                    para_text_parts.append(f"*{text}*")
                else:
                    para_text_parts.append(text)

            line = heading_prefix + "".join(para_text_parts)
            if line.strip():
                md_lines.append(line)

        for key in ref_order:
            g_num = ref_map[key]
            text = fn_map.get(key, "[missing note]")
            md_lines.append(f"[^{g_num}]: {text}")

        return "\n\n".join(md_lines)

    # ===================================================================
    # STAGE 2: Post-Translation Compilation (DOCX Exporting)
    # ===================================================================

    def from_markdown(self, md_text: str, output_path: Path):
        """Standard plain text fallback."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md_text, encoding="utf-8")

    def _add_md_runs(self, paragraph, text: str) -> None:
        """Split text on markdown emphasis markers and add runs with
        italic/bold formatting. Plain spans get a normal run;
        *x* -> italic, **x** -> bold, ***x*** -> both."""
        if not HAS_DOCX:
            return
        assert docx is not None
        spans = _MD_EMPHASIS_RE.split(text)
        for span in spans:
            if not span:
                continue
            if span.startswith("***") and span.endswith("***"):
                run = paragraph.add_run(span[3:-3])
                run.bold = True
                run.italic = True
            elif span.startswith("**") and span.endswith("**"):
                run = paragraph.add_run(span[2:-2])
                run.bold = True
            elif span.startswith("*") and span.endswith("*") and not span.startswith("**"):
                run = paragraph.add_run(span[1:-1])
                run.italic = True
            else:
                paragraph.add_run(span)

    def compile_to_designed_docx(
        self,
        md_text: str,
        output_path: Path,
        house_font: str = "Georgia",
        segments: list[dict] | None = None,
        house_typography: dict | None = None,
    ):
        """Compile markdown to a styled DOCX with true page-bottom Word footnotes.

        Markdown emphasis *italic*, **bold*, ***both*** are rendered as real
        Word italic/bold runs in both body text and footnotes.

        When ``segments`` is provided (a list of segment dicts carrying
        pdf_para_idx/heading_level/para_align/para_indent_in/is_blockquote
        manifest keys), the export reconstructs the original PDF paragraphs:
        segments sharing the same pdf_para_idx are joined into one DOCX
        paragraph, headings render at their detected level, and block quotes
        get the house block-quote style. The translator's segmentation is
        never changed — only grouped for paragraph reconstruction.

        ``house_typography`` (default: config.MASKA_TYPOGRAPHY when segments
        are given, else the legacy Georgia/11pt defaults) governs fonts,
        sizes, margins, and spacing.
        """
        if not HAS_DOCX:
            print("[Parser Error] python-docx not installed. Writing plain text fallback.")
            self.from_markdown(md_text, output_path)
            return
        assert (
            docx is not None
            and Inches is not None
            and Pt is not None
            and WD_ALIGN_PARAGRAPH is not None
            and WD_SECTION is not None
            and OxmlElement is not None
            and qn is not None
        ), "HAS_DOCX is True but optional symbols are unbound"

        # Resolve house typography. When segments carry a manifest we default
        # to the Maska publisher style; legacy md_text-only exports keep the
        # prior Georgia/11pt defaults for backwards compatibility.
        if house_typography is None:
            if segments is not None:
                try:
                    import config
                    house_typography = config.MASKA_TYPOGRAPHY
                except Exception:
                    house_typography = _LEGACY_TYPOGRAPHY
            else:
                house_typography = _LEGACY_TYPOGRAPHY
        typo = house_typography

        # 1. Parse footnote defs and separate them from the body stream.
        footnote_defs: Dict[str, str] = {}
        body_lines: List[str] = []
        for line in md_text.splitlines():
            fn_match = re.match(r"^\[\^(\w+)\]:\s+(.*)$", line)
            if fn_match:
                footnote_defs[fn_match.group(1)] = fn_match.group(2)
            else:
                body_lines.append(line)

        doc = docx.Document()

        # 2. Margins + base typography (Maska: A4, 1" margins, TNR 12pt, 1.5 line).
        _apply_page_setup(doc, typo)

        from typing import cast
        from docx.styles.style import ParagraphStyle

        style_normal = cast(ParagraphStyle, doc.styles["Normal"])
        style_normal.font.name = typo.get("body_font", house_font)
        style_normal.font.size = Pt(typo.get("body_size_pt", 11))
        style_normal.paragraph_format.line_spacing = typo.get("line_spacing", 1.25)
        # Maska SLOG ODSTAVKA: no space between paragraphs.
        style_normal.paragraph_format.space_after = Pt(typo.get("space_after_pt", 8))
        style_normal.paragraph_format.space_before = Pt(0)

        # 3. Body — manifest path (grouped paragraphs) or legacy md_text path.
        next_footnote_id = 1
        footnotes_to_add: List[Tuple[int, str]] = []
        is_first_chapter = True

        if segments is not None and any("pdf_para_idx" in s for s in segments):
            # Manifest path: group segments by pdf_para_idx → one DOCX
            # paragraph per original PDF paragraph.
            from translate_core.book_outline import group_segments_by_para
            groups = group_segments_by_para(segments)
            for grp in groups:
                # Join the group's target (or source fallback) into one text.
                text = " ".join(
                    (s.get("target", "").strip() or s.get("source", "").strip())
                    for s in grp
                ).strip()
                if not text:
                    continue
                # Footnote-def segments (no pdf_para_idx) pass through as
                # their own group; skip them here — they're in footnote_defs.
                if text.lstrip().startswith("[^") and re.match(r"^\[\^(\w+)\]:", text.lstrip()):
                    continue
                heading_level = grp[0].get("heading_level", 0)
                para_align = grp[0].get("para_align", "left")
                is_bq = grp[0].get("is_blockquote", False)

                if heading_level >= 1:
                    self._emit_heading(doc, text, heading_level, typo, is_first_chapter)
                    is_first_chapter = False
                elif is_bq:
                    self._emit_blockquote(
                        doc, text, footnote_defs, footnotes_to_add,
                        next_footnote_id, typo,
                    )
                    # footnote ids advanced inside _emit_blockquote
                    next_footnote_id += text.count("[^")
                else:
                    next_footnote_id = self._emit_body_paragraph(
                        doc, text, footnote_defs, footnotes_to_add,
                        next_footnote_id, para_align,
                    )
        else:
            # Legacy md_text path: one paragraph per body line.
            for line in body_lines:
                line_str = line.strip()
                if not line_str:
                    continue
                if line_str.startswith("# "):
                    self._emit_heading(doc, line_str[2:], 1, typo, is_first_chapter)
                    is_first_chapter = False
                    continue
                if line_str.startswith("## "):
                    self._emit_heading(doc, line_str[3:], 2, typo, is_first_chapter)
                    is_first_chapter = False
                    continue
                next_footnote_id = self._emit_body_paragraph(
                    doc, line_str, footnote_defs, footnotes_to_add,
                    next_footnote_id, "left",
                )

        # 4. Configure per-section footnote restart.
        for section in doc.sections:
            self._set_section_footnote_restart(section)

        # 5. Save and post-process.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        if footnotes_to_add:
            _inject_footnotes_part(output_path, footnotes_to_add, typo)
        print(f"[Parser] Styled Book compiled successfully to {output_path}")

    def _emit_heading(
        self, doc, title: str, level: int, typo: dict, is_first_chapter: bool,
    ) -> None:
        """Emit a heading paragraph. Level 1 starts a new page section."""
        assert docx is not None and Pt is not None and WD_SECTION is not None and WD_ALIGN_PARAGRAPH is not None
        _pt = Pt  # narrowed local for pyright
        _align = WD_ALIGN_PARAGRAPH  # narrowed local for pyright
        if level >= 1 and not is_first_chapter:
            doc.add_section(WD_SECTION.NEW_PAGE)
        p = doc.add_paragraph()
        p.alignment = _align.CENTER
        p.paragraph_format.space_before = _pt(36 if level == 1 else 18)
        p.paragraph_format.space_after = _pt(24 if level == 1 else 8)
        run = p.add_run(title)
        run.bold = True
        run.font.size = _pt(typo.get("h1_size_pt", 18) if level == 1 else typo.get("h2_size_pt", 13))

    def _emit_body_paragraph(
        self, doc, text: str, footnote_defs: dict,
        footnotes_to_add: list, next_fn_id: int, para_align: str,
    ) -> int:
        """Emit a regular body paragraph with inline [^N] footnote refs.
        Returns the updated next footnote id."""
        assert WD_ALIGN_PARAGRAPH is not None and Pt is not None and Inches is not None
        _pt, _in = Pt, Inches  # narrowed locals for pyright
        p = doc.add_paragraph()
        align_map = {
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
            "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }
        p.alignment = align_map.get(para_align, WD_ALIGN_PARAGRAPH.LEFT)
        # Maska SLOG ODSTAVKA: no first-line indent, no space after.
        p.paragraph_format.first_line_indent = _in(0)
        p.paragraph_format.space_after = _pt(0)
        pattern = re.compile(r"\[\^(\w+)\]")
        last_idx = 0
        fn_id_counter = next_fn_id
        for match in pattern.finditer(text):
            start, end = match.span()
            if start > last_idx:
                self._add_md_runs(p, text[last_idx:start])
            fn_id = match.group(1)
            citation_text = footnote_defs.get(fn_id, f"[missing footnote {fn_id}]")
            footnotes_to_add.append((fn_id_counter, citation_text))
            self._add_footnote_reference_run(p, fn_id_counter)
            fn_id_counter += 1
            last_idx = end
        if last_idx < len(text):
            self._add_md_runs(p, text[last_idx:])
        return fn_id_counter

    def _emit_blockquote(
        self, doc, text: str, footnote_defs: dict,
        footnotes_to_add: list, next_fn_id: int, typo: dict,
    ) -> None:
        """Emit a block-quote paragraph (Maska SLOG SAMOSTOJNEGA CITATA:
        11pt, 1.0 line spacing, right-indented)."""
        assert Pt is not None and Inches is not None and WD_ALIGN_PARAGRAPH is not None
        _pt, _in = Pt, Inches  # narrowed locals for pyright
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = _in(typo.get("blockquote_indent_in", 0.5))
        p.paragraph_format.line_spacing = typo.get("blockquote_line_spacing", 1.0)
        p.paragraph_format.space_after = _pt(0)
        # Block-quote font size is applied per-run via _add_md_runs_sized
        # below (the paragraph has no runs yet at this point).
        # Re-emit with size override: add runs via _add_md_runs then resize.
        pattern = re.compile(r"\[\^(\w+)\]")
        last_idx = 0
        fn_id_counter = next_fn_id
        for match in pattern.finditer(text):
            start, end = match.span()
            if start > last_idx:
                self._add_md_runs_sized(p, text[last_idx:start], typo.get("blockquote_size_pt", 11))
            fn_id = match.group(1)
            citation_text = footnote_defs.get(fn_id, f"[missing footnote {fn_id}]")
            footnotes_to_add.append((fn_id_counter, citation_text))
            self._add_footnote_reference_run(p, fn_id_counter)
            fn_id_counter += 1
            last_idx = end
        if last_idx < len(text):
            self._add_md_runs_sized(p, text[last_idx:], typo.get("blockquote_size_pt", 11))

    def _add_md_runs_sized(self, paragraph, text: str, size_pt: float) -> None:
        """Like _add_md_runs but sets an explicit font size on each run
        (for block quotes that need a smaller size than Normal)."""
        if not HAS_DOCX:
            return
        assert docx is not None and Pt is not None
        _pt = Pt  # narrowed local for pyright
        spans = _MD_EMPHASIS_RE.split(text)
        for span in spans:
            if not span:
                continue
            if span.startswith("***") and span.endswith("***"):
                run = paragraph.add_run(span[3:-3])
                run.bold = True
                run.italic = True
                run.font.size = _pt(size_pt)
            elif span.startswith("**") and span.endswith("**"):
                run = paragraph.add_run(span[2:-2])
                run.bold = True
                run.font.size = _pt(size_pt)
            elif span.startswith("*") and span.endswith("*") and not span.startswith("**"):
                run = paragraph.add_run(span[1:-1])
                run.italic = True
                run.font.size = _pt(size_pt)
            else:
                run = paragraph.add_run(span)
                run.font.size = _pt(size_pt)

    def compile_from_template(
        self,
        template_path: Path,
        output_path: Path,
        segments: List[dict],
        comments_mode: str = "none",
    ) -> None:
        """Compile a translated DOCX by cloning the original and replacing
        text in-place, preserving all paragraph styles, run formatting,
        and document structure.

        When *comments_mode* is not ``"none"``, segment comments are
        inserted as new paragraphs immediately after the paragraph they
        belong to, prefixed with » to distinguish them from body text.
        """
        if not HAS_DOCX:
            raise RuntimeError("python-docx not installed")
        assert docx is not None and OxmlElement is not None and qn is not None

        from translate_core import comments as cm

        doc = docx.Document(str(template_path))

        idx_to_segs: Dict[int, list] = {}
        for seg in segments:
            pi = seg.get("docx_para_idx")
            if pi is not None:
                idx_to_segs.setdefault(pi, []).append(seg)

        paragraphs = doc.paragraphs
        insertions: list[tuple[int, str]] = []
        for para_idx, para in enumerate(paragraphs):
            segs = idx_to_segs.get(para_idx)
            if segs is None:
                continue
            if all(not seg.get("target", "").strip() for seg in segs):
                continue
            replacement = " ".join(
                seg.get("target", "").strip() or seg.get("source", "").strip()
                for seg in segs
            )
            self._replace_paragraph_text(para, replacement)
            if comments_mode != cm.EXPORT_NONE:
                for seg in segs:
                    c_text = cm.format_comments_for_export(seg, comments_mode)
                    if c_text:
                        insertions.append((para_idx, c_text))

        if insertions:
            for para_idx, c_text in reversed(insertions):
                self._insert_comment_paragraph_after(paragraphs[para_idx], c_text)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))

    @staticmethod
    def _insert_comment_paragraph_after(para, comment_text: str) -> None:
        """Insert a comment paragraph immediately after *para*.

        The comment is prefixed with » to distinguish it from body text.
        Multiple comments in one segment are separated by line breaks.
        """
        _W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        p_el = para._element
        new_p = OxmlElement("w:p")
        pPr = OxmlElement("w:pPr")
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), "720")
        pPr.append(ind)
        spacing = OxmlElement("w:spacing")
        spacing.set(qn("w:before"), "120")
        spacing.set(qn("w:after"), "120")
        pPr.append(spacing)
        new_p.append(pPr)
        lines = [ln.lstrip("> ").rstrip() for ln in comment_text.strip().splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            if i > 0:
                br_r = OxmlElement("w:r")
                br = OxmlElement("w:br")
                br_r.append(br)
                new_p.append(br_r)
            run = OxmlElement("w:r")
            rPr = OxmlElement("w:rPr")
            sz = OxmlElement("w:sz")
            sz.set(qn("w:val"), "20")
            rPr.append(sz)
            run.append(rPr)
            t = OxmlElement("w:t")
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            t.text = f"» {line}" if i == 0 else line
            run.append(t)
            new_p.append(run)
        p_el.addnext(new_p)

    @staticmethod
    def _replace_paragraph_text(paragraph, new_text: str) -> None:
        """Replace paragraph text preserving bold/italic/font at the XML level."""
        _W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        p_el = paragraph._element
        r_elements = list(p_el.findall(f"{_W}r"))
        if not r_elements:
            paragraph.add_run(new_text)
            return

        def _fmt_key(r_el):
            rPr = r_el.find(f"{_W}rPr")
            b = rPr is not None and rPr.find(f"{_W}b") is not None
            i = rPr is not None and rPr.find(f"{_W}i") is not None
            u = rPr is not None and rPr.find(f"{_W}u") is not None
            return (b, i, u)

        idx = 0
        while idx < len(r_elements) - 1:
            if _fmt_key(r_elements[idx]) == _fmt_key(r_elements[idx + 1]):
                cur_t = r_elements[idx].find(f"{_W}t")
                nxt_t = r_elements[idx + 1].find(f"{_W}t")
                if cur_t is not None and nxt_t is not None:
                    cur_t.text = (cur_t.text or "") + (nxt_t.text or "")
                p_el.remove(r_elements[idx + 1])
                r_elements.pop(idx + 1)
            else:
                idx += 1

        t_els = [r.find(f"{_W}t") for r in r_elements if r.find(f"{_W}t") is not None]
        orig_lens = [max(len(t.text or ""), 1) for t in t_els]
        total = sum(orig_lens)

        remaining = new_text
        for j, length in enumerate(orig_lens):
            if j == len(orig_lens) - 1:
                t_els[j].text = remaining
                break
            share = length / total
            target = int(share * len(new_text))
            snap = remaining.find(" ", target)
            cut = snap + 1 if snap != -1 and snap < len(remaining) else len(remaining)
            t_els[j].text = remaining[:cut]
            remaining = remaining[cut:]

    def _add_footnote_reference_run(self, paragraph, fn_global_id: int) -> None:
        """Add a footnoteReference inside a new superscript run."""
        assert OxmlElement is not None and qn is not None
        run = paragraph.add_run()
        rPr = run._r.get_or_add_rPr()
        rStyle = OxmlElement("w:rStyle")
        rStyle.set(qn("w:val"), "FootnoteReference")
        rPr.append(rStyle)
        ref = OxmlElement("w:footnoteReference")
        ref.set(qn("w:id"), str(fn_global_id))
        run._r.append(ref)

    def _set_section_footnote_restart(self, section) -> None:
        """Add per-section footnote numbering restart."""
        assert OxmlElement is not None and qn is not None
        sectPr = section._sectPr
        for existing in sectPr.findall(qn("w:footnotePr")):
            sectPr.remove(existing)
        footnote_pr = OxmlElement("w:footnotePr")
        num_restart = OxmlElement("w:numRestart")
        num_restart.set(qn("w:val"), "eachSect")
        footnote_pr.append(num_restart)
        sectPr.append(footnote_pr)

    # ===================================================================
    # STAGE 3: Para-index relinking (simple-pipeline export repair)
    # ===================================================================

    def relink_docx_para_idx(self, template_path: Path, segments: List[dict]) -> int:
        """Re-assign docx_para_idx to segments that lack it by matching
        normalized source text against the template DOCX paragraphs.

        Returns the number of segments successfully assigned.
        """
        if not HAS_DOCX:
            return 0
        assert docx is not None
        d = docx.Document(str(template_path))
        paras = d.paragraphs

        def _normalize(text: str) -> str:
            return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()

        para_texts = [_normalize(p.text) for p in paras]

        assigned = 0
        cursor = 0
        for seg in segments:
            if seg.get("docx_para_idx") is not None:
                continue
            src = _normalize(seg.get("source", ""))
            if not src:
                continue
            for pi in range(cursor, len(para_texts)):
                pt = para_texts[pi]
                if pt == src or src in pt:
                    seg["docx_para_idx"] = pi
                    cursor = pi
                    assigned += 1
                    break
        return assigned