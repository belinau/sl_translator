#!/usr/bin/env python3
"""Ingest a translated book's bibliography into the KG.

Pipeline:
  1. Parse the bibliography from the docx (style-aware)
  2. Match each parsed citation against the TM (bilingual: EN + SL fields)
  3. Create ONE cited_work source_text per citation with:
     - structured fields (type, title, subtitle, container_title, vol/issue, pages, url)
     - publisher info (publisher, place)
     - SL edition info if found (alt_publisher, alt_place, alt_translator)
     - tm_segment_refs: list of (origin, global_idx, lang, confidence) where this citation appears
  4. Wire edges:
     - cited_work --written_by--> author agent (multiple if co-authored)
     - cited_work --edited_by--> editor agent (for book chapters)
     - cited_work --translated_by--> translator agent (for translations)
     - cited_work --published_by--> institution
     - cited_work --cited_in--> containing translated_work
  5. Save KG

Usage:
  python ingest_book_bibliography.py --docx data/books/Skrb_...docx --container-work-id kunst-zivljenje-umetnosti
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from translate_core.tm import TranslationMemory
from translate_core.entity_extraction.bibliography_parser import (
    parse_docx_bibliography,
    ParsedCitation,
    ParsedAuthor,
)
from translate_core.entity_extraction.bilingual_tm_matcher import (
    match_all_citations,
    summarize_matches,
    CitationWithTMRefs,
)
from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.entity_extraction._slug import _slugify



def _agent_id(author: ParsedAuthor) -> str:
    full = f"{author.given} {author.surname}".strip() or author.surname
    return _slugify(full)


def _ensure_agent(kg: KnowledgeGraph, author: ParsedAuthor) -> str:
    aid = _agent_id(author)
    node_key = f"agent:{aid.lower()}"
    if not kg.G.has_node(node_key):
        full = f"{author.given} {author.surname}".strip() or author.surname
        kg.add_agent_node(aid, name=full, role=author.role)
    return aid


def _ensure_institution(
    kg: KnowledgeGraph, name: str, city: str | None, kind: str = "publisher"
) -> str:
    iid = _slugify(name)
    node_key = f"institution:{iid.lower()}"
    if not kg.G.has_node(node_key):
        kg.add_institution_node(iid, name=name, kind=kind, city=city)
    return iid


def _cited_work_id(citation: ParsedCitation) -> str:
    """Stable id for the cited work."""
    surname = citation.primary_author_surname or "anon"
    title_part = (citation.title or "untitled")[:40]
    year = citation.year or ""
    return _slugify(f"{surname}-{title_part}-{year}")


def ingest_citation(
    kg: KnowledgeGraph,
    rec: CitationWithTMRefs,
    container_work_id: str,
) -> str:
    """Create cited_work + edges for one citation. Returns the cited_work id."""
    c = rec.citation
    cwid = _cited_work_id(c)
    src_node = f"source:{cwid.lower()}"

    # Build payload with all structured fields.
    # Avoid `title` key — that's the positional kwarg on add_source_text_node.
    extra = {
        "citation_type": c.citation_type,
        "title_short": c.title,
        "subtitle": c.subtitle,
        "container_title": c.container_title,
        "volume": c.volume,
        "issue": c.issue,
        "pages": c.pages,
        "place": c.place,
        "places": c.places or None,
        "publisher": c.publisher,
        "translator_name": c.translator,
        "url": c.url,
        "accessed": c.accessed,
        "project_type": "cited_work",
        "container_work_id": container_work_id,
        "raw_bibliography_entry": c.raw,
        "tm_segment_refs": rec.tm_segment_refs or None,
        "alt_publishers": rec.alt_publishers or None,
        "notes": c.notes or None,
    }
    extra = {k: v for k, v in extra.items() if v not in (None, [])}

    kg.add_source_text_node(
        cwid,
        title=c.display_title,
        year=c.year,
        **extra,
    )

    # Author edges
    for a in c.authors:
        aid = _ensure_agent(kg, a)
        if not kg.G.has_edge(src_node, f"agent:{aid.lower()}"):
            kg.G.add_edge(
                src_node, f"agent:{aid.lower()}",
                relation="written_by",
            )

    # Editor edges
    for ed in c.editors:
        eid = _ensure_agent(kg, ed)
        if not kg.G.has_edge(src_node, f"agent:{eid.lower()}"):
            kg.G.add_edge(
                src_node, f"agent:{eid.lower()}",
                relation="edited_by",
            )

    # Translator edge (for translation citations or any citation with translator)
    if c.translator:
        # Parse translator name into surname/given
        parts = c.translator.split()
        if len(parts) >= 2:
            given = " ".join(parts[:-1])
            surname = parts[-1]
        else:
            given, surname = "", parts[0]
        tr_author = ParsedAuthor(surname=surname, given=given, role="translator")
        tid = _ensure_agent(kg, tr_author)
        if not kg.G.has_edge(src_node, f"agent:{tid.lower()}"):
            kg.G.add_edge(
                src_node, f"agent:{tid.lower()}",
                relation="translated_by",
            )

    # Publisher edge
    if c.publisher:
        iid = _ensure_institution(kg, c.publisher, c.place, kind="publisher")
        if not kg.G.has_edge(src_node, f"institution:{iid.lower()}"):
            kg.G.add_edge(
                src_node, f"institution:{iid.lower()}",
                relation="published_by",
            )

    # Alt-publisher edges (SL editions etc.)
    for ap in rec.alt_publishers:
        ap_name = ap.get("publisher")
        if not ap_name:
            continue
        iid = _ensure_institution(kg, ap_name, ap.get("city"), kind="publisher")
        if not kg.G.has_edge(src_node, f"institution:{iid.lower()}"):
            kg.G.add_edge(
                src_node, f"institution:{iid.lower()}",
                relation="alt_published_by",
            )

    # Container book (for chapter): create as separate source_text
    if c.citation_type == "book-chapter" and c.container_title:
        cnt_id = _slugify(f"container-{c.container_title[:40]}-{c.year or ''}")
        cnt_node = f"source:{cnt_id.lower()}"
        if not kg.G.has_node(cnt_node):
            kg.add_source_text_node(
                cnt_id,
                title=c.container_title,
                year=c.year,
                project_type="cited_container",
                citation_type="book",
            )
        if not kg.G.has_edge(src_node, cnt_node):
            kg.G.add_edge(src_node, cnt_node, relation="appears_in")

    # cited_in edge → containing translated work
    container_node = f"source:{container_work_id.lower()}"
    if kg.G.has_node(container_node):
        if not kg.G.has_edge(src_node, container_node):
            kg.G.add_edge(src_node, container_node, relation="cited_in")

    return cwid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", type=Path, required=True,
                        help="Path to the translated book .docx with bibliography")
    parser.add_argument("--container-work-id", required=True,
                        help="The translated_work id this bibliography belongs to (e.g. kunst-zivljenje-umetnosti)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse, match, and report — but do NOT write to KG")
    parser.add_argument("--report-path", type=Path, default=Path("data/bibliography_ingest_report.md"),
                        help="Where to write the parsing+matching report")
    args = parser.parse_args()

    print(f"[1/4] Parsing bibliography from {args.docx} …")
    citations = parse_docx_bibliography(str(args.docx))
    print(f"      Parsed {len(citations)} citations")

    print(f"[2/4] Loading TM and matching citations …")
    tm = TranslationMemory()
    matched = match_all_citations(citations, tm.entries)
    summary = summarize_matches(matched)
    print(f"      {summary['citations_with_tm_matches']}/{summary['total_citations']} citations matched to TM "
          f"({summary['total_tm_match_count']} total segment refs)")
    print(f"      Alt-publishers found: {summary['citations_with_alt_publisher']}")

    print(f"[3/4] Writing report to {args.report_path} …")
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(args.report_path, "w", encoding="utf-8") as f:
        f.write(f"# Bibliography ingest report\n\n")
        f.write(f"**Source:** `{args.docx}`\n")
        f.write(f"**Container work:** `{args.container_work_id}`\n\n")
        f.write(f"## Summary\n\n")
        for k, v in summary.items():
            f.write(f"- **{k}:** {v}\n")
        f.write(f"\n## Sample entries\n\n")
        for m in matched[:20]:
            c = m.citation
            a = " + ".join(au.full_name for au in c.authors)
            f.write(f"### [{c.citation_type}] {a} ({c.year})\n\n")
            f.write(f"- **Title:** {c.title}\n")
            if c.subtitle:
                f.write(f"- **Subtitle:** {c.subtitle}\n")
            if c.container_title:
                f.write(f"- **In:** {c.container_title}\n")
            if c.publisher:
                f.write(f"- **Publisher:** {c.publisher}")
                if c.place:
                    f.write(f" ({c.place})")
                f.write("\n")
            if c.translator:
                f.write(f"- **Translator:** {c.translator}\n")
            if c.volume:
                f.write(f"- **Volume/Issue:** {c.volume}/{c.issue or ''}\n")
            if c.pages:
                f.write(f"- **Pages:** {c.pages}\n")
            if c.url:
                f.write(f"- **URL:** {c.url}\n")
            f.write(f"- **TM matches:** {len(m.tm_matches)}\n")
            if m.alt_publishers:
                f.write(f"- **Alt publishers (SL editions found in TM):**\n")
                for ap in m.alt_publishers:
                    f.write(f"  - {ap.get('city')}: {ap.get('publisher')}\n")
            f.write(f"- **Raw:** `{c.raw[:200]}`\n\n")

    if args.dry_run:
        print(f"\n[dry-run] No KG writes performed.")
        return

    print(f"[4/4] Writing to KG …")
    kg = KnowledgeGraph()
    container_node = f"source:{args.container_work_id.lower()}"
    if not kg.G.has_node(container_node):
        print(f"      ERROR: container work {container_node} not found in KG. "
              f"Run the seed pipeline first to create it.")
        sys.exit(1)

    nodes_before = kg.G.number_of_nodes()
    edges_before = kg.G.number_of_edges()
    for rec in matched:
        ingest_citation(kg, rec, args.container_work_id)
    kg.save()
    print(f"      Nodes: {nodes_before} → {kg.G.number_of_nodes()} (+{kg.G.number_of_nodes() - nodes_before})")
    print(f"      Edges: {edges_before} → {kg.G.number_of_edges()} (+{kg.G.number_of_edges() - edges_before})")
    print(f"\n      KG saved.")


if __name__ == "__main__":
    main()
