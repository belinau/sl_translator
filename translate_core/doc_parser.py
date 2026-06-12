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

# Notes-section header patterns (case-insensitive)
_NOTES_SECTION_RE = re.compile(
    r"^(?:#{1,3}\s*)?(?:Notes|Opombe|Bibliography|Viri|Reference):?\s*$",
    re.IGNORECASE,
)


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

    # ===================================================================
    # STAGE 1: Pre-Translation Normalization (Ingestion)
    # ===================================================================

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

    def _renumber_footnotes(self, text: str) -> tuple:
        """Renumber footnote refs/defs with sequence-expectation validation.

        Three input classes:
          (a) Book with per-chapter endnote/notes sections: promote + renumber.
          (b) Markdown already carrying [^N]/[^N]: markers: validated renumbering.
          (c) Neither notes section nor markers: convert nothing.

        Returns (processed_text, report_dict).
        """
        lines = text.splitlines()
        rejected_def_rows = 0

        in_notes = False
        expected_def = 1
        current_block_count = 0
        chapter_blocks: List[int] = []

        # Pass 1: notes-section detection and def promotion
        processed_lines: List[str] = []
        for line in lines:
            if _NOTES_SECTION_RE.match(line):
                in_notes = True
                processed_lines.append(line)
                continue

            if in_notes:
                existing_def = re.match(r"^(\s*)\[\^(\w+)\]:\s+(.*)$", line)
                if existing_def:
                    num_str = existing_def.group(2)
                    if num_str.isdigit() and int(num_str) == expected_def:
                        expected_def += 1
                        current_block_count += 1
                        processed_lines.append(line)
                        continue
                    if num_str.isdigit() and int(num_str) == 1:
                        if current_block_count > 0:
                            chapter_blocks.append(current_block_count)
                        current_block_count = 1
                        expected_def = 2
                        processed_lines.append(line)
                        continue
                    if not num_str.isdigit():
                        expected_def += 1
                        current_block_count += 1
                        processed_lines.append(line)
                        continue
                    rejected_def_rows += 1
                    processed_lines.append(line)
                    continue

                bare_def = re.match(r"^(\d{1,3})\.?\s+(.*)$", line)
                if bare_def:
                    num = int(bare_def.group(1))
                    content = bare_def.group(2)
                    if num == expected_def:
                        processed_lines.append(f"[^{expected_def}]: {content}")
                        expected_def += 1
                        current_block_count += 1
                        continue
                    elif num == 1:
                        if current_block_count > 0:
                            chapter_blocks.append(current_block_count)
                        current_block_count = 1
                        processed_lines.append("[^1]: " + content)
                        expected_def = 2
                        continue
                    else:
                        rejected_def_rows += 1
                        processed_lines.append(line)
                        continue

                if not line.strip():
                    in_notes = False
                    processed_lines.append(line)
                    continue

                processed_lines.append(line)
                continue

            processed_lines.append(line)

        if current_block_count > 0:
            chapter_blocks.append(current_block_count)

        # Check if defs exist at all
        has_defs = bool(re.search(r"^\[\^(\w+)\]:", text, re.MULTILINE))
        total_defs = sum(chapter_blocks) if chapter_blocks else 0

        # Class (c): no notes section and no existing defs — nothing to convert
        if total_defs == 0 and not has_defs:
            result = "\n".join(processed_lines)
            return result, {
                "defs": 0, "refs": 0, "blocks": [],
                "aligned": True,
                "rejected_def_rows": rejected_def_rows,
                "rejected_body_candidates": 0,
            }

        # Pass 2: global sequential renumbering of defs and refs
        def_line_re = re.compile(r"^(\s*)\[\^(\w+)\]:\s+(.*)$")
        inline_ref_re = re.compile(r"\[\^(\w+)\]")
        output_lines: List[str] = []
        rejected_body_candidates = [0]

        global_counter = 0
        def_map: Dict[str, str] = {}

        for line in processed_lines:
            m = def_line_re.match(line)
            if m:
                global_counter += 1
                old_label = m.group(2)
                new_label = str(global_counter)
                def_map[old_label] = new_label
                output_lines.append(f"[^{new_label}]: {m.group(3)}")
            else:
                output_lines.append(line)

        final_lines: List[str] = []
        for line in output_lines:
            if def_line_re.match(line):
                final_lines.append(line)
                continue

            def _ref_replace(m: re.Match) -> str:
                old = m.group(1)
                if old in def_map:
                    return f"[^{def_map[old]}]"
                rejected_body_candidates[0] += 1
                return m.group(0)
            line = inline_ref_re.sub(_ref_replace, line)
            final_lines.append(line)

        total_ref_count = 0
        for line in final_lines:
            if not def_line_re.match(line):
                total_ref_count += len(inline_ref_re.findall(line))

        result = "\n".join(final_lines)
        return result, {
            "defs": global_counter,
            "refs": total_ref_count,
            "blocks": chapter_blocks,
            "aligned": global_counter == total_ref_count,
            "rejected_def_rows": rejected_def_rows,
            "rejected_body_candidates": rejected_body_candidates[0],
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
        use_vl: bool = False,
        vl_cache_dir: Path | None = None,
        progress_callback=None,
    ) -> str:
        """Convert a local book file to Markdown."""
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
        progress_callback=None,
    ) -> Tuple[str, list]:
        """Convert a local book file to Markdown, returning (markdown, segments_meta)."""
        del use_vl, vl_cache_dir, progress_callback
        raw_text = self.md.convert(str(source)).text_content or ""
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
        self, md_text: str, output_path: Path, house_font: str = "Georgia"
    ):
        """Compile markdown to a styled DOCX with true page-bottom Word footnotes.

        Markdown emphasis *italic*, **bold**, ***both*** are rendered as real
        Word italic/bold runs in both body text and footnotes.
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

        # 3. Body
        next_footnote_id = 1
        footnotes_to_add: List[Tuple[int, str]] = []
        is_first_chapter = True

        for line in body_lines:
            line_str = line.strip()
            if not line_str:
                continue

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
                    self._add_md_runs(p, line_str[last_idx:start])
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
                self._add_md_runs(p, line_str[last_idx:])

        # 4. Configure per-section footnote restart.
        for section in doc.sections:
            self._set_section_footnote_restart(section)

        # 5. Save and post-process.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
        if footnotes_to_add:
            _inject_footnotes_part(output_path, footnotes_to_add)
        print(f"[Parser] Styled Book compiled successfully to {output_path}")

    def compile_from_template(
        self,
        template_path: Path,
        output_path: Path,
        segments: List[dict],
    ) -> None:
        """Compile a translated DOCX by cloning the original and replacing
        text in-place, preserving all paragraph styles, run formatting,
        and document structure."""
        if not HAS_DOCX:
            raise RuntimeError("python-docx not installed")
        assert docx is not None

        doc = docx.Document(str(template_path))

        idx_to_segs: Dict[int, list] = {}
        for seg in segments:
            pi = seg.get("docx_para_idx")
            if pi is not None:
                idx_to_segs.setdefault(pi, []).append(seg)

        paragraphs = doc.paragraphs
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

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))

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