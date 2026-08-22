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
from typing import TYPE_CHECKING, Callable, Optional
from xml.sax.saxutils import escape as _xml_escape

from .citation_collector import (
    CitationSnippet,
    IngestReport,
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
    n_body_pairs: int = 0
    n_bilingual: int = 0
    n_en_only: int = 0
    n_sl_only: int = 0
    ingest_report: Optional[IngestReport] = None


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
        "cited_id": _slugify(f"{surname}-{title}".strip("-") or f"unresolved-{snippet.origin}-{snippet.segment_idx}"),
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

# Body-alignment emits the FULL literary body — every paragraph, no cap.
# A literary translation's value is the complete text in the TM; abridging
# here would defeat the entire point of the aligner.


def _body_paragraphs(side: SideParsed) -> list[str]:
    """Body paragraphs for one side, with citation matter stripped.

    Removes footnote definition blocks (``[^N]: …``, the citation text that
    ``_extract_footnotes_from_md`` already harvests) and the bibliography
    section (``# Bibliography`` / ``# Literatura`` / ``# Viri`` / … to the
    next heading or EOF) so the remaining paragraphs are the literary body
    the translator actually translated — not the citations, which are
    paired separately as footnote TUs and routed to the KG.

    Inline footnote reference markers (``… text[^1] more …``) are kept:
    they are part of the source text the translator worked against and
    belong in the TM pair.
    """
    md = side.markdown or ""
    # Drop footnote definition blocks (single + multi-line).
    md = _FN_DEF_BLOCK_RE.sub("", md)
    # Drop the bibliography/works-cited section: from its header to the
    # next heading or end of document.
    out_lines: list[str] = []
    in_biblio = False
    for line in md.splitlines():
        stripped = line.strip()
        if _BIBLIO_HEADER_RE.match(stripped):
            in_biblio = True
            continue
        if in_biblio and stripped.startswith("#"):
            in_biblio = False
        if in_biblio:
            continue
        out_lines.append(line)
    body_md = "\n".join(out_lines)
    from .book_outline import split_paragraphs


    # Keep each paragraph whole as one TU — do NOT sub-split at the
    # 700-char sentence boundary. Sub-splitting would split an EN
    # paragraph into N chunks and its SL counterpart into a different
    # number, and positional 1:1 alignment would then pair the wrong
    # sentences. Paragraph boundaries (blank lines / structural lines)
    # are the reliable alignment unit for a faithful literary translation.
    paras = split_paragraphs(body_md, max_chars=10**9)
    return [p.strip() for p in paras if p.strip()]


def _body_pair_count(en_side: SideParsed, sl_side: SideParsed) -> int:
    """Count the body TUs the no-drop alignment would emit, without writing.

    Mirrors ``_build_tmx``'s Pass-1 counting (positional 1:1 for the
    overlapping prefix + excess paragraphs from the longer side as
    language-only TUs). Used by ``process_pair``'s dry-run path so the
    Preview shows the real body-pair count instead of 0 — the TMX file is
    only written on the non-dry-run path, but the count is free to compute.
    """
    en_body = _body_paragraphs(en_side)
    sl_body = _body_paragraphs(sl_side)
    n_overlap = min(len(en_body), len(sl_body))
    n = sum(1 for i in range(n_overlap) if en_body[i].strip() and sl_body[i].strip())
    n += sum(1 for i in range(n_overlap, len(en_body)) if en_body[i].strip())
    n += sum(1 for i in range(n_overlap, len(sl_body)) if sl_body[i].strip())
    return n


def _build_tmx(
    en_side: SideParsed,
    sl_side: SideParsed,
    container_work_id: str,
    output_path: Path,
) -> tuple[Path, int, int]:
    """Write a TMX aligning the FULL bilingual document pair.

    Two alignment passes, both positional (1:1, capped at the smaller side):
      1. **Body** — the literary text the translator translated. Every
         paragraph of the original is paired with the same-position
         paragraph of the translation. Footnote-definition blocks and the
         bibliography section are stripped first (they are citation
         matter, paired separately in pass 2 and routed to the KG).
      2. **Footnotes** — the translated footnote text, paired by footnote
         position.

    The full body goes in — no abridging. The manifest sidecar records the
    container + lang pair so ``collect_from_tmx`` can later recover
    ``container_work_id`` without being told.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    en_body = _body_paragraphs(en_side)
    sl_body = _body_paragraphs(sl_side)
    en_fn = en_side.footnotes
    sl_fn = sl_side.footnotes

    parts: list[str] = [_TMX_HEADER.format(srclang=en_side.lang)]

    # ── Pass 1: full body. Positional 1:1 for the overlapping prefix, then
    #    the EXCESS paragraphs from the longer side appended as unpaired TUs
    #    (other side empty). NOTHING is dropped — the full bilingual document
    #    goes in. A faithful literary translation preserves paragraph order,
    #    so the prefix aligns correctly; trailing extras (e.g. a translator
    #    credit line absent from the original) land as language-only TUs.
    n_overlap = min(len(en_body), len(sl_body))
    n_body = 0
    for i in range(n_overlap):
        en_text = en_body[i]
        sl_text = sl_body[i]
        if not en_text.strip() or not sl_text.strip():
            continue
        parts.append(_tu_xml(en_side.lang, en_text, sl_side.lang, sl_text))
        n_body += 1
    # Excess from the longer side — emitted, not dropped (no abridging).
    for i in range(n_overlap, len(en_body)):
        if not en_body[i].strip():
            continue
        parts.append(_tu_xml(en_side.lang, en_body[i], sl_side.lang, ""))
        n_body += 1
    for i in range(n_overlap, len(sl_body)):
        if not sl_body[i].strip():
            continue
        parts.append(_tu_xml(en_side.lang, "", sl_side.lang, sl_body[i]))
        n_body += 1
    if len(en_body) != len(sl_body):
        log.warning(
            "Body paragraph counts differ (en=%d sl=%d); paired the "
            "overlapping %d positionally and emitted the %d excess "
            "paragraph(s) as language-only TUs (nothing dropped). Review "
            "the TMX: a mid-document structural divergence (merged/split "
            "paragraphs) will misalign from the divergence point onward.",
            len(en_body), len(sl_body), n_overlap,
            abs(len(en_body) - len(sl_body)),
        )

    # ── Pass 2: footnotes, paired by footnote position ─────────────────
    n_fn = min(len(en_fn), len(sl_fn))
    for i in range(n_fn):
        en_text = en_fn[i]["text"]
        sl_text = sl_fn[i]["text"]
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
        "TMX written: %s (%d body + %d footnote TUs, container=%s)",
        output_path, n_body, n_fn, container_work_id,
    )
    return output_path, n_body, n_fn

# ── Orchestrator ─────────────────────────────────────────────────────────────
def _bilingual_snippet_for_match(
    match: CitationMatch,
    en_side: SideParsed,
    sl_side: SideParsed,
    container_work_id: str,
) -> CitationSnippet:
    """Build a bilingual CitationSnippet from one matched citation pair.

    Mirrors ``collect_from_editor_segment``'s bilingual shape and origin
    convention so the snippet flows through ``extract_and_ingest`` exactly
    like an editor confirm. ``origin`` embeds the language pair
    (``docpair_en-sl_<container>``) so ``smol_extractor._detect_source_lang``
    recovers it; ``target_text`` carries the SL side → smol does bilingual
    extraction. Only bilingual matches (``sl_record is not None``) produce
    a snippet — EN-only citations stay in ``unmatched_en`` for the caller.
    """
    en_payload = (match.en_record or {}).get("payload") or {}
    sl_payload = (match.sl_record or {}).get("payload") or {}
    text = (en_payload.get("raw_text") or "").strip()
    target = (sl_payload.get("raw_text") or "").strip()
    fn_num = ((match.en_record or {}).get("source") or {}).get("footnote_number")
    fmt = "footnote" if fn_num is not None else "bibliography"
    origin = f"docpair_{en_side.lang}-{sl_side.lang}_{container_work_id}"
    return CitationSnippet(
        text=text,
        origin=origin,
        segment_idx=fn_num if isinstance(fn_num, int) else 0,
        format=fmt,
        container_work_id=container_work_id,
        footnote_number=fn_num if isinstance(fn_num, int) else None,
        target_text=target,
    )


# ── Orchestrator ─────────────────────────────────────────────────────────────
def process_pair(
    en_doc_path: Path,
    sl_doc_path: Path,
    container_work_id: str,
    kg: KnowledgeGraph,
    *,
    dry_run: bool = False,
    extractor: Optional[Callable[..., Optional[list[dict]]]] = None,
) -> PairResult:
    """Process a matched EN/SL document pair end-to-end.

    The matching stage stays heuristic (its stated purpose: title text
    strong enough to pair EN against SL). Only the **KG submit** is
    modernized — matched pairs are routed through
    ``citation_collector.extract_and_ingest`` (the same pipeline the
    translation editor uses on confirm), so records flow through the
    smol extractor → confidence tiers (O-10) → dedup → review queue.

    Steps:
        1. Parse both sides → SideParsed (markdown + footnotes).
        2. Collect CitationSnippets from each side.
        3. Build minimal "matching records" via the surname/title heuristic.
        4. Match EN ↔ SL by (author_surname, title) token overlap.
        5. Build bilingual CitationSnippets from matched pairs and run
           ``extract_and_ingest`` once (writes KG entities + review queue,
           wires ``cited_in``→container via ``write_to_kg``).
        6. Emit the TMX: full body (paragraph-aligned) + footnote pairs.

    ``extractor`` has the ``smol_client.extract_entities`` contract; when
    None the live Ollama client is used. Tests inject a fake. When the LLM
    is unreachable, snippets count as ``errors`` and are left for the
    offline batch pipeline (``run_entity_extraction.py``); the TMX is
    still written.

    ``container_work_id`` may be passed bare (``cesta-…``) or with the
    ``source:`` prefix; it is normalised to the bare slug form so the
    smol pipeline's container lookup (``write_to_kg`` → ``_route_record``
    matches against bare lowercased slugs) and ``link_cited_in`` both
    resolve it. This mirrors the editor confirm path, which passes the
    bare ``_slugify_project`` slug as ``container_work_id``.
    """
    # Normalise to the bare slug form the smol pipeline expects.
    container_work_id = _slugify(container_work_id.removeprefix("source:"))
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
    ingest_report: Optional[IngestReport] = None
    n_body_pairs = 0
    if not dry_run:
        from .citation_collector import extract_and_ingest

        pair_snippets = [
            _bilingual_snippet_for_match(m, en_side, sl_side, container_work_id)
            for m in matches
            if m.sl_record is not None
        ]
        # Empty batch → all-zero IngestReport (no smol calls, safe).
        # Ollama down → per-snippet errors; records left for the offline batch.
        ingest_report = extract_and_ingest(pair_snippets, kg, extractor=extractor)

        # Emit TMX into the TM store (config.TM_DIR), named after the
        # container work slug. TranslationMemory() globs data/tm/*.tmx on
        # init, so the aligned pairs are picked up by fuzzy lookup
        # automatically — no separate import step. Named after the text
        # (e.g. lumerai-the-mother-snake.tmx), not the source filename.
        import config as _config

        tmx_path = _config.TM_DIR / f"{container_work_id}.tmx"
        try:
            tmx_path, n_body_pairs, _ = _build_tmx(en_side, sl_side, container_work_id, tmx_path)
        except Exception:
            log.exception("Failed to build TMX at %s", tmx_path)
            tmx_path = None
    else:
        # Dry run: don't write the TMX, but still report the body-pair count
        # so Preview shows the real alignment size instead of 0.
        n_body_pairs = _body_pair_count(en_side, sl_side)

    result = PairResult(
        container_work_id=container_work_id,
        citation_matches=matches,
        unmatched_en=unmatched_en,
        unmatched_sl=unmatched_sl,
        tmx_path=tmx_path,
        n_body_pairs=n_body_pairs,
        n_bilingual=bilingual,
        n_en_only=en_only,
        n_sl_only=sl_only,
        ingest_report=ingest_report,
    )
    log.info(
        "process_pair: container=%s bilingual=%d en_only=%d sl_only=%d",
        container_work_id,
        bilingual,
        en_only,
        sl_only,
    )
    if ingest_report is not None:
        log.info(
            "process_pair: ingest written=%d queued=%d dropped=%d errors=%d",
            ingest_report.written,
            ingest_report.queued,
            ingest_report.dropped,
            ingest_report.errors,
        )
    return result


__all__ = [
    "SideParsed",
    "CitationMatch",
    "PairResult",
    "parse_side",
    "process_pair",
]
