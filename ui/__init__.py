"""Zen Translator UI package.

Importing this package registers the @ui.page("/translate/{project_id}")
decorator (declared in workspace.py). Every submodule is exposed here so
relative imports inside the package resolve cleanly for type checkers.
"""
from . import (  # noqa: F401
    components,
    intel_panel,
    predictions,
    segment_editor,
    segment_navigator,
    settings,
    state,
    workspace,
)

__all__ = [
    "components",
    "intel_panel",
    "predictions",
    "segment_editor",
    "segment_navigator",
    "settings",
    "state",
    "workspace",
]

