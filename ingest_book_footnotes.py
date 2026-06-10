#!/usr/bin/env python3
"""Ingest a translated book's footnotes (markdown export) into the KG.

For books like Založnik's Zavzemanje prostora where references live in
footnotes (no separate end-bibliography), the markdown export has the
canonical structure as `[^N]: citation text`.

Pipeline:
  1. Parse all footnotes from the .md file
  2. Resolve Ibid → previous full citation
  3. Bilingual TM matching with page-tail and URL signals
  4. Ingest one cited_work per unique citation (multiple footnotes referring
     to the same work collapse to one cited_work node)
  5. cited_in edge → containing translated_work
"""

import logging
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from translate_core.tm import TranslationMemory
from translate_core.entity_extraction.bibliography_parser import (
    ParsedAuthor,
    ParsedCitation,
)
from translate_core.entity_extraction.footnote_parser import (
    parse_markdown_footnotes,
    FootnoteParsed,
)
from translate_core.entity_extraction.bilingual_tm_matcher import (
    match_citation_against_tm,
    summarize_matches,
    CitationWithTMRefs,
)
from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.entity_extraction._slug import _slugify
from translate_core.entity_extraction.ingest_helpers import (
    ensure_agent,
    ensure_institution,
    cited_work_id as _cited_work_id,
)


log = logging.getLogger(__name__)

def _cited_work_id(citation: ParsedCitation) -> str:
    return _cited_work_id(
        citation.primary_author_surname or "anon",
        (citation.title or citation.container_title or "untitled")[:40],
        citation.year or "",
    )


def _is_short_form(citation: ParsedCitation) -> bool:
    """True if citation is just `Lastname, page` shortform (no title/pub/year)."""
    return (
        citation.citation_type == "ibid"
        and not citation.title
        and not citation.publisher
    )


def _resolve_ibid_references(
    footnotes: List[FootnoteParsed],
) -> List[ParsedCitation]:
    """Walk footnotes in order. Replace Ibid citations with the previous full
    citation, copying its identity but keeping the new page number.

    Short-form references (`Lastname, str. N`) where the surname matches a
    previously-cited work are merged with that work's identity.
    """
    out: List[ParsedCitation] = []
    last_full: Optional[ParsedCitation] = None
    surname_to_last: Dict[str, ParsedCitation] = {}

    for fn in footnotes:
        for cit in fn.citations:
            if cit.citation_type == "ibid" and not cit.title and not cit.authors:
                # Pure Ibid — refer to last full
                if last_full:
                    inherited = ParsedCitation(
                        raw=cit.raw,
                        authors=list(last_full.authors),
                        year=last_full.year,
                        title=last_full.title,
                        subtitle=last_full.subtitle,
                        container_title=last_full.container_title,
                        place=last_full.place,
                        publisher=last_full.publisher,
                        translator=last_full.translator,
                        url=last_full.url,
                        citation_type=last_full.citation_type,
                        pages=cit.pages,  # NEW page from the ibid line
                    )
                    inherited.notes.append(f"resolved_from_ibid_to_fn_{fn.footnote_number}")
                    out.append(inherited)
                continue

            # Short-form `Lastname, str. N` — match against previous full
            if (cit.authors and len(cit.authors) == 1
                and not cit.title and not cit.year and not cit.publisher):
                surname = cit.authors[0].surname.lower()
                prev = surname_to_last.get(surname)
                if prev:
                    inherited = ParsedCitation(
                        raw=cit.raw,
                        authors=list(prev.authors),
                        year=prev.year,
                        title=prev.title,
                        subtitle=prev.subtitle,
                        container_title=prev.container_title,
                        place=prev.place,
                        publisher=prev.publisher,
                        translator=prev.translator,
                        url=prev.url,
                        citation_type=prev.citation_type,
                        pages=cit.pages,
                    )
                    inherited.notes.append(f"resolved_shortform_in_fn_{fn.footnote_number}")
                    out.append(inherited)
                    continue
                # No match found — keep as-is, downstream may skip
                out.append(cit)
                continue

            # Full citation — store as last_full and per-surname
            if cit.authors and (cit.title or cit.container_title or cit.publisher):
                last_full = cit
                for a in cit.authors:
                    surname_to_last[a.surname.lower()] = cit
            out.append(cit)
    return out


def _ensure_agent(kg: KnowledgeGraph, author: ParsedAuthor) -> str:
    full = f"{author.given} {author.surname}".strip() or author.surname
    return ensure_agent(kg, full, role=author.role)


def _ensure_institution(
    kg: KnowledgeGraph, name: str, city: Optional[str], kind: str = "publisher"
) -> str:
    return ensure_institution(kg, name, city, kind)


def ingest_one(
    kg: KnowledgeGraph,
    citation: ParsedCitation,
    tm_match: CitationWithTMRefs,
    container_work_id: str,
    footnote_numbers: List[int],
) -> str:
    """Create cited_work + edges. Returns the cited_work id (may be reused
    if same work cited in multiple footnotes)."""
    cwid = _cited_work_id(citation)
    src_node = f"source:{cwid.lower()}"

    extra = {
        "citation_type": citation.citation_type,
        "title_short": citation.title,
        "subtitle": citation.subtitle,
        "container_title": citation.container_title,
        "volume": citation.volume,
        "issue": citation.issue,
        "pages": citation.pages,
        "place": citation.place,
        "publisher": citation.publisher,
        "translator_name": citation.translator,
        "url": citation.url,
        "project_type": "cited_work",
        "container_work_id": container_work_id,
        "footnote_numbers": footnote_numbers,
        "raw_citation": citation.raw[:500],
        "tm_segment_refs": tm_match.tm_segment_refs or None,
        "alt_publishers": tm_match.alt_publishers or None,
    }
    extra = {k: v for k, v in extra.items() if v not in (None, [])}

    if kg.G.has_node(src_node):
        # Already exists — accumulate footnote numbers + tm refs
        existing = kg.G.nodes[src_node]
        existing_fns = set(existing.get("footnote_numbers", []))
        existing_fns.update(footnote_numbers)
        existing["footnote_numbers"] = sorted(existing_fns)
        # Merge tm refs
        existing_refs = existing.get("tm_segment_refs", [])
        new_refs = tm_match.tm_segment_refs or []
        seen_ids = {r.get("global_idx") for r in existing_refs}
        for r in new_refs:
            if r.get("global_idx") not in seen_ids:
                existing_refs.append(r)
        existing["tm_segment_refs"] = existing_refs
    else:
        kg.add_source_text_node(
            cwid,
            title=citation.display_title,
            year=citation.year,
            **extra,
        )

    # Author edges
    for a in citation.authors:
        aid = _ensure_agent(kg, a)
        agent_node = f"agent:{aid.lower()}"
        if not kg.G.has_edge(src_node, agent_node):
            kg.G.add_edge(src_node, agent_node, relation="written_by")

    # Editor edges
    for ed in citation.editors:
        eid = _ensure_agent(kg, ed)
        agent_node = f"agent:{eid.lower()}"
        if not kg.G.has_edge(src_node, agent_node):
            kg.G.add_edge(src_node, agent_node, relation="edited_by")

    # Translator
    if citation.translator:
        parts = citation.translator.split()
        if parts:
            tr = ParsedAuthor(
                surname=parts[-1],
                given=" ".join(parts[:-1]),
                role="translator",
            )
            tid = _ensure_agent(kg, tr)
            agent_node = f"agent:{tid.lower()}"
            if not kg.G.has_edge(src_node, agent_node):
                kg.G.add_edge(src_node, agent_node, relation="translated_by")

    # Publisher
    if citation.publisher:
        iid = _ensure_institution(kg, citation.publisher, citation.place, "publisher")
        inst_node = f"institution:{iid.lower()}"
        if not kg.G.has_edge(src_node, inst_node):
            kg.G.add_edge(src_node, inst_node, relation="published_by")

    # cited_in → container book
    container_node = f"source:{container_work_id.lower()}"
    if kg.G.has_node(container_node):
        if not kg.G.has_edge(src_node, container_node):
            kg.G.add_edge(src_node, container_node, relation="cited_in")

    return cwid


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--md", type=Path, required=True,
                        help="Path to the markdown export of the translated book")
    parser.add_argument("--container-work-id", required=True,
                        help="Translated work id this book belongs to")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-path", type=Path,
                        default=Path("data/footnote_ingest_report.md"))
    args = parser.parse_args()

    log.info(f"Parsing footnotes from {args.md} …")
    footnotes = parse_markdown_footnotes(str(args.md))
    log.info(f"      {len(footnotes)} footnotes, {sum(len(f.citations) for f in footnotes)} raw citations")

    log.info(f"Resolving Ibid + short-form references …")
    # Map citation idx → list of footnote numbers it appeared in
    # We need this so each cited_work knows its footnote provenance
    resolved: List[ParsedCitation] = []
    fn_numbers_per_cit: List[List[int]] = []
    last_full: Optional[ParsedCitation] = None
    surname_to_last: Dict[str, ParsedCitation] = {}

    for fn in footnotes:
        for cit in fn.citations:
            if cit.citation_type == "ibid" and not cit.title and not cit.authors and last_full:
                # Resolve to last full
                inherited = ParsedCitation(
                    raw=cit.raw,
                    authors=list(last_full.authors),
                    year=last_full.year,
                    title=last_full.title,
                    subtitle=last_full.subtitle,
                    container_title=last_full.container_title,
                    place=last_full.place,
                    publisher=last_full.publisher,
                    translator=last_full.translator,
                    url=last_full.url,
                    citation_type=last_full.citation_type,
                    pages=cit.pages,
                )
                resolved.append(inherited)
                fn_numbers_per_cit.append([fn.footnote_number])
                continue
            if (cit.authors and len(cit.authors) == 1
                and not cit.title and not cit.year and not cit.publisher):
                surname = cit.authors[0].surname.lower()
                prev = surname_to_last.get(surname)
                if prev:
                    inherited = ParsedCitation(
                        raw=cit.raw,
                        authors=list(prev.authors),
                        year=prev.year,
                        title=prev.title,
                        subtitle=prev.subtitle,
                        container_title=prev.container_title,
                        place=prev.place,
                        publisher=prev.publisher,
                        translator=prev.translator,
                        url=prev.url,
                        citation_type=prev.citation_type,
                        pages=cit.pages,
                    )
                    resolved.append(inherited)
                    fn_numbers_per_cit.append([fn.footnote_number])
                    continue
            if cit.authors and (cit.title or cit.container_title or cit.publisher):
                last_full = cit
                for a in cit.authors:
                    surname_to_last[a.surname.lower()] = cit
            resolved.append(cit)
            fn_numbers_per_cit.append([fn.footnote_number])

    log.info(f"      {len(resolved)} citations after Ibid resolution")

    log.info(f"Bilingual TM matching (with page-tail & URL signals) …")
    tm = TranslationMemory()
    matched: List[CitationWithTMRefs] = []
    for cit in resolved:
        matched.append(match_citation_against_tm(cit, tm.entries))
    summary = summarize_matches(matched)
    for k, v in summary.items():
        log.info(f"      {k}: {v}")

    # Collapse same-work citations: same cwid = same node
    cwid_to_fn_numbers: Dict[str, List[int]] = {}
    cwid_to_match: Dict[str, CitationWithTMRefs] = {}
    cwid_to_citation: Dict[str, ParsedCitation] = {}
    for i, m in enumerate(matched):
        cwid = _cited_work_id(m.citation)
        cwid_to_fn_numbers.setdefault(cwid, []).extend(fn_numbers_per_cit[i])
        if cwid not in cwid_to_match or len(m.tm_matches) > len(cwid_to_match[cwid].tm_matches):
            cwid_to_match[cwid] = m
            cwid_to_citation[cwid] = m.citation
    log.info(f"      {len(cwid_to_match)} unique cited_works (collapsed from {len(matched)} footnote citations)")

    if args.dry_run:
        # Write report
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(args.report_path, "w", encoding="utf-8") as f:
            f.write(f"# Footnote ingest report\n\n")
            f.write(f"**Source:** `{args.md}`\n")
            f.write(f"**Container work:** `{args.container_work_id}`\n\n")
            f.write(f"## Summary\n\n")
            f.write(f"- Footnotes: {len(footnotes)}\n")
            f.write(f"- Raw citations: {sum(len(fn.citations) for fn in footnotes)}\n")
            f.write(f"- After Ibid resolution: {len(resolved)}\n")
            f.write(f"- Unique cited_works: {len(cwid_to_match)}\n")
            f.write(f"- With TM matches: {summary['citations_with_tm_matches']}\n")
            f.write(f"- Total TM segment refs: {summary['total_tm_match_count']}\n\n")
            f.write(f"## Sample (top 20 by TM match count)\n\n")
            top = sorted(cwid_to_match.values(), key=lambda m: -len(m.tm_matches))[:20]
            for m in top:
                c = m.citation
                a = " + ".join(au.full_name for au in c.authors) if c.authors else "(no author)"
                f.write(f"### [{c.citation_type}] {a} ({c.year})\n\n")
                if c.title:
                    f.write(f"- title: {c.title}\n")
                if c.subtitle:
                    f.write(f"- subtitle: {c.subtitle}\n")
                if c.publisher:
                    f.write(f"- publisher: {c.place}: {c.publisher}\n")
                if c.url:
                    f.write(f"- url: {c.url}\n")
                f.write(f"- footnote numbers: {sorted(set(cwid_to_fn_numbers[_cited_work_id(c)]))[:10]}\n")
                f.write(f"- tm matches: {len(m.tm_matches)}\n")
                f.write(f"\n")
        log.info(f"\nReport written to {args.report_path}")
        return

    log.info(f"Writing to KG …")
    kg = KnowledgeGraph()
    container_node = f"source:{args.container_work_id.lower()}"
    if not kg.G.has_node(container_node):
        log.error(f"      ERROR: container {container_node} not in KG")
        sys.exit(1)
    nb, eb = kg.G.number_of_nodes(), kg.G.number_of_edges()
    for cwid, m in cwid_to_match.items():
        fns = sorted(set(cwid_to_fn_numbers[cwid]))
        ingest_one(kg, cwid_to_citation[cwid], m, args.container_work_id, fns)
    kg.save()
    log.info(f"      Nodes: {nb} → {kg.G.number_of_nodes()} (+{kg.G.number_of_nodes() - nb})")
    log.info(f"      Edges: {eb} → {kg.G.number_of_edges()} (+{kg.G.number_of_edges() - eb})")


if __name__ == "__main__":
    main()
