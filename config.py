# config.py

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
SMOL_MODEL = "deepseek-v4-flash:cloud"
SMOL_LIVE_EXTRACTION = True
OLLAMA_URL = "http://localhost:11434"
SEGMENT_MAX_CHARS = 700  # Max chars per translation segment; split at sentence boundary.
