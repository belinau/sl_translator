"""Read TMX files with TU-level creationdate timecodes.

This module exists because ``translate.storage.tmx.tmxfile`` (used by
``translate_core.tm._load_tmx``) silently discards the ``creationdate``
attribute on ``<tu>`` elements. Phase 1 of the parsing-simplification
plan needs that timestamp to expose a chronological view of the
translation memory while keeping ``self.entries`` in natural file order
for editor-side stability.

The public entry point is :func:`read_tmx_with_timecodes`. It parses
the TMX with ``lxml.etree`` directly, pairs ``<tuv>`` children by header
``srclang`` + positional fallback (Phase 1B blueprint §5), applies the
``clean_xml`` text scrubbing that ``tm.py`` performs today, and returns
enriched dicts with three new keys: ``raw_index``, ``creationdate``, and
``t_index``. ``source_lang`` / ``target_lang`` carry the ACTUAL
``xml:lang`` codes (or ``None``) — no normalisation to EN/SL.
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


def _read_srclang(root: etree._Element) -> Optional[str]:
    """Return the TMX header's ``srclang`` attribute (normalized) or ``None``.

    Phase 1B blueprint §5: previous behaviour defaulted to ``"en"`` on missing
    header / missing attribute. We now return ``None`` so the caller can fall
    back to positional pairing without inventing a language label.
    """
    header = root.find("header")
    if header is None:
        # Some TMX serializations namespace-qualify; try a broad search.
        header = root.find(".//header")
    if header is None:
        return None
    return _norm_lang(header.get("srclang")) or None


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


def read_tmx_with_timecodes(path: Path, *, skip_empty: bool = True) -> List[Dict[str, Any]]:
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
    - ``source_lang`` / ``target_lang`` are the ACTUAL ``xml:lang`` codes
      from the TMX (lowercased, locale stripped) or ``None`` when absent.
      Phase 1B blueprint §5 / audit §3.2: no normalisation to EN/SL.

    Pairing strategy (blueprint §5):
      1. If the header declares ``srclang`` and one of the two ``<tuv>``
         children carries that ``xml:lang``, that ``<tuv>`` is the source
         and the other is the target.
      2. Otherwise fall back to positional order: first ``<tuv>`` is the
         source, second is the target.

    Entries with empty source OR empty target are skipped by default
    (``skip_empty=True``), matching ``tm.py`` behaviour. Pass
    ``skip_empty=False`` to keep language-only TUs (e.g. a translator
    credit line with no counterpart) — used by the aligner's review
    viewer so the full bilingual document is shown, nothing dropped.
    """
    path = Path(path)
    _parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    tree = etree.parse(str(path), parser=_parser)
    root = tree.getroot()

    srclang = _read_srclang(root)  # may be None
    origin = path.name

    entries: List[Dict[str, Any]] = []
    for tu in root.iter("tu"):
        creationdate = tu.get("creationdate")  # None when absent

        # TMX `<prop>` elements carry the SDL Trados / OmegaT linked-list
        # checksums that chain consecutive TUs created in the same
        # translation session. When TU N's `previousMd5Checksum` does NOT
        # equal TU N-1's `nextMd5Checksum`, that's a deterministic session
        # boundary — typically a new work or a session resumption. No
        # statistics required; this is structural metadata stamped by the
        # CAT tool. Downstream session-detection lives in
        # `translate_core.tm.iter_sessions`.
        prev_md5: Optional[str] = None
        next_md5: Optional[str] = None
        for prop in tu.findall("prop"):
            ptype = prop.get("type")
            if ptype == "previousMd5Checksum":
                prev_md5 = prop.text
            elif ptype == "nextMd5Checksum":
                next_md5 = prop.text

        tuvs = tu.findall("tuv")
        if len(tuvs) != 2:
            continue  # skip TU silently; matches today's behaviour
        a_lang = _norm_lang(tuvs[0].get(XML_LANG))  # "" if absent
        b_lang = _norm_lang(tuvs[1].get(XML_LANG))

        if srclang and a_lang and a_lang == srclang:
            src_text, src_lang = _seg_text(tuvs[0]), a_lang
            tgt_text, tgt_lang = _seg_text(tuvs[1]), b_lang or None
        elif srclang and b_lang and b_lang == srclang:
            src_text, src_lang = _seg_text(tuvs[1]), b_lang
            tgt_text, tgt_lang = _seg_text(tuvs[0]), a_lang or None
        else:
            # Positional fallback
            src_text, src_lang = _seg_text(tuvs[0]), a_lang or None
            tgt_text, tgt_lang = _seg_text(tuvs[1]), b_lang or None

        if skip_empty and (not src_text or not tgt_text):
            continue

        entries.append(
            {
                "source": src_text,
                "target": tgt_text,
                "origin": origin,
                "source_lang": src_lang,
                "target_lang": tgt_lang,
                "raw_index": len(entries),
                "creationdate": creationdate,
                "prev_md5": prev_md5,
                "next_md5": next_md5,
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
