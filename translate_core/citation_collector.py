# translate_core/citation_collector.py
#
# Citation snippet adapters for multiple source formats (editor segments,
# project segments_meta, MD footnotes, DOCX footnotes, TMX manifests).
#
# extract_and_ingest runs live smol extraction (Ollama via
# entity_extraction.smol_client) on each snippet, builds ontology-compliant
# records (smol_extractor.build_record), scores them and writes via the
# kg_ingest_entities chokepoint (confidence tiers, O-10). When the LLM is
# unreachable, snippets are skipped — the offline batch pipeline
# (working.tmx → run_entity_extraction.py) remains the backstop.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .entity_extraction.citation_types import (
    CitationStyle,
    detect_style,
    is_short_reference,
)
from .entity_extraction.segment_classifier import SegmentClass

log = logging.getLogger("citation_collector")


# ── CitationSnippet ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CitationSnippet:
    """A single citation extracted from any source format.

    This is the universal intermediate representation between source
    format adapters and the typed VL extraction pipeline.
    """
    text: str
    origin: str
    segment_idx: int
    format: str                    # "footnote" | "bibliography" | "endnote"
    style_hint: CitationStyle = CitationStyle.UNKNOWN
    container_work_id: Optional[str] = None
    source_page: Optional[int] = None
    footnote_number: Optional[int] = None
    target_text: Optional[str] = None  # translated side, for bilingual extraction


# ── Ingest report ─────────────────────────────────────────────────────────────

@dataclass
class IngestReport:
    """Counts from a citation extraction + ingest run."""
    extracted: int = 0       # Snippets that entered the pipeline
    classified: int = 0     # Snippets that received a citation type
    verified: int = 0       # Verified citations that passed type checks
    written: int = 0        # Records written directly to KG (confidence ≥ 0.85)
    queued: int = 0        # Records queued for review (0.55 ≤ confidence < 0.85)
    dropped: int = 0        # Records dropped (confidence < 0.55)
    errors: int = 0         # Extraction or parsing errors


# ── Format adapters ───────────────────────────────────────────────────────────

def collect_from_segments_meta(
    project_json: dict,
    container_work_id: Optional[str] = None,
) -> list[CitationSnippet]:
    """Extract CitationSnippets from a project JSON's segments_meta.

    Filters for `bibliography_entry` and `footnote` segment types,
    producing one snippet per qualifying segment.
    """
    segments_meta = project_json.get("segments_meta", [])
    segments = project_json.get("segments", [])
    origin = project_json.get("filename", project_json.get("id", "unknown"))

    snippets: list[CitationSnippet] = []
    for i, sm in enumerate(segments_meta):
        seg_type = sm.get("type", "")
        if seg_type in ("bibliography_entry", "bibliography"):
            text = segments[i].get("source", "") if i < len(segments) else ""
            if text.strip():
                snippets.append(CitationSnippet(
                    text=text,
                    origin=origin,
                    segment_idx=i,
                    format="bibliography",
                    style_hint=detect_style(text),
                    container_work_id=container_work_id,
                    source_page=sm.get("page_number"),
                ))
        elif seg_type == "footnote":
            text = segments[i].get("source", "") if i < len(segments) else ""
            if text.strip():
                # Try to extract footnote number from text or metadata
                fn_num = sm.get("footnote_number")
                if not fn_num:
                    m = re.match(r"^\[?(\d{1,3})[\]\.):]\s*", text)
                    fn_num = int(m.group(1)) if m else None
                snippets.append(CitationSnippet(
                    text=text,
                    origin=origin,
                    segment_idx=i,
                    format="footnote",
                    style_hint=detect_style(text),
                    container_work_id=container_work_id,
                    source_page=sm.get("page_number"),
                    footnote_number=fn_num,
                ))

    return snippets


# ── TMX manifest sidecar ──────────────────────────────────────────────────────

def read_tmx_manifest(tmx_path: str) -> Optional[dict]:
    """Read the TMX manifest sidecar if it exists.

    The sidecar is written by the document-pair pipeline (Phase 4) as
    ``<tmx_name>.manifest.json`` next to the TMX file. It contains:
        {
            "container_work_id": "source:...",
            "container_type": "book_translation",
            "source_lang": "en",
            "target_lang": "sl",
            "created_at": "2025-..."
        }
    Returns the parsed dict or None if the sidecar does not exist.
    """
    import json
    manifest_path = Path(tmx_path).with_suffix(".manifest.json")
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Failed to read TMX manifest at %s", manifest_path)
        return None


def write_tmx_manifest(
    tmx_path: str,
    container_work_id: str,
    container_type: str,
    source_lang: str = "en",
    target_lang: str = "sl",
) -> None:
    """Write a TMX manifest sidecar for the given TMX path.

    Called by the document-pair pipeline (Phase 4) after creating a TMX.
    """
    import json
    from datetime import datetime, timezone

    manifest = {
        "container_work_id": container_work_id,
        "container_type": container_type,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = Path(tmx_path).with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def collect_from_tmx(
    tmx_path: str,
    container_work_id: Optional[str] = None,
) -> list[CitationSnippet]:
    """Extract CitationSnippets from a TMX translation memory.

    Checks for a ``<tmx_name>.manifest.json`` sidecar (written by the
    document-pair pipeline). When found, uses the sidecar's
    ``container_work_id`` if none was passed explicitly.

    Only bibliography entries and footnotes are extracted.
    """
    from .tm import TranslationMemory

    # Sidecar: prefer explicit arg, fall back to manifest
    if container_work_id is None:
        manifest = read_tmx_manifest(tmx_path)
        if manifest:
            container_work_id = manifest.get("container_work_id")
            log.debug("collect_from_tmx: loaded container_work_id=%s from manifest",
                      container_work_id)

    tm = TranslationMemory(Path(tmx_path).parent)
    # Reload with the specific file
    tm.entries = []
    tm._load_tmx(Path(tmx_path))

    from .entity_extraction.segment_classifier import classify_segments

    snippets: list[CitationSnippet] = []
    origin = Path(tmx_path).stem

    for i, entry in enumerate(tm.entries):
        src = entry.get("source", "")
        if not src.strip():
            continue
        style = detect_style(src)
        labels = classify_segments([{"source": src, "target": entry.get("target", "")}])
        if labels and labels[0].klass in (
            SegmentClass.BIBLIOGRAPHY_ENTRY,
            SegmentClass.FOOTNOTE,
        ):
            fmt = "bibliography" if labels[0].klass == SegmentClass.BIBLIOGRAPHY_ENTRY else "footnote"
            snippets.append(CitationSnippet(
                text=src,
                origin=origin,
                segment_idx=i,
                format=fmt,
                style_hint=style,
                container_work_id=container_work_id,
            ))

    return snippets


def collect_from_docx(
    docx_path: str,
    container_work_id: Optional[str] = None,
) -> list[CitationSnippet]:
    """Extract CitationSnippets from a DOCX file's footnotes.

    Wraps the existing docx_footnote_parser.
    """
    from .entity_extraction.docx_footnote_parser import parse_docx_footnotes

    try:
        parsed = parse_docx_footnotes(docx_path)
    except Exception:
        log.warning("Failed to parse DOCX footnotes from %s", docx_path)
        return []

    origin = Path(docx_path).stem
    snippets: list[CitationSnippet] = []

    for fn in parsed:
        if fn.raw.strip():
            style = detect_style(fn.raw)
            snippets.append(CitationSnippet(
                text=fn.raw,
                origin=origin,
                segment_idx=fn.footnote_number or 0,
                format="footnote",
                style_hint=style,
                container_work_id=container_work_id,
                footnote_number=fn.footnote_number,
            ))

    return snippets


def collect_from_md(
    md_path: str,
    container_work_id: Optional[str] = None,
) -> list[CitationSnippet]:
    """Extract CitationSnippets from a Markdown file's footnote definitions.

    Matches `[^N]: definition` blocks.
    """
    text = Path(md_path).read_text(encoding="utf-8")
    origin = Path(md_path).stem

    # Regex: [^N]: definition text (possibly multi-line until next footnote or blank)
    fn_pattern = re.compile(
        r"\[\^(\d+)\]:\s*(.+?)(?=\n\[\^|\n\n|\Z)",
        re.DOTALL,
    )

    snippets: list[CitationSnippet] = []
    for m in fn_pattern.finditer(text):
        fn_num = int(m.group(1))
        fn_text = m.group(2).strip()
        if fn_text:
            style = detect_style(fn_text)
            snippets.append(CitationSnippet(
                text=fn_text,
                origin=origin,
                segment_idx=fn_num,
                format="footnote",
                style_hint=style,
                container_work_id=container_work_id,
                footnote_number=fn_num,
            ))

    # Also collect bibliography entries (lines starting with a lastname-first pattern
    # at the start of a line, commonly found in bibliography sections)
    lines = text.split("\n")
    in_biblio = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Detect bibliography section header
        if re.match(r"^#\s*(bibliography|works?\s+cited|references|literatura|viri)", stripped, re.IGNORECASE):
            in_biblio = True
            continue
        if stripped.startswith("#") and in_biblio:
            in_biblio = False
            continue
        if in_biblio and len(stripped) > 20:
            style = detect_style(stripped)
            if style in (CitationStyle.CHICAGO_EN, CitationStyle.CHICAGO_SL):
                snippets.append(CitationSnippet(
                    text=stripped,
                    origin=origin,
                    segment_idx=i,
                    format="bibliography",
                    style_hint=style,
                    container_work_id=container_work_id,
                ))

    return snippets


def collect_from_editor_segment(
    segment_text: str,
    segments_meta_entry: dict,
    project_id: str,
    container_work_id: Optional[str] = None,
    target_text: Optional[str] = None,
    lang_pair: Optional[str] = None,
) -> Optional[CitationSnippet]:
    """Build a CitationSnippet from a single confirmed editor segment.

    Used when the user confirms a segment in the editor, triggering
    immediate live extraction. Citation-bearing types map to their format;
    everything else is "body" (smol extracts agents/works/concepts from
    body text too, matching the offline batch pipeline).

    ``lang_pair`` ("en-sl") is embedded into the snippet origin so
    smol_extractor._detect_source_lang recovers the language combo.
    """
    if not segment_text.strip():
        return None

    seg_type = segments_meta_entry.get("type", "")
    if seg_type == "footnote":
        fmt = "footnote"
    elif seg_type in ("bibliography", "bibliography_entry"):
        fmt = "bibliography"
    elif seg_type == "endnote":
        fmt = "endnote"
    else:
        fmt = "body"

    origin = f"editor_{lang_pair}_{project_id}" if lang_pair else project_id
    style = detect_style(segment_text)
    fn_num = segments_meta_entry.get("footnote_number")
    page = segments_meta_entry.get("page_number")

    return CitationSnippet(
        text=segment_text,
        origin=origin,
        segment_idx=segments_meta_entry.get("index", 0),
        format=fmt,
        style_hint=style,
        container_work_id=container_work_id,
        source_page=page,
        footnote_number=fn_num,
        target_text=target_text,
    )


# ── Pipeline orchestrator ──────────────────────────────────────────────────────

def extract_and_ingest(
    snippets: Iterable[CitationSnippet],
    kg,  # KnowledgeGraph instance
    review_path: str = "data/extraction_review.json",
    dropped_path: str = "data/extraction_dropped.jsonl",
    extractor: Optional[Callable[..., Optional[list[dict]]]] = None,
) -> IngestReport:
    """Run live smol extraction + KG ingest on a batch of snippets.

    ``extractor`` has the smol_client.extract_entities contract:
    ``(src, tgt, origin, container_work_id) -> list[entity] | None`` where
    None means "LLM unavailable" (counted as error; the offline batch
    pipeline picks the segment up later from working.tmx). Defaults to the
    live Ollama client.

    Records flow through the same chokepoint as the batch pipeline:
    score_all → dedup_records → write_to_kg (O-10 confidence tiers).

    Returns an IngestReport with counts per phase.
    """
    from .entity_extraction.smol_extractor import _detect_source_lang, build_record
    from .kg_ingest_entities import write_to_kg, score_all, dedup_records

    if extractor is None:
        from .entity_extraction.smol_client import extract_entities
        extractor = extract_entities

    report = IngestReport()
    all_records: list[dict] = []

    for snippet in snippets:
        report.extracted += 1
        text = snippet.text

        # Skip short references — they need back-reference resolution
        if is_short_reference(text):
            report.dropped += 1
            continue

        # Skip very short segments (noise)
        if len(text.strip()) < 15:
            report.dropped += 1
            continue

        entities = extractor(
            src=text,
            tgt=snippet.target_text or "",
            origin=snippet.origin,
            container_work_id=snippet.container_work_id or "",
        )
        if entities is None:
            # LLM unavailable — leave for the offline batch pipeline.
            report.errors += 1
            continue

        src_lang, tgt_lang = _detect_source_lang(snippet.origin)
        for ent in entities:
            rec = build_record(
                ent,
                origin=snippet.origin,
                seg_idx=snippet.segment_idx,
                container_work_id=snippet.container_work_id or "",
                src_lang=src_lang,
                tgt_lang=tgt_lang,
            )
            if rec is not None:
                all_records.append(rec)

    # Score → dedup → re-score (dedup merges rebuild records without tier),
    # then write through the O-10 chokepoint — same sequence as the batch
    # pipeline (run_entity_extraction.ingest_extractions).
    deduped = dedup_records(score_all(all_records))
    re_scored = score_all(deduped)

    # Write to KG or queue for review
    from pathlib import Path as _Path
    stats = write_to_kg(
        kg,
        re_scored,
        review_path=_Path(review_path),
        dropped_path=_Path(dropped_path),
    )

    report.written = stats.direct_write
    report.queued = stats.review_queued
    report.dropped += stats.dropped

    # write_to_kg already wires cited_in for every direct-write deferred kind;
    # no post-loop wiring is needed.
    return report