# config.py

import os
import pathlib

BASE_DIR = pathlib.Path(__file__).parent

LANG_PAIRS = [
    {"source": "en", "target": "sl"},
    {"source": "sl", "target": "en"},
]

DEFAULT_SOURCE_LANG = "en"
DEFAULT_TARGET_LANG = "sl"

TM_DIR = BASE_DIR / "data" / "tm"
GLOSSARY_DIR = BASE_DIR / "data" / "glossary"
KG_DB_PATH = BASE_DIR / "data" / "knowledge.db"

# Live smol entity extraction (editor confirm → Ollama → KG).
# When the endpoint is unreachable, confirmed segments are simply left for
# the offline batch pipeline (working.tmx → run_entity_extraction.py).
SMOL_MODEL = os.environ.get("SMOL_MODEL", "deepseek-v4-flash:cloud")
SMOL_LIVE_EXTRACTION = True
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
STORAGE_SECRET = os.environ.get("STORAGE_SECRET", "zen-translator-local-storage")
MAX_UPLOAD_SIZE_MB = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "50"))
SEGMENT_MAX_CHARS = 700  # Max chars per translation segment; split at sentence boundary.


# ---------------------------------------------------------------------------
# Maska house typography — the publisher's prescribed target-DOCX style.
# Source: Maska "Navodila pri citiranju" (pregledano julija 2018).
# Applied by DocumentParser.compile_to_designed_docx when a paragraph
# manifest is present; legacy projects fall back to the prior defaults.
# ---------------------------------------------------------------------------
MASKA_TYPOGRAPHY = {
    # SLOG BESEDILA — Times New Roman, 12pt, 1.5 line spacing
    "body_font": "Times New Roman",
    "body_size_pt": 12,
    "line_spacing": 1.5,
    # FORMAT STRANI — A4, 1-inch margins all sides
    "page_size": "A4",
    "margins_in": 1.0,
    # SLOG ODSTAVKA — new line, no blank line, no tab, no space between paragraphs
    "space_after_pt": 0,
    "para_first_line_indent_in": 0.0,
    # SLOG OPOMB — footnotes 10pt, 1.0 line spacing
    "footnote_size_pt": 10,
    "footnote_line_spacing": 1.0,
    # SLOG SAMOSTOJNEGA CITATA — block quote 11pt, 1.0 line, right indent
    "blockquote_size_pt": 11,
    "blockquote_line_spacing": 1.0,
    "blockquote_indent_in": 0.5,  # "zamaknemo v desno" — measurement not in spec
    # Headings — not specified by Maska citation spec; placeholders until
    # the book's real heading sizes are confirmed.
    "h1_size_pt": 18,
    "h2_size_pt": 13,
    # NAREKOVAJI — srednji narekovaji, enojni zgornji for nested
    "quote_primary": "\u201c\u201d",    # " "
    "quote_secondary": "\u2018\u2019",  # ' '
}

# The default publisher style key. Projects without an explicit house_style
# use this. The matching profile is seeded into data/publisher_styles.json
# on first run by translate_core.publisher_styles.load_styles().
DEFAULT_HOUSE_STYLE = "maska"
