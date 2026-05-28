# seed_kg_processing_additional.py
#
# Concept-Centered KG Seeding Pipeline
# Reads exclusively from data/tm/tm_processing/ inbox subfolder.
#
# Parses each TMX file individually using the <tuv> language attributes
# (the ground truth), normalizes all entries to EN→SL, then seeds the KG
# with concept-centered architecture.
#
# Key fixes from v22:
#   - No double-swap: TM parser normalizes EN source → SL target
#   - Each TMX file loaded individually (not all files × N)
#   - Concepts created from aligned EN↔SL pairs, not monolingual stubs
#   - Dice coefficient replaces noisy cartesian co-occurrence
#

import os
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.tm import clean_xml


# Files to skip during seeding (unverified / testing data)
EXCLUDED_FILES = {"working.tmx"}


def parse_tmx_file(file_path: Path) -> tuple[list[dict], str]:
    """Parse a single TMX file and return entries normalized to EN source → SL target,
    plus the detected srclang code for logging.

    Uses the srclang header attribute to determine which side is English
    and which is Slovenian. The tmxfile library returns source/target based
    on srclang, so we swap when srclang is SL.
    """
    from translate.storage.tmx import tmxfile

    raw = file_path.read_text(encoding="utf-8")

    # Detect source language from TMX header
    m = re.search(r'srclang\s*=\s*"([^"]+)"', raw, re.IGNORECASE)
    srclang = m.group(1).lower().split("-")[0] if m else "en"

    entries = []
    with open(file_path, "rb") as f:
        tmx = tmxfile(f)
        for unit in tmx.unit_iter():
            src = clean_xml(unit.source)
            tgt = clean_xml(unit.target)
            if not src or not tgt:
                continue

            # tmxfile returns source = text in srclang, target = other language.
            # When srclang is SL: source=SL text, target=EN text → swap.
            # When srclang is EN: source=EN text, target=SL text → correct as-is.
            # Normalize: always store EN text as "source", SL text as "target".
            if srclang == "sl":
                src, tgt = tgt, src

            entries.append({
                "source": src,
                "target": tgt,
                "origin": file_path.name,
                "source_lang": "en",
                "target_lang": "sl",
            })
    return entries, srclang


def parse_filename_metadata(filename: str) -> dict:
    """Parse metadata from TMX filenames like '2022-SL-EN.tmx'."""
    base_name = Path(filename).stem
    parts = base_name.split("_")

    metadata = {
        "lineage": base_name,
        "source_title": base_name.replace("_", " "),
        "agent_name": None,
        "year": None,
    }

    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", base_name)
    if year_match:
        metadata["year"] = int(year_match.group(1))
        parts = [p for p in parts if p != year_match.group(1)]

    if len(parts) >= 3:
        metadata["agent_name"] = parts[0]
        metadata["source_title"] = " ".join(parts[1:])
    elif len(parts) == 2:
        metadata["source_title"] = parts[1]

    return metadata


def seed():
    kg = KnowledgeGraph()

    # Read directly from the existing TM directory — no file moving needed
    tm_directory = Path("./data/tm")

    if not tm_directory.exists():
        print(f"[Error] TM directory '{tm_directory}' does not exist.")
        return

    # Find all TMX files
    tm_files = sorted(tm_directory.glob("*.tmx"))

    # Exclude unverified/testing files
    tm_files = [f for f in tm_files if f.name not in EXCLUDED_FILES]

    if not tm_files:
        print(f"No TMX files found in '{tm_directory}' (after exclusions).")
        print("Drop new client files there and re-run this script to seed them.")
        return

    print(f"Found {len(tm_files)} TMX files to process in '{tm_directory}'.")
    total_segments = 0

    for tm_file in tm_files:
        print(f"\nProcessing: {tm_file.name}")

        # Parse this file individually with correct language detection
        entries, srclang = parse_tmx_file(tm_file)
        srclang_code = srclang.split("-")[0] if "-" in srclang else srclang
        if srclang_code == "sl":
            direction_note = "SL→EN TMX: entries swapped so EN=source, SL=target"
        else:
            direction_note = "EN→SL TMX: entries already correct"
        print(f"  └ {len(entries)} segments ({direction_note})")

        if not entries:
            print(f"  └ No valid segments, skipping.")
            continue

        total_segments += len(entries)

        # Parse metadata from filename
        meta = parse_filename_metadata(tm_file.name)
        lineage_label = meta["lineage"]
        source_title = meta["source_title"]
        agent_name = meta["agent_name"]
        year = meta["year"]

        print(f"  └ Lineage: '{lineage_label}'")
        print(f"  └ Source text: '{source_title}'")
        if agent_name:
            print(f"  └ Agent: '{agent_name}'")
        if year:
            print(f"  └ Year: {year}")

        # Register metadata nodes
        agent_id = None
        if agent_name:
            agent_id = agent_name.lower().replace(" ", "_")
            kg.add_agent_node(agent_id, name=agent_name, role="author")

        source_id = source_title.lower().replace(" ", "_")
        kg.add_source_text_node(
            source_id, title=source_title, author_id=agent_id, year=year
        )

        # Seed from this file's entries — always EN source, SL target
        # (TM parser has already normalized direction)
        kg.seed_from_tm(
            tm_entries=entries,
            source_lang="en",
            target_lang="sl",
            min_freq=2,
            domain="humanities",
            default_lineage=lineage_label,
            default_source_id=source_id,
            default_agent_id=agent_id,
            default_year=year,
        )

    kg.save()
    print(f"\n{'='*60}")
    print(f"Knowledge Graph seeded from {total_segments} total segments.")
    print(kg.stats())


if __name__ == "__main__":
    seed()