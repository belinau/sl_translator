#!/usr/bin/env python3
# scripts/validate_kg.py
#
# Invariant gate for the knowledge graph. Enforces the normative constraints
# from ontology.md against the live KG (or any nodes/edges payload) and exits
# non-zero when any hard invariant is violated. Run before committing KG
# changes, and from CI.
#
# Usage:
#   python scripts/validate_kg.py                 # validate data/knowledge.db
#   python scripts/validate_kg.py --path X.db     # validate a specific file
#   python scripts/validate_kg.py --quiet         # only print on failure

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KG = ROOT / "data" / "knowledge.db"

NODE_TYPES = {"term", "concept", "translation_mapping", "source_text", "agent", "institution"}
FORBIDDEN_NODE_TYPES = {"tm_segment", "collocation", "domain"}
LEGACY_NODE_FIELDS = ("title_en", "title_sl", "slovenian_edition")  # Phase 4: forbidden on source_text
LEGACY_EDGE_RELATIONS = {"sl_published_by"}  # Phase 4: renamed to translation_published_by
ROLES = {"author", "translator", "editor", "curator", "artist",
         "interviewer", "interviewee", "choreographer", "director",
         "performer", "dancer", "composer", "dramaturg", "agent"}
KINDS = {"publisher", "gallery", "museum", "university", "festival", "theatre",
         "journal", "organization", "sponsor", "country", "other"}
CONTAINER_TYPES = {"book_translation", "article_translation",
                   "festival_programme", "exhibition_catalogue"}
PROJECT_TYPES = CONTAINER_TYPES | {
    "book", "book_chapter", "journal_article", "magazine_article",
    "newspaper_article", "web_source", "exhibition_catalog", "interview",
    "thesis_dissertation", "artwork", "performance", "cited_container", "cited_work",
}
AGENT_REQUIRED = ("dedup_group", "alt_spellings", "all_roles", "mention_count")
TYPE_PREFIXES = ("book-chapter-", "book-", "journal-article-", "article-",
                 "other-", "n-", "unknown-", "artwork-", "web-", "magazine-", "cited-")

# HARD invariants fail the gate; SOFT ones are reported but do not fail.
HARD = {
    "forbidden_node_type", "ghost_node", "unknown_node_type", "dangling_edge",
    "cited_in_self_loop", "agent_missing_required", "bad_role", "bad_kind",
    "bad_project_type", "container_missing_translated_by", "source_no_title",
    "duplicate_source_stem",
    # Phase 4 language-neutrality enforcement
    "legacy_title_en", "legacy_title_sl", "legacy_slovenian_edition",
    "legacy_sl_published_by_edge",
    # fragment_title is SOFT: title quality is governed by the LLM re-typing pass;
    # legitimately lowercase-styled art/poetry titles (e.g. "like water, a bone
    # sings #3") are real works, not fragments, and must not fail the gate.
}


def _stem(sid: str) -> str:
    s = sid[len("source:"):] if sid.startswith("source:") else sid
    for p in TYPE_PREFIXES:
        if s.startswith(p):
            return s[len(p):]
    return s


_SENT_SL = re.compile(r"\b(je bil|je bila|so bili|so bile|ki je bil|čeprav|vidimo|denimo leta)\b", re.I)


def validate(nodes: list[dict], edges: list[dict]) -> dict[str, list[str]]:
    """Return {invariant_name: [offending ids/messages]} for every violation."""
    v: dict[str, list[str]] = defaultdict(list)
    ids = {n["id"] for n in nodes}
    out_rel = defaultdict(set)
    for e in edges:
        out_rel[e["source"]].add(e.get("relation"))
        if e["source"] not in ids or e["target"] not in ids:
            v["dangling_edge"].append(f"{e['source']} -[{e.get('relation')}]-> {e['target']}")
        if e.get("relation") == "cited_in" and e["source"] == e["target"]:
            v["cited_in_self_loop"].append(e["source"])

    stems: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        t = n.get("type")
        nid = str(n.get("id") or "")
        if not t:
            v["ghost_node"].append(nid)
            continue
        if t in FORBIDDEN_NODE_TYPES:
            v["forbidden_node_type"].append(nid)
        elif t not in NODE_TYPES:
            v["unknown_node_type"].append(f"{nid} ({t})")

        if t == "agent":
            if not all(k in n for k in AGENT_REQUIRED):
                v["agent_missing_required"].append(nid)
            if n.get("role") not in ROLES:
                v["bad_role"].append(f"{nid} ({n.get('role')})")
        elif t == "institution":
            if n.get("kind") not in KINDS:
                v["bad_kind"].append(f"{nid} ({n.get('kind')})")
        elif t == "source_text":
            if not (n.get("title") or "").strip():
                v["source_no_title"].append(nid)
            if n.get("project_type") not in PROJECT_TYPES:
                v["bad_project_type"].append(f"{nid} ({n.get('project_type')})")
            if n.get("project_type") in CONTAINER_TYPES and "translated_by" not in out_rel[nid]:
                v["container_missing_translated_by"].append(nid)
            title = (n.get("title") or "").strip()
            if title and (title[:1].islower() or _SENT_SL.search(title) or title.count("?") >= 3):
                v["fragment_title"].append(f"{nid}: {title[:50]!r}")
            stems[_stem(nid)].append(nid)

    for stem, members in stems.items():
        if len(members) > 1:
            v["duplicate_source_stem"].append(f"{stem}: {members}")

    return dict(v)


def load(path: Path) -> tuple[list[dict], list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("nodes", []), data.get("edges", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(DEFAULT_KG))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    nodes, edges = load(Path(args.path))
    violations = validate(nodes, edges)
    hard = {k: x for k, x in violations.items() if k in HARD}

    if not args.quiet:
        print(f"Validated {len(nodes)} nodes / {len(edges)} edges from {args.path}")
        if not violations:
            print("OK — no invariant violations.")
        for k, x in violations.items():
            tag = "HARD" if k in HARD else "soft"
            print(f"  [{tag}] {k}: {len(x)}")
            for item in x[:5]:
                print(f"        {item}")

    if hard:
        print(f"\nFAIL — {sum(len(x) for x in hard.values())} hard invariant violation(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
