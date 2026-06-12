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
    """Matching-only normalisation: strip footnote markers / md heading
    prefixes, drop TOC locator tails (" — ix"), collapse letter-spaced
    titles ("F e m i n i s t" -> "feminist"), collapse whitespace,
    casefold (extraction backends disagree on capitalisation)."""
    text = _FN_MARKER_RE.sub("", source)
    text = re.sub(r"^[#>\s]+", "", text)
    text = re.sub(r"\s*[—–-]\s*(?:\d{1,4}|[ivxlcdmIVXLCDM]{1,7})\s*$", "", text)
    # collapse single-letter spacing runs (title-page typography)
    text = re.sub(r"(?<=\b\w)\s+(?=\w\b)", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


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

    # Pass 2 — span consolidation: every remaining DONE translation is
    # carried over verbatim. Two stages:
    #   A) Per-segment: each old done paragraph matches its own span of
    #      new segments — keeps per-paragraph granularity for the translator.
    #   B) Run fallback: only fragments that failed alone (mid-sentence
    #      splits from the legacy importer) are merged with their consecutive
    #      neighbours and retried with an UNCONSTRAINED search (ignoring
    #      claimed segments) because the span must grow through segments
    #      that Stage A already claimed. Overlapping Stage A spans are
    #      evicted in favour of the run — the run is the authoritative
    #      mapping for mid-sentence fragments.
    consolidated = 0
    unrecovered: list[str] = []
    try:
        from rapidfuzz import fuzz
    except ImportError:
        fuzz = None
    if fuzz is not None:
        already_done = {
            _normalise(n["source"]) for n in new_segments if n["status"] == "done"
        }
        already_idx = {
            i for i, s in enumerate(old_segments)
            if s.get("status") == "done" and s.get("target", "").strip()
            and _normalise(s.get("source", "")) in already_done
        }
        pending_indices = [
            i for i, s in enumerate(old_segments)
            if s.get("status") == "done" and s.get("target", "").strip()
            and i not in already_idx
        ]

        new_keys = [_normalise(s["source"]) for s in new_segments]
        claimed = [s["status"] == "done" for s in new_segments]
        spans: list[tuple[int, int, str, int]] = []  # (start, end, target, count)
        audit: list[tuple[str, int, int, float, int]] = []
        matched_indices: set[int] = set()

        def _find_span(old_key: str, constrain: bool = True, partial: bool = False) -> tuple[float, int, int] | None:
            best: tuple[float, int, int] | None = None
            for j in range(len(new_segments)):
                if constrain and claimed[j]:
                    continue
                if not new_keys[j]:
                    continue
                if fuzz.ratio(old_key[:40], new_keys[j][:40]) < 40:
                    continue
                span_text = new_keys[j]
                score = fuzz.partial_ratio(old_key, span_text) if partial else fuzz.ratio(old_key, span_text)
                k = j
                while (
                    k + 1 < len(new_segments)
                    and (not constrain or not claimed[k + 1])
                    and len(span_text) < len(old_key) * 2
                ):
                    cand = span_text + " " + new_keys[k + 1]
                    cand_score = fuzz.partial_ratio(old_key, cand) if partial else fuzz.ratio(old_key, cand)
                    if cand_score >= score:
                        span_text, score, k = cand, cand_score, k + 1
                    else:
                        break
                if best is None or score > best[0]:
                    best = (score, j, k)
            return best

        # ── Stage A: per-segment (constrained) ─────────────────────────
        for idx in pending_indices:
            old_seg = old_segments[idx]
            old_key = _normalise(old_seg.get("source", ""))
            if not old_key:
                continue
            result = _find_span(old_key, constrain=True)
            if result is not None and result[0] >= 85.0:
                score, j, k = result
                for x in range(j, k + 1):
                    claimed[x] = True
                spans.append((j, k, old_seg["target"], 1))
                audit.append((old_key[:60], j, k, score, 1))
                matched_indices.add(idx)

        # ── Stage B: run fallback (unconstrained, evicts overlaps) ──────
        failures = [i for i in pending_indices if i not in matched_indices]
        runs: list[tuple[str, str, int]] = []
        cur_run: list[int] = []
        for fi, idx in enumerate(failures):
            if cur_run and idx == failures[fi - 1] + 1:
                cur_run.append(idx)
            else:
                if cur_run:
                    runs.append((
                        " ".join(old_segments[i].get("source", "") for i in cur_run),
                        " ".join(old_segments[i]["target"] for i in cur_run),
                        len(cur_run),
                    ))
                cur_run = [idx]
        if cur_run:
            runs.append((
                " ".join(old_segments[i].get("source", "") for i in cur_run),
                " ".join(old_segments[i]["target"] for i in cur_run),
                len(cur_run),
            ))

        for src, tgt, cnt in runs:
            old_key = _normalise(src)
            if not old_key:
                continue
            # Try full ratio first (works for most runs); fall back to
            # partial_ratio only for mid-sentence fragments that need it.
            result = _find_span(old_key, constrain=False, partial=False)
            if result is None or result[0] < 85.0:
                result = _find_span(old_key, constrain=False, partial=True)
            if result is not None and result[0] >= 85.0:
                score, j, k = result
                overlap = [s for s in spans if not (s[1] < j or s[0] > k)]
                for ov in overlap:
                    spans.remove(ov)
                for x in range(j, k + 1):
                    claimed[x] = True
                spans.append((j, k, tgt, cnt))
                audit.append((old_key[:60], j, k, score, cnt))
            else:
                unrecovered.append(src[:70])
        if spans:
            span_start = {j: (k, tgt, cnt) for j, k, tgt, cnt in spans}
            span_member = {x for j, k, _, _ in spans for x in range(j, k + 1)}
            rebuilt: list[dict] = []
            i = 0
            while i < len(new_segments):
                if i in span_start:
                    k, tgt, _ = span_start[i]
                    rebuilt.append({
                        "id": 0,
                        "source": " ".join(
                            new_segments[x]["source"] for x in range(i, k + 1)
                        ),
                        "target": tgt,
                        "status": "done",
                    })
                    i = k + 1
                elif i in span_member:
                    i += 1
                else:
                    rebuilt.append(new_segments[i])
                    i += 1
            for n, seg in enumerate(rebuilt):
                seg["id"] = n
            new_segments = rebuilt
            consolidated = len(spans)

        if audit:
            print("\n  Consolidated done spans (old -> new segs), audit:")
            for ok, j, k, sc, cnt in audit:
                frag = f" ({cnt} old fragments)" if cnt > 1 else ""
                print(f"    [{j}..{k}] {sc:.0f}%{frag}  {ok!r}")
        if unrecovered:
            print("\n  UNRECOVERED done translations (no span >= 85%):")
            for u in unrecovered:
                print(f"    {u!r}")

    total_done = sum(1 for s in new_segments if s.get("status") == "done")
    print(f"\n  New segments: {len(new_segments)}")
    print(f"  Exact re-attached: {attached}")
    print(f"  Consolidated done spans: {consolidated}")
    print(f"  Done after attach: {total_done} (old project had "
          f"{sum(1 for s in old_segments if s.get('status') == 'done')})")

    # ── Completeness gate: EVERY old translation must be present ────────
    # Each old done target must appear verbatim inside some new done
    # target (runs concatenate fragment targets, so substring check).
    new_done_targets = [s["target"] for s in new_segments if s.get("status") == "done"]
    missing_targets: list[str] = []
    old_done_count = 0
    for s in old_segments:
        tgt = s.get("target", "").strip()
        if s.get("status") != "done" or not tgt:
            continue
        old_done_count += 1
        if not any(tgt in nt for nt in new_done_targets):
            missing_targets.append(tgt[:70])
    print(f"\n  Translation completeness: "
          f"{old_done_count - len(missing_targets)}/{old_done_count} present verbatim")
    if missing_targets:
        print("  MISSING translations:")
        for m in missing_targets:
            print(f"    {m!r}")

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

    if missing_targets:
        print("\nERROR: refusing to apply — not every translation could be "
              "carried over. Fix matching (or re-run dry-run) first; the "
              "project on disk is untouched.")
        return 1

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