# tests/test_style_rules.py
#
# Tests for translate_core/style_rules — citation hints, orthography hints,
# footnote integrity, emphasis integrity, and check_segment integration.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.style_rules import (
    CITATION_HINTS,
    ORTHO_HINTS,
    citation_hints,
    emphasis_integrity,
    footnote_integrity,
    orthography_hints,
)


# ======================================================================
# Citation hints — Slovene target
# ======================================================================


class TestCitationHintsSL:
    """SL target: each convention pattern triggers its own hint."""

    def test_p_dot_triggers_page_hint(self):
        results = citation_hints("Glej tudi p. 12 za več.", "sl")
        assert any("str." in r["message"] for r in results)

    def test_pp_dot_triggers_page_hint(self):
        results = citation_hints("Glej pp. 12–15.", "sl")
        assert any("str." in r["message"] for r in results)

    def test_ed_triggers_editor_hint(self):
        results = citation_hints("V ed. Smith, str. 5.", "sl")
        assert any("ur." in r["message"] for r in results)

    def test_trans_triggers_prev_hint(self):
        results = citation_hints("V trans. Smith, str. 5.", "sl")
        assert any("prev." in r["message"] for r in results)

    def test_ibid_triggers_prav_tam_hint(self):
        results = citation_hints("Ibid., 23.", "sl")
        assert any("Prav tam" in r["message"] for r in results)

    def test_ibid_lowercase_triggers_hint(self):
        results = citation_hints("ibid. 23.", "sl")
        assert any("Prav tam" in r["message"] for r in results)

    def test_et_al_triggers_idr_hint(self):
        results = citation_hints("Smith et al. pravijo", "sl")
        assert any("idr." in r["message"] for r in results)

    def test_english_quotes_triggers_slovene_quote_hint(self):
        results = citation_hints('To je "citat" v besedilu.', "sl")
        assert any("»…«" in r["message"] for r in results)

    def test_smart_quotes_triggers_slovene_quote_hint(self):
        results = citation_hints("\u201cCitat\u201d v besedilu.", "sl")
        assert any("»…«" in r["message"] for r in results)

    def test_clean_sl_target_yields_no_citation_hints(self):
        """str. 12 is the correct Slovene convention — no citation violation hints."""
        results = citation_hints("Glej str. 12 za več.", "sl")
        # "str." should NOT match the English page-ref pattern \bpp?\.\s*\d
        cit_hints = [r for r in results if "Citation" in r["message"]]
        assert len(cit_hints) == 0

    def test_no_hints_for_unknown_lang(self):
        results = citation_hints("Ibid., p. 12", "de")
        assert results == []


# ======================================================================
# Citation hints — English target (reverse rules)
# ======================================================================


class TestCitationHintsEN:
    """EN target: Slovene conventions trigger English-equivalent hints."""

    def test_guillemets_trigger_english_quote_hint(self):
        results = citation_hints("See »citat« in text.", "en")
        assert any("English quotes" in r["message"] for r in results)

    def test_str_dot_triggers_p_dot_hint(self):
        results = citation_hints("Glej str. 12.", "en")
        assert any("p." in r["message"] or "pp." in r["message"] for r in results)

    def test_ur_dot_triggers_ed_hint(self):
        results = citation_hints("V ur. Smith.", "en")
        assert any("ed." in r["message"] for r in results)

    def test_prev_dot_triggers_trans_hint(self):
        results = citation_hints("V prev. Smith.", "en")
        assert any("trans." in r["message"] for r in results)

    def test_prav_tam_triggers_ibid_hint(self):
        results = citation_hints("Prav tam, 23.", "en")
        assert any("Ibid" in r["message"] for r in results)


# ======================================================================
# Orthography hints
# ======================================================================


class TestOrthographyHints:
    def test_sl_english_quotes_hint(self):
        results = orthography_hints('Besedilo "citat" tukaj.', "sl")
        assert len(results) >= 1
        assert any("»…«" in r["message"] for r in results)

    def test_sl_range_dash_hint(self):
        results = orthography_hints("Strani 10-15.", "sl")
        assert any("en-dash" in r["message"] or "–" in r["message"] for r in results)

    def test_en_guillemets_hint(self):
        results = orthography_hints("See »citat« here.", "en")
        assert any("English quotes" in r["message"] for r in results)


# ======================================================================
# Footnote integrity
# ======================================================================


class TestFootnoteIntegrity:
    def test_dropped_prefix_error(self):
        """Dropping [^1]: prefix in target → error."""
        source = "[^1]: This is a footnote definition."
        target = "This is a footnote definition."
        results = footnote_integrity(source, target)
        assert len(results) == 1
        assert results[0]["type"] == "error"
        assert "missing in target" in results[0]["message"]

    def test_ref_count_mismatch_warning(self):
        """Different number of inline refs → warning."""
        source = "See [^1] and [^2] for details."
        target = "Glej [^1] za podrobnosti."
        results = footnote_integrity(source, target)
        assert len(results) >= 1
        assert any(r["type"] == "warning" and "mismatch" in r["message"] for r in results)

    def test_clean_footnote_no_warnings(self):
        """Matching footnote def with proper target → no warnings."""
        source = "[^1]: Definition text."
        target = "[^1]: Besedilo definicije."
        results = footnote_integrity(source, target)
        assert results == []

    def test_clean_body_no_refs_no_warnings(self):
        """Body text with no footnote refs → no warnings."""
        results = footnote_integrity("Plain text.", "Navadno besedilo.")
        assert results == []

    def test_matching_ref_counts_no_warning(self):
        """Same number of refs in source and target → no mismatch warning."""
        source = "See [^1] here."
        target = "Glej [^1] tukaj."
        results = footnote_integrity(source, target)
        assert not any("mismatch" in r["message"] for r in results)


# ======================================================================
# Emphasis integrity
# ======================================================================


class TestEmphasisIntegrity:
    def test_missing_italic_warning(self):
        """Source has *italic* but target does not → warning."""
        source = "This is *important* text."
        target = "To je pomembno besedilo."
        results = emphasis_integrity(source, target)
        assert len(results) >= 1
        assert any("count differs" in r["message"] for r in results)

    def test_balanced_target_no_warning(self):
        """Both source and target have one *italic* span → clean."""
        source = "This is *important* text."
        target = "To je *pomembno* besedilo."
        results = emphasis_integrity(source, target)
        count_warnings = [r for r in results if "count differs" in r["message"]]
        assert count_warnings == []

    def test_odd_star_count_warning(self):
        """Odd number of * characters with emphasis spans present → unbalanced warning."""
        source = "This has *italic* text."
        target = "To je *pomembno besedilo."  # Single bare * — odd count, with emphasis in source
        results = emphasis_integrity(source, target)
        assert any("Unbalanced" in r["message"] for r in results)

    def test_no_markers_no_warnings(self):
        """No emphasis in source or target → no warnings."""
        results = emphasis_integrity("Plain text.", "Navadno besedilo.")
        assert results == []

    def test_bold_and_italic_mismatch(self):
        """Source has **bold** and *italic*, target only has **bold**."""
        source = "This is **bold** and *italic*."
        target = "To je **krepko** in navadno."
        results = emphasis_integrity(source, target)
        assert any("count differs" in r["message"] for r in results)


# ======================================================================
# check_segment integration
# ======================================================================


class TestCheckSegmentIntegration:
    """Test that QAEngine.check_segment includes citation hints
    for academic pipeline on footnote defs, but not for simple pipeline."""

    def test_academic_footnote_gets_citation_hints(self):
        from translate_core.qa import QAEngine

        engine = QAEngine()
        source = "[^1]: See note."
        target = '[^1]: Ibid., p. 12 in "Quoted text".'
        warnings = engine.check_segment(source, target, pipeline="academic")
        # Should have citation hints: Ibid → Prav tam, p. → str., "…" → »…«
        cit_warnings = [w for w in warnings if "Citation" in w.get("message", "")]
        assert len(cit_warnings) >= 1

    def test_simple_pipeline_no_citation_hints_on_footnote(self):
        from translate_core.qa import QAEngine

        engine = QAEngine()
        source = "[^1]: See note."
        target = '[^1]: Ibid., p. 12 in "Quoted text".'
        warnings = engine.check_segment(source, target, pipeline="simple")
        # No citation hints in simple pipeline, even on footnote defs
        cit_warnings = [w for w in warnings if "Citation" in w.get("message", "")]
        assert len(cit_warnings) == 0

    def test_academic_footnote_dropped_prefix_error(self):
        from translate_core.qa import QAEngine

        engine = QAEngine()
        source = "[^1]: Definition."
        target = "Definicija brez oznake."
        warnings = engine.check_segment(source, target, pipeline="academic")
        assert any(w["type"] == "error" and "missing in target" in w["message"] for w in warnings)

    def test_orthography_hints_always_present(self):
        from translate_core.qa import QAEngine

        engine = QAEngine()
        source = "Plain text."
        target = 'Besedilo z "narekovaji".'
        # Orthography hints fire regardless of pipeline
        for pipeline in ("academic", "simple"):
            warnings = engine.check_segment(source, target, pipeline=pipeline)
            ortho = [w for w in warnings if "»…«" in w.get("message", "")]
            assert len(ortho) >= 1, f"No orthography hints for pipeline={pipeline}"


    def test_no_false_positive_on_markdown_bullet(self):
        """A markdown list bullet '* text' must not trigger unbalanced emphasis warning."""
        warnings = emphasis_integrity("Some source text", "* This is a bullet point")
        unbalanced = [w for w in warnings if "nbalanced" in w.get("message", "")]
        assert len(unbalanced) == 0, \
            f"False positive: markdown bullet triggered emphasis warning: {unbalanced}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])