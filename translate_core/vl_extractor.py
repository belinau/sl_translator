"""vl_extractor.py — entity extraction from TM segments via LFM2.5-VL.

Replaces the regex pipeline (segment_classifier + book_extractor) for the
hard cases: art catalogues, mixed-project corpora, ambiguous bibliography
formats, Slovenian/English citation variants.

Architecture:
  - Takes batches of (src, tgt) pairs from a single TMX origin
  - Sends them as text to VLMClient (same endpoint as vl_parser.py: localhost:8081)
  - Uses assistant prefill to force clean JSON from the 1.6B model
  - Returns records in the same shape write_to_kg() already expects
  - Falls back to the existing regex pipeline if server is unavailable

Usage:
    from translate_core.vl_extractor import VLExtractor

    extractor = VLExtractor()
    records = extractor.extract_from_origin(entries, origin="MyBook_2023.tmx")
    # records is the same List[dict] that write_to_kg() consumes
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import unicodedata
from typing import Optional

log = logging.getLogger("vl_extractor")

# ── Batch tuning ─────────────────────────────────────────────────────────────
# BATCH_SIZE = 1: one segment per VL call. Slower than batching, but the only
# way to get exact segment_idx attribution (with N>1 the model's output doesn't
# tell us which input segment produced each entity).
BATCH_SIZE = 1
EXTRACT_MAX_TOKENS = 900

# ── Role allowlist ────────────────────────────────────────────────────────────
# All other VL-suggested roles collapse to "agent" and can be reassigned in the
# review queue. KG add_agent_node stores role as a free string, so this is a
# pure naming convention with no schema impact.
KNOWN_ROLES = {
    "author", "translator", "editor", "curator", "artist",
    "interviewer", "interviewee", "choreographer", "director",
    "performer", "dancer", "composer", "dramaturg", "agent",
}


def _normalize_role(role) -> str:
    if not role:
        return "agent"
    if isinstance(role, list):
        role = role[0] if role else "agent"
    r = str(role).strip().lower()
    return r if r in KNOWN_ROLES else "agent"


# ── Rejected-segment diagnostic log ──────────────────────────────────────────
# Citation segments classified as BIBLIOGRAPHY_ENTRY but rejected by the
# verifier are written here so the next iteration can see exactly which
# segments are slipping through.
import json as _json_for_log

_REJECT_LOG_PATH = "data/extraction_rejected_for_review.jsonl"


def _log_rejected_segment(origin: str, seg_idx: int, segment_text: str, reason: str) -> None:
    """Append a rejected biblio segment to the diagnostic JSONL. Best-effort —
    swallow any I/O error to avoid breaking the pipeline."""
    try:
        record = {
            "origin": origin,
            "segment_idx": int(seg_idx),
            "reason": reason,
            "segment_text": (segment_text or "")[:400],
        }
        with open(_REJECT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(_json_for_log.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ── Prompts ───────────────────────────────────────────────────────────────────
# Ultra-short system prompt — same discipline as vl_prompts.py.
# EN side only: the 1.6B model has no Slovenian, EN text is always cleaner.

SYSTEM_EXTRACT = "You extract named entities from text. Output only JSON. No prose."

_USER_TEMPLATE = """\
Text segments:
{segments}

For each segment containing a named entity output one JSON object.
Kinds: person, institution.
  person      {{"kind":"person","name":"...","role":"author|translator|curator|other"}}
  institution {{"kind":"institution","name":"...","type":"publisher|gallery|museum|festival|other","city":"..."}}
Omit missing fields. Reply: {{"segments":[...]}}"""

# Prefill forces the model to start mid-JSON — no preamble.
PREFILL = '{"segments":['


# ── Citation extraction — bibliography vs footnote (distinct prompts) ────────
# Bibliography entries (end-biblio sections, alphabetic) and footnotes (full
# in-text citations, often multi-citation per footnote) have different
# structures and need different prompts. Both run through
# vl_citation_verifier.verify_citation, which drops any field that doesn't
# literally appear in the source segment text.

# --- Bibliography entry: lastname-first, year follows author with colon ------

SYSTEM_BIBLIO_ENTRY = (
    "Parse one bibliography entry. Output only JSON. No prose. "
    "Copy every field verbatim from the text — never paraphrase or invent."
)

_USER_BIBLIO_ENTRY_TEMPLATE = """\
Bibliography entry:
{segment}

This is from an alphabetic bibliography. Authors appear in lastname-first form ("Lastname, Firstname"). The year typically follows the author block, often with a colon. Use null when a field is absent.

Extract:
- authors: list of "Lastname, Firstname" strings, in the order they appear
- year: 4-digit year
- title: the work's title
- container: journal name or parent book the work appears in
- publisher
- city
- pages: page range like "117-139" or "p. 117"

Reply: {{"authors":[...],"year":NNNN,"title":"...","container":"...","publisher":"...","city":"...","pages":"..."}}"""

PREFILL_BIBLIO_ENTRY = '{"authors":'
BIBLIO_ENTRY_MAX_TOKENS = 400


# --- Footnote: firstname-last, year near end, possibly multi-citation --------

SYSTEM_FOOTNOTE = (
    "Parse a footnote. Output only JSON. No prose. "
    "Copy every field verbatim from the text — never paraphrase or invent."
)

_USER_FOOTNOTE_TEMPLATE = """\
Footnote text:
{segment}

This is a footnote. Authors are usually in firstname-last form ("Firstname Lastname"). The year typically appears toward the end. The text may begin with a prefix like "See", "Glej", "Cf.", "Prim.", or "Ibid." Multiple citations may be present, separated by semicolons — emit one object per citation.

Extract a list of citations. Each citation contains:
- authors: list of author names as they appear (keep original order)
- title: the work's title
- container: journal name or parent book the work appears in
- year: 4-digit year
- publisher
- city
- pages: page range like "p. 215" or "str. 9" or "40-45"

If the footnote is a back-reference (Ibid., op. cit.) with no full citation visible, return an empty list.

Reply: {{"citations":[{{"authors":[...],"title":"...","container":"...","year":NNNN,"publisher":"...","city":"...","pages":"..."}}]}}"""

PREFILL_FOOTNOTE = '{"citations":['
FOOTNOTE_MAX_TOKENS = 600


# --- Sub-classifier: distinguish biblio shape from footnote shape ------------
# segment_classifier labels both as BIBLIOGRAPHY_ENTRY when structural signals
# are present. The shapes differ in how author names open the segment.

# Real signal (per user): footnotes have a leading number marker.
# Page or URL at the tail is shared with bibliography entries (journal articles
# always have pages) so it's not a discriminator. Only the leading number is.

_FOOTNOTE_LEAD_NUMBER_RE = re.compile(r"^\s*\[?\d{1,3}\]?[\.\s)]")
_SHORT_REF_RE = re.compile(
    r"^\s*(?:Ibid\.?|Ibidem|op\.\s*cit\.|loc\.\s*cit\.|cit\.|prav tam|nav\.\s*delo)",
    re.IGNORECASE,
)


def _citation_subclass(segment_text: str) -> str:
    """Return 'biblio' / 'footnote' / 'short_ref'.

    footnote   = leading number marker (1., [1], 12., 1))
    short_ref  = leading Ibid./op. cit. (back-reference, skip)
    biblio     = default (alphabetic-list shape)
    """
    s = segment_text.strip()
    if not s:
        return "short_ref"
    if _SHORT_REF_RE.match(s):
        return "short_ref"
    if _FOOTNOTE_LEAD_NUMBER_RE.match(s):
        return "footnote"
    return "biblio"


# --- Parsers for each VL response shape --------------------------------------

def _parse_single_citation_response(raw: str, prefill: str) -> Optional[dict]:
    """Parse a single-citation JSON object (biblio entry shape)."""
    text = raw.strip()
    fence = chr(96) * 3
    text = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", text)
    text = re.sub(r"\s*" + re.escape(fence) + r"$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    if not text.startswith("{"):
        try:
            data = json.loads(prefill + text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return _clean_broken_json_object(text)


def _parse_footnote_response(raw: str) -> list[dict]:
    """Parse a {citations:[...]} response — may contain 0, 1, or many citations."""
    text = raw.strip()
    fence = chr(96) * 3
    text = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", text)
    text = re.sub(r"\s*" + re.escape(fence) + r"$", "", text)
    text = text.strip()

    candidate = text if text.startswith("{") else PREFILL_FOOTNOTE + text
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            cits = data.get("citations")
            if isinstance(cits, list):
                return [c for c in cits if isinstance(c, dict)]
    except json.JSONDecodeError:
        pass

    # Fallback: extract individual {...} objects from the raw text
    objs: list[dict] = []
    depth = 0
    in_string = False
    escape = False
    start_idx = -1
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start_idx >= 0:
                obj_text = text[start_idx:i + 1]
                try:
                    obj = json.loads(obj_text)
                    if isinstance(obj, dict) and "citations" not in obj:
                        objs.append(obj)
                except json.JSONDecodeError:
                    cleaned = _clean_broken_json_object(obj_text)
                    if cleaned and "citations" not in cleaned:
                        objs.append(cleaned)
                start_idx = -1
    return objs


# ── Bilingual second pass (SL title lookup) ───────────────────────────────────
SYSTEM_BILINGUAL = "You find Slovenian title forms. Output only JSON. No prose."

_BILINGUAL_TEMPLATE = """\
English: {src}
Slovenian: {tgt}

The English mentions a title: "{title_en}"
What exact Slovenian form is used for this title in the Slovenian text?
Reply: {{"sl":"<slovenian title>"}} or {{"sl":null}} if not present."""

BILINGUAL_PREFILL = '{"sl":'
BILINGUAL_MAX_TOKENS = 120


def _parse_bilingual(raw: str) -> Optional[dict]:
    text = raw.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r'"sl"\s*:\s*"([^"]*)"', text)
    if m:
        return {"sl": m.group(1)}
    if re.search(r'"sl"\s*:\s*null', text):
        return {"sl": None}
    return None


# ── Verification helpers (used by all generic builders) ──────────────────────
def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _name_in_segment(name: str, segment: str) -> bool:
    """Cheap check: a name VL claims to have found must literally appear in the
    segment it claims to have found it in. Diacritic-tolerant, case-insensitive.
    Used to reject pure VL fabrications before they become records.
    """
    if not name or not segment:
        return False
    n_norm = _strip_diacritics(name).lower().strip()
    s_norm = _strip_diacritics(segment).lower()
    if len(n_norm) < 2:
        return False
    # Match either the whole name or its strongest token (longest word ≥3 chars)
    if n_norm in s_norm:
        return True
    tokens = [t for t in re.split(r"\s+", n_norm) if len(t) >= 3]
    if not tokens:
        return False
    longest = max(tokens, key=len)
    return longest in s_norm


# ── Slugify ───────────────────────────────────────────────────────────────────
def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] if s else "unknown"


# ── Record builders — same shape as kg_ingest_entities.py ────────────────────

def _make_person_record(entity: dict, origin: str, seg_idx: int,
                        src: str, tgt: str) -> dict:
    name_raw = entity.get("name") or ""
    if isinstance(name_raw, list):
        name_raw = " ".join(str(x) for x in name_raw)
    name = str(name_raw).strip()
    if not name:
        return {}

    # Reject fabrications: VL must have seen this name in the segment text.
    if not _name_in_segment(name, src):
        return {}

    # Reject obviously-organisational strings ("Robert Rauschenberg Foundation"
    # tagged as PERSON, "Eastern Europe" tagged as PERSON, etc.).
    from .entity_extraction.name_dedup import (
        looks_like_organization, is_plausible_person_name, dedup_group_key,
    )
    if looks_like_organization(name):
        return {}

    # Reject names where ANY token is a city/publisher/country/org.
    # Catches VL mis-extractions like "čvorč Ljubljana" where the model
    # invented a Slavic-sounding given_name and used a city as surname —
    # the substring check passes (Ljubljana is in text) but the result is
    # clearly not a person.
    from .entity_extraction.vl_typed_verifier import _NOT_A_SURNAME
    tokens = [t.strip(",.;:()").lower() for t in re.split(r"\s+", name) if t]
    if any(t in _NOT_A_SURNAME for t in tokens if t):
        return {}

    role = _normalize_role(entity.get("role"))

    # Honest signals: only flag plausible_person_name when the name actually
    # passes name_dedup's gate (2–5 tokens, alpha, not all-caps, etc.).
    # ner_person_match stays False here — we did NOT run NER; only VL labelled
    # this as a person, and VL routinely mis-labels (country/journal/title).
    plausible = is_plausible_person_name(name)

    return {
        "kind": "agent_person",
        "payload": {
            "name": name,
            "role": role,
            "all_roles": [role],
            "dedup_group": dedup_group_key(name),
            "norm": _slugify(name),
            "origin": origin,
            "mention_count": 1,
            "alt_spellings": [name],
        },
        "signals": {
            "plausible_person_name": plausible,
            "multi_mention": False,
            "multi_origin": False,
            "ner_person_match": False,
            "role_attribution_context": role in KNOWN_ROLES,
        },
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "src_excerpt": src[:240],
            "tgt_excerpt": tgt[:240],
            "segment_class": "vl_extracted",
        },
    }



def _make_institution_record(entity: dict, origin: str, seg_idx: int,
                              src: str, tgt: str) -> dict:
    name_raw = entity.get("name") or ""
    if isinstance(name_raw, list):
        name_raw = " ".join(str(x) for x in name_raw)
    name = str(name_raw).strip()
    if not name:
        return {}
    if not _name_in_segment(name, src):
        return {}

    kind_raw = entity.get("type") or "other"
    if isinstance(kind_raw, list):
        kind_raw = kind_raw[0] if kind_raw else "other"
    kind = str(kind_raw).strip().lower()
    _ALLOWED_KINDS = {
        "publisher", "gallery", "museum", "university", "festival",
        "theatre", "journal", "organization", "sponsor", "country", "other",
    }
    if kind not in _ALLOWED_KINDS:
        kind = "other"

    city_raw = entity.get("city") or ""
    if isinstance(city_raw, list):
        city_raw = " ".join(str(x) for x in city_raw)
    city = str(city_raw).strip() or None

    return {
        "kind": "institution",
        "payload": {
            "name": name,
            "kind": kind,
            "city": city,
        },
        "signals": {
            "multi_mention": False,
        },
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "src_excerpt": src[:240],
            "tgt_excerpt": tgt[:240],
            "segment_class": "vl_extracted",
        },
    }



_BUILDERS = {
    "person": _make_person_record,
    "institution": _make_institution_record,
}
def _records_from_verified_citation(
    verified: dict,
    origin: str,
    seg_idx: int,
    src: str,
    tgt: str,
    *,
    source_class: str = "citation_verified",
) -> list[dict]:
    """Build cited_work + companion agent_person + optional institution
    records from a verifier-cleaned citation dict.

    `verified` is the output of vl_citation_verifier.verify_citation — every
    field has already been checked against `src`. We never fabricate fields
    or paraphrase; we just package the verified data into the standard record
    schema downstream expects.
    """
    if not verified or not verified.get("authors") or not verified.get("title"):
        return []

    authors: list[str] = list(verified["authors"])
    primary_author = authors[0]
    title = verified["title"]
    year = verified.get("year")
    publisher = verified.get("publisher")
    city = verified.get("city")
    container = verified.get("container")
    pages = verified.get("pages")

    work_id = _slugify(f"{primary_author}-{title}-{year or ''}")
    records: list[dict] = []

    cited = {
        "kind": "cited_work",
        "payload": {
            "cited_id": work_id,
            "project_type": "cited_work",
            "author": primary_author,
            "all_authors": authors,
            "title_en": title,
            "title_sl": None,
            "title_orig": title,
            "year": year,
            "container": container,
            "pages": pages,
            "original_pub": {"publisher": publisher, "city": city} if publisher else None,
            "slovenian_edition": None,
            "container_work_id": _slugify(origin.rsplit(".", 1)[0]),
        },
        "signals": {
            "has_author": True,
            "has_title": True,
            "has_year": year is not None,
            "has_publisher": publisher is not None,
            "has_publisher_city": city is not None,
            "in_biblio_cluster": True,
            "title_bilingual": False,
            "verified_from_text": True,
        },
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "src_excerpt": src[:240],
            "tgt_excerpt": tgt[:240],
            "segment_class": source_class,
        },
    }
    records.append(cited)

    for author in authors:
        pr = _make_person_record(
            {"name": author, "role": "author"},
            origin, seg_idx, src, tgt,
        )
        if pr:
            records.append(pr)

    if publisher:
        inst = _make_institution_record(
            {"name": publisher, "type": "publisher", "city": city},
            origin, seg_idx, src, tgt,
        )
        if inst:
            records.append(inst)

    return records


# ── JSON parsing ──────────────────────────────────────────────────────────────

def _clean_broken_json_object(candidate: str) -> Optional[dict]:
    """Applies robust regex-based syntax patching to commonly malformed LLM objects."""
    s = candidate.strip()

    # Fix missing opening quote on keys (e.g., ,city": -> ,"city":)
    s = re.sub(r'([,{])\s*(\w+)\s*"\s*:', r'\1"\2":', s)

    # Fix missing closing quote on keys (e.g., ,"city: -> ,"city":)
    s = re.sub(r'([,{])\s*"\s*(\w+)\s*:', r'\1"\2":', s)

    # Fix trailing commas before closing braces
    s = re.sub(r',\s*\}', '}', s)

    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Ultimate Regex Fallback for key-value extraction
    try:
        extracted = {}
        for key in ["kind", "name", "role", "title_en", "author", "agent", "year", "publisher", "city", "type", "artist", "medium"]:
            # Find string pattern
            val_match = re.search(r'"' + key + r'"\s*:\s*"([^"]*)"', candidate)
            if val_match:
                extracted[key] = val_match.group(1)
                continue
            # Find array pattern
            arr_match = re.search(r'"' + key + r'"\s*:\s*\[\s*"([^"]*)"\s*\]', candidate)
            if arr_match:
                extracted[key] = [arr_match.group(1)]
                continue
            # Find integer pattern
            int_match = re.search(r'"' + key + r'"\s*:\s*(\d+)', candidate)
            if int_match:
                extracted[key] = int(int_match.group(1))
                continue
        if "kind" in extracted:
            return extracted
    except Exception:
        pass

    return None


def _parse_response(raw: str) -> list[dict]:
    """Extract individual segment objects from the response.

    Even if the response is truncated or has malformed JSON structures,
    this character-by-character scanner will extract all balanced and
    semi-valid {...} objects individually.
    """
    text = raw.strip()

    # Strip markdown fences safely
    fence = chr(96) * 3
    text = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", text)
    text = re.sub(r"\s*" + re.escape(fence) + r"$", "", text)
    text = text.strip()

    # First attempt: complete JSON parse of the whole string
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "segments" in data:
            segs = data["segments"]
            if isinstance(segs, list):
                return [s for s in segs if isinstance(s, dict)]
    except json.JSONDecodeError:
        pass

    # Second attempt: maybe prefill prepended parses it completely
    try:
        candidate = PREFILL + text if not text.startswith("{") else text
        data = json.loads(candidate)
        if isinstance(data, dict) and "segments" in data:
            segs = data["segments"]
            if isinstance(segs, list):
                return [s for s in segs if isinstance(s, dict)]
    except json.JSONDecodeError:
        pass

    # Fallback: Find and parse individual JSON objects {...}
    # Robust single-pass stack-based brace locator to bypass outer wrapper truncation.
    objs = []
    n = len(text)
    in_string = False
    escape = False
    brace_stack = []

    i = 0
    while i < n:
        char = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if char == '\\':
            escape = True
            i += 1
            continue
        if char == '"':
            in_string = not in_string
            i += 1
            continue

        if not in_string:
            if char == '{':
                brace_stack.append(i)
            elif char == '}':
                if brace_stack:
                    start_idx = brace_stack.pop()
                    if not brace_stack:  # Outermost brace candidate
                        candidate_obj = text[start_idx : i + 1]
                        try:
                            obj = json.loads(candidate_obj)
                            if isinstance(obj, dict) and "segments" not in obj:
                                objs.append(obj)
                        except json.JSONDecodeError:
                            cleaned = _clean_broken_json_object(candidate_obj)
                            if cleaned and "segments" not in cleaned:
                                objs.append(cleaned)
        i += 1

    # Recovery: parse nested unclosed blocks if outermost scan returned nothing
    if not objs and brace_stack:
        for start_idx in sorted(brace_stack, reverse=True):
            subtext = text[start_idx:]
            sub_depth = 0
            sub_in_string = False
            sub_escape = False
            for k, c in enumerate(subtext):
                if sub_escape:
                    sub_escape = False
                    continue
                if c == '\\':
                    sub_escape = True
                    continue
                if c == '"':
                    sub_in_string = not sub_in_string
                    continue
                if not sub_in_string:
                    if c == '{':
                        sub_depth += 1
                    elif c == '}':
                        sub_depth -= 1
                        if sub_depth == 0:
                            candidate_obj = subtext[:k+1]
                            try:
                                obj = json.loads(candidate_obj)
                                if isinstance(obj, dict) and "segments" not in obj:
                                    objs.append(obj)
                            except json.JSONDecodeError:
                                cleaned = _clean_broken_json_object(candidate_obj)
                                if cleaned and "segments" not in cleaned:
                                    objs.append(cleaned)
                            break

    # Last resort: fallback regex matching for flat {} structures
    if not objs:
        for match in re.finditer(r'\{[^{}]*\}', text):
            candidate_obj = match.group(0)
            try:
                obj = json.loads(candidate_obj)
                if isinstance(obj, dict) and "segments" not in obj:
                    objs.append(obj)
            except json.JSONDecodeError:
                cleaned = _clean_broken_json_object(candidate_obj)
                if cleaned and "segments" not in cleaned:
                    objs.append(cleaned)

    valid_objs = [o for o in objs if isinstance(o, dict) and o.get("kind")]
    return valid_objs


# ── Core extractor ────────────────────────────────────────────────────────────

class VLExtractor:
    """Entity extractor using LFM2.5-VL via the VLMClient HTTP endpoint.

    Uses a text-only request (no image) — the model server handles both.
    Falls back to returning [] if the server is unavailable.

    auto_start_server (default True): if the server is not already running,
    VLMServerManager is used to start it automatically. Pass False to
    suppress this (e.g. if you are managing the server externally).
    Call close() when done to stop the managed server subprocess.
    """

    def __init__(
        self,
        server_url: str | None = None,
        model: str | None = None,
        batch_size: int = BATCH_SIZE,
        fallback_on_error: bool = True,
        auto_start_server: bool = True,
    ):
        try:
            from translate_core.vl_parser import DEFAULT_VL_SERVER_URL, DEFAULT_VL_MODEL
        except ImportError:
            DEFAULT_VL_SERVER_URL = "http://localhost:8081/v1"
            DEFAULT_VL_MODEL = "MuXodious/LFM2.5-VL-1.6B-absolute-heresy-mlx-4Bit"

        import httpx

        self._url = (server_url or DEFAULT_VL_SERVER_URL).rstrip("/")
        self._model = model or DEFAULT_VL_MODEL
        self._http = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0))
        self.batch_size = batch_size
        self.fallback_on_error = fallback_on_error
        self._available: bool | None = None
        self._server_manager = None

        if auto_start_server and not self._tcp_ping():
            try:
                from translate_core.vl_server import VLMServerManager
                self._server_manager = VLMServerManager(model=self._model)
                started = self._server_manager.start(timeout=180.0)
                if started:
                    self._available = True
                else:
                    log.warning(
                        "vl_extractor: auto-start failed — "
                        "will fall back to regex pipeline"
                    )
                    self._server_manager = None
            except Exception as e:
                log.warning("vl_extractor: could not import or start VLMServerManager: %s", e)
                self._server_manager = None

    def close(self):
        """Stop the VLM server subprocess if we started it."""
        if self._server_manager is not None:
            self._server_manager.stop()
            self._server_manager = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _tcp_ping(self) -> bool:
        import socket
        from urllib.parse import urlparse
        try:
            parsed = urlparse(self._url)
            host = parsed.hostname or "127.0.0.1"
            if host == "localhost":
                host = "127.0.0.1"
            port = parsed.port or 8081
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except Exception:
            # Fallback to resolving 'localhost' directly
            try:
                parsed = urlparse(self._url)
                host = parsed.hostname or "localhost"
                port = parsed.port or 8081
                with socket.create_connection((host, port), timeout=2.0):
                    return True
            except Exception:
                return False

    def _check_available(self) -> bool:
        if self._available is None:
            if self._tcp_ping():
                self._available = True
            else:
                self._available = False
                print(f"vl_extractor: server NOT reachable at {self._url}. Falling back to regex pipeline.", file=sys.stderr)
                log.warning(
                    "vl_extractor: server not reachable at %s — "
                    "falling back to regex pipeline", self._url
                )
        return self._available

    def _call_batch(
        self,
        pairs: list[tuple[int, str, str]],  # (original_idx, src, tgt)
        origin: str,
    ) -> list[dict]:
        # EN side only — the 1.6B model has no Slovenian
        seg_lines = "\n".join(
            f"{i+1}. {src[:180]}"
            for i, (_, src, _) in enumerate(pairs)
        )
        user_prompt = _USER_TEMPLATE.format(segments=seg_lines)

        # Text-only messages — no image_url block
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system",    "content": SYSTEM_EXTRACT},
                {"role": "user",      "content": user_prompt},
                {"role": "assistant", "content": PREFILL},
            ],
            "temperature": 0.1,
            "top_p": 0.95,
            "top_k": 64,
            "min_p": 0.15,
            "repetition_penalty": 1.05,
            "max_tokens": EXTRACT_MAX_TOKENS,
            "stream": False,
        }

        raw = ""
        last_err = None
        for attempt in range(3):
            try:
                r = self._http.post(
                    f"{self._url}/chat/completions",
                    json=payload
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                # Ensure prefill is present
                if not raw.startswith(PREFILL[:5]):
                    raw = PREFILL + raw
                break
            except Exception as e:
                last_err = e
                log.warning(
                    "vl_extractor: batch attempt %d failed: %s", attempt + 1, e
                )
                time.sleep(1.5 * (attempt + 1))
        else:
            log.warning("vl_extractor: giving up on batch: %s", last_err)
            return []

        entities = _parse_response(raw)
        records: list[dict] = []

        for entity in entities:
            if not isinstance(entity, dict):
                continue
            kind = entity.get("kind", "")
            builder = _BUILDERS.get(kind)
            if not builder:
                continue

            seg_idx, src, tgt = pairs[0]
            rec = builder(entity, origin, seg_idx, src, tgt)
            if not rec:
                continue
            records.append(rec)

            # Report finding to stderr immediately in a clean format
            name_val = entity.get("name") or entity.get("title_en") or "unknown"
            if isinstance(name_val, list):
                name_val = ", ".join(str(x) for x in name_val)
            extra_val = entity.get("author") or entity.get("artist") or entity.get("role") or entity.get("type") or ""
            if isinstance(extra_val, list):
                extra_val = ", ".join(str(x) for x in extra_val)
            extra_str = f" ({extra_val})" if extra_val else ""
            print(f"  ↳ [{kind.upper()}] {name_val}{extra_str}", file=sys.stderr)

            # Generic batch path only emits person + institution (Phase 0: O-16).
            # Work/artwork extraction is handled by the typed citation pipeline.

        return records

    def _vl_chat(
        self,
        *,
        system: str,
        user: str,
        prefill: str,
        max_tokens: int,
    ) -> Optional[str]:
        """One-shot VL chat call. Returns raw content or None on failure."""
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system",    "content": system},
                {"role": "user",      "content": user},
                {"role": "assistant", "content": prefill},
            ],
            "temperature": 0.1,
            "top_p": 0.95,
            "top_k": 64,
            "min_p": 0.15,
            "repetition_penalty": 1.05,
            "max_tokens": max_tokens,
            "stream": False,
        }
        for attempt in range(2):
            try:
                r = self._http.post(
                    f"{self._url}/chat/completions",
                    json=payload,
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                if not raw.startswith(prefill[:5]):
                    raw = prefill + raw
                return raw
            except Exception as e:
                log.warning(
                    "vl_extractor: chat attempt %d failed: %s", attempt + 1, e
                )
                time.sleep(1.0)
        return None

    def extract_biblio_entry_from_segment(
        self,
        src: str,
        tgt: str,
        origin: str,
        seg_idx: int,
    ) -> list[dict]:
        """Specialised extraction for BIBLIOGRAPHY_ENTRY segments.

        VL output is verified against the segment text — fields that don't
        appear in the text are dropped; records with no verified author OR
        no verified title are rejected entirely.
        """
        if not self._check_available() or not src:
            return []
        from .entity_extraction.vl_citation_verifier import verify_citation

        user_prompt = _USER_BIBLIO_ENTRY_TEMPLATE.format(segment=src[:600])
        raw = self._vl_chat(
            system=SYSTEM_BIBLIO_ENTRY,
            user=user_prompt,
            prefill=PREFILL_BIBLIO_ENTRY,
            max_tokens=BIBLIO_ENTRY_MAX_TOKENS,
        )
        if not raw:
            return []
        extracted = _parse_single_citation_response(raw, PREFILL_BIBLIO_ENTRY)
        if not extracted:
            return []
        verified = verify_citation(extracted, src)
        if not verified:
            return []
        return _records_from_verified_citation(
            verified, origin, seg_idx, src, tgt, source_class="biblio_entry"
        )

    def extract_footnote_from_segment(
        self,
        src: str,
        tgt: str,
        origin: str,
        seg_idx: int,
    ) -> list[dict]:
        """Specialised extraction for full footnote segments (possibly
        multi-citation, separated by ';'). Each citation is verified
        independently against the segment text.
        """
        if not self._check_available() or not src:
            return []
        from .entity_extraction.vl_citation_verifier import verify_citation

        user_prompt = _USER_FOOTNOTE_TEMPLATE.format(segment=src[:800])
        raw = self._vl_chat(
            system=SYSTEM_FOOTNOTE,
            user=user_prompt,
            prefill=PREFILL_FOOTNOTE,
            max_tokens=FOOTNOTE_MAX_TOKENS,
        )
        if not raw:
            return []
        citations = _parse_footnote_response(raw)
        if not citations:
            return []
        records: list[dict] = []
        for cit in citations:
            verified = verify_citation(cit, src)
            if not verified:
                continue
            records.extend(_records_from_verified_citation(
                verified, origin, seg_idx, src, tgt, source_class="footnote"
            ))
        return records

    def enrich_title_sl(
        self,
        title_en: str,
        src: str,
        tgt: str,
    ) -> Optional[str]:
        """Single-segment lookup: given an EN title and the EN+SL TM pair where
        it was mentioned, return the SL form of the title (or None if absent).
        """
        if not self._check_available():
            return None
        if not title_en or not tgt:
            return None

        user_prompt = _BILINGUAL_TEMPLATE.format(
            src=src[:400],
            tgt=tgt[:400],
            title_en=title_en[:200],
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system",    "content": SYSTEM_BILINGUAL},
                {"role": "user",      "content": user_prompt},
                {"role": "assistant", "content": BILINGUAL_PREFILL},
            ],
            "temperature": 0.1,
            "top_p": 0.95,
            "max_tokens": BILINGUAL_MAX_TOKENS,
            "stream": False,
        }

        for attempt in range(2):
            try:
                r = self._http.post(
                    f"{self._url}/chat/completions",
                    json=payload,
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                if not raw.startswith(BILINGUAL_PREFILL[:5]):
                    raw = BILINGUAL_PREFILL + raw
                data = _parse_bilingual(raw)
                if data is None:
                    continue
                sl = data.get("sl")
                if not sl or not isinstance(sl, str):
                    return None
                sl_clean = sl.strip().strip('"\'').strip()
                if not sl_clean or sl_clean.lower() == "null":
                    return None
                return sl_clean
            except Exception as e:
                log.warning(
                    "vl_extractor: enrich_title_sl attempt %d failed: %s",
                    attempt + 1, e,
                )
                time.sleep(1.0)
        return None

    def extract_from_origin(
        self,
        entries: list[dict],
        origin: str,
        classes_by_idx: Optional[dict[int, str]] = None,
    ) -> list[dict]:
        """Extract KG entities from all TM entries of one TMX origin.

        When `classes_by_idx` is supplied (per-segment SegmentClass values),
        BIBLIOGRAPHY_ENTRY segments are routed to one of two specialised VL
        prompts (biblio-shape vs footnote-shape) with output verification;
        NOISE segments are skipped; everything else uses the generic prompt.

        Without classes_by_idx, every segment uses the generic prompt
        (legacy behaviour, retained as a safety fallback).
        """
        if not self._check_available():
            if self.fallback_on_error:
                return []
            raise RuntimeError("VL server not available")

        pairs: list[tuple[int, str, str]] = []
        for i, e in enumerate(entries):
            src = (e.get("source") or "").strip()
            tgt = (e.get("target") or "").strip()
            if src:
                pairs.append((i, src, tgt))

        all_records: list[dict] = []
        total = len(pairs)
        route_counts = {
            "generic": 0, "typed_citation": 0, "spilled": 0,
            "short_ref_skipped": 0, "skip_noise": 0, "rejected_by_verifier": 0,
        }

        print(
            f"vl_extractor [{origin}]: starting extraction for {total} segments "
            f"in batches of {self.batch_size} "
            f"(class-aware routing: {'on' if classes_by_idx else 'off'})",
            file=sys.stderr,
        )

        if classes_by_idx is None:
            # Legacy path — generic prompt only, batched
            for batch_start in range(0, total, self.batch_size):
                batch = pairs[batch_start: batch_start + self.batch_size]
                records = self._call_batch(batch, origin)
                all_records.extend(records)
                route_counts["generic"] += len(batch)
        else:
            # Class-aware per-segment routing with forward-spill merging and
            # multi-citation splitting on `;`.
            from .entity_extraction.multi_segment_merge import (
                should_merge_forward, merge_forward, split_multi_citation,
            )
            from .entity_extraction.vl_typed_extractor import (
                extract_typed_citation, records_from_verified,
            )

            spill_consumed: set[int] = set()
            route_counts["multi_citation_parts"] = 0

            for pos, (seg_idx, src, tgt) in enumerate(pairs):
                if seg_idx in spill_consumed:
                    continue
                klass = classes_by_idx.get(seg_idx, "")
                if klass == "noise":
                    route_counts["skip_noise"] += 1
                    continue
                if klass == "bibliography_entry":
                    merged_src = src
                    merged_tgt = tgt
                    next_pair_idx = pos + 1
                    if next_pair_idx < len(pairs):
                        next_seg_idx, next_src, next_tgt = pairs[next_pair_idx]
                        if should_merge_forward(src, klass, next_src):
                            merged_src = merge_forward(src, next_src)
                            if tgt and next_tgt:
                                merged_tgt = merge_forward(tgt, next_tgt)
                            spill_consumed.add(next_seg_idx)
                            route_counts["spilled"] += 1

                    # Split on `;` if this is a multi-citation footnote
                    # ("X, Title1, year; Y, Title2, year"). Each fragment
                    # gets its own classify+extract pipeline.
                    parts = split_multi_citation(merged_src)
                    if len(parts) > 1:
                        route_counts["multi_citation_parts"] += len(parts) - 1
                    seg_typed_count = 0
                    seg_rejected_count = 0
                    for part_src in parts:
                        verified = extract_typed_citation(self, part_src)
                        if not verified:
                            seg_rejected_count += 1
                            _log_rejected_segment(origin, seg_idx, part_src, "verifier_null")
                            continue
                        recs = records_from_verified(
                            verified, origin, seg_idx, part_src, merged_tgt,
                        )
                        if not recs:
                            seg_rejected_count += 1
                            _log_rejected_segment(origin, seg_idx, part_src, "record_builder_empty")
                            continue
                        seg_typed_count += 1
                        all_records.extend(recs)
                        for r in recs:
                            if r["kind"] == "cited_work":
                                ptype = r["payload"].get("project_type", "cited_work")
                                print(
                                    f"  ↳ [{ptype.upper()}] {(r['payload'].get('author') or '?')[:30]} — "
                                    f"\"{(r['payload'].get('title_en') or '')[:60]}\"",
                                    file=sys.stderr,
                                )

                    if seg_typed_count > 0:
                        route_counts["typed_citation"] += 1
                    if seg_rejected_count > 0 and seg_typed_count == 0:
                        route_counts["rejected_by_verifier"] += 1
                    continue

                if klass == "footnote":
                    # segment_classifier reserves FOOTNOTE for short refs
                    # (ibid./op.cit./pages-only). Skip — back-resolution is a
                    # separate problem not in v1 scope.
                    route_counts["short_ref_skipped"] += 1
                    continue

                # All other classes (body, book_metadata, artwork, artist, etc.)
                # → generic entity prompt, one segment at a time.
                batch = [(seg_idx, src, tgt)]
                recs = self._call_batch(batch, origin)
                all_records.extend(recs)
                route_counts["generic"] += 1

        print(
            f"vl_extractor [{origin}]: completed with {len(all_records)} raw records "
            f"from {total} segments. Routing: {route_counts}",
            file=sys.stderr,
        )
        return all_records
