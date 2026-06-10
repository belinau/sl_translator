# import_book.py
#
# One-command book importer.
# Put your PDF or DOCX in data/books/ and run:
#   python import_book.py data/books/book.pdf
#   python import_book.py data/books/book.docx

from __future__ import annotations

import logging
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from translate_core.doc_parser import DocumentParser
sys.path.append(str(Path(__file__).parent))

log = logging.getLogger(__name__)

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


def import_book(file_path: str, lang_pair: str = "en->sl"):
    path = Path(file_path)
    if not path.exists():
        log.error(f"File not found: {path.resolve()}")
        return

    clean_pair = sanitize_lang_pair(lang_pair)
    suffix = path.suffix.lower()
    log.info(f"\n📖 {path.name}  ({clean_pair})", flush=True)

    # ── DOCX: direct python-docx paragraph extraction ────────────────────
    if suffix == ".docx":
        import docx as _docx
        log.info("   Parsing with python-docx…")
        try:
            doc = _docx.Document(str(path))
        except Exception as ex:
            log.error(f"\nDOCX parse failed: {ex}")
            return

        segments = []
        for i, p in enumerate(doc.paragraphs):
            txt = p.text.strip()
            if txt:
                segments.append({
                    "id": len(segments),
                    "source": txt,
                    "target": "",
                    "status": "pending",
                    "docx_para_idx": i,
                })

        segments_meta = []

    # ── PDF: MarkItDown fallback ──────────────────────────────────────────
    else:
        log.info("   Parsing with MarkItDown…")
        parser = DocumentParser()
        try:
            md_text, segments_meta = parser.to_markdown_with_meta(
                path, preprocess=True
            )
        except Exception as ex:
            log.error(f"\nParse failed: {ex}")
            log.info("   pip install 'markitdown[pdf]'")
            return

        # Use the smart paragraph splitter (handles PyMuPDF's indent-based
        # paragraph boundaries, de-hyphenates wrapped words, caps long
        # paragraphs at ~10 sentences so segments stay editable).
        from translate_core.book_outline import split_paragraphs as _split_paragraphs
        segments = []
        for txt in _split_paragraphs(md_text):
            segments.append({"id": len(segments), "source": txt, "target": "", "status": "pending"})

    if not segments:
        log.error("No text extracted from document.")
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

    # ── Summary ────────────────────────────────────────────────────────
    log.info("\n✅ Done!")
    log.info(f"   Project:  {project_id}")
    log.info(f"   Language: {clean_pair}")
    log.info(f"   Segments: {len(segments)}")
    log.info(f"   File:     {target_json}")
    log.info("\n   Open localhost:8080 to translate")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    import argparse

    ap = argparse.ArgumentParser(
        description="Import a PDF or DOCX as a new translation project.",
    )
    ap.add_argument("path", help="PDF or DOCX file")
    ap.add_argument("lang_pair", nargs="?", default="en->sl", help="en->sl (default)")
    args = ap.parse_args()
    import_book(args.path, args.lang_pair)