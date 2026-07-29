"""Smoke test: comments_panel imports and exposes the expected API."""
from __future__ import annotations


def test_comments_panel_imports():
    import importlib
    importlib.import_module("ui.comments_panel")


def test_comments_panel_exposes_build():
    from ui import comments_panel
    assert hasattr(comments_panel, "build")
    assert hasattr(comments_panel, "_render_entry")
    assert hasattr(comments_panel, "_render_input")


def test_comments_panel_renders_history_logic():
    """Verify the helper functions work with a real segment dict."""
    from translate_core import comments as cm
    seg = {"id": 0, "source": "", "target": "", "status": "pending"}
    cm.add_comment(seg, "translator", 0, "Hello")
    assert len(cm.ensure_comments(seg)) == 1
    # build() returns a handle with rebuild; we can't run NiceGUI without
    # a client, so just assert the module exposes the expected API.
    from ui import comments_panel
    assert callable(comments_panel.build)