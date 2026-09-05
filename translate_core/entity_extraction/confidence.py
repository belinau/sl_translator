"""Per-record confidence scoring.

Each extracted entity record carries:
- `confidence` ∈ [0, 1]
- `reason_codes`: list[str] of signals that contributed
- `tier`: ConfidenceTier (DIRECT_WRITE / REVIEW / DROP)

Thresholds:
- confidence ≥ 0.85 → DIRECT_WRITE (written directly to KG)
- 0.55 ≤ confidence < 0.85 → REVIEW (queued for user review)
- confidence < 0.55 → DROP (logged forensically, not written)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ConfidenceTier(str, Enum):
    DIRECT_WRITE = "direct_write"
    REVIEW = "review"
    DROP = "drop"


THRESHOLD_DIRECT = 0.85
THRESHOLD_REVIEW = 0.55


@dataclass
class ScoreResult:
    confidence: float
    reason_codes: list[str] = field(default_factory=list)
    tier: ConfidenceTier = ConfidenceTier.DROP


def _to_tier(score: float) -> ConfidenceTier:
    if score >= THRESHOLD_DIRECT:
        return ConfidenceTier.DIRECT_WRITE
    if score >= THRESHOLD_REVIEW:
        return ConfidenceTier.REVIEW
    return ConfidenceTier.DROP


def score_record(record_kind: str, signals: dict) -> ScoreResult:
    """Score a record based on its kind and the signals collected during extraction.

    `record_kind`:
      - "translated_work": book/article the user translated
      - "cited_work": bibliography entry inside a translated work
      - "artwork": catalogue artwork record
      - "artist": catalogue artist header
      - "agent_person": person extracted as agent
      - "institution": publisher / gallery / venue
      - "festival": festival programme top-level record

    `signals`: dict with boolean / numeric flags the extractor populated.
    """
    s = 0.30  # base
    reasons: list[str] = []

    def bump(code: str, delta: float):
        nonlocal s
        s += delta
        reasons.append(code)

    # Universal bumps (Phase 3): the +0.60 smol_verified_classification
    # blanket bump was removed — it caused every smol record to bypass the
    # review queue (audit §3.4 / ontology §4 invariant 9). The signal flag
    # itself is still informational and may appear in `signals`, but it
    # does not contribute to the score.
    if signals.get("smol_extracted"):
        bump("smol_extracted", 0.15)
    if signals.get("verified_from_text"):
        bump("verified_from_text", 0.10)

    # Composite bump (Phase 3): 2-of-3 of {title_bilingual, container_attached,
    # project_type_typed}. `has_bilingual_title` is accepted as an alias for
    # `title_bilingual` because cited_work builders historically used that
    # name for the same notion. Counted at most once across the two aliases.
    _composite_signals = (
        bool(signals.get("title_bilingual") or signals.get("has_bilingual_title")),
        bool(signals.get("container_attached")),
        bool(signals.get("project_type_typed")),
    )
    if sum(_composite_signals) >= 2:
        bump("composite_2of3", 0.30)

    # Curator endorsement (Phase 3): explicit human approval signal.
    if signals.get("curator_endorsed"):
        bump("curator_endorsed", 0.40)
    
    if record_kind == "translated_work":
        if signals.get("seeded"):
            # User-confirmed translated work from data/translated_works_seeds.json
            bump("seeded_in_manifest", 0.70)
        if signals.get("has_title"):
            bump("has_title", 0.10)
        if signals.get("has_author"):
            bump("has_author", 0.10)
        if signals.get("has_year"):
            bump("has_year", 0.05)
        if signals.get("position_front_matter"):
            bump("position_front_matter", 0.05)
        if signals.get("title_bilingual"):
            bump("title_bilingual", 0.05)
        if signals.get("has_publisher"):
            bump("has_publisher", 0.05)

    elif record_kind == "cited_work":
        # Generic cited_work (legacy path / non-typed extraction). Per-type
        # cited_works (book / journal_article / book_chapter / …) get scored
        # via the per-type branches below.
        if signals.get("has_author"):
            bump("has_author", 0.20)
        if signals.get("has_title"):
            bump("has_title", 0.15)
        if signals.get("has_year"):
            bump("has_year", 0.10)
        
        if signals.get("has_publisher"):
            bump("has_publisher", 0.15)

        # Phase 3: bilingual-title credit moved into the composite_2of3 gate
        # (see universal bumps block). The per-kind `has_bilingual_title`
        # bump that lived here was removed because it double-credited the
        # bilingual axis already counted by the composite. Tests in
        # tests/test_confidence.py require composite alone to deliver 0.85.
        # Phase 1B blueprint §8 / audit §3.3: signal renamed from
        # SL-baked `has_sl_edition` to language-neutral
        # `has_target_lang_edition`. Maps to ontology §2.4.2's
        # `slovenian_edition` sub-dict (sub-dict key rename deferred to
        # Phase 11). No backwards-compat aliasing — the legacy key is
        # now ignored.
        if signals.get("has_target_lang_edition"):
            bump("has_target_lang_edition", 0.10)
        
        

    # ── Typed citations: per-type scoring (spec §8) ────────────────────────
    # Each type pegs the structural fields its schema declares required.
    # Records reaching the scorer have already been verified field-by-field
    # by vl_typed_verifier, so any field set on `signals` corresponds to a
    # substring-checked, type-valid field in the segment text.

    elif record_kind == "book":
        if signals.get("has_author"):
            bump("has_author", 0.20)
        if signals.get("has_title"):
            bump("has_title", 0.15)
        if signals.get("has_year"):
            bump("has_year", 0.10)
        if signals.get("has_publisher"):
            bump("has_publisher", 0.15)
        
        
        
        

    elif record_kind == "book_chapter":
        # Book chapters need authors + chapter_title + book_title + year + editors
        if signals.get("has_author"):
            bump("has_author", 0.15)
        if signals.get("has_title"):
            bump("has_chapter_title", 0.15)
        if signals.get("has_book_title"):
            bump("has_book_title", 0.15)
        if signals.get("has_editors"):
            bump("has_editors", 0.10)
        if signals.get("has_year"):
            bump("has_year", 0.10)
        
        
        

    elif record_kind == "journal_article":
        # Journal articles need authors + article_title + journal + year
        # (volume/pages strongly expected; if the verifier saw a forbidden
        # publisher field it would have rejected the record before this).
        if signals.get("has_author"):
            bump("has_author", 0.20)
        if signals.get("has_title"):
            bump("has_article_title", 0.15)
        if signals.get("has_journal"):
            bump("has_journal", 0.15)
        if signals.get("has_volume"):
            bump("has_volume", 0.05)
        if signals.get("has_year"):
            bump("has_year", 0.10)
        if signals.get("has_pages"):
            bump("has_pages", 0.05)
        
        
        

    elif record_kind == "magazine_article":
        if signals.get("has_title"):
            bump("has_article_title", 0.20)
        if signals.get("has_magazine"):
            bump("has_magazine", 0.20)
        if signals.get("has_date"):
            bump("has_date", 0.15)
        if signals.get("has_author"):
            bump("has_author", 0.10)
        
        

    elif record_kind == "newspaper_article":
        if signals.get("has_title"):
            bump("has_article_title", 0.20)
        if signals.get("has_newspaper"):
            bump("has_newspaper", 0.20)
        if signals.get("has_date"):
            bump("has_date", 0.15)
        if signals.get("has_author"):
            bump("has_author", 0.10)
        
        

    elif record_kind == "web_source":
        # URL is mandatory in spec; if present + verified, score sharply.
        if signals.get("has_url"):
            bump("has_url", 0.25)
        if signals.get("has_title"):
            bump("has_title", 0.20)
        if signals.get("has_site_name"):
            bump("has_site_name", 0.15)
        if signals.get("has_author"):
            bump("has_author", 0.05)
        
        

    elif record_kind in ("exhibition_catalog", "exhibition_catalogue"):
        if signals.get("has_title"):
            bump("has_title", 0.20)
        if signals.get("has_venue"):
            bump("has_venue", 0.20)
        if signals.get("has_year"):
            bump("has_year", 0.10)
        if signals.get("has_curators") or signals.get("has_editors"):
            bump("has_curators_or_editors", 0.10)
        if signals.get("in_biblio_cluster"):
            bump("in_biblio_cluster", 0.05)
        
        

    elif record_kind == "interview":
        if signals.get("has_interviewee"):
            bump("has_interviewee", 0.25)
        if signals.get("has_interviewer"):
            bump("has_interviewer", 0.20)
        if signals.get("has_date"):
            bump("has_date", 0.15)
        if signals.get("has_publication_or_network"):
            bump("has_publication_or_network", 0.05)
        
        

    elif record_kind == "thesis_dissertation":
        if signals.get("has_author"):
            bump("has_author", 0.20)
        if signals.get("has_title"):
            bump("has_title", 0.15)
        if signals.get("has_degree_type"):
            bump("has_degree_type", 0.10)
        if signals.get("has_institution"):
            bump("has_institution", 0.15)
        if signals.get("has_year"):
            bump("has_year", 0.10)
        
        

    elif record_kind in ("short_reference", "other"):
        # Always REVIEW tier: short_refs need back-reference resolution,
        # 'other' didn't fit any type cleanly.
        bump("structural_review_only", 0.30)

    elif record_kind == "artwork":
        if signals.get("has_title"):
            bump("has_title", 0.20)
        if signals.get("has_year"):
            bump("has_year", 0.15)
        if signals.get("has_medium"):
            bump("has_medium", 0.10)
        if signals.get("has_artist") or signals.get("has_artist_link"):
            bump("has_artist", 0.20)
        if signals.get("has_host"):
            bump("has_host", 0.10)
        if signals.get("has_bilingual_title"):
            bump("has_bilingual_title", 0.10)
        

    elif record_kind == "performance":
        # Performances need at minimum a title plus either creators or
        # performers to be a real ontological node; the smol builder already
        # drops shapeless performances.
        if signals.get("has_title"):
            bump("has_title", 0.20)
        if signals.get("has_year"):
            bump("has_year", 0.10)
        if signals.get("has_creators"):
            bump("has_creators", 0.25)
        if signals.get("has_performers"):
            bump("has_performers", 0.15)
        if signals.get("has_venue"):
            bump("has_venue", 0.10)
        if signals.get("has_bilingual_title"):
            bump("has_bilingual_title", 0.10)
        

    elif record_kind == "concept":
        # Concept scoring: label + originating-author attribution is the
        # backbone; without an originating author the concept is just a term
        # and belongs in the termbase layer, not the concept layer.
        if signals.get("has_label"):
            bump("has_label", 0.20)
        if signals.get("has_bilingual_label"):
            bump("has_bilingual_label", 0.15)
        if signals.get("has_originating_author"):
            bump("has_originating_author", 0.25)
        # Verified attribution (grounded + roster/model-confirmed): the
        # person→concept link has passed the three-layer verifier, so the
        # concept + its attributed_to edge may direct-write. Unverified
        # attributions keep the baseline author credit for the *node* tier;
        # the edge itself is gated separately in kg_ingest_entities.
        if signals.get("attribution_verified"):
            bump("attribution_verified", 0.10)
        if signals.get("has_source_work"):
            bump("has_source_work", 0.20)
        if signals.get("has_container"):
            bump("has_container", 0.10)
        

    elif record_kind == "artist":
        if signals.get("plausible_person_name"):
            bump("plausible_person_name", 0.30)
        if signals.get("preceded_artwork_records"):
            bump("preceded_artwork_records", 0.25)
        if signals.get("all_caps_header"):
            bump("all_caps_header", 0.10)

    elif record_kind == "agent_person":
        if signals.get("plausible_person_name"):
            bump("plausible_person_name", 0.25)
        
        if signals.get("multi_mention"):
            bump("multi_mention", 0.15)
        if signals.get("multi_origin"):
            bump("multi_origin", 0.10)
        if signals.get("role_attribution_context"):
            bump("role_attribution_context", 0.10)
        
        if signals.get("bilingual_name"):
            bump("bilingual_name", 0.10)
        

    elif record_kind == "institution":
        if signals.get("known_publisher"):
            bump("known_publisher", 0.30)
        if signals.get("named_institution_kind"):
            bump("named_institution_kind", 0.20)
        if signals.get("institution_pattern_match"):
            bump("institution_pattern_match", 0.15)
        if signals.get("multi_mention"):
            bump("multi_mention", 0.10)
        
        

    elif record_kind == "festival":
        if signals.get("has_festival_title"):
            bump("has_festival_title", 0.25)
        if signals.get("has_lead_curator"):
            bump("has_lead_curator", 0.20)
        if signals.get("has_year"):
            bump("has_year", 0.10)

    # Typed citation kind set (shared by verified_typed_pipeline and style_detected)
    _TYPED_KINDS = {
        "book", "book_chapter", "journal_article", "magazine_article",
        "newspaper_article", "web_source", "exhibition_catalog", "exhibition_catalogue",
        "interview", "thesis_dissertation",
    }

    # Typed-pipeline signal: records that went through the classified →
    # extracted → verified VL pipeline are more reliable than the generic
    # batch path. Applies to all typed citation kinds + agent_person.
    if record_kind in _TYPED_KINDS or record_kind == "agent_person":
        if signals.get("verified_typed_pipeline"):
            bump("verified_typed_pipeline", 0.15)
    # Style-detected bump (spec §8): if the segment matched a citation style
    # by regex markers (Glej / parens / ISBN / vol+no), give a uniform boost
    # to typed citation kinds.
    if record_kind in _TYPED_KINDS and signals.get("style_detected"):
        bump("style_detected", 0.05)

    # Cap and clamp. Round to absorb the float underflow that otherwise
    # makes 0.30+0.15+0.10 land at 0.5499999… and miss the 0.55 REVIEW
    # threshold by 1 ULP (Phase 3 tests rely on exact boundary equality).
    s = max(0.0, min(1.0, round(s, 6)))
    return ScoreResult(confidence=s, reason_codes=reasons, tier=_to_tier(s))


