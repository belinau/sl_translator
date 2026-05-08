# config.py

import pathlib

BASE_DIR = pathlib.Path(__file__).parent

TRANSLATION_MODEL_TYPE = "mlx"

# Switch to Llama 3.1 (Much better at Slovenian syntax and instruction following)
MLX_MODEL_NAME = "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit"
NLLB_MODEL_NAME = "facebook/nllb-200-distilled-1.3B"

LANG_PAIRS = [
    {"source": "en", "target": "sl"},
    {"source": "sl", "target": "en"},
]

DEFAULT_SOURCE_LANG = "en"
DEFAULT_TARGET_LANG = "sl"

TM_DIR = BASE_DIR / "data" / "tm"
GLOSSARY_DIR = BASE_DIR / "data" / "glossary"
KG_DB_PATH = BASE_DIR / "data" / "knowledge.db"
