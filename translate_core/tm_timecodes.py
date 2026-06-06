"""Read TMX files with TU-level creationdate timecodes.

This module exists because ``translate.storage.tmx.tmxfile`` (used by
``translate_core.tm._load_tmx``) silently discards the ``creationdate``
attribute on ``<tu>`` elements. Phase 1 of the parsing-simplification
plan needs that timestamp to expose a chronological view of the
translation memory while keeping ``self.entries`` in natural file order
for editor-side stability.

The public entry point is :func:`read_tmx_with_timecodes`. It parses
the TMX with ``lxml.etree`` directly, applies the same EN-source/
SL-target normalization and ``clean_xml`` text scrubbing that
``tm.py`` performs today, and returns enriched dicts with three new
keys: ``raw_index``, ``creationdate``, and ``t_index``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from lxml import etree

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _norm_lang(value: Optional[str]) -> str:
    """Strip locale suffix and lowercase. ``EN-GB`` -> ``en``."""
    if not value:
        return ""
    return value.lower().split("-")[0]


def _read_srclang(root: etree._Element) -> str:
    """Return the TMX header's ``srclang`` attribute (normalized) or ``en``."""
    header = root.find("header")
    if header is None:
        # Some TMX serializations namespace-qualify; try a broad search.
        header = root.find(".//header")
    if header is None:
        return "en"
    return _norm_lang(header.get("srclang")) or "en"


def _seg_text(tuv: etree._Element) -> str:
    """Return cleaned text from a ``<tuv>``'s ``<seg>`` child.

    The clean_xml helper lives in ``translate_core.tm``. We import it
    lazily inside this function to avoid a circular import at module
    load time (``tm.py`` imports ``tm_timecodes`` at the top level).
    """
    from translate_core.tm import clean_xml  # local import to break cycle

    seg = tuv.find("seg")
    if seg is None:
        return ""
    # ``etree.tostring`` keeps inline markup; serialize and let clean_xml strip it.
    # method="text" would lose tail handling for nested tags inside <seg>; we want
    # to mirror translate-toolkit's behavior, which returns the inner text. Using
    # itertext() concatenates child text reliably.
    raw = "".join(seg.itertext())
    return clean_xml(raw)


def read_tmx_with_timecodes(path: Path) -> List[Dict[str, Any]]:
    """Parse a TMX file with lxml and return entries in natural file order.

    Each returned dict carries:
        source, target, origin, source_lang, target_lang,
        raw_index, creationdate, t_index

    - ``raw_index`` is the position in the returned list (file order).
    - ``creationdate`` is the raw ``<tu creationdate="...">`` string, or
      ``None`` when absent.
    - ``t_index`` is the position the entry would occupy if the list were
      sorted ascending by ``creationdate``. Dateless entries are pushed
      after all dated entries, preserving their relative natural order.

    Source/target are normalized so the in-memory representation is
    always EN -> SL, matching the existing convention in ``tm.py``.
    Entries with empty source OR empty target are skipped, also matching
    ``tm.py``.
    """
    path = Path(path)
    tree = etree.parse(str(path))
    root = tree.getroot()

    srclang = _read_srclang(root)
    origin = path.name

    entries: List[Dict[str, Any]] = []
    for tu in root.iter("tu"):
        creationdate = tu.get("creationdate")  # None when absent

        # Pair the two tuv children by xml:lang.
        src_text = ""
        tgt_text = ""
        for tuv in tu.findall("tuv"):
            lang = _norm_lang(tuv.get(XML_LANG))
            text = _seg_text(tuv)
            if lang == "en":
                src_text = text
            elif lang == "sl":
                tgt_text = text
            # Unknown langs are ignored; matches existing behavior where
            # only EN/SL pairs are kept.

        # If the file declares SL as source, the EN/SL detection above
        # already labels them correctly. The legacy swap in tm.py was a
        # workaround for the translate-toolkit API; here we infer by
        # xml:lang directly, so no manual swap is needed --- but we still
        # honor the legacy invariant for files where xml:lang labels are
        # missing or where srclang flips meaning.
        if srclang == "sl" and not src_text and not tgt_text:
            # Defensive: if the lang detection found nothing but srclang
            # is SL, fall back to positional order with a swap.
            tuvs = tu.findall("tuv")
            if len(tuvs) >= 2:
                tgt_text = _seg_text(tuvs[0])
                src_text = _seg_text(tuvs[1])

        if not src_text or not tgt_text:
            continue

        entries.append(
            {
                "source": src_text,
                "target": tgt_text,
                "origin": origin,
                "source_lang": "en",
                "target_lang": "sl",
                "raw_index": len(entries),
                "creationdate": creationdate,
                "t_index": -1,  # filled in below
            }
        )

    # Second pass: compute t_index. Sort a copy by (dateless?, creationdate,
    # raw_index) so dated entries come first in ascending date order and
    # dateless entries trail in natural order. Then enumerate and write
    # t_index back into the original-order list.
    sorted_view = sorted(
        entries,
        key=lambda e: (e["creationdate"] is None, e["creationdate"] or "", e["raw_index"]),
    )
    for i, entry in enumerate(sorted_view):
        entry["t_index"] = i

    return entries
