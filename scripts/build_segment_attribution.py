"""
Build segment-to-container mapping from source DOCX files.

Strategy:
1. Parse ALL accessible source DOCX/MD files (local + Google Drive)
2. Deduplicate sources (multiple slugs may share the same DOCX)
3. Build n-gram index for each unique source
4. Match each TM segment against all sources
5. Output: _segment_attribution_final.json with {origin: {idx: container_slug}}
"""

from __future__ import annotations

import json
import os
import re
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.tm import TranslationMemory

# ── Constants ──────────────────────────────────────────────────────────

GDRIVE = Path("/Users/bel/Library/CloudStorage/GoogleDrive-urban@bel.si/My Drive/prevodi in objave - urban/")
KG_DB = config.KG_DB_PATH
STA_PATH = Path("data/segment_title_attribution.json")
S2B_PATH = Path("data/quarantine/_segment_to_book.json")
OUTPUT_PATH = Path("data/segment_attribution_ngram.json")

NGRAM_SIZE = 4
MIN_WORDS = 3
MATCH_THRESHOLD = 0.3  # At least 30% of n-grams must match

# ── Slug mapping: known containers in KG → source file paths ──────────

# Local source files (always available)
LOCAL_SOURCES = {
    "kunst-zivljenje-umetnosti": "data/books/Skrb_07-15-SL-en-GB-FULL-FINAL-CLEAN.docx",
    "zaloznik-jasmina-zavzemanje-prostora-2024": "data/books/Jasmina Založnik_Zavzemanje prostorov_lektura-POTRJENA.doc.md",
    "okri-ben-cesta-sestradanih-2016": "data/books/The Famished Road - Ben Okri.docx.md",
}

# ── Helpers ─────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Normalize text for n-gram comparison."""
    text = text.lower().strip()
    text = re.sub(r"['\"\u2018\u2019\u201c\u201d]", "", text)
    text = re.sub(r"[\u2013\u2014]", "-", text)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def build_ngrams(text: str, n: int = NGRAM_SIZE) -> Set[str]:
    """Build set of n-grams from normalized text."""
    words = normalize(text).split()
    if len(words) < n:
        return set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def load_docx(path: str) -> Optional[str]:
    """Load text from a DOCX file. Returns None on error."""
    try:
        import docx
        doc = docx.Document(path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return None


def load_text(path: str) -> Optional[str]:
    """Load text from MD or text file. Returns None on error."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


# ── Main pipeline ──────────────────────────────────────────────────────

def build_source_corpus() -> Dict[str, Tuple[str, str]]:
    """Build source text corpus from all available DOCX files.
    
    Returns: {slug: (path, text)}
    """
    import docx
    
    corpus: Dict[str, Tuple[str, str]] = {}
    
    # 1. Local sources
    for slug, path in LOCAL_SOURCES.items():
        if path.endswith(".md"):
            text = load_text(path)
        else:
            text = load_docx(path)
        if text and len(text) > 500:
            corpus[slug] = (path, text)
            print(f"  Local: {slug}: {len(text):,} chars")
    
    # 2. Article DOCX files from _article_docx_map.json
    adm = json.load(open("data/quarantine/_article_docx_map.json"))
    loaded_paths = set()
    for slug, paths in adm.items():
        for path in paths:
            if path in loaded_paths:
                continue
            if not os.path.exists(path):
                continue
            text = load_docx(path)
            if text and len(text) > 500:
                corpus[slug] = (path, text)
                loaded_paths.add(path)
                print(f"  Article: {slug}: {len(text):,} chars")
                break
    
    # 3. Google Drive DOCX files (key containers)
    # Walk known directories for larger DOCX files that likely correspond to
    # translation containers
    key_dirs = [
        "Cesta sestradanih Okri",
        "Bojana Kunst Življenje umetnosti",
        "Jasmina knjiga Maska",
        "Feminist, Queer, Crip",
        "Lahkotnost",
        "Lažna dojka",
        "Mesto žensk",
        "Mending the Invisible",
        "Aplavz Krpanje nevidnega",
        "Velika važnost malih priča",
        "Mikropolitike kuriranja",
        "Take a number",
        "DSPS",
        "Jubilej MG",
    ]
    
    for dirname in key_dirs:
        dirpath = GDRIVE / dirname
        if not dirpath.exists():
            continue
        
        # Find the largest DOCX file that looks like a main source
        best_file = None
        best_size = 0
        best_text = None
        
        for fname in os.listdir(dirpath):
            if not fname.endswith(".docx") or fname.startswith("~"):
                continue
            fpath = str(dirpath / fname)
            try:
                size = os.path.getsize(fpath)
            except:
                continue
            if size < 5000 or size < best_size:
                continue
            
            # Prefer FINAL/CLEAN versions
            fname_lower = fname.lower()
            if "final" in fname_lower or "clean" in fname_lower or "potrj" in fname_lower:
                size *= 2  # Boost priority
            
            
            text = load_docx(fpath)
            text_len = len(text) if text else 0
            if text_len > (len(best_text) if best_text else 0):
                best_file = fpath
                best_size = size
                best_text = text
        if best_text:
            slug = dirname.lower().replace(" ", "-").replace("č", "c").replace("š", "s").replace("ž", "z")
            corpus[f"gdrive-{slug}"] = (best_file, best_text)
            print(f"  GDrive: {dirname}: {len(best_text):,} chars")
    
    return corpus


def build_ngram_index(corpus: Dict[str, Tuple[str, str]]) -> Dict[str, Set[str]]:
    """Build n-gram index for each source."""
    index = {}
    for slug, (path, text) in corpus.items():
        ngrams = build_ngrams(text)
        index[slug] = ngrams
        print(f"  {slug}: {len(ngrams):,} n-grams")
    return index


def match_segments(
    tm: TranslationMemory,
    ngram_index: Dict[str, Set[str]],
    corpus: Dict[str, Tuple[str, str]],
) -> Dict[str, Dict[int, List[Tuple[str, float, int]]]]:
    """Match each TM segment against source n-gram indexes.
    
    Returns: {origin: {seg_idx: [(slug, ratio, hits), ...]}}
    """
    results: Dict[str, Dict[int, List[Tuple[str, float, int]]]] = {}
    
    for origin in ["big-EN-SL.tmx", "2022-SL-EN.tmx", "mglc-EN-SL.tmx", "MGLC-SL-EN.tmx"]:
        entries = [e for e in tm.entries if e.get("origin") == origin]
        origin_results: Dict[int, List[Tuple[str, float, int]]] = {}
        
        for i, e in enumerate(entries):
            en = e.get("source", "").strip()
            sl = e.get("target", "").strip()
            
            en_ngrams = build_ngrams(en) if len(en.split()) >= MIN_WORDS else set()
            sl_ngrams = build_ngrams(sl) if len(sl.split()) >= MIN_WORDS else set()
            
            if not en_ngrams and not sl_ngrams:
                continue  # Too short to match
            
            best_matches = []
            for slug, src_ngrams in ngram_index.items():
                en_hits = len(en_ngrams & src_ngrams) if en_ngrams else 0
                sl_hits = len(sl_ngrams & src_ngrams) if sl_ngrams else 0
                
                total_ngrams = max(len(en_ngrams) + len(sl_ngrams), 1)
                total_hits = en_hits + sl_hits
                ratio = total_hits / total_ngrams
                
                if ratio >= MATCH_THRESHOLD:
                    best_matches.append((slug, ratio, total_hits))
            
            if best_matches:
                best_matches.sort(key=lambda x: -x[1])
                origin_results[i] = best_matches
        
        results[origin] = origin_results
        matched = len(origin_results)
        total = len(entries)
        print(f"  {origin}: {matched}/{total} segments matched ({matched/total*100:.1f}%)")
    
    return results


def resolve_shared_sources(
    results: Dict[str, Dict[int, List[Tuple[str, float, int]]]],
    corpus: Dict[str, Tuple[str, str]],
) -> Dict[str, Dict[int, str]]:
    """Resolve shared DOCX sources to their most likely container.
    
    When multiple slugs share the same DOCX file, segments that match
    that DOCX are attributed to the slug with the highest match ratio,
    unless we can determine the correct container from context.
    
    Returns: {origin: {seg_idx: container_slug}}
    """
    # Map from source text hash to list of slugs
    text_to_slugs: Dict[int, List[str]] = defaultdict(list)
    for slug, (path, text) in corpus.items():
        text_to_slugs[hash(text)].append(slug)
    
    # For shared sources, we need additional heuristics
    # For now, just use the best match
    resolved: Dict[str, Dict[int, str]] = {}
    
    for origin, seg_results in results.items():
        resolved[origin] = {}
        for idx, matches in seg_results.items():
            best_slug = matches[0][0]  # Highest ratio
            resolved[origin][idx] = best_slug
    
    return resolved


def main():
    print("=" * 70)
    print("Segment-to-Container Attribution Pipeline")
    print("=" * 70)
    
    # 1. Build source corpus
    print("\n[1/5] Building source corpus...")
    import docx  # ensure import
    corpus = build_source_corpus()
    print(f"\n  Total unique sources: {len(corpus)}")
    
    # 2. Deduplicate (preserve (path, text) tuple shape — required by build_ngram_index)
    print("\n[2/5] Deduplicating sources...")
    unique_sources: Dict[int, Tuple[str, str, str]] = {}  # hash -> (slug, path, text)
    slug_to_canonical = {}
    for slug, (path, text) in corpus.items():
        text_hash = hash(text)
        if text_hash not in unique_sources:
            unique_sources[text_hash] = (slug, path, text)
            slug_to_canonical[slug] = slug
        else:
            canonical_slug = unique_sources[text_hash][0]
            slug_to_canonical[slug] = canonical_slug
            print(f"  {slug} shares text with {canonical_slug}")

    # Rebuild corpus with unique sources only — keep tuple shape
    deduped_corpus: Dict[str, Tuple[str, str]] = {}
    for slug, path, text in unique_sources.values():
        deduped_corpus[slug] = (path, text)

    print(f"  Unique sources after dedup: {len(deduped_corpus)}")
    
    # 3. Build n-gram indexes
    print("\n[3/5] Building n-gram indexes...")
    ngram_index = build_ngram_index(deduped_corpus)
    
    # 4. Match segments
    print("\n[4/5] Matching TM segments against sources...")
    tm = TranslationMemory()
    results = match_segments(tm, ngram_index, corpus)
    
    # 5. Resolve and output
    print("\n[5/5] Resolving and outputting...")
    resolved = resolve_shared_sources(results, corpus)
    
    # Count attribution
    total_attributed = 0
    total_segments = 0
    container_counts: Counter = Counter()
    
    for origin, seg_map in resolved.items():
        total_attributed += len(seg_map)
        total_segments += len([e for e in tm.entries if e.get("origin") == origin])
        for idx, slug in seg_map.items():
            container_counts[slug] += 1
    
    print(f"\n  Total attributed: {total_attributed}/{total_segments} ({total_attributed/max(total_segments,1)*100:.1f}%)")
    print(f"\n  Attribution by container:")
    for slug, count in container_counts.most_common(20):
        print(f"    {slug}: {count}")
    
    # 6. Normalize attribution slugs to canonical COBISS source: IDs.
    # The ngram corpus uses heterogeneous slugs (e.g. "kunst-zivljenje-umetnosti",
    # "gdrive-bojana-kunst-zivljenje-umetnosti") that don't necessarily match the
    # COBISS-built container IDs in the KG. The run_entity_extraction loader
    # requires values that start with "source:". Build a lookup from KG containers
    # and map every output slug.
    print("\n[6/5] Normalizing slugs to KG container IDs...")
    try:
        kg_data = json.loads(Path(KG_DB).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  WARNING: could not load KG at {KG_DB} ({e}); skipping normalization, slugs emitted bare")
        kg_data = None

    CONTAINER_TYPES = {"book_translation", "article_translation",
                       "festival_programme", "exhibition_catalogue"}
    slug_to_source_id: Dict[str, str] = {}
    if kg_data:
        # Build a title→source_id index from the KG's existing containers.
        # When the ngram slug stem appears in the title (case-insensitive,
        # diacritic-stripped), accept that as the canonical mapping.
        import unicodedata as _ud
        def _strip(s: str) -> str:
            n = _ud.normalize("NFKD", s)
            return "".join(c for c in n if not _ud.combining(c)).lower()

        kg_containers = [n for n in kg_data.get("nodes", [])
                         if n.get("project_type") in CONTAINER_TYPES]
        print(f"  {len(kg_containers)} containers in KG")

        for ngram_slug in container_counts:
            # First try a direct prefixed match
            direct = f"source:{ngram_slug.removeprefix('gdrive-')}"
            if any(n["id"] == direct for n in kg_containers):
                slug_to_source_id[ngram_slug] = direct
                continue
            # Otherwise fuzzy-match by stem against container titles + ids
            stem = _strip(ngram_slug.removeprefix("gdrive-").replace("-", " "))
            best = None
            best_overlap = 0
            stem_tokens = set(stem.split())
            for c in kg_containers:
                cand = _strip((c.get("title_orig") or c.get("title") or "") + " " + c["id"].removeprefix("source:"))
                cand_tokens = set(cand.split())
                overlap = len(stem_tokens & cand_tokens)
                if overlap > best_overlap and overlap >= 2:
                    best = c["id"]
                    best_overlap = overlap
            if best:
                slug_to_source_id[ngram_slug] = best

        matched = len(slug_to_source_id)
        print(f"  Mapped {matched}/{len(container_counts)} ngram slugs to KG container IDs")
        for ngram_slug, cobiss_id in list(slug_to_source_id.items())[:10]:
            print(f"    {ngram_slug}  →  {cobiss_id}")

    # Emit attribution with normalized source: IDs (curator loader-compatible)
    output = {}
    skipped_no_mapping: Counter = Counter()
    for origin, seg_map in resolved.items():
        output[origin] = {}
        for idx, slug in seg_map.items():
            cobiss_id = slug_to_source_id.get(slug)
            if cobiss_id:
                output[origin][str(idx)] = cobiss_id
            else:
                # Slug couldn't be resolved to a KG container; preserve raw for
                # debugging but loader will skip it (no "source:" prefix)
                output[origin][str(idx)] = slug
                skipped_no_mapping[slug] += 1

    if skipped_no_mapping:
        print(f"\n  Unmapped slugs (loader will skip these):")
        for slug, n in skipped_no_mapping.most_common(10):
            print(f"    {slug}: {n} segments")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  Saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()