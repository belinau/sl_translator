# translate_core/document_pair_pipeline.py
#
# Bilingual document-pair orchestrator.
#
# Given a matched EN/SL pair of source documents (DOCX or DOCX+MD), this
# module:
#   1. Parses each side independently via DocumentParser.to_markdown_with_meta
#      (text-only path; no VL/LLM calls happen here).
#   2. Collects citation snippets from both sides using citation_collector
#      adapters (collect_from_segments_meta for the parsed metadata path and
#      collect_from_md for the raw markdown footnote definitions).
#   3. Builds minimal "matching records" from the footnote text via a small
#      heuristic that pulls out a first-author surname and a probable title.
#      The full typed extraction (VL classify + typed extract) lives in the
#      standard pipeline; we only need title text strong enough to match EN
#      against SL.
#   4. Matches EN ↔ SL records by (author_surname, title token overlap) per
#      plan §13.5: SL citation is NOT a translation of the EN footnote, so
#      both sides are extracted independently and matched after.
#   5. Writes ONE merged source_text node per matched pair, carrying both
#      title_en and title_sl (O-5), via KnowledgeGraph.add_source_text_node.
#   6. Wires cited_in to the container_work_id (O-17 forbids self-loops; the
#      KG factory already enforces this, and we guard before calling).
#   7. Optionally emits a TMX 1.4 sentence-aligned file from the matched
#      footnote texts, with a manifest sidecar via write_tmx_manifest.
#
# Invariants honoured:
#   O-1  Only KnowledgeGraph factory methods write to the KG.
#   O-2  NFKD slug: strip combining → lowercase → re.sub(r"[^a-zA-Z0-9]+","-")
#        → strip "-" → truncate 80 → fallback "unknown".
#   O-5  Every merged source_text MUST carry both title_en and title_sl.
#   O-17 Never create cited_in self-loops.
#
# Best-effort matching — unmatched citations are reported, not forced.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from xml.sax.saxutils import escape as _xml_escape

from .citation_collector import (
    CitationSnippet,
    collect_from_md,
    collect_from_segments_meta,
    write_tmx_manifest,
)
from .doc_parser import DocumentParser
from .entity_extraction._slug import _slugify

if TYPE_CHECKING:  # avoid runtime import cycle; KG passed in by caller anyway
    from .knowledge_graph import KnowledgeGraph

log = logging.getLogger("document_pair_pipeline")


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class SideParsed:
    """Result of parsing one side of a document pair."""

    path: Path
    lang: str  # "en" or "sl"
    markdown: str
    segments_meta: list[dict]  # from doc_parser (may be empty for DOCX)
    footnotes: list[dict] = field(default_factory=list)
    # footnotes entries: {"text": str, "fn_number": int | None, "page": int | None}


@dataclass
class CitationMatch:
    """A matched bilingual citation pair (translation_record may be None
    when the original side has no matched translation).

    The pipeline contract binds `en_record` (the orig side) and
    `sl_record` (the translation side) to a fixed EN-orig / SL-translation
    orientation enforced by `parse_side`'s lang validation. The neutral
    `title_orig`/`title_translation` fields are populated accordingly at
    construction time."""

    en_record: dict
    sl_record: Optional[dict]
    match_score: float  # 0.0–1.0 token-overlap score
    title_orig: str
    title_translation: str  # empty string when not matched


@dataclass
class PairResult:
    """Full result of processing one document pair."""

    container_work_id: str
    citation_matches: list[CitationMatch] = field(default_factory=list)
    unmatched_en: list[dict] = field(default_factory=list)
    unmatched_sl: list[dict] = field(default_factory=list)
    tmx_path: Optional[Path] = None
    n_bilingual: int = 0
    n_en_only: int = 0
    n_sl_only: int = 0


# ── Normalisation and slug helpers (O-2) ─────────────────────────────────────


def _normalise(text: str) -> str:
    """NFKD → strip combining → lowercase → non-alnum collapsed to spaces."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z0-9]+", " ", stripped).strip().lower()



def _tokens(text: str) -> list[str]:
    """Split normalised text into content tokens, dropping 1-char noise."""
    return [t for t in _normalise(text).split() if len(t) > 1]


# ── Footnote and bibliography extraction from markdown ───────────────────────


# Single-line definition pattern per assignment:
#   re.findall(r"\[\^(\d+)\]:\s*(.+)", markdown, re.MULTILINE)
_FN_DEF_LINE_RE = re.compile(r"\[\^(\d+)\]:\s*(.+)", re.MULTILINE)

# Multi-line definition pattern (continuation lines until next def or blank line)
_FN_DEF_BLOCK_RE = re.compile(
    r"\[\^(\d+)\]:\s*(.+?)(?=\n\[\^\d+\]:|\n\n|\Z)", re.DOTALL
)

# Detect a bibliography section header.
_BIBLIO_HEADER_RE = re.compile(
    r"^#+\s*(bibliography|works?\s+cited|references|literatura|viri)\b",
    re.IGNORECASE,
)


def _extract_footnotes_from_md(markdown: str) -> list[dict]:
    """Return [{text, fn_number, page}] for every footnote definition in md.

    Uses the single-line pattern from the assignment for the primary scan,
    then upgrades each entry's ``text`` to its full multi-line block when
    one exists (continuation lines that belong to the same def).
    Bibliography-section lines are emitted as text-only entries with
    fn_number=None.
    """
    if not markdown:
        return []

    # 1. Single-line capture (assignment-specified regex).
    primary = _FN_DEF_LINE_RE.findall(markdown)

    # 2. Multi-line block upgrade keyed by footnote number.
    block_map: dict[str, str] = {}
    for m in _FN_DEF_BLOCK_RE.finditer(markdown):
        block_map[m.group(1)] = m.group(2).strip()

    footnotes: list[dict] = []
    seen_numbers: set[str] = set()
    for num_str, line_text in primary:
        if num_str in seen_numbers:
            continue
        seen_numbers.add(num_str)
        text = (block_map.get(num_str) or line_text).strip()
        if not text:
            continue
        try:
            fn_number: Optional[int] = int(num_str)
        except ValueError:
            fn_number = None
        footnotes.append({"text": text, "fn_number": fn_number, "page": None})

    # 3. Bibliography section: every non-empty line after a heading until
    #    the next heading is treated as a citation entry.
    in_biblio = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _BIBLIO_HEADER_RE.match(stripped):
            in_biblio = True
            continue
        if stripped.startswith("#"):
            in_biblio = False
            continue
        if in_biblio and len(stripped) > 20:
            footnotes.append({"text": stripped, "fn_number": None, "page": None})

    return footnotes


# ── Heuristic record builder (no VL/LLM) ─────────────────────────────────────


# "Lastname, Firstname[ M.]" at the start — author-date / Chicago notes-bibl form.
_LASTNAME_FIRST_RE = re.compile(
    r"^\s*([A-ZŠŽČĆĐÁÉÍÓÚÀÈÌÒÙÄËÏÖÜ][\w'’\-]+),\s+"
    r"([A-ZŠŽČĆĐÁÉÍÓÚÀÈÌÒÙÄËÏÖÜ][\w'’\-\.\s]+?)"
    r"(?=[,\.])"
)

# "Firstname [Middle] Lastname," — Chicago footnote form.
_FIRSTNAME_FIRST_RE = re.compile(
    r"^\s*([A-ZŠŽČĆĐÁÉÍÓÚÀÈÌÒÙÄËÏÖÜ][\w'’\-]+(?:\s+[A-ZŠŽČĆĐÁÉÍÓÚÀÈÌÒÙÄËÏÖÜ]\.?)?)"
    r"\s+([A-ZŠŽČĆĐÁÉÍÓÚÀÈÌÒÙÄËÏÖÜ][\w'’\-]+),"
)

# Italicised title — `*Title*` or `_Title_`.
_ITALIC_TITLE_RE = re.compile(r"[*_]([^*_\n]{4,250})[*_]")

# Quoted article/chapter title — `"Title,"` or `“Title,”` or `'Title,'`.
# Built as a non-raw string so the double-quote escape is unambiguous
# alongside the smart-quote unicode characters in the class.
_QUOTED_TITLE_RE = re.compile(
    "[\"\u201c\u2018']([^\"\u201d\u2019'\n]{4,250})[\"\u201d\u2019']"
)
# Trailing punctuation (`,` `.`) often sits inside the close quote in
# Chicago style — strip it from the captured title.
_TRAILING_PUNCT_RE = re.compile(r"[\s,.:;]+$")


def _parse_author_and_title(text: str) -> tuple[str, str]:
    """Heuristically pull (author_surname, title) from a footnote/biblio entry.

    Returns ``("", "")`` when nothing recognisable is found. This is *only*
    used for matching — the real typed pipeline still runs through VL.
    """
    if not text:
        return "", ""

    surname = ""
    # Lastname-first wins when present (biblio entries) — it's the more
    # reliable signal because the first token before the comma is the surname.
    m = _LASTNAME_FIRST_RE.match(text)
    if m:
        surname = m.group(1)
    else:
        m = _FIRSTNAME_FIRST_RE.match(text)
        if m:
            surname = m.group(2)

    title = ""
    m_italic = _ITALIC_TITLE_RE.search(text)
    if m_italic:
        title = _TRAILING_PUNCT_RE.sub("", m_italic.group(1).strip())
    else:
        m_quoted = _QUOTED_TITLE_RE.search(text)
        if m_quoted:
            title = _TRAILING_PUNCT_RE.sub("", m_quoted.group(1).strip())
        else:
            # Fall back to the clause after the first ", YEAR:" or "), Year."
            # construct or just everything after the first comma.
            tail = text
            after_year = re.search(
                r"(?:,\s*\d{4}[\.:,]?\s*|\(\d{4}\)\s*[\.:,]?\s*)(.+)", tail
            )
            if after_year:
                tail = after_year.group(1)
            tail = tail.split(".")[0]
            tail = tail.strip(" ,;:—–-")
            if 4 <= len(tail) <= 250:
                title = tail

    return surname, title


def _heuristic_record(
    snippet: CitationSnippet,
    *,
    lang: str,
) -> dict:
    """Build a record-shaped dict (NOT through VL) suitable for matching.

    The shape mirrors the cited_work records produced by the typed pipeline
    closely enough that downstream code reading ``payload.author`` and
    ``payload.title_en`` / ``payload.title_sl`` works uniformly.
    """
    surname, title = _parse_author_and_title(snippet.text)
    payload: dict = {
        "cited_id": _slugify(f"{surname}-{title}"),
        "raw_text": snippet.text,
        "author": surname,
        "authors": [surname] if surname else [],
        "year": None,
    }
    if lang == "en":
        payload["title_en"] = title
        payload["title_sl"] = ""
    else:
        payload["title_en"] = ""
        payload["title_sl"] = title
    return {
        "kind": "cited_work",
        "lang": lang,
        "payload": payload,
        "signals": {
            "has_author": bool(surname),
            "has_title": bool(title),
            "from_doc_pair_heuristic": True,
        },
        "source": {
            "origin": snippet.origin,
            "segment_idx": snippet.segment_idx,
            "format": snippet.format,
            "footnote_number": snippet.footnote_number,
            "provenance": "doc_pair",
        },
    }


# ── Author / title extraction from records for matching ──────────────────────


def _author_surname(record: dict) -> str:
    """Return normalised first-author surname from a record payload."""
    payload = record.get("payload") or {}
    candidate = ""
    authors = payload.get("authors")
    if isinstance(authors, list) and authors:
        first = authors[0]
        if isinstance(first, dict):
            candidate = first.get("surname") or first.get("name") or ""
        elif isinstance(first, str):
            # The typed pipeline serialises as "Lastname, Firstname"; for
            # those, the surname is the leading clause before the comma.
            candidate = first.split(",")[0].strip()
    if not candidate:
        author = payload.get("author")
        if isinstance(author, str):
            candidate = author.split(",")[0].strip()
        elif isinstance(author, dict):
            candidate = author.get("surname") or author.get("name") or ""
    return _normalise(candidate)


def _record_title(record: dict, *, prefer_lang: str) -> str:
    """Return the most informative title for matching on a record."""
    payload = record.get("payload") or {}
    if prefer_lang == "en":
        return payload.get("title_en") or payload.get("title") or ""
    return payload.get("title_sl") or payload.get("title") or ""


# ── Matching (§13.5: independent extraction + post-hoc pairing) ──────────────


_MATCH_THRESHOLD = 0.6


def _score_pair(en_record: dict, sl_record: dict) -> float:
    """Score = |matching tokens| / max(|en_tokens|, |sl_tokens|).

    Tokens come from (author_surname + title), normalised.
    """
    en_tokens = set(_tokens(_author_surname(en_record))) | set(
        _tokens(_record_title(en_record, prefer_lang="en"))
    )
    sl_tokens = set(_tokens(_author_surname(sl_record))) | set(
        _tokens(_record_title(sl_record, prefer_lang="sl"))
    )
    if not en_tokens or not sl_tokens:
        return 0.0
    overlap = en_tokens & sl_tokens
    denom = max(len(en_tokens), len(sl_tokens))
    if denom == 0:
        return 0.0
    return len(overlap) / denom


def _match_citations(
    en_records: list[dict],
    sl_records: list[dict],
) -> list[CitationMatch]:
    """Greedy 1-to-1 best-match pairing across the two sides.

    For each EN record we pick the highest-scoring SL record that has not
    yet been claimed, accept the match when score ≥ ``_MATCH_THRESHOLD``,
    and leave unmatched EN records with ``sl_record=None`` and
    ``title_sl=""``. Unmatched SL records are *not* emitted here — the
    caller computes them as ``sl_records − {claimed SL}`` for the
    PairResult.unmatched_sl list.
    """
    matches: list[CitationMatch] = []
    claimed_sl: set[int] = set()  # indices into sl_records

    for en in en_records:
        best_idx = -1
        best_score = 0.0
        for i, sl in enumerate(sl_records):
            if i in claimed_sl:
                continue
            score = _score_pair(en, sl)
            if score > best_score:
                best_score = score
                best_idx = i

        title_orig = _record_title(en, prefer_lang="en")
        if best_idx >= 0 and best_score >= _MATCH_THRESHOLD:
            claimed_sl.add(best_idx)
            sl_match = sl_records[best_idx]
            matches.append(
                CitationMatch(
                    en_record=en,
                    sl_record=sl_match,
                    match_score=best_score,
                    title_orig=title_orig,
                    title_translation=_record_title(sl_match, prefer_lang="sl"),
                )
            )
        else:
            matches.append(
                CitationMatch(
                    en_record=en,
                    sl_record=None,
                    match_score=best_score,
                    title_orig=title_orig,
                    title_translation="",
                )
            )

    return matches


# ── Side parser ──────────────────────────────────────────────────────────────


def parse_side(doc_path: Path, lang: str) -> SideParsed:
    """Parse one document and extract its markdown + footnotes."""
    if lang not in ("en", "sl"):
        raise ValueError(f"lang must be 'en' or 'sl', got {lang!r}")
    parser = DocumentParser()
    markdown, segments_meta = parser.to_markdown_with_meta(doc_path)
    footnotes = _extract_footnotes_from_md(markdown)
    log.debug(
        "parse_side: %s lang=%s footnotes=%d segments_meta=%d",
        doc_path.name,
        lang,
        len(footnotes),
        len(segments_meta),
    )
    return SideParsed(
        path=doc_path,
        lang=lang,
        markdown=markdown,
        segments_meta=segments_meta,
        footnotes=footnotes,
    )


# ── Snippet collection across both adapter paths ─────────────────────────────


def _collect_side_snippets(
    side: SideParsed,
    container_work_id: str,
) -> list[CitationSnippet]:
    """Pull CitationSnippets from a parsed side.

    Tries the segments_meta adapter first (populated only on VL PDF paths,
    which never happens in this module — but kept for symmetry should a
    caller seed an already-parsed PDF project). Falls back to a direct
    markdown footnote scan via ``collect_from_md`` written against a
    temp-file-free derivation: we synthesise CitationSnippet objects here
    from our already-extracted ``side.footnotes`` to avoid round-tripping
    through disk.
    """
    snippets: list[CitationSnippet] = []
    if side.segments_meta:
        project_json = {
            "segments_meta": side.segments_meta,
            "segments": [],  # collect_from_segments_meta tolerates this
            "filename": side.path.name,
        }
        snippets.extend(
            collect_from_segments_meta(
                project_json, container_work_id=container_work_id
            )
        )

    # Always also surface footnotes/biblio from the markdown — segments_meta
    # is empty for the MarkItDown DOCX path and footnote text is the only
    # signal we can match on. We synthesise snippets directly from the
    # already-extracted footnote list to avoid a redundant regex pass.
    origin = side.path.stem
    for fn in side.footnotes:
        text = fn["text"]
        if not text or not text.strip():
            continue
        fn_num = fn.get("fn_number")
        snippets.append(
            CitationSnippet(
                text=text,
                origin=origin,
                segment_idx=fn_num if isinstance(fn_num, int) else 0,
                format="footnote" if fn_num is not None else "bibliography",
                container_work_id=container_work_id,
                footnote_number=fn_num if isinstance(fn_num, int) else None,
            )
        )
    return snippets


def _collect_from_md_path(
    md_path: Path, container_work_id: str
) -> list[CitationSnippet]:
    """Thin wrapper around citation_collector.collect_from_md.

    Exposed so callers can pre-render a side's markdown to disk (e.g.
    when an EN/SL ``.md`` ships alongside the source) and reuse the
    canonical adapter.
    """
    return collect_from_md(str(md_path), container_work_id=container_work_id)


# ── KG ingest of a matched pair ──────────────────────────────────────────────


def _maybe_attach_publisher(
    kg: KnowledgeGraph,
    source_node_id: str,
    match: CitationMatch,
) -> None:
    """If either side surfaced a publisher, link source → publisher.

    Heuristic-only: we look on ``payload.original_pub.publisher`` first
    (matches the typed pipeline shape) and fall back to a flat
    ``payload.publisher`` field.
    """
    for record in (match.en_record, match.sl_record):
        if not record:
            continue
        payload = record.get("payload") or {}
        pub_name = ""
        op = payload.get("original_pub")
        if isinstance(op, dict):
            pub_name = (op.get("publisher") or "").strip()
        if not pub_name:
            pub_name = (payload.get("publisher") or "").strip()
        if not pub_name:
            continue
        inst_id = _slugify(pub_name)
        kg.add_institution_node(inst_id, name=pub_name, kind="publisher")
        kg.link_published_by(source_node_id, inst_id)
        return  # one publisher edge per source_text is enough


def _ingest_match(
    kg: KnowledgeGraph,
    match: CitationMatch,
    container_work_id: str,
    *,
    orig_lang: str,
    translation_lang: str,
) -> Optional[str]:
    """Create the merged source_text node + cited_in edge for one match.

    Returns the resulting node id (or None when the match has no usable
    title on either side and nothing is written).

    `orig_lang` / `translation_lang` come from the caller's SideParsed
    instances (`en_side.lang`, `sl_side.lang`) and are written as
    evidence-derived values on the resulting source_text node.
    """
    title_orig = (match.title_orig or "").strip()
    title_translation = (match.title_translation or "").strip()
    if not title_orig and not title_translation:
        return None

    # Display title: prefer the orig side; fall back to translation when
    # only that side is populated.
    display_title = title_orig or title_translation

    # Pull author and year off whichever record has them.
    en_payload = (match.en_record or {}).get("payload") or {}
    sl_payload = (match.sl_record or {}).get("payload") if match.sl_record else {}
    sl_payload = sl_payload or {}

    primary_author = (
        en_payload.get("author")
        or sl_payload.get("author")
        or ""
    )
    year = en_payload.get("year") or sl_payload.get("year")

    cited_id = (
        en_payload.get("cited_id")
        or sl_payload.get("cited_id")
        or _slugify(f"{primary_author}-{display_title}-{year or ''}")
    )

    extra: dict = {
        "project_type": en_payload.get("project_type") or "cited_work",
        "title_orig": title_orig,
        "orig_lang": orig_lang,
        "title_translation": title_translation,
        "translation_lang": translation_lang,
        "container_work_id": container_work_id,
        "provenance": "doc_pair",
    }

    source_node_id = kg.add_source_text_node(
        cited_id,
        display_title,
        year=year if isinstance(year, int) else None,
        **extra,
    )

    # O-17: never create cited_in self-loops. The KG factory already drops
    # them, but we additionally short-circuit here so the user sees no
    # surprise edge attempt in logs.
    container_node_id = (
        container_work_id
        if container_work_id.startswith("source:")
        else f"source:{container_work_id.lower()}"
    )
    if source_node_id != container_node_id:
        kg.link_cited_in(source_node_id, container_work_id)

    _maybe_attach_publisher(kg, source_node_id, match)
    return source_node_id


# ── TMX writer ───────────────────────────────────────────────────────────────


_TMX_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE tmx SYSTEM "tmx14.dtd">\n'
    '<tmx version="1.4">\n'
    '  <header creationtool="sl_translator.document_pair_pipeline"\n'
    '          creationtoolversion="1.0"\n'
    '          segtype="sentence"\n'
    '          o-tmf="markdown-footnote"\n'
    '          adminlang="en"\n'
    '          srclang="{srclang}"\n'
    '          datatype="plaintext"/>\n'
    '  <body>\n'
)
_TMX_FOOTER = "  </body>\n</tmx>\n"


def _tu_xml(src_lang: str, src: str, tgt_lang: str, tgt: str) -> str:
    return (
        "    <tu>\n"
        f'      <tuv xml:lang="{src_lang}"><seg>{_xml_escape(src)}</seg></tuv>\n'
        f'      <tuv xml:lang="{tgt_lang}"><seg>{_xml_escape(tgt)}</seg></tuv>\n'
        "    </tu>\n"
    )


def _build_tmx(
    en_side: SideParsed,
    sl_side: SideParsed,
    container_work_id: str,
    output_path: Path,
) -> Path:
    """Write a simple sentence-aligned TMX from paired footnote texts.

    Pairs are formed positionally on the smaller of the two footnote lists
    — this is sentence-aligned only at the footnote-block granularity,
    which is the strongest alignment available without VL on the DOCX
    path. The manifest sidecar records the container + lang pair so
    ``collect_from_tmx`` can later recover ``container_work_id`` without
    being told.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    n = min(len(en_side.footnotes), len(sl_side.footnotes))
    parts: list[str] = [_TMX_HEADER.format(srclang=en_side.lang)]
    for i in range(n):
        en_text = en_side.footnotes[i]["text"]
        sl_text = sl_side.footnotes[i]["text"]
        if not en_text.strip() or not sl_text.strip():
            continue
        parts.append(_tu_xml(en_side.lang, en_text, sl_side.lang, sl_text))
    parts.append(_TMX_FOOTER)

    output_path.write_text("".join(parts), encoding="utf-8")

    write_tmx_manifest(
        str(output_path),
        container_work_id=container_work_id,
        container_type="book_translation",
        source_lang=en_side.lang,
        target_lang=sl_side.lang,
    )
    log.info(
        "TMX written: %s (%d translation units, container=%s)",
        output_path,
        n,
        container_work_id,
    )
    return output_path

# ── Orchestrator ─────────────────────────────────────────────────────────────
def _wire_bridges_for_pair(
    kg: "KnowledgeGraph",
    *,
    container_work_id: str,
    translator_agent_id: str,
    en_side: "SideParsed",
    sl_side: "SideParsed",
) -> int:
    """Wire bridge edges for this doc-pair run.
    Ontology §3.3 defines two bridges off every translation_mapping:
      * (mapping) -[attributed_to]-> (agent)        # translator/curator
      * (mapping) -[instantiated_in]-> (source_text) # work it appeared in
    In a single-translator KG every mapping is by definition attributed to
    that translator, so we add the ``attributed_to`` edge for every mapping
    that is missing it. ``instantiated_in`` is per-work and cannot be safely
    attached to mappings without knowing which book each came from — that
    backfill belongs to ``scripts/backfill_lineage_bridges.py``.
    Returns the number of ``attributed_to`` edges added.
    """
    if not translator_agent_id:
        return 0
    agent_node = (
        translator_agent_id
        if translator_agent_id.startswith("agent:")
        else f"agent:{translator_agent_id.lower()}"
    )
    if not kg.G.has_node(agent_node):
        log.warning(
            "Translator agent %s missing; skipping attributed_to wiring",
            agent_node,
        )
        return 0
    added = 0
    mapping_ids = [
        nid
        for nid, nd in kg.G.nodes(data=True)
        if nd.get("type") == "translation_mapping"
    ]
    for mid in mapping_ids:
        if kg.G.has_edge(mid, agent_node):
            continue
        slug = agent_node.split(":", 1)[1] if ":" in agent_node else agent_node
        if kg.link_attributed_to(mid, slug):
            added += 1
    if added:
        log.info(
            "process_pair: wired %d attributed_to bridges to %s (container=%s)",
            added,
            agent_node,
            container_work_id,
        )
    return added
# ── Orchestrator ─────────────────────────────────────────────────────────────
def process_pair(
    en_doc_path: Path,
    sl_doc_path: Path,
    container_work_id: str,
    kg: KnowledgeGraph,
    *,
    translator_agent_id: str = "urban-belina",
    bridge_mappings: bool = True,
    dry_run: bool = False,
) -> PairResult:
    """Process a matched EN/SL document pair end-to-end.

    Steps (no VL/LLM at any point):
        1. Parse both sides → SideParsed (markdown + footnotes).
        2. Collect CitationSnippets from each side.
        3. Build minimal "matching records" from snippet text via a small
           heuristic.
        4. Match EN ↔ SL by (author_surname, title) token overlap.
        5. For each match, write ONE merged source_text node carrying
           both title_en and title_sl, link cited_in to the container.
        6. Wire a publisher edge when a publisher was surfaced.

    ``dry_run=True`` performs every parse + match step but writes nothing
    to the KG and emits no TMX — the returned PairResult still reflects
    what *would* have been done.
    """
    en_side = parse_side(en_doc_path, lang="en")
    sl_side = parse_side(sl_doc_path, lang="sl")

    en_snippets = _collect_side_snippets(en_side, container_work_id)
    sl_snippets = _collect_side_snippets(sl_side, container_work_id)

    en_records = [_heuristic_record(s, lang="en") for s in en_snippets]
    sl_records = [_heuristic_record(s, lang="sl") for s in sl_snippets]

    matches = _match_citations(en_records, sl_records)

    claimed_sl_ids: set[int] = set()
    bilingual = en_only = 0
    unmatched_en: list[dict] = []

    for m in matches:
        if m.sl_record is not None:
            bilingual += 1
            claimed_sl_ids.add(id(m.sl_record))
        else:
            en_only += 1
            unmatched_en.append(m.en_record)

    unmatched_sl = [r for r in sl_records if id(r) not in claimed_sl_ids]
    sl_only = len(unmatched_sl)

    tmx_path: Optional[Path] = None
    if not dry_run:
        for m in matches:
            if m.sl_record is None:
                # EN-only citations still get written — but without title_sl
                # the record violates O-5 for "bilingual" entries. We DO
                # NOT write these here. They're reported in unmatched_en
                # for the caller to decide whether to push through the
                # standard typed pipeline.
                continue
            _ingest_match(
                kg, m, container_work_id,
                orig_lang=en_side.lang,
                translation_lang=sl_side.lang,
            )
        # Wire termbase↔bibliography bridges for translation_mappings whose
        # source/target terms participate in the citations we just ingested.
        # Per ontology §3.3, bridges are written exclusively via
        # KnowledgeGraph.link_translations_with_context.
        if bridge_mappings:
            try:
                _wire_bridges_for_pair(
                    kg,
                    container_work_id=container_work_id,
                    translator_agent_id=translator_agent_id,
                    en_side=en_side,
                    sl_side=sl_side,
                )
            except Exception:
                log.exception("Bridge wiring failed for container=%s", container_work_id)
        # Emit TMX next to the EN doc.
        tmx_path = en_doc_path.with_suffix(".tmx")
        try:
            _build_tmx(en_side, sl_side, container_work_id, tmx_path)
        except Exception:
            log.exception("Failed to build TMX at %s", tmx_path)
            tmx_path = None

    result = PairResult(
        container_work_id=container_work_id,
        citation_matches=matches,
        unmatched_en=unmatched_en,
        unmatched_sl=unmatched_sl,
        tmx_path=tmx_path,
        n_bilingual=bilingual,
        n_en_only=en_only,
        n_sl_only=sl_only,
    )
    log.info(
        "process_pair: container=%s bilingual=%d en_only=%d sl_only=%d",
        container_work_id,
        bilingual,
        en_only,
        sl_only,
    )
    return result


__all__ = [
    "SideParsed",
    "CitationMatch",
    "PairResult",
    "parse_side",
    "process_pair",
]
