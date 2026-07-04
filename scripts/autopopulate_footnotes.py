#!/usr/bin/env python3
"""scripts/autopopulate_footnotes.py

Mechanically convert footnote source text to Maska citation format and
populate the target field. No LLM, no translation — the footnote stays
in the source language; only typographic conventions are converted.

For each footnote segment group ([^N]: def + continuation segments):
  1. Take the source text (which now carries *...* italic markers from
     the PDF dict walk in _pdf_dict_to_markdown_text).
  2. Run convert_footnote_to_maska on it.
  3. Write the result into the target field.
  4. Set status to "pending" (NOT "done") so the translator reviews each.

Preserves any existing non-empty target — won't overwrite manual work.

Usage:
    # Dry run (default) — show report, no changes
    python scripts/autopopulate_footnotes.py <project_id>

    # Apply — back up, then write converted targets
    python scripts/autopopulate_footnotes.py <project_id> --apply
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.maska_footnote_convert import convert_footnote_to_maska

PROJECTS_DIR = ROOT / "data" / "projects"
_FN_DEF_RE = re.compile(r"^\[\^(\w+)\]:")


def autopopulate(project_id: str, apply: bool = False) -> None:
    json_path = PROJECTS_DIR / f"{project_id}.json"
    if not json_path.exists():
        print(f"ERROR: project JSON not found: {json_path}")
        sys.exit(1)

    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    if not segments:
        print("ERROR: project has no segments")
        sys.exit(1)

    # Build footnote groups: [^N]: def + continuation segments
    groups: list[tuple[int, list[int]]] = []
    i = 0
    while i < len(segments):
        m = _FN_DEF_RE.match(segments[i].get("source", "").lstrip())
        if m:
            fn_num = int(m.group(1))
            j = i + 1
            while j < len(segments):
                if _FN_DEF_RE.match(segments[j].get("source", "").lstrip()):
                    break
                j += 1
            groups.append((fn_num, list(range(i, j))))
            i = j
        else:
            i += 1

    print(f"Project: {project_id}")
    print(f"  segments: {len(segments)}")
    print(f"  footnote groups: {len(groups)}")

    populated = 0
    skipped_has_target = 0
    skipped_no_source = 0
    conversions_applied: dict[str, int] = {
        "quotes": 0, "v_marker": 0, "ur": 0, "prev": 0,
        "str": 0, "ibid": 0, "date": 0, "en_dash": 0, "italic": 0,
    }

    for fn_num, seg_indices in groups:
        # Get full footnote source text
        full_src = " ".join(
            segments[k].get("source", "").strip() for k in seg_indices
        )
        if not full_src.strip():
            skipped_no_source += 1
            continue

        # Check if target already has content (skip existing work)
        existing_target = " ".join(
            segments[k].get("target", "").strip() for k in seg_indices
        ).strip()
        if existing_target:
            skipped_has_target += 1
            continue

        # Strip the [^N]: prefix, convert content, reattach prefix
        prefix_match = re.match(r"^(\[\^\d+\]:\s*)", full_src)
        prefix = prefix_match.group(1) if prefix_match else ""
        content = full_src[len(prefix):]

        converted = convert_footnote_to_maska(content)
        target_text = prefix + converted

        # Track conversions
        if "»" in converted and ('"' in content or "\u201c" in content):
            conversions_applied["quotes"] += 1
        if " v: *" in converted and " in *" in content:
            conversions_applied["v_marker"] += 1
        if "ur." in converted and re.search(r"\beds?\.", content):
            conversions_applied["ur"] += 1
        if "prev." in converted and "trans." in content:
            conversions_applied["prev"] += 1
        if "str." in converted and re.search(r"\bpp?\.", content):
            conversions_applied["str"] += 1
        if "*Ibid*" in converted and "Ibid" in content:
            conversions_applied["ibid"] += 1
        if re.search(r"\d{1,2}\.\s\d{1,2}\.\s\d{4}", converted) and \
           re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)", content):
            conversions_applied["date"] += 1
        if "\u2013" in converted and re.search(r"\d-\d", content):
            conversions_applied["en_dash"] += 1
        if "*" in converted:
            conversions_applied["italic"] += 1

        if apply:
            # Write target into the def segment, clear continuation targets
            segments[seg_indices[0]]["target"] = target_text
            for k in seg_indices[1:]:
                segments[k]["target"] = ""
            # Set status to pending for review
            for k in seg_indices:
                if segments[k].get("status") != "done":
                    segments[k]["status"] = "pending"

        populated += 1

    print(f"\n  populated: {populated}")
    print(f"  skipped (has existing target): {skipped_has_target}")
    print(f"  skipped (no source): {skipped_no_source}")
    print("\n  conversions applied:")
    for k, v in conversions_applied.items():
        print(f"    {k}: {v}")

    if not apply:
        print("\n[DRY RUN] No changes written. Use --apply to populate targets.")
        return

    # Backup + write
    bak = json_path.with_suffix(".json.bak")
    if not bak.exists():
        shutil.copy2(json_path, bak)
        print(f"\n  backup: {bak.name}")
    else:
        print(f"\n  backup exists: {bak.name} (not overwritten)")

    data["segments"] = segments
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  wrote: {json_path.name}")
    print("Done.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Autopopulate footnote targets with Maska citation format."
    )
    parser.add_argument("project_id", help="Project ID (filename in data/projects/)")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes (default: dry run)")
    args = parser.parse_args(argv)
    autopopulate(args.project_id, apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())