#!/usr/bin/env python3
"""Wire curator-validated performances/artworks/cited-works into the KG.

`data/quarantine/_performances_artworks.json` is a curator-built list of
1 558 records. Each record carries:

    {
      "o": "<origin_tmx>",          # TM origin file
      "i": <seg_idx>,               # TM segment index in that origin
      "entity_type": "<type>",      # performance|book|artwork|...
      "title": "<title_string>",
      "people": [
        {"name": "<person>", "role": "<role>"}, ...
      ]
    }

This is the deterministic anchor evidence the audit's primary path
expected. For each record:

  1. Compute a stable source_text id from author(first person) + title
     (or fall back to title-only for collective works).
  2. Create / upsert the source_text node with the right project_type.
  3. For each person, create the agent node (no stub if role is
     `mentioned` and the agent doesn't already exist) and wire the
     ontology §3.2 edge for that role:
       - author          -> written_by
       - artist          -> written_by  (creator)
       - choreographer   -> written_by  (creator)
       - composer        -> written_by  (creator)
       - director        -> written_by  (creator)
       - curator         -> edited_by   (per ontology §3.2: edited_by
                                          also covers exhibition curators)
       - editor          -> edited_by
       - performer       -> performed_by
       - dancer          -> performed_by
       - translator      -> translated_by
       - interviewer     -> edited_by   (treat as content-shaper)
       - interviewee     -> written_by  (treat as primary author)
       - mentioned       -> add to agent.mention_segments only
  4. Append `{origin: <o>, segment_idx: <i>}` to each agent's
     `mention_segments` list (ontology §2.5).

No regex; no statistics. Each record yields a deterministic
(source_text, person, role) triple.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.knowledge_graph import KnowledgeGraph

DATA = ROOT / "data" / "quarantine" / "_performances_artworks.json"

ROLE_EDGE = {
    "author":         "link_written_by",
    "artist":         "link_written_by",
    "choreographer":  "link_written_by",
    "composer":       "link_written_by",
    "director":       "link_written_by",
    "curator":        "link_edited_by",
    "editor":         "link_edited_by",
    "performer":      "link_performed_by",
    "dancer":         "link_performed_by",
    "translator":     "link_translated_by",
    "interviewer":    "link_edited_by",
    "interviewee":    "link_written_by",
}
# "mentioned" deliberately not here — these only get mention_segments.

# Map curator entity_type to ontology project_type
ENTITY_TYPE_MAP = {
    "performance":         "performance",
    "book":                "book",
    "artwork":             "artwork",
    "journal_article":     "journal_article",
    "book_chapter":        "book_chapter",
    "exhibition_catalog":  "exhibition_catalog",
    "interview":           "interview",
    "web_source":          "web_source",
    "magazine_article":    "magazine_article",
    "newspaper_article":   "newspaper_article",
    "thesis_dissertation": "thesis_dissertation",
}

ROLE_ALLOWLIST = frozenset({
    "author", "translator", "editor", "curator", "artist",
    "interviewer", "interviewee", "choreographer", "director",
    "performer", "dancer", "composer", "dramaturg", "agent",
})


def _slug(s: str) -> str:
    """NFKD strip + lowercase + non-alphanumeric ASCII → '-'."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    interim = "".join(c if (c.isalnum() and c.isascii()) else "-" for c in s)
    parts = [p for p in interim.split("-") if p]
    return "-".join(parts)[:80]


def _author_lastname(name: str) -> str:
    """Last token of a person name (works for 'Surname' or 'First Last')."""
    parts = name.strip().split()
    return parts[-1] if parts else ""


def _agent_id(name: str) -> str:
    """`agent:<surname>-<first>` slug, matching the COBISS ingester convention."""
    parts = name.strip().split()
    if not parts:
        return ""
    if len(parts) == 1:
        return f"agent:{_slug(parts[0])}"
    surname = parts[-1]
    first = " ".join(parts[:-1])
    return f"agent:{_slug(f'{surname} {first}')}"


def _source_id(title: str, primary_author: str) -> str:
    """Build source_text id: `source:<author>-<title>`. No year (data does
    not carry it explicitly per record)."""
    parts = []
    if primary_author:
        parts.append(_slug(_author_lastname(primary_author)))
    if title:
        parts.append(_slug(title[:60]))
    return "source:" + "-".join(p for p in parts if p)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Mutate KG and save.")
    args = parser.parse_args(argv)

    print("loading KG...")
    kg = KnowledgeGraph()
    initial_nodes = kg.G.number_of_nodes()
    initial_edges = kg.G.number_of_edges()
    print(f"  nodes: {initial_nodes}, edges: {initial_edges}")

    records = json.loads(DATA.read_text(encoding="utf-8"))
    print(f"\nrecords: {len(records)}")

    stats: Counter = Counter()

    for rec in records:
        title = (rec.get("title") or "").strip()
        if not title:
            stats["skipped_no_title"] += 1
            continue
        origin = rec.get("o")
        seg_idx = rec.get("i")
        entity_type = rec.get("entity_type", "")
        project_type = ENTITY_TYPE_MAP.get(entity_type, "cited_work")
        people = rec.get("people") or []

        primary_author_name = next(
            (p.get("name", "") for p in people if p.get("role") == "author"),
            "",
        )
        if not primary_author_name and people:
            primary_author_name = people[0].get("name", "")

        src_id = _source_id(title, primary_author_name)
        if not src_id or src_id == "source:":
            stats["skipped_no_id"] += 1
            continue

        # Upsert source_text node
        if not kg.G.has_node(src_id):
            if args.apply:
                kg.add_source_text_node(
                    text_id=src_id.removeprefix("source:"),
                    title=title,
                    project_type=project_type,
                    provenance="curator_extra",
                )
            stats["source_text_created"] += 1
        else:
            node = kg.G.nodes[src_id]
            if not node.get("project_type") and args.apply:
                node["project_type"] = project_type
            if not node.get("provenance") and args.apply:
                node["provenance"] = "curator_extra"
            stats["source_text_existed"] += 1

        # Process each person
        for person in people:
            name = (person.get("name") or "").strip()
            role = (person.get("role") or "").strip().lower()
            if not name:
                continue

            agent_id = _agent_id(name)
            if not agent_id or agent_id == "agent:":
                continue

            existed_before = kg.G.has_node(agent_id)
            if not existed_before:
                # Create agent only when role implies authorship/agency
                if role == "mentioned":
                    stats["mentioned_agent_not_created"] += 1
                    continue
                # Use role if in allowlist, otherwise fall back to "agent"
                kg_role = role if role in ROLE_ALLOWLIST else "agent"
                if args.apply:
                    kg.add_agent_node(
                        agent_id=agent_id.removeprefix("agent:"),
                        name=name,
                        role=kg_role,
                        dedup_group=f"{name[:1].lower()}.{_slug(_author_lastname(name))}",
                        alt_spellings=[name],
                        all_roles=[kg_role],
                        mention_count=1,
                    )
                stats["agent_created"] += 1
            else:
                stats["agent_existed"] += 1

            # Append mention_segments entry on the agent
            if args.apply and origin and seg_idx is not None and kg.G.has_node(agent_id):
                agent_data = kg.G.nodes[agent_id]
                ms = agent_data.setdefault("mention_segments", [])
                entry = {"origin": origin, "segment_idx": seg_idx}
                if entry not in ms:
                    ms.append(entry)
                    if len(ms) > 50:  # keep memory bounded
                        agent_data["mention_segments"] = ms[-50:]

            # Wire the role-appropriate edge (skip for "mentioned")
            writer_name = ROLE_EDGE.get(role)
            if writer_name and kg.G.has_node(agent_id):
                writer = getattr(kg, writer_name)
                if args.apply:
                    if writer(src_id, agent_id):
                        stats[f"edge_{role}"] += 1
                else:
                    stats[f"edge_{role}"] += 1

    print()
    for k, v in stats.most_common():
        print(f"  {k}: {v}")

    if args.apply:
        kg.save()
        print()
        print("KG saved.")
        print(f"  node count: {initial_nodes} -> {kg.G.number_of_nodes()} "
              f"(+{kg.G.number_of_nodes()-initial_nodes})")
        print(f"  edge count: {initial_edges} -> {kg.G.number_of_edges()} "
              f"(+{kg.G.number_of_edges()-initial_edges})")
    else:
        print("\n(dry-run; pass --apply to mutate KG)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
