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


INVOICE_TEMPLATE_DIR = BASE_DIR / "data" / "invoice_templates"
CLIENT_DB_PATH = BASE_DIR / "data" / "clients.db"
# Invoice export/archive folder — override with INVOICE_OUTPUT_DIR env var
# (absolute path; this is the user's long-term invoicing archive).
INVOICE_OUTPUT_DIR = pathlib.Path(
    os.environ.get("INVOICE_OUTPUT_DIR", str(BASE_DIR / "data" / "invoices"))
).expanduser()
ESLOG_XSD_PATH = INVOICE_TEMPLATE_DIR / "eSLOG20_INVOIC_v200.xsd"

# Issuer (Urban Belina) — appears on every invoice.
ISSUER = {
    "name": "URBAN BELINA – SAMOZAPOSLEN V KULTURI",
    "profession": (
        "PREVAJALEC IZ TUJEGA JEZIKA V SLOVENSKI JEZIK IN "
        "IZ SLOVENSKEGA JEZIKA V TUJ JEZIK,\n"
        "INTERMEDIJSKI UMETNIK, REŽISER, SCENOGRAF IN KNJIŽEVNIK"
    ),
    "address": "Rašiška ulica 1",
    "postal": "1000",
    "city": "Ljubljana",
    "country": "EU-Slovenija",
    "country_code": "SI",
    "vat_id": "77397975",
    "vat_obliged": False,  # normirani stroški — NOT in VAT register, no SI prefix
    "iban": "SI56 6100 0000 7436 658",
    "iban_compact": "SI56610000007436658",
    "bic": "HDELSI22",
    "bank_name": "Delavska hranilnica, d.d.",
    "bank_name_xml": "DELAVSKA HRANILNICA D.D. LJUBLJANA",
    "email": "urban@bel.si",
    "registration": "5274",
    "maticna": "2487870000",
}

# Legal notes — plain invoice + e-račun visualization.
INVOICE_LEGAL_NOTES = [
    "Sem zavezanec za plačilo davka od dohodka po 3. odstavku 48. člena ZDoh-2.",
    "DDV ni obračunan v skladu s 1. odstavkom 94. člena ZDDV-1. "
    "Številka vpisa v razvid samozaposlenih v kulturi: 5274.",
    "Poslujem brez žiga.",
]

# Footer text — appears on all generated documents.
GENERATED_BY = "Generirano z Bel Translation Suite"

# e-SLOG free-text clauses.
ESLOG_AGM_TEXT = (
    "Sem zavezanec za plačilo davka od dohodka po 3. odstavku 48. člena ZDoh-2. "
    "DDV ni obračunan v skladu s 1. odstavkom 94. člena ZDDV-1."
)
ESLOG_REG_TEXT = (
    "Št. vpisa v razvid samozaposlenih v kulturi: 5274 "
    "Matična št.: 2487870000"
)

# Service type dropdown (A19 in XLSX template).
INVOICE_SERVICE_TYPES = [
    "Prevod",
    "Prevod urejanje",
    "Prevod urejanje korektur",
    "Lektura",
    "Scenografija",
    "Video",
    "Scenarij",
    "Besedilo",
    "Prenos avtorskih pravic",
    "Sodelovanje pri",
]

# Target languages for rate differentiation.
# The second part of a lang_pair (e.g. "ENG>SLO" → "SLO") determines the rate.
INVOICE_TARGET_LANGS = ["SLO", "ENG", "HRV", "SRB", "DE", "FR", "IT"]

# Unit dropdown (B21 in XLSX template).
# "avtorska pola" = 16 strani × 1800 znakov s presledki = 28.800 znakov.
INVOICE_UNITS = [
    "pavšal", "ura", "stran", "beseda", "projekt", "kos", "verz", "znak",
    "avtorska pola",
]

# Rate key: "{service_type}:{target_lang}:{unit}".
# e.g. "Prevod:SLO:stran" = 22 EUR/page for translation into Slovenian.
# Helper to build/lookup rate keys.
def rate_key(service_type: str, target_lang: str, unit: str) -> str:
    """Build a rate lookup key: service_type:target_lang:unit."""
    return f"{service_type}:{target_lang}:{unit}"

def parse_lang_pair(pair: str) -> str:
    """Extract the target language from a lang_pair like 'ENG>SLO' → 'SLO'."""
    if ">" in pair:
        tgt = pair.split(">")[1].strip()
        # Handle variants like "ENG>SLO (100 % ujemanje)" → "SLO"
        return tgt.split()[0] if tgt else "SLO"
    return "SLO"

# Project lang codes ('en->sl', 'sl->en', ...) → eSLOG target tokens.
_LANG_CODE_TO_TARGET = {
    "sl": "SLO", "slo": "SLO",
    "en": "ENG", "eng": "ENG",
    "hr": "HRV", "hrv": "HRV",
    "sr": "SRB", "srp": "SRB", "srb": "SRB",
    "de": "DE", "fr": "FR", "it": "IT",
}

def parse_target(pair: str) -> str:
    """Uppercase target-language token from any pair form
    ('en->sl' → 'SLO', 'ENG>SLO (100 % ujemanje)' → 'SLO')."""
    if not pair:
        return "SLO"
    tgt = pair.strip().replace("->", ">").split(">")[-1].strip()
    tgt = tgt.split()[0] if tgt else ""
    return _LANG_CODE_TO_TARGET.get(tgt.lower(), (tgt.upper() or "SLO"))

# Language pair dropdown (A20/D16 in XLSX template).
INVOICE_LANG_PAIRS = [
    "ENG>SLO",
    "ENG>SLO (100 % ujemanje)",
    "ENG>SLO (delno ujemanje)",
    "ENG>SLO (delno ujemanje s pon.)",
    "ENG>SLO (ponovitve)",
    "ENG>SLO (brez ujemanja)",
    "SLO>ENG",
    "SLO",
    "HRV>SLO",
    "SRB>SLO",
    "HRV>ENG",
]

# Avtorska pola: 16 strani × 1800 znakov s presledki = 28.800 znakov.
AUTHORIAL_SHEET_CHARS = 28800

# eSLOG unit-of-measure codes (D_6411 in QTY segment).
ESLOG_UNIT_CODES = {
    "stran": "ZP",
    "kos": "C62",
    "ura": "HUR",
    "beseda": "WRD",
    "projekt": "TST",
    "verz": "VER",
    "znak": "CHA",
    "pavšal": "LS",
    "avtorska pola": "AH",  # avtorska pola — custom code
}
