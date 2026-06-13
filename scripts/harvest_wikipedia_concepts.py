#!/usr/bin/env python3
"""Harvest concepts from Wikipedia infoboxes for KG cited authors.

Uses MediaWiki API batch fetch (50 pages/request) to extract:
- notable_ideas / main_interests / known_for from infoboxes
- school_tradition / school / movement from infoboxes

Human-curated Wikipedia data. No LLM. ~35 seconds for 1,730 authors.

Usage:
  python scripts/harvest_wikipedia_concepts.py [--dry-run]
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv
DATA_DIR = pathlib.Path("data")
OUTPUT = DATA_DIR / "wikipedia_author_concepts.json"
HEADERS = {"User-Agent": "sl_translator/1.0 (academic research; urban.belina@gmail.com)"}
BATCH_SIZE = 50
DELAY = 1.5  # seconds between requests

IDEA_FIELDS = ["notable_ideas", "main_interests", "known_for"]
SCHOOL_FIELDS = ["school_tradition", "school", "movement"]


def wiki_batch_fetch(titles: list[str]) -> dict[str, str]:
    """Fetch wikitext section 0 for up to 50 pages. Returns {title: wikitext}."""
    encoded = "|".join(titles)
    url = (
        f"https://en.wikipedia.org/w/api.php?action=query"
        f"&titles={urllib.parse.quote(encoded, safe='|')}"
        f"&prop=revisions&rvprop=content&rvslots=main&rvsection=0&format=json"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    result = {}
    for pid, page in data.get("query", {}).get("pages", {}).items():
        title = page.get("title", "")
        rev = page.get("revisions", [{}])[0]
        wt = rev.get("slots", {}).get("main", {}).get("*", "") or rev.get("*", "")
        if title and wt:
            result[title.lower()] = wt
    return result


def extract_infobox(wikitext: str) -> dict:
    """Extract notable_ideas and school from infobox wikitext."""
    ideas = []
    for field in IDEA_FIELDS:
        match = re.search(rf"\|\s*{field}\s*=\s*(.+?)(?:\n\||\n\}}\}})", wikitext, re.DOTALL)
        if match:
            raw = match.group(1).strip()
            found = re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", raw)
            if found:
                ideas = [f.strip() for f in found if f.strip()]
                break
            # Try plaintext (some infoboxes don't use wikilinks)
            if raw and not raw.startswith("{"):
                # Split on commas or <br>
                parts = re.split(r",|<br\s*/?>|\n", raw)
                ideas = [p.strip() for p in parts if p.strip() and len(p.strip()) > 2]
                break

    schools = []
    for field in SCHOOL_FIELDS:
        match = re.search(rf"\|\s*{field}\s*=\s*(.+?)(?:\n\||\n\}}\}})", wikitext, re.DOTALL)
        if match:
            raw = match.group(1).strip()
            found = re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", raw)
            if found:
                schools = [f.strip() for f in found if f.strip()]
                break

    return {"ideas": ideas, "schools": schools}


# ---------------------------------------------------------------------------
# Load KG
# ---------------------------------------------------------------------------
kg_data = json.loads((DATA_DIR / "knowledge.db").read_text("utf-8"))
nodes = {n["id"]: n for n in kg_data["nodes"]}
edges = kg_data["edges"]

# Find all cited authors
cited_in = defaultdict(set)
work_authors = defaultdict(set)
for e in edges:
    if e["relation"] == "cited_in":
        cited_in[e["target"]].add(e["source"])
    elif e["relation"] == "written_by":
        work_authors[e["source"]].add(e["target"])

cited_authors = set()
for container, cited_works in cited_in.items():
    for cw in cited_works:
        cited_authors.update(work_authors.get(cw, set()))

# Already have concepts?
concept_agents = set()
for e in edges:
    if e["relation"] == "attributed_to" and e["source"].startswith("concept:"):
        concept_agents.add(e["target"])

need_concepts = cited_authors - concept_agents
log.info(f"Cited authors: {len(cited_authors)}")
log.info(f"Already have concepts: {len(cited_authors & concept_agents)}")
log.info(f"Need concepts: {len(need_concepts)}")

# Build name → agent_id mapping
author_names: dict[str, str] = {}
for aid in need_concepts:
    name = nodes.get(aid, {}).get("name", "")
    if name and len(name) >= 3:
        author_names[name] = aid

# Convert names to Wikipedia title format
def name_to_wiki_title(name: str) -> str:
    return name.replace(" ", "_")

# Also try common disambiguations
DISAMBIG = {
    "Stuart Hall": "Stuart_Hall_(cultural_theorist)",
    "Paul Gilroy": "Paul_Gilroy",
}

wiki_titles: dict[str, str] = {}  # wiki_title → agent_id
for name, aid in author_names.items():
    if name in DISAMBIG:
        wiki_titles[DISAMBIG[name]] = aid
    else:
        wiki_titles[name_to_wiki_title(name)] = aid

log.info(f"Wikipedia titles to look up: {len(wiki_titles)}")

if DRY_RUN:
    log.info("--dry-run: showing first 20")
    for t in sorted(wiki_titles)[:20]:
        log.info(f"  {t}")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Batch fetch from Wikipedia
# ---------------------------------------------------------------------------
results: dict[str, dict] = {}
titles_list = list(wiki_titles.keys())
found = 0
not_found = 0

for i in range(0, len(titles_list), BATCH_SIZE):
    batch = titles_list[i:i + BATCH_SIZE]
    try:
        pages = wiki_batch_fetch(batch)
        for title in batch:
            title_lower = title.replace("_", " ").lower()
            wikitext = pages.get(title_lower, "")
            aid = wiki_titles[title]
            name = nodes.get(aid, {}).get("name", title)

            if wikitext:
                info = extract_infobox(wikitext)
                if info["ideas"] or info["schools"]:
                    results[aid] = {
                        "name": name,
                        "notable_ideas": info["ideas"],
                        "schools": info["schools"],
                        "source": "wikipedia",
                    }
                    found += 1
                else:
                    not_found += 1
            else:
                not_found += 1

    except Exception as e:
        log.warning(f"  Batch {i // BATCH_SIZE + 1} failed: {e}")

    if (i // BATCH_SIZE + 1) % 5 == 0:
        log.info(f"  Progress: {i + len(batch)}/{len(titles_list)} "
                 f"(found={found}, missing={not_found})")
    time.sleep(DELAY)

# Save results
OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), "utf-8")
log.info(f"\nDone: {found} authors with concepts, {not_found} without.")
log.info(f"Saved to {OUTPUT}")
