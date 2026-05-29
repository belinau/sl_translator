"""Ingest extracted entity records into the KnowledgeGraph.

Takes the flat list of records produced by the entity_extraction package,
scores each by confidence, and routes them by tier:

- ConfidenceTier.DIRECT_WRITE → KG nodes/edges via knowledge_graph factories
- ConfidenceTier.REVIEW      → data/extraction_review.json
- ConfidenceTier.DROP        → data/extraction_dropped.jsonl

Records are pre-aggregated by the orchestrator so that agent_person records
carry accurate multi_mention / multi_origin signals before scoring.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .entity_extraction.confidence import ConfidenceTier, score_record
from .knowledge_graph import KnowledgeGraph


def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:80] if s else "unknown"


@dataclass
class IngestStats:
    direct_write: int = 0
    review_queued: int = 0
    dropped: int = 0
    by_kind: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(
        lambda: {"direct_write": 0, "review": 0, "dropped": 0}
    ))

    def bump(self, kind: str, tier: ConfidenceTier):
        if tier == ConfidenceTier.DIRECT_WRITE:
            self.direct_write += 1
            self.by_kind[kind]["direct_write"] += 1
        elif tier == ConfidenceTier.REVIEW:
            self.review_queued += 1
            self.by_kind[kind]["review"] += 1
        else:
            self.dropped += 1
            self.by_kind[kind]["dropped"] += 1


def aggregate_agent_signals(records: List[dict]) -> None:
    """Pre-pass: for agent_person records, populate multi_mention / multi_origin.

    Mutates records in place.
    """
    counts: Dict[str, int] = defaultdict(int)
    origins: Dict[str, set] = defaultdict(set)
    for r in records:
        if r["kind"] != "agent_person":
            continue
        key = r["payload"]["dedup_group"]
        counts[key] += 1
        origins[key].add(r["payload"]["origin"])
    for r in records:
        if r["kind"] != "agent_person":
            continue
        key = r["payload"]["dedup_group"]
        r["signals"]["multi_mention"] = counts[key] >= 2
        r["signals"]["multi_origin"] = len(origins[key]) >= 2


def aggregate_institution_signals(records: List[dict]) -> None:
    counts: Dict[str, int] = defaultdict(int)
    for r in records:
        if r["kind"] != "institution":
            continue
        key = _slugify(r["payload"]["name"])
        counts[key] += 1
    for r in records:
        if r["kind"] != "institution":
            continue
        key = _slugify(r["payload"]["name"])
        r["signals"]["multi_mention"] = counts[key] >= 2


def score_all(records: List[dict]) -> List[dict]:
    """Annotate each record with confidence + reason_codes + tier."""
    scored = []
    for r in records:
        kind_map = {
            "translated_work": "translated_work",
            "cited_work": "cited_work",
            "agent_person": "agent_person",
            "institution": "institution",
            "artwork": "artwork",
            "artist": "artist",
            "festival": "festival",
        }
        rk = kind_map.get(r["kind"], r["kind"])
        result = score_record(rk, r["signals"])
        r2 = dict(r)
        r2["confidence"] = result.confidence
        r2["reason_codes"] = result.reason_codes
        r2["tier"] = result.tier.value
        scored.append(r2)
    return scored


def dedup_records(records: List[dict]) -> List[dict]:
    """Collapse exact duplicates and merge agents within the same dedup_group.

    For agent_person: same dedup_group → pick the longest canonical name and
    accumulate role variants. (Different-spelling variants remain separate
    candidates until the curator merges them in kg_editor.)

    For translated_work / cited_work / institution: dedupe by their id field.
    """
    out: List[dict] = []

    # --- Agents: group by dedup_group, pick canonical name ---
    agents_by_group: Dict[str, List[dict]] = defaultdict(list)
    others: List[dict] = []
    for r in records:
        if r["kind"] == "agent_person":
            key = r["payload"]["dedup_group"] or r["payload"]["norm"]
            agents_by_group[key].append(r)
        else:
            others.append(r)

    for key, group in agents_by_group.items():
        # Canonical name: the longest non-abbreviated form
        canonical = max(
            group,
            key=lambda g: (
                sum(1 for c in g["payload"]["name"] if c.isalpha()),
                # prefer not-just-initial forms
                "." not in g["payload"]["name"],
            ),
        )
        roles = sorted({g["payload"]["role"] for g in group})
        origins = sorted({g["payload"]["origin"] for g in group})
        merged_signals = dict(canonical["signals"])
        merged_signals["multi_mention"] = len(group) >= 2
        merged_signals["multi_origin"] = len(origins) >= 2
        merged_signals["plausible_person_name"] = any(
            g["signals"].get("plausible_person_name") for g in group
        )
        merged_signals["ner_person_match"] = any(
            g["signals"].get("ner_person_match") for g in group
        )
        merged_signals["role_attribution_context"] = any(
            g["signals"].get("role_attribution_context") for g in group
        )
        out.append({
            "kind": "agent_person",
            "payload": {
                "name": canonical["payload"]["name"],
                "role": roles[0] if len(roles) == 1 else "multi",
                "all_roles": roles,
                "dedup_group": key,
                "norm": canonical["payload"]["norm"],
                "origins": origins,
                "mention_count": len(group),
                "alt_spellings": sorted({g["payload"]["name"] for g in group}),
            },
            "signals": merged_signals,
            "source": canonical["source"],
        })

    # --- Other kinds: dedupe by id ---
    seen_ids: Dict[str, dict] = {}
    for r in others:
        kind = r["kind"]
        if kind == "translated_work":
            rid = f"work:{r['payload']['work_id']}"
        elif kind == "cited_work":
            rid = f"cited:{r['payload']['cited_id']}"
        elif kind == "institution":
            rid = f"inst:{_slugify(r['payload']['name'])}"
        else:
            rid = f"misc:{r['kind']}:{hash(json.dumps(r['payload'], sort_keys=True, default=str))}"

        if rid in seen_ids:
            # Accumulate multi-mention signal
            existing = seen_ids[rid]
            existing["signals"]["multi_mention"] = True
        else:
            seen_ids[rid] = r
            out.append(r)
    return out


def write_to_kg(
    kg: KnowledgeGraph,
    records: List[dict],
    *,
    review_path: Path,
    dropped_path: Path,
    dry_run: bool = False,
) -> IngestStats:
    """Apply records to the KG according to their tier.

    Returns IngestStats summarising what happened. Saves the KG once at the
    end (only if not dry_run AND at least one direct-write occurred).
    """
    stats = IngestStats()
    review: List[dict] = []
    dropped_lines: List[str] = []

    # Index agents and works ingested directly so cited_work edges can wire up
    agent_id_by_dedup: Dict[str, str] = {}
    work_id_by_payload: Dict[str, str] = {}

    # Pass 1: write translated_work + author + translator + institutions
    # (containers must exist before cited_work edges can wire them up)
    deferred_cited: List[dict] = []

    for r in records:
        kind = r["kind"]
        tier = ConfidenceTier(r["tier"])

        if tier == ConfidenceTier.DROP:
            dropped_lines.append(json.dumps(r, ensure_ascii=False, default=str))
            stats.bump(kind, tier)
            continue

        if tier == ConfidenceTier.REVIEW:
            review.append(r)
            stats.bump(kind, tier)
            continue

        # DIRECT_WRITE
        if dry_run:
            stats.bump(kind, tier)
            continue

        if kind == "translated_work":
            p = r["payload"]
            wid = p["work_id"]
            year_int = _to_int_year(p.get("year"))
            extra = {
                "title_en": p.get("title_en"),
                "title_sl": p.get("title_sl"),
                "title_orig": p.get("title_orig"),
                "title_translation": p.get("title_translation"),
                "orig_lang": p.get("orig_lang"),
                "translation_lang": p.get("translation_lang"),
                "project_type": p.get("project_type", "book_translation"),
                "anchor_origin": p.get("origin"),
                "anchor_idx": p.get("anchor_idx"),
                "segment_window": p.get("segment_window"),
                "matched_pattern": p.get("matched_pattern"),
            }
            extra = {k: v for k, v in extra.items() if v is not None}
            kg.add_source_text_node(
                wid,
                title=p.get("title_orig") or p.get("title_translation") or wid,
                year=year_int,
                **extra,
            )
            work_id_by_payload[wid] = f"source:{wid.lower()}"
            r["_pending_author"] = p.get("author")
            r["_pending_translator"] = p.get("translator")
            r["_pending_publisher"] = p.get("publisher")
            r["_pending_publisher_city"] = p.get("publisher_city")
            stats.bump(kind, tier)
            continue

        if kind == "agent_person":
            p = r["payload"]
            agent_id = _slugify(p["name"])
            kg.add_agent_node(
                agent_id,
                name=p["name"],
                role=p["role"] if p["role"] != "multi" else "author",
                dedup_group=p["dedup_group"],
                alt_spellings=p.get("alt_spellings", []),
                all_roles=p.get("all_roles", [p["role"]]),
                mention_count=p.get("mention_count", 1),
            )
            agent_id_by_dedup[p["dedup_group"]] = agent_id
            stats.bump(kind, tier)
            continue

        if kind == "institution":
            p = r["payload"]
            inst_id = _slugify(p["name"])
            kg.add_institution_node(
                inst_id,
                name=p["name"],
                kind=p.get("kind", "publisher"),
                city=p.get("city"),
            )
            stats.bump(kind, tier)
            continue

        if kind == "cited_work":
            deferred_cited.append(r)
            stats.bump(kind, tier)
            continue

        # Unknown kind — treat as review for safety
        review.append(r)

    # Pass 2: wire up translated_work author / translator edges (need agents created)
    if not dry_run:
        from .entity_extraction.name_dedup import dedup_group_key
        for r in records:
            if r["kind"] != "translated_work":
                continue
            if "_pending_author" not in r:
                continue
            wid = r["payload"]["work_id"]
            author_name = r.get("_pending_author")
            translator_name = r.get("_pending_translator")
            if author_name:
                grp = dedup_group_key(author_name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(author_name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(agent_id, name=author_name, role="author")
                src_node = f"source:{wid.lower()}"
                agent_node = f"agent:{agent_id.lower()}"
                if kg.G.has_node(src_node) and kg.G.has_node(agent_node):
                    if not kg.G.has_edge(src_node, agent_node):
                        kg.G.add_edge(src_node, agent_node, relation="written_by")
            if translator_name:
                grp = dedup_group_key(translator_name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(translator_name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(agent_id, name=translator_name, role="translator")
                kg.link_translated_by(wid, agent_id)
            # Publisher → institution for the translated_work itself
            pub_name = r.get("_pending_publisher")
            if pub_name:
                iid = _slugify(pub_name)
                if not kg.G.has_node(f"institution:{iid.lower()}"):
                    kg.add_institution_node(
                        iid, name=pub_name, kind="publisher",
                        city=r.get("_pending_publisher_city"),
                    )
                kg.link_published_by(wid, iid)

    # Pass 3: cited_work nodes + cited_in + written_by + published_by
    if not dry_run:
        from .entity_extraction.name_dedup import dedup_group_key
        for r in deferred_cited:
            p = r["payload"]
            cid = p["cited_id"]
            kg.add_source_text_node(
                cid,
                title=p.get("title_en") or p.get("title_sl") or p.get("title_orig") or cid,
                year=_to_int_year(p.get("year")),
                title_en=p.get("title_en"),
                title_sl=p.get("title_sl"),
                title_orig=p.get("title_orig"),
                project_type="cited_work",
                original_language=_infer_language(p.get("title_orig")),
                slovenian_edition=p.get("slovenian_edition"),
            )
            # Author edge
            author_name = p.get("author")
            if author_name:
                grp = dedup_group_key(author_name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(author_name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(agent_id, name=author_name, role="author")
                src_node = f"source:{cid.lower()}"
                agent_node = f"agent:{agent_id.lower()}"
                if kg.G.has_node(src_node) and kg.G.has_node(agent_node):
                    if not kg.G.has_edge(src_node, agent_node):
                        kg.G.add_edge(src_node, agent_node, relation="written_by")

            # cited_in edge → containing work
            container_id = p.get("container_work_id")
            if container_id:
                kg.link_cited_in(cid, container_id)

            # published_by edge → publisher institution
            pub = p.get("original_pub") or {}
            if pub.get("publisher"):
                inst_id = _slugify(pub["publisher"])
                if not kg.G.has_node(f"institution:{inst_id.lower()}"):
                    kg.add_institution_node(
                        inst_id, name=pub["publisher"], kind="publisher",
                        city=pub.get("city"),
                    )
                kg.link_published_by(cid, inst_id)

            sl_pub = p.get("slovenian_edition") or {}
            if sl_pub.get("publisher"):
                inst_id = _slugify(sl_pub["publisher"])
                if not kg.G.has_node(f"institution:{inst_id.lower()}"):
                    kg.add_institution_node(
                        inst_id, name=sl_pub["publisher"], kind="publisher",
                        city=sl_pub.get("city"),
                    )

    # Write review and dropped sinks
    review_path.parent.mkdir(parents=True, exist_ok=True)
    if review:
        review_path.write_text(
            json.dumps(review, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if dropped_lines:
        with open(dropped_path, "w", encoding="utf-8") as f:
            f.write("\n".join(dropped_lines))

    if not dry_run and stats.direct_write > 0:
        kg.save()

    return stats


def _to_int_year(year) -> Optional[int]:
    if year is None or year == "":
        return None
    try:
        if isinstance(year, int):
            return year
        s = str(year).split("-")[0].split("–")[0]
        return int(s)
    except (ValueError, TypeError):
        return None


_LANG_HINTS = [
    (re.compile(r"\b(?:der|die|das|und|im|von|zur?)\b", re.IGNORECASE), "de"),
    (re.compile(r"\b(?:le|la|les|de|du|et|dans)\b", re.IGNORECASE), "fr"),
    (re.compile(r"\b(?:il|la|lo|gli|di|del|nel)\b", re.IGNORECASE), "it"),
    (re.compile(r"\b(?:el|la|los|las|de|en|por)\b", re.IGNORECASE), "es"),
    (re.compile(r"\b(?:i|na|u|sa|kao|ali)\b"), "sr/hr"),
]


def _infer_language(title: Optional[str]) -> Optional[str]:
    if not title or len(title) < 4:
        return None
    for rx, code in _LANG_HINTS:
        # Need at least 2 hits to be confident
        if len(rx.findall(title)) >= 2:
            return code
    return None
