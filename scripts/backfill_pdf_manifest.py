#!/usr/bin/env python3
"""scripts/backfill_pdf_manifest.py

Backfill a PDF-origin translation project with the paragraph-formatting
manifest (pdf_para_idx, heading_level, para_align, para_indent_in,
is_blockquote) WITHOUT touching target/status/source/id.

Usage:
    python scripts/backfill_pdf_manifest.py <project_id>

Looks for data/projects/<project_id>.pdf and data/projects/<project_id>.json.
Writes a .bak backup before modifying the JSON. Only adds manifest keys to
segments; never alters existing keys. Re-runnable (idempotent).

This brings a project created before the manifest-capture feature up to the
new schema so the Maska-styled DOCX export reconstructs original paragraphs.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.pdf_format_capture import (
    capture_paragraph_manifest,
    attach_manifest_to_segments,
)


def backfill(project_id: str, projects_dir: Path | None = None) -> None:
    projects_dir = projects_dir or Path("data/projects")
    pdf_path = projects_dir / f"{project_id}.pdf"
    json_path = projects_dir / f"{project_id}.json"

    if not pdf_path.exists():
        print(f"ERROR: source PDF not found: {pdf_path}")
        sys.exit(1)
    if not json_path.exists():
        print(f"ERROR: project JSON not found: {json_path}")
        sys.exit(1)

    # Load project
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data.get("segments", [])
    if not segments:
        print("ERROR: project has no segments")
        sys.exit(1)

    done_before = sum(1 for s in segments if s.get("status") == "done")
    print(f"Project: {project_id}")
    print(f"  segments: {len(segments)}, done: {done_before}")
    print(f"  source PDF: {pdf_path.name} ({pdf_path.stat().st_size // 1024} KB)")

    # Snapshot the original target/status for a post-backfill integrity check
    orig_targets = [s.get("target", "") for s in segments]
    orig_status = [s.get("status", "") for s in segments]
    orig_sources = [s.get("source", "") for s in segments]
    orig_ids = [s.get("id") for s in segments]

    # Capture manifest from the PDF and attach to segments (additive only)
    print("  capturing manifest…")
    manifest = capture_paragraph_manifest(pdf_path)
    print(f"  PDF blocks: {len(manifest.paragraphs)}, body_font_size: {manifest.body_font_size}")

    # Clear any stale manifest keys so attach is clean (idempotent re-run)
    for s in segments:
        for k in ("pdf_para_idx", "heading_level", "para_align", "para_indent_in", "is_blockquote"):
            s.pop(k, None)

    matched = attach_manifest_to_segments(segments, manifest)
    print(f"  manifest attached: {matched}/{len(segments)}")

    # Integrity check: target/status/source/id MUST be byte-identical
    assert len(segments) == len(orig_targets)
    for i, s in enumerate(segments):
        assert s.get("target", "") == orig_targets[i], f"target changed at seg {i}"
        assert s.get("status", "") == orig_status[i], f"status changed at seg {i}"
        assert s.get("source", "") == orig_sources[i], f"source changed at seg {i}"
        assert s.get("id") == orig_ids[i], f"id changed at seg {i}"
    done_after = sum(1 for s in segments if s.get("status") == "done")
    assert done_after == done_before, f"done count changed: {done_before} → {done_after}"
    print(f"  integrity OK: target/status/source/id untouched, done={done_after}")

    # Backup + write
    bak = json_path.with_suffix(".json.bak")
    if not bak.exists():
        shutil.copy2(json_path, bak)
        print(f"  backup: {bak.name}")
    else:
        print(f"  backup exists: {bak.name} (not overwritten)")

    data["segments"] = segments
    # Ensure segments_meta persists (fix the silent-drop bug for existing projects)
    if "segments_meta" not in data:
        from translate_core.book_outline import build_segments_meta
        data["segments_meta"] = build_segments_meta(segments)
        print(f"  added segments_meta: {len(data['segments_meta'])} entries")
    # Ensure house_style persists (defaults to Maska for existing projects)
    if "house_style" not in data:
        import config
        data["house_style"] = config.DEFAULT_HOUSE_STYLE
        print(f"  added house_style: {data['house_style']}")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  wrote: {json_path.name}")
    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/backfill_pdf_manifest.py <project_id>")
        sys.exit(1)
    backfill(sys.argv[1])