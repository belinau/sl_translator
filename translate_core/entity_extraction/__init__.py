"""Entity extraction from translation memories.

Per-segment auto-classification → project-type dispatch → confidence-tiered
ingestion. Replaces the monolithic regex-based extract_agents_and_works.py.

Entry point: run_entity_extraction.py at repo root.
"""

from .segment_classifier import classify_segments, SegmentClass
from .origin_walker import walk_origin, OriginContext
from .bilingual_titles import parse_bilingual_title, BilingualTitle
from .name_dedup import dedup_group_key, normalize_person_name
from .confidence import score_record, ConfidenceTier

__all__ = [
    "classify_segments",
    "SegmentClass",
    "walk_origin",
    "OriginContext",
    "parse_bilingual_title",
    "BilingualTitle",
    "dedup_group_key",
    "normalize_person_name",
    "score_record",
    "ConfidenceTier",
]
