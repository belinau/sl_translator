# translate_core/doc_parser.py
#
# Advanced Document Parser supporting:
# 1. Pre-translation style restructuring (Endnote ➔ Footnote normalization)
# 2. House-style enumeration remapping
# 3. Post-translation DOCX compilation with native bottom-of-page Word Footnotes
#

import re
from pathlib import Path
from typing import Dict, List

from markitdown import MarkItDown

# python-docx imports
try:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    HAS_DOCX = True
except ImportError:
    docx = None  # type: ignore[assignment]
    WD_ALIGN_PARAGRAPH = None  # type: ignore[assignment,misc]
    OxmlElement = None  # type: ignore[assignment,misc]
    qn = None  # type: ignore[assignment,misc]
    Inches = None  # type: ignore[assignment,misc]
    Pt = None  # type: ignore[assignment,misc]
    HAS_DOCX = False


class DocumentParser:
    """
    Wrapper around MarkItDown to convert, normalize, and compile styled book-like documents.
    """

    def __init__(self, enable_plugins: bool = False):
        self.md = MarkItDown(enable_plugins=enable_plugins)

    # =======================================================================
    # STAGE 1: Pre-Translation Normalization (Ingestion)
    # =======================================================================

    def preprocess_source_style(
        self,
        raw_markdown: str,
        remap_lists: bool = True,
        list_style: str = "alphabetical",
    ) -> str:
        """
        Normalize the source text structure BEFORE translation.
        Converts generic trailing endnote indices in text to standard Markdown footnotes,
        and adapts numbering configurations to custom publishing house styles.
        """
        text = raw_markdown

        # 1. Convert standard inline endnote indices (e.g., "[1]" or "[i]") to Markdown footnotes (e.g. "[^1]")
        # Find references in body and normalize them to standard Markdown footnote markers
        text = re.sub(r"\[(\d+)\]", r"[^\1]", text)

        # If the PDF converter parsed the endnotes list at the bottom as a list of "1. Citation",
        # normalize them to standard Markdown footnote definitions: "[^1]: Citation"
        lines = text.splitlines()
        normalized_lines = []
        in_notes_section = False

        for line in lines:
            # Detect starting of an endnote list (e.g. "Notes", "Opombe", "Bibliography")
            if re.match(
                r"^(Notes|Opombe|Bibliography|Viri|Reference):?\s*$",
                line,
                re.IGNORECASE,
            ):
                in_notes_section = True
                normalized_lines.append(line)
                continue

            if in_notes_section:
                # Match "1. Citation text" or "1 Citation text"
                note_match = re.match(r"^(\d+)\.?\s+(.*)$", line)
                if note_match:
                    num, content = note_match.groups()
                    normalized_lines.append(f"[^{num}]: {content}")
                    continue

            # 2. Remap Enumerations to Custom House Styles
            if remap_lists:
                line = self._remap_list_line(line, list_style)

            normalized_lines.append(line)

        return "\n".join(normalized_lines)

    def _remap_list_line(self, line: str, target_style: str) -> str:
        """
        Detects standard hierarchical outline enumerations (e.g. '1.1.1')
        and maps them to preferred house styles (e.g. 'a)').
        """
        # Match leading outline numbers: e.g. "1.1.1 First Section"
        match = re.match(r"^(\s*)(\d+(\.\d+)+)\.?\s+(.*)$", line)
        if match:
            indent, outline, _, content = match.groups()
            depth = outline.count(".")

            if target_style == "alphabetical":
                # Map deep levels to letters: level 2 -> 'a)', level 3 -> 'i)'
                if depth == 1:
                    letter = chr(96 + int(outline.split(".")[-1]))  # 97 is 'a'
                    return f"{indent}{letter}) {content}"
                elif depth >= 2:
                    return f"{indent}- {content}"
            elif target_style == "roman":
                # Map level 1 to Roman numerals (handled externally if headers, else standard list)
                pass

        return line

    def to_markdown(
        self, source: Path, preprocess: bool = True, list_style: str = "alphabetical"
    ) -> str:
        """Convert a local book file to Markdown, automatically restructuring layout and styles."""
        raw_text = self.md.convert(str(source)).text_content or ""
        if preprocess:
            return self.preprocess_source_style(
                raw_text, remap_lists=True, list_style=list_style
            )
        return raw_text

    # =======================================================================
    # STAGE 2: Post-Translation Compilation (DOCX Exporting)
    # =======================================================================

    def from_markdown(self, md_text: str, output_path: Path):
        """Standard plain text fallback."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md_text, encoding="utf-8")

    def compile_to_designed_docx(
        self, md_text: str, output_path: Path, house_font: str = "Georgia"
    ):
        """
        Parses Markdown text and compiles it into a beautiful, styled DOCX document.
        Generates clean book elements, chapter titles, page breaks, and genuine
        Word bottom-of-page footnotes.
        """
        if not HAS_DOCX:
            print(
                "[Parser Error] python-docx not installed. Writing plain text fallback."
            )
            self.from_markdown(md_text, output_path)
            return
        assert (
            docx is not None
            and Inches is not None
            and Pt is not None
            and WD_ALIGN_PARAGRAPH is not None
            and OxmlElement is not None
            and qn is not None
        ), "HAS_DOCX is True but optional symbols are unbound"

        doc = docx.Document()

        # Configure Book-like Margins
        sections = doc.sections
        for section in sections:
            section.top_margin = Inches(1.2)
            section.bottom_margin = Inches(1.2)
            section.left_margin = Inches(1.2)
            section.right_margin = Inches(1.2)

        # Configure Base Book Typography. python-docx returns BaseStyle from the
        # styles collection; the actual instance is a ParagraphStyle with .font
        # and .paragraph_format, but the type stub doesn't expose them on the
        # base. cast() avoids the false-positive type warnings.
        from typing import cast
        from docx.styles.style import ParagraphStyle

        style_normal = cast(ParagraphStyle, doc.styles["Normal"])
        font = style_normal.font
        font.name = house_font
        font.size = Pt(11)
        style_normal.paragraph_format.line_spacing = 1.25
        style_normal.paragraph_format.space_after = Pt(8)

        # First pass: Parse and isolate standard Markdown Footnote Definitions ([^1]: Citation)
        lines = md_text.splitlines()
        footnote_defs: Dict[str, str] = {}
        body_lines: List[str] = []

        for line in lines:
            # Match "[^1]: Citation text"
            fn_match = re.match(r"^\[\^(\w+)\]:\s+(.*)$", line)
            if fn_match:
                fn_id, fn_content = fn_match.groups()
                footnote_defs[fn_id] = fn_content
            else:
                body_lines.append(line)

        # Second pass: Process body text and dynamically insert OpenXML elements
        for line in body_lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Chapter Title: "# Chapter"
            if line_str.startswith("# "):
                doc.add_page_break()  # Standard book layout starts chapters on a new page
                title = line_str[2:]
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(36)
                p.paragraph_format.space_after = Pt(24)
                run = p.add_run(title)
                run.bold = True
                run.font.size = Pt(18)
                continue

            # Heading 2: "## Section"
            if line_str.startswith("## "):
                title = line_str[3:]
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(18)
                p.paragraph_format.space_after = Pt(8)
                run = p.add_run(title)
                run.bold = True
                run.font.size = Pt(13)
                continue

            # Parse Paragraphs with Inline Footnotes references
            p = doc.add_paragraph()
            self._write_paragraph_with_footnotes(p, line_str, footnote_defs)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        print(f"[Parser] Styled Book compiled successfully to {output_path}")

    def _write_paragraph_with_footnotes(
        self, paragraph, text: str, footnote_defs: Dict[str, str]
    ):
        """
        Parses standard paragraph strings for inline footnote indicators '[^1]'
        and constructs OpenXML footnote elements inside python-docx runs.
        """
        # Regex to locate markers like "[^1]"
        pattern = re.compile(r"\[\^(\w+)\]")
        last_idx = 0

        for match in pattern.finditer(text):
            # Write preceding plain text run
            start, end = match.span()
            if start > last_idx:
                paragraph.add_run(text[last_idx:start])

            fn_id = match.group(1)
            citation_text = footnote_defs.get(
                fn_id, f"Missing citation footnote reference {fn_id}"
            )

            # Construct Genuine Word Footnote OpenXML Elements
            self._add_native_word_footnote(paragraph, fn_id, citation_text)
            last_idx = end

        # Write trailing plain text run
        if last_idx < len(text):
            paragraph.add_run(text[last_idx:])

    def _add_native_word_footnote(self, paragraph, fn_id: str, footnote_text: str):
        """
        Injects standard OOXML structures to define and refer to bottom-of-page Word footnotes.
        """
        assert OxmlElement is not None and qn is not None, (
            "_add_native_word_footnote requires HAS_DOCX"
        )
        # Access document-wide OpenXML relationships
        doc = paragraph.part.document
        try:
            footnotes = doc.part.footnotes_part.footnotes
        except AttributeError:
            # Initialize footnotes part structure if empty
            footnotes = doc.part.footnotes_part._element

        # Generate a unique integer ID for this footnote
        footnote_num_id = abs(hash(fn_id)) % 10000

        # Create base footnote XML elements
        footnote = OxmlElement("w:footnote")
        footnote.set(qn("w:type"), "normal")
        footnote.set(qn("w:id"), str(footnote_num_id))

        # Add citation text inside footnote XML block
        p = OxmlElement("w:p")
        pPr = OxmlElement("w:pPr")
        pStyle = OxmlElement("w:pStyle")
        pStyle.set(qn("w:val"), "FootnoteText")
        pPr.append(pStyle)
        p.append(pPr)

        # Inline superscript number in footnote margin
        r_num = OxmlElement("w:run")
        rPr_num = OxmlElement("w:rPr")
        rStyle = OxmlElement("w:rStyle")
        rStyle.set(qn("w:val"), "FootnoteReference")
        rPr_num.append(rStyle)
        r_num.append(rPr_num)
        footnote_num_el = OxmlElement("w:footnoteRef")
        r_num.append(footnote_num_el)
        p.append(r_num)

        # Footnote body text
        r_text = OxmlElement("w:r")
        t_el = OxmlElement("w:t")
        t_el.text = " " + footnote_text
        r_text.append(t_el)
        p.append(r_text)

        footnote.append(p)
        footnotes.append(footnote)

        # Insert Footnote Reference indicator in the actual body paragraph text run
        run = paragraph.add_run()
        rPr = run._r.get_or_add_rPr()
        rStyle = OxmlElement("w:rStyle")
        rStyle.set(qn("w:val"), "FootnoteReference")
        rPr.append(rStyle)

        footnote_ref = OxmlElement("w:footnoteReference")
        footnote_ref.set(qn("w:id"), str(footnote_num_id))
        run._r.append(footnote_ref)
