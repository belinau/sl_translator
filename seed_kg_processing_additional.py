# seed_kg_processing_additonal.py
#
# Automated, Bidirectional-Aware Seeding Pipeline
# Reads exclusively from the data/tm/tm_processing/ inbox subfolder.
#

import os
import re
import sys
from pathlib import Path
from typing import Tuple

sys.path.append(str(Path(__file__).parent))

from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.tm import TranslationMemory


def detect_tmx_languages(file_path: Path) -> Tuple[str, str]:
    """
    Detect the translation direction of a TMX file.

    1. First looks at the file name for identifiers like 'SL-EN' or 'EN-SL'.
    2. Scans the first 100 lines for TMX header metadata ('srclang') if ambiguous.
    Defaults to Source: English ('en'), Target: Slovenian ('sl').
    """
    name = file_path.name.upper()

    # 1. Filename pattern check
    if "SL-EN" in name or "SL_EN" in name:
        return "sl", "en"
    if "EN-SL" in name or "EN_SL" in name:
        return "en", "sl"

    # 2. TMX Attribute header scan
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(100):
                line = f.readline()
                if not line:
                    break
                match = re.search(
                    r'srclang=["\']([a-zA-Z-]+)["\']', line, re.IGNORECASE
                )
                if match:
                    detected_src = match.group(1).lower()[:2]
                    if detected_src == "sl":
                        return "sl", "en"
                    elif detected_src == "en":
                        return "en", "sl"
    except Exception:
        pass

    # Default standard fallback
    return "en", "sl"


def parse_filename_metadata(filename: str) -> dict:
    """
    Automatically parses metadata from messy filenames.
    Defaults to raw file name values if no patterns match.
    """
    base_name = Path(filename).stem
    parts = base_name.split("_")

    metadata = {
        "lineage": base_name,  # Use raw filename as starting lineage label
        "source_title": base_name.replace("_", " "),
        "agent_name": None,
        "year": None,
    }

    # Extract 4-digit year if present
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

    # Point directly to your designated inbox subfolder
    tm_directory = Path("./data/tm/tm_processing")

    # Safely ensure the subfolder exists without disturbing other paths
    tm_directory.mkdir(parents=True, exist_ok=True)

    # Find all translation memory files inside your processing folder
    tm_files = (
        list(tm_directory.glob("*.json"))
        + list(tm_directory.glob("*.tmx"))
        + list(tm_directory.glob("*.db"))
    )

    if not tm_files:
        print(
            f"No new TM files (.json, .tmx, .db) found inside your '{tm_directory}' inbox folder."
        )
        print("Drop new client files there and re-run this script to seed them.")
        return

    print(
        f"Found {len(tm_files)} new translation memory archives to process inside '{tm_directory}'..."
    )

    for tm_file in tm_files:
        print(f"\nProcessing: {tm_file.name}")

        # 1. Detect dynamic translation direction
        src_lang, tgt_lang = detect_tmx_languages(tm_file)
        print(f"  └ Detected Direction: {src_lang.upper()} ➔ {tgt_lang.upper()}")

        # 2. Parse metadata automatically
        meta = parse_filename_metadata(tm_file.name)
        lineage_label = meta["lineage"]
        source_title = meta["source_title"]
        agent_name = meta["agent_name"]
        year = meta["year"]

        print(f"  └ Auto-Lineage: '{lineage_label}'")
        print(f"  └ Auto-Source Text: '{source_title}'")
        if agent_name:
            print(f"  └ Auto-Agent: '{agent_name}'")
        if year:
            print(f"  └ Auto-Year: {year}")

        # Register metadata
        agent_id = None
        if agent_name:
            agent_id = agent_name.lower().replace(" ", "_")
            kg.add_agent_node(agent_id, name=agent_name, role="author")

        source_id = source_title.lower().replace(" ", "_")
        kg.add_source_text_node(
            source_id, title=source_title, author_id=agent_id, year=year
        )

        try:
            # Instantiate TranslationMemory with the specific file path
            tm = TranslationMemory(file_path=tm_file)
        except TypeError:
            tm = TranslationMemory()

        if tm.entries:
            # Feed the dynamically detected languages to prevent any jumbled mappings
            kg.seed_from_tm(
                tm.entries,
                source_lang=src_lang,
                target_lang=tgt_lang,
                min_freq=2,
                domain="humanities",
                default_lineage=lineage_label,
                default_source_id=source_id,
                default_agent_id=agent_id,
                default_year=year,
            )
        else:
            print(f"  No entries extracted from {tm_file.name}.")

    kg.save()
    print("\nKnowledge Graph successfully built and context-mapped.")
    print(kg.stats())


if __name__ == "__main__":
    seed()
