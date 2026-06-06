"""Smoke tests for the Phase-2 editor-safe outline module.

These guard the symbols ``import_book.py`` consumes after we extracted them
from ``translate_core/vl_parser.py``. See
``docs/superpowers/plans/2026-06-06-parsing-simplification.md`` Phase 2.
"""

from __future__ import annotations

import inspect

from translate_core.book_outline import (
    BookOutline,
    TOCEntry,
    split_paragraphs,
)


def test_split_paragraphs_splits_long_text() -> None:
    """A long single paragraph with sentence boundaries gets split into
    chunks that each honour the function's ``max_chars`` default."""
    # Read the actual default from the function signature so the test
    # doesn't drift if the source changes.
    sig = inspect.signature(split_paragraphs)
    default_max_chars = sig.parameters["max_chars"].default
    assert isinstance(default_max_chars, int) and default_max_chars > 0

    # Build a long paragraph of well-formed sentences so the splitter has
    # legitimate boundaries to cut on (it never splits mid-sentence).
    sentence = "This is a perfectly ordinary sentence of moderate length. "
    # Aim well past 2x default to force multiple splits.
    repeats = max(2, (default_max_chars * 3) // len(sentence))
    long_text = (sentence * repeats).strip()
    assert len(long_text) > default_max_chars * 2

    chunks = split_paragraphs(long_text)

    assert isinstance(chunks, list)
    assert all(isinstance(c, str) for c in chunks)
    assert len(chunks) >= 2, "long input should split into multiple chunks"
    # The splitter cuts at sentence boundaries; a single sentence may still
    # be larger than max_chars (it never splits mid-sentence). Our test
    # sentences are short enough that every resulting chunk should fit.
    for chunk in chunks:
        assert len(chunk) <= default_max_chars, (
            f"chunk of {len(chunk)} chars exceeds max_chars={default_max_chars}"
        )


def test_split_paragraphs_empty_returns_empty_list() -> None:
    """Empty / whitespace-only inputs short-circuit to an empty list."""
    assert split_paragraphs("") == []
    assert split_paragraphs("   \n\n  ") == []


def test_book_outline_instantiates_with_defaults() -> None:
    """``BookOutline()`` constructs cleanly with the documented defaults."""
    outline = BookOutline()

    assert outline.entries == []
    assert outline.page_to_chapter == {}
    assert outline.reconciliation_warnings == []

    # Each field uses an independent factory (no shared mutable default).
    other = BookOutline()
    outline.entries.append(
        TOCEntry(
            level=1,
            kind="chapter",
            number="1",
            title="Prologue",
            page_number="7",
        )
    )
    outline.page_to_chapter["7"] = 0
    outline.reconciliation_warnings.append("warn")

    assert other.entries == []
    assert other.page_to_chapter == {}
    assert other.reconciliation_warnings == []


def test_toc_entry_default_source_page() -> None:
    """``TOCEntry.source_page`` defaults to -1 (sentinel for unknown)."""
    entry = TOCEntry(
        level=2,
        kind="section",
        number="2.1",
        title="Background",
        page_number="42",
    )
    assert entry.source_page == -1
