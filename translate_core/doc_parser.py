# translate_core/doc_parser.py
#
# Advanced Document Parser supporting:
# 1. Pre-translation style restructuring (Endnote ➔ Footnote normalization)
# 2. House-style enumeration remapping
# 3. Post-translation DOCX compilation with native bottom-of-page Word Footnotes
#

import re
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from markitdown import MarkItDown

# python-docx imports
try:
    import docx
    from docx.enum.section import WD_SECTION
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    HAS_DOCX = True
except ImportError:
    docx = None  # type: ignore[assignment]
    WD_SECTION = None  # type: ignore[assignment,misc]
    WD_ALIGN_PARAGRAPH = None  # type: ignore[assignment,misc]
    OxmlElement = None  # type: ignore[assignment,misc]
    qn = None  # type: ignore[assignment,misc]
    Inches = None  # type: ignore[assignment,misc]
    Pt = None  # type: ignore[assignment,misc]
    HAS_DOCX = False


_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_FOOTNOTES_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes"
)
_FOOTNOTES_CT = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
)


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_footnotes_xml(footnotes: List[Tuple[int, str]]) -> bytes:
    """Build the contents of word/footnotes.xml.

    `footnotes` is a list of (id, citation_text). Word reserves IDs
    -1 (separator) and 0 (continuation separator) — user footnotes
    start at id=1.
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
        parts.append(
            f'<w:footnote w:id="{fn_id}">'
            '<w:p>'
            '<w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr>'
            '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr>'
            '<w:footnoteRef/></w:r>'
            f'<w:r><w:t xml:space="preserve"> {_xml_escape(text)}</w:t></w:r>'
            '</w:p></w:footnote>'
        )
    parts.append("</w:footnotes>")
    return "".join(parts).encode("utf-8")


def _inject_footnotes_part(
    docx_path: Path, footnotes: List[Tuple[int, str]]
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
        f'<Relationship Id="{fn_rel_id}" Type="{_FOOTNOTES_REL_TYPE}" '
        f'Target="footnotes.xml"/></Relationships>',
    )
    files[rels_path] = rels_xml.encode("utf-8")

    ct_path = "[Content_Types].xml"
    ct_xml = files[ct_path].decode("utf-8")
    if "footnotes+xml" not in ct_xml:
        ct_xml = ct_xml.replace(
            "</Types>",
            f'<Override PartName="/word/footnotes.xml" '
            f'ContentType="{_FOOTNOTES_CT}"/></Types>',
        )
        files[ct_path] = ct_xml.encode("utf-8")

    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)


class DocumentParser:
    """
    Wrapper around MarkItDown to convert, normalize, and compile styled book-like documents.
    """

    def __init__(self, enable_plugins: bool = False):
        self.md = MarkItDown(enable_plugins=enable_plugins)
        self._last_vl_result = None  # Retained as None; VL pipeline retired in Phase 7

    # =======================================================================
    # STAGE 1: Pre-Translation Normalization (Ingestion)
    # =======================================================================

    def preprocess_source_style(
        self,
        raw_markdown: str,
        remap_lists: bool = True,
        list_style: str = "alphabetical",
        convert_endnotes: bool = True,
    ) -> str:
        """
        Normalize the source text structure BEFORE translation.
        Converts generic trailing endnote indices in text to standard Markdown footnotes,
        and adapts numbering configurations to custom publishing house styles.
        """
        text = raw_markdown

        # 0. Convert bare-digit footnote markers ("terrain.31" → "terrain.[^N]")
        #    to markdown footnote refs. PyMuPDF strips superscript formatting,
        #    so the original "terrain³¹" comes through as plain "terrain.31".
        #    We renumber sequentially per chapter so ref order aligns with the
        #    parsed footnote-definition order in the back-matter notes — Word's
        #    `numRestart="eachSect"` then displays per-chapter numbering.
        text = self._convert_bare_footnote_refs(text)

        # 1. Convert standard inline endnote indices (e.g., "[1]" or "[i]") to Markdown footnotes (e.g. "[^1]")
        # Find references in body and normalize them to standard Markdown footnote markers
        # Skip when the VL pipeline already emitted correct [^N] markers.
        if convert_endnotes:
            text = re.sub(r"\[(\d+)\]", r"[^\1]", text)

        # Legacy "N. text → [^N]: text" promotion path. Only safe for the
        # MarkItDown ingestion path: the VL parser already extracts every
        # footnote into a [^N]: def line BEFORE preprocess runs and
        # _convert_bare_footnote_refs has already renumbered both refs and
        # defs sequentially. Running the legacy logic here would re-mark
        # them with their original (per-chapter restart) numbers and
        # undo the renumbering.
        if convert_endnotes:
            lines = text.splitlines()
            normalized_lines = []
            in_notes_section = False
            for line in lines:
                if re.match(
                    r"^(Notes|Opombe|Bibliography|Viri|Reference):?\s*$",
                    line,
                    re.IGNORECASE,
                ):
                    in_notes_section = True
                    normalized_lines.append(line)
                    continue
                if in_notes_section:
                    note_match = re.match(r"^(\d+)\.?\s+(.*)$", line)
                    if note_match:
                        num, content = note_match.groups()
                        normalized_lines.append(f"[^{num}]: {content}")
                        continue
                if remap_lists:
                    line = self._remap_list_line(line, list_style)
                normalized_lines.append(line)
            return "\n".join(normalized_lines)

        # VL path: only remap lists; refs/defs were already renumbered above.
        if remap_lists:
            text = "\n".join(
                self._remap_list_line(line, list_style)
                for line in text.splitlines()
            )
        return text

    # Bare-digit footnote markers PyMuPDF leaves in body text. Matches a
    # digit run (1-3 digits) immediately following a word character or
    # close-punctuation, with no preceding space — that's how PyMuPDF
    # renders superscript footnote numerals. The 1-3 digit cap rules out
    # 4-digit years like "1995".
    # A true footnote marker is: preceded by a word-char or sentence-ending
    # punctuation (no preceding space — superscript glues to the prior token),
    # AND followed by whitespace or sentence punctuation that is itself
    # followed by whitespace/end-of-string. This rules out:
    #   • "Z39.48"      (39 followed by "." then digit, not space)
    #   • "800-842"     (800 followed by "-", which isn't in our trailing class)
    #   • "ISBN-978-0"  (each digit run followed by "-", excluded)
    #   • "Smith 2005"  (2005 preceded by space — lookbehind fails)
    _BARE_FN_REF_RE = re.compile(
        # Exclude index "page–note" references: "214n100" (Kafer's index uses
        # this format heavily). A "100" preceded by "n" preceded by a digit
        # is a note-cross-reference, not a body footnote marker.
        r"(?<!\dn)"
        r"(?<=[a-zA-Z\".,;:!?\)\]”’»])"
        r"(\d{1,3})"
        r"(?=\s|[.,;:!?\"\)\]”’»](?:\s|$)|$)"
    )
    # Page-reference contexts where a digit run is NOT a footnote.
    _PAGE_REF_PREFIX_RE = re.compile(r"\b[pP]p?\.\s*$")
    # Year/century/decade contexts where 1-3 digit clusters appear with
    # surrounding digits — also excluded by lookbehind on word char already.

    def _convert_bare_footnote_refs(self, text: str) -> str:
        """Renumber footnote refs (body) and definitions (notes section)
        sequentially across the whole document.

        Why both: the source book restarts footnote numbering per chapter
        ("1, 2, ..., 100" then "1, 2, ..., 100" again). The N-th body ref
        in document order corresponds to the N-th def in the notes section.
        We assign them matching sequential IDs; Word's
        ``<w:numRestart w:val="eachSect"/>`` per chapter section then
        displays them as 1-N per chapter at output time.

        For each line:
          • If it's a ``[^N]: text`` definition line, replace N with
            ``def_counter`` and increment.
          • Otherwise, in body text:
              - replace existing ``[^N]`` inline refs with body_counter, AND
              - convert bare-digit footnote markers ("terrain.31") with
                body_counter, AND
              - leave endnote rows (`  17. Author…`) untouched (the parser
                already converted them to ``[^N]:`` defs upstream).
        """
        body_counter = [0]
        def_counter = [0]
        def_line_re = re.compile(r"^(\s*)\[\^([^\]]+)\]:\s*(.*)$")
        inline_ref_re = re.compile(r"\[\^([^\]]+)\]")
        endnote_row_re = re.compile(r"^\s*\d{1,3}\.\s+[A-Z]")

        def _convert_line(line: str) -> str:
            m = def_line_re.match(line)
            if m:
                def_counter[0] += 1
                return f"{m.group(1)}[^{def_counter[0]}]: {m.group(3)}"
            # Untouched: legacy endnote rows (parser already lifted these
            # into [^N]: defs further down the document; mutating them
            # here would re-mark them as body refs).
            if endnote_row_re.match(line):
                return line

            # Renumber existing inline refs first.
            def _inline_repl(m: re.Match) -> str:
                body_counter[0] += 1
                return f"[^{body_counter[0]}]"
            line = inline_ref_re.sub(_inline_repl, line)

            # Convert bare-digit markers ("terrain.31" → "terrain.[^N]").
            def _bare_repl(m: re.Match) -> str:
                before = line[max(0, m.start() - 6):m.start()]
                if self._PAGE_REF_PREFIX_RE.search(before):
                    return m.group(0)
                body_counter[0] += 1
                return f"[^{body_counter[0]}]"
            line = self._BARE_FN_REF_RE.sub(_bare_repl, line)
            return line

        return "\n".join(_convert_line(l) for l in text.splitlines())

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
        self,
        source: Path,
        preprocess: bool = True,
        list_style: str = "alphabetical",
        use_vl: bool = False,
        vl_cache_dir: Path | None = None,
        progress_callback: Callable[[str, int, int, bool], None] | None = None,
    ) -> str:
        """Convert a local book file to Markdown, automatically restructuring layout and styles.

        ``use_vl`` / ``vl_cache_dir`` are accepted for backward compatibility
        with editor call sites and are silently ignored — the VL pipeline was
        retired in Phase 7. Only the MarkItDown text path runs.
        """
        md, _ = self.to_markdown_with_meta(
            source,
            preprocess=preprocess,
            list_style=list_style,
            use_vl=use_vl,
            vl_cache_dir=vl_cache_dir,
            progress_callback=progress_callback,
        )
        return md

    def to_markdown_with_meta(
        self,
        source: Path,
        preprocess: bool = True,
        list_style: str = "alphabetical",
        use_vl: bool = False,
        vl_cache_dir: Path | None = None,
        progress_callback: Callable[[str, int, int, bool], None] | None = None,
    ) -> Tuple[str, list]:
        """Convert a local book file to Markdown, returning (markdown, segments_meta).

        ``use_vl`` / ``vl_cache_dir`` / ``progress_callback`` are accepted for
        backward compatibility with editor call sites and are silently ignored
        — the VL pipeline was retired in Phase 7. segments_meta is always an
        empty list (the MarkItDown text path emits no per-segment metadata).
        """
        del use_vl, vl_cache_dir, progress_callback  # retired; signature kept for compat
        raw_text = self.md.convert(str(source)).text_content or ""
        if preprocess:
            raw_text = self.preprocess_source_style(
                raw_text, remap_lists=True, list_style=list_style, convert_endnotes=True
            )
        self._last_vl_result = None
        return raw_text, []

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
        """Compile markdown to a styled DOCX with true page-bottom Word footnotes.

        Each `# Chapter` becomes its own section so footnote numbering can
        restart per chapter (Word `<w:numRestart w:val="eachSect"/>`). The
        footnotes part is injected into the .docx package after python-docx
        saves the body — python-docx 1.x has no public API for footnotes.
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
            and WD_SECTION is not None
            and OxmlElement is not None
            and qn is not None
        ), "HAS_DOCX is True but optional symbols are unbound"

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

        # 2. Margins + base typography.
        for section in doc.sections:
            section.top_margin = Inches(1.2)
            section.bottom_margin = Inches(1.2)
            section.left_margin = Inches(1.2)
            section.right_margin = Inches(1.2)

        from typing import cast
        from docx.styles.style import ParagraphStyle

        style_normal = cast(ParagraphStyle, doc.styles["Normal"])
        style_normal.font.name = house_font
        style_normal.font.size = Pt(11)
        style_normal.paragraph_format.line_spacing = 1.25
        style_normal.paragraph_format.space_after = Pt(8)

        # 3. Body. Track global footnote IDs (unique in footnotes.xml) and
        #    accumulate the (id, text) pairs for the post-process step.
        next_footnote_id = 1
        footnotes_to_add: List[Tuple[int, str]] = []
        is_first_chapter = True

        for line in body_lines:
            line_str = line.strip()
            if not line_str:
                continue

            # `# Chapter` → new section (so footnote numbering can restart).
            if line_str.startswith("# "):
                if not is_first_chapter:
                    doc.add_section(WD_SECTION.NEW_PAGE)
                is_first_chapter = False
                title = line_str[2:]
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(36)
                p.paragraph_format.space_after = Pt(24)
                run = p.add_run(title)
                run.bold = True
                run.font.size = Pt(18)
                continue

            if line_str.startswith("## "):
                title = line_str[3:]
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(18)
                p.paragraph_format.space_after = Pt(8)
                run = p.add_run(title)
                run.bold = True
                run.font.size = Pt(13)
                continue

            # Regular paragraph with inline [^N] footnote refs.
            p = doc.add_paragraph()
            pattern = re.compile(r"\[\^(\w+)\]")
            last_idx = 0
            for match in pattern.finditer(line_str):
                start, end = match.span()
                if start > last_idx:
                    p.add_run(line_str[last_idx:start])
                fn_id = match.group(1)
                citation_text = footnote_defs.get(
                    fn_id, f"[missing footnote {fn_id}]"
                )
                fn_global_id = next_footnote_id
                next_footnote_id += 1
                footnotes_to_add.append((fn_global_id, citation_text))
                self._add_footnote_reference_run(p, fn_global_id)
                last_idx = end
            if last_idx < len(line_str):
                p.add_run(line_str[last_idx:])

        # 4. Configure every section to restart footnote numbering at its
        #    start — this is what makes per-chapter numbering work.
        for section in doc.sections:
            self._set_section_footnote_restart(section)

        # 5. Save and post-process the package.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        if footnotes_to_add:
            _inject_footnotes_part(output_path, footnotes_to_add)
        print(f"[Parser] Styled Book compiled successfully to {output_path}")

    def _add_footnote_reference_run(self, paragraph, fn_global_id: int) -> None:
        """Add a `<w:footnoteReference w:id="N"/>` inside a new superscript
        run on the given paragraph. The actual footnote definition lives in
        footnotes.xml (injected after save)."""
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
        """Add `<w:footnotePr><w:numRestart w:val="eachSect"/></w:footnotePr>`
        to a section's `sectPr`. With one section per chapter, this is what
        makes Word restart footnote numbering at every chapter."""
        assert OxmlElement is not None and qn is not None
        sectPr = section._sectPr
        # Remove any pre-existing footnotePr to avoid duplicates.
        for existing in sectPr.findall(qn("w:footnotePr")):
            sectPr.remove(existing)
        footnote_pr = OxmlElement("w:footnotePr")
        num_restart = OxmlElement("w:numRestart")
        num_restart.set(qn("w:val"), "eachSect")
        footnote_pr.append(num_restart)
        sectPr.append(footnote_pr)
