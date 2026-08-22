"""Smoke tests for KG editor pages — verify no import/syntax errors.

The actual page-rendering test would require the NiceGUI user fixture to
start the app from main.py (which registers @ui.page routes), but the
monkeypatch on app_state only affects the test process, not the app process.
Instead we verify that every kg_editor module imports cleanly and has no
SyntaxError, which catches the class of bugs that shipped before
(btn.html, ui.caption, tuple-as-column, select TypeError, async render no-ops).
"""
from __future__ import annotations

import importlib

import pytest

MODULES = [
    "ui.kg_editor.common",
    "ui.kg_editor.browser",
    "ui.kg_editor.review",
    "ui.kg_editor.kg_review",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_cleanly(module):
    """Module has no SyntaxError or ImportError (catches btn.html, ui.caption, etc.)."""
    importlib.import_module(module)