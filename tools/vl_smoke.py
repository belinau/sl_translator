#!/usr/bin/env python3
"""
tools/vl_smoke.py — Run Pass 1 (classify) on the first N pages of a PDF
and dump the classifications as JSON for eyeballing.

Usage:
    python tools/vl_smoke.py data/books/sample.pdf [--pages 10] [--cache-dir data/.vl_cache/sample]
    python tools/vl_smoke.py data/books/sample.pdf --rebuild

Requires mlx_vlm.server running locally (or will start it automatically with --start-server).
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.vl_parser import (
    VLBookParser,
    PageClassification,
    parse_classification,
)
from translate_core.vl_server import VLMServerManager


def main():
    ap = argparse.ArgumentParser(description="VL smoke test: classify pages of a PDF")
    ap.add_argument("pdf", help="Path to the PDF file")
    ap.add_argument(
        "--pages", type=int, default=10, help="Number of pages to classify (default: 10)"
    )
    ap.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="Cache directory (default: data/.vl_cache/{pdf_stem})",
    )
    ap.add_argument(
        "--rebuild", action="store_true", help="Force cache invalidation"
    )
    ap.add_argument(
        "--start-server", action="store_true", help="Start VL server if not running"
    )
    ap.add_argument(
        "--extract", action="store_true", help="Also run Pass 2 extraction on complex pages"
    )
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

    cache_dir = Path(args.cache_dir) if args.cache_dir else Path(f"data/.vl_cache/{pdf_path.stem}")
    if args.rebuild and cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir)
        print(f"Cache cleared: {cache_dir}")

    server = None
    if args.start_server:
        server = VLMServerManager()
        print("Starting VL server...")
        if not server.start(timeout=180):
            print("ERROR: VL server failed to start. Check data/.vl_cache/vl_server.log")
            sys.exit(1)
        print("VL server ready.")

    try:
        import fitz

        with fitz.open(str(pdf_path)) as doc:
            total = len(doc)
            n = min(args.pages, total)
            print(f"Classifying pages 0-{n-1} of {total}...")

            parser = VLBookParser()

            classifications = []
            for i in range(n):
                t0 = time.time()
                from translate_core.vl_parser import _cache_get, _cache_put, _render_from_doc

                cached = _cache_get(cache_dir, "cls", i)
                if cached is not None:
                    cls = cached
                    elapsed = 0
                    print(f"  Page {i}: {cls.page_type.value} (cached)")
                else:
                    img = _render_from_doc(doc, i)
                    cls = parser._classify_page(img, i)
                    _cache_put(cache_dir, "cls", i, cls)
                    elapsed = time.time() - t0
                    print(f"  Page {i}: {cls.page_type.value} ({elapsed:.1f}s)")

                classifications.append(cls)

            if args.extract:
                from translate_core.vl_parser import COMPLEX_TYPES, _cache_get as ext_cache_get, _cache_put as ext_cache_put

                print(f"\nExtracting complex pages...")
                for i, cls in enumerate(classifications):
                    if cls.page_type in COMPLEX_TYPES:
                        cached = ext_cache_get(cache_dir, "ext", i)
                        if cached is not None:
                            ext = cached
                            print(f"  Page {i}: extracted (cached)")
                        else:
                            img = _render_from_doc(doc, i)
                            t0 = time.time()
                            ext = parser._extract_page(img, cls)
                            ext_cache_put(cache_dir, "ext", i, ext)
                            print(f"  Page {i}: extracted ({time.time()-t0:.1f}s)")

        # Dump classifications as JSON
        output = {
            "source_file": str(pdf_path),
            "pages_classified": len(classifications),
            "classifications": [
                {
                    "page_number": c.page_number,
                    "page_type": c.page_type.value,
                    "has_footnotes": c.has_footnotes,
                    "has_running_header": c.has_running_header,
                    "has_running_footer": c.has_running_footer,
                    "header_text": c.header_text,
                    "footer_text": c.footer_text,
                    "column_count": c.column_count,
                    "chapter_title": c.chapter_title,
                }
                for c in classifications
            ],
        }

        out_path = cache_dir / "classifications.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nClassifications written to: {out_path}")

    finally:
        if server:
            server.stop()
            print("VL server stopped.")


if __name__ == "__main__":
    main()