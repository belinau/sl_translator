"""Compliance tests for translate_core/vl_extractor.py.

Each assertion corresponds to one §2.5/§2.6/§2.4.1 violation listed in
data/ontology_audit/generic-vl.md.

Uses no KG / no DB / no VL server — exercises pure record builders.
"""

from translate_core import vl_extractor
from translate_core.vl_extractor import (
    _normalize_role,
    _make_person_record,
    _make_institution_record,
)
from translate_core.entity_extraction import name_dedup


# §2.5 — _normalize_role MUST preserve the full 14-value allowlist ------------

def test_normalize_role_choreographer_preserved():
    assert _normalize_role("choreographer") == "choreographer"


def test_normalize_role_performer_preserved():
    assert _normalize_role("performer") == "performer"


def test_normalize_role_director_preserved():
    assert _normalize_role("director") == "director"


def test_normalize_role_unknown_collapses_to_agent():
    assert _normalize_role("xyz_unknown_role") == "agent"


# §2.5 — _make_person_record.payload.dedup_group MUST use dedup_group_key ----

def test_make_person_record_dedup_group_uses_name_dedup_key():
    rec = _make_person_record(
        {"name": "Michel Foucault", "role": "author"},
        "origin", 0,
        "Michel Foucault wrote things.",
        "tgt",
    )
    assert rec, "person record must not be empty for a plausible name in segment"
    expected = name_dedup.dedup_group_key("Michel Foucault")
    assert rec["payload"]["dedup_group"] == expected
    # And specifically NOT the slugify form (the violation we just fixed):
    assert rec["payload"]["dedup_group"] != "michel-foucault"


# §2.6 — _make_institution_record.kind MUST be clamped to allowlist ----------

def test_make_institution_record_kind_clamped_to_other():
    rec = _make_institution_record(
        {"name": "Maska", "type": "press", "city": "Ljubljana"},
        "origin", 0,
        "Published by Maska in Ljubljana.",
        "tgt",
    )
    assert rec, "institution record must not be empty for a name in segment"
    assert rec["payload"]["kind"] == "other"
    assert rec["payload"]["kind"] != "press"


# Dead-code removal — _make_work_record / _make_artwork_record gone ----------

def test_make_work_record_removed():
    assert not hasattr(vl_extractor, "_make_work_record")


def test_make_artwork_record_removed():
    assert not hasattr(vl_extractor, "_make_artwork_record")
