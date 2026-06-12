"""Tests for the segment editor prev/next context strips."""
from __future__ import annotations

import pytest
from nicegui import ui

from ui import segment_editor, settings as ui_settings
from ui.state import WorkspaceState


def _make_state(segments: list[dict], active_index: int = 0) -> WorkspaceState:
    """Build a WorkspaceState with stub dependencies."""
    ws_dict = {
        "project_id": "test123",
        "filename": "test.pdf",
        "lang_pair": "en->sl",
        "active_index": active_index,
        "saved_at": "2026-01-01T00:00:00",
        "total": len(segments),
        "done": sum(1 for s in segments if s["status"] == "done"),
        "segments": segments,
    }
    state = WorkspaceState(ws_dict, save_callback=lambda w: None)
    return state


def _stub_deps(state: WorkspaceState) -> dict:
    """Minimal deps dict for segment_editor.build."""
    from translate_core.knowledge_graph import KnowledgeGraph
    from translate_core.tm import TranslationMemory
    from translate_core.glossary import Glossary

    kg = KnowledgeGraph()
    tm = TranslationMemory()
    glossary = Glossary()

    return {
        "tm": tm,
        "glossary": glossary,
        "kg": kg,
        "qa_engine": None,
        "parse_lang_pair": lambda p: ("en", "sl"),
    }


class TestContextStrips:
    """Test prev/next strip visibility and text content."""

    def test_strips_at_first_index(self, user):
        """At index 0: prev strip hidden, next strip visible."""
        segments = [
            {"id": 0, "source": "First segment text here.", "target": "", "status": "pending"},
            {"id": 1, "source": "Second segment text here.", "target": "Prevod dva.", "status": "done"},
            {"id": 2, "source": "Third segment text here.", "target": "", "status": "pending"},
        ]
        state = _make_state(segments, active_index=0)
        deps = _stub_deps(state)

        @ui.page("/test_strips_first")
        def test_page():
            refs = segment_editor.build(state, deps, on_confirm=lambda: None)
            # Check prev strip hidden at index 0
            assert not refs["prev_strip"].visible
            # Next strip should be visible and show the next segment's source
            assert refs["next_strip"].visible
            assert "Second" in refs["next_label"].text or "Prevod" in refs["next_label"].text

        user.open("/test_strips_first")

    def test_strips_at_last_index(self, user):
        """At last index: next strip hidden, prev strip visible."""
        segments = [
            {"id": 0, "source": "First segment text here.", "target": "Prevod ena.", "status": "done"},
            {"id": 1, "source": "Second segment text here.", "target": "", "status": "pending"},
            {"id": 2, "source": "Third segment text here.", "target": "", "status": "pending"},
        ]
        state = _make_state(segments, active_index=2)
        deps = _stub_deps(state)

        @ui.page("/test_strips_last")
        def test_page():
            refs = segment_editor.build(state, deps, on_confirm=lambda: None)
            assert refs["prev_strip"].visible
            assert not refs["next_strip"].visible

        user.open("/test_strips_last")

    def test_strips_at_middle_index(self, user):
        """At middle index: both strips visible."""
        segments = [
            {"id": 0, "source": "First segment.", "target": "Prevod ena.", "status": "done"},
            {"id": 1, "source": "Second segment.", "target": "", "status": "pending"},
            {"id": 2, "source": "Third segment.", "target": "Prevod tri.", "status": "done"},
        ]
        state = _make_state(segments, active_index=1)
        deps = _stub_deps(state)

        @ui.page("/test_strips_middle")
        def test_page():
            refs = segment_editor.build(state, deps, on_confirm=lambda: None)
            assert refs["prev_strip"].visible
            assert refs["prev_caption"].text.startswith("#")
            assert refs["next_caption"].text.startswith("#")

        user.open("/test_strips_middle")

    def test_prev_strip_shows_target_when_available(self, user):
        """Prev strip prefers target text over source."""
        segments = [
            {"id": 0, "source": "Source text one.", "target": "Prevod ena.", "status": "done"},
            {"id": 1, "source": "Source text two.", "target": "", "status": "pending"},
        ]
        state = _make_state(segments, active_index=1)
        deps = _stub_deps(state)

        @ui.page("/test_strips_target")
        def test_page():
            refs = segment_editor.build(state, deps, on_confirm=lambda: None)
            # Prev strip should show target (which is available)
            assert "Prevod ena." in refs["prev_label"].text

        user.open("/test_strips_target")

    def test_empty_segments_both_strips_hidden(self, user):
        """When segments is empty, both strips are hidden."""
        state = _make_state([], active_index=0)
        deps = _stub_deps(state)

        @ui.page("/test_strips_empty")
        def test_page():
            refs = segment_editor.build(state, deps, on_confirm=lambda: None)
            assert not refs["prev_strip"].visible
            assert not refs["next_strip"].visible

        user.open("/test_strips_empty")