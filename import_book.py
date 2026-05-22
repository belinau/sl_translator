# import_book.py
#
# High-Reliability CLI Importer for heavy academic books (PDF/DOCX)
# Bypasses browser upload limits, runs pre-processing, and registers the project instantly.
# Auto-sanitizes trailing hyphen errors from shell redirection.
#
# Usage:
#   python import_book.py data/books/feminist-queer-crip-alison-kafer.pdf en->sl
#

import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Tuple

sys.path.append(str(Path(__file__).parent))

from translate_core.doc_parser import DocumentParser


def sanitize_lang_pair(pair: str) -> str:
    """
    Detect if the terminal shell intercepted the '>' character (eating '>sl' / '>en')
    and heal 'en-' or 'sl-' back into standard form on the fly.
    """
    if not pair:
        return "en->sl"

    cleaned = pair.strip().lower()

    # Check if the shell cut it off at "en-" or "sl-"
    if cleaned == "en-":
        print("⚠️ [Warning] Your terminal shell intercepted the '>' character.")
        print(
            "            We have automatically healed your input parameter 'en-' to 'en->sl'."
        )
        print(
            '            (To avoid this in the future, wrap the language pair in quotes: "en->sl")\n'
        )
        return "en->sl"

    if cleaned == "sl-":
        print("⚠️ [Warning] Your terminal shell intercepted the '>' character.")
        print(
            "            We have automatically healed your input parameter 'sl-' to 'sl->en'."
        )
        print(
            '            (To avoid this in the future, wrap the language pair in quotes: "sl->en")\n'
        )
        return "sl->en"

    # Normalize standard hyphen variations to "->"
    if "-" in cleaned and "->" not in cleaned:
        cleaned = cleaned.replace("-", "->")

    if "->" not in cleaned:
        return "en->sl"

    return cleaned


def import_book(file_path: str, lang_pair: str = "en->sl"):
    path = Path(file_path)
    if not path.exists():
        print(f"[ERROR] Book file not found at: {path.resolve()}")
        return

    # Sanitize language pair in case of shell redirections
    clean_pair = sanitize_lang_pair(lang_pair)

    print(f"[Importer] Loading and parsing book: {path.name}")
    print(f"           This can take up to a minute for heavy PDFs...")

    parser = DocumentParser()

    try:
        # Run Ingestion Preprocessing: Convert PDF to Markdown, convert endnotes ➔ footnotes,
        # and adapt hierarchical list configurations
        md_text = parser.to_markdown(path, preprocess=True)
    except Exception as ex:
        print(f"\n[CRITICAL ERROR] Failed to parse document: {ex}")
        print(
            "                 Ensure you have installed pdf support: pip install 'markitdown[pdf]'"
        )
        return

    # Segment the processed Markdown text by paragraph boundaries
    segments = []
    for block in md_text.split("\n\n"):
        txt = block.strip()
        if txt:
            segments.append(
                {"id": len(segments), "source": txt, "target": "", "status": "pending"}
            )

    if not segments:
        print("[ERROR] No paragraphs could be extracted from this document.")
        return

    # Generate a unique project ID
    project_id = str(uuid.uuid4())[:8]

    # Save the original file with its new project ID inside your existing project directory
    projects_dir = Path("./data/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    target_book_path = projects_dir / f"{project_id}{path.suffix}"
    target_book_path.write_bytes(path.read_bytes())

    # Build the workspace structure
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

    # Write the segmented JSON to register the project on the home screen
    target_json_path = projects_dir / f"{project_id}.json"
    target_json_path.write_text(
        json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n🎉 [SUCCESS] Book imported and segmented successfully!")
    print(f"             Project ID: {project_id}")
    print(f"             Language Direction: {clean_pair.upper()}")
    print(f"             Total Paragraphs Segments: {len(segments)}")
    print(f"             Segmented metadata saved to: {target_json_path}")
    print(
        f"             Open localhost:8080 - the project is ready to translate with 1 click!"
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_book.py <path_to_book> [lang_pair]")
        print(
            "Example: python import_book.py data/projects/Feminist_Queer_Crip.pdf en->sl"
        )
        sys.exit(1)

    path_arg = sys.argv[1]
    pair_arg = sys.argv[2] if len(sys.argv) > 2 else "en->sl"
    import_book(path_arg, pair_arg)
