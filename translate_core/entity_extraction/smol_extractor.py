#!/usr/bin/env python3
"""Smol agent entity extraction — prompt formatting and result parsing.

This module does NOT call any LLM. It provides:
- Prompt formatting for smol agent dispatches via the OMP harness
- JSON parsing of smol agent responses into kg_ingest_entities-compatible records
- Ontology constraint enforcement on all extracted entities

Pipeline:
  1. run_entity_extraction.py --export-segments → writes segments to JSON
  2. OMP harness dispatches smol task agents to extract entities
  3. Agent results written to data/smol_extractions.json
  4. run_entity_extraction.py --ingest-extractions → reads results, scores, writes to KG

CANONICAL ONTOLOGY FIELDS (per ontology.md §2.4.2):
- title_orig / title_translation     (NOT title_en/title_sl for new writes)
- orig_lang / translation_lang       (two-letter codes)
- slovenian_edition: {publisher, city, year, translator}
- original_pub: {publisher, city, year}

Per ontology §4 invariant #4 — bilingual extraction is REQUIRED for citations
that appear in both languages of the TM. Per §3.2 — every cited_work must
have a `cited_in` edge to its container, plus `written_by` for authors,
`published_by` for original publisher, `sl_published_by` for SL-edition
publisher when distinct.

CONCEPT EXTRACTION (per ontology §2.2, §3.1):
For theoretical concepts mentioned in body text (e.g. "biopower", "the
heterogeneous"), we emit:
- a `concept` node (id: concept:<slug>, label, domain, definition)
- a `cited_work` node for the work where the concept was introduced
  (with author, year, project_type)
- the `cited_work` carries `cited_in → container_work_id` (where the
  concept was quoted) and `written_by → originating_author`
- two `instantiates_concept` edges (EN term → concept, SL term → concept)
  are wired by the termbase pipeline, not here

The smol extractor emits the concept along with its (a) originating author,
(b) source work, and (c) container where it was quoted, so downstream
ingestion can wire them up via factory methods only (ontology O-1).
"""

from __future__ import annotations

import json
import logging
import re

from ._slug import _slugify

logger = logging.getLogger(__name__)

# Tracks origins for which we've already warned about a missing t_index in
# ingest_smol_extractions. One warning per origin per process is enough to
# surface a stale smol export without spamming the log.
_t_index_fallback_warned: set[str] = set()


def _warn_once_t_index_fallback(origin: str) -> None:
    if origin not in _t_index_fallback_warned:
        logger.warning(
            "ingest_smol_extractions: origin=%r has no t_index; "
            "falling back to seg_idx. Re-run smol export after Phase 6.",
            origin,
        )
        _t_index_fallback_warned.add(origin)

# ── Ontology constraints ──────────────────────────────────────────────────────

VALID_AGENT_ROLES = {
    "author", "translator", "editor", "curator", "artist",
    "interviewer", "interviewee", "choreographer", "director",
    "performer", "dancer", "composer", "dramaturg", "agent",
}

VALID_INSTITUTION_KINDS = {
    "publisher", "gallery", "museum", "university", "festival",
    "theatre", "journal", "organization", "sponsor", "country", "other",
}

VALID_CITED_PROJECT_TYPES = {
    "book", "book_chapter", "journal_article", "magazine_article",
    "newspaper_article", "web_source", "exhibition_catalog",
    "exhibition_catalogue", "interview", "thesis_dissertation",
    "artwork", "performance", "cited_container", "cited_work",
}

VALID_CONCEPT_DOMAINS = {
    "humanities", "performance", "visual-art", "philosophy",
    "sociology", "anthropology", "politics", "aesthetics",
    "linguistics", "psychoanalysis", "feminism", "other",
}

# Two-letter language codes used in ontology
LANG_EN = "en"
LANG_SL = "sl"


# ── Prompt formatting for OMP agent dispatch ─────────────────────────────────

SYSTEM_PROMPT = (
    "You extract named entities and theoretical concepts from bilingual "
    "(EN/SL) translation segments from a Slovenian translator's corpus. "
    "Output only JSON. No prose. Preserve diacritics. Distinguish:\n"
    "- agents (people with roles: author/translator/editor/curator/artist/"
    "interviewer/interviewee/choreographer/director/performer/dancer/composer/dramaturg)\n"
    "- institutions (publisher/gallery/museum/festival/theatre/university/journal/...)\n"
    "- cited_works (books, articles, chapters quoted/referenced)\n"
    "- artworks (visual works: paintings, sculptures, photographs, installations)\n"
    "- performances (live works: dance, theatre, performance art) with creators "
    "(choreographer/director via written_by) and performers (via performed_by) "
    "and venue (hosted_by)\n"
    "- concepts (theoretical terms introduced or quoted by a specific author "
    "in a specific work). For concepts you MUST identify the originating "
    "author and source work whenever the segment provides them."
)

EXTRACT_PROMPT_TEMPLATE = """\
Container origin: {origin}
Container work (if known): {container}
Segment SOURCE ({src_lang}): {src}
Segment TARGET ({tgt_lang}): {tgt}

Identify every named entity and theoretical concept. For each, output one JSON object.

Schemas (omit any field you cannot fill from the segment):

agent_person:
  {{"kind":"agent_person",
    "name_orig":"<name as written in SOURCE>",
    "name_translation":"<name as written in TARGET, if it differs>",
    "role":"<one of: {roles}>"}}

institution:
  {{"kind":"institution",
    "name_orig":"<name in SOURCE>",
    "name_translation":"<name in TARGET, if it differs>",
    "kind_value":"<one of: {inst_kinds}>",
    "city":"<city if mentioned>"}}

cited_work:
  {{"kind":"cited_work",
    "author":"<original-language author name>",
    "title_orig":"<title in original language of the work>",
    "title_translation":"<title in the translation language, if visible>",
    "orig_lang":"<two-letter code: en|sl|de|fr|...>",
    "translation_lang":"<two-letter code or null>",
    "year":<int or null>,
    "project_type":"<one of: {proj_types}>",
    "original_pub":{{"publisher":"...","city":"...","year":<int>}},
    # SUNSET: Phase 11 — rename prompt key "slovenian_edition" → "translation_edition"
    # after model-regression testing confirms no recall drop. The builder already
    # maps this key to translation_edition in the payload (around line 540-552).
    # The prompt rename requires a separate model evaluation pass; do not rename
    # before Phase 11 regression suite runs.
    "slovenian_edition":{{"publisher":"...","city":"...","year":<int>,"translator":"..."}},
    "pages":"<page range if cited>"}}

artwork:
  {{"kind":"artwork",
    "artist":"<artist canonical name>",
    "title_orig":"<title in SOURCE language>",
    "title_translation":"<title in TARGET language, if visible>",
    "orig_lang":"<two-letter code>",
    "translation_lang":"<two-letter code or null>",
    "year":<int or null>,
    "medium":"<painting/sculpture/photograph/installation/video/...>",
    "host_institution":"<gallery/museum hosting the work, if mentioned>",
    "host_city":"<city of host institution>"}}

performance:
  {{"kind":"performance",
    "title_orig":"<title in SOURCE language>",
    "title_translation":"<title in TARGET language, if visible>",
    "orig_lang":"<two-letter code>",
    "translation_lang":"<two-letter code or null>",
    "year":<int or null>,
    "performance_kind":"<dance|theatre|performance_art|festival_act|opera|concert|other>",
    "creators":[
      {{"name":"<choreographer/director/dramaturg/composer name>",
        "role":"<one of: choreographer, director, dramaturg, composer, artist, author>"}}
    ],
    "performers":[
      {{"name":"<performer/dancer/actor name>",
        "role":"<one of: performer, dancer, actor>"}}
    ],
    "venue":"<theatre/festival/venue name>",
    "venue_city":"<city>"}}

concept:
  {{"kind":"concept",
    "label_orig":"<concept term in SOURCE language, e.g. 'biopower'>",
    "label_translation":"<concept term in TARGET language, e.g. 'biooblast'>",
    "orig_lang":"<two-letter code>",
    "translation_lang":"<two-letter code>",
    "domain":"<one of: {concept_domains}>",
    "originating_author":"<author who introduced this concept, if mentioned>",
    "source_work_title":"<title of the work where the concept was first introduced, if mentioned>",
    "source_work_year":<int or null>}}

Rules:
- Only output entities ACTUALLY MENTIONED in the segment.
- For concepts: only emit if the segment explicitly attributes the concept to an author or quotes it from a named work. Do NOT invent attributions.
- For bilingual fields: if SOURCE and TARGET both name the same entity, fill BOTH name_orig/name_translation (or title_orig/title_translation).
- For cited_work: container_work_id will be filled by the downstream pipeline from segment metadata; you do not need to emit it.
- Reply with: {{"entities":[<objects>]}}

Start your response with: {{"entities":["""


def _detect_source_lang(origin: str) -> tuple[str | None, str | None]:
    """Infer (src_lang, tgt_lang) from origin filename.

    Phase 1B blueprint §6: a single regex matches any two-letter ISO-639-1
    pair flanked by non-letter boundaries. Returns lowercase pair tuple or
    ``(None, None)`` when no pair is recognised. The caller is responsible
    for surfacing the (None, None) case (e.g. routing to review) — we no
    longer silently default to EN→SL.

    Recognised: ``big-HR-SL.tmx`` → (hr, sl); ``2022-SL-EN.tmx`` → (sl, en);
    ``mglc-EN-SL.tmx`` → (en, sl); ``en-sl.tmx`` → (en, sl);
    ``DE-FR.tmx`` → (de, fr). Unrecognised: ``foo.tmx`` → (None, None).
    """
    m = re.search(r"(?<![a-z])([a-z]{2})-([a-z]{2})(?![a-z])", origin.lower())
    return (m.group(1), m.group(2)) if m else (None, None)


def format_extract_prompt(
    src: str,
    tgt: str,
    origin: str,
    container_work_id: str = "",
) -> str:
    """Format a prompt for a smol agent extraction dispatch."""
    src_lang, tgt_lang = _detect_source_lang(origin)
    # Phase 1B blueprint §6 / Risk 3: detector may return None when no pair
    # is embedded in the origin filename. Guard the .upper() calls and emit
    # "??" as a sentinel so the LLM knows the language is undetermined.
    src_lang_label = src_lang.upper() if src_lang else "??"
    tgt_lang_label = tgt_lang.upper() if tgt_lang else "??"
    return EXTRACT_PROMPT_TEMPLATE.format(
        origin=origin,
        container=container_work_id or "(unknown — body text)",
        src_lang=src_lang_label,
        tgt_lang=tgt_lang_label,
        src=src[:1000],
        tgt=tgt[:1000],
        roles=", ".join(sorted(VALID_AGENT_ROLES)),
        inst_kinds=", ".join(sorted(VALID_INSTITUTION_KINDS)),
        proj_types=", ".join(sorted(VALID_CITED_PROJECT_TYPES)),
        concept_domains=", ".join(sorted(VALID_CONCEPT_DOMAINS)),
    )


# ── JSON parsing (robust, handles partial/malformed responses) ─────────────────

def parse_smol_response(raw: str) -> list[dict]:
    """Parse a smol agent's JSON response into entity dicts.

    Handles full JSON, partial JSON, leading prefill text, and brute-force
    object recovery from malformed output.
    """
    text = raw.strip()
    if not text:
        return []

    # If it doesn't start with { or [, find the first JSON delimiter
    if text[0] not in "{[":
        for delim in ("{", "["):
            idx = text.find(delim)
            if idx >= 0:
                text = text[idx:]
                break

    # Try full parse
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "entities" in data:
            return [e for e in data["entities"] if isinstance(e, dict)]
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
    except json.JSONDecodeError:
        pass

    # Brute-force: extract individual JSON objects with balanced braces
    entities = []
    depth = 0
    start = -1
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict) and "kind" in obj:
                        entities.append(obj)
                except json.JSONDecodeError:
                    pass
                start = -1
    return entities


# ── Record builders (ontology-compliant) ──────────────────────────────────────

def build_record(
    ent: dict,
    origin: str,
    seg_idx: int,
    container_work_id: str = "",
    *,
    src_lang: str | None,
    tgt_lang: str | None,
) -> dict | None:
    """Build a kg_ingest_entities-compatible record from a parsed entity.

    Phase 1B blueprint §7: ``src_lang`` and ``tgt_lang`` are required
    keyword arguments with no defaults — callers MUST pass an explicit
    pair (or explicit ``None``). The legacy ``LANG_EN`` / ``LANG_SL``
    defaults were removed; the only external caller
    (``ingest_smol_extractions``) already passes both explicitly.

    Enforces all ontology constraints. Returns None for empty/invalid entities.
    """
    kind = ent.get("kind", "")

    record: dict | None
    if kind == "agent_person":
        record = _build_agent_person(ent, origin, seg_idx, src_lang, tgt_lang)
    elif kind == "institution":
        record = _build_institution(ent, origin, seg_idx, src_lang, tgt_lang)
    elif kind == "cited_work":
        record = _build_cited_work(ent, origin, seg_idx, container_work_id, src_lang, tgt_lang)
    elif kind == "concept":
        record = _build_concept(ent, origin, seg_idx, container_work_id, src_lang, tgt_lang)
    elif kind == "artwork":
        record = _build_artwork(ent, origin, seg_idx, container_work_id, src_lang, tgt_lang)
    elif kind == "performance":
        record = _build_performance(ent, origin, seg_idx, container_work_id, src_lang, tgt_lang)
    else:
        return None

    if record is not None:
        # Phase 5 §4: stamp the producer provenance on the source dict.
        record.setdefault("source", {})["provenance"] = "tm_smol"
    return record


def _name_canonical(ent: dict) -> str:
    """Pick the canonical (longest non-abbreviated) name from name_orig/name_translation/name."""
    candidates = [
        (ent.get("name_orig") or "").strip(),
        (ent.get("name_translation") or "").strip(),
        (ent.get("name") or "").strip(),
    ]
    candidates = [c for c in candidates if c]
    if not candidates:
        return ""
    # Longest non-abbreviated wins
    return max(candidates, key=len)


def _build_agent_person(
    ent: dict, origin: str, seg_idx: int,
    src_lang: str | None, tgt_lang: str | None,
) -> dict | None:
    name = _name_canonical(ent)
    if not name:
        return None

    role = ent.get("role") or "agent"
    if role not in VALID_AGENT_ROLES:
        role = "agent"

    # Collect alt_spellings from both languages
    alt_spellings = []
    for k in ("name_orig", "name_translation", "name"):
        v = (ent.get(k) or "").strip()
        if v and v not in alt_spellings:
            alt_spellings.append(v)

    # Ontology §2.5: `name` is the canonical display name — longest non-
    # abbreviated form seen, with original casing preserved. The lowercased
    # `normalize_person_name` form is used for dedup_group/matching only,
    # NOT for the display name.
    try:
        from .name_dedup import dedup_group_key
        dg = dedup_group_key(name)
    except Exception:
        dg = _slugify(name)
    display_name = name  # preserve original casing

    return {
        "kind": "agent_person",
        "payload": {
            "name": display_name,
            "role": role,
            "dedup_group": dg,
            "alt_spellings": alt_spellings,
            "all_roles": [role],
            "mention_count": 1,
        },
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "segment_class": "smol_extracted",
        },
        "signals": {
            "smol_extracted": True,
            "verified_from_text": True,
            "has_name": True,
            "bilingual_name": len(alt_spellings) >= 2,
            # Smol returned a structured classification (kind=agent_person)
            # with a name. That IS evidence of person-name plausibility.
            "plausible_person_name": True,
            # Smol returned a role (author/translator/etc.) — explicit
            # role attribution context, not just a guess.
            "role_attribution_context": bool(ent.get("role")),
            # Composite signal: smol gave us a complete classification via
            # structured schema. We're here only because `name` was non-empty
            # (the function returns None otherwise). The 0.60 score bump
            # reflects that smol's structured-schema classification is far
            # stronger evidence than any regex/NER heuristic.
            "smol_verified_classification": True,
        },
    }


def _build_institution(
    ent: dict, origin: str, seg_idx: int,
    src_lang: str | None, tgt_lang: str | None,
) -> dict | None:
    # Institution name canonical form: prefer name_orig, fall back to name_translation/name
    name = (
        (ent.get("name_orig") or "").strip()
        or (ent.get("name") or "").strip()
        or (ent.get("name_translation") or "").strip()
    )
    if not name:
        return None

    # accept multiple key shapes
    kind = (
        ent.get("kind_value")
        or ent.get("institution_kind")
        or ent.get("kind_")
        or "other"
    )
    if kind not in VALID_INSTITUTION_KINDS:
        kind = "other"

    city = (ent.get("city") or "").strip() or None

    # Bilingual name handling
    name_translation = (ent.get("name_translation") or "").strip() or None

    return {
        "kind": "institution",
        "payload": {
            "name": name,
            "name_translation": name_translation,
            "kind": kind,
            "city": city,
        },
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "segment_class": "smol_extracted",
        },
        "signals": {
            "smol_extracted": True,
            "verified_from_text": True,
            "has_name": True,
            # Smol returned an institution.kind_value (gallery/publisher/...)
            # — that's a named-kind classification, not a regex guess.
            "named_institution_kind": kind != "other",
            # Composite: smol gave a complete institution classification
            # (name + recognized kind) via structured schema.
            "smol_verified_classification": True,
        },
    }


def _build_cited_work(
    ent: dict, origin: str, seg_idx: int, container_work_id: str,
    src_lang: str | None, tgt_lang: str | None,
) -> dict | None:
    title_orig = (ent.get("title_orig") or "").strip() or None
    title_translation = (ent.get("title_translation") or "").strip() or None
    # Legacy compat: accept title_en/title_sl if model emits them
    if not title_orig:
        title_orig = (ent.get("title_en") or "").strip() or None
    if not title_translation:
        title_translation = (ent.get("title_sl") or "").strip() or None
    if not title_orig and not title_translation:
        return None

    author = (ent.get("author") or "").strip() or None

    year = ent.get("year")
    try:
        year = int(year) if year is not None else None
    except (ValueError, TypeError):
        year = None

    project_type = ent.get("project_type") or "cited_work"
    if project_type not in VALID_CITED_PROJECT_TYPES:
        project_type = "cited_work"

    orig_lang = (ent.get("orig_lang") or "").strip().lower() or None
    translation_lang = (ent.get("translation_lang") or "").strip().lower() or None

    # Build slugified cited_id from author + title_orig + year
    canonical_title = title_orig or title_translation or ""
    parts = [p for p in (author, canonical_title, str(year) if year else "") if p]
    cited_id = _slugify("-".join(parts)) if parts else _slugify(canonical_title)

    # original_pub
    op = ent.get("original_pub") or {}
    original_pub = None
    if isinstance(op, dict) and op.get("publisher"):
        op_year = op.get("year")
        try:
            op_year = int(op_year) if op_year is not None else None
        except (ValueError, TypeError):
            op_year = None
        original_pub = {
            "publisher": (op.get("publisher") or "").strip() or None,
            "city": (op.get("city") or "").strip() or None,
            "year": op_year,
        }

    # slovenian_edition (per ontology §2.4.2)
    sl = ent.get("slovenian_edition") or {}
    slovenian_edition = None
    if isinstance(sl, dict) and (sl.get("publisher") or sl.get("translator")):
        sl_year = sl.get("year")
        try:
            sl_year = int(sl_year) if sl_year is not None else None
        except (ValueError, TypeError):
            sl_year = None
        slovenian_edition = {
            "publisher": (sl.get("publisher") or "").strip() or None,
            "city": (sl.get("city") or "").strip() or None,
            "year": sl_year,
            "translator": (sl.get("translator") or "").strip() or None,
        }

    payload = {
        "cited_id": cited_id,
        "author": author,
        # Canonical ontology fields (ontology §2.4.2):
        "title_orig": title_orig,
        "title_translation": title_translation,
        "orig_lang": orig_lang,
        "translation_lang": translation_lang,
        "year": year,
        "project_type": project_type,
        "pages": (ent.get("pages") or "").strip() or None,
        "original_pub": original_pub,
        # Phase 4: ontology §2.4.2 — translation_edition replaces the
        # SL/EN-named slovenian_edition sub-dict. The `language` field is
        # an ISO 639-1 code matching translation_lang. The smol prompt
        # still emits the model-facing key "slovenian_edition"; that
        # prompt-level rename is deferred to Phase 11 pending a model-
        # regression test (see SUNSET tag in the prompt template below).
        "translation_edition": (
            {**slovenian_edition, "language": translation_lang}
            if slovenian_edition and translation_lang
            else None
        ),
        "container_work_id": container_work_id or None,
    }
    # O-5 enforcement (ontology §2.4.2 + §4 invariant #4): translation_edition
    # implies the citation exists in BOTH languages. If we don't have both
    # title_orig AND title_translation, drop translation_edition rather than
    # write a node that violates the bilingual invariant.
    if payload.get("translation_edition") and not (
        payload.get("title_orig") and payload.get("title_translation")
    ):
        payload["translation_edition"] = None
    # Drop None values to keep payloads compact
    payload = {k: v for k, v in payload.items() if v is not None}
    return {
        "kind": "cited_work",
        "payload": payload,
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "segment_class": "smol_extracted",
        },
        "signals": {
            "smol_extracted": True,
            "verified_typed_pipeline": True,
            "verified_from_text": True,
            "has_author": bool(author),
            "has_title": True,
            "has_year": year is not None,
            "has_bilingual_title": bool(title_orig and title_translation),
            "has_publisher": bool(original_pub),
            # Phase 1B blueprint §8 / audit §3.3: signal key renamed from
            # the SL-baked `has_sl_edition` to the language-neutral
            # `has_target_lang_edition`. The +0.10 cited_work bump in
            # confidence.py:129 reads the new key. Ontology §2.4.2 still
            # uses the `slovenian_edition` sub-dict shape (sunset deferred
            # to Phase 11); the rename here is purely about the signal
            # flowing into the confidence scorer.
            "has_target_lang_edition": bool(slovenian_edition),
            # Smol returned a structured cited_work classification (author + title)
            "smol_verified_classification": bool(title_orig or title_translation),
            # Phase 3 composite-gate signals (audit §3.4, ontology §4 inv 9):
            # `title_bilingual` mirrors `has_bilingual_title`; the confidence
            # scorer already accepts either as the bilingual axis, but we set
            # both for explicitness. `container_attached` reflects the
            # cited_in anchor. `project_type_typed` is True only when smol
            # produced a real subtype (book/journal_article/…), NOT when it
            # fell back to the generic "cited_work" / "cited_container"
            # bucket — those fallbacks must NOT earn composite credit.
            "title_bilingual": bool(title_orig and title_translation),
            "container_attached": bool(container_work_id),
            "project_type_typed": (
                project_type in VALID_CITED_PROJECT_TYPES
                and project_type not in {"cited_work", "cited_container"}
            ),
        },
    }


def _build_concept(
    ent: dict, origin: str, seg_idx: int, container_work_id: str,
    src_lang: str | None, tgt_lang: str | None,
) -> dict | None:
    """Build a concept record with originating author + source work attribution.

    Emits a record with kind="concept" carrying:
    - label_orig / label_translation in two languages
    - originating_author: who introduced the concept (becomes agent + written_by edge)
    - source_work_title: where the concept was introduced (becomes cited_work + cited_in)
    - container_work_id: where the concept was quoted/used

    Ingestion will create:
    - concept node (id: concept:<slug>)
    - agent node for originating_author
    - cited_work source_text node for source_work_title
    - cited_in edge: source_work → container
    - written_by edge: source_work → originating_author
    - instantiates_concept edges are wired by the termbase layer separately
    """
    label_orig = (ent.get("label_orig") or "").strip() or None
    label_translation = (ent.get("label_translation") or "").strip() or None
    # Legacy compat
    if not label_orig:
        label_orig = (ent.get("label") or "").strip() or None
    if not label_orig and not label_translation:
        return None

    # Pick canonical label (prefer EN form per ontology §2.2)
    canonical_label = label_orig or label_translation or ""
    orig_lang = (ent.get("orig_lang") or "").strip().lower() or None
    translation_lang = (ent.get("translation_lang") or "").strip().lower() or None

    domain = ent.get("domain") or "humanities"
    if domain not in VALID_CONCEPT_DOMAINS:
        domain = "humanities"

    originating_author = (ent.get("originating_author") or "").strip() or None
    source_work_title = (ent.get("source_work_title") or "").strip() or None
    source_work_year = ent.get("source_work_year")
    try:
        source_work_year = int(source_work_year) if source_work_year is not None else None
    except (ValueError, TypeError):
        source_work_year = None

    concept_id = f"concept:{_slugify(canonical_label)}"

    payload = {
        "concept_id": concept_id,
        "label": canonical_label,
        "label_orig": label_orig,
        "label_translation": label_translation,
        "orig_lang": orig_lang,
        "translation_lang": translation_lang,
        "domain": domain,
        "definition": "",  # curator fills in
        # Anchoring fields — used by ingestion to wire edges
        "originating_author": originating_author,
        "source_work_title": source_work_title,
        "source_work_year": source_work_year,
        "container_work_id": container_work_id or None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    return {
        "kind": "concept",
        "payload": payload,
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "segment_class": "smol_extracted",
        },
        "signals": {
            "smol_extracted": True,
            "verified_from_text": True,
            "has_label": True,
            "has_bilingual_label": bool(label_orig and label_translation),
            "has_originating_author": bool(originating_author),
            "has_source_work": bool(source_work_title),
            "has_container": bool(container_work_id),
            # Smol returned a complete concept (label + originating author + source work)
            "smol_verified_classification": bool(
                label_orig and originating_author and source_work_title
            ),
            # Phase 3 composite-gate signal applicable to concept: only the
            # container axis is semantically meaningful. `title_bilingual` and
            # `project_type_typed` do not apply to concepts (concepts have a
            # bilingual label tracked separately, and no project_type).
            "container_attached": bool(container_work_id),
        },
    }


# ── Batch ingestion from smol agent results ────────────────────────────────────
def _build_artwork(
    ent: dict, origin: str, seg_idx: int, container_work_id: str,
    src_lang: str | None, tgt_lang: str | None,
) -> dict | None:
    """Build an artwork record (ontology §2.4.1: project_type=artwork).

    Emits payload compatible with kg_ingest_entities.deferred_artwork pass:
    needs work_id, title_en, title_sl, artist, medium. Also carries the
    canonical bilingual title_orig/title_translation + orig_lang/translation_lang
    fields per ontology §2.4.2.
    """
    title_orig = (ent.get("title_orig") or "").strip() or None
    title_translation = (ent.get("title_translation") or "").strip() or None
    if not title_orig and not title_translation:
        return None

    artist = (ent.get("artist") or "").strip() or None
    if not artist:
        return None

    year = ent.get("year")
    try:
        year = int(year) if year is not None else None
    except (ValueError, TypeError):
        year = None

    medium = (ent.get("medium") or "").strip() or None

    orig_lang = (ent.get("orig_lang") or "").strip().lower() or None
    translation_lang = (ent.get("translation_lang") or "").strip().lower() or None

    canonical_title = title_orig or title_translation or ""
    work_parts = [p for p in (artist, canonical_title, str(year) if year else "") if p]
    work_id = _slugify("-".join(work_parts))

    host_institution = (ent.get("host_institution") or "").strip() or None
    host_city = (ent.get("host_city") or "").strip() or None

    payload = {
        "work_id": work_id,
        "artist": artist,
        # Canonical bilingual fields (ontology §2.4.2)
        "title_orig": title_orig,
        "title_translation": title_translation,
        "orig_lang": orig_lang,
        "translation_lang": translation_lang,
        "year": year,
        "medium": medium,
        # Host institution captured so ingestion can wire hosted_by edge
        "host_institution": host_institution,
        "host_city": host_city,
        # Container (e.g. exhibition catalogue this artwork appears in)
        "container_work_id": container_work_id or None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    return {
        "kind": "artwork",
        "payload": payload,
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "segment_class": "smol_extracted",
        },
        "signals": {
            "smol_extracted": True,
            "verified_from_text": True,
            "has_title": True,
            "has_artist": True,
            "has_year": year is not None,
            "has_bilingual_title": bool(title_orig and title_translation),
            "has_host": bool(host_institution),
            # All required fields enforced at build time — always True if we got here.
            "smol_verified_classification": True,
            # Phase 3 composite-gate signals: artworks have a typed
            # project_type by definition (this builder only fires for kind
            # "artwork"); container_attached reflects the catalogue/source
            # this artwork was cited in.
            "title_bilingual": bool(title_orig and title_translation),
            "container_attached": bool(container_work_id),
            "project_type_typed": True,
        },
    }


def _build_performance(
    ent: dict, origin: str, seg_idx: int, container_work_id: str,
    src_lang: str | None, tgt_lang: str | None,
) -> dict | None:
    """Build a performance record (ontology §2.4.1: project_type=performance).

    Performances are source_text with creators (choreographer/director via
    written_by) and performers (via performed_by). Venue wires via hosted_by.

    Returns a single record with kind="performance" carrying creator and
    performer lists for downstream ingestion to expand into agent nodes +
    typed edges (written_by / performed_by).
    """
    title_orig = (ent.get("title_orig") or "").strip() or None
    title_translation = (ent.get("title_translation") or "").strip() or None
    if not title_orig and not title_translation:
        return None

    year = ent.get("year")
    try:
        year = int(year) if year is not None else None
    except (ValueError, TypeError):
        year = None

    orig_lang = (ent.get("orig_lang") or "").strip().lower() or None
    translation_lang = (ent.get("translation_lang") or "").strip().lower() or None

    canonical_title = title_orig or title_translation or ""

    # Sanitize creators and performers
    valid_creator_roles = {"choreographer", "director", "dramaturg", "composer", "artist", "author"}
    valid_performer_roles = {"performer", "dancer", "actor"}

    raw_creators = ent.get("creators") or []
    creators: list[dict] = []
    if isinstance(raw_creators, list):
        for c in raw_creators:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "").strip()
            if not name:
                continue
            role = c.get("role") or "director"
            if role not in valid_creator_roles:
                role = "director"
            creators.append({"name": name, "role": role})

    raw_performers = ent.get("performers") or []
    performers: list[dict] = []
    if isinstance(raw_performers, list):
        for p in raw_performers:
            if not isinstance(p, dict):
                continue
            name = (p.get("name") or "").strip()
            if not name:
                continue
            role = p.get("role") or "performer"
            if role not in valid_performer_roles:
                role = "performer"
            performers.append({"name": name, "role": role})

    if not creators and not performers:
        # A performance with no people attached is too thin — drop.
        return None

    work_anchor = (creators[0]["name"] if creators else "") or (performers[0]["name"] if performers else "")
    work_parts = [p for p in (work_anchor, canonical_title, str(year) if year else "") if p]
    work_id = _slugify("-".join(work_parts))

    venue = (ent.get("venue") or "").strip() or None
    venue_city = (ent.get("venue_city") or "").strip() or None

    performance_kind = (ent.get("performance_kind") or "").strip().lower() or None

    payload = {
        "work_id": work_id,
        # Canonical bilingual fields (ontology §2.4.2)
        "title_orig": title_orig,
        "title_translation": title_translation,
        "orig_lang": orig_lang,
        "translation_lang": translation_lang,
        "year": year,
        "performance_kind": performance_kind,
        "creators": creators,
        "performers": performers,
        "venue": venue,
        "venue_city": venue_city,
        "container_work_id": container_work_id or None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    return {
        "kind": "performance",
        "payload": payload,
        "source": {
            "origin": origin,
            "segment_idx": seg_idx,
            "segment_class": "smol_extracted",
        },
        "signals": {
            "smol_extracted": True,
            "verified_from_text": True,
            "has_title": True,
            "has_year": year is not None,
            "has_bilingual_title": bool(title_orig and title_translation),
            "has_creators": bool(creators),
            "has_performers": bool(performers),
            "has_venue": bool(venue),
            # All required fields enforced at build time — always True if we got here.
            "smol_verified_classification": True,
            # Phase 3 composite-gate signals: performances have a typed
            # project_type by definition (this builder only fires for kind
            # "performance"); container_attached reflects the festival /
            # programme this performance was cited in.
            "title_bilingual": bool(title_orig and title_translation),
            "container_attached": bool(container_work_id),
            "project_type_typed": True,
        },
    }


# ── Batch ingestion from smol agent results ────────────────────────────────────


def ingest_smol_extractions(
    extractions: list[dict],
) -> list[dict]:
    """Convert a batch of smol agent extraction results into records.

    Each item in extractions has:
      - origin: TM origin filename
      - seg_idx: segment index within origin
      - container_work_id: container slug (may be empty)
      - entities: list of parsed entity dicts from smol agent

    Returns a flat list of kg_ingest_entities-compatible record dicts.
    """
    records: list[dict] = []
    for item in extractions:
        origin = item.get("origin", "")
        # Phase 6: prefer t_index (TM chronological rank); fall back to seg_idx
        # only when the export pre-dates Phase 6. Warn once per origin so a
        # stale export surfaces without spamming the log.
        t_index = item.get("t_index")
        if t_index is None:
            _warn_once_t_index_fallback(origin)
            effective_idx = item.get("seg_idx", -1)
        else:
            effective_idx = t_index
        container = item.get("container_work_id", "")
        entities = item.get("entities", [])
        src_lang, tgt_lang = _detect_source_lang(origin)
        for ent in entities:
            rec = build_record(
                ent, origin, effective_idx, container,
                src_lang=src_lang, tgt_lang=tgt_lang,
            )
            if rec:
                records.append(rec)
    return records