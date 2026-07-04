# tests/test_maska_footnote_convert.py
#
# Tests for translate_core/maska_footnote_convert.py — mechanical Maska
# citation conversion (no LLM, no translation).

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.maska_footnote_convert import convert_footnote_to_maska


class TestQuoteConversion:
    """English quotes → Slovenian »...« with comma moved outside."""

    def test_straight_quotes_comma_inside(self):
        src = 'Author, "Article Title," Journal, 2007, 5.'
        tgt = convert_footnote_to_maska(src)
        assert "»Article Title«," in tgt
        assert '"Article' not in tgt

    def test_straight_quotes_no_comma(self):
        src = 'Author, "Article Title" Journal, 2007, 5.'
        tgt = convert_footnote_to_maska(src)
        assert "»Article Title«" in tgt

    def test_smart_quotes_comma_inside(self):
        src = "Author, \u201cArticle Title,\u201d Journal, 2007, 5."
        tgt = convert_footnote_to_maska(src)
        assert "»Article Title«," in tgt

    def test_smart_quotes_no_comma(self):
        src = "Author, \u201cArticle Title\u201d Journal, 2007, 5."
        tgt = convert_footnote_to_maska(src)
        assert "»Article Title«" in tgt

    def test_multiple_quotes(self):
        src = '"First," and "Second," in Book.'
        tgt = convert_footnote_to_maska(src)
        assert "»First«," in tgt
        assert "»Second«," in tgt

    def test_exclamation_in_quotes(self):
        src = '"Calling All Revolutionaries!" in Book.'
        tgt = convert_footnote_to_maska(src)
        assert "»Calling All Revolutionaries!«" in tgt


class TestChapterMarker:
    """'in *Title*' → 'v: *Title*' — only when italic book title follows."""

    def test_in_followed_by_italic_converts(self):
        src = 'Author, "Article," in *Book Title*, ed. Editor, 2009, 5.'
        tgt = convert_footnote_to_maska(src)
        assert "v: *Book Title*" in tgt

    def test_in_without_italic_not_converted(self):
        src = "Discussed in Chapter Five of the book."
        tgt = convert_footnote_to_maska(src)
        assert "v:" not in tgt

    def test_in_inside_title_not_converted(self):
        src = "*Disability in Public* (New York: Publisher, 2009), 5."
        tgt = convert_footnote_to_maska(src)
        # "in" inside the italic title should NOT become "v:"
        assert "v:" not in tgt


class TestEditorTranslator:
    """ed./eds. → ur., trans. → prev."""

    def test_ed_singular(self):
        assert "ur." in convert_footnote_to_maska("ed. Lennard J. Davis")

    def test_eds_plural(self):
        assert "ur." in convert_footnote_to_maska("eds. Smith and Jones")

    def test_trans(self):
        assert "prev." in convert_footnote_to_maska("trans. Božidar Debenjak")


class TestPageRef:
    """p./pp. → str."""

    def test_p_singular(self):
        assert "str." in convert_footnote_to_maska("p. 95.")

    def test_pp_plural(self):
        assert "str." in convert_footnote_to_maska("pp. 95-103.")


class TestIbid:
    """Ibid. → *Ibid*."""

    def test_ibid_with_period(self):
        tgt = convert_footnote_to_maska("Ibid., 58.")
        assert "*Ibid*." in tgt

    def test_ibid_without_period(self):
        tgt = convert_footnote_to_maska("Ibid, 58.")
        assert "*Ibid*." in tgt


class TestDateFormat:
    """Month DD, YYYY → DD. MM. YYYY."""

    def test_october(self):
        src = "October 24, 2007"
        tgt = convert_footnote_to_maska(src)
        assert tgt == "24. 10. 2007"

    def test_january(self):
        src = "January 5, 1999"
        tgt = convert_footnote_to_maska(src)
        assert tgt == "5. 1. 1999"

    def test_no_comma(self):
        src = "March 15 2004"
        tgt = convert_footnote_to_maska(src)
        assert tgt == "15. 3. 2004"


class TestPageRange:
    """Hyphen between digits → en-dash."""

    def test_range(self):
        tgt = convert_footnote_to_maska("275-82")
        assert "\u2013" in tgt
        assert "-" not in tgt

    def test_no_range_in_text(self):
        """Non-numeric hyphen not converted."""
        tgt = convert_footnote_to_maska("well-known")
        assert "\u2013" not in tgt


class TestItalicPreserved:
    """*...* italic markers are preserved unchanged."""

    def test_italic_passes_through(self):
        src = "Author, *Book Title*, 2009, 5."
        tgt = convert_footnote_to_maska(src)
        assert "*Book Title*" in tgt


class TestFullFootnote:
    """Full footnote conversion matches expected Maska output."""

    def test_kafer_fn1(self):
        """Kafer footnote [^1] matches user's manual conversion."""
        src = 'Michael Gerson, "The Eugenics Temptation," *Washington Post,* October 24, 2007, A19.'
        tgt = convert_footnote_to_maska(src)
        expected = 'Michael Gerson, »The Eugenics Temptation«, *Washington Post,* 24. 10. 2007, A19.'
        assert tgt == expected

    def test_chapter_in_collection(self):
        """Chapter-in-collection with ed. → v: and ur."""
        src = 'Author, "Article," in *Book*, ed. Editor (City: Publisher, 2009), 93-103.'
        tgt = convert_footnote_to_maska(src)
        assert "v: *Book*" in tgt
        assert "ur. Editor" in tgt
        assert "93\u2013103" in tgt

    def test_no_change_when_already_maska(self):
        """Footnote already in Maska format is unchanged."""
        src = "Author, *Book Title*, str. 5."
        tgt = convert_footnote_to_maska(src)
        assert tgt == src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])