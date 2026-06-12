# tests/test_pipeline_inference.py
#
# Tests for ui.state.infer_pipeline — explicit field wins, legacy inference
# from footnote-def segments, segment count thresholds, and simple default.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui.state import infer_pipeline


class TestInferPipeline:
    """Explicit pipeline field wins; legacy projects inferred from content."""

    def test_explicit_academic_wins(self):
        ws = {"pipeline": "academic", "segments": []}
        assert infer_pipeline(ws) == "academic"

    def test_explicit_simple_wins(self):
        ws = {"pipeline": "simple", "segments": []}
        assert infer_pipeline(ws) == "simple"

    def test_fn_def_segments_infer_academic(self):
        """Segments with footnote definitions → academic."""
        ws = {
            "segments": [
                {"source": "[^1]: A footnote definition."},
                {"source": "Regular paragraph."},
            ]
        }
        assert infer_pipeline(ws) == "academic"

    def test_large_count_infers_academic(self):
        """250 plain segments → academic (book-scale threshold)."""
        ws = {"segments": [{"source": f"Segment {i}."} for i in range(250)]}
        assert infer_pipeline(ws) == "academic"

    def test_small_count_infers_simple(self):
        """12 plain segments → simple."""
        ws = {"segments": [{"source": f"Segment {i}."} for i in range(12)]}
        assert infer_pipeline(ws) == "simple"

    def test_invalid_pipeline_field_falls_through(self):
        """An unrecognized pipeline value falls through to content-based inference."""
        ws = {"pipeline": "unknown", "segments": []}
        # Empty segments → simple
        assert infer_pipeline(ws) == "simple"

    def test_fn_def_among_few_segments_still_academic(self):
        """Even with < 200 segments, a footnote def forces academic."""
        ws = {
            "segments": [
                {"source": "[^5]: Some note."},
                {"source": "Body text."},
            ]
        }
        assert infer_pipeline(ws) == "academic"

    def test_no_segments_defaults_simple(self):
        ws = {}
        assert infer_pipeline(ws) == "simple"

    def test_empty_segments_defaults_simple(self):
        ws = {"segments": []}
        assert infer_pipeline(ws) == "simple"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])