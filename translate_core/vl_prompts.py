# translate_core/vl_prompts.py
#
# VL Book Parser Prompts — v10 staged architecture.
#
# ARCHITECTURE
# ───────────
# Stage 1 (Classification): One-word plain text. No JSON.
#   The 1.6B VLM cannot reliably produce structured JSON classification.
#   One-word plain text classification is accurate and fast (128 tokens).
#
# Stage 2 (Extraction): Simple markdown transcription. No JSON.
#   The model transcribes accurately in markdown. We parse structure
#   (footnotes, TOC entries, chapter titles) from the markdown output
#   using regex and heuristics instead of requiring JSON from the model.
#
# IMPORTANT: Bump PROMPT_VERSION when any prompt changes —
# cache invalidation depends on it.

PROMPT_VERSION = 10

# ── Stage 1: Page classification ─────────────────────────────────────
#
# Ask for a single word. The model answers accurately with this format.
# No JSON, no structured output, just a one-word answer.
# "endnotes" and "footnotes" are distinct page types:
#   endnotes = collected notes at the back of the book
#   footnotes = notes at the bottom of a body-text page

SYSTEM_CLASSIFY = "You are a page classifier. Answer with only the page type."

CLASSIFY_PROMPT = (
    "What type of page is this? "
    "Answer with one word: blank, title_page, copyright, "
    "table_of_contents, chapter_start, body_text, endnotes, "
    "footnotes, bibliography, index, or illustration."
)

CLASSIFY_MAX_TOKENS = 16  # One word + maybe punctuation

# Map VLM plain-text responses (lowercased, stripped) to PageType values.
# Handles common variations and synonyms the 1.6B model produces.
CLASSIFY_WORD_MAP: dict[str, str] = {
    "blank": "blank",
    "title_page": "title_page",
    "title page": "title_page",
    "title": "title_page",
    "titlepage": "title_page",
    "copyright_page": "copyright",
    "copyright page": "copyright",
    "copyright": "copyright",
    "table_of_contents": "table_of_contents",
    "table of contents": "table_of_contents",
    "toc": "table_of_contents",
    "contents": "table_of_contents",
    "chapter_start": "chapter_start",
    "chapter start": "chapter_start",
    "chapter": "chapter_start",
    "body_text": "body_text",
    "body text": "body_text",
    "body": "body_text",
    "text": "body_text",
    "endnotes": "endnotes",
    "endnote": "endnotes",
    "notes": "endnotes",
    "footnotes": "footnotes_heavy",
    "footnotes_heavy": "footnotes_heavy",
    "footnote": "footnotes_heavy",
    "bibliography": "bibliography",
    "references": "bibliography",
    "works cited": "bibliography",
    "works_cited": "bibliography",
    "index": "index",
    "illustration": "illustration",
    "figure": "illustration",
    "image": "illustration",
    "epigraph": "epigraph",
    "appendix": "appendix",
}

# ── Stage 2: Content extraction ──────────────────────────────────────
#
# All extraction prompts ask for markdown transcription.
# No JSON. The model transcribes accurately in markdown.
# We parse structure from the markdown output in vl_parser.py.

SYSTEM_EXTRACT = "Transcribe exactly what you see in markdown."

EXTRACT_BODY = "Transcribe this page to markdown."
EXTRACT_CHAPTER_START = "Transcribe title and page content."
EXTRACT_ENDNOTES = (
    "Transcribe all endnotes on this page. "
    "Use markdown footnote format: [^1]: Author, Title, p. 42."
)
EXTRACT_FOOTNOTES = (
    "Transcribe this page to markdown. "
    "Use [^1]: format for footnotes at the bottom."
)
EXTRACT_BIBLIOGRAPHY = "Transcribe all bibliography entries on this page."
EXTRACT_INDEX = "Transcribe all index entries on this page. Keep indentation."
EXTRACT_TOC = "Transcribe the table of contents on this page."
EXTRACT_ILLUSTRATION = "Transcribe the figure caption."
EXTRACT_EPIGRAPH = "Transcribe the epigraph and attribution."

# Extraction config: page_type → (user_prompt, max_tokens)
EXTRACT_CONFIG: dict[str, tuple[str, int]] = {
    "body_text":             (EXTRACT_BODY, 2048),
    "appendix":              (EXTRACT_BODY, 2048),
    "copyright":             (EXTRACT_BODY, 1024),
    "title_page":            (EXTRACT_BODY, 1024),
    "chapter_start":         (EXTRACT_CHAPTER_START, 2048),
    "endnotes":              (EXTRACT_ENDNOTES, 2048),
    "footnotes_heavy":       (EXTRACT_FOOTNOTES, 2048),
    "bibliography":          (EXTRACT_BIBLIOGRAPHY, 2048),
    "index":                 (EXTRACT_INDEX, 2048),
    "table_of_contents":     (EXTRACT_TOC, 2048),
    "illustration":          (EXTRACT_ILLUSTRATION, 512),
    "epigraph":              (EXTRACT_EPIGRAPH, 512),
    "body_two_column":       (EXTRACT_BODY, 2048),
    "body_parallel_columns": (EXTRACT_BODY, 2048),
    "mixed":                 (EXTRACT_BODY, 2048),
}

# ── Legacy (kept for backward compat with cached v9 results) ────────
SYSTEM_JSON = "Output JSON only."