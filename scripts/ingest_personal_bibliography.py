# scripts/ingest_personal_bibliography.py
#
# Ingest the curator's personal COBISS bibliography into the KG.
#
# Creates container source_text nodes (book_translation, article_translation,
# festival_programme, exhibition_catalogue) for translator-role entries, and
# cited source_text nodes (book, magazine_article) for author-role entries.
#
# Wires:
#   - translated_by → curator agent (for containers)
#   - written_by → agent:<original_author> (for all works)
#   - published_by / translation_published_by → institution:<publisher>
#   - cited_in edges from cited works to their containers
#
# Usage:
#   python scripts/ingest_personal_bibliography.py [--path PATH] [--dry-run]
#
# Per O-1: only KnowledgeGraph factory methods write to the KG.
# Per O-2: all slugs use NFKD-normalized lowercase.
# Per O-12: agent writes include dedup_group, alt_spellings, all_roles, mention_count.
# Per O-13: role ∈ {author, translator, editor, curator, artist, interviewer, interviewee, agent}.
# Per O-14: institution.kind ∈ valid set.
# Per O-16: source_text.project_type ∈ valid set.
# Per O-20: every container node has a translated_by edge.

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.cobiss_parser import parse_cobiss_file
from translate_core.cobiss_classifier import (
    classify_entry,
    classify_institution_kind,
    CURATOR_SLUG,
    CURATOR_NAME,
    CONTAINER_TYPES,
    AGENT_ROLES,
)
from translate_core.entity_extraction.name_dedup import dedup_group_key
from translate_core.entity_extraction._slug import _slugify


KG_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge.db"
COBISS_PATH = Path(__file__).resolve().parent.parent / "data" / "personal bibliography" / "bibliography_export.txt"
REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "cobiss_ingest_report.md"
UNCLASSIFIED_PATH = Path(__file__).resolve().parent.parent / "data" / "cobiss_unclassified_entries.json"



def _make_agent_id(last_name: str, first_name: str) -> str:
    """Create a deterministic agent ID slug from name parts."""
    parts = [last_name]
    if first_name:
        parts.append(first_name)
    return _slugify(" ".join(parts))


def _make_source_id(title: str, year: int | None, author_slug: str) -> str:
    """Create a deterministic source_text ID from title, year, author."""
    parts = []
    if author_slug:
        parts.append(author_slug)
    if title:
        parts.append(title[:60])
    if year:
        parts.append(str(year))
    return _slugify("-".join(parts))


def _make_institution_id(name: str) -> str:
    """Create a deterministic institution ID slug."""
    return _slugify(name)


def ingest_bibliography(
    cobiss_path: str | Path | None = None,
    kg_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Ingest COBISS bibliography into the KG.

    Returns a report dict with counts and any unclassified entries.
    """
    cobiss_path = Path(cobiss_path) if cobiss_path else COBISS_PATH
    kg_path = Path(kg_path) if kg_path else KG_PATH

    # Parse
    entries = parse_cobiss_file(str(cobiss_path))

    # Load KG
    kg = KnowledgeGraph(db_path=kg_path)

    # Counters
    report = {
        "total_entries": len(entries),
        "containers_created": 0,
        "cited_works_created": 0,
        "agents_created": 0,
        "agents_updated": 0,
        "institutions_created": 0,
        "edges_created": 0,
        "skipped_duplicates": 0,
        "unclassified": [],
        "errors": [],
    }
    curator_id = kg.add_agent_node(
        agent_id=CURATOR_SLUG,
        name=CURATOR_NAME,
        role="translator",
        dedup_group=dedup_group_key(CURATOR_NAME),
        alt_spellings=["Belina, Urban", "BELINA, Urban", CURATOR_NAME],
        all_roles=["translator", "author", "editor", "curator"],
        mention_count=1,
    )

    for entry in entries:
        try:
            ptype, curator_role = classify_entry(entry)
        except Exception as e:
            report["errors"].append(f"Entry #{entry.entry_number}: classification error: {e}")
            continue

        if ptype is None:
            report["unclassified"].append({
                "entry_number": entry.entry_number,
                "title": entry.title,
                "agents": [(a.last_name, a.first_name, a.roles) for a in entry.agents],
                "cobiss_id": entry.cobiss_id,
            })
            continue

        # Dedup key: use cobiss_id if available, else title+year
        if entry.cobiss_id:
            pass
        else:
            _make_source_id(entry.title, entry.year, "")

        # Primary author slug (first listed author)
        primary_author_slug = ""
        if entry.agents:
            primary_author_slug = _make_agent_id(entry.agents[0].last_name, entry.agents[0].first_name)
            f"{entry.agents[0].first_name} {entry.agents[0].last_name}".strip()

        # Source text ID
        source_id = _make_source_id(entry.title, entry.year, primary_author_slug)
        source_node_id = f"source:{source_id}"
        is_existing = kg.G.has_node(source_node_id)

        # Bilingual title handling (O-5). The KG source_text has no subtitle
        # field, so fold the SL subtitle back into the primary title — storing
        # it separately truncates the visible title.
        primary_title = entry.title  # SL side per COBISS parser convention
        if entry.subtitle and entry.subtitle.lower() not in (entry.title or "").lower():
            primary_title = f"{entry.title}: {entry.subtitle}"
        secondary_title = entry.title_en  # EN side after "=" separator

        # Create source_text node (O-1, O-16). Assign canonical neutral
        # bilingual fields based on curator_role. Language codes are DATA VALUES
        # from the COBISS parser convention: entry.title = SL side,
        # entry.title_en = EN side. No text-level language detection.
        kwargs: dict = {
            "provenance": "cobiss_personal",
            "kind": "translated_work" if ptype in CONTAINER_TYPES else "cited_work",
        }
        # COBISS parser convention: entry.title is the catalogued side
        # (SL in the COBISS observed data); entry.title_en is the
        # `=`-separator second side. Role-based field assignment below.
        # Direction (which side is orig vs translation) follows the
        # role label set by classify_entry — author/editor → catalogued
        # side is the original; translator → catalogued side is the
        # translation. End-of-plan curator review settles edge cases
        # where role-derived direction disagrees with the actual work.
        if curator_role == "translator":
            if primary_title:
                kwargs["title_translation"] = primary_title
                kwargs["translation_lang"] = "sl"
            if secondary_title:
                kwargs["title_orig"] = secondary_title
                kwargs["orig_lang"] = "en"
        else:
            if primary_title:
                kwargs["title_orig"] = primary_title
                kwargs["orig_lang"] = "sl"
            if secondary_title:
                kwargs["title_translation"] = secondary_title
                kwargs["translation_lang"] = "en"
        if entry.year:
            kwargs["year"] = entry.year
        if entry.extent:
            kwargs["extent"] = entry.extent
        if entry.series:
            kwargs["series"] = entry.series
        if entry.edition:
            kwargs["edition"] = entry.edition
        if entry.journal_name:
            kwargs["journal_name"] = entry.journal_name
        if entry.journal_volume:
            kwargs["journal_volume"] = entry.journal_volume
        if entry.journal_issue:
            kwargs["journal_issue"] = entry.journal_issue
        if entry.pages:
            kwargs["pages"] = entry.pages
        if entry.issn:
            kwargs["issn"] = entry.issn
        if entry.isbn:
            kwargs["isbn"] = ", ".join(entry.isbn)
        if entry.cobiss_id:
            kwargs["cobiss_id"] = entry.cobiss_id
        if entry.urls:
            kwargs["urls"] = entry.urls

        node_id = kg.add_source_text_node(
            text_id=source_id,
            title=primary_title or secondary_title or f"Entry #{entry.entry_number}",
            project_type=ptype,
            **kwargs,
        )

        # Upsert: backfill kind/provenance/neutral title fields on a node
        # that already existed from a previous (pre-Phase-5) ingest run.
        # Existing edges and other curator-set fields are preserved; we only
        # write fields that are currently missing or empty.
        node_data = kg.G.nodes[node_id]
        for k, v in kwargs.items():
            if not node_data.get(k):
                node_data[k] = v
        if not node_data.get("project_type"):
            node_data["project_type"] = ptype

        if is_existing:
            report["upserted_existing"] = report.get("upserted_existing", 0) + 1
        elif ptype in CONTAINER_TYPES:
            report["containers_created"] += 1
        else:
            report["cited_works_created"] += 1

        # Wire written_by for all authors (O-1)
        for agent in entry.agents:
            agent_id_slug = _make_agent_id(agent.last_name, agent.first_name)
            agent_name = f"{agent.first_name} {agent.last_name}".strip()
            roles = agent.roles if agent.roles else ["author"]

            # Validate roles (O-13)
            valid_roles = [r for r in roles if r in AGENT_ROLES]
            if not valid_roles:
                valid_roles = ["agent"]

            primary_role = valid_roles[0]
            agent_existed_before = kg.G.has_node(f"agent:{agent_id_slug.lower()}")
            node = kg.add_agent_node(
                agent_id=agent_id_slug,
                name=agent_name,
                role=primary_role,
                dedup_group=dedup_group_key(agent_name),
                alt_spellings=[f"{agent.last_name}, {agent.first_name}"],
                all_roles=valid_roles,
                mention_count=1,
            )
            if not agent_existed_before:
                report["agents_created"] += 1

            # written_by edge
            if kg.link_written_by(node_id, node):
                report["edges_created"] += 1

        # Wire translated_by for containers (O-20)
        if ptype in CONTAINER_TYPES:
            if kg.link_translated_by(node_id, curator_id):
                report["edges_created"] += 1

        # Wire published_by for publisher (O-1, O-14). Bilingual publisher
        # convention in COBISS: "SL Publisher Name: = EN Publisher Name".
        # Split on ": =" → primary (original) and translation publishers.
        if entry.publisher:
            raw_pub = entry.publisher.strip()
            if ": =" in raw_pub:
                parts = raw_pub.split(": =", maxsplit=1)
                primary_pub = parts[0].strip()
                translation_pub = parts[1].strip()

                if primary_pub:
                    p_kind = classify_institution_kind(primary_pub)
                    p_id = _make_institution_id(primary_pub)
                    p_existed = kg.G.has_node(f"institution:{p_id.lower()}")
                    p_node = kg.add_institution_node(
                        inst_id=p_id, name=primary_pub, kind=p_kind,
                    )
                    if not p_existed:
                        report["institutions_created"] += 1
                    if kg.link_published_by(node_id, p_node):
                        report["edges_created"] += 1

                if translation_pub:
                    t_kind = classify_institution_kind(translation_pub)
                    t_id = _make_institution_id(translation_pub)
                    t_existed = kg.G.has_node(f"institution:{t_id.lower()}")
                    t_node = kg.add_institution_node(
                        inst_id=t_id, name=translation_pub, kind=t_kind,
                    )
                    if not t_existed:
                        report["institutions_created"] += 1
                    if kg.link_translation_published_by(node_id, t_node):
                        report["edges_created"] += 1
            else:
                pub_name = raw_pub
                if pub_name:
                    inst_kind = classify_institution_kind(pub_name)
                    inst_id = _make_institution_id(pub_name)
                    inst_existed = kg.G.has_node(f"institution:{inst_id.lower()}")
                    inst_node = kg.add_institution_node(
                        inst_id=inst_id,
                        name=pub_name,
                        kind=inst_kind,
                    )
                    if not inst_existed:
                        report["institutions_created"] += 1
                    if kg.link_published_by(node_id, inst_node):
                        report["edges_created"] += 1

    # Save KG (unless dry-run)
    if not dry_run:
        kg.save()


    # Write report
    report_text = f"""# COBISS Ingest Report

- **Total entries parsed:** {report['total_entries']}
- **Containers created:** {report['containers_created']}
- **Cited works created:** {report['cited_works_created']}
- **Agents created:** {report['agents_created']}
- **Institutions created:** {report['institutions_created']}
- **Edges created:** {report['edges_created']}
- **Skipped duplicates:** {report['skipped_duplicates']}
- **Unclassified entries:** {len(report['unclassified'])}
- **Errors:** {len(report['errors'])}

## Unclassified Entries

"""
    for ue in report["unclassified"]:
        report_text += f"- #{ue['entry_number']}: {ue['title'][:80]}\n"

    if not dry_run:
        REPORT_PATH.write_text(report_text, encoding="utf-8")
        if report["unclassified"]:
            UNCLASSIFIED_PATH.write_text(
                json.dumps(report["unclassified"], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Ingest COBISS bibliography into KG")
    parser.add_argument("--path", default=str(COBISS_PATH), help="Path to COBISS plain-text file")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to KG")
    args = parser.parse_args()

    report = ingest_bibliography(cobiss_path=args.path, dry_run=args.dry_run)

    print(f"Total entries: {report['total_entries']}")
    print(f"Containers created: {report['containers_created']}")
    print(f"Cited works created: {report['cited_works_created']}")
    print(f"Agents created: {report['agents_created']}")
    print(f"Institutions created: {report['institutions_created']}")
    print(f"Edges created: {report['edges_created']}")
    print(f"Skipped duplicates: {report['skipped_duplicates']}")
    print(f"Upserted existing: {report.get('upserted_existing', 0)}")
    print(f"Unclassified: {len(report['unclassified'])}")
    print(f"Errors: {len(report['errors'])}")

    if report["errors"]:
        print("\nErrors:")
        for err in report["errors"]:
            print(f"  {err}")