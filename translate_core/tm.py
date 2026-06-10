# translate_core/tm.py

import html
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from rapidfuzz import fuzz, process

import config


def clean_xml(text: str) -> str:
    """Strips raw XML/HTML tags that export tools leave inside TMX segments."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)  # Strip tags
    text = html.unescape(text)  # Convert &amp; to &
    return re.sub(r"\s+", " ", text).strip()


class TranslationMemory:
    def __init__(self, tm_dir: Path = config.TM_DIR):
        self.tm_dir = Path(tm_dir)
        # Phase 1B blueprint §2: per-pair bucket index keyed by actual
        # (source_lang, target_lang) tuples (None tolerated). The compat-view
        # ``self.entries`` (built at the end of _load_all) is derived from
        # this dict; runtime ``main.py:251`` appends append directly to
        # ``self.entries`` so it stays a concrete list, not a property.
        self._entries_by_pair: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]] = {}
        self.entries: List[Dict[str, Any]] = []
        self._load_all()

    def _load_all(self):
        if not self.tm_dir.exists():
            self.tm_dir.mkdir(parents=True, exist_ok=True)
            return
        for p in self.tm_dir.glob("*.tmx"):
            self._load_tmx(p)

        # Phase 1B blueprint §10 step 4 / Risk 1: FIRST recompute t_index
        # across ALL pair buckets so original entry dicts in
        # ``_entries_by_pair`` receive their global rank. THEN build the
        # ``self.entries`` compat view from those (now correctly-indexed)
        # originals — the swapped shallow copies inherit the assigned
        # ``t_index`` from the originals. Inverting this order would leave
        # the swapped copies with a stale ``t_index`` of -1.
        self._reindex_t_index()
        self._build_compat_entries()

    def _load_tmx(self, path: Path):
        # Delegate to tm_timecodes.read_tmx_with_timecodes so we keep the
        # TU-level creationdate attribute (translate.storage.tmx silently
        # drops it). Imported lazily because tm_timecodes does a lazy
        # back-import of clean_xml from this module.
        from translate_core.tm_timecodes import read_tmx_with_timecodes

        file_entries = read_tmx_with_timecodes(path)

        # Phase 1B blueprint §2: raw_index is GLOBAL across all pair
        # buckets — count the flat total across every bucket already
        # populated, not per-pair.
        offset = sum(len(bucket) for bucket in self._entries_by_pair.values())
        for entry in file_entries:
            entry["raw_index"] += offset
            key = (entry.get("source_lang"), entry.get("target_lang"))
            self._entries_by_pair.setdefault(key, []).append(entry)

    def _reindex_t_index(self) -> None:
        """Recompute ``t_index`` for every entry across the full corpus.

        Sort key: dated entries first (ascending by creationdate),
        dateless entries after (ascending by raw_index to preserve
        natural order). Writes ``t_index`` back into the ORIGINAL entry
        dicts living in ``_entries_by_pair`` — those dicts are the
        source of truth that the compat-view swap copies from.
        """
        flat: List[Dict[str, Any]] = []
        for bucket in self._entries_by_pair.values():
            flat.extend(bucket)
        sorted_view = sorted(
            flat,
            key=lambda e: (
                e.get("creationdate") is None,
                e.get("creationdate") or "",
                e["raw_index"],
            ),
        )
        for i, entry in enumerate(sorted_view):
            entry["t_index"] = i

    def _build_compat_entries(self) -> None:
        """Materialise ``self.entries`` as the EN/SL compat view.

        Per blueprint §3: draws from exactly ``("en", "sl")`` (originals)
        and ``("sl", "en")`` (shallow-copy with source/target swapped and
        labels forced to en/sl). All other pair buckets are excluded.
        Merged list sorted by global ``raw_index`` to preserve natural
        load order, then re-numbered 0..N-1 so the editor / inline_test
        fixtures see a contiguous index (blueprint §3 invariant).

        Both EN-SL originals AND SL-EN swapped copies are shallow-copied
        before any mutation here — the dicts living in
        ``_entries_by_pair`` MUST remain untouched as the chronological
        source of truth.

        Built ONCE at end of ``_load_all`` AFTER ``_reindex_t_index``;
        ``main.py:251`` appends to ``self.entries`` at runtime so this
        must remain a concrete list, never a derived property.
        """
        # COMPAT-SHIM: audited exception to constraint 9; sunset in Phase 11
        en_sl = self._entries_by_pair.get(("en", "sl"), [])
        # COMPAT-SHIM: audited exception to constraint 9; sunset in Phase 11
        sl_en = self._entries_by_pair.get(("sl", "en"), [])

        merged: List[Dict[str, Any]] = []
        # Shallow-copy each EN-SL original so the renumbering below
        # doesn't mutate the bucket source of truth.
        for entry in en_sl:
            merged.append(dict(entry))
        for entry in sl_en:
            # COMPAT-SHIM: audited exception to constraint 9; sunset in Phase 11
            # Swap source/target so the editor sees a uniform EN→SL
            # orientation. Original entry in _entries_by_pair is NOT
            # mutated (shallow-copy first).
            swapped = dict(entry)
            swapped["source"] = entry["target"]
            swapped["target"] = entry["source"]
            swapped["source_lang"] = "en"
            swapped["target_lang"] = "sl"
            merged.append(swapped)

        merged.sort(key=lambda e: e["raw_index"])

        # Re-number raw_index across the compat view so it forms a
        # contiguous 0..N-1 range — blueprint §3 invariant and the
        # editor-side contract (test_translation_memory_entries_remain_in_natural_load_order).
        for i, entry in enumerate(merged):
            entry["raw_index"] = i

        self.entries = merged

    def upsert_runtime_pair(
        self,
        source: str,
        target: str,
        src_lang: str,
        tgt_lang: str,
        origin: str = "working.tmx",
    ) -> None:
        """Mirror a runtime-confirmed TU into ``_entries_by_pair``.

        If the same ``source`` already exists in the ``(src_lang, tgt_lang)``
        bucket with the same ``origin``, update its ``target`` in place.
        Otherwise append a new entry with the highest ``raw_index`` and
        ``t_index`` (a runtime confirm is chronologically newest).

        Also appends/updates in ``self.entries`` (compat view) so both
        collections stay in sync.
        """
        key = (src_lang, tgt_lang)
        bucket = self._entries_by_pair.setdefault(key, [])

        for entry in bucket:
            if entry.get("source") == source and entry.get("origin") == origin:
                entry["target"] = target
                # Mirror into self.entries if present there
                for ce in self.entries:
                    if ce.get("source") == source and ce.get("origin") == origin:
                        ce["target"] = target
                return

        # Compute next raw_index / t_index (highest + 1)
        max_raw = max((e.get("raw_index", -1) for e in bucket), default=-1)
        max_t = max(
            (e.get("t_index", -1) for bucket2 in self._entries_by_pair.values() for e in bucket2),
            default=-1,
        )
        new_entry = {
            "source": source,
            "target": target,
            "origin": origin,
            "source_lang": src_lang,
            "target_lang": tgt_lang,
            "raw_index": max_raw + 1,
            "t_index": max_t + 1,
        }
        bucket.append(new_entry)
        self.entries.append(new_entry)

    def iter_chronological(
        self, origin: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        """Yield entries in ascending ``t_index`` (chronological) order.

        Reads from ALL pair buckets in ``_entries_by_pair`` (blueprint §4),
        not from the EN/SL-filtered ``self.entries``. Entries carry their
        ACTUAL ``source_lang``/``target_lang`` codes — no swap, no
        normalisation.

        When ``origin`` is provided, only entries from that origin file
        are yielded, still in chronological order within that origin.

        confirmed TUs are now mirrored into the pair index via
        ``upsert_runtime_pair``, so they are visible here.
        """
        flat: List[Dict[str, Any]] = []
        for bucket in self._entries_by_pair.values():
            flat.extend(bucket)
        sel = (
            flat
            if origin is None
            else [e for e in flat if e.get("origin") == origin]
        )
        yield from sorted(sel, key=lambda e: e["t_index"])

    def lookup_fuzzy(
        self, text: str, threshold: float = 90.0, limit: int = 3
    ) -> List[Dict]:
        """
        Fuzzy lookup in TM.
        Enforces a high threshold (default 90%) and penalizes extreme length differences.
        """
        sources = [e["source"] for e in self.entries if e["source"]]
        if not sources:
            return []

        # We use a slightly lower initial limit for process.extract to filter ourselves later
        matches = process.extract(text, sources, scorer=fuzz.ratio, limit=limit * 5)
        results = []
        input_len = len(text)

        for src, score, _ in matches:
            if score >= threshold:
                # Length check: avoid segments that are vastly different in length
                src_len = len(src)
                len_ratio = (
                    max(src_len, input_len) / min(src_len, input_len)
                    if min(src_len, input_len) > 0
                    else 10
                )

                if (
                    len_ratio > 2.5
                ):  # If one is more than 2.5x longer than the other, skip
                    continue

                for e in self.entries:
                    if e["source"] == src:
                        results.append({**e, "score": score})
                        break
            if len(results) >= limit:
                break
        return results

    def search_concordance(self, text: str, top_n: int = 5) -> List[Dict]:
        """
        Concordance search: return all segments containing any of the query words.
        Ranked by word coverage (more query words matched = higher rank), then
        by segment length ascending (shorter = more precise match).
        No length penalty — concordance must return full sentences regardless of
        how short the query is.
        """
        words = [w for w in text.split() if len(w) >= 2]
        if not words:
            return []

        scored_entries = []
        for entry in self.entries:
            src_text = entry["source"]
            tgt_text = entry["target"]
            count = sum(
                1
                for w in words
                if w.lower() in src_text.lower() or w.lower() in tgt_text.lower()
            )
            if count > 0:
                scored_entries.append({
                    **entry,
                    "relevance": count / len(words),
                    "_seg_len": len(src_text),
                })

        scored_entries.sort(key=lambda x: (-x["relevance"], x["_seg_len"]))
        return scored_entries[:top_n]

    def search_prefix(self, prefix: str) -> List[str]:
        """
        Quickly find completions starting with the given prefix.
        Limits results to short phrases (max 3 words) to avoid 'sausage' predictions.
        """
        if not prefix or len(prefix) < 2:
            return []
        prefix_low = prefix.lower()
        matches = []
        for e in self.entries:
            target_words = e["target"].split()
            for i, w in enumerate(target_words):
                if w.lower().startswith(prefix_low):
                    # Only suggest the current word and at most 2 subsequent words
                    suggestion = " ".join(target_words[i : i + 3])
                    matches.append(suggestion)
                    break
            if len(matches) > 10:
                break
        return list(set(matches))
