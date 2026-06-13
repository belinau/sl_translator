#!/usr/bin/env python3
"""Harvest concepts for KG authors from Wikidata REST API.

Uses wbsearchentities + wbgetentities (NOT SPARQL — avoids outage throttling).
3-second delay between requests. ~12 min for 246 authors.

Properties: P135 (movement), P101 (field), P737 (influenced by).
Label resolution batched in groups of 50.

Usage:
  python scripts/harvest_wikidata_concepts.py
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DATA_DIR = pathlib.Path("data")
OUTPUT = DATA_DIR / "wikidata_author_concepts.json"
HEADERS = {"User-Agent": "sl_translator/1.0 (academic research; urban.belina@gmail.com)"}
DELAY = 3  # seconds between requests


def api_get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def search_entity(name: str) -> str | None:
    safe = urllib.parse.quote(name)
    url = (f"https://www.wikidata.org/w/api.php?action=wbsearchentities"
           f"&search={safe}&language=en&format=json&limit=1&type=item")
    data = api_get(url)
    results = data.get("search", [])
    return results[0]["id"] if results else None


def get_claims(qid: str) -> dict:
    url = (f"https://www.wikidata.org/w/api.php?action=wbgetentities"
           f"&ids={qid}&format=json&props=claims")
    data = api_get(url)
    return data.get("entities", {}).get(qid, {}).get("claims", {})


def resolve_labels(qids: list[str]) -> dict[str, str]:
    if not qids:
        return {}
    results = {}
    for i in range(0, len(qids), 50):
        batch = "|".join(qids[i:i + 50])
        url = (f"https://www.wikidata.org/w/api.php?action=wbgetentities"
               f"&ids={batch}&format=json&props=labels&languages=en|sl")
        data = api_get(url)
        for qid, ent in data.get("entities", {}).items():
            label = (ent.get("labels", {}).get("en", {}).get("value", "")
                     or ent.get("labels", {}).get("sl", {}).get("value", ""))
            if label:
                results[qid] = label
        time.sleep(DELAY)
    return results


def extract_qids(claims: dict, prop: str) -> list[str]:
    qids = []
    for v in claims.get(prop, [])[:20]:
        ms = v.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(ms, dict) and "id" in ms:
            qids.append(ms["id"])
    return qids


# Load authors
authors = json.loads((DATA_DIR / "authors_needing_concepts.json").read_text("utf-8"))["authors"]

# Skip last-name-only dupes
SKIP = {"Unsigned", "Ali", "Foucault", "Bataille", "Benjamin", "Rolnik",
        "Vasarely", "Šuvaković", "Bajič", "Foglar", "Putrih", "Fiškin",
        "Piny", "Pregl", "Žižek", "Chekhov", "Fisher", "Lugones",
        "Boccioni", "Pavlov", "Bihalji-Merin", "seid'ōu", "Zico",
        "Editorial board", "kąrî'ka· chä seid'ōu"}
clean = [a for a in authors if a["name"].strip() not in SKIP and len(a["name"].strip()) >= 3]
log.info(f"Authors to look up: {len(clean)}")

# Resume from existing
existing: dict[str, dict] = {}
if OUTPUT.exists():
    for entry in json.loads(OUTPUT.read_text("utf-8")):
        existing[entry.get("id", "")] = entry
log.info(f"Already done: {len(existing)}")

results: list[dict] = list(existing.values())

for i, author in enumerate(clean):
    if author["id"] in existing:
        continue

    name = author["name"]
    entry = {"id": author["id"], "name": name, "citations": author["citations"]}

    try:
        # Search
        qid = search_entity(name)
        time.sleep(DELAY)

        if not qid:
            # Try Slovenian search
            safe = urllib.parse.quote(name)
            url = (f"https://www.wikidata.org/w/api.php?action=wbsearchentities"
                   f"&search={safe}&language=sl&format=json&limit=1&type=item")
            sl_data = api_get(url)
            sl_results = sl_data.get("search", [])
            qid = sl_results[0]["id"] if sl_results else None
            time.sleep(DELAY)

        if not qid:
            entry.update({"qid": "", "movements": [], "fields": [], "influenced_by": []})
            results.append(entry)
            log.info(f"  [{i+1}/{len(clean)}] {name}: not found")
            continue

        # Get claims
        claims = get_claims(qid)
        time.sleep(DELAY)

        # Extract QIDs
        movement_qids = extract_qids(claims, "P135")
        field_qids = extract_qids(claims, "P101")
        influence_qids = extract_qids(claims, "P737")

        # Batch resolve all labels
        all_qids = list(set(movement_qids + field_qids + influence_qids))
        labels = resolve_labels(all_qids) if all_qids else {}

        entry.update({
            "qid": qid,
            "movements": [labels[q] for q in movement_qids if q in labels],
            "fields": [labels[q] for q in field_qids if q in labels],
            "influenced_by": [labels[q] for q in influence_qids if q in labels],
        })

        if entry["movements"] or entry["fields"]:
            log.info(f"  [{i+1}/{len(clean)}] {name}: {entry['movements'][:3]} / {entry['fields'][:3]}")
        else:
            log.info(f"  [{i+1}/{len(clean)}] {name}: found Q{qid} but no concepts")

    except Exception as e:
        entry.update({"qid": "", "movements": [], "fields": [], "influenced_by": []})
        log.warning(f"  [{i+1}/{len(clean)}] {name}: {e}")

    results.append(entry)

    # Save every 10 authors
    if len(results) % 10 == 0:
        OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), "utf-8")

# Final save
OUTPUT.write_text(json.dumps(results, indent=2, ensure_ascii=False), "utf-8")
with_data = sum(1 for r in results if r.get("movements") or r.get("fields"))
log.info(f"\nDone: {len(results)} entries, {with_data} with concept data. Saved to {OUTPUT}")
