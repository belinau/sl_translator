# translate_core/publisher_styles.py
#
# User-managed publisher typography profiles.
#
# Profiles are JSON data in data/publisher_styles.json — the user creates,
# edits, and deletes them via the UI. They survive restarts. The Maska
# profile is seeded on first run so the dropdown is never empty and the
# Kafer project works out of the box.
#
# The paragraph-break manifest (pdf_format_capture) is publisher-independent;
# only the typography (fonts, sizes, margins) is publisher-specific.

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import config

log = logging.getLogger(__name__)

STYLES_PATH = Path("data/publisher_styles.json")

# The seed profile — the Maska house style. Applied on first run when the
# styles file doesn't exist. The user can edit it but not delete it.
_SEED_PROFILE = {
    "label": "Maska (TNR 12pt, 1.5)",
    "body_font": "Times New Roman",
    "body_size_pt": 12,
    "line_spacing": 1.5,
    "page_size": "A4",
    "margins_in": 1.0,
    "space_after_pt": 0,
    "para_first_line_indent_in": 0.0,
    "footnote_size_pt": 10,
    "footnote_line_spacing": 1.0,
    "blockquote_size_pt": 11,
    "blockquote_line_spacing": 1.0,
    "blockquote_indent_in": 0.5,
    "h1_size_pt": 18,
    "h2_size_pt": 13,
}


def load_styles() -> dict[str, dict[str, Any]]:
    """Load all publisher profiles from data/publisher_styles.json.

    Seeds the Maska profile on first run (file missing). Returns a dict
    keyed by style slug, each value a profile dict.
    """
    if not STYLES_PATH.exists():
        styles = {"maska": dict(_SEED_PROFILE)}
        save_styles(styles)
        return styles
    try:
        styles = json.loads(STYLES_PATH.read_text(encoding="utf-8"))
    except Exception as ex:
        log.warning("publisher_styles: load failed (%s) — reseeding", ex)
        styles = {"maska": dict(_SEED_PROFILE)}
        save_styles(styles)
    # Always ensure the Maska seed exists (can't be deleted by the user)
    if "maska" not in styles:
        styles["maska"] = dict(_SEED_PROFILE)
        save_styles(styles)
    return styles


def save_styles(styles: dict[str, dict[str, Any]]) -> None:
    """Atomic write (temp file + rename) of all profiles."""
    STYLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STYLES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(styles, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STYLES_PATH)


def resolve_typography(style_key: str | None) -> dict[str, Any]:
    """Return the profile dict for a style key, falling back to Maska.

    Never raises — a missing/unknown key yields the default profile so
    export never breaks on a stale project JSON.
    """
    styles = load_styles()
    if style_key and style_key in styles:
        return styles[style_key]
    return styles.get(config.DEFAULT_HOUSE_STYLE, dict(_SEED_PROFILE))


def get_style_label(key: str) -> str:
    """Return the human-readable label for a style key."""
    styles = load_styles()
    prof = styles.get(key)
    if prof:
        return prof.get("label", key)
    return key


def get_style_options() -> dict[str, str]:
    """Return {slug: label} for all profiles, for UI dropdowns."""
    styles = load_styles()
    return {key: prof.get("label", key) for key, prof in styles.items()}


__all__ = [
    "load_styles",
    "save_styles",
    "resolve_typography",
    "get_style_label",
    "get_style_options",
]