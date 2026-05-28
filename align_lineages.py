# align_lineages.py
#
# Post-seed lineage and domain alignment.
# Analyzes TMX segment content using spaCy NER to detect authors, works,
# and domains, then clusters segments by source and assigns lineages/domains.
#
# Usage:
#   python align_lineages.py                  # Apply and save
#   python align_lineages.py --dry-run       # Preview changes without saving
#

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.tm import clean_xml

# ═══════════════════════════════════════════════════════════════════════
# Content-based domain detection
#
# These keywords are detected in the ENGLISH text of TM segments.
# When a segment contains these words, it signals a particular domain.
# The script counts how many segments in each TMX file match each domain
# and assigns the dominant domain to that file's lineage.
# ═══════════════════════════════════════════════════════════════════════

DOMAIN_KEYWORDS = {
    "visual-art": [
        "exhibition", "curating", "curated", "curation", "installation",
        "sculpture", "photograph", "gallery", "biennale", "museum",
        "painting", "visual", "canvas", "portrait", "print",
        "artistic", "artwork", "art piece", "exhibit",
    ],
    "performance": [
        "choreography", "choreograph", "dance", "performance", "performative",
        "theatre", "theater", "stage", "body", "movement",
        "rehearsal", "improvisation", "performer", "performing",
        "kostumografija",  # costume design (SL)
    ],
    "philosophy": [
        "epistemology", "ontology", "phenomenology", "philosophy",
        "dialectic", "theory", "concept", "speculative",
        "ideology", "hermeneutics", "deconstruction", "postmodern",
        "theoretical", "think", "thought",
    ],
    "feminist-queer": [
        "feminism", "feminist", "queer", "gender", "crip",
        "patriarchy", "intersectionality", "body politics",
        "masculinity", "inclusive", "femininity",
    ],
    "disability": [
        "disability", "accessibility", "wheelchair", "impairment",
        "rehabilitation", "disabled", "barrier-free",
    ],
    "music": [
        "music", "sound", "concert", "composition", "acoustic",
        "noise", "resonance", "instrument", "sonic", "audio",
    ],
    "literature": [
        "novel", "poetry", "narrative", "fiction", "translation",
        "poem", "prose", "literary", "writing", "text",
        "author", "book", "chapter",
    ],
    "politics": [
        "politics", "activism", "protest", "democracy",
        "social movement", "revolution", "power", "state",
        "political", "institution", "policy",
    ],
    "curatorial": [
        "curator", "curatorial", "curating", "curated", "curation",
        "exhibition", "display", "collection", "archiving",
        "museum", "gallery", "institution",
    ],
}

# Known author → lineage mapping (detected via NER or keyword)
KNOWN_AUTHORS = {
    "dimitrijević": "curatorial-exhibition",
    "branislav dimitrijević": "curatorial-exhibition",
    "tomaž grom": "contemporary-art-performance",
    "grom": "contemporary-art-performance",
    "maja hodošček": "curatorial-exhibition",
    "hodošček": "curatorial-exhibition",
    "armen avanessian": "philosophy-speculative",
    "avanessian": "philosophy-speculative",
    "suhail malik": "philosophy-speculative",
    "malik": "philosophy-speclicative",
    "joana costa santos": "curatorial-exhibition",
    "alexandra baybutt": "contemporary-art-performance",
    "baybutt": "contemporary-art-performance",
}

# Known work titles → lineage
KNOWN_WORKS = {
    "the speculative time-complex": "philosophy-speculative",
    "speculative time": "philosophy-speculative",
    "bienale": "curatorial-exhibition",
    "biennale": "curatorial-exhibition",
    "kasimir": "photography-choreography",
}


def detect_domains_in_text(text: str) -> list[str]:
    """Detect which domains a segment's content suggests."""
    text_lower = text.lower()
    hits = []
    for domain, keywords in DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                hits.append(domain)
                break
    return hits


def detect_authors_in_text(text: str) -> list[tuple[str, str]]:
    """Detect known authors in text. Returns [(author_name, lineage), ...]."""
    text_lower = text.lower()
    hits = []
    for author, lineage in KNOWN_AUTHORS.items():
        if author in text_lower:
            hits.append((author, lineage))
    return hits


def detect_works_in_text(text: str) -> list[tuple[str, str]]:
    """Detect known work titles. Returns [(title, lineage), ...]."""
    text_lower = text.lower()
    hits = []
    for work, lineage in KNOWN_WORKS.items():
        if work in text_lower:
            hits.append((work, lineage))
    return hits


def analyze_tmx_content(tm_path: Path) -> dict:
    """Read a TMX file, analyze EN content, return detected metadata."""
    from translate.storage.tmx import tmxfile

    raw = tm_path.read_text(encoding="utf-8")
    m = re.search(r'srclang\s*=\s*"([^"]+)"', raw, re.IGNORECASE)
    srclang = m.group(1).lower().split("-")[0] if m else "en"

    with open(tm_path, "rb") as f:
        tmx = tmxfile(f)

    segments = []
    for unit in tmx.unit_iter():
        src = clean_xml(unit.source)
        tgt = clean_xml(unit.target)
        if not src or not tgt:
            continue
        # Normalize: EN text always as source
        if srclang == "sl":
            en_text = tgt
        else:
            en_text = src
        segments.append(en_text)

    # Count domain hits across all segments
    domain_counts = Counter()
    author_counts = Counter()
    work_counts = Counter()

    for seg in segments:
        for domain in detect_domains_in_text(seg):
            domain_counts[domain] += 1
        for author, lineage in detect_authors_in_text(seg):
            author_counts[(author, lineage)] += 1
        for work, lineage in detect_works_in_text(seg):
            work_counts[(work, lineage)] += 1

    # Determine dominant lineage
    lineage_votes = Counter()

    # Authors vote for lineages
    for (author, lineage), count in author_counts.items():
        lineage_votes[lineage] += count

    # Works vote for lineages
    for (work, lineage), count in work_counts.items():
        lineage_votes[lineage] += count * 5  # Work titles are strong signals

    # Domains vote for lineages (top domain → lineage)
    if domain_counts:
        top_domain = domain_counts.most_common(1)[0][0]
        lineage_votes[top_domain] += domain_counts[top_domain]

    # Pick the dominant lineage
    if lineage_votes:
        detected_lineage = lineage_votes.most_common(1)[0][0]
    else:
        detected_lineage = tm_path.stem.lower().replace("_", "-")

    # All domains with significant presence
    significant_domains = [
        domain for domain, count in domain_counts.most_common()
        if count >= max(3, len(segments) * 0.01)  # At least 1% of segments
    ]

    return {
        "filename": tm_path.name,
        "segments": len(segments),
        "detected_lineage": detected_lineage,
        "detected_domains": significant_domains or ["humanities"],
        "domain_counts": dict(domain_counts.most_common(10)),
        "author_counts": {f"{a}→{l}": c for (a, l), c in author_counts.most_common(10)},
        "work_counts": {f"{w}→{l}": c for (w, l), c in work_counts.most_common(5)},
        "lineage_votes": dict(lineage_votes.most_common(10)),
    }


def main():
    dry_run = "--dry-run" in sys.argv

    # ── Step 1: Analyze each TMX file's content ──
    print("=== Step 1: Analyzing TMX content ===\n")

    tm_dir = Path("./data/tm")
    tm_files = sorted(tm_dir.glob("*.tmx"))
    tm_files = [f for f in tm_files if f.name != "working.tmx"]

    file_analysis = {}
    for tm_file in tm_files:
        info = analyze_tmx_content(tm_file)
        file_analysis[tm_file.name] = info
        print(f"  {info['filename']}:")
        print(f"    Segments: {info['segments']}")
        print(f"    Lineage:  {info['detected_lineage']}")
        print(f"    Domains:  {', '.join(info['detected_domains'])}")
        if info['author_counts']:
            top_authors = list(info['author_counts'].items())[:3]
            print(f"    Authors:  {top_authors}")
        if info['domain_counts']:
            top_domains = list(info['domain_counts'].items())[:5]
            print(f"    Domain hits: {top_domains}")
        print()

    # ── Step 2: Build lineage map (filename → detected lineage) ──
    print("=== Step 2: Lineage mapping ===\n")

    lineage_map = {}  # old_lineage → new_lineage
    domain_map = {}   # old_lineage → [domains]

    for filename, info in file_analysis.items():
        old_lineage = filename.replace('.tmx', '')  # Keep original case like '2022-SL-EN'
        new_lineage = info["detected_lineage"]
        lineage_map[old_lineage] = new_lineage
        domain_map[old_lineage] = info["detected_domains"]
        print(f"  {old_lineage} → {new_lineage} (domains: {info['detected_domains']})")

    # ── Step 3: Load KG and apply changes ──
    print(f"\n=== Step 3: Applying to KG ===\n")

    kg = KnowledgeGraph()
    print(f"Loaded KG: {kg.G.number_of_nodes()} nodes, {kg.G.number_of_edges()} edges")

    # ── 3a. Merge lineages ──
    total_merged = 0
    for nid, nd in kg.G.nodes(data=True):
        if nd.get("type") == "translation_mapping":
            old_lin = nd.get("lineage", "")
            if old_lin in lineage_map:
                if not dry_run:
                    nd["lineage"] = lineage_map[old_lin]
                total_merged += 1

    print(f"  Lineage updates: {total_merged}")

    # ── 3b. Update source node titles ──
    source_updated = 0
    for nid, nd in kg.G.nodes(data=True):
        if nd.get("type") == "source_text":
            old_title = nd.get("title", "")
            if old_title in lineage_map:
                new_title = lineage_map[old_title]
                if not dry_run:
                    nd["title"] = new_title
                source_updated += 1
                print(f"  Source: '{old_title}' → '{new_title}'")
    print(f"  Source nodes updated: {source_updated}")

    # ── 3c. Assign domains to concepts ──
    import re as regex_mod
    domain_assigned = 0
    for nid, nd in kg.G.nodes(data=True):
        if nd.get("type") != "concept":
            continue
        label = nd.get("label", "").lower()
        if not label:
            continue
        current_domain = nd.get("domain", "")
        for domain, keywords in DOMAIN_KEYWORDS.items():
            if any(kw in label for kw in keywords):
                if current_domain != domain:
                    if not dry_run:
                        nd["domain"] = domain
                    domain_assigned += 1
                break
    print(f"  Concept domains assigned: {domain_assigned}")

    # ── 4. Save ──
    if dry_run:
        print("\n=== DRY RUN — no changes saved ===")
    else:
        print("\nSaving...")
        kg.save()
        print("Done.")

    # ── 5. Summary ──
    from collections import Counter
    lineages = Counter()
    domains = Counter()
    for nid, nd in kg.G.nodes(data=True):
        if nd.get("type") == "translation_mapping":
            lineages[nd.get("lineage", "general")] += 1
        if nd.get("type") == "concept":
            domains[nd.get("domain", "(none)")] += 1

    print("\n=== Final Lineage Distribution ===")
    for lin, count in lineages.most_common():
        print(f"  {lin}: {count}")

    print("\n=== Final Domain Distribution ===")
    for domain, count in domains.most_common():
        print(f"  {domain}: {count}")


if __name__ == "__main__":
    main()