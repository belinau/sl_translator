# translate_core/style_rules.py
"""QA-hint rulesets for citation conventions, orthography, footnote
integrity, and emphasis integrity.

All rules are **hints only** — the editor never auto-rewrites translated text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class StyleHint:
    """A single style rule: pattern to detect, message to display, severity."""

    pattern: re.Pattern
    message: str
    severity: str = "warning"


# ---------------------------------------------------------------------------
# Citation-convention hints — keyed by TARGET language
# ---------------------------------------------------------------------------
CITATION_HINTS: dict[str, list[StyleHint]] = {
    "sl": [
        StyleHint(
            re.compile(r'["\u201c][^"\u201c\u201d\n]+["\u201d]'),
            'Citation: use Slovene quotes »…« instead of English quotes',
        ),
        StyleHint(
            re.compile(r"\bpp?\.\s*\d"),
            'Citation: use "str." for page references',
        ),
        StyleHint(
            re.compile(r"\beds?\."),
            'Citation: use "ur." (urednik)',
        ),
        StyleHint(
            re.compile(r"\btrans\."),
            'Citation: use "prev." (prevedel/-la)',
        ),
        StyleHint(
            re.compile(r"\b[Ii]bid\.?"),
            'Citation: use "Prav tam"',
        ),
        StyleHint(
            re.compile(r"\bet al\."),
            'Citation: consider "idr."',
        ),
    ],
    "en": [
        StyleHint(
            re.compile(r'[\u00bb\u00ab]'),
            'Citation: use English quotes \u201c\u2026\u201d',
        ),
        StyleHint(
            re.compile(r"\bstr\.\s*\d"),
            'Citation: use "p."/"pp."',
        ),
        StyleHint(
            re.compile(r"\bur\."),
            'Citation: use "ed."',
        ),
        StyleHint(
            re.compile(r"\bprev\."),
            'Citation: use "trans."',
        ),
        StyleHint(
            re.compile(r"\b[Pp]rav tam\b"),
            'Citation: use "Ibid."',
        ),
    ],
}


# ---------------------------------------------------------------------------
# Orthography hints — keyed by TARGET language
# ---------------------------------------------------------------------------
ORTHO_HINTS: dict[str, list[StyleHint]] = {
    "sl": [
        StyleHint(
            re.compile(r"(?<=\d)\s*-\s*(?=\d)"),
            'Use en-dash (\u2013) for ranges',
        ),
    ],
    "en": [],
}


# ---------------------------------------------------------------------------
# Public hint-application functions
# ---------------------------------------------------------------------------

def citation_hints(target: str, tgt_lang: str) -> list[dict]:
    """Return citation-convention hints for *target* text written in *tgt_lang*."""
    hints: list[dict] = []
    for sh in CITATION_HINTS.get(tgt_lang, []):
        if sh.pattern.search(target):
            hints.append({"type": sh.severity, "message": sh.message})
    return hints


def orthography_hints(target: str, tgt_lang: str) -> list[dict]:
    """Return orthography hints for *target* text written in *tgt_lang*."""
    hints: list[dict] = []
    for sh in ORTHO_HINTS.get(tgt_lang, []):
        if sh.pattern.search(target):
            hints.append({"type": sh.severity, "message": sh.message})
    return hints


# ---------------------------------------------------------------------------
# Integrity checks (pipeline-agnostic)
# ---------------------------------------------------------------------------

# Duplicate the emphasis regex from doc_parser to keep this module import-free.
_MD_EMPHASIS_RE = re.compile(
    r"(\*\*\*[^*\n]+\*\*\*|\*\*[^*\n]+\*\*|\*[^*\n]+\*)"
)

_FOOTNOTE_DEF_RE = re.compile(r"^\[\^(\w+)\]:", re.MULTILINE)
_FOOTNOTE_REF_RE = re.compile(r"\[\^(\w+)\]")


def footnote_integrity(source: str, target: str) -> list[dict]:
    """Check footnote marker integrity between source and target.

    - Error if a footnote-definition source has a non-footnote target.
    - Warning if inline footnote-ref counts differ.
    """
    warnings: list[dict] = []
    is_def = source.lstrip().startswith("[^")

    if is_def and target.strip() and not target.lstrip().startswith("[^"):
        warnings.append({
            "type": "error",
            "message": (
                'Footnote marker "[^N]:" missing in target '
                "\u2014 export will lose this footnote"
            ),
        })

    # Count inline refs only in non-definition segments
    if not is_def:
        src_refs = len(_FOOTNOTE_REF_RE.findall(source))
        tgt_refs = len(_FOOTNOTE_REF_RE.findall(target))
        if src_refs != tgt_refs and src_refs > 0:
            warnings.append({
                "type": "warning",
                "message": (
                    f"Footnote reference count mismatch "
                    f"(source {src_refs}, target {tgt_refs})"
                ),
            })

    return warnings


def emphasis_integrity(source: str, target: str) -> list[dict]:
    """Check that emphasis markup (*…*, **…**, ***…***) is preserved in target."""
    warnings: list[dict] = []

    src_spans = _MD_EMPHASIS_RE.findall(source)
    tgt_spans = _MD_EMPHASIS_RE.findall(target)

    if src_spans and len(src_spans) != len(tgt_spans):
        warnings.append({
            "type": "warning",
            "message": (
                f"Italic/bold markup (*\u2026*) count differs "
                f"(source {len(src_spans)}, target {len(tgt_spans)}) "
                "\u2014 styling will be lost in export"
            ),
        })

    # Odd number of bare asterisks means unbalanced markup
    bare_star_count = target.count("*") - target.count("**") * 2
    if (src_spans or tgt_spans) and bare_star_count % 2 != 0:
        warnings.append({
            "type": "warning",
            "message": "Unbalanced * marker \u2014 emphasis will not render",
        })

    return warnings