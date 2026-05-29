# tests/test_vl_parser.py
#
# Unit tests for the VL Book Parser pipeline.
# Recorded-fixture tests (no live VLM in CI).
# Live integration tests gated by VL_LIVE=1 env var.

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on sys.path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.vl_parser import (
    BookOutline,
    BookParseResult,
    ColumnPair,
    FootnoteDef,
    PageClassification,
    PageExtraction,
    PageType,
    TOCEntry,
    VLBookParser,
    classify_block,
    extract_json_object,
    parse_classification,
    parse_extraction,
    parse_extraction_md,
    parse_word_classification,
    strip_exact_line,
    strip_inline_footnote_defs,
    strip_running_lines,
    titles_match,
    _serialize_for_cache,
    _deserialize_cached,
)
from translate_core.vl_prompts import PROMPT_VERSION


# ======================================================================
# Fixtures: recorded VLM outputs
# ======================================================================

CLASSIFICATION_JSON_OK = """```json
{
  "page_type": "chapter_start",
  "has_footnotes": true,
  "has_running_header": true,
  "has_running_footer": false,
  "header_text": "42        Heidegger and the Question of Being        Chapter 3",
  "footer_text": "",
  "column_count": 1,
  "chapter_title": "Being and Time"
}
```"""

CLASSIFICATION_JSON_WITH_FENCES = """Here is the classification:
```json
{"page_type": "body_text", "has_footnotes": false, "has_running_header": true, "has_running_footer": true, "header_text": "Chapter 2", "footer_text": "43", "column_count": 1, "chapter_title": ""}
```"""

CLASSIFICATION_INVALID_JSON = """This page appears to be a regular text page with no special layout features."""

CLASSIFICATION_FOOTNOTES_HEAVY = """{
  "page_type": "footnotes_heavy",
  "has_footnotes": true,
  "has_running_header": false,
  "has_running_footer": false,
  "header_text": "",
  "footer_text": "",
  "column_count": 1,
  "chapter_title": ""
}"""

EXTRACTION_BODY_MARKDOWN = """The quick brown fox jumps over the lazy dog.

This is the second paragraph of body text on this page.

[^1]: See chapter 2 for details.
"""

EXTRACTION_FOOTNOTES_JSON = """{
  "body_text": "The main argument rests on three premises.[^1]",
  "footnotes": [
    {"marker": "1", "text": "Heidegger, M. (1962). Being and Time. p. 45."},
    {"marker": "2", "text": "Derrida, J. (1976). Of Grammatology. p. 12."}
  ],
  "running_header": "Chapter 3",
  "running_footer": ""
}"""

EXTRACTION_TOC_JSON = """{
  "entries": [
    {"level": 0, "kind": "part", "number": "I", "title": "Foundations", "page_number": "1"},
    {"level": 1, "kind": "chapter", "number": "1", "title": "The Question of Being", "page_number": "3"},
    {"level": 2, "kind": "section", "number": "1.1", "title": "The Ontological Difference", "page_number": "5"}
  ],
  "continues_from_previous_page": false,
  "continues_to_next_page": true,
  "running_header": "Contents"
}"""


# ======================================================================
# Tests: VLMClient.prepare_image
# ======================================================================


class TestPrepareImage:
    def test_resize_preserves_small(self):
        """Images smaller than max_dim should not be resized."""
        from PIL import Image as PILImage

        from translate_core.vl_parser import VLMClient

        img = PILImage.new("RGB", (100, 200))
        result = VLMClient.prepare_image(img, max_dim=1536)
        # Result is base64-encoded JPEG — decode to check dimensions
        import base64
        import io

        decoded = base64.b64decode(result)
        restored = PILImage.open(io.BytesIO(decoded))
        assert restored.size[0] <= 1536
        assert restored.size[1] <= 1536

    def test_resize_large_image(self):
        """Images larger than max_dim should be thumbnailed."""
        from PIL import Image as PILImage

        from translate_core.vl_parser import VLMClient

        img = PILImage.new("RGB", (3000, 4000))
        result = VLMClient.prepare_image(img, max_dim=1536)
        import base64
        import io

        decoded = base64.b64decode(result)
        restored = PILImage.open(io.BytesIO(decoded))
        assert restored.size[0] <= 1536
        assert restored.size[1] <= 1536


# ======================================================================
# Tests: parse_classification
# ======================================================================


class TestParseClassification:
    def test_valid_json(self):
        cls = parse_classification(5, CLASSIFICATION_JSON_OK)
        assert cls.page_type == PageType.CHAPTER_START
        assert cls.has_footnotes is True
        assert cls.has_running_header is True
        assert cls.header_text == "42        Heidegger and the Question of Being        Chapter 3"
        assert cls.chapter_title == "Being and Time"

    def test_json_with_fences(self):
        cls = parse_classification(10, CLASSIFICATION_JSON_WITH_FENCES)
        assert cls.page_type == PageType.BODY_TEXT
        assert cls.has_running_header is True
        assert cls.header_text == "Chapter 2"

    def test_invalid_json_falls_back(self):
        cls = parse_classification(20, CLASSIFICATION_INVALID_JSON)
        assert cls.page_type == PageType.BODY_TEXT
        assert cls.raw_vl_output == CLASSIFICATION_INVALID_JSON

    def test_footnotes_heavy(self):
        cls = parse_classification(30, CLASSIFICATION_FOOTNOTES_HEAVY)
        assert cls.page_type == PageType.FOOTNOTES_HEAVY
        assert cls.has_footnotes is True


# ======================================================================
# Tests: extract_json_object
# ======================================================================


class TestExtractJsonObject:
    def test_plain_json(self):
        result = extract_json_object('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_fences(self):
        result = extract_json_object('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_json_with_prose_prefix(self):
        result = extract_json_object('Here is the result:\n{"key": "value"}')
        assert result == {"key": "value"}

    def test_no_json(self):
        result = extract_json_object("No JSON here at all.")
        assert result is None

    def test_nested_braces(self):
        result = extract_json_object('{"outer": {"inner": "val"}}')
        assert result == {"outer": {"inner": "val"}}


# ======================================================================
# Tests: parse_extraction
# ======================================================================


class TestParseExtraction:
    def test_body_markdown(self):
        ext = parse_extraction(0, PageType.BODY_TEXT, EXTRACTION_BODY_MARKDOWN, expects_json=False)
        assert "quick brown fox" in ext.markdown_text
        assert ext.page_number == 0

    def test_footnotes_json(self):
        ext = parse_extraction(5, PageType.FOOTNOTES_HEAVY, EXTRACTION_FOOTNOTES_JSON, expects_json=True)
        assert len(ext.footnotes) == 2
        assert ext.footnotes[0].marker == "1"
        assert "Heidegger" in ext.footnotes[0].text
        assert ext.running_header == "Chapter 3"

    def test_toc_hierarchical(self):
        ext = parse_extraction(2, PageType.TABLE_OF_CONTENTS, EXTRACTION_TOC_JSON, expects_json=True)
        assert len(ext.toc_entries) == 3
        assert ext.toc_entries[0].level == 0
        assert ext.toc_entries[0].kind == "part"
        assert ext.toc_entries[1].kind == "chapter"
        assert ext.toc_entries[2].kind == "section"
        assert ext.toc_continues_next is True

    def test_invalid_json_fallback(self):
        ext = parse_extraction(10, PageType.CHAPTER_START, "Just some text", expects_json=True)
        assert ext.markdown_text == "Just some text"

    def test_parallel_columns(self):
        raw = json.dumps({
            "column_pairs": [
                {"left": "Original text", "right": "Translated text"},
                {"left": "Second paragraph", "right": "Second translation"},
            ],
            "running_header": "",
            "running_footer": "",
        })
        ext = parse_extraction(15, PageType.BODY_PARALLEL_COLUMNS, raw, expects_json=True)
        assert len(ext.column_pairs) == 2
        assert ext.column_pairs[0].left == "Original text"
        assert ext.column_pairs[0].right == "Translated text"


# ======================================================================
# Tests: Header / footer stripping
# ======================================================================


class TestHeaderStripping:
    def test_strip_exact_line(self):
        text = "Long Header Text Here\n\nFirst paragraph.\n\nSecond paragraph."
        result = strip_exact_line(text, "Long Header Text Here")
        assert "Long Header" not in result
        assert "First paragraph" in result

    def test_strip_exact_line_no_match(self):
        text = "First paragraph.\n\nSecond paragraph."
        result = strip_exact_line(text, "Not present")
        assert result == text

    def test_strip_running_lines(self):
        text = "42\n\nFirst paragraph.\n\nSecond paragraph.\n\n43"
        result = strip_running_lines(text)
        assert result.strip().startswith("First paragraph")
        # Trailing short page number should be stripped
        lines = result.strip().split("\n")
        assert not lines[-1].strip().startswith("43") or len(lines[-1].strip()) >= 60

    def test_strip_inline_footnote_defs(self):
        text = "Some body text.\n\n[^1]: This is a footnote.\n\nMore body text."
        result = strip_inline_footnote_defs(text)
        assert "[^1]:" not in result
        assert "Some body text" in result


# ======================================================================
# Tests: Merge (chapter-scoped footnote reconciliation)
# ======================================================================


class TestMerge:
    def _make_result(self, pages):
        """Helper to build a BookParseResult from a list of (page_type, markdown, footnotes, header_text, chapter_title)."""
        classifications = []
        markdown_by_page = {}
        page_extractions = {}
        for i, (pt, md, fns, header, ch_title) in enumerate(pages):
            classifications.append(
                PageClassification(
                    page_number=i,
                    page_type=pt,
                    has_running_header=bool(header),
                    header_text=header,
                    chapter_title=ch_title or "",
                )
            )
            markdown_by_page[i] = md
            if fns:
                page_extractions[i] = PageExtraction(
                    page_number=i,
                    page_type=pt,
                    markdown_text=md,
                    footnotes=fns,
                )
        return BookParseResult(
            source_file="test.pdf",
            total_pages=len(pages),
            page_classifications=classifications,
            page_extractions=page_extractions,
            markdown_by_page=markdown_by_page,
        )

    def test_simple_chapter(self):
        r = self._make_result([
            (PageType.CHAPTER_START, "Chapter intro text.", [], "", "Chapter 1"),
            (PageType.BODY_TEXT, "Body text paragraph.", [], "", ""),
        ])
        parser = VLBookParser()
        result = parser._merge(r)
        assert "# Chapter 1" in result
        assert "Chapter intro text" in result
        assert "Body text paragraph" in result

    def test_chapter_with_footnotes(self):
        fns = [
            FootnoteDef(marker="1", text="First footnote.", page_number=1),
            FootnoteDef(marker="2", text="Second footnote.", page_number=1),
        ]
        r = self._make_result([
            (PageType.CHAPTER_START, "Text with [^1] and [^2].", fns, "", "Chapter 2"),
        ])
        parser = VLBookParser()
        result = parser._merge(r)
        assert "[^1]: First footnote." in result
        assert "[^2]: Second footnote." in result

    def test_footnote_dedup_per_chapter(self):
        fns = [
            FootnoteDef(marker="1", text="Footnote one.", page_number=1),
            FootnoteDef(marker="1", text="Footnote one duplicate.", page_number=2),
        ]
        r = self._make_result([
            (PageType.CHAPTER_START, "Page 1 text.", [fns[0]], "", "Chapter 1"),
            (PageType.BODY_TEXT, "Page 2 text.", [fns[1]], "", ""),
        ])
        parser = VLBookParser()
        result = parser._merge(r)
        # Should only have one [^1] definition (first occurrence wins)
        assert result.count("[^1]:") == 1

    def test_header_stripping(self):
        r = self._make_result([
            (PageType.BODY_TEXT, "Running Header Text\n\nBody content here.", [], "Running Header Text", ""),
        ])
        parser = VLBookParser()
        result = parser._merge(r)
        assert "Running Header Text" not in result
        assert "Body content here" in result


# ======================================================================
# Tests: Build outline (TOC reconciliation)
# ======================================================================


class TestBuildOutline:
    def test_multi_page_toc(self):
        toc_page_1 = PageExtraction(
            page_number=2,
            page_type=PageType.TABLE_OF_CONTENTS,
            markdown_text="",
            toc_entries=[
                TOCEntry(level=1, kind="chapter", number="1", title="Introduction", page_number="1", source_page=2),
                TOCEntry(level=1, kind="chapter", number="2", title="The Question", page_number="15", source_page=2),
            ],
        )
        toc_page_2 = PageExtraction(
            page_number=3,
            page_type=PageType.TABLE_OF_CONTENTS,
            markdown_text="",
            toc_entries=[
                TOCEntry(level=1, kind="chapter", number="3", title="Being and Time", page_number="45", source_page=3),
            ],
        )
        classifications = [
            PageClassification(page_number=0, page_type=PageType.TITLE_PAGE),
            PageClassification(page_number=1, page_type=PageType.COPYRIGHT),
            PageClassification(page_number=2, page_type=PageType.TABLE_OF_CONTENTS),
            PageClassification(page_number=3, page_type=PageType.TABLE_OF_CONTENTS),
            PageClassification(page_number=4, page_type=PageType.CHAPTER_START, chapter_title="Introduction"),
            PageClassification(page_number=5, page_type=PageType.BODY_TEXT),
        ]
        result = BookParseResult(
            source_file="test.pdf",
            total_pages=6,
            page_classifications=classifications,
            page_extractions={2: toc_page_1, 3: toc_page_2},
            markdown_by_page={},
        )
        parser = VLBookParser()
        outline = parser._build_outline(result)
        assert len(outline.entries) == 3
        assert outline.entries[0].title == "Introduction"
        assert outline.entries[2].title == "Being and Time"

    def test_chapter_count_mismatch_warning(self):
        result = BookParseResult(
            source_file="test.pdf",
            total_pages=5,
            page_classifications=[
                PageClassification(page_number=0, page_type=PageType.BODY_TEXT),
                PageClassification(page_number=1, page_type=PageType.CHAPTER_START, chapter_title="Chapter 1"),
                PageClassification(page_number=2, page_type=PageType.BODY_TEXT),
                PageClassification(page_number=3, page_type=PageType.TABLE_OF_CONTENTS),
                PageClassification(page_number=4, page_type=PageType.BODY_TEXT),
            ],
            page_extractions={
                3: PageExtraction(
                    page_number=3,
                    page_type=PageType.TABLE_OF_CONTENTS,
                    markdown_text="",
                    toc_entries=[
                        TOCEntry(level=1, kind="chapter", number="1", title="Chapter 1", page_number="1"),
                        TOCEntry(level=1, kind="chapter", number="2", title="Chapter 2", page_number="50"),
                    ],
                ),
            },
            markdown_by_page={},
        )
        parser = VLBookParser()
        outline = parser._build_outline(result)
        assert len(outline.reconciliation_warnings) > 0


# ======================================================================
# Tests: Title fuzzy matching
# ======================================================================


class TestTitlesMatch:
    def test_exact_match(self):
        assert titles_match("On Being and Time", "On Being and Time")

    def test_case_insensitive(self):
        assert titles_match("ON BEING AND TIME", "on being and time")

    def test_trailing_punctuation(self):
        assert titles_match("Being and Time.", "Being and Time")

    def test_fuzzy_match(self):
        assert titles_match("On Being and Time", "ON BEING AND TIME")

    def test_no_match(self):
        assert not titles_match("Completely Different Title", "Another Title Altogether")


# ======================================================================
# Tests: Cache roundtrip
# ======================================================================


class TestCacheRoundtrip:
    def test_classification_roundtrip(self):
        cls = PageClassification(
            page_number=5,
            page_type=PageType.CHAPTER_START,
            has_footnotes=True,
            header_text="Chapter 3",
            chapter_title="Being and Time",
        )
        serialized = _serialize_for_cache(cls)
        deserialized = _deserialize_cached(serialized, "cls")
        assert isinstance(deserialized, PageClassification)
        assert deserialized.page_type == PageType.CHAPTER_START
        assert deserialized.chapter_title == "Being and Time"

    def test_extraction_roundtrip(self):
        ext = PageExtraction(
            page_number=10,
            page_type=PageType.FOOTNOTES_HEAVY,
            markdown_text="Body text",
            footnotes=[FootnoteDef(marker="1", text="Note", page_number=10)],
            running_header="Ch 3",
        )
        serialized = _serialize_for_cache(ext)
        deserialized = _deserialize_cached(serialized, "ext")
        assert isinstance(deserialized, PageExtraction)
        assert deserialized.page_type == PageType.FOOTNOTES_HEAVY
        assert len(deserialized.footnotes) == 1
        assert deserialized.footnotes[0].marker == "1"

    def test_cache_file_roundtrip(self, tmp_path):
        from translate_core.vl_parser import _cache_put, _cache_get, _init_cache

        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"fake pdf content for hashing")
        cache_dir = tmp_path / "cache"
        _init_cache(cache_dir, pdf, "test-model")

        cls = PageClassification(
            page_number=0,
            page_type=PageType.BODY_TEXT,
            has_footnotes=False,
        )
        _cache_put(cache_dir, "cls", 0, cls)
        loaded = _cache_get(cache_dir, "cls", 0)
        assert loaded is not None
        assert loaded.page_type == PageType.BODY_TEXT


# ======================================================================
# Tests: Cache invalidation
# ======================================================================


class TestCacheInvalidation:
    def test_invalidation_on_source_change(self, tmp_path):
        from translate_core.vl_parser import _cache_is_valid, _init_cache

        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"original content")
        cache_dir = tmp_path / "cache"
        _init_cache(cache_dir, pdf, "test-model")
        assert _cache_is_valid(cache_dir, pdf, "test-model")

        # Change the file content
        pdf.write_bytes(b"modified content")
        assert not _cache_is_valid(cache_dir, pdf, "test-model")

    def test_invalidation_on_model_change(self, tmp_path):
        from translate_core.vl_parser import _cache_is_valid, _init_cache

        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"some content")
        cache_dir = tmp_path / "cache"
        _init_cache(cache_dir, pdf, "model-a")
        assert _cache_is_valid(cache_dir, pdf, "model-a")
        assert not _cache_is_valid(cache_dir, pdf, "model-b")


# ======================================================================
# Tests: classify_block for segment metadata
# ======================================================================


class TestClassifyBlock:
    def test_footnote_def(self):
        assert classify_block("[^1]: This is a footnote.") == "footnote_def"

    def test_chapter_title(self):
        assert classify_block("# Introduction") == "chapter_title"

    def test_epigraph(self):
        assert classify_block("> To be or not to be") == "epigraph"

    def test_body(self):
        assert classify_block("This is a regular paragraph.") == "body"


# ======================================================================
# Tests: Prompt version consistency
# ======================================================================


class TestPromptVersion:
    def test_prompt_version_is_int(self):
        assert isinstance(PROMPT_VERSION, int)
        assert PROMPT_VERSION >= 10  # Staged architecture: one-word classification + markdown extraction


# ======================================================================
# Tests: v10 one-word classification
# ======================================================================


class TestWordClassification:
    def test_exact_match(self):
        from translate_core.vl_parser import parse_word_classification
        cls = parse_word_classification(0, "endnotes")
        assert cls.page_type == PageType.ENDNOTES

    def test_case_insensitive(self):
        from translate_core.vl_parser import parse_word_classification
        cls = parse_word_classification(0, "Bibliography")
        assert cls.page_type == PageType.BIBLIOGRAPHY

    def test_trailing_punctuation(self):
        from translate_core.vl_parser import parse_word_classification
        cls = parse_word_classification(0, "title_page.")
        assert cls.page_type == PageType.TITLE_PAGE

    def test_unknown_falls_back(self):
        from translate_core.vl_parser import parse_word_classification
        cls = parse_word_classification(0, "i dont know what this is")
        assert cls.page_type == PageType.BODY_TEXT

    def test_endnotes_vs_footnotes(self):
        from translate_core.vl_parser import parse_word_classification
        cls_endnotes = parse_word_classification(195, "endnotes")
        cls_footnotes = parse_word_classification(10, "footnotes")
        assert cls_endnotes.page_type == PageType.ENDNOTES
        assert cls_footnotes.page_type == PageType.FOOTNOTES_HEAVY

    def test_synonyms(self):
        from translate_core.vl_parser import parse_word_classification
        assert parse_word_classification(0, "notes").page_type == PageType.ENDNOTES
        assert parse_word_classification(0, "toc").page_type == PageType.TABLE_OF_CONTENTS
        assert parse_word_classification(0, "references").page_type == PageType.BIBLIOGRAPHY

    def test_raw_output_preserved(self):
        from translate_core.vl_parser import parse_word_classification
        cls = parse_word_classification(42, "endnotes")
        assert cls.raw_vl_output == "endnotes"
        assert cls.page_number == 42


# ======================================================================
# Tests: v10 markdown extraction
# ======================================================================


class TestMarkdownExtraction:
    def test_chapter_start_with_heading(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "# Being and Time\n\nThe question of being..."
        ext = parse_extraction_md(5, PageType.CHAPTER_START, md)
        assert ext.chapter_title == "Being and Time"
        assert "question of being" in ext.markdown_text

    def test_chapter_start_plain_title(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "3. Feminist, Queer, Crip\n\nThe main argument..."
        ext = parse_extraction_md(5, PageType.CHAPTER_START, md)
        assert ext.chapter_title  # Got some title

    def test_endnotes_extraction(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "[^1]: Kafer, Alison. Feminist, Queer, Crip. p. 42.\n[^2]: Butler, Judith. Gender Trouble. p. 15."
        ext = parse_extraction_md(195, PageType.ENDNOTES, md)
        assert len(ext.footnotes) == 2
        assert ext.footnotes[0].marker == "1"
        assert "Kafer" in ext.footnotes[0].text

    def test_endnotes_bare_numbers(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "1. Kafer, Alison. Feminist, Queer, Crip. p. 42.\n2. Butler, Judith. Gender Trouble. p. 15."
        ext = parse_extraction_md(195, PageType.ENDNOTES, md)
        assert len(ext.footnotes) == 2

    def test_endnotes_bracket_numbers(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "[1] Kafer, Alison. Feminist, Queer, Crip.\n[2] Butler, Judith. Gender Trouble."
        ext = parse_extraction_md(195, PageType.ENDNOTES, md)
        assert len(ext.footnotes) == 2

    def test_footnotes_heavy_strips_notes(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "The main argument.[^1]\n\n[^1]: See chapter 2."
        ext = parse_extraction_md(10, PageType.FOOTNOTES_HEAVY, md)
        assert len(ext.footnotes) == 1
        # Body text remains, footnote defs stripped
        assert "main argument" in ext.markdown_text

    def test_toc_extraction(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "Chapter 1: The Question of Being .... 3\nChapter 2: Feminist Critique ........... 45"
        ext = parse_extraction_md(3, PageType.TABLE_OF_CONTENTS, md)
        assert len(ext.toc_entries) >= 1

    def test_epigraph_extraction(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "The only way out is through.\n— Robert Frost"
        ext = parse_extraction_md(0, PageType.EPIGRAPH, md)
        assert ext.epigraph  # Has epigraph text

    def test_body_text_passthrough(self):
        from translate_core.vl_parser import parse_extraction_md
        md = "The quick brown fox jumps over the lazy dog."
        ext = parse_extraction_md(0, PageType.BODY_TEXT, md)
        assert ext.markdown_text == md
        assert len(ext.footnotes) == 0


# ======================================================================
# Tests: Footnotes heuristic promotion
# ======================================================================


class TestIsFootnotesHeavy:
    def test_pure_endnotes(self):
        from translate_core.vl_parser import _is_footnotes_heavy

        text = (
            "1. Kafer, Alison. Feminist, Queer, Crip. p. 42.\n"
            "2. Butler, Judith. Gender Trouble. p. 15.\n"
            "3. Sedgwick, Eve Kosofsky. Epistemology of the Closet. p. 7.\n"
            "4. McRuer, Robert. Crip Theory. p. 33.\n"
            "5. Garland-Thomson, Rosemarie. Extraordinary Bodies. p. 21."
        )
        assert _is_footnotes_heavy(text) is True

    def test_bracket_endnotes(self):
        from translate_core.vl_parser import _is_footnotes_heavy

        text = (
            "[1] Kafer, Alison. Feminist, Queer, Crip.\n"
            "[2] Butler, Judith. Gender Trouble.\n"
            "[3] Sedgwick, Eve Kosofsky. Epistemology of the Closet."
        )
        assert _is_footnotes_heavy(text) is True

    def test_mixed_body_and_notes(self):
        from translate_core.vl_parser import _is_footnotes_heavy

        # Only ~30% note lines — still triggers at 25% threshold
        text = (
            "This is a paragraph of regular body text that contains\n"
            "important arguments and ideas about disability studies.\n"
            "1. Kafer, Alison. Feminist, Queer, Crip. p. 42.\n"
            "2. Butler, Judith. Gender Trouble. p. 15."
        )
        assert _is_footnotes_heavy(text) is True

    def test_pure_body_text(self):
        from translate_core.vl_parser import _is_footnotes_heavy

        text = (
            "This is a paragraph of regular body text.\n"
            "It contains no footnote references.\n"
            "It is just regular prose about important ideas."
        )
        assert _is_footnotes_heavy(text) is False

    def test_too_few_lines(self):
        from translate_core.vl_parser import _is_footnotes_heavy

        # Less than 3 non-blank lines → skip heuristic
        assert _is_footnotes_heavy("1. A note.") is False

    def test_md_footnote_defs(self):
        from translate_core.vl_parser import _is_footnotes_heavy

        text = (
            "[^1]: Kafer, Alison. Feminist, Queer, Crip. p. 42.\n"
            "[^2]: Butler, Judith. Gender Trouble. p. 15.\n"
            "[^3]: Sedgwick, Eve Kosofsky. Epistemology of the Closet."
        )
        assert _is_footnotes_heavy(text) is True


class TestMaybePromoteFootnotes:
    def test_promotes_body_text_with_note_patterns(self):
        from translate_core.vl_parser import _maybe_promote_footnotes

        cls = PageClassification(
            page_number=200,
            page_type=PageType.BODY_TEXT,
            has_footnotes=False,
            raw_vl_output="1. Kafer, Alison. Feminist, Queer, Crip. p. 42.\n2. Butler, Judith. Gender Trouble. p. 15.",
        )
        result = _maybe_promote_footnotes(cls)
        assert result.page_type == PageType.FOOTNOTES_HEAVY
        assert result.has_footnotes is True

    def test_no_promote_pure_body_text(self):
        from translate_core.vl_parser import _maybe_promote_footnotes

        cls = PageClassification(
            page_number=10,
            page_type=PageType.BODY_TEXT,
            raw_vl_output="This is regular body text with no notes.",
        )
        result = _maybe_promote_footnotes(cls)
        assert result.page_type == PageType.BODY_TEXT

    def test_no_promote_other_types(self):
        from translate_core.vl_parser import _maybe_promote_footnotes

        cls = PageClassification(
            page_number=5,
            page_type=PageType.CHAPTER_START,
            raw_vl_output="1. Some reference.",
        )
        result = _maybe_promote_footnotes(cls)
        assert result.page_type == PageType.CHAPTER_START

    def test_no_promote_empty_raw(self):
        from translate_core.vl_parser import _maybe_promote_footnotes

        cls = PageClassification(
            page_number=10,
            page_type=PageType.BODY_TEXT,
            raw_vl_output="",
        )
        result = _maybe_promote_footnotes(cls)
        assert result.page_type == PageType.BODY_TEXT

    def test_preserves_other_fields(self):
        from translate_core.vl_parser import _maybe_promote_footnotes

        cls = PageClassification(
            page_number=200,
            page_type=PageType.BODY_TEXT,
            has_footnotes=False,
            has_running_header=True,
            header_text="Chapter 3",
            column_count=2,
            raw_vl_output="1. Kafer, Alison. p. 42.\n2. Butler, Judith. p. 15.",
        )
        result = _maybe_promote_footnotes(cls)
        assert result.page_type == PageType.FOOTNOTES_HEAVY
        assert result.has_running_header is True
        assert result.header_text == "Chapter 3"
        assert result.column_count == 2


# ======================================================================
# Live integration tests (gated by VL_LIVE=1)
# ======================================================================


VL_LIVE = os.environ.get("VL_LIVE") == "1"
LIVE_SKIP_REASON = "Set VL_LIVE=1 to run live VLM integration tests"


@pytest.mark.skipif(not VL_LIVE, reason=LIVE_SKIP_REASON)
class TestLiveIntegration:
    """Live VLM tests that require a running mlx_vlm.server."""

    def test_live_classify_one_page(self, tmp_path):
        """First page of a known PDF → expected chapter_start or title_page."""
        from translate_core.vl_parser import render_page_image, VLBookParser
        from PIL import Image as PILImage

        # Requires a fixture PDF at docs/vl_eval/fixtures/sample.pdf
        fixture = Path("docs/vl_eval/fixtures/sample.pdf")
        if not fixture.exists():
            pytest.skip("No fixture PDF at docs/vl_eval/fixtures/sample.pdf")

        parser = VLBookParser()
        img = render_page_image(fixture, 0)
        cls = parser._classify_page(img, 0)
        assert cls.page_type != PageType.BLANK

    def test_live_extract_footnotes_heavy(self, tmp_path):
        """A known footnote-heavy page returns >=3 footnotes."""
        from translate_core.vl_parser import render_page_image, VLBookParser

        fixture = Path("docs/vl_eval/fixtures/sample.pdf")
        if not fixture.exists():
            pytest.skip("No fixture PDF")

        parser = VLBookParser()
        # First classify, then extract if footnotes_heavy
        img = render_page_image(fixture, 0)
        cls = parser._classify_page(img, 0)
        if cls.page_type == PageType.FOOTNOTES_HEAVY:
            ext = parser._extract_page(img, cls)
            assert len(ext.footnotes) >= 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])