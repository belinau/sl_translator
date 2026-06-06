"""Typed citation extraction via two VL calls (classify → typed extract).

Workflow per citation segment:

1. **Call 1 (classify):** ask VL for citation type only — small focused prompt.
2. Detect style from diagnostic markers (no VL call needed).
3. **Call 2 (typed extract):** prompt VL with the type-specific schema; the
   model only needs to fill in the right fields.
4. Verify VL output against the segment text via vl_typed_verifier.
5. Build standard KG records (cited_work + companion agent_person + optional
   institution) from the verified, typed payload.

Bilingual handling lives in bilingual_enrichment (match-by-pair).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .citation_types import (
    CitationStyle,
    CitationType,
    CLASSIFY_TEMPLATE,
    CLASSIFY_PREFILL,
    CLASSIFY_MAX_TOKENS,
    SYSTEM_CLASSIFY,
    detect_author_form,
    detect_style,
    is_short_reference,
    prompt_for_type,
    system_for_type,
)
from .vl_typed_verifier import verify_typed_citation

log = logging.getLogger("vl_typed_extractor")


# Multi-citation-prose refusal: a single segment that packs ≥2 italic
# titles AND ≥2 four-digit years (1500–2100) AND is >200 chars long is
# almost certainly a sentence-prose paragraph that names several works,
# not a single citation. Sending it to the typed pipeline produces the
# Grom→Sujets class of error (one record attributing N people to 1 work).
# Pair-fix with segment_classifier; here we refuse at the typed-extractor
# entry point so the VL client is never called.
_BOLD_RE = re.compile(r"\*\*[^*]*\*\*")
_ITALIC_STAR_RE = re.compile(r"\*[^*]+\*")
_ITALIC_UNDERSCORE_RE = re.compile(r"_[^_]+_")
_QUOTED_RE = re.compile(r"\"[^\"]+\"")
_YEAR_RE = re.compile(r"\b(?:1[5-9]\d{2}|20\d{2}|2100)\b")


def _is_multi_citation_prose(s: str) -> bool:
    """True iff the segment looks like packed multi-work prose, not one citation."""
    if len(s) <= 200:
        return False
    stripped = _BOLD_RE.sub("", s)
    italic_count = (
        len(_ITALIC_STAR_RE.findall(stripped))
        + len(_ITALIC_UNDERSCORE_RE.findall(s))
        + len(_QUOTED_RE.findall(s))
    )
    if italic_count < 2:
        return False
    year_count = len(_YEAR_RE.findall(s))
    return year_count >= 2

# ── Response parsing ─────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    fence = chr(96) * 3
    text = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", text)
    text = re.sub(r"\s*" + re.escape(fence) + r"$", "", text)
    return text.strip()


# Map common VL hallucinated type names back to the canonical enum.
# e.g. 1.6B sometimes emits "book_dissertation" instead of "thesis_dissertation".
_TYPE_ALIASES = {
    "thesis": "thesis_dissertation",
    "dissertation": "thesis_dissertation",
    "book_dissertation": "thesis_dissertation",
    "phd_thesis": "thesis_dissertation",
    "phd": "thesis_dissertation",
    "ma_thesis": "thesis_dissertation",
    "journal": "journal_article",
    "article": "journal_article",
    "magazine": "magazine_article",
    "newspaper": "newspaper_article",
    "web": "web_source",
    "website": "web_source",
    "webpage": "web_source",
    "online": "web_source",
    "catalog": "exhibition_catalog",
    "catalogue": "exhibition_catalog",
    "chapter": "book_chapter",
}


def _resolve_type(type_str) -> Optional[CitationType]:
    """Map a VL-emitted type string to a CitationType, with alias fallback."""
    if not isinstance(type_str, str):
        return None
    s = type_str.strip().lower()
    if not s:
        return None
    try:
        return CitationType(s)
    except ValueError:
        pass
    alias = _TYPE_ALIASES.get(s)
    if alias:
        try:
            return CitationType(alias)
        except ValueError:
            return None
    return None


def _parse_classify_response(raw: str) -> Optional[CitationType]:
    """Parse {"type":"book|..."} response — returns CitationType or None.
    Tolerates common VL alias names via _TYPE_ALIASES."""
    text = _strip_fences(raw.strip())
    candidate = text if text.startswith("{") else CLASSIFY_PREFILL + text
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return _resolve_type(data.get("type"))
    except json.JSONDecodeError:
        pass
    m = re.search(r'"type"\s*:\s*"([a-z_]+)"', text)
    if m:
        return _resolve_type(m.group(1))
    return None


def _parse_typed_response(raw: str, prefill: str) -> Optional[dict]:
    """Parse the typed-extraction JSON object.

    Tail-truncation recovery: 1.6B occasionally emits the object without the
    closing `}` (or with a dangling comma). Before giving up, try patching
    common tail shapes — `,]` → `]`, trailing `,` removed, missing `}` added.
    """
    text = _strip_fences(raw.strip())
    candidate = text if text.startswith("{") else prefill + text

    # Try strict parse first
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Tail-truncation patches: strip dangling commas, append missing `}`
    patched = candidate.rstrip().rstrip(",")
    # Balance brackets/braces
    n_open_obj = patched.count("{") - patched.count("}")
    n_open_arr = patched.count("[") - patched.count("]")
    if n_open_arr > 0:
        patched += "]" * n_open_arr
    if n_open_obj > 0:
        patched += "}" * n_open_obj
    if patched != candidate:
        try:
            data = json.loads(patched)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Fallback: import the broken-object cleaner from vl_extractor
    try:
        from translate_core.vl_extractor import _clean_broken_json_object
        return _clean_broken_json_object(text)
    except ImportError:
        return None


# ── Public API ───────────────────────────────────────────────────────────────

def extract_typed_citation(
    extractor,
    segment_text: str,
) -> Optional[dict]:
    """Spec §6 two-call pipeline: VL Call 1 classifies type, VL Call 2 extracts
    typed fields, verifier validates against the segment text.

    Style is detected by regex markers (spec §5). Form (footnote vs
    bibliography) is detected by regex on the author-block opener; the
    detected form drives which author-order instruction is baked into the
    Call 2 prompt.

    Up to 2 attempts on Call 2 when the first response is unparseable or
    fails verification — the 1.6B model occasionally emits malformed JSON;
    a second attempt at temperature 0.1 usually produces something
    parseable. Records that fail both return None.
    """
    s = (segment_text or "").strip()
    if not s:
        return None

    # Short-reference short-circuit (no VL call needed)
    if is_short_reference(s):
        return {
            "type": CitationType.SHORT_REFERENCE.value,
            "style": detect_style(s).value,
            "marker": _short_ref_marker(s),
            "raw_text": s,
        }

    # Multi-citation-prose refusal — see _is_multi_citation_prose for criteria.
    # Returning None BEFORE Call 1 prevents the VL client from being touched.
    if _is_multi_citation_prose(s):
        return None

    style = detect_style(s)
    form = detect_author_form(s, style)

    # ── VL Call 1 — classify type (spec §6) ────────────────────────────────
    classify_user = CLASSIFY_TEMPLATE.format(segment=s[:600])
    classify_raw = extractor._vl_chat(
        system=SYSTEM_CLASSIFY,
        user=classify_user,
        prefill=CLASSIFY_PREFILL,
        max_tokens=CLASSIFY_MAX_TOKENS,
    )
    citation_type = _parse_classify_response(classify_raw) if classify_raw else None
    if citation_type is None or citation_type == CitationType.SHORT_REFERENCE:
        citation_type = CitationType.OTHER

    # ── VL Call 2 — typed extraction with form-specific author order ──────
    user_prompt, prefill, max_tokens = prompt_for_type(
        citation_type, s, style, form,
    )
    system_msg = system_for_type(citation_type)

    last_parsed: Optional[dict] = None
    for _ in range(2):
        raw = extractor._vl_chat(
            system=system_msg,
            user=user_prompt,
            prefill=prefill,
            max_tokens=max_tokens,
        )
        if not raw:
            continue
        extracted = _parse_typed_response(raw, prefill)
        if not extracted:
            continue
        last_parsed = extracted
        verified = verify_typed_citation(
            citation_type, extracted, s, style,
        )
        if verified:
            return verified

    # Verifier rejected both attempts but VL gave us a parseable response.
    # Preserve the unverified extraction as an `other` record so the curator
    # can review it in extraction_review.json (rather than losing it).
    if last_parsed:
        return _fallback_other_record(last_parsed, s, style)
    return None


def _fallback_other_record(parsed: dict, segment_text: str, style) -> Optional[dict]:
    """Build a minimal `other`-typed record from VL output that failed
    verification — keeps the data accessible for curator review.

    No substring verification is applied: the curator will decide whether
    the VL extraction is correct. Only basic shape is enforced.
    """
    if not isinstance(parsed, dict):
        return None
    out: dict = {
        "type": CitationType.OTHER.value,
        "style": style.value if hasattr(style, "value") else str(style),
        "raw_text": segment_text[:400],
        "notes": "verifier rejected typed extraction; preserved unverified for curator review",
    }
    # Best-effort field passthrough — only when the values are non-empty strings
    # or non-empty lists.
    authors = parsed.get("authors") or parsed.get("editors") or []
    if isinstance(authors, dict):
        authors = [authors]
    if isinstance(authors, list):
        clean_authors = []
        for a in authors:
            if isinstance(a, dict) and a.get("surname"):
                clean_authors.append({
                    "surname": str(a["surname"]).strip(),
                    "given_name": str(a.get("given_name") or "").strip(),
                })
        if clean_authors:
            out["authors"] = clean_authors

    title = (parsed.get("title") or parsed.get("article_title")
             or parsed.get("chapter_title") or parsed.get("title_or_program"))
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()

    interviewee = parsed.get("interviewee")
    if isinstance(interviewee, dict) and interviewee.get("surname"):
        out["interviewee"] = {
            "surname": str(interviewee["surname"]).strip(),
            "given_name": str(interviewee.get("given_name") or "").strip(),
        }

    # If we got literally nothing useful, give up
    if not (out.get("authors") or out.get("title") or out.get("interviewee")):
        return None
    return out


def _short_ref_marker(s: str) -> str:
    s_lower = s.lower()
    if "ibid" in s_lower or "prav tam" in s_lower:
        return "ibid"
    if "op. cit" in s_lower or "op.cit" in s_lower or "nav. delo" in s_lower:
        return "op_cit"
    if re.match(r"^\s*(?:pp?\.|str\.)\s*\d+", s_lower):
        return "page_only"
    return "short_title"


# ── Record building (one verified citation → multiple KG records) ────────────

def records_from_verified(
    verified: dict,
    origin: str,
    seg_idx: int,
    src: str,
    tgt: str,
    *,
    sl_verified: Optional[dict] = None,
) -> list[dict]:
    """Build cited_work + companion agent_person + institution records from
    a verified typed citation. If `sl_verified` is provided, merge its
    title and field translations into the EN-side record (bilingual).

    Returns a flat list of records in the existing KG-ingest record shape:
        {"kind": "cited_work" | "agent_person" | "institution",
         "payload": {...},
         "signals": {...},
         "source": {"origin", "segment_idx", "src_excerpt", "tgt_excerpt"}}
    """
    if not verified:
        return []
    citation_type = verified.get("type", CitationType.OTHER.value)
    style = verified.get("style") or CitationStyle.UNKNOWN.value

    if citation_type == CitationType.SHORT_REFERENCE.value:
        # Short references aren't cited_works on their own — emit a
        # minimal "review" record so the curator can resolve them later.
        return [{
            "kind": "short_reference",
            "payload": {
                "marker": verified.get("marker"),
                "raw_text": verified.get("raw_text"),
                "style": style,
            },
            "signals": {},
            "source": _make_source(origin, seg_idx, src, tgt, "short_reference"),
        }]

    if citation_type == CitationType.OTHER.value:
        # Verifier rejected typed extraction. §4 invariant 9 forbids
        # dropping the record on the floor — route it through the review
        # queue by emitting a cited_work with sparse signals so the
        # scorer tiers it to REVIEW (no verified_from_text bump).
        authors = verified.get("authors") or []
        title = (
            verified.get("title")
            or verified.get("article_title")
            or verified.get("chapter_title")
        )
        work_id_seed = "-".join([
            _surname_str(authors[0]) if authors else "",
            title or "",
        ])
        payload: dict = {
            "cited_id": _slugify(work_id_seed),
            "project_type": CitationType.OTHER.value,
            "title_en": title,
            "raw_text": verified.get("raw_text"),
            "notes": verified.get("notes"),
            "container_work_id": _slugify(origin.rsplit(".", 1)[0]),
        }
        if style and style != CitationStyle.UNKNOWN.value:
            payload["citation_style"] = style
        if authors:
            payload["author"] = _format_author(authors[0])
            payload["all_authors"] = [_format_author(a) for a in authors]
        signals = {
            "has_author": bool(authors),
            "has_title": bool(title),
            "in_biblio_cluster": True,
        }
        return [{
            "kind": "cited_work",
            "payload": payload,
            "signals": signals,
            "source": _make_source(
                origin, seg_idx, src, tgt, CitationType.OTHER.value,
            ),
        }]

    # Pick primary fields by type ---------------------------------------------
    authors = verified.get("authors") or []
    title_en = _pick_title(verified, citation_type)
    title_sl = _pick_title(sl_verified, citation_type) if sl_verified else None
    year = verified.get("year")
    publisher = verified.get("publisher")
    city = verified.get("city")
    pages = verified.get("pages") or verified.get("page")

    # Make the primary cited_work record ------------------------------------
    if not (authors or title_en):
        return []

    work_id_seed = "-".join([
        _surname_str(authors[0]) if authors else "",
        title_en or "",
        str(year) if year else "",
    ])
    work_id = _slugify(work_id_seed)

    payload = {
        "cited_id": work_id,
        "project_type": citation_type,
        "title_en": title_en,
        "title_sl": title_sl,
        "year": year,
        "pages": pages,
        # Type-specific extra fields preserved on the payload so write_to_kg
        # can put them on the source_text node.
        "extra_fields": _type_specific_extras(verified, citation_type),
    }
    if style and style != CitationStyle.UNKNOWN.value:
        payload["citation_style"] = style

    # Primary author (for backward-compat with existing agent_person edge wiring)
    if authors:
        payload["author"] = _format_author(authors[0])
        payload["all_authors"] = [_format_author(a) for a in authors]

    # Publisher embedded as the existing schema expects
    if publisher:
        payload["original_pub"] = {"publisher": publisher, "city": city}

    # Slovenian-edition metadata when SL side extracted typed fields.
    # Per spec §9.1 the SL footnote often cites the SL edition, which has
    # different publisher/city/year than the original — keep both.
    if sl_verified:
        sl_pub = sl_verified.get("publisher")
        sl_city = sl_verified.get("city")
        sl_year = sl_verified.get("year")
        sl_translator = sl_verified.get("translator")
        if sl_pub or sl_city or (sl_year and sl_year != year) or sl_translator:
            sl_edition: dict = {}
            if sl_pub: sl_edition["publisher"] = sl_pub
            if sl_city: sl_edition["city"] = sl_city
            if sl_year and sl_year != year: sl_edition["year"] = sl_year
            if sl_translator: sl_edition["translator"] = _format_author(sl_translator)
            payload["slovenian_edition"] = sl_edition

    # Container/parent — keep on payload (the user's TM context)
    payload["container_work_id"] = _slugify(origin.rsplit(".", 1)[0])

    signals = {
        "has_author": bool(authors),
        "has_title": bool(title_en),
        "has_year": bool(year),
        "has_publisher": bool(publisher),
        "has_publisher_city": bool(city),
        "in_biblio_cluster": True,
        "title_bilingual": bool(title_sl),
        "verified_from_text": True,
        "verified_typed_pipeline": True,
        "style_detected": style != "unknown",
        # Per-type structural confidence boost: e.g. journal article that
        # passed verification (which rejected publisher) is high-confidence.
        f"valid_{citation_type}": True,
    }

    records: list[dict] = [{
        "kind": "cited_work",
        "payload": payload,
        "signals": signals,
        "source": _make_source(origin, seg_idx, src, tgt, citation_type),
    }]

    # Companion agent_person records --------------------------------------
    role = _role_for_type(citation_type)
    for a in authors:
        name = _format_author(a)
        if name:
            from translate_core.vl_extractor import _make_person_record
            pr = _make_person_record(
                {"name": name, "role": role},
                origin, seg_idx, src, tgt,
            )
            if pr:
                pr["signals"]["verified_typed_pipeline"] = True
                pr["signals"]["verified_from_text"] = True
                records.append(pr)

    # Editor / translator companion records (book/book_chapter mainly)
    for field, person_role in (
        ("editors", "editor"),
        ("editor", "editor"),
        ("translator", "translator"),
        ("curators", "curator"),
        ("interviewer", "interviewer"),
        ("interviewee", "interviewee"),
    ):
        v = verified.get(field)
        if not v:
            continue
        people = v if isinstance(v, list) else [v]
        for p in people:
            name = _format_author(p)
            if not name:
                continue
            from translate_core.vl_extractor import _make_person_record
            pr = _make_person_record(
                {"name": name, "role": person_role},
                origin, seg_idx, src, tgt,
            )
            if pr:
                pr["signals"]["verified_typed_pipeline"] = True
                pr["signals"]["verified_from_text"] = True
                records.append(pr)

    # Institution record for publisher ------------------------------------
    if publisher:
        from translate_core.vl_extractor import _make_institution_record
        inst = _make_institution_record(
            {"name": publisher, "type": "publisher", "city": city},
            origin, seg_idx, src, tgt,
        )
        if inst:
            records.append(inst)

    # For exhibition_catalog: venue is an institution
    venue = verified.get("venue")
    if venue:
        from translate_core.vl_extractor import _make_institution_record
        inst = _make_institution_record(
            {"name": venue, "type": "gallery"},
            origin, seg_idx, src, tgt,
        )
        if inst:
            records.append(inst)

    return records


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_source(origin: str, seg_idx: int, src: str, tgt: str, klass: str) -> dict:
    return {
        "origin": origin,
        "segment_idx": seg_idx,
        "src_excerpt": (src or "")[:240],
        "tgt_excerpt": (tgt or "")[:240],
        "segment_class": klass,
    }


def _pick_title(verified: Optional[dict], citation_type: str) -> Optional[str]:
    """Pick the appropriate title field for the given type, with the subtitle
    folded back in. The KG source_text has no separate subtitle field, so a
    title's subtitle (the part after the colon) belongs in the title itself —
    dropping it truncates the work title."""
    if not verified:
        return None
    if citation_type in (
        CitationType.JOURNAL_ARTICLE.value,
        CitationType.MAGAZINE_ARTICLE.value,
        CitationType.NEWSPAPER_ARTICLE.value,
    ):
        base = verified.get("article_title")
    elif citation_type == CitationType.BOOK_CHAPTER.value:
        base = verified.get("chapter_title")
    elif citation_type == CitationType.INTERVIEW.value:
        base = verified.get("title_or_program")
    else:
        base = verified.get("title")
    sub = (verified.get("subtitle") or "").strip()
    if base and sub and sub.lower() not in base.lower():
        return f"{base}: {sub}"
    return base


def _type_specific_extras(verified: dict, citation_type: str) -> dict:
    """Fields particular to a citation type that don't fit the primary
    title/author/year/publisher slots. Stored as a sub-dict on the node so
    the editor UI can present them per type."""
    keys_by_type = {
        # "subtitle" intentionally omitted — _pick_title folds it into the title
        CitationType.BOOK.value: ["edition", "doi", "url"],
        CitationType.BOOK_CHAPTER.value: ["book_title"],
        CitationType.JOURNAL_ARTICLE.value: ["journal", "volume", "issue", "doi", "url"],
        CitationType.MAGAZINE_ARTICLE.value: ["magazine", "date", "url"],
        CitationType.NEWSPAPER_ARTICLE.value: ["newspaper", "date", "section", "url"],
        CitationType.WEB_SOURCE.value: ["site_name", "publisher_or_org", "date_published", "accessed_date", "url"],
        CitationType.EXHIBITION_CATALOG.value: ["venue", "exhibition_dates"],
        CitationType.INTERVIEW.value: ["publication_or_network", "date", "medium", "url"],
        CitationType.THESIS_DISSERTATION.value: ["degree_type", "institution", "url"],
        CitationType.OTHER.value: ["raw_text", "notes"],
    }
    extras: dict = {}
    for k in keys_by_type.get(citation_type, []):
        v = verified.get(k)
        if v:
            extras[k] = v
    return extras


def _role_for_type(citation_type: str) -> str:
    """Default author role for a citation type's primary authors."""
    if citation_type in (
        CitationType.BOOK.value,
        CitationType.BOOK_CHAPTER.value,
        CitationType.JOURNAL_ARTICLE.value,
        CitationType.MAGAZINE_ARTICLE.value,
        CitationType.NEWSPAPER_ARTICLE.value,
        CitationType.THESIS_DISSERTATION.value,
        CitationType.WEB_SOURCE.value,
        CitationType.OTHER.value,
    ):
        return "author"
    if citation_type == CitationType.EXHIBITION_CATALOG.value:
        return "editor"
    return "author"


def _surname_str(person) -> str:
    if isinstance(person, dict):
        return str(person.get("surname") or "")
    return str(person or "").split(",")[0].strip()


def _format_author(person) -> str:
    if isinstance(person, dict):
        surname = (person.get("surname") or "").strip()
        given = (person.get("given_name") or "").strip()
        if surname and given:
            return f"{given} {surname}".strip()
        return surname or given
    return str(person or "").strip()


def _slugify(text: str) -> str:
    """Local slug helper (avoid circular import)."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] if s else "unknown"
