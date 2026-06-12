#!/usr/bin/env python3
"""Re-parse a book project's PDF through the hardened footnote pipeline
and re-attach existing translations.

Re-runs the full academic import path (MarkItDown + sequence-validated
endnote→footnote conversion) to produce corrected segments, then matches
them against existing translations by normalised source text.

Usage:
    # Dry run (default) — show report and attach counts
    python scripts/repair_book_footnotes.py <project_id>

    # Apply — back up, then write the repaired project JSON
    python scripts/repair_book_footnotes.py <project_id> --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translate_core.doc_parser import DocumentParser, footnote_alignment_report
from translate_core.book_outline import split_paragraphs
import config

PROJECTS_DIR = ROOT / "data" / "projects"

# Pattern to strip all footnote markers for source-matching
_FN_MARKER_RE = re.compile(r"\[\^\w+\]")


def _normalise(source: str) -> str:
    """Strip footnote markers and collapse whitespace for matching."""
    text = _FN_MARKER_RE.sub("", source)
    return re.sub(r"\s+", " ", text).strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-parse a book project's PDF through the hardened footnote pipeline "
                    "and re-attach existing translations."
    )
    parser.add_argument("project_id", help="Project ID (subdirectory name in data/projects/)")
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the repaired project JSON (default: dry-run only)",
    )
    args = parser.parse_args(argv)

    project_id = args.project_id
    project_path = PROJECTS_DIR / f"{project_id}.json"
    pdf_path = PROJECTS_DIR / f"{project_id}.pdf"

    # ── Load existing project ────────────────────────────────────────────
    if not project_path.exists():
        print(f"ERROR: project not found: {project_path}")
        return 1
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}")
        return 1

    data = json.loads(project_path.read_text(encoding="utf-8"))
    old_segments = data.get("segments", [])
    filename = data.get("filename", f"{project_id}.pdf")
    lang_pair = data.get("lang_pair", "en-sl")

    print(f"Project: {project_id}")
    print(f"  Existing segments: {len(old_segments)}")
    print(f"  Done: {sum(1 for s in old_segments if s.get('status') == 'done')}")

    # ── Re-parse via academic pipeline ────────────────────────────────────
    print("\nRe-parsing PDF through academic pipeline...")
    doc_parser = DocumentParser()
    md_text, _ = doc_parser.to_markdown_with_meta(pdf_path, preprocess=True)
    new_segments_raw = split_paragraphs(md_text, max_chars=config.SEGMENT_MAX_CHARS)

    report = doc_parser.last_footnote_report
    if report:
        print(f"\n  Footnote report:")
        print(f"    Defs: {report['defs']}, Refs: {report['refs']}")
        print(f"    Blocks: {report['blocks']}")
        print(f"    Aligned: {report['aligned']}")
        print(f"    Rejected def rows: {report.get('rejected_def_rows', 0)}")
        print(f"    Rejected body candidates: {report.get('rejected_body_candidates', 0)}")
    else:
        print("  No footnote report (preprocessing produced no report).")

    # Build new segment dicts
    new_segments = [
        {"id": i, "source": txt, "target": "", "status": "pending"}
        for i, txt in enumerate(new_segments_raw)
    ]

    # ── Re-attach translations ────────────────────────────────────────────
    # Pass 1 — exact: normalised source text → (target, status), first wins.
    old_by_key: dict[str, tuple[str, str]] = {}
    for seg in old_segments:
        key = _normalise(seg.get("source", ""))
        if key and key not in old_by_key:
            old_by_key[key] = (seg.get("target", ""), seg.get("status", "pending"))

    attached = 0
    lost = 0
    for seg in new_segments:
        key = _normalise(seg["source"])
        if key in old_by_key:
            target, status = old_by_key[key]
            seg["target"] = target
            seg["status"] = status
            attached += 1
        else:
            lost += 1

    # Pass 2 — fuzzy: re-home DONE translations whose old segmentation no
    # longer exists verbatim (e.g. VL-era paragraph joins, editorial em-dash
    # TOC shaping). Uses rapidfuzz — the same matcher the TM relies on.
    fuzzy_attached = 0
    try:
        from rapidfuzz import fuzz, process as rf_process
    except ImportError:
        rf_process = None
    if rf_process is not None:
        unattached_done = [
            s for s in old_segments
            if s.get("status") == "done" and s.get("target", "").strip()
            and _normalise(s.get("source", "")) not in {
                _normalise(n["source"]) for n in new_segments if n["status"] == "done"
            }
        ]
        new_keys = [_normalise(s["source"]) for s in new_segments]
        fuzzy_pairs: list[tuple[str, str]] = []
        for old_seg in unattached_done:
            old_key = _normalise(old_seg["source"])
            if not old_key:
                continue
            # fuzz.ratio (full-string) — token_set_ratio is subset-friendly
            # and happily attaches a short TOC line to an unrelated long
            # segment. A wrong attachment is worse than none.
            best = rf_process.extractOne(
                old_key, new_keys, scorer=fuzz.ratio, score_cutoff=88,
            )
            if best is None:
                continue
            _, score, idx = best
            tgt_seg = new_segments[idx]
            cand_key = new_keys[idx]
            # Length guard: similar strings must be similar lengths.
            if not (0.5 <= len(old_key) / max(len(cand_key), 1) <= 2.0):
                continue
            if tgt_seg["status"] == "done":
                continue  # already claimed by an exact or earlier fuzzy match
            tgt_seg["target"] = old_seg["target"]
            tgt_seg["status"] = "done"
            fuzzy_attached += 1
            fuzzy_pairs.append((old_key[:60], cand_key[:60]))
        if fuzzy_pairs:
            print("\n  Fuzzy matches (old -> new), audit:")
            for ok, nk in fuzzy_pairs:
                print(f"    {ok!r} -> {nk!r}")

    total_done = sum(1 for s in new_segments if s.get("status") == "done")
    print(f"\n  New segments: {len(new_segments)}")
    print(f"  Fuzzy re-homed done segments: {fuzzy_attached}")
    print(f"  Re-attached translations: {attached}")
    print(f"  Unmatched (no prior translation): {lost}")
    print(f"  Done after attach: {total_done}")

    # Alignment check on the new segments
    alignment = footnote_alignment_report(new_segments)
    print(f"\n  Alignment check on new segments:")
    print(f"    Defs: {alignment['defs']}, Refs: {alignment['refs']}")
    print(f"    Aligned: {alignment['aligned']}")
    if alignment["missing_def_numbers"]:
        print(f"    Missing defs for refs: {alignment['missing_def_numbers']}")
    if alignment["unreferenced_def_numbers"]:
        print(f"    Unreferenced defs: {alignment['unreferenced_def_numbers']}")

    if not args.apply:
        print("\n(dry-run — no changes written. Re-run with --apply to persist.)")
        return 0

    # ── Apply: backup + write ────────────────────────────────────────────
    backup_path = PROJECTS_DIR / f"{project_id}.json.bak"
    if backup_path.exists():
        # Never clobber the first backup — it is the pre-repair original.
        n = 2
        while (PROJECTS_DIR / f"{project_id}.json.bak{n}").exists():
            n += 1
        backup_path = PROJECTS_DIR / f"{project_id}.json.bak{n}"
    backup_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nBackup written to: {backup_path}")

    # Exact save_project schema: the home-page project list hard-indexes
    # saved_at/total/done and silently drops projects missing them.
    from datetime import datetime
    ws = {
        "id": project_id,
        "filename": filename,
        "lang_pair": lang_pair,
        "pipeline": "academic",
        "project_type": data.get("project_type", "book_translation"),
        "active_index": 0,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(new_segments),
        "done": total_done,
        "segments": new_segments,
    }
    # Preserve segments_meta if present
    if data.get("segments_meta"):
        ws["segments_meta"] = data["segments_meta"]

    project_path.write_text(
        json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Repaired project written to: {project_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())