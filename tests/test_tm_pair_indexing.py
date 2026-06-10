"""Phase 1B (TDD red) — pair-bucket indexing tests for ``TranslationMemory``.

These tests target the blueprint §2-§4 design: a new
``TranslationMemory._entries_by_pair: dict[tuple[str | None, str | None], list[dict]]``
index that holds entries per actual ``(source_lang, target_lang)`` pair, and a
compat-shim ``tm.entries`` that filters to ``("en", "sl")`` + swapped
``("sl", "en")`` so the editor's runtime path keeps working unchanged for
today's EN/SL corpus.

Every test in this module is designed to FAIL against the current
implementation, which:
  - does not expose ``_entries_by_pair`` at all
  - hardcodes every entry's ``source_lang`` / ``target_lang`` to ``en`` / ``sl``
    at loader level (so any non-EN-SL TMX is mis-labelled)
  - silently drops <tuv> children whose ``xml:lang`` is not ``en`` / ``sl``,
    so non-EN-SL fixtures load zero entries
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures"
EN_SL_FIXTURE = FIXTURE_DIR / "tmx_unordered.tmx"
HR_SL_FIXTURE = FIXTURE_DIR / "tmx_hr_sl.tmx"
SL_EN_FIXTURE = FIXTURE_DIR / "tmx_sl_en.tmx"


@pytest.fixture
def tm_dir_en_sl_and_hr_sl(tmp_path):
    """Tmp TM dir containing both the EN-SL and HR-SL fixtures."""
    tm_dir = tmp_path / "tm"
    tm_dir.mkdir()
    shutil.copy(EN_SL_FIXTURE, tm_dir / "tmx_unordered.tmx")
    shutil.copy(HR_SL_FIXTURE, tm_dir / "tmx_hr_sl.tmx")
    return tm_dir


@pytest.fixture
def tm_dir_en_sl_only(tmp_path):
    tm_dir = tmp_path / "tm"
    tm_dir.mkdir()
    shutil.copy(EN_SL_FIXTURE, tm_dir / "tmx_unordered.tmx")
    return tm_dir


@pytest.fixture
def tm_dir_sl_en_only(tmp_path):
    tm_dir = tmp_path / "tm"
    tm_dir.mkdir()
    shutil.copy(SL_EN_FIXTURE, tm_dir / "tmx_sl_en.tmx")
    return tm_dir


# ---------------------------------------------------------------------------
# A. _entries_by_pair surface
# ---------------------------------------------------------------------------


def test_entries_by_pair_keys(tm_dir_en_sl_and_hr_sl):
    """Loading both EN-SL (4 entries) and HR-SL (3 entries) populates
    ``_entries_by_pair`` with BOTH keys at their expected counts.

    Against current code: ``_entries_by_pair`` does not exist at all —
    AttributeError. (Even if it did exist, HR-SL would load 0 entries due to
    the audit §3.2 drop.)
    """
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_en_sl_and_hr_sl)

    assert hasattr(tm, "_entries_by_pair"), (
        "TranslationMemory must expose a `_entries_by_pair` dict per "
        "Phase 1B blueprint §2"
    )
    by_pair = tm._entries_by_pair

    assert ("en", "sl") in by_pair, (
        f"missing ('en', 'sl') bucket; got keys {list(by_pair)}"
    )
    assert ("hr", "sl") in by_pair, (
        f"missing ('hr', 'sl') bucket; got keys {list(by_pair)}"
    )
    assert len(by_pair[("en", "sl")]) == 4
    assert len(by_pair[("hr", "sl")]) == 3


def test_entries_by_pair_actual_codes(tm_dir_en_sl_and_hr_sl):
    """Every entry in the HR-SL bucket carries actual ``source_lang='hr'``
    and ``target_lang='sl'``. Today's loader writes the hardcoded ``en`` /
    ``sl`` instead.
    """
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_en_sl_and_hr_sl)

    hr_sl_entries = tm._entries_by_pair[("hr", "sl")]
    assert len(hr_sl_entries) == 3
    for entry in hr_sl_entries:
        assert entry["source_lang"] == "hr", (
            f"HR-SL bucket entry must carry source_lang='hr', got "
            f"{entry.get('source_lang')!r}"
        )
        assert entry["target_lang"] == "sl"


# ---------------------------------------------------------------------------
# B. tm.entries compat-shim — EN-SL only corpus is byte-identical
# ---------------------------------------------------------------------------


def test_tm_entries_compat_view_en_sl_only(tm_dir_en_sl_only):
    """For today's EN/SL-only corpus, ``tm.entries`` must continue to expose
    every entry as ``source_lang='en'`` / ``target_lang='sl'`` so the editor
    and inline test fixtures keep working unchanged (impact survey §1 / §6).

    This passes against the current implementation by accident (loader
    hardcodes EN/SL). It is included to lock the compat behaviour during
    the refactor — green phase must keep it green.
    """
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_en_sl_only)

    assert len(tm.entries) == 4
    assert [e["source_lang"] for e in tm.entries] == ["en"] * len(tm.entries)
    assert [e["target_lang"] for e in tm.entries] == ["sl"] * len(tm.entries)


# ---------------------------------------------------------------------------
# C. tm.entries excludes non-EN-SL pair buckets (HR-SL invisible)
# ---------------------------------------------------------------------------


def test_tm_entries_excludes_non_en_sl_pairs(tm_dir_en_sl_and_hr_sl):
    """``tm.entries`` is the EN/SL-only compat view. With both EN-SL (4) and
    HR-SL (3) loaded, ``tm.entries`` must equal the sum of the
    ``("en", "sl")`` and ``("sl", "en")`` buckets (here: 4 + 0 = 4). The
    HR-SL entries live in ``_entries_by_pair[("hr", "sl")]`` only — they
    must NOT bleed into ``tm.entries``.

    Today's loader either drops the HR-SL TUs (zero, audit §3.2) or — if
    they slip through — labels them as EN/SL and they appear in
    ``tm.entries`` with wrong codes. Either way today's behaviour does not
    match the blueprint's compat-view contract once ``_entries_by_pair`` is
    introduced.
    """
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_en_sl_and_hr_sl)

    by_pair = tm._entries_by_pair
    en_sl_count = len(by_pair.get(("en", "sl"), []))
    sl_en_count = len(by_pair.get(("sl", "en"), []))

    assert len(tm.entries) == en_sl_count + sl_en_count, (
        f"tm.entries must equal |('en','sl')| + |('sl','en')| = "
        f"{en_sl_count} + {sl_en_count}; got {len(tm.entries)}"
    )

    # HR-SL entries are invisible to the compat view.
    for entry in tm.entries:
        assert entry["source_lang"] != "hr", (
            f"HR-SL bucket must not leak into tm.entries; found "
            f"source_lang='hr' on {entry!r}"
        )


# ---------------------------------------------------------------------------
# D. raw_index across tm.entries remains contiguous
# ---------------------------------------------------------------------------


def test_tm_entries_raw_index_contiguous(tm_dir_en_sl_and_hr_sl):
    """The editor-side invariant from existing
    ``test_translation_memory_entries_remain_in_natural_load_order`` (Phase 1)
    must continue to hold when ``tm.entries`` is built from the compat shim:
    ``raw_index`` for the entries visible in ``tm.entries`` is a contiguous
    range starting at 0.

    Even though HR-SL entries get a global ``raw_index`` in
    ``_entries_by_pair``, the compat view must re-present its own slice as a
    contiguous 0..N-1 sequence (blueprint §3 invariant).
    """
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_en_sl_and_hr_sl)

    raw_indexes = [e["raw_index"] for e in tm.entries]
    assert raw_indexes == list(range(len(tm.entries))), (
        f"raw_index sequence on tm.entries must be 0..{len(tm.entries)-1} "
        f"contiguous; got {raw_indexes}"
    )


# ---------------------------------------------------------------------------
# E. iter_chronological sees ALL pair buckets (including HR-SL)
# ---------------------------------------------------------------------------


def test_iter_chronological_includes_all_pairs(tm_dir_en_sl_and_hr_sl):
    """Per blueprint §4: ``iter_chronological`` reads from ALL pair buckets
    in ``_entries_by_pair`` (not from the EN/SL-filtered ``tm.entries``).
    With both EN-SL (4) and HR-SL (3) loaded, it must yield 7 entries total,
    and at least one of them must carry ``source_lang='hr'``.

    Today's ``iter_chronological`` reads ``self.entries``, which is hardcoded
    to EN/SL — so even if HR-SL entries survived the loader, they'd carry
    the wrong code; today they don't survive at all.
    """
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_en_sl_and_hr_sl)

    chrono = list(tm.iter_chronological())
    assert len(chrono) == 7, (
        f"expected 4 EN-SL + 3 HR-SL = 7 entries from iter_chronological, "
        f"got {len(chrono)}"
    )

    hr_entries = [e for e in chrono if e.get("source_lang") == "hr"]
    assert len(hr_entries) >= 1, (
        "iter_chronological must yield entries from the HR-SL bucket with "
        "their actual source_lang='hr' code (blueprint §4)"
    )


# ---------------------------------------------------------------------------
# F. SL-EN entries get swapped into the EN-SL compat view
# ---------------------------------------------------------------------------


def test_sl_en_swapped_into_en_sl_view(tm_dir_sl_en_only):
    """Per blueprint §3 — the compat-shim swap mechanic: entries in the
    ``("sl", "en")`` bucket appear in ``tm.entries`` as shallow-copy dicts
    with ``source`` and ``target`` swapped, ``source_lang`` forced to ``en``,
    ``target_lang`` forced to ``sl``. The original entry in
    ``_entries_by_pair[("sl", "en")]`` is NOT mutated.

    Today's loader silently labels SL-source TMX entries as EN/SL at load
    time without any swap; the texts in ``e["source"]`` are whatever the
    loader's branching landed on. After the refactor, the swap is explicit
    and bucket-aware.
    """
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_sl_en_only)

    # Original SL-EN bucket: 2 entries, SL source.
    sl_en_bucket = tm._entries_by_pair[("sl", "en")]
    assert len(sl_en_bucket) == 2
    for entry in sl_en_bucket:
        assert entry["source_lang"] == "sl", (
            f"original ('sl', 'en') bucket entry must keep source_lang='sl'; "
            f"got {entry.get('source_lang')!r}"
        )
        assert entry["target_lang"] == "en"
        # Original text orientation: SL <seg> is the source side.
        assert entry["source"].startswith("slovenski izvor")
        assert entry["target"].startswith("english target")

    # Compat view: same 2 entries, swapped + relabelled to en/sl.
    assert len(tm.entries) == 2
    for entry in tm.entries:
        assert entry["source_lang"] == "en", (
            f"compat-view entry must report source_lang='en' after swap; "
            f"got {entry.get('source_lang')!r}"
        )
        assert entry["target_lang"] == "sl"
        # After the swap, the EN text is in `source` and the SL text in
        # `target` — matching today's editor expectation.
        assert entry["source"].startswith("english target")
        assert entry["target"].startswith("slovenski izvor")


# ---------------------------------------------------------------------------
# B. upsert_runtime_pair tests
# ---------------------------------------------------------------------------


def test_upsert_new_pair_visible_in_iter_chronological(tm_dir_en_sl_only):
    """Upserting a new runtime pair makes it visible in iter_chronological
    with the highest t_index (chronologically newest)."""
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_en_sl_only)

    existing_max_t = max(
        e.get("t_index", -1)
        for bucket in tm._entries_by_pair.values()
        for e in bucket
    )

    tm.upsert_runtime_pair("new source", "new target", "en", "sl")

    # Should appear in iter_chronological
    chron = list(tm.iter_chronological())
    new_entries = [e for e in chron if e.get("source") == "new source"]
    assert len(new_entries) == 1
    assert new_entries[0]["target"] == "new target"
    assert new_entries[0]["t_index"] == existing_max_t + 1


def test_upsert_existing_source_updates_target(tm_dir_en_sl_only):
    """Upserting the same source with a new target updates the existing
    entry — no duplicate in either collection."""
    from translate_core.tm import TranslationMemory

    tm = TranslationMemory(tm_dir=tm_dir_en_sl_only)

    # First upsert
    tm.upsert_runtime_pair("hello", "zdravo", "en", "sl")
    count_after_first = len(list(tm.iter_chronological()))

    # Second upsert with same source, different target
    tm.upsert_runtime_pair("hello", "pozdravljeni", "en", "sl")
    count_after_second = len(list(tm.iter_chronological()))

    # No duplicate — same count
    assert count_after_first == count_after_second

    # Target was updated
    chron = list(tm.iter_chronological())
    hello_entries = [e for e in chron if e.get("source") == "hello"]
    assert len(hello_entries) == 1
    assert hello_entries[0]["target"] == "pozdravljeni"

    # Also updated in self.entries
    entries_hello = [e for e in tm.entries if e.get("source") == "hello"]
    assert len(entries_hello) == 1
    assert entries_hello[0]["target"] == "pozdravljeni"
