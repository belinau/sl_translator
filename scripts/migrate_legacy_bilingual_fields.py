#!/usr/bin/env python3
"""One-off migration: remove legacy bilingual fields from source_text nodes.

Removes forbidden fields per ontology §2.4.2 / invariant #4:
  - title_en / title_sl (redundant — all nodes already have title_orig/title_translation)
  - slovenian_edition (converted to canonical translation_edition with language code)

Idempotent — re-running produces no changes.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KG_PATH = ROOT / "data" / "knowledge.db"

LEGACY_FIELDS = ("title_en", "title_sl")
EDITION_FIELD = "slovenian_edition"


def main() -> None:
    if not KG_PATH.exists():
        print(f"KG not found at {KG_PATH}")
        sys.exit(1)

    raw = json.loads(KG_PATH.read_text(encoding="utf-8"))
    nodes = raw["nodes"]

    removed_title = 0
    removed_edition = 0
    converted_edition = 0
    skipped_edition = 0

    for node in nodes:
        if node.get("type") != "source_text":
            continue

        # Remove legacy title fields (pure redundant duplicates)
        for field in LEGACY_FIELDS:
            if field in node:
                del node[field]
                removed_title += 1

        # Convert slovenian_edition → translation_edition
        if EDITION_FIELD in node:
            slo_ed = node.pop(EDITION_FIELD)
            removed_edition += 1
            if "translation_edition" in node:
                # Already has canonical form — don't overwrite
                skipped_edition += 1
            elif isinstance(slo_ed, dict):
                # Build canonical translation_edition with language code
                lang = node.get("translation_lang") or "sl"
                node["translation_edition"] = {
                    **slo_ed,
                    "language": lang,
                }
                converted_edition += 1
            # else: slo_ed is None or non-dict — just remove it

    if removed_title or removed_edition:
        # Atomic write with backup
        bak_path = KG_PATH.with_suffix(KG_PATH.suffix + ".bak")
        if not bak_path.exists():
            bak_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )

        tmp_path = KG_PATH.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp_path.replace(KG_PATH)

    print(
        f"Removed {removed_title} legacy title fields "
        f"({removed_title // 2 if removed_title else 0} nodes), "
        f"converted {converted_edition} slovenian_edition → translation_edition, "
        f"skipped {skipped_edition} (already canonical)."
    )


if __name__ == "__main__":
    main()