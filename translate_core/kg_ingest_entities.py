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

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .entity_extraction._slug import _slugify
from .entity_extraction.confidence import ConfidenceTier, score_record
from .knowledge_graph import KnowledgeGraph


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically: write to a temp file, then rename into place."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


MAX_MENTION_SEGMENTS = 20

# §2.5 agent role allowlist (14 values). Off-set roles are coerced to "agent".
ROLE_ALLOWLIST = {
    "author", "translator", "editor", "curator", "artist",
    "interviewer", "interviewee", "choreographer", "director",
    "performer", "dancer", "composer", "dramaturg", "agent",
}

# §2.6 institution kind allowlist (11 values). Off-set kinds coerced to "other".
KIND_ALLOWLIST = {
    "publisher", "gallery", "museum", "university", "festival",
    "theatre", "journal", "organization", "sponsor", "country", "other",
}

# §2.4.1 container project_type allowlist (translated_work).
CONTAINER_TYPES = {
    "book_translation", "article_translation",
    "festival_programme", "exhibition_catalogue",
}

# §2.4.1 cited project_type allowlist.
CITED_TYPES = {
    "book", "book_chapter", "journal_article", "magazine_article",
    "newspaper_article", "web_source", "exhibition_catalog",
    "interview", "thesis_dissertation", "short_reference",
    "other", "cited_work",
}

# §4 invariant 6 citation_style allowlist.
STYLE_ALLOWLIST = {"chicago_en", "chicago_sl", "mla", "sist_iso690"}

# §5 provenance allowlist — single source of truth.
CONTAINER_PROVENANCE: frozenset[str] = frozenset({"cobiss_personal", "curator_extra"})
VALID_PROVENANCE: frozenset[str] = CONTAINER_PROVENANCE | frozenset({"tm_smol", "doc_pair"})


def _route_record(
    record: dict,
    *,
    container_index: set[str] | None = None,
) -> tuple[str, dict]:
    """Phase 5 routing dispatcher (see phase5_blueprint.md §1).

    Returns ``("direct", record)`` if the record should fall through to the
    per-kind handler, or ``("review", {"reason": "<code>"})`` if the record
    should be routed to the curator review queue.

    ``container_index`` is the set of slugified container work IDs already
    written in pass 1. Pass 1 passes ``None`` (skip container resolution);
    pass 3 (deferred_cited loop) passes ``set(work_id_by_payload.keys())``.
    """
    kind = record.get("kind")
    source = record.get("source") or {}
    provenance = source.get("provenance")
    payload = record.get("payload") or {}

    if kind == "translated_work":
        if provenance in CONTAINER_PROVENANCE:
            return ("direct", record)
        if provenance in {"tm_smol", "doc_pair"}:
            return ("review", {"reason": "provenance_mismatch_for_translated_work"})
        return ("review", {"reason": "provenance_missing_or_unknown"})

    if kind == "cited_work":
        if provenance is None or provenance not in VALID_PROVENANCE:
            return ("review", {"reason": "provenance_missing_or_unknown"})
        if payload.get("orig_lang") is None and payload.get("translation_lang") is None:
            return ("review", {"reason": "language_pair_undetermined"})
        if container_index is not None:
            container_work_id = payload.get("container_work_id")
            # Case-insensitive lookup: KG slugs are lowercased on creation
            # (see add_source_text_node), but payloads may carry mixed-case
            # boundary placeholders.
            if container_work_id and container_work_id.lower() not in container_index:
                return ("review", {"reason": "container_not_found"})
        return ("direct", record)

    # agent_person, institution, concept, artwork, performance
    if provenance in VALID_PROVENANCE:
        return ("direct", record)
    return ("review", {"reason": "provenance_missing_or_unknown"})


def _segment_pointer(record: dict) -> Optional[dict]:
    """Extract (origin, segment_idx) pointer from a record's source dict, or None."""
    src = record.get("source") or {}
    origin = src.get("origin")
    seg = src.get("segment_idx")
    if origin is None or seg is None:
        return None
    try:
        return {"origin": str(origin), "segment_idx": int(seg)}
    except (ValueError, TypeError):
        return None


def _collect_mention_segments(records: List[dict]) -> List[dict]:
    """Deduped list of {origin, segment_idx} pointers from a group of records."""
    seen: set[tuple] = set()
    out: List[dict] = []
    for r in records:
        ptr = _segment_pointer(r)
        if not ptr:
            continue
        key = (ptr["origin"], ptr["segment_idx"])
        if key in seen:
            continue
        seen.add(key)
        out.append(ptr)
        if len(out) >= MAX_MENTION_SEGMENTS:
            break
    return out



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
        origins[key].add(r.get("source", {}).get("origin") or r["payload"].get("origin", ""))
    for r in records:
        if r["kind"] != "agent_person":
            continue
        key = r["payload"]["dedup_group"]
        r["signals"]["multi_mention"] = counts[key] >= 2
        r["signals"]["multi_origin"] = len(origins[key]) >= 2


def aggregate_institution_signals(records: List[dict]) -> None:
    """multi_mention firing rule for institution records.

    Dedup by (slugified_name, kind) so distinct institutions sharing a
    surface name (e.g. 'Maska' the publisher vs 'Maska' the journal)
    stay in separate buckets per ontology §2.6.
    """
    counts: Dict[tuple, int] = defaultdict(int)
    for r in records:
        if r["kind"] != "institution":
            continue
        key = (_slugify(r["payload"]["name"]), r["payload"].get("kind", "other"))
        counts[key] += 1
    for r in records:
        if r["kind"] != "institution":
            continue
        key = (_slugify(r["payload"]["name"]), r["payload"].get("kind", "other"))
        r["signals"]["multi_mention"] = counts[key] >= 2


_TYPED_PROJECT_TYPES = {
    "book", "book_chapter", "journal_article", "magazine_article",
    "newspaper_article", "web_source", "exhibition_catalog", "interview",
    "thesis_dissertation", "short_reference", "other",
}


def _record_kind_for_scoring(record: dict) -> str:
    """Map a record to the scoring kind. Typed cited_work records score
    under their specific type (book / journal_article / ...) so the
    per-type signals in confidence.score_record fire."""
    kind = record["kind"]
    if kind == "cited_work":
        ptype = (record.get("payload") or {}).get("project_type", "")
        if ptype in _TYPED_PROJECT_TYPES:
            return ptype
        return "cited_work"
    return kind


def score_all(records: List[dict]) -> List[dict]:
    """Annotate each record with confidence + reason_codes + tier."""
    scored = []
    for r in records:
        rk = _record_kind_for_scoring(r)
        # Augment per-type signals from the payload so the scorer can see
        # `has_journal`, `has_book_title`, `has_venue` etc.
        signals = _augment_typed_signals(r, rk)
        result = score_record(rk, signals)
        r2 = dict(r)
        r2["confidence"] = result.confidence
        r2["reason_codes"] = result.reason_codes
        r2["tier"] = result.tier.value
        scored.append(r2)
    return scored


def _augment_typed_signals(record: dict, scoring_kind: str) -> dict:
    """Project type-specific payload fields into signal flags the scorer
    reads. Cheap: just reads `payload.extra_fields` and `payload.original_pub`
    to set `has_journal` / `has_venue` / etc."""
    base = dict(record.get("signals") or {})
    p = record.get("payload") or {}
    extras = p.get("extra_fields") or {}

    # Map payload presence → signal flags by scoring_kind
    if scoring_kind == "journal_article":
        if extras.get("journal"):
            base["has_journal"] = True
        if extras.get("volume"):
            base["has_volume"] = True
        if p.get("pages"):
            base["has_pages"] = True
    elif scoring_kind == "book_chapter":
        if extras.get("book_title"):
            base["has_book_title"] = True
        # The chapter authors are in p["all_authors"]; editors live as
        # companion records, but presence is reflected via the verifier.
        if p.get("all_authors") or p.get("author"):
            base["has_author"] = True
    elif scoring_kind in ("magazine_article", "newspaper_article"):
        if extras.get("magazine") or extras.get("newspaper"):
            base[f"has_{scoring_kind.split('_')[0]}"] = True
        if extras.get("date"):
            base["has_date"] = True
    elif scoring_kind == "web_source":
        if extras.get("url"):
            base["has_url"] = True
        if extras.get("site_name"):
            base["has_site_name"] = True
    elif scoring_kind == "exhibition_catalog":
        if extras.get("venue") or p.get("venue"):
            base["has_venue"] = True
    elif scoring_kind == "interview":
        if p.get("interviewee"):
            base["has_interviewee"] = True
        if p.get("interviewer"):
            base["has_interviewer"] = True
        if extras.get("date"):
            base["has_date"] = True
        if extras.get("publication_or_network"):
            base["has_publication_or_network"] = True
    elif scoring_kind == "thesis_dissertation":
        if extras.get("degree_type"):
            base["has_degree_type"] = True
        if extras.get("institution"):
            base["has_institution"] = True

    return base

def _record_identity(r: dict) -> str:
    """Stable cross-process identity for a record.

    Used to dedupe non-agent records in dedup_records and to merge the review
    queue file across successive editor confirms. Must be deterministic and
    process-independent (no builtin hash()).
    """
    kind = r["kind"]
    p = r["payload"]
    if kind == "agent_person":
        return f"agent:{p.get('dedup_group') or _slugify(p.get('name', ''))}"
    if kind == "translated_work":
        return f"work:{p['work_id']}"
    if kind == "cited_work":
        return f"cited:{p['cited_id']}"
    if kind == "artwork":
        return f"art:{p.get('work_id') or _slugify(p.get('title_orig') or p.get('title_translation') or '')}"
    if kind == "institution":
        return f"inst:{_slugify(p['name'])}::{p.get('kind', 'other')}"
    if kind == "concept":
        raw_cid = p.get("concept_id") or f"concept:{_slugify(p.get('label', ''))}"
        return raw_cid if raw_cid.startswith("concept:") else f"concept:{raw_cid}"
    if kind == "performance":
        return f"perf:{p.get('work_id') or _slugify(p.get('title_orig', ''))}"
    # Fallback: deterministic hash of the payload (sha1 — process-stable).
    return f"misc:{kind}:{hashlib.sha1(json.dumps(p, sort_keys=True, default=str).encode()).hexdigest()}"



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
            key = r["payload"]["dedup_group"] or r["payload"].get("norm", "") or _slugify(r["payload"]["name"])
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
        origins = sorted({g.get("source", {}).get("origin") or g["payload"].get("origin", "") for g in group})
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
        # Smol-quality flags: OR across the group so the merged record keeps
        # the +0.60 universal bump even when the canonical winner happens to
        # be a non-smol record (e.g. a seeded_book author construction).
        for flag in ("smol_verified_classification", "smol_extracted",
                     "verified_from_text", "bilingual_name"):
            merged_signals[flag] = any(g["signals"].get(flag) for g in group)
        mention_segments = _collect_mention_segments(group)
        out.append({
            "kind": "agent_person",
            "payload": {
                "name": canonical["payload"]["name"],
                "role": roles[0] if len(roles) == 1 else "agent",
                "all_roles": roles,
                "dedup_group": key,
                "norm": canonical["payload"].get("norm") or _slugify(canonical["payload"]["name"]),
                "origins": origins,
                "mention_count": len(group),
                "alt_spellings": sorted({g["payload"]["name"] for g in group}),
                "mention_segments": mention_segments,
            },
            "signals": merged_signals,
            "source": canonical["source"],
        })

    # --- Other kinds: dedupe by id ---
    seen_ids: Dict[str, dict] = {}
    id_groups: Dict[str, List[dict]] = defaultdict(list)
    for r in others:
        kind = r["kind"]
        rid = _record_identity(r)

        id_groups[rid].append(r)
        if rid in seen_ids:
            existing = seen_ids[rid]
            existing["signals"]["multi_mention"] = True
            _merge_bilingual_payload(existing["payload"], r["payload"])
            # Accumulate distinct containers for concept records so each
            # container gets its own cited_in edge during ingest.
            if r["kind"] == "concept":
                ep = existing["payload"]
                containers = ep.setdefault("cited_containers", [])
                if ep.get("container_work_id") and ep["container_work_id"] not in containers:
                    containers.append(ep["container_work_id"])
                src_container = r["payload"].get("container_work_id")
                if src_container and src_container not in containers:
                    containers.append(src_container)
            # Bilingual signal lights up once both halves are present.
            if r["signals"].get("verified_typed_pipeline"):
                existing["signals"]["verified_typed_pipeline"] = True
            if r["signals"].get("verified_from_text"):
                existing["signals"]["verified_from_text"] = True
            ep = existing["payload"]
            has_both = (
                bool(ep.get("title_orig") and ep.get("title_translation"))
                or bool(ep.get("label_orig") and ep.get("label_translation"))
            )
            if has_both:
                existing["signals"]["has_bilingual_title"] = True
                existing["signals"]["has_bilingual_label"] = True
        else:
            seen_ids[rid] = r
            out.append(r)

    # Attach mention_segments to the canonical record for each id group
    for rid, canonical in seen_ids.items():
        group = id_groups[rid]
        if len(group) >= 2 or _segment_pointer(canonical):
            canonical["payload"]["mention_segments"] = _collect_mention_segments(group)
    return out


# ── Bilingual payload merge (audit-template violation #9 / ontology §2.4.2) ──

_BILINGUAL_TEXT_FIELDS = (
    "title_orig", "title_translation",
    "label_orig", "label_translation", "label",
    "name_translation",
    "orig_lang", "translation_lang",
    "author", "artist",
    "medium", "year",
    "pages", "venue", "venue_city", "host_institution", "host_city",
    "originating_author", "source_work_title", "source_work_year",
    "container_work_id", "domain", "performance_kind",
)


def _merge_bilingual_payload(dst: dict, src: dict) -> None:
    """Fold `src` payload fields into `dst` without overwriting present values.

    For scalar fields: the first non-empty value wins. For dict fields
    (`original_pub`, `translation_edition`): merge sub-keys field-by-field. For
    list fields (`creators`, `performers`, `alt_spellings`): union by name.
    This makes two segments that mention the same work merge into one
    bilingual node (ontology §2.4.2 / audit-template #9).
    """
    for fld in _BILINGUAL_TEXT_FIELDS:
        if not dst.get(fld) and src.get(fld):
            dst[fld] = src[fld]

    for sub_fld in ("original_pub", "translation_edition"):
        src_sub = src.get(sub_fld) or {}
        if not isinstance(src_sub, dict) or not src_sub:
            continue
        dst_sub = dst.get(sub_fld) or {}
        if not isinstance(dst_sub, dict):
            continue
        for k, v in src_sub.items():
            if v and not dst_sub.get(k):
                dst_sub[k] = v
        if dst_sub:
            dst[sub_fld] = dst_sub

    for list_fld in ("creators", "performers"):
        src_list = src.get(list_fld) or []
        if not isinstance(src_list, list) or not src_list:
            continue
        dst_list = dst.get(list_fld) or []
        if not isinstance(dst_list, list):
            dst_list = []
        seen = {(d.get("name"), d.get("role")) for d in dst_list if isinstance(d, dict)}
        for entry in src_list:
            if not isinstance(entry, dict):
                continue
            key = (entry.get("name"), entry.get("role"))
            if key not in seen:
                dst_list.append(entry)
                seen.add(key)
        if dst_list:
            dst[list_fld] = dst_list


def _merge_review_queue(review_path: Path, new_records: List[dict]) -> List[dict]:
    """Merge new review-tier records into the existing queue file.

    Existing entries keep their position. When a new record shares the same
    identity as an existing entry the two are bilingual-merged in place
    (newer non-empty fields win for scalars; bilingual text fields are
    combined so neither confirm loses its half). Novel records append.
    A corrupt or non-list existing file is renamed to <name>.corrupt and
    treated as empty — the sidecar is the evidence trail.
    """
    merged: List[dict] = []
    if review_path.exists():
        try:
            existing = json.loads(review_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                raise ValueError("not a list")
            merged = existing
        except (json.JSONDecodeError, ValueError):
            corrupt = review_path.with_name(review_path.name + ".corrupt")
            review_path.replace(corrupt)

    # Build position index from the loaded existing entries.
    by_id: Dict[str, int] = {}
    for idx, rec in enumerate(merged):
        try:
            by_id[_record_identity(rec)] = idx
        except Exception:
            pass  # malformed existing entry — keep it, skip indexing

    for new_rec in new_records:
        try:
            identity = _record_identity(new_rec)
        except Exception:
            merged.append(new_rec)
            continue

        if identity in by_id:
            existing_rec = merged[by_id[identity]]
            # Merge bilingual payload fields so two confirms that each carry
            # one language's half combine into a complete bilingual record.
            _merge_bilingual_payload(existing_rec["payload"], new_rec["payload"])
            # Update confidence and signals from the newer extraction.
            if new_rec.get("confidence", 0) >= existing_rec.get("confidence", 0):
                existing_rec["confidence"] = new_rec["confidence"]
                existing_rec["tier"] = new_rec.get("tier", existing_rec.get("tier"))
            for sig_key, sig_val in new_rec.get("signals", {}).items():
                if sig_val:
                    existing_rec.setdefault("signals", {})[sig_key] = sig_val
        else:
            by_id[identity] = len(merged)
            merged.append(new_rec)

    return merged


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
    deferred_artwork: List[dict] = []
    deferred_performance: List[dict] = []
    deferred_concept: List[dict] = []

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

        # §5 provenance routing (Phase 5). Pass 1 skips container resolution
        # by passing `container_index=None`; pass 3 supplies the index.
        route, route_meta = _route_record(r, container_index=None)
        if route == "review":
            r["_route_reason"] = route_meta.get("reason")
            review.append(r)
            stats.bump(kind, ConfidenceTier.REVIEW)
            continue

        if kind == "translated_work":
            p = r["payload"]
            if not p.get("translator"):
                # O-20: container source_text MUST NOT be written without a translator.
                review.append(r)
                continue
            wid = _slugify(p["work_id"])
            year_int = _to_int_year(p.get("year"))
            extra = {
                "title_orig": p.get("title_orig"),
                "title_translation": p.get("title_translation"),
                "orig_lang": p.get("orig_lang"),
                "translation_lang": p.get("translation_lang"),
                "project_type": (
                    p["project_type"]
                    if p.get("project_type") in CONTAINER_TYPES
                    else "book_translation"
                ),
            }
            extra = {k: v for k, v in extra.items() if v is not None}
            # TM-internal anchors (anchor_origin/anchor_idx/segment_window/
            # matched_pattern/origin/segment_idx) live on the extraction
            # record, not on the persisted KG node. Keep the KG clean.
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
                role=p.get("role") if p.get("role") in ROLE_ALLOWLIST else "agent",
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
                kind=p.get("kind") if p.get("kind") in KIND_ALLOWLIST else "other",
                city=p.get("city"),
                name_translation=p.get("name_translation"),  # ontology §2.6 bilingual
            )
            stats.bump(kind, tier)
            continue

        if kind == "cited_work":
            deferred_cited.append(r)
            stats.bump(kind, tier)
            continue

        if kind == "artwork":
            deferred_artwork.append(r)
            stats.bump(kind, tier)
            continue

        if kind == "performance":
            deferred_performance.append(r)
            stats.bump(kind, tier)
            continue

        if kind == "concept":
            deferred_concept.append(r)
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
            wid = _slugify(r["payload"]["work_id"])
            author_name = r.get("_pending_author")
            translator_name = r.get("_pending_translator")
            if author_name:
                grp = dedup_group_key(author_name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(author_name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(
                        agent_id,
                        name=author_name,
                        role="author",
                        dedup_group=dedup_group_key(author_name),
                        alt_spellings=[author_name],
                        all_roles=["author"],
                        mention_count=1,
                    )
                kg.link_written_by(wid, agent_id)
            if translator_name:
                grp = dedup_group_key(translator_name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(translator_name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(
                        agent_id,
                        name=translator_name,
                        role="translator",
                        dedup_group=dedup_group_key(translator_name),
                        alt_spellings=[translator_name],
                        all_roles=["translator"],
                        mention_count=1,
                    )
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
        # Container index = current-batch payloads UNION existing KG source_text
        # nodes. The chokepoint's `container_not_found` check used to only see
        # the current pass, rejecting valid cited_works whose container was
        # ingested in a prior run. We now consult the live KG so previously-
        # ingested containers (COBISS, curator_extra, boundary placeholders)
        # are recognised.
        container_index = {k.lower() for k in work_id_by_payload.keys()}
        for nid, ndata in kg.G.nodes(data=True):
            if ndata.get("type") == "source_text":
                # Strip the `source:` prefix and lowercase so the index
                # matches the payload `container_work_id` (which is bare
                # and may be mixed-case for boundary placeholders).
                container_index.add(nid.removeprefix("source:").lower())
        for r in deferred_cited:
            # §5 routing — re-check with container resolution available.
            route, route_meta = _route_record(r, container_index=container_index)
            if route == "review":
                r["_route_reason"] = route_meta.get("reason")
                review.append(r)
                # tier was already counted in pass 1 as DIRECT_WRITE; rebalance.
                stats.direct_write -= 1
                stats.by_kind[r["kind"]]["direct_write"] -= 1
                stats.review_queued += 1
                stats.by_kind[r["kind"]]["review"] += 1
                continue

            p = r["payload"]
            cid = _slugify(p["cited_id"])

            # The typed pipeline writes project_type ∈ {book, journal_article,
            # book_chapter, magazine_article, newspaper_article, web_source,
            # exhibition_catalog, interview, thesis_dissertation, ...}.
            # Older non-typed records fall back to "cited_work".
            project_type = p["project_type"] if p.get("project_type") in CITED_TYPES else "cited_work"

            # Type-specific extras from the typed extractor go directly on the
            # node so the editor UI can render them per type. e.g. for a
            # journal_article the node ends up with `journal`, `volume`,
            # `issue`, `doi`; a web_source gets `site_name`, `url`, etc.
            extras = dict(p.get("extra_fields") or {})

            # Bilingual canonical fields (ontology §2.4.2)
            extras.setdefault("title_orig", p.get("title_orig"))
            extras.setdefault("title_translation", p.get("title_translation"))
            extras.setdefault("orig_lang", p.get("orig_lang"))
            extras.setdefault("translation_lang", p.get("translation_lang"))
            if p.get("citation_style") in STYLE_ALLOWLIST:
                extras["citation_style"] = p["citation_style"]
            if p.get("pages"):
                extras["pages"] = p["pages"]
            extras["original_language"] = _infer_language(p.get("title_orig"))
            extras["project_type"] = project_type
            # Original publisher + translation edition (ontology §2.4.2)
            if p.get("original_pub"):
                extras["original_pub"] = p["original_pub"]
            if p.get("translation_edition"):
                extras["translation_edition"] = p["translation_edition"]
            # Strip None and merge provenance
            extras = {k: v for k, v in extras.items() if v is not None}

            kg.add_source_text_node(
                cid,
                title=p.get("title_orig") or p.get("title_translation") or cid,
                year=_to_int_year(p.get("year")),
                **extras,
            )
            # Author edge
            author_name = p.get("author")
            if author_name:
                grp = dedup_group_key(author_name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(author_name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(
                        agent_id,
                        name=author_name,
                        role="author",
                        dedup_group=dedup_group_key(author_name),
                        alt_spellings=[author_name],
                        all_roles=["author"],
                        mention_count=1,
                    )
                kg.link_written_by(cid, agent_id)

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

            # Translation-edition publisher → translation_published_by
            trans_pub = p.get("translation_edition") or {}
            if trans_pub.get("publisher"):
                inst_id = _slugify(trans_pub["publisher"])
                if not kg.G.has_node(f"institution:{inst_id.lower()}"):
                    kg.add_institution_node(
                        inst_id, name=trans_pub["publisher"], kind="publisher",
                        city=trans_pub.get("city"),
                    )
                kg.link_translation_published_by(cid, inst_id)

            # Translation-edition translator → translated_by (ontology §3.2).
            # `translator` may carry multiple names joined by " in "/" and "/",".
            translator_raw = trans_pub.get("translator")
            if translator_raw:
                for tname in _split_person_names(translator_raw):
                    tgrp = dedup_group_key(tname)
                    tag_id = agent_id_by_dedup.get(tgrp) or _slugify(tname)
                    if not kg.G.has_node(f"agent:{tag_id.lower()}"):
                        kg.add_agent_node(
                            tag_id, name=tname, role="translator",
                            dedup_group=tgrp, alt_spellings=[tname],
                            all_roles=["translator"], mention_count=1,
                        )
                        agent_id_by_dedup[tgrp] = tag_id
                    kg.link_translated_by(cid, tag_id)

            # Editor (e.g. on book_chapter) → edited_by
            editor_raw = p.get("editor")
            if editor_raw:
                for ename in _split_person_names(editor_raw):
                    egrp = dedup_group_key(ename)
                    eag_id = agent_id_by_dedup.get(egrp) or _slugify(ename)
                    if not kg.G.has_node(f"agent:{eag_id.lower()}"):
                        kg.add_agent_node(
                            eag_id, name=ename, role="editor",
                            dedup_group=egrp, alt_spellings=[ename],
                            all_roles=["editor"], mention_count=1,
                        )
                        agent_id_by_dedup[egrp] = eag_id
                    kg.link_edited_by(cid, eag_id)

    # Pass 4: artwork nodes + written_by edge to artist + hosted_by + cited_in.
    # Artworks reuse source_text with project_type="artwork"; biblio fields
    # (publisher / city) are NOT required because visual artwork often has
    # no bibliographic data. Bilingual title fields per ontology §2.4.2.
    if not dry_run:
        from .entity_extraction.name_dedup import dedup_group_key
        for r in deferred_artwork:
            p = r["payload"]
            wid = _slugify(p["work_id"])
            year_int = _to_int_year(p.get("year"))
            extra = {
                # Canonical bilingual (ontology §2.4.2)
                "title_orig": p.get("title_orig"),
                "title_translation": p.get("title_translation"),
                "orig_lang": p.get("orig_lang"),
                "translation_lang": p.get("translation_lang"),
                "artist": p.get("artist"),
                "medium": p.get("medium"),
                "project_type": "artwork",
            }
            extra = {k: v for k, v in extra.items() if v is not None}
            canonical_title = (
                p.get("title_orig")
                or p.get("title_translation")
                or wid
            )
            kg.add_source_text_node(
                wid,
                title=canonical_title,
                year=year_int,
                **extra,
            )
            artist_name = p.get("artist")
            if artist_name:
                grp = dedup_group_key(artist_name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(artist_name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(
                        agent_id,
                        name=artist_name,
                        role="artist",
                        dedup_group=dedup_group_key(artist_name),
                        alt_spellings=[artist_name],
                        all_roles=["artist"],
                        mention_count=1,
                    )
                kg.link_written_by(wid, agent_id)

            # Hosted_by: gallery/museum institution that hosted the artwork
            host_name = p.get("host_institution")
            if host_name:
                inst_id = _slugify(host_name)
                if not kg.G.has_node(f"institution:{inst_id.lower()}"):
                    kg.add_institution_node(
                        inst_id, name=host_name, kind="gallery",
                        city=p.get("host_city"),
                    )
                kg.link_hosted_by(wid, inst_id)

            # Container (e.g. exhibition catalogue this artwork appears in)
            container_id = p.get("container_work_id")
            if container_id:
                kg.link_cited_in(wid, container_id)

    # Pass 5: performance nodes (ontology §2.4.1 project_type="performance").
    # Creators (choreographer/director/dramaturg/composer/artist/author) wire
    # via written_by; performers (performer/dancer/actor) wire via
    # performed_by; venue wires via hosted_by.
    if not dry_run:
        from .entity_extraction.name_dedup import dedup_group_key
        for r in deferred_performance:
            p = r["payload"]
            wid = _slugify(p["work_id"])
            year_int = _to_int_year(p.get("year"))
            extra = {
                "title_orig": p.get("title_orig"),
                "title_translation": p.get("title_translation"),
                "orig_lang": p.get("orig_lang"),
                "translation_lang": p.get("translation_lang"),
                "performance_kind": p.get("performance_kind"),
                "project_type": "performance",
            }
            extra = {k: v for k, v in extra.items() if v is not None}
            canonical_title = (
                p.get("title_orig")
                or p.get("title_translation")
                or wid
            )
            kg.add_source_text_node(
                wid,
                title=canonical_title,
                year=year_int,
                **extra,
            )

            # Creators → written_by (ontology §3.2)
            for creator in (p.get("creators") or []):
                name = (creator.get("name") or "").strip()
                role = (creator.get("role") or "director").strip()
                if not name:
                    continue
                if role not in ROLE_ALLOWLIST:
                    role = "director"
                grp = dedup_group_key(name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(
                        agent_id,
                        name=name,
                        role=role,
                        dedup_group=grp,
                        alt_spellings=[name],
                        all_roles=[role],
                        mention_count=1,
                    )
                kg.link_written_by(wid, agent_id)

            # Performers → performed_by (ontology §3.2)
            for perf in (p.get("performers") or []):
                name = (perf.get("name") or "").strip()
                role = (perf.get("role") or "performer").strip()
                if not name:
                    continue
                if role not in ROLE_ALLOWLIST:
                    role = "performer"
                grp = dedup_group_key(name)
                agent_id = agent_id_by_dedup.get(grp) or _slugify(name)
                if not kg.G.has_node(f"agent:{agent_id.lower()}"):
                    kg.add_agent_node(
                        agent_id,
                        name=name,
                        role=role,
                        dedup_group=grp,
                        alt_spellings=[name],
                        all_roles=[role],
                        mention_count=1,
                    )
                kg.link_performed_by(wid, agent_id)

            # Venue → hosted_by (ontology §3.2)
            venue_name = p.get("venue")
            if venue_name:
                inst_id = _slugify(venue_name)
                if not kg.G.has_node(f"institution:{inst_id.lower()}"):
                    kg.add_institution_node(
                        inst_id, name=venue_name, kind="theatre",
                        city=p.get("venue_city"),
                    )
                kg.link_hosted_by(wid, inst_id)

            # Container (festival programme / exhibition catalogue / etc.)
            container_id = p.get("container_work_id")
            if container_id:
                kg.link_cited_in(wid, container_id)

    # Pass 6: concept nodes + originating-author agent + source-work cited_work
    # + cited_in to the container where the concept was quoted.
    # Ontology §2.2 concept node; §3.3 attributed_to via translation_mapping
    # bridge is wired by termbase code, not here. We DO create the concept,
    # its originating author (as an agent_person), and the source_work
    # source_text where the concept was introduced (with cited_in →
    # container so the lineage is traceable).
    if not dry_run:
        from .entity_extraction.name_dedup import dedup_group_key
        for r in deferred_concept:
            p = r["payload"]
            concept_id = p.get("concept_id") or f"concept:{_slugify(p.get('label', ''))}"
            label = p.get("label") or p.get("label_orig") or p.get("label_translation") or ""
            if not label:
                continue
            domain = p.get("domain") or "humanities"

            # Concept node (single canonical write per ontology §2.2)
            kg.add_concept_node(
                concept_id,
                label=label,
                domain=domain,
                definition=p.get("definition", ""),
                # Bilingual label captured for downstream curator UI; the
                # ontology only requires `label`/`domain`/`definition` but
                # carrying alt labels is informational, not a violation.
                label_orig=p.get("label_orig"),
                label_translation=p.get("label_translation"),
                orig_lang=p.get("orig_lang"),
                translation_lang=p.get("translation_lang"),
            )

            originating_author = p.get("originating_author")
            source_work_title = p.get("source_work_title")
            source_work_year = _to_int_year(p.get("source_work_year"))
            container_id = p.get("container_work_id")
            # When a concept is mentioned across multiple containers, dedup
            # accumulates them on `cited_containers`. Union the primary +
            # the accumulated list (deduplicated).
            cited_containers = list(p.get("cited_containers") or [])
            if container_id and container_id not in cited_containers:
                cited_containers.append(container_id)

            # Originating author → agent node
            author_agent_id = None
            if originating_author:
                grp = dedup_group_key(originating_author)
                author_agent_id = agent_id_by_dedup.get(grp) or _slugify(originating_author)
                if not kg.G.has_node(f"agent:{author_agent_id.lower()}"):
                    kg.add_agent_node(
                        author_agent_id,
                        name=originating_author,
                        role="author",
                        dedup_group=grp,
                        alt_spellings=[originating_author],
                        all_roles=["author"],
                        mention_count=1,
                    )
                    agent_id_by_dedup[grp] = author_agent_id
            # Concept → theorist attribution (concept's own attributed_to edge,
            # distinct from the mapping attribution which records the translator).
            # Guard: a concept is held by its originating theorist, never by the
            # translator (Citation-model invariant).
            if author_agent_id and author_agent_id.lower() not in ("urban-belina", "belina-urban"):
                kg.link_attributed_to(concept_id, author_agent_id)

            # Source work → cited_work source_text, with cited_in →
            # container(s) and written_by → originating author
            sw_id = None
            if source_work_title:
                src_parts = [originating_author, source_work_title]
                if source_work_year:
                    src_parts.append(str(source_work_year))
                src_parts = [s for s in src_parts if s]
                sw_id = _slugify("-".join(src_parts))
                if not kg.G.has_node(f"source:{sw_id.lower()}"):
                    kg.add_source_text_node(
                        sw_id,
                        title=source_work_title,
                        year=source_work_year,
                        project_type="book",  # default; curator can refine
                    )
                if author_agent_id:
                    kg.link_written_by(sw_id, author_agent_id)
                # One cited_in edge per distinct container (O-17 forbids
                # self-loops; link_cited_in already drops them).
                for cid in cited_containers:
                    if cid and cid != sw_id:
                        kg.link_cited_in(sw_id, cid)

    # Write review and dropped sinks (review merges with any existing queue so
    # successive editor confirms accumulate instead of clobbering — O-10).
    review_path.parent.mkdir(parents=True, exist_ok=True)
    if review:
        merged = _merge_review_queue(review_path, review)
        _atomic_write_text(
            review_path,
            json.dumps(merged, ensure_ascii=False, indent=2, default=str),
        )
    if dropped_lines:
        with open(dropped_path, "a", encoding="utf-8") as f:
            for line in dropped_lines:
                f.write(line + "\n")

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


_PERSON_SPLIT_RE = re.compile(
    r"\s*(?:,| in | and | et |\s&\s|\s/\s|;)\s*",
    re.IGNORECASE,
)

def _split_person_names(raw: str) -> list[str]:
    """Split a multi-person string into individual canonical names.

    Examples:
        "Samo Tom\u0161i\u010d in Ana \u017derjav"  -> ["Samo Tom\u0161i\u010d", "Ana \u017derjav"]
        "Foucault, M., and Deleuze, G."              -> ["Foucault, M.", "Deleuze, G."]
        "A; B; C"                                    -> ["A", "B", "C"]

    Returns the original string as a 1-element list if no separators are
    found. Empty fragments are dropped.
    """
    if not raw:
        return []
    parts = [p.strip() for p in _PERSON_SPLIT_RE.split(raw) if p.strip()]
    # Strip stray conjunction prefixes that landed at the head of a fragment
    # (e.g. "and Deleuze, G." -> "Deleuze, G.").
    cleaned: list[str] = []
    for p in parts:
        low = p.lower()
        for lead in ("and ", "in ", "& ", "et "):
            if low.startswith(lead):
                p = p[len(lead):].strip()
                break
        if p:
            cleaned.append(p)
    # Merge short trailing initials back into the previous fragment, but only
    # when the previous fragment is a real name (>=4 chars), so "A; B; C"
    # stays as three single-char entries instead of being collapsed.
    merged: list[str] = []
    for p in cleaned:
        if (merged and len(p) <= 3 and len(merged[-1]) >= 4
                and (p.endswith(".") or len(p) == 1)):
            merged[-1] = f"{merged[-1]}, {p}"
        else:
            merged.append(p)
    return merged

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

