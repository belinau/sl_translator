"""Confirm-pipeline tests — TM upsert + KG NLP-based promote_pair.

Covers the two bugs the user reported:
  1. save_pair_to_tm wrote N duplicate <tu> blocks for N confirms of
     the same segment (no upsert).
  2. KnowledgeGraph.promote_pair stuffed the entire confirmed segment
     into the KG as a single fake concept + sentence-as-term.

The KG tests stub the Spacy/Stanza pipelines so the assertions can
focus on graph-mutation behaviour (term upsert with frequency bump,
mapping promotion with verified=True, no sentence-as-concept).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

import pytest

from _kg_helpers import make_kg, seed_pair


# ---------------------------------------------------------------------------
# TM upsert
# ---------------------------------------------------------------------------

@pytest.fixture
def tm_dir(tmp_path, monkeypatch):
    """Point config.TM_DIR at a temp dir and replace the in-memory TM with
    a SimpleNamespace exposing the same `entries` list main.py mutates."""
    import config as _config
    import main as _main

    monkeypatch.setattr(_config, "TM_DIR", tmp_path)
    monkeypatch.setattr(_main.config, "TM_DIR", tmp_path)
    class _StubTM:
        def __init__(self):
            self.entries = []
        def upsert_runtime_pair(self, source, target, src_lang, tgt_lang, origin="working.tmx"):
            for e in self.entries:
                if e["source"] == source and e["origin"] == origin:
                    e["target"] = target
                    return
            self.entries.append({"source": source, "target": target, "origin": origin, "source_lang": src_lang, "target_lang": tgt_lang})
    monkeypatch.setattr(_main, "tm", _StubTM())
    return tmp_path


def _count_tus(tm_path: Path) -> int:
    return len(re.findall(r"<tu>", tm_path.read_text(encoding="utf-8")))


def _targets(tm_path: Path) -> list[str]:
    """Return target seg contents in document order."""
    raw = tm_path.read_text(encoding="utf-8")
    return re.findall(
        r'<tuv xml:lang="sl"><seg>([^<]*)</seg></tuv>', raw,
    )


def test_save_pair_to_tm_appends_new_pair(tm_dir):
    from main import save_pair_to_tm, tm

    save_pair_to_tm("Hello world.", "Pozdrav svet.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert tm_path.exists()
    assert _count_tus(tm_path) == 1
    assert _targets(tm_path) == ["Pozdrav svet."]
    assert tm.entries == [{
        "source": "Hello world.",
        "target": "Pozdrav svet.",
        "origin": "working.tmx",
        "source_lang": "en",
        "target_lang": "sl",
    }]


def test_save_pair_to_tm_is_idempotent_on_identical_pair(tm_dir):
    """The reported bug: confirming the same segment five times wrote
    five duplicate TUs. The fix must collapse them to one."""
    from main import save_pair_to_tm, tm

    for _ in range(5):
        save_pair_to_tm("Hello world.", "Pozdrav svet.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert _count_tus(tm_path) == 1, "duplicate TUs leaked through"
    assert len(tm.entries) == 1


def test_save_pair_to_tm_rewrites_target_on_change(tm_dir):
    """Confirming the same source with a corrected target rewrites the
    existing TU's target rather than appending a stale copy."""
    from main import save_pair_to_tm, tm

    save_pair_to_tm("Hello world.", "Pozdrav.", "en->sl")
    save_pair_to_tm("Hello world.", "Pozdravljen svet.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert _count_tus(tm_path) == 1
    assert _targets(tm_path) == ["Pozdravljen svet."]
    assert len(tm.entries) == 1
    assert tm.entries[0]["target"] == "Pozdravljen svet."


def test_save_pair_to_tm_appends_distinct_sources_independently(tm_dir):
    from main import save_pair_to_tm

    save_pair_to_tm("Hello.", "Pozdrav.", "en->sl")
    save_pair_to_tm("Goodbye.", "Adijo.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert _count_tus(tm_path) == 2
    assert sorted(_targets(tm_path)) == ["Adijo.", "Pozdrav."]


def test_save_pair_to_tm_handles_xml_special_chars(tm_dir):
    """Sources containing &, <, > etc. must round-trip through the
    matcher so a re-confirm doesn't append a duplicate."""
    from main import save_pair_to_tm

    src = 'Smith & Jones <on the right>'
    save_pair_to_tm(src, "Smith in Jones.", "en->sl")
    save_pair_to_tm(src, "Smith in Jones.", "en->sl")
    tm_path = tm_dir / "working.tmx"
    assert _count_tus(tm_path) == 1

