# import_book.py
#
# One-command book importer.
# Put your PDF in data/books/ and run:
#   python import_book.py data/books/book.pdf
#
# VL server auto-starts for PDFs. Re-running picks up cached pages.

import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

logging.basicConfig(level=logging.WARNING, format="   ! %(message)s")

from translate_core.doc_parser import DocumentParser


def cli_progress(phase: str, current: int, total: int, cached: bool = False):
    """Print one line per page with phase, page type when available."""
    pct = int((current + 1) / total * 100)
    mark = "✓" if cached else "·"
    print(f"  {mark} [{phase}] {current + 1}/{total} ({pct}%)", flush=True)


def sanitize_lang_pair(pair: str) -> str:
    if not pair:
        return "en->sl"
    cleaned = pair.strip().lower()
    if cleaned == "en-":
        return "en->sl"
    if cleaned == "sl-":
        return "sl->en"
    if "-" in cleaned and "->" not in cleaned:
        cleaned = cleaned.replace("-", "->")
    if "->" not in cleaned:
        return "en->sl"
    return cleaned


def import_book(file_path: str, lang_pair: str = "en->sl", use_vl: bool = True):
    path = Path(file_path)
    if not path.exists():
        print(f"[ERROR] File not found: {path.resolve()}")
        return

    is_pdf = path.suffix.lower() == ".pdf"
    if use_vl and not is_pdf:
        use_vl = False

    clean_pair = sanitize_lang_pair(lang_pair)
    print(f"\n📖 {path.name}  ({clean_pair})", flush=True)

    # ── Start VL server if needed ─────────────────────────────────────
    server = None
    vl_result = None

    if use_vl:
        from translate_core.vl_server import VLMServerManager

        print("   Starting VL server…", flush=True)
        server = VLMServerManager()
        if not server.start(timeout=180):
            print("   ✗ Failed. Falling back to MarkItDown.", flush=True)
            server = None
            use_vl = False

    if use_vl:
        import fitz
        with fitz.open(str(path)) as doc:
            total_pages = len(doc)
        mins_lo = total_pages * 2.5 // 60 + 1
        mins_hi = total_pages * 4 // 60 + 1
        print(f"   Parsing {total_pages} pages (est. {mins_lo}–{mins_hi} min, cached pages skipped)", flush=True)
        print()

    if not use_vl:
        print("   Parsing with MarkItDown…")

    # ── Parse ─────────────────────────────────────────────────────────
    parser = DocumentParser()
    try:
        cache_dir = Path("data/.vl_cache") / path.stem if use_vl else None
        md_text, segments_meta = parser.to_markdown_with_meta(
            path,
            preprocess=True,
            use_vl=use_vl,
            vl_cache_dir=cache_dir,
            progress_callback=cli_progress if use_vl else None,
        )
    except Exception as ex:
        print(f"\n[ERROR] Parse failed: {ex}")
        if use_vl:
            print("   pip install mlx-vlm")
        else:
            print("   pip install 'markitdown[pdf]'")
        return
    finally:
        if server:
            server.stop()

    # ── Segment ────────────────────────────────────────────────────────
    # Use the smart paragraph splitter (handles PyMuPDF's indent-based
    # paragraph boundaries, de-hyphenates wrapped words, caps long
    # paragraphs at ~10 sentences so segments stay editable).
    from translate_core.vl_parser import split_paragraphs as _split_paragraphs

    segments = []
    for txt in _split_paragraphs(md_text):
        segments.append({"id": len(segments), "source": txt, "target": "", "status": "pending"})

    if not segments:
        print("[ERROR] No text extracted from document.")
        return

    # ── Save ───────────────────────────────────────────────────────────
    project_id = str(uuid.uuid4())[:8]
    projects_dir = Path("./data/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    (projects_dir / f"{project_id}{path.suffix}").write_bytes(path.read_bytes())

    ws = {
        "id": project_id,
        "filename": path.name,
        "lang_pair": clean_pair,
        "active_index": 0,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(segments),
        "done": 0,
        "segments": segments,
    }
    if segments_meta:
        ws["segments_meta"] = segments_meta

    target_json = projects_dir / f"{project_id}.json"
    target_json.write_text(json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8")

    vl_result = getattr(parser, "_last_vl_result", None)
    if vl_result is not None:
        from translate_core.vl_parser import BookOutline
        outline_path = projects_dir / f"{project_id}_outline.json"
        outline_data = {
            "entries": [
                {
                    "level": e.level,
                    "kind": e.kind,
                    "number": e.number,
                    "title": e.title,
                    "page_number": e.page_number,
                    "source_page": e.source_page,
                }
                for e in vl_result.outline.entries
            ],
            "page_to_chapter": vl_result.outline.page_to_chapter,
            "reconciliation_warnings": vl_result.outline.reconciliation_warnings,
        }
        outline_path.write_text(json.dumps(outline_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n✅ Done!")
    print(f"   Project:  {project_id}")
    print(f"   Language: {clean_pair}")
    print(f"   Segments: {len(segments)}")
    if vl_result is not None:
        print(f"   Pages:    {vl_result.total_pages}")
        types = {}
        for cls in vl_result.page_classifications:
            t = cls.page_type.value
            types[t] = types.get(t, 0) + 1
        for t, c in sorted(types.items(), key=lambda x: -x[1]):
            print(f"     {c:3d} {t}")
    print(f"   File:     {target_json}")
    print(f"\n   Open localhost:8080 to translate")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Import a book. VL auto-starts for PDFs.",
    )
    ap.add_argument("path", help="PDF or DOCX file")
    ap.add_argument("lang_pair", nargs="?", default="en->sl", help="en->sl (default)")
    ap.add_argument("--no-vl", action="store_true", help="Use MarkItDown instead of VL")
    args = ap.parse_args()

    import_book(args.path, args.lang_pair, use_vl=not args.no_vl)