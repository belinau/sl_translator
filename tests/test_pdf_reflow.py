# tests/test_pdf_reflow.py
#
# DocumentParser._reflow_pdf_text — sentence-preserving reflow of PDF
# extraction output. The contract: a translator never receives a
# mid-sentence fragment, and list-like regions (title page, TOC,
# bibliography headers) keep one row per entry.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core.doc_parser import DocumentParser


def _reflow(text: str) -> str:
    return DocumentParser()._reflow_pdf_text(text)


class TestSentenceRejoining:
    def test_blank_separated_wrap_rejoined(self):
        """PDF wraps with blank lines between lines of one sentence."""
        text = (
            "This is a long opening line of prose that wraps because the page is narrow and\n"
            "\n"
            "continues on the next visual line with the rest of the sentence.\n"
        )
        out = _reflow(text)
        assert "narrow and continues" in out

    def test_hyphen_split_repaired(self):
        text = (
            "The author discusses the relationship between the medi-\n"
            "\n"
            "cal profession and disability studies in considerable depth.\n"
        )
        out = _reflow(text)
        assert "medical profession" in out

    def test_sentence_boundary_kept(self):
        """A terminal line followed by an uppercase start stays separate."""
        text = (
            "The first sentence of the paragraph ends here with a proper full stop.\n"
            "\n"
            "Another sentence begins with a capital letter and stands alone fine.\n"
        )
        out = _reflow(text)
        assert out.count("\n\n") == 1  # two units


class TestListRegionsStaySplit:
    def test_short_title_rows_not_joined(self):
        """Title-page / TOC rows are short — each stays its own unit."""
        text = "Contents\n\nAcknowledgments\n\nAlison Kafer\n\nindiana university press\n"
        out = _reflow(text)
        units = out.split("\n\n")
        assert "Contents" in units
        assert "Acknowledgments" in units

    def test_locator_tail_rows_not_joined(self):
        """TOC/index rows ending in a page locator are self-terminated."""
        text = "Acknowledgments ix\n\nIntroduction: Imagined Futures 1\n"
        out = _reflow(text)
        units = out.split("\n\n")
        assert units[0] == "Acknowledgments ix"

    def test_footnote_def_rows_stay_structural(self):
        text = (
            "1. First note citation (New York: Some Press, 2010), 12.\n"
            "2. Second note.\n"
        )
        out = _reflow(text)
        assert "1. First note" in out.split("\n\n")[0]
        assert "2. Second note." in out.split("\n\n")[1]


class TestPageArtifactsDropped:
    def test_page_numbers_and_running_headers_dropped(self):
        text = (
            "Prose line one of the body that is comfortably long and wraps to the\n"
            "\n"
            "179\n"
            "\n"
            "180    |    Notes to Pages 4-7\n"
            "\n"
            "next page where the sentence finally completes itself properly.\n"
        )
        parser = DocumentParser()
        out = parser._reflow_pdf_text(text)
        assert "179" not in out
        assert "Notes to Pages" not in out
        assert "wraps to the next page" in out
        assert parser.last_reflow_report is not None
        assert parser.last_reflow_report["dropped_page_artifacts"] == 2
