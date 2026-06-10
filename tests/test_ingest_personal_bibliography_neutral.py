"""Phase 4 — COBISS ingest uses neutral bilingual fields only.

Verifies blueprint §2 (the two surgical edits to
`scripts/ingest_personal_bibliography.py`):

- kwargs rename per `curator_role` branch — author/editor → title_orig+sl,
  translator → title_translation+sl; secondary side flips accordingly.
- `provenance="cobiss_personal"` is stamped on every container/cited write.
- Legacy kwarg names (`title_en`, `title_sl`, `slovenian_edition`) MUST
  never appear in `add_source_text_node` calls.
- Bilingual publisher convention `"A: = B"` → `published_by` +
  `translation_published_by`; singular publisher → only `published_by`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.cobiss_parser import CobissEntry, CobissAgent
import scripts.ingest_personal_bibliography as ing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _curator(role: str) -> CobissAgent:
    return CobissAgent(last_name="BELINA", first_name="Urban", roles=[role])


def _other(role: str = "author", *, first="Jane", last="DOE") -> CobissAgent:
    return CobissAgent(last_name=last, first_name=first, roles=[role])


class _Graph:
    """Minimal networkx-like stub: `has_node` + dict-like `nodes` for upsert."""

    def __init__(self) -> None:
        self._nodes: set[str] = set()
        self.nodes: dict[str, dict] = {}

    def has_node(self, nid: str) -> bool:
        return nid in self._nodes


class _RecordingKG:
    """Drop-in KG stub: records every `add_source_text_node` kwargs.

    Other factory methods return a deterministic id and accumulate edges so
    publisher-split tests can inspect relations.
    """

    def __init__(self) -> None:
        self.source_calls: list[dict] = []
        self.edges: list[tuple[str, str, str]] = []
        self.G = _Graph()

    # --- node factories ---------------------------------------------------

    def add_agent_node(self, *, agent_id, name, role, **_kw):
        return f"agent:{agent_id.lower()}"

    def add_source_text_node(self, *, text_id, title, project_type, **kwargs):
        nid = f"source:{text_id.lower()}"
        self.G._nodes.add(nid)
        self.G.nodes[nid] = {
            "id": nid,
            "title": title,
            "project_type": project_type,
            **kwargs,
        }
        self.source_calls.append(
            {
                "text_id": text_id,
                "title": title,
                "project_type": project_type,
                **kwargs,
            }
        )
        return nid

    def add_institution_node(self, *, inst_id, name, kind, **_kw):
        return f"institution:{inst_id.lower()}"

    # --- edge factories ---------------------------------------------------

    def link_written_by(self, src, tgt) -> bool:
        self.edges.append((src, tgt, "written_by"))
        return True

    def link_translated_by(self, src, tgt) -> bool:
        self.edges.append((src, tgt, "translated_by"))
        return True

    def link_published_by(self, src, tgt) -> bool:
        self.edges.append((src, tgt, "published_by"))
        return True

    def link_translation_published_by(self, src, tgt) -> bool:
        self.edges.append((src, tgt, "translation_published_by"))
        return True

    def save(self) -> None:  # pragma: no cover
        return None


def _drive_ingest(
    monkeypatch, tmp_path, entries: list[CobissEntry]
) -> _RecordingKG:
    kg = _RecordingKG()
    monkeypatch.setattr(ing, "KnowledgeGraph", lambda db_path: kg)
    monkeypatch.setattr(ing, "parse_cobiss_file", lambda _path: entries)
    ing.ingest_bibliography(
        cobiss_path=tmp_path / "fake.txt",
        kg_path=tmp_path / "kg.db",
        dry_run=True,
    )
    return kg


# ---------------------------------------------------------------------------
# A. Author entry — the curator as first author / sole author
# ---------------------------------------------------------------------------


def test_author_entry_kwargs(monkeypatch, tmp_path):
    """`curator_role="author"`: SL → title_orig+orig_lang=sl;
    EN side → title_translation+translation_lang=en."""
    entry = CobissEntry(
        entry_number=1,
        raw_text="",
        agents=[_curator("author")],
        title="Avtorski naslov",
        title_en="Authored Title",
        publisher="Some Publisher",
        year=2010,
        isbn=["978-1"],
    )
    kg = _drive_ingest(monkeypatch, tmp_path, [entry])

    assert len(kg.source_calls) == 1
    call = kg.source_calls[0]
    assert call["title_orig"] == "Avtorski naslov"
    assert call["orig_lang"] == "sl"
    assert call["title_translation"] == "Authored Title"
    assert call["translation_lang"] == "en"
    assert "title_en" not in call
    assert "title_sl" not in call
    assert "slovenian_edition" not in call


def test_author_entry_no_en_side(monkeypatch, tmp_path):
    """Author entry without EN side writes only orig fields."""
    entry = CobissEntry(
        entry_number=1,
        raw_text="",
        agents=[_curator("author")],
        title="Slovenski naslov",
        publisher="Pub",
        year=2010,
        isbn=["978-2"],
    )
    kg = _drive_ingest(monkeypatch, tmp_path, [entry])

    call = kg.source_calls[0]
    assert call["title_orig"] == "Slovenski naslov"
    assert call["orig_lang"] == "sl"
    assert "title_translation" not in call
    assert "translation_lang" not in call


# ---------------------------------------------------------------------------
# B. Translator entry — the curator translates
# ---------------------------------------------------------------------------


def test_translator_entry_kwargs(monkeypatch, tmp_path):
    """`curator_role="translator"`: SL side → title_translation+sl;
    EN side → title_orig+en."""
    entry = CobissEntry(
        entry_number=2,
        raw_text="",
        agents=[_other(role="author"), _curator("translator")],
        title="Slovenski prevod",
        title_en="Original English",
        publisher="Translator Pub",
        year=2015,
        isbn=["978-3"],
    )
    kg = _drive_ingest(monkeypatch, tmp_path, [entry])

    call = kg.source_calls[0]
    assert call["title_translation"] == "Slovenski prevod"
    assert call["translation_lang"] == "sl"
    assert call["title_orig"] == "Original English"
    assert call["orig_lang"] == "en"
    assert "title_en" not in call
    assert "title_sl" not in call


# ---------------------------------------------------------------------------
# C. Editor entry — treated as author (per blueprint §2 Edit A)
# ---------------------------------------------------------------------------


def test_editor_entry_kwargs(monkeypatch, tmp_path):
    """`curator_role="editor"` ⇒ same shape as author: SL→orig, EN→translation."""
    entry = CobissEntry(
        entry_number=3,
        raw_text="",
        agents=[_curator("editor")],
        title="Urednikov uvod",
        title_en="Editor Introduction",
        publisher="Editor Pub",
        year=2018,
        isbn=["978-4"],
    )
    kg = _drive_ingest(monkeypatch, tmp_path, [entry])

    call = kg.source_calls[0]
    assert call["title_orig"] == "Urednikov uvod"
    assert call["orig_lang"] == "sl"
    assert call["title_translation"] == "Editor Introduction"
    assert call["translation_lang"] == "en"


# ---------------------------------------------------------------------------
# D. No legacy fields anywhere
# ---------------------------------------------------------------------------


def test_no_legacy_field_in_kwargs(monkeypatch, tmp_path):
    """No `add_source_text_node` call ever passes title_en/title_sl/slovenian_edition."""
    entries = [
        CobissEntry(
            entry_number=1,
            raw_text="",
            agents=[_curator("author")],
            title="Avtor",
            title_en="Author EN",
            publisher="P",
            year=2010,
            isbn=["1"],
        ),
        CobissEntry(
            entry_number=2,
            raw_text="",
            agents=[_other("author"), _curator("translator")],
            title="Prevod",
            title_en="Translation EN",
            publisher="P2",
            year=2011,
            isbn=["2"],
        ),
        CobissEntry(
            entry_number=3,
            raw_text="",
            agents=[_curator("editor")],
            title="Ured",
            title_en="Edited EN",
            publisher="P3",
            year=2012,
            isbn=["3"],
        ),
    ]
    kg = _drive_ingest(monkeypatch, tmp_path, entries)

    assert len(kg.source_calls) >= 3
    for call in kg.source_calls:
        for banned in ("title_en", "title_sl", "slovenian_edition"):
            assert banned not in call, f"{banned!r} leaked into kwargs: {call}"


# ---------------------------------------------------------------------------
# E. Provenance stamped on every write
# ---------------------------------------------------------------------------


def test_provenance_cobiss_personal(monkeypatch, tmp_path):
    entries = [
        CobissEntry(
            entry_number=1,
            raw_text="",
            agents=[_curator("author")],
            title="X",
            publisher="P",
            year=2010,
            isbn=["1"],
        ),
        CobissEntry(
            entry_number=2,
            raw_text="",
            agents=[_other("author"), _curator("translator")],
            title="Y",
            publisher="P",
            year=2010,
            isbn=["2"],
        ),
    ]
    kg = _drive_ingest(monkeypatch, tmp_path, entries)

    assert kg.source_calls, "expected at least one source_text write"
    for call in kg.source_calls:
        assert call["provenance"] == "cobiss_personal"


# ---------------------------------------------------------------------------
# F. Bilingual publisher split
# ---------------------------------------------------------------------------


def test_bilingual_publisher_split(monkeypatch, tmp_path):
    """`"SL Pub: = EN Pub"` → `published_by` + `translation_published_by`."""
    entry = CobissEntry(
        entry_number=1,
        raw_text="",
        agents=[_other("author"), _curator("translator")],
        title="Naslov",
        title_en="Title",
        publisher="Maska: = English Pub",
        year=2020,
        isbn=["1"],
    )
    kg = _drive_ingest(monkeypatch, tmp_path, [entry])

    relations = [rel for _, _, rel in kg.edges]
    assert "published_by" in relations
    assert "translation_published_by" in relations


def test_single_publisher_no_split(monkeypatch, tmp_path):
    """A non-bilingual publisher string yields only `published_by`."""
    entry = CobissEntry(
        entry_number=1,
        raw_text="",
        agents=[_other("author"), _curator("translator")],
        title="Naslov",
        title_en="Title",
        publisher="Maska",
        year=2020,
        isbn=["1"],
    )
    kg = _drive_ingest(monkeypatch, tmp_path, [entry])

    relations = [rel for _, _, rel in kg.edges]
    assert relations.count("published_by") == 1
    assert "translation_published_by" not in relations


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
