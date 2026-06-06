#!/usr/bin/env python3
"""Backfill dedup_group, alt_spellings, all_roles, mention_count on agent nodes.

Reads data/knowledge.db, patches every agent node that is missing these fields,
and writes back.  Idempotent — re-running produces no changes.
"""

import json
import sys
from pathlib import Path

# Project root so we can import translate_core
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translate_core.entity_extraction.name_dedup import dedup_group_key

KG_PATH = ROOT / "data" / "knowledge.db"


def main() -> None:
    if not KG_PATH.exists():
        print(f"KG not found at {KG_PATH}")
        sys.exit(1)

    raw = json.loads(KG_PATH.read_text(encoding="utf-8"))
    nodes = raw["nodes"]

    updated = 0
    samples: list[dict] = []

    for node in nodes:
        if node.get("type") != "agent":
            continue

        changed = False

        # (a) dedup_group — if missing or empty (dedup_group_key returns '' for non-names)
        dg = node.get("dedup_group")
        if not dg:
            computed = dedup_group_key(node["name"])
            node["dedup_group"] = computed if computed else node["id"].replace("agent:", "")
            changed = True

        # (b) alt_spellings
        if not node.get("alt_spellings"):
            node["alt_spellings"] = [node["name"]]
            changed = True

        # (c) all_roles
        if not node.get("all_roles"):
            role = node.get("role", "agent")
            node["all_roles"] = [role]
            changed = True

        # (d) mention_count
        if not node.get("mention_count"):
            node["mention_count"] = 1
            changed = True

        if changed:
            updated += 1
            if len(samples) < 10:
                samples.append({
                    "id": node["id"],
                    "name": node["name"],
                    "dedup_group": node["dedup_group"],
                    "alt_spellings": node["alt_spellings"],
                    "all_roles": node["all_roles"],
                    "mention_count": node["mention_count"],
                })

    if updated:
        # Atomic write with backup
        bak_path = KG_PATH.with_suffix(KG_PATH.suffix + ".bak")
        if KG_PATH.exists():
            import shutil
            shutil.copy2(str(KG_PATH), str(bak_path))

        import tempfile, os
        fd, tmp_path = tempfile.mkstemp(dir=str(KG_PATH.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(KG_PATH))
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    print(f"Updated {updated} agent nodes.")
    if samples:
        print("\nSample updated agents:")
        for s in samples:
            print(f"  {s['id']}: dedup_group={s['dedup_group']!r}, "
                  f"alt_spellings={s['alt_spellings']!r}, "
                  f"all_roles={s['all_roles']!r}, "
                  f"mention_count={s['mention_count']}")


if __name__ == "__main__":
    main()