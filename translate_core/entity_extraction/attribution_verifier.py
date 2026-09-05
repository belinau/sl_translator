"""Three-layer verification for concept→agent attributions.

The live smol (DeepSeek) extraction is *author-gated*: a concept is only
emitted when the segment attributes it to a named author. But "the model
said so" is not enough — a hallucinated or misattributed link is exactly
the noise the curator does not want in the KG. This module grounds every
attribution in evidence before any `attributed_to` edge is written.

Layers (cheap → expensive):

1. **Textual grounding** (deterministic, free).
   The model must return ``evidence_span``: a verbatim substring of the
   SOURCE or TARGET where the concept is attributed to the author. We
   verify programmatically that the span really occurs in the segment and
   that **both** the author name and the concept term appear inside it
   (diacritic-insensitive). A fabricated or paraphrased span fails here.

2. **Authority cross-check** (deterministic, free).
   ``data/concept_theorists.json`` is a curated 546-entry concept→theorist
   roster. Where it has an entry for the concept, the attributed author
   must match the roster's theorist (via ``dedup_group_key`` so alt
   spellings count). A match confirms the attribution; a mismatch marks
   it unverified (no edge, queued for review).

3. **Second model pass** (one extra LLM call — only for un-rostered
   concepts). A focused yes/no verification call checks the proposed
   attribution against the segment. Runs ONLY when the concept is not in
   the roster, so rostered+grounded attributions skip it entirely.

Verdict: ``attribution_verified = grounded AND (roster_confirmed OR
model_verified)``. Unverified attributions never produce a KG edge; they
are queued for optional curator confirm so the KG stays noise-free while
the link remains recoverable.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Callable, Optional

from .name_dedup import dedup_group_key, normalize_person_name
from .smol_extractor import _concept_theorist_roster

log = logging.getLogger(__name__)

# A model_verify callable has the contract:
#   (src, tgt, concept_label, author_name, evidence_span) -> bool | None
# True = attribution confirmed, False = rejected, None = model unavailable.
ModelVerifyFn = Callable[[str, str, str, str, str], Optional[bool]]


def _norm_text(text: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace — for substring
    matching that survives case/accent variation."""
    nfkd = unicodedata.normalize("NFKD", (text or "").lower())
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip()


def _norm_name(name: str) -> str:
    """normalize_person_name but lowercased + diacritic-stripped for
    containment checks inside a span."""
    return _norm_text(normalize_person_name(name))


def _span_contains(span_norm: str, needle_norm: str) -> bool:
    """True if needle occurs in span after normalisation. Empty needle → False."""
    if not needle_norm:
        return False
    return needle_norm in span_norm


def _grounding_check(
    evidence_span: str,
    author: str,
    concept_label: str,
    src_text: str,
    tgt_text: str,
) -> bool:
    """Layer 1: the span is a real substring of the segment AND contains
    both the author name and the concept term."""
    if not evidence_span or not author or not concept_label:
        return False
    span_norm = _norm_text(evidence_span)
    if not span_norm:
        return False
    haystack = _norm_text(f"{src_text} {tgt_text}")
    if span_norm not in haystack:
        # The span is not a verbatim piece of the segment — fabricated/paraphrased.
        return False
    # Both the author and the concept must appear *inside* the span itself.
    if not _span_contains(span_norm, _norm_name(author)):
        # Try the raw author string too (normalize_person_name strips titles
        # that may legitimately be absent from the span).
        if not _span_contains(span_norm, _norm_text(author)):
            return False
    if not _span_contains(span_norm, _norm_text(concept_label)):
        return False
    return True


def _roster_check(concept_id: str, author: str) -> tuple[bool, bool]:
    """Layer 2: returns (roster_confirmed, roster_mismatch).

    roster_confirmed → the roster knows this concept and the attributed
    author matches the roster's theorist.
    roster_mismatch  → the roster knows this concept but the attributed
    author does NOT match (strong wrong-attribution signal).
    Both False → the concept is not in the roster (un-rostered → layer 3).
    """
    roster = _concept_theorist_roster()
    roster_author = roster.get(concept_id)
    if not roster_author:
        return False, False
    if dedup_group_key(author) == dedup_group_key(roster_author):
        return True, False
    # Also accept raw normalised equality (dedup_group_key collapses a lot).
    if _norm_name(author) == _norm_name(roster_author):
        return True, False
    return False, True


def verify_concept_attribution(
    record: dict,
    src_text: str,
    tgt_text: str,
    *,
    model_verify: Optional[ModelVerifyFn] = None,
) -> dict:
    """Verify the attribution on a concept record. Mutates ``record["signals"]``
    in place and returns a result dict::

        {
          "grounded": bool,
          "roster_confirmed": bool,
          "roster_mismatch": bool,
          "model_verified": bool|None,   # None = not run / unavailable
          "verified": bool,              # the final verdict
          "reason": str,
        }

    ``verified`` is True iff grounded AND (roster_confirmed OR model_verified).
    """
    p = record.get("payload") or {}
    signals = record.setdefault("signals", {})
    concept_id = p.get("concept_id") or ""
    author = p.get("originating_author") or ""
    label = p.get("label") or p.get("label_orig") or p.get("label_translation") or ""
    evidence_span = p.get("evidence_span") or ""

    # A roster-fallback attribution (filled by _build_concept) is already
    # authority-confirmed — reflect that and skip grounding (there is no
    # evidence span for a fallback). It is verified directly.
    if signals.get("attribution_roster_confirmed") and not evidence_span:
        signals["attribution_grounded"] = True
        signals["attribution_roster_confirmed"] = True
        signals["attribution_verified"] = True
        return {"grounded": True, "roster_confirmed": True,
                "roster_mismatch": False, "model_verified": None,
                "verified": True, "reason": "roster_fallback"}

    grounded = _grounding_check(evidence_span, author, label, src_text, tgt_text)
    signals["attribution_grounded"] = grounded
    if not grounded:
        signals["attribution_verified"] = False
        return {"grounded": False, "roster_confirmed": False,
                "roster_mismatch": False, "model_verified": None,
                "verified": False, "reason": "not_grounded"}

    roster_confirmed, roster_mismatch = _roster_check(concept_id, author)
    signals["attribution_roster_confirmed"] = roster_confirmed
    signals["attribution_roster_mismatch"] = roster_mismatch

    if roster_confirmed:
        signals["attribution_verified"] = True
        return {"grounded": True, "roster_confirmed": True,
                "roster_mismatch": False, "model_verified": None,
                "verified": True, "reason": "roster_confirmed"}

    if roster_mismatch:
        # Roster knows the concept but disagrees with the attributed author —
        # do NOT auto-write; do not spend a model call defending a conflict.
        signals["attribution_verified"] = False
        return {"grounded": True, "roster_confirmed": False,
                "roster_mismatch": True, "model_verified": None,
                "verified": False, "reason": "roster_mismatch"}

    # Un-rostered concept → layer 3 second model pass.
    model_verified: Optional[bool] = None
    if model_verify is not None:
        try:
            model_verified = model_verify(src_text, tgt_text, label,
                                          author, evidence_span)
        except Exception as e:  # never let the verifier crash the commit
            log.warning("attribution model-verify call failed: %s", e)
            model_verified = None
    signals["attribution_model_verified"] = model_verified
    verified = bool(model_verified)
    signals["attribution_verified"] = verified
    return {"grounded": True, "roster_confirmed": False,
            "roster_mismatch": False, "model_verified": model_verified,
            "verified": verified,
            "reason": "model_verified" if verified else
                      ("model_unavailable" if model_verified is None
                       else "model_rejected")}


def verify_records(
    records: list[dict],
    src_text: str,
    tgt_text: str,
    *,
    model_verify: Optional[ModelVerifyFn] = None,
) -> None:
    """Run :func:`verify_concept_attribution` on every concept record in
    ``records``. Non-concept records are untouched. Mutates in place."""
    for r in records:
        if r.get("kind") != "concept":
            continue
        if not (r.get("payload") or {}).get("originating_author"):
            # Nothing to verify — no attribution claimed.
            r.setdefault("signals", {})["attribution_verified"] = False
            continue
        verify_concept_attribution(r, src_text, tgt_text,
                                   model_verify=model_verify)