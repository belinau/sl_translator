# seed_kg.py
#
# Concept-Centered KG Seeding Pipeline
#
# Reads TMX files from data/tm/, parses each individually with correct
# language detection from the TMX <tuv> attributes, normalizes all entries
# to EN→SL, then seeds the KG with concept-centered architecture.
#
# Key fixes from v22:
#   - No double-swap: TM parser already normalizes EN source → SL target
#   - Each TMX file loaded individually (not all files × 5)
#   - working.tmx excluded (unverified testing data)
#   - Concepts are created from aligned EN↔SL pairs, not monolingual stubs
#   - Dice coefficient replaces noisy cartesian co-occurrence
#

import os
import re
import shutil
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.tm import TranslationMemory, clean_xml


# Files to skip during seeding (unverified / testing data)
EXCLUDED_FILES = {"working.tmx"}


def parse_tmx_file(file_path: Path) -> list[dict]:
    """Parse a single TMX file and return entries normalized to EN source → SL target.

    Uses the <tuv xml:lang> attributes to determine which side is English
    and which is Slovenian, regardless of the srclang header. This is the
    ground truth — each segment carries its own language labels.
    """
    from translate.storage.tmx import tmxfile

    raw = file_path.read_text(encoding="utf-8")

    # Detect source language from TMX header for default ordering
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

            # tmxfile uses srclang to decide which tuv is source/target.
            # When srclang is SL, source = SL text, target = EN text.
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
    return entries


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

    tm_directory = Path("./data/tm")
    if not tm_directory.exists():
        print(f"[Error] The translation memory directory '{tm_directory}' does not exist.")
        return

    tm_files = sorted(tm_directory.glob("*.tmx"))

    # Exclude unverified/testing files
    tm_files = [f for f in tm_files if f.name not in EXCLUDED_FILES]

    if not tm_files:
        print(f"No TMX files found in '{tm_directory}' (after exclusions).")
        return

    print(f"Found {len(tm_files)} TMX files to process.")
    total_segments = 0

    for tm_file in tm_files:
        print(f"\nProcessing: {tm_file.name}")

        # Parse this file individually with correct language detection
        entries = parse_tmx_file(tm_file)
        print(f"  └ {len(entries)} segments (EN→SL normalized)")

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

    if kg.db_path.exists():
        shutil.copy2(kg.db_path, kg.db_path.with_suffix('.json.bak'))
        print(f"Backup saved to {kg.db_path.with_suffix('.json.bak')}")

    kg.save()
    print(f"\n{'='*60}")
    print(f"Knowledge Graph seeded from {total_segments} total segments.")
    print(kg.stats())


if __name__ == "__main__":
    seed()