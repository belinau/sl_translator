# translate_core/vl_parser.py
#
# Vision-Language Book Parser — staged pipeline for humanities PDF ingestion.
#
# Architecture (v10 — one-word classification, markdown extraction):
#   Stage 1: Classify every page via VLM (one-word plain text response).
#   Stage 1.5: Heuristic enrichment (running headers, footnote promotion,
#              chapter title detection) using PyMuPDF text.
#   Stage 2: Extract complex pages via VLM (markdown transcription).
#            Simple pages use PyMuPDF text directly (fast path).
#   Stage 3: Merge pages into chapters, reconcile footnotes, build outline.
#
# Endnotes vs Footnotes:
#   ENDNOTES    = collected notes at the back of the book (pages 195–234 etc.)
#   FOOTNOTES_HEAVY = body-text pages where >25% of lines are footnote refs
#
# This module is lazy-imported by doc_parser.py only when use_vl=True.
# The editor (main.py) never imports it.

import base64
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import httpx
from PIL import Image

from translate_core.vl_prompts import (
    CLASSIFY_MAX_TOKENS,
    CLASSIFY_PROMPT,
    CLASSIFY_WORD_MAP,
    EXTRACT_CONFIG,
    PROMPT_VERSION,
    SYSTEM_CLASSIFY,
    SYSTEM_EXTRACT,
)

log = logging.getLogger("vl_parser")

DEFAULT_VL_MODEL = os.environ.get(
    "SL_VL_MODEL", "MuXodious/LFM2.5-VL-1.6B-absolute-heresy-mlx-4Bit"
)
DEFAULT_VL_SERVER_URL = "http://localhost:8081/v1"


# ======================================================================
# Data structures
# ======================================================================


class PageType(str, Enum):
    BLANK = "blank"
    TITLE_PAGE = "title_page"
    COPYRIGHT = "copyright"
    TABLE_OF_CONTENTS = "table_of_contents"
    CHAPTER_START = "chapter_start"
    BODY_TEXT = "body_text"
    BODY_TWO_COLUMN = "body_two_column"
    BODY_PARALLEL_COLUMNS = "body_parallel_columns"
    ENDNOTES = "endnotes"                  # Collected notes at the back of the book
    FOOTNOTES_HEAVY = "footnotes_heavy"    # Body-text page where >25% of lines are footnote refs
    EPIGRAPH = "epigraph"
    BIBLIOGRAPHY = "bibliography"
    INDEX = "index"
    APPENDIX = "appendix"
    ILLUSTRATION = "illustration"
    MIXED = "mixed"


@dataclass
class PageClassification:
    page_number: int
    page_type: PageType
    has_footnotes: bool = False
    has_running_header: bool = False
    has_running_footer: bool = False
    header_text: str = ""
    footer_text: str = ""
    column_count: int = 1
    chapter_title: str = ""
    raw_vl_output: str = ""


@dataclass
class FootnoteDef:
    marker: str
    text: str
    page_number: int


@dataclass
class ColumnPair:
    """One aligned row of a parallel-column page (e.g. source / translation)."""

    left: str
    right: str


@dataclass
class TOCEntry:
    """One entry from the table of contents."""

    level: int
    kind: str  # part|chapter|section|subsection|front_matter|back_matter|untitled
    number: str  # printed numbering ("3", "III", "3.2", "a)", "")
    title: str  # exact title, leader dots stripped
    page_number: str  # printed page number, or "" if absent
    source_page: int = -1  # PDF page index where this entry appeared


@dataclass
class PageExtraction:
    page_number: int
    page_type: PageType
    markdown_text: str = ""
    footnotes: list[FootnoteDef] = field(default_factory=list)
    chapter_title: str = ""
    epigraph: str = ""
    running_header: str = ""
    running_footer: str = ""
    column_pairs: list[ColumnPair] = field(default_factory=list)
    toc_entries: list[TOCEntry] = field(default_factory=list)
    toc_continues_next: bool = False
    raw_vl_output: str = ""


@dataclass
class BookOutline:
    """Resolved table of contents."""

    entries: list[TOCEntry] = field(default_factory=list)
    page_to_chapter: dict[str, int] = field(default_factory=dict)
    reconciliation_warnings: list[str] = field(default_factory=list)


@dataclass
class BookParseResult:
    source_file: str
    total_pages: int
    page_classifications: list[PageClassification] = field(default_factory=list)
    page_extractions: dict[int, PageExtraction] = field(default_factory=dict)
    markdown_by_page: dict[int, str] = field(default_factory=dict)
    outline: BookOutline = field(default_factory=BookOutline)
    final_markdown: str = ""
    segments_with_metadata: list[dict] = field(default_factory=list)
    parse_time_seconds: float = 0.0


# ======================================================================
# Page-type classification sets (used by orchestrator)
# ======================================================================

COMPLEX_TYPES = {
    PageType.CHAPTER_START,
    PageType.BODY_TWO_COLUMN,
    PageType.BODY_PARALLEL_COLUMNS,
    PageType.ENDNOTES,
    PageType.FOOTNOTES_HEAVY,
    PageType.BIBLIOGRAPHY,
    PageType.TABLE_OF_CONTENTS,
    PageType.INDEX,
    PageType.EPIGRAPH,
    PageType.MIXED,
}

TEXT_FAST_PATH_TYPES = {
    PageType.BODY_TEXT,
    PageType.APPENDIX,
    PageType.COPYRIGHT,
    PageType.TITLE_PAGE,
}

SKIP_TYPES = {PageType.BLANK}


# ======================================================================
# VLMClient (sync, OpenAI-compatible)
# ======================================================================


class VLMClient:
    """Synchronous HTTP client for mlx_vlm.server (OpenAI-compatible API)."""

    def __init__(
        self,
        url: str = DEFAULT_VL_SERVER_URL,
        model: str = DEFAULT_VL_MODEL,
        timeout: float = 300.0,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    @staticmethod
    def prepare_image(
        img: Image.Image, max_dim: int = 1024, quality: int = 85
    ) -> str:
        # 1024 max dim + quality 85 matches the proven pod-farm settings for
        # LFM2.5-VL 1.6B. Larger images cause token-queue timeouts on this small model.
        if max(img.size) > max_dim:
            img = img.copy()
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode()

    def health(self) -> bool:
        try:
            return self._client.get(f"{self.url}/models").status_code == 200
        except Exception:
            return False

    def call(
        self,
        image_b64: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        assistant_prefill: str | None = None,
    ) -> str:
        # Build messages list. Assistant prefill (per Liquid docs) starts
        # the model's response with a known prefix — critical for JSON output
        # on small models like LFM2.5-VL-1.6B.
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        },
                    },
                    {"type": "text", "text": user_prompt},
                ],
            },
        ]
        if assistant_prefill:
            messages.append({"role": "assistant", "content": assistant_prefill})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "top_p": 0.95,
            "top_k": 64,
            "min_p": 0.15,
            "repetition_penalty": 1.05,
            "max_tokens": max_tokens,
            "stream": False,
        }

        last_err = None
        for attempt in range(3):
            try:
                r = self._client.post(f"{self.url}/chat/completions", json=payload)
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                # If we prefilled, the model's output starts after the prefix.
                # Some APIs return the full text including the prefix, some don't.
                # We always want to return the complete text (prefix + generated).
                if assistant_prefill and not content.startswith(assistant_prefill[:5]):
                    content = assistant_prefill + content
                return content
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                last_err = e
                log.warning("VLM call attempt %d failed: %s", attempt + 1, e)
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"VLM call failed after 3 attempts: {last_err}")

    def close(self):
        self._client.close()


# Robust JSON extraction from potentially malformed VLM output.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


# ── TOC parsing regex (handles humanities-book layouts) ──────────────
# Page numbers: Arabic OR Roman (lower/upper). Roman capped at 12 chars.
_ROMAN_RE = r"[ivxlcdmIVXLCDM]{1,12}"
_ARABIC_RE = r"\d+"
_PAGE_NUM_RE = rf"(?:{_ARABIC_RE}|{_ROMAN_RE})"

# Line ends with: 2+ chars of [whitespace/dot/ellipsis] then a page number.
_PAGE_TAIL_RE = re.compile(rf"[\s\.…]{{2,}}({_PAGE_NUM_RE})\s*$")

# Full TOC entry: optional leading section number (1, 1.1, etc.),
# title (lazy), leader (4+ spaces OR 2+ dots/ellipses), page number.
_TOC_ENTRY_RE = re.compile(
    r"^\s*"
    rf"(?:({_ARABIC_RE}(?:\.{_ARABIC_RE})*)\s+)?"
    r"(.+?)"
    r"(?:[\.…]{2,}|\s{4,})\s*"
    rf"({_PAGE_NUM_RE})\s*$"
)

# "Contents" / "Table of Contents" heading (any case, possible markdown #s).
_TOC_HEADING_RE = re.compile(
    r"^\s*#*\s*(?:table\s+of\s+)?contents\s*$", re.IGNORECASE
)

# "Part I", "Book Two", "Section 1" — structural headings without page nums.
_PART_HEADING_RE = re.compile(
    r"^\s*(part|book|section)\s+[ivxlcdm\d]", re.IGNORECASE
)

# Validation: keep only well-formed page numbers (avoid English words
# happening to match Roman-numeral chars like "did" or "mix").
_VALID_ROMAN_RE = re.compile(r"^[ivxlcdm]+$|^[IVXLCDM]+$")


def _is_valid_page_num(s: str) -> bool:
    if s.isdigit():
        return True
    return bool(_VALID_ROMAN_RE.match(s)) and len(s) <= 8


def _infer_toc_kind(title: str, number: str) -> tuple[int, str]:
    """Return (level, kind) for a parsed TOC entry.

    kind ∈ {part, chapter, section, front_matter, back_matter}
    """
    t_lower = title.lower().strip()
    head = t_lower.split(":")[0].strip()  # "Introduction: ..." → "introduction"

    if t_lower.startswith(("part ", "book ", "section ")):
        return 0, "part"
    front = {"preface", "foreword", "acknowledgments", "acknowledgements",
             "dedication", "abstract", "prologue", "textual description of the cover art"}
    back = {"notes", "bibliography", "references", "index", "glossary",
            "appendix", "appendices", "epilogue", "afterword", "works cited"}
    if head in front:
        return 1, "front_matter"
    if head in back:
        return 1, "back_matter"
    if number and "." in number:
        return 2, "section"
    return 1, "chapter"


def extract_json_object(raw: str) -> dict | None:
    s = _JSON_FENCE_RE.sub("", raw).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None


# ======================================================================
# PDF rendering and text extraction (PyMuPDF)
# ======================================================================


def render_page_image(
    pdf_path: Path, page_index: int, dpi: int = 150
) -> Image.Image:
    """Render a PDF page as a PIL Image at the given DPI.
    150 DPI on a 6\u00d79\" page gives ~900\u00d71350px, well within the
    1024px max that LFM2.5-VL 1.6B can handle efficiently."""
    import fitz

    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_index]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def extract_page_text(pdf_path: Path, page_index: int) -> str:
    """Extract text from a PDF page using PyMuPDF (sort=True for reading order)."""
    import fitz

    with fitz.open(str(pdf_path)) as doc:
        return doc[page_index].get_text("text", sort=True) or ""


def page_count(pdf_path: Path) -> int:
    """Return the number of pages in a PDF."""
    import fitz

    with fitz.open(str(pdf_path)) as doc:
        return len(doc)


# Internal helper: render from an already-open fitz.Document (avoids re-opening).
def _render_from_doc(doc, page_index: int, dpi: int = 150) -> Image.Image:
    import fitz

    page = doc[page_index]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# ======================================================================
# Header / footer stripping helpers
# ======================================================================


def strip_exact_line(text: str, line_content: str) -> str:
    """Remove lines that exactly match line_content (after stripping)."""
    if not line_content:
        return text
    lines = text.split("\n")
    stripped = [l for l in lines if l.strip() != line_content.strip()]
    return "\n".join(stripped)


# Page-header patterns commonly found in humanities books:
#   "Chapter Title    |  42"      (title separator page)
#   "42    |    Chapter Title"    (page separator title)
#   "42"                            (bare page number)
#   "Notes to Pages 25–27    |  185"   (back-matter header)
_PAGE_HEADER_PATTERNS = [
    re.compile(r"^\s*[^|\n]{1,80}\s+\|\s+\d+\s*$"),
    re.compile(r"^\s*\d+\s+\|\s+[^|\n]{1,80}\s*$"),
    re.compile(r"^\s*\d{1,4}\s*$"),
    re.compile(r"^\s*[ivxlcdm]{1,8}\s*$"),
]


def looks_like_page_header(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return any(p.match(s) for p in _PAGE_HEADER_PATTERNS)


def strip_page_headers(text: str) -> str:
    """Strip leading and trailing lines that look like running page headers
    or footers (title|num, num|title, bare page number).

    Applied to every page's PyMuPDF text — humanities books reliably put
    these at the page top/bottom and they aren't translatable content.
    Internal lines are left alone (real footnote markers can look similar).
    """
    lines = text.split("\n")
    # Strip leading headers (skip blanks between)
    while lines:
        first_non_blank = next((idx for idx, l in enumerate(lines) if l.strip()), None)
        if first_non_blank is None:
            break
        if looks_like_page_header(lines[first_non_blank]):
            del lines[: first_non_blank + 1]
        else:
            break
    # Strip trailing footers
    while lines:
        last_non_blank = next(
            (idx for idx, l in enumerate(reversed(lines)) if l.strip()), None
        )
        if last_non_blank is None:
            break
        actual_idx = len(lines) - 1 - last_non_blank
        if looks_like_page_header(lines[actual_idx]):
            del lines[actual_idx:]
        else:
            break
    return "\n".join(lines)


def strip_running_lines(text: str) -> str:
    """Conservative heuristic: strip first/last lines shorter than 60 chars
    that aren't Markdown structural lines (#, *, >, [^, -, numbered list items).
    Bare page numbers like '42' are stripped; numbered list items like '1. Item' are kept."""
    lines = text.split("\n")

    def _is_structural(line: str) -> bool:
        s = line.strip()
        if not s:
            return True  # blank lines are neutral
        if s.startswith(("#", "*", ">", "[^")):
            return True
        # Keep numbered list items like "1. First item" or "1) First item"
        if re.match(r"^\d+[.)\s]", s) and len(s) > 4:
            return True
        # Keep bullet list items
        if s.startswith("-"):
            return True
        return False

    # Strip leading running line (short, non-structural)
    while lines and not _is_structural(lines[0]) and len(lines[0].strip()) < 60:
        lines.pop(0)
    # Strip trailing running line (short, non-structural)
    while lines and not _is_structural(lines[-1]) and len(lines[-1].strip()) < 60:
        lines.pop()
    return "\n".join(lines)


def strip_inline_footnote_defs(text: str) -> str:
    """Remove [^N]: ... lines from per-page markdown (they'll be
    collected at the chapter level by _merge)."""
    return re.sub(r"^\[\^[^\]]+\]:\s+.*$", "", text, flags=re.MULTILINE).strip()


# ── Paragraph segmentation ────────────────────────────────────────────
# Goal: translator-editable segments that never contain half-sentences.
#
# Rules:
#   1. Blank lines = paragraph break.
#   2. Markdown structural lines (#, >, -, *, N., [^N]:) = their own segment.
#   3. Otherwise: ALL consecutive non-blank lines join into one paragraph —
#      PDF line wraps are never segment boundaries.
#   4. Hyphenated word-splits across lines ("iden-\nities") repair to "identities".
#   5. Paragraphs longer than max_chars split ONLY at sentence boundaries
#      (period/!/? followed by space + capital). Never mid-sentence.

_MD_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*(?:#+\s|>\s|[-*+]\s|\d+[.)]\s|\[\^\w+\]:)"
)
_MULTI_SPACE_RE = re.compile(r"  +")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“‘«¿¡])")


def _flush_buffer(buf: list[str], out: list[str]) -> None:
    """Join a buffer of non-structural lines into one paragraph, repairing
    hyphenated word-splits at line boundaries. Push onto out, clear buf."""
    if not buf:
        return
    joined = ""
    for raw in buf:
        s = raw.strip()
        if not s:
            continue
        if joined.endswith("-") and joined[-2:-1].isalpha() and s[:1].isalpha():
            # "exaggerat-" + "ing" → "exaggerating"
            joined = joined[:-1] + s
        elif joined:
            joined += " " + s
        else:
            joined = s
    joined = _MULTI_SPACE_RE.sub(" ", joined).strip()
    if joined:
        out.append(joined)
    buf.clear()


def split_paragraphs(text: str, max_chars: int = 1500) -> list[str]:
    """Split markdown into translator-sized segments without ever breaking
    a sentence. See module-level rules comment for the segmentation policy."""
    if not text or not text.strip():
        return []

    paragraphs: list[str] = []
    buffer: list[str] = []

    for line in text.split("\n"):
        if not line.strip():
            _flush_buffer(buffer, paragraphs)
            continue
        if _MD_STRUCTURAL_LINE_RE.match(line):
            # New structural element. Close out the previous one, then
            # start this one in the buffer — continuation lines (no
            # marker) will fold into it.
            _flush_buffer(buffer, paragraphs)
            buffer.append(line)
            continue
        buffer.append(line)
    _flush_buffer(buffer, paragraphs)

    # Split overlong paragraphs at sentence boundaries only.
    result: list[str] = []
    for p in paragraphs:
        if len(p) <= max_chars:
            result.append(p)
            continue
        sentences = _SENTENCE_BOUNDARY_RE.split(p)
        chunk = ""
        for s in sentences:
            candidate = f"{chunk} {s}".strip() if chunk else s
            if len(candidate) > max_chars and chunk:
                result.append(chunk)
                chunk = s
            else:
                chunk = candidate
        if chunk:
            result.append(chunk)
    return result


# ======================================================================
# Title fuzzy matching for outline reconciliation
# ======================================================================


def _normalize_title(title: str) -> str:
    """Lowercase, collapse whitespace, strip trailing punctuation."""
    t = re.sub(r"\s+", " ", title.strip()).lower()
    t = t.rstrip(".,;:!? ")
    return t


def titles_match(toc_title: str, body_title: str, threshold: float = 0.85) -> bool:
    """Fuzzy title match: normalized equality or Levenshtein ratio >= threshold."""
    a, b = _normalize_title(toc_title), _normalize_title(body_title)
    if a == b:
        return True
    # Simple Levenshtein ratio (no external dep)
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio() >= threshold


# ======================================================================
# Caching (per-page JSON files)
# ======================================================================


def _source_hash(pdf_path: Path) -> str:
    """SHA-1 of the entire file (not a partial chunk — humanities books
    from the same series often share front matter)."""
    h = hashlib.sha1()
    with open(pdf_path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _cache_meta_path(cache_dir: Path) -> Path:
    return cache_dir / "cache_meta.json"


def _load_cache_meta(cache_dir: Path | None) -> dict | None:
    if cache_dir is None:
        return None
    meta_path = _cache_meta_path(cache_dir)
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _cache_is_valid(cache_dir: Path | None, pdf_path: Path, model: str) -> bool:
    """Check if cache is still valid (same file, same model, same prompt version)."""
    meta = _load_cache_meta(cache_dir)
    if meta is None:
        return False
    return (
        meta.get("source_sha1") == _source_hash(pdf_path)
        and meta.get("model") == model
        and meta.get("prompt_version") == PROMPT_VERSION
    )


def _init_cache(cache_dir: Path | None, pdf_path: Path, model: str) -> None:
    """Create cache directory and write meta file."""
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "source_sha1": _source_hash(pdf_path),
        "source_size": pdf_path.stat().st_size,
        "source_mtime": pdf_path.stat().st_mtime,
        "model": model,
        "prompt_version": PROMPT_VERSION,
    }
    _cache_meta_path(cache_dir).write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )


def _cache_get(cache_dir: Path | None, kind: str, page_index: int) -> object | None:
    """Load a cached classification or extraction. Returns None on miss."""
    if cache_dir is None:
        return None
    f = cache_dir / kind / f"{page_index:03d}.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return _deserialize_cached(data, kind)
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _cache_put(cache_dir: Path | None, kind: str, page_index: int, obj: object) -> None:
    """Store a classification or extraction result."""
    if cache_dir is None:
        return
    d = cache_dir / kind
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{page_index:03d}.json"
    f.write_text(json.dumps(_serialize_for_cache(obj), indent=2), encoding="utf-8")


# ======================================================================
# Serialization helpers for cache objects
# ======================================================================


def _serialize_for_cache(obj: object) -> dict:
    """Convert a dataclass to a JSON-serializable dict."""
    if isinstance(obj, PageClassification):
        return {
            "__type__": "PageClassification",
            "page_number": obj.page_number,
            "page_type": obj.page_type.value,
            "has_footnotes": obj.has_footnotes,
            "has_running_header": obj.has_running_header,
            "has_running_footer": obj.has_running_footer,
            "header_text": obj.header_text,
            "footer_text": obj.footer_text,
            "column_count": obj.column_count,
            "chapter_title": obj.chapter_title,
            "raw_vl_output": obj.raw_vl_output,
        }
    if isinstance(obj, PageExtraction):
        return {
            "__type__": "PageExtraction",
            "page_number": obj.page_number,
            "page_type": obj.page_type.value,
            "markdown_text": obj.markdown_text,
            "footnotes": [
                {"marker": fn.marker, "text": fn.text, "page_number": fn.page_number}
                for fn in obj.footnotes
            ],
            "chapter_title": obj.chapter_title,
            "epigraph": obj.epigraph,
            "running_header": obj.running_header,
            "running_footer": obj.running_footer,
            "column_pairs": [
                {"left": cp.left, "right": cp.right} for cp in obj.column_pairs
            ],
            "toc_entries": [
                {
                    "level": e.level,
                    "kind": e.kind,
                    "number": e.number,
                    "title": e.title,
                    "page_number": e.page_number,
                    "source_page": e.source_page,
                }
                for e in obj.toc_entries
            ],
            "toc_continues_next": obj.toc_continues_next,
            "raw_vl_output": obj.raw_vl_output,
        }
    raise TypeError(f"Cannot serialize {type(obj)}")


def _deserialize_cached(data: dict, kind: str) -> object:
    """Reconstruct a dataclass from cached JSON."""
    if kind == "cls":
        return PageClassification(
            page_number=data["page_number"],
            page_type=PageType(data["page_type"]),
            has_footnotes=data.get("has_footnotes", False),
            has_running_header=data.get("has_running_header", False),
            has_running_footer=data.get("has_running_footer", False),
            header_text=data.get("header_text", ""),
            footer_text=data.get("footer_text", ""),
            column_count=data.get("column_count", 1),
            chapter_title=data.get("chapter_title", ""),
            raw_vl_output=data.get("raw_vl_output", ""),
        )
    if kind == "ext":
        return PageExtraction(
            page_number=data["page_number"],
            page_type=PageType(data["page_type"]),
            markdown_text=data.get("markdown_text", ""),
            footnotes=[
                FootnoteDef(
                    marker=fn["marker"],
                    text=fn["text"],
                    page_number=fn["page_number"],
                )
                for fn in data.get("footnotes", [])
            ],
            chapter_title=data.get("chapter_title", ""),
            epigraph=data.get("epigraph", ""),
            running_header=data.get("running_header", ""),
            running_footer=data.get("running_footer", ""),
            column_pairs=[
                ColumnPair(left=cp["left"], right=cp["right"])
                for cp in data.get("column_pairs", [])
            ],
            toc_entries=[
                TOCEntry(
                    level=e["level"],
                    kind=e["kind"],
                    number=e["number"],
                    title=e["title"],
                    page_number=e["page_number"],
                    source_page=e.get("source_page", -1),
                )
                for e in data.get("toc_entries", [])
            ],
            toc_continues_next=data.get("toc_continues_next", False),
            raw_vl_output=data.get("raw_vl_output", ""),
        )
    raise ValueError(f"Unknown cache kind: {kind}")


# ======================================================================
# Parsing helpers — classification (v10: one-word plain text)
# ======================================================================


def parse_word_classification(page_index: int, raw: str) -> PageClassification:
    """Parse v10 one-word VLM classification into a PageClassification.

    The VLM responds with a single word like "endnotes", "bibliography",
    "title_page", etc. We map it via CLASSIFY_WORD_MAP and fall back
    to BODY_TEXT on unknown responses.

    Heuristic enrichment (running headers, footnote promotion,
    chapter titles) happens in Stage 1.5, not here.
    """
    word = raw.strip().lower().rstrip(".,;:!")
    # Handle cases where the model adds a period or explanation
    word = word.split()[0] if word and not word.replace("_", "").isalnum() else word
    # Try exact match first, then the first word
    pt_str = CLASSIFY_WORD_MAP.get(word)
    if pt_str is None and " " in word:
        # Model might have responded with a phrase — try the full phrase
        pt_str = CLASSIFY_WORD_MAP.get(word)
    if pt_str is None:
        # Try matching the first token against known words
        first = word.split()[0] if word.split() else word
        pt_str = CLASSIFY_WORD_MAP.get(first, "body_text")
    try:
        pt = PageType(pt_str)
    except ValueError:
        pt = PageType.BODY_TEXT

    return PageClassification(
        page_number=page_index,
        page_type=pt,
        raw_vl_output=raw,
    )


def parse_classification(page_index: int, raw: str) -> PageClassification:
    """Parse legacy v9 JSON classification output into a PageClassification.

    Kept for backward compatibility with cached results from v9.
    Falls back to BODY_TEXT on invalid JSON or unknown page_type.
    """
    data = extract_json_object(raw)
    if data is None:
        return PageClassification(
            page_number=page_index,
            page_type=PageType.BODY_TEXT,
            raw_vl_output=raw,
        )
    try:
        pt = PageType(data.get("page_type", "body_text"))
    except ValueError:
        pt = PageType.BODY_TEXT

    return PageClassification(
        page_number=page_index,
        page_type=pt,
        has_footnotes=bool(data.get("has_footnotes", False)),
        has_running_header=bool(data.get("has_running_header", False)),
        has_running_footer=bool(data.get("has_running_footer", False)),
        header_text=str(data.get("header_text", "")),
        footer_text=str(data.get("footer_text", "")),
        column_count=int(data.get("column_count", 1)),
        chapter_title=str(data.get("chapter_title", "")),
        raw_vl_output=raw,
    )


# ======================================================================
# Parsing helpers — extraction JSON → PageExtraction
# ======================================================================


def _parse_footnotes(data: dict, page_index: int) -> list[FootnoteDef]:
    """Extract footnote list from VL output dict."""
    raw = data.get("footnotes", [])
    result = []
    for fn in raw:
        if isinstance(fn, dict):
            result.append(
                FootnoteDef(
                    marker=str(fn.get("marker", "")),
                    text=str(fn.get("text", "")),
                    page_number=page_index,
                )
            )
    return result


def parse_extraction(
    page_index: int, page_type: PageType, raw: str, expects_json: bool
) -> PageExtraction:
    """Parse VLM extraction output into a PageExtraction.

    For Markdown-returning types, the raw text becomes markdown_text.
    For JSON-returning types, we parse the structured fields.
    """
    if not expects_json:
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=raw,
            raw_vl_output=raw,
        )

    data = extract_json_object(raw)
    if data is None:
        # Fallback: treat raw output as plain markdown
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=raw,
            raw_vl_output=raw,
        )

    footnotes = _parse_footnotes(data, page_index)

    # Type-specific field extraction
    if page_type == PageType.TABLE_OF_CONTENTS:
        toc_entries = []
        for e in data.get("entries", []):
            toc_entries.append(
                TOCEntry(
                    level=int(e.get("level", 1)),
                    kind=str(e.get("kind", "chapter")),
                    number=str(e.get("number", "")),
                    title=str(e.get("title", "")),
                    page_number=str(e.get("page_number", "")),
                    source_page=page_index,
                )
            )
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=raw,
            footnotes=footnotes,
            chapter_title=str(data.get("chapter_title", "")),
            running_header=str(data.get("running_header", "")),
            running_footer=str(data.get("running_footer", "")),
            toc_entries=toc_entries,
            toc_continues_next=bool(data.get("continues_to_next_page", False)),
            raw_vl_output=raw,
        )

    if page_type == PageType.BODY_PARALLEL_COLUMNS:
        pairs = []
        for cp in data.get("column_pairs", []):
            pairs.append(ColumnPair(left=str(cp.get("left", "")), right=str(cp.get("right", ""))))
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=raw,
            footnotes=footnotes,
            running_header=str(data.get("running_header", "")),
            running_footer=str(data.get("running_footer", "")),
            column_pairs=pairs,
            raw_vl_output=raw,
        )

    if page_type == PageType.EPIGRAPH:
        epigraph_text = ""
        ep = data.get("epigraph")
        if isinstance(ep, dict):
            epigraph_text = str(ep.get("text", ""))
        else:
            epigraph_text = str(data.get("epigraph_text", ""))

        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=raw,
            footnotes=footnotes,
            epigraph=epigraph_text,
            running_header=str(data.get("running_header", "")),
            running_footer=str(data.get("running_footer", "")),
            raw_vl_output=raw,
        )

    # chapter_start, footnotes_heavy, bibliography, index, mixed
    # These all share a similar shape: body_text/entries/zones + footnotes + header/footer
    if page_type == PageType.BIBLIOGRAPHY:
        entries = data.get("entries", [])
        bib_md = "\n\n".join(str(e) for e in entries) if entries else ""
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=bib_md,
            footnotes=footnotes,
            running_header=str(data.get("running_header", "")),
            raw_vl_output=raw,
        )

    if page_type == PageType.INDEX:
        entries = data.get("entries", [])
        idx_md = "\n".join(str(e) for e in entries) if entries else ""
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=idx_md,
            footnotes=footnotes,
            running_header=str(data.get("running_header", "")),
            raw_vl_output=raw,
        )

    if page_type == PageType.MIXED:
        zones = data.get("zones", [])
        zone_md = "\n\n".join(
            f"[{z.get('type', 'body')}] {z.get('text', '')}" for z in zones
        )
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=zone_md,
            footnotes=footnotes,
            running_header=str(data.get("running_header", "")),
            raw_vl_output=raw,
        )

    # chapter_start, footnotes_heavy — generic JSON extraction
    body_text = str(data.get("body_text", ""))
    chapter_title = str(data.get("chapter_title", ""))
    epigraph = ""
    ep = data.get("epigraph")
    if isinstance(ep, dict):
        epigraph = str(ep.get("text", ""))

    return PageExtraction(
        page_number=page_index,
        page_type=page_type,
        markdown_text=body_text,
        footnotes=footnotes,
        chapter_title=chapter_title,
        epigraph=epigraph,
        running_header=str(data.get("running_header", "")),
        running_footer=str(data.get("running_footer", "")),
        raw_vl_output=raw,
    )


# ======================================================================
# Parsing helpers — v10 markdown extraction
# ======================================================================


def _parse_footnotes_from_md(md: str, page_index: int) -> tuple[list[FootnoteDef], str]:
    """Extract footnote definitions, KEEPING multi-line continuation text.

    Recognizes three marker shapes for the start of a footnote:
        [^1]: Author...    (markdown)
        1. Author...       (bare number)
        [1] Author...      (bracket number)

    Until the next marker (or a blank line on a pure endnotes page), every
    non-blank line that follows is treated as continuation of the current
    footnote and joined into its text. Hyphenated splits across lines
    ("Dis-\\nability") are repaired. This is critical: bibliographic
    citations routinely span 3–6 lines and the old single-line-only
    parser was dropping everything after the marker line.

    Returns (footnotes, remaining_markdown). Pre-marker non-blank lines
    (body text on FOOTNOTES_HEAVY pages) end up in remaining_markdown.
    """
    md_fn_re = re.compile(r"^\[\^(\d+)\]:\s*(.+)$")
    bare_fn_re = re.compile(r"^(\d+)[\.:)]\s+(.+)$")
    bracket_fn_re = re.compile(r"^\[(\d+)\]\s*(.+)$")

    footnotes: list[FootnoteDef] = []
    remaining_lines: list[str] = []

    current_marker: str | None = None
    current_parts: list[str] = []

    def finalize() -> None:
        nonlocal current_marker, current_parts
        if current_marker is None:
            return
        text = ""
        for part in current_parts:
            if not part:
                continue
            if text.endswith("-") and text[-2:-1].isalpha() and part[:1].isalpha():
                text = text[:-1] + part
            elif text:
                text += " " + part
            else:
                text = part
        text = re.sub(r"  +", " ", text).strip()
        footnotes.append(
            FootnoteDef(marker=current_marker, text=text, page_number=page_index)
        )
        current_marker = None
        current_parts = []

    for line in md.split("\n"):
        s = line.strip()
        if not s:
            # Blank line ends the current footnote (back-matter endnotes
            # use blank-line separation between entries).
            finalize()
            continue
        m = md_fn_re.match(s) or bare_fn_re.match(s) or bracket_fn_re.match(s)
        if m:
            finalize()
            current_marker = m.group(1)
            current_parts = [m.group(2).strip()]
            continue
        if current_marker is not None:
            current_parts.append(s)
        else:
            remaining_lines.append(line)
    finalize()

    return footnotes, "\n".join(remaining_lines)


def _parse_toc_from_md(md: str, page_index: int) -> tuple[list[TOCEntry], bool]:
    """Parse TOC entries from a TOC page's markdown / plain-text transcription.

    Handles humanities-book layouts:
      \u2022 Roman numeral page numbers (front matter):  Acknowledgments    ix
      \u2022 Bare-number prefix without period:          1 The Question of Being   25
      \u2022 Decimal section numbering:                  1.1 Subsection            30
      \u2022 Dot-leader OR whitespace-only leaders:      Title ....  3   |  Title    3
      \u2022 Multi-line entries (title wraps):           3 Long Title,
                                                       continuation             69
      \u2022 Part/Book/Section headings without pages:   PART I  Foundations

    Returns (toc_entries, continues_to_next_page).
    """
    entries: list[TOCEntry] = []
    continues = False

    # Strip blank lines, markdown bullets, and the "Contents" heading itself.
    raw_lines: list[str] = []
    for line in md.split("\n"):
        s = line.strip()
        if not s:
            continue
        if _TOC_HEADING_RE.match(s):
            continue
        # Strip markdown list/bullet markers
        s = re.sub(r"^[-*+]\s+", "", s)
        # Strip markdown heading hashes (keep the text)
        s = re.sub(r"^#+\s+", "", s)
        raw_lines.append(s)

    # Merge multi-line entries: a line without a page-number tail
    # buffers into the next, until we see a line ending in a page number.
    # Part/Book/Section headings without page tails get emitted standalone.
    merged: list[str] = []
    buf = ""
    for s in raw_lines:
        is_part_heading = (
            _PART_HEADING_RE.match(s) and not _PAGE_TAIL_RE.search(s)
        )
        if is_part_heading:
            if buf:
                merged.append(buf)
                buf = ""
            merged.append(s)
            continue
        candidate = f"{buf} {s}" if buf else s
        if _PAGE_TAIL_RE.search(s):
            merged.append(candidate)
            buf = ""
        else:
            buf = candidate
    if buf:
        # Unterminated entry \u2014 TOC likely continues on next page.
        continues = True

    for s in merged:
        # Part/Book/Section heading without page number \u2192 standalone entry.
        if _PART_HEADING_RE.match(s) and not _PAGE_TAIL_RE.search(s):
            entries.append(TOCEntry(
                level=0, kind="part", number="", title=s.strip(),
                page_number="", source_page=page_index,
            ))
            continue

        m = _TOC_ENTRY_RE.match(s)
        if not m:
            continue
        number = (m.group(1) or "").strip()
        title = m.group(2).strip().rstrip(".").rstrip()
        page_num = m.group(3).strip()

        if not _is_valid_page_num(page_num):
            continue
        if not title:
            continue

        level, kind = _infer_toc_kind(title, number)
        entries.append(TOCEntry(
            level=level, kind=kind, number=number, title=title,
            page_number=page_num, source_page=page_index,
        ))

    return entries, continues


def parse_extraction_md(
    page_index: int, page_type: PageType, raw: str
) -> PageExtraction:
    """Parse v10 markdown VLM extraction into a PageExtraction.

    All v10 extraction prompts produce markdown output — no JSON.
    Structure (footnotes, TOC entries, chapter titles) is parsed
    from the markdown using regex and heuristics.
    """
    md = raw.strip()

    # ── chapter_start: first # heading or first line = title ──────
    if page_type == PageType.CHAPTER_START:
        lines = md.split("\n")
        chapter_title = ""
        body_lines = lines
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("#"):
                # Markdown heading: strip #s and whitespace
                chapter_title = re.sub(r"^#+\s*", "", s).strip()
                body_lines = lines[i + 1:]
                break
            if s and not chapter_title:
                # First non-empty line is the chapter title
                chapter_title = s.rstrip(".")
                body_lines = lines[i + 1:]
                break
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=md,
            chapter_title=chapter_title,
            raw_vl_output=raw,
        )

    # ── endnotes / footnotes_heavy: parse footnote defs ──────────
    if page_type in (PageType.ENDNOTES, PageType.FOOTNOTES_HEAVY):
        footnotes, remaining = _parse_footnotes_from_md(md, page_index)
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=remaining if page_type == PageType.FOOTNOTES_HEAVY else md,
            footnotes=footnotes,
            raw_vl_output=raw,
        )

    # ── table_of_contents: parse TOC entries ─────────────────────
    if page_type == PageType.TABLE_OF_CONTENTS:
        toc_entries, continues = _parse_toc_from_md(md, page_index)
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=md,
            toc_entries=toc_entries,
            toc_continues_next=continues,
            raw_vl_output=raw,
        )

    # ── epigraph: extract attribution if present ──────────────────
    if page_type == PageType.EPIGRAPH:
        epigraph = md
        # Attribution is often on the last line starting with "—"
        attribution_line = ""
        lines = md.split("\n")
        if len(lines) > 1:
            last = lines[-1].strip()
            if last.startswith("—") or last.startswith("–"):
                attribution_line = last
        return PageExtraction(
            page_number=page_index,
            page_type=page_type,
            markdown_text=md,
            epigraph=epigraph,
            raw_vl_output=raw,
        )

    # ── All other types: plain markdown ──────────────────────────
    # bibliography, index, illustration, body_text, etc.
    return PageExtraction(
        page_number=page_index,
        page_type=page_type,
        markdown_text=md,
        raw_vl_output=raw,
    )


# ======================================================================
# Block type classifier for _segment
# ======================================================================


def _is_footnotes_heavy(text: str, threshold: float = 0.25) -> bool:
    """Determine if page text is predominantly endnote/footnote entries.

    Checks for patterns like:
      "1. Author, Title, p. 42."
      "1 Author, Title, p. 42."
      "[1] Author, Title."
      "[^1]: Author, Title."
    Returns True if >threshold of non-blank lines match these patterns.
    """
    lines = text.strip().split("\n")
    non_blank = [l for l in lines if l.strip()]
    if len(non_blank) < 3:
        return False

    note_lines = 0
    for line in non_blank:
        s = line.strip()
        # Pattern 1: "1. " or "1 " — bare number at line start
        if re.match(r"^\d+[\.:)]\s", s):
            note_lines += 1
        # Pattern 2: "[1]" or "[^1]:" — bracketed or markdown footnote defs
        elif re.match(r"^\[\^?\d+\]", s):
            note_lines += 1

    return note_lines / len(non_blank) > threshold


def _maybe_promote_footnotes(
    cls: PageClassification, img: Image.Image | None = None
) -> PageClassification:
    """Heuristic: promote body_text to footnotes_heavy if the VLM
    flagged has_footnotes=True, or if PyMuPDF text / VLM raw output
    contains endnote/footnote patterns.

    The 1.6B VLM model often misclassifies endnote pages as body_text
    but sets has_footnotes=True. This fallback catches those cases.
    """
    if cls.page_type not in (PageType.BODY_TEXT, PageType.APPENDIX):
        return cls

    # VLM explicitly flagged footnotes — promote immediately.
    if cls.has_footnotes:
        return PageClassification(
            page_number=cls.page_number,
            page_type=PageType.FOOTNOTES_HEAVY,
            has_footnotes=True,
            has_running_header=cls.has_running_header,
            has_running_footer=cls.has_running_footer,
            header_text=cls.header_text,
            footer_text=cls.footer_text,
            column_count=cls.column_count,
            chapter_title=cls.chapter_title,
            raw_vl_output=cls.raw_vl_output,
        )

    # Check the VLM's raw output for footnote-like patterns.
    raw = cls.raw_vl_output
    if not raw:
        return cls

    # Count lines that look like endnote/footnote entries:
    #   "1. Author, Title, p. 42."  or  "1 Author, Title, p. 42."
    #   "[1] Author, Title."  or  "[^1]: ..."
    lines = raw.strip().split("\n")
    note_lines = 0
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Pattern 1: bare number at line start
        if re.match(r"^\d+[\.:)]\s", s):
            note_lines += 1
        # Pattern 2: bracketed or markdown footnote defs
        elif re.match(r"^\[\^?\d+\]", s):
            note_lines += 1

    total_lines = max(len([l for l in lines if l.strip()]), 1)
    if note_lines / total_lines > 0.25:
        # More than 25% of lines are note patterns → promote
        return PageClassification(
            page_number=cls.page_number,
            page_type=PageType.FOOTNOTES_HEAVY,
            has_footnotes=True,
            has_running_header=cls.has_running_header,
            has_running_footer=cls.has_running_footer,
            header_text=cls.header_text,
            footer_text=cls.footer_text,
            column_count=cls.column_count,
            chapter_title=cls.chapter_title,
            raw_vl_output=cls.raw_vl_output,
        )

    return cls


def classify_block(block: str) -> str:
    """Classify a Markdown block for segment metadata."""
    b = block.strip()
    if b.startswith("[^") and "]: " in b.split("\n")[0]:
        return "footnote_def"
    if b.startswith("# "):
        return "chapter_title"
    if b.startswith("> "):
        return "epigraph"
    return "body"


# ======================================================================
# VLBookParser — main orchestrator
# ======================================================================


class VLBookParser:
    def __init__(
        self,
        model: str | None = None,
        server_url: str | None = None,
        progress_callback: Callable[[str, int, int, bool], None] | None = None,
    ):
        self.model = model or DEFAULT_VL_MODEL
        self.server_url = server_url or DEFAULT_VL_SERVER_URL
        self.progress_callback = progress_callback

    def _report(self, phase: str, current: int, total: int, cached: bool = False):
        if self.progress_callback:
            self.progress_callback(phase, current, total, cached)

    def _classify_page(self, img: Image.Image, page_index: int) -> PageClassification:
        """Stage 1: Classify page type using one-word VLM response.

        The 1.6B model answers accurately with a single word.
        No JSON — structured output overwhelms this model.
        Heuristic enrichment (headers, footnotes) happens in Stage 1.5.
        """
        image_b64 = VLMClient.prepare_image(img)
        client = VLMClient(url=self.server_url, model=self.model)
        try:
            raw = client.call(
                image_b64, SYSTEM_CLASSIFY, CLASSIFY_PROMPT,
                max_tokens=CLASSIFY_MAX_TOKENS,
            )
            cls = parse_word_classification(page_index, raw)
            return cls
        finally:
            client.close()

    def _extract_page(
        self, img: Image.Image, cls: PageClassification
    ) -> PageExtraction:
        """Stage 2: Extract content from a complex page via VLM.

        Uses simple markdown transcription prompts — no JSON.
        The model transcribes accurately in markdown.
        We parse structure (footnotes, TOC entries, chapter titles)
        from the markdown output in parse_extraction_md().
        """
        pt_value = cls.page_type.value
        entry = EXTRACT_CONFIG.get(pt_value)
        if entry is None:
            # Fallback: treat as body text transcription
            entry = (SYSTEM_EXTRACT, EXTRACT_CONFIG["body_text"][0], EXTRACT_CONFIG["body_text"][1])
            # Unpack properly
            user_prompt = entry[0]
            max_tokens = entry[1]
            system_prompt = SYSTEM_EXTRACT
        else:
            user_prompt, max_tokens = entry
            system_prompt = SYSTEM_EXTRACT

        image_b64 = VLMClient.prepare_image(img)
        client = VLMClient(url=self.server_url, model=self.model)

        label = f"page {cls.page_number + 1} {pt_value}"
        print(f"      → {label} (max {max_tokens}t)…", flush=True)
        stop = threading.Event()
        t_start = time.time()

        def _heartbeat():
            while not stop.wait(30.0):
                elapsed = int(time.time() - t_start)
                print(f"      … {label} still running, {elapsed}s elapsed", flush=True)

        hb = threading.Thread(target=_heartbeat, daemon=True)
        hb.start()
        try:
            raw = client.call(
                image_b64, system_prompt, user_prompt,
                max_tokens=max_tokens,
            )
            return parse_extraction_md(cls.page_number, cls.page_type, raw)
        finally:
            stop.set()
            hb.join(timeout=1.0)
            client.close()

    def _enrich_classifications(
        self,
        classifications: list[PageClassification],
        doc: "fitz.Document",
    ) -> None:
        """Stage 1.5: Heuristic enrichment of VLM classifications.

        After VLM classification, use PyMuPDF text to:
        0. Promote misclassified TOC pages (sparse layout → title_page)
        1. Detect running headers (short lines repeating across pages)
        2. Promote body_text → ENDNOTES or FOOTNOTES_HEAVY where appropriate
        3. Extract chapter_title for chapter_start pages

        Mutates classifications in place.
        """
        total = len(classifications)

        # ── -1. Promote misclassified notes-section pages ──────────────
        # Any page whose top-of-page or header text says "Notes to
        # Page(s) N–M" is part of the back-matter notes section. The
        # 1.6B classifier scatters these across body_text, chapter_start,
        # index, etc. — losing ~half the book's footnotes. Override
        # decisively based on this unambiguous header signal.
        notes_header_re = re.compile(
            r"notes\s+to\s+pages?\s+\d+", re.IGNORECASE
        )
        for i, cls in enumerate(classifications):
            if cls.page_type in (PageType.FOOTNOTES_HEAVY, PageType.ENDNOTES):
                continue
            text = doc[i].get_text("text", sort=True) or ""
            head_lines = [l for l in text.split("\n") if l.strip()][:3]
            if any(notes_header_re.search(l) for l in head_lines):
                classifications[i] = PageClassification(
                    page_number=cls.page_number,
                    page_type=PageType.FOOTNOTES_HEAVY,
                    has_footnotes=True,
                    has_running_header=cls.has_running_header,
                    has_running_footer=cls.has_running_footer,
                    header_text=cls.header_text,
                    footer_text=cls.footer_text,
                    column_count=cls.column_count,
                    chapter_title=cls.chapter_title,
                    raw_vl_output=cls.raw_vl_output,
                )

        # ── 0. Promote misclassified TOC pages ─────────────────────────
        # The 1.6B VLM often calls the TOC page "title_page" because the
        # layout is sparse. Catch it by looking for a "Contents" heading
        # in the first few lines of PyMuPDF text, then walk forward to
        # catch continuation pages whose lines are mostly TOC-shaped.
        toc_shape_re = re.compile(rf".+\s+(?:{_PAGE_NUM_RE})\s*$")
        toc_seeds: list[int] = []
        promotable_types = {
            PageType.TITLE_PAGE, PageType.BODY_TEXT, PageType.BLANK,
            PageType.MIXED, PageType.COPYRIGHT,
        }
        for i, cls in enumerate(classifications):
            if cls.page_type not in promotable_types:
                continue
            text = doc[i].get_text("text", sort=True) or ""
            head_lines = [l.strip() for l in text.split("\n") if l.strip()][:4]
            if any(_TOC_HEADING_RE.match(l) for l in head_lines):
                toc_seeds.append(i)

        to_promote_toc: set[int] = set()
        for seed in toc_seeds:
            to_promote_toc.add(seed)
            # Walk forward while pages look like TOC continuations.
            j = seed + 1
            while j < len(classifications):
                cls_j = classifications[j]
                if cls_j.page_type not in promotable_types:
                    break
                text_j = doc[j].get_text("text", sort=True) or ""
                lines_j = [l.strip() for l in text_j.split("\n") if l.strip()]
                if len(lines_j) < 3:
                    break
                toc_lines = sum(1 for l in lines_j if toc_shape_re.match(l))
                if toc_lines / len(lines_j) < 0.5:
                    break
                to_promote_toc.add(j)
                j += 1

        for i in to_promote_toc:
            cls = classifications[i]
            classifications[i] = PageClassification(
                page_number=cls.page_number,
                page_type=PageType.TABLE_OF_CONTENTS,
                has_footnotes=cls.has_footnotes,
                has_running_header=cls.has_running_header,
                has_running_footer=cls.has_running_footer,
                header_text=cls.header_text,
                footer_text=cls.footer_text,
                column_count=cls.column_count,
                chapter_title=cls.chapter_title,
                raw_vl_output=cls.raw_vl_output,
            )

        # ── 0.5. Mark chapter boundaries from the TOC ──────────────────
        # The 1.6B classifier under-fires `chapter_start` (caught 2 of 8 in
        # Kafer). Without per-chapter classification the DOCX gets one
        # giant section and footnote numbering can't restart per chapter.
        # We have all chapter titles + printed page numbers in the TOC
        # (which step 0 above just promoted into TABLE_OF_CONTENTS); cross
        # reference printed page numbers with what appears in running
        # headers/footers to find each chapter's PDF page index.
        self._mark_chapters_from_toc(classifications, doc)

        # ── 1. Detect running headers ──────────────────────────────────
        # A running header is a short line that appears at the top of
        # multiple pages. We collect first lines and find those that
        # repeat across 3+ adjacent pages.
        first_lines: dict[int, str] = {}
        skip_types = SKIP_TYPES | {
            PageType.TITLE_PAGE, PageType.COPYRIGHT,
            PageType.ILLUSTRATION, PageType.TABLE_OF_CONTENTS,
        }
        for i, cls in enumerate(classifications):
            if cls.page_type in skip_types:
                continue
            text = doc[i].get_text("text", sort=True) or ""
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if lines and 5 < len(lines[0]) < 120:
                first_lines[i] = lines[0]

        # Find lines appearing on 3+ adjacent-ish pages
        from collections import Counter
        line_counts = Counter(first_lines.values())
        header_candidates = {
            line for line, count in line_counts.items() if count >= 3
        }
        for i, line in first_lines.items():
            if line in header_candidates:
                cls = classifications[i]
                classifications[i] = PageClassification(
                    page_number=cls.page_number,
                    page_type=cls.page_type,
                    has_footnotes=cls.has_footnotes,
                    has_running_header=True,
                    has_running_footer=cls.has_running_footer,
                    header_text=line,
                    footer_text=cls.footer_text,
                    column_count=cls.column_count,
                    chapter_title=cls.chapter_title,
                    raw_vl_output=cls.raw_vl_output,
                )

        # ── 2. Promote body_text → ENDNOTES / FOOTNOTES_HEAVY ─────────
        # The VLM may miss endnote pages, but PyMuPDF text analysis
        # catches them. ENDNOTES are pure note pages (back of book).
        # FOOTNOTES_HEAVY are body pages with >25% footnote lines.
        for i, cls in enumerate(classifications):
            if cls.page_type in (PageType.BODY_TEXT, PageType.APPENDIX):
                text = doc[i].get_text("text", sort=True) or ""
                if _is_footnotes_heavy(text):
                    # Determine if this is ENDNOTES (pure notes) or
                    # FOOTNOTES_HEAVY (mixed body + notes)
                    lines = [l for l in text.strip().split("\n") if l.strip()]
                    note_lines = sum(
                        1 for l in lines
                        if re.match(r"^\d+[\.:)]\s", l.strip())
                        or re.match(r"^\[\^?\d+\]", l.strip())
                    )
                    # If >60% are note lines, it's pure ENDNOTES
                    if lines and note_lines / len(lines) > 0.6:
                        new_type = PageType.ENDNOTES
                    else:
                        new_type = PageType.FOOTNOTES_HEAVY
                    classifications[i] = PageClassification(
                        page_number=cls.page_number,
                        page_type=new_type,
                        has_footnotes=True,
                        has_running_header=cls.has_running_header,
                        has_running_footer=cls.has_running_footer,
                        header_text=cls.header_text,
                        footer_text=cls.footer_text,
                        column_count=cls.column_count,
                        chapter_title=cls.chapter_title,
                        raw_vl_output=cls.raw_vl_output,
                    )

        # ── 3. Extract chapter_title for chapter_start pages ──────────
        # The VLM classified these as chapter_start, so we know there's
        # a title. Extract it from PyMuPDF text (first non-empty line)
        # or from the VLM extraction in Pass 2.
        for i, cls in enumerate(classifications):
            if cls.page_type == PageType.CHAPTER_START and not cls.chapter_title:
                text = doc[i].get_text("text", sort=True) or ""
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if lines:
                    # First non-empty line is typically the chapter title
                    # (strip common prefixes like "Chapter 3: ")
                    title = lines[0]
                    # Only set if it looks like a title (short, not a paragraph)
                    if len(title) < 120:
                        classifications[i] = PageClassification(
                            page_number=cls.page_number,
                            page_type=cls.page_type,
                            has_footnotes=cls.has_footnotes,
                            has_running_header=cls.has_running_header,
                            has_running_footer=cls.has_running_footer,
                            header_text=cls.header_text,
                            footer_text=cls.footer_text,
                            column_count=cls.column_count,
                            chapter_title=title,
                            raw_vl_output=cls.raw_vl_output,
                        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def _mark_chapters_from_toc(
        self,
        classifications: list[PageClassification],
        doc: "fitz.Document",
    ) -> None:
        """Mark every TOC-listed chapter's PDF page as CHAPTER_START so the
        DOCX compiler emits a section break per chapter (required for
        per-chapter footnote numbering restart).

        Algorithm:
          1. Find TOC pages (already promoted by step 0) and parse their
             entries to get (title, printed page number) per chapter.
          2. Scan every PDF page for its printed page number by extracting
             a small integer from the first or last short line — that's
             the running header/footer page number.
          3. For each chapter entry with a numeric page number, look up
             the PDF page whose printed number matches and mark it
             CHAPTER_START with the TOC title. Walk back one page when the
             preceding page has no printed number (typical chapter-title
             page with blank header).
        """
        toc_pdf_pages = [
            i for i, c in enumerate(classifications)
            if c.page_type == PageType.TABLE_OF_CONTENTS
        ]
        if not toc_pdf_pages:
            return

        # Parse TOC entries directly from PyMuPDF text (no VL needed).
        entries: list[TOCEntry] = []
        for i in toc_pdf_pages:
            text = doc[i].get_text("text", sort=True) or ""
            page_entries, _ = _parse_toc_from_md(text, i)
            entries.extend(page_entries)
        chapter_entries = [
            e for e in entries
            if e.kind in ("chapter", "part") and e.page_number.isdigit()
        ]
        if not chapter_entries:
            return

        # Map PDF index → printed book page number from running headers.
        # Headers in this book are either "Title | 42" or "42 | Title" or
        # a bare "42" on its own line.
        pdf_to_book: dict[int, int] = {}
        bare_num_re = re.compile(r"^\s*(\d{1,4})\s*$")
        side_num_re = re.compile(r"\b(\d{1,4})\b")
        for j in range(len(classifications)):
            if classifications[j].page_type == PageType.BLANK:
                continue
            text = doc[j].get_text("text", sort=True) or ""
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if not lines:
                continue
            # Inspect the first 2 and last 2 non-blank lines — that's
            # where running header / footer page numbers live.
            for line in lines[:2] + lines[-2:]:
                if len(line) > 120:
                    continue  # too long to be a header
                # First try a bare-number-only line
                m = bare_num_re.match(line)
                if not m:
                    # Then any small integer near a "|" separator
                    if "|" in line:
                        m = side_num_re.search(line)
                if m:
                    n = int(m.group(1))
                    if 1 <= n <= 9999:
                        pdf_to_book[j] = n
                        break

        # For each chapter entry, find the PDF page with that book number.
        # Then walk back one page if its predecessor has no printed number
        # (likely the chapter title page itself, which has no header).
        canonical_chapter_pages: dict[int, str] = {}  # pdf_idx → title
        for entry in chapter_entries:
            target = int(entry.page_number)
            candidate = next(
                (j for j in sorted(pdf_to_book) if pdf_to_book[j] == target),
                None,
            )
            if candidate is None:
                continue
            chapter_pdf = candidate
            if (
                candidate > 0
                and candidate - 1 not in pdf_to_book
                and classifications[candidate - 1].page_type
                not in {
                    PageType.CHAPTER_START, PageType.TABLE_OF_CONTENTS,
                    PageType.FOOTNOTES_HEAVY, PageType.ENDNOTES,
                }
            ):
                chapter_pdf = candidate - 1
            canonical_chapter_pages[chapter_pdf] = entry.title.strip()

        # Demote existing chapter_start pages that aren't on the TOC's
        # canonical list — these are VL false positives (titles that came
        # from running headers like "Introduction | 21" or
        # "232 | Bibliography"). Without this, the DOCX picks up the wrong
        # chapter boundaries and footnote restart fires at random pages.
        for i, cls in enumerate(classifications):
            if (
                cls.page_type == PageType.CHAPTER_START
                and i not in canonical_chapter_pages
            ):
                classifications[i] = PageClassification(
                    page_number=cls.page_number,
                    page_type=PageType.BODY_TEXT,
                    has_footnotes=cls.has_footnotes,
                    has_running_header=cls.has_running_header,
                    has_running_footer=cls.has_running_footer,
                    header_text=cls.header_text,
                    footer_text=cls.footer_text,
                    column_count=cls.column_count,
                    chapter_title="",
                    raw_vl_output=cls.raw_vl_output,
                )

        # Promote the canonical pages.
        for pdf_idx, title in canonical_chapter_pages.items():
            cls = classifications[pdf_idx]
            classifications[pdf_idx] = PageClassification(
                page_number=cls.page_number,
                page_type=PageType.CHAPTER_START,
                has_footnotes=cls.has_footnotes,
                has_running_header=cls.has_running_header,
                has_running_footer=cls.has_running_footer,
                header_text=cls.header_text,
                footer_text=cls.footer_text,
                column_count=cls.column_count,
                chapter_title=title,
                raw_vl_output=cls.raw_vl_output,
            )

    def parse_book(
        self, pdf_path: Path, cache_dir: Path | None = None, progress=None
    ) -> BookParseResult:
        """Staged parse: classify → enrich → extract → merge.

        Stage 1:   VLM classifies every page (one-word response).
        Stage 1.5: Heuristic enrichment (headers, footnotes, titles).
        Stage 2:   VLM extracts complex pages; PyMuPDF for simple pages.
        Stage 3:   Merge pages, reconcile footnotes, build outline.

        Args:
            pdf_path: Path to the PDF file.
            cache_dir: Directory for per-page JSON cache. None disables caching.
            progress: Optional callback(phase, current, total, cached).
        """
        import fitz

        t0 = time.time()

        # Cache validity check
        if cache_dir and not _cache_is_valid(cache_dir, pdf_path, self.model):
            # Invalidate: nuke existing cache
            import shutil

            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            _init_cache(cache_dir, pdf_path, self.model)
        elif cache_dir and not (cache_dir / "cls").exists():
            _init_cache(cache_dir, pdf_path, self.model)

        with fitz.open(str(pdf_path)) as doc:
            total = len(doc)
            result = BookParseResult(
                source_file=str(pdf_path), total_pages=total
            )

            # ── Stage 1: Classify every page (one-word VLM) ─────────────
            classifications: list[PageClassification] = []
            for i in range(total):
                cached = _cache_get(cache_dir, "cls", i)
                if cached is not None:
                    classifications.append(cached)
                    self._report("classify", i, total, cached=True)
                    continue
                img = _render_from_doc(doc, i)
                cls = self._classify_page(img, i)
                classifications.append(cls)
                _cache_put(cache_dir, "cls", i, cls)
                self._report("classify", i, total)

            # ── Stage 1.5: Heuristic enrichment ──────────────────────────
            # Detect running headers, promote endnote/footnote pages,
            # extract chapter titles — all from PyMuPDF text.
            self._enrich_classifications(classifications, doc)

            # Update cache with enriched classifications
            for i, cls in enumerate(classifications):
                _cache_put(cache_dir, "cls", i, cls)

            result.page_classifications = classifications

            # ── Stage 2: Extract page content ─────────────────────────────
            #
            # The 1.6B VL model HALLUCINATES content on text-heavy pages
            # (chapter starts, endnotes, bibliography, index, footnotes —
            # observed: real "Ashley X" → fabricated "Mandy Walker").
            # PyMuPDF text from text-faithful PDFs is exact. We use VL ONLY
            # for genuinely visual / spatial needs:
            #   - Empty PyMuPDF text (scanned page)
            #   - BODY_PARALLEL_COLUMNS (needs spatial alignment)
            #
            # For COMPLEX_TYPES with text, run PyMuPDF text through
            # parse_extraction_md to extract structure (footnote defs,
            # TOC entries, chapter titles) — same parsers, just fed
            # truthful input.
            for i, cls in enumerate(classifications):
                if cls.page_type in SKIP_TYPES:
                    result.markdown_by_page[i] = ""
                    continue

                text = doc[i].get_text("text", sort=True) or ""
                # VL is only the right tool when the visual layout IS the
                # content: parallel columns (alignment matters). For pages
                # where PyMuPDF returns empty text, that's a blank or
                # decorative front-matter page (title page on bare cover
                # art etc.) — fall back to VL only if the page is also
                # BODY_PARALLEL_COLUMNS; otherwise emit nothing. Without
                # this guard we re-surface stale hallucinated VL cache.
                needs_vl = cls.page_type == PageType.BODY_PARALLEL_COLUMNS
                if not text.strip() and not needs_vl:
                    result.markdown_by_page[i] = ""
                    self._report("pymupdf", i, total)
                    continue

                if not needs_vl:
                    # Strip running headers/footers (title|num, num|title,
                    # bare page number) — they always leak through PyMuPDF
                    # text and aren't translatable content. Done for every
                    # page regardless of type.
                    text = strip_page_headers(text)
                    if cls.page_type in COMPLEX_TYPES:
                        # Strip known running header/footer before structure
                        # parsing so chapter-title detection doesn't grab
                        # "Introduction | 21" as the title.
                        md_for_parse = text
                        if cls.header_text:
                            md_for_parse = strip_exact_line(md_for_parse, cls.header_text)
                        if cls.footer_text:
                            md_for_parse = strip_exact_line(md_for_parse, cls.footer_text)
                        ext = parse_extraction_md(
                            cls.page_number, cls.page_type, md_for_parse
                        )
                        # The 1.6B classifier over-fires chapter_start on
                        # any centered-line page. A real chapter title is
                        # short, has no sentence-final punctuation, and isn't
                        # a comma-dense fragment. Otherwise demote — the page
                        # is body text, and we don't want a spurious chapter
                        # break in the merged output.
                        #
                        # IMPORTANT: if Stage 1.5's TOC-driven detection
                        # already gave this page a real chapter title from
                        # the printed table of contents, trust it. The
                        # page-extracted title is a fallback heuristic
                        # (first non-empty line) and will usually be
                        # wrong here ("1", a running-header fragment,
                        # body text, etc.). Only override when the slot
                        # is still empty.
                        if cls.page_type == PageType.CHAPTER_START and not cls.chapter_title:
                            ct = (ext.chapter_title or "").strip()
                            looks_like_title = bool(
                                ct
                                and len(ct) < 80
                                and ct.count(",") <= 2
                                and not ct.endswith((".", ":", ",", ";"))
                                and "|" not in ct
                            )
                            if looks_like_title:
                                classifications[i] = PageClassification(
                                    page_number=cls.page_number,
                                    page_type=cls.page_type,
                                    has_footnotes=cls.has_footnotes,
                                    has_running_header=cls.has_running_header,
                                    has_running_footer=cls.has_running_footer,
                                    header_text=cls.header_text,
                                    footer_text=cls.footer_text,
                                    column_count=cls.column_count,
                                    chapter_title=ct,
                                    raw_vl_output=cls.raw_vl_output,
                                )
                            else:
                                # Demote — treat as ordinary body text
                                classifications[i] = PageClassification(
                                    page_number=cls.page_number,
                                    page_type=PageType.BODY_TEXT,
                                    has_footnotes=cls.has_footnotes,
                                    has_running_header=cls.has_running_header,
                                    has_running_footer=cls.has_running_footer,
                                    header_text=cls.header_text,
                                    footer_text=cls.footer_text,
                                    column_count=cls.column_count,
                                    chapter_title="",
                                    raw_vl_output=cls.raw_vl_output,
                                )
                                # Skip the extraction structure; raw text only.
                                result.markdown_by_page[i] = text
                                self._report("pymupdf", i, total)
                                continue
                        result.page_extractions[i] = ext
                        result.markdown_by_page[i] = ext.markdown_text or md_for_parse
                    else:
                        result.markdown_by_page[i] = text
                    self._report("pymupdf", i, total)
                    continue

                # VL path: scanned page or spatial layout
                cached = _cache_get(cache_dir, "ext", i)
                if cached is not None:
                    result.page_extractions[i] = cached
                    result.markdown_by_page[i] = cached.markdown_text
                    self._report("extract", i, total, cached=True)
                    continue
                img = _render_from_doc(doc, i)
                ext = self._extract_page(img, cls)
                result.page_extractions[i] = ext
                result.markdown_by_page[i] = ext.markdown_text
                _cache_put(cache_dir, "ext", i, ext)
                self._report("extract", i, total)

        # ── Book-level reconciliation ──────────────────────────────────
        result.outline = self._build_outline(result)
        result.final_markdown = self._merge(result)
        result.segments_with_metadata = self._segment(result)
        result.parse_time_seconds = time.time() - t0
        return result

    # ------------------------------------------------------------------
    # Book-level merge and footnote reconciliation
    # ------------------------------------------------------------------

    def _merge(self, r: BookParseResult) -> str:
        out: list[str] = []
        current_chapter: list[str] = []
        chapter_footnotes: list[FootnoteDef] = []
        endnotes: list[FootnoteDef] = []  # Collected endnotes (back of book)
        current_chapter_title: str | None = None
        toc_emitted: bool = False

        def flush_chapter():
            if not current_chapter and not current_chapter_title:
                return
            if current_chapter_title:
                out.append(f"# {current_chapter_title}")
            out.extend(current_chapter)
            # Emit every footnote in encounter order. Per-chapter restart
            # numbering in the source book means marker "1" appears in many
            # chapters — earlier code deduped by marker and dropped ~80%
            # of the book's footnotes. preprocess_source_style renumbers
            # everything sequentially downstream, so duplicate markers
            # here are fine.
            for fn in chapter_footnotes:
                out.append(f"[^{fn.marker}]: {fn.text}")
            out.append("")

        for i in range(r.total_pages):
            cls = r.page_classifications[i]

            # TABLE_OF_CONTENTS: emit a clean translatable TOC block from
            # the parsed outline entries (one entry per line, separated by
            # blank lines → each becomes its own segment). Fall back to
            # the raw PyMuPDF text only if no outline was parsed.
            if cls.page_type == PageType.TABLE_OF_CONTENTS:
                if not toc_emitted:
                    if r.outline.entries:
                        # Use H2 so it isn't promoted to a Word section
                        # break (DOCX compiler treats only H1 chapter
                        # titles as section starts for per-chapter
                        # footnote restart).
                        out.append("## Contents")
                        for entry in r.outline.entries:
                            number = entry.number.strip()
                            title = entry.title.strip()
                            page = entry.page_number.strip()
                            parts: list[str] = []
                            if number:
                                parts.append(number)
                            if title:
                                parts.append(title)
                            line = " ".join(parts)
                            if page:
                                line = f"{line} — {page}" if line else page
                            out.append(line)
                    else:
                        md = r.markdown_by_page.get(i, "").strip()
                        if md:
                            out.append(md)
                    toc_emitted = True
                continue

            # ENDNOTES: collect all endnote entries for a back-of-book section
            if cls.page_type == PageType.ENDNOTES:
                ext = r.page_extractions.get(i)
                if ext and ext.footnotes:
                    endnotes.extend(ext.footnotes)
                # Also include the raw markdown if there are no parsed footnotes
                md = r.markdown_by_page.get(i, "").strip()
                if md and (not ext or not ext.footnotes):
                    endnotes_md = md
                    out.append(endnotes_md)
                continue

            if cls.page_type == PageType.CHAPTER_START:
                flush_chapter()
                current_chapter = []
                chapter_footnotes = []
                current_chapter_title = cls.chapter_title or None

            md = r.markdown_by_page.get(i, "").strip()
            if not md:
                continue

            # Exact-match strip of running headers/footers
            if cls.header_text:
                md = strip_exact_line(md, cls.header_text)
            if cls.footer_text:
                md = strip_exact_line(md, cls.footer_text)
            # Fallback heuristic: if classify flagged a header/footer but
            # didn't transcribe it, try conservative length-based strip.
            if (cls.has_running_header and not cls.header_text) or (
                cls.has_running_footer and not cls.footer_text
            ):
                md = strip_running_lines(md)

            # Footnotes from extraction (FOOTNOTES_HEAVY = body+notes page)
            ext = r.page_extractions.get(i)
            if ext and ext.footnotes:
                chapter_footnotes.extend(ext.footnotes)
                md = strip_inline_footnote_defs(md)

            current_chapter.append(md)

        flush_chapter()

        # Append endnotes section at the end of the book.
        # Same rule as chapter footnotes — emit all in order, no dedup;
        # preprocess will renumber sequentially.
        if endnotes:
            out.append("")
            # H2 — see Contents note above.
            out.append("## Notes")
            for fn in endnotes:
                out.append(f"[^{fn.marker}]: {fn.text}")

        return "\n\n".join(out).strip() + "\n"

    # ------------------------------------------------------------------
    # Table of contents: build outline, cross-validate against body
    # ------------------------------------------------------------------

    def _build_outline(self, r: BookParseResult) -> BookOutline:
        outline = BookOutline()

        # 1. Collect all TOC pages in order
        toc_pdf_pages: list[int] = [
            i
            for i, cls in enumerate(r.page_classifications)
            if cls.page_type == PageType.TABLE_OF_CONTENTS
        ]

        # 2. Concatenate entries across consecutive TOC pages.
        #    If VL extraction yielded zero entries for a TOC page (regex
        #    didn't match its transcription), fall back to parsing the
        #    PyMuPDF text of that page directly — humanities books have
        #    text-based TOCs and PyMuPDF preserves the leader-whitespace
        #    structure that the parser keys on.
        raw_entries: list[TOCEntry] = []
        for i in toc_pdf_pages:
            ext = r.page_extractions.get(i)
            page_entries: list[TOCEntry] = []
            if ext and ext.toc_entries:
                page_entries = list(ext.toc_entries)
            else:
                try:
                    pymupdf_text = extract_page_text(Path(r.source_file), i)
                except Exception:
                    pymupdf_text = ""
                if pymupdf_text:
                    fb_entries, _ = _parse_toc_from_md(pymupdf_text, i)
                    page_entries = fb_entries
                    if fb_entries:
                        outline.reconciliation_warnings.append(
                            f"PDF page {i}: VL TOC extraction returned no entries; "
                            f"PyMuPDF fallback parsed {len(fb_entries)}."
                        )
            for entry in page_entries:
                entry.source_page = i
                raw_entries.append(entry)

        outline.entries = raw_entries

        # 3. Cross-validate against detected body chapter starts
        body_chapter_starts: list[tuple[int, str]] = [
            (i, cls.chapter_title)
            for i, cls in enumerate(r.page_classifications)
            if cls.page_type == PageType.CHAPTER_START
        ]
        toc_chapters = [e for e in raw_entries if e.kind in ("chapter", "part")]

        if len(toc_chapters) == len(body_chapter_starts) and toc_chapters:
            for toc_entry, (pdf_idx, body_title) in zip(
                toc_chapters, body_chapter_starts
            ):
                if toc_entry.page_number:
                    outline.page_to_chapter[toc_entry.page_number] = (
                        body_chapter_starts.index((pdf_idx, body_title))
                    )
                if body_title and toc_entry.title and not titles_match(
                    toc_entry.title, body_title
                ):
                    outline.reconciliation_warnings.append(
                        f"TOC chapter '{toc_entry.title}' (page {toc_entry.page_number}) "
                        f"does not match body title '{body_title}' on PDF page {pdf_idx}."
                    )
        elif toc_chapters:
            outline.reconciliation_warnings.append(
                f"TOC lists {len(toc_chapters)} chapters, body has "
                f"{len(body_chapter_starts)} chapter starts — manual review needed."
            )

        return outline

    # ------------------------------------------------------------------
    # Segment metadata for TM / KG ingestion
    # ------------------------------------------------------------------

    def _segment(self, r: BookParseResult) -> list[dict]:
        segs: list[dict] = []
        chapter_idx = -1
        for i in range(r.total_pages):
            cls = r.page_classifications[i]
            if cls.page_type == PageType.CHAPTER_START:
                chapter_idx += 1

            # Outline path for this page (when available)
            outline_path = ""
            if r.outline.entries and chapter_idx >= 0:
                # Find matching TOC entry for this chapter
                toc_chapters = [
                    e for e in r.outline.entries if e.kind in ("chapter", "part")
                ]
                if chapter_idx < len(toc_chapters):
                    entry = toc_chapters[chapter_idx]
                    # Build a path like "part_1/chapter_3" or just "chapter_3"
                    if entry.kind == "part":
                        outline_path = f"part_{entry.number or chapter_idx}".lower().replace(" ", "_")
                    else:
                        # Find parent part if any
                        parent_part = ""
                        for e in r.outline.entries:
                            if e.kind == "part" and e.level < entry.level:
                                parent_part = f"part_{e.number or ''}".lower().replace(" ", "_")
                                break
                        ch_slug = f"chapter_{entry.number or chapter_idx}".lower().replace(" ", "_")
                        outline_path = f"{parent_part}/{ch_slug}" if parent_part else ch_slug

            ext = r.page_extractions.get(i)
            # Parallel-column pages: emit paired segments
            if (
                cls.page_type == PageType.BODY_PARALLEL_COLUMNS
                and ext
                and ext.column_pairs
            ):
                for pair_idx, pair in enumerate(ext.column_pairs):
                    segs.append(
                        {
                            "source_page": i,
                            "chapter_index": max(chapter_idx, 0),
                            "outline_path": outline_path,
                            "type": "parallel_column",
                            "column": "left",
                            "pair_index": pair_idx,
                            "text": pair.left,
                        }
                    )
                    segs.append(
                        {
                            "source_page": i,
                            "chapter_index": max(chapter_idx, 0),
                            "outline_path": outline_path,
                            "type": "parallel_column",
                            "column": "right",
                            "pair_index": pair_idx,
                            "text": pair.right,
                        }
                    )
                continue

            # Standard page: flat split
            md = r.markdown_by_page.get(i, "").strip()
            for block in md.split("\n\n"):
                block = block.strip()
                if not block:
                    continue
                seg_type = classify_block(block)
                segs.append(
                    {
                        "source_page": i,
                        "chapter_index": max(chapter_idx, 0),
                        "outline_path": outline_path,
                        "type": seg_type,
                        "text": block,
                    }
                )
        return segs