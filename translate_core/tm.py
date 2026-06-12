# translate_core/tm.py

import bisect
import html
import re
import threading
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


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _kwic_at(text: str, pos: int, window: int = 90) -> str:
    """Excerpt of ``text`` centered on ``pos`` (start index of the matched
    word), snapped to word boundaries, with ``…`` prefixed/suffixed where
    the excerpt is truncated. Returns the full text unchanged when it
    already fits inside ``window``. Caller must ensure ``pos`` is valid."""
    if not text:
        return ""
    n = len(text)
    if n <= window:
        return text
    half = window // 2
    start = max(0, pos - half)
    end = min(n, start + window)
    if end == n:
        start = max(0, n - window)
    # Snap inward to the next word boundary so we never cut a word in half.
    if start > 0:
        sp = text.find(" ", start)
        if 0 <= sp < pos:
            start = sp + 1
    if end < n:
        sp = text.rfind(" ", pos, end)
        if sp > pos:
            end = sp
    return (
        ("…" if start > 0 else "")
        + text[start:end].strip()
        + ("…" if end < n else "")
    )


def _kwic_head(text: str, window: int = 90) -> str:
    """Head excerpt with trailing ``…`` on truncation; word-boundary snapped.
    Used as the fallback when no query word matched on a given side
    (e.g. matched in source but not target)."""
    if not text or len(text) <= window:
        return text
    sp = text.rfind(" ", 0, window)
    end = sp if sp > window // 2 else window - 1
    return text[:end].strip() + "…"


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
        # Lazy search index over ``self.entries`` (see _sync_index).
        self._index_lock = threading.Lock()
        self._src_low: List[str] = []
        self._tgt_low: List[str] = []
        self._inv: Dict[str, List[int]] = {}
        self._inv_tokens: List[str] = []
        self._fuzzy_sources: List[str] = []
        self._fuzzy_ids: List[int] = []
        self._indexed_len = 0

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

    def _sync_index(self) -> None:
        """Index any ``self.entries`` tail not yet covered.

        Structures (all parallel to ``self.entries`` by integer id):
        - ``_src_low`` / ``_tgt_low``: lowercase mirrors (built once, not
          per query — the old per-query ``.lower()`` over ~10MB of text
          was the single biggest concordance cost).
        - ``_inv``: token (>=2 chars, both sides) -> posting list of ids.
        - ``_inv_tokens``: sorted vocabulary for prefix range scans.
        - ``_fuzzy_sources`` / ``_fuzzy_ids``: prebuilt rapidfuzz choices
          (every non-empty source) and their entry ids.

        Lazy + incremental: called at the top of every query method, so
        entries appended at runtime (``upsert_runtime_pair`` or direct
        ``tm.entries.append``) become searchable with no explicit hook.
        Guarded by a lock — queries run on NiceGUI's thread pool and a
        double-append would break the parallel-list alignment.
        """
        if self._indexed_len == len(self.entries):
            return
        with self._index_lock:
            n = len(self.entries)
            new_tokens = False
            for i in range(self._indexed_len, n):
                e = self.entries[i]
                src = e.get("source") or ""
                tgt = e.get("target") or ""
                sl, tl = src.lower(), tgt.lower()
                self._src_low.append(sl)
                self._tgt_low.append(tl)
                for w in set(_TOKEN_RE.findall(sl)) | set(_TOKEN_RE.findall(tl)):
                    if len(w) >= 2:
                        if w not in self._inv:
                            new_tokens = True
                        self._inv.setdefault(w, []).append(i)
                if src:
                    self._fuzzy_sources.append(src)
                    self._fuzzy_ids.append(i)
            self._indexed_len = n
            if new_tokens:
                self._inv_tokens = sorted(self._inv)

    def _reindex_entry(self, i: int) -> None:
        """Refresh mirrors/postings after an in-place target update.

        Stale postings (tokens the old target had) are left in ``_inv`` —
        they only produce false candidates, and the scoring pass re-checks
        every candidate with ``find()`` so correctness is unaffected.
        """
        if i >= self._indexed_len:
            return  # tail not indexed yet; _sync_index will cover it
        with self._index_lock:
            e = self.entries[i]
            sl = (e.get("source") or "").lower()
            tl = (e.get("target") or "").lower()
            self._src_low[i] = sl
            self._tgt_low[i] = tl
            new_tokens = False
            for w in set(_TOKEN_RE.findall(sl)) | set(_TOKEN_RE.findall(tl)):
                if len(w) >= 2:
                    ids = self._inv.get(w)
                    if ids is None:
                        self._inv[w] = [i]
                        new_tokens = True
                    elif ids[-1] != i and i not in ids:
                        ids.append(i)
            if new_tokens:
                self._inv_tokens = sorted(self._inv)

    def _prefix_postings(self, w: str) -> List[int]:
        """Posting ids for ``w`` expanded over token PREFIXES (at most 50
        vocabulary tokens scanned). Shared by candidate generation and
        the rarity trim so both rank words by the same notion of
        document frequency."""
        ids: List[int] = []
        lo = bisect.bisect_left(self._inv_tokens, w)
        for tok in self._inv_tokens[lo : lo + 50]:
            if not tok.startswith(w):
                break
            ids.extend(self._inv[tok])
        return ids

    def _candidates_for(self, words: List[str], cap: int = 20000) -> set:
        """Union of posting lists for each query word, prefix-expanded.

        Rarest words are unioned first so that when the cap trips on
        stop-word-frequency tokens, the informative words have already
        contributed their postings.
        """
        postings = [self._prefix_postings(w) for w in words]
        cand: set = set()
        for ids in sorted(postings, key=len):
            cand.update(ids)
            if len(cand) >= cap:
                break
        return cand

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
        """Whole-segment fuzzy lookup via rapidfuzz ``fuzz.ratio``.

        Uses the prebuilt ``_fuzzy_sources`` choice list and rapidfuzz's
        ``score_cutoff`` pruning (massively faster than post-filtering),
        and recovers the matched entry through ``_fuzzy_ids`` using the
        index rapidfuzz returns — the old implementation re-scanned all
        of ``self.entries`` per match.

        No minimum-source-length prefilter: with ``fuzz.ratio`` a short
        entry cannot spuriously score high against a long query (length
        mismatch tanks the ratio), and filtering would drop legitimate
        short segments such as headings.
        """
        self._sync_index()
        if not text or not self._fuzzy_sources:
            return []
        matches = process.extract(
            text,
            self._fuzzy_sources,
            scorer=fuzz.ratio,
            limit=limit,
            score_cutoff=threshold,
        )
        return [
            {**self.entries[self._fuzzy_ids[idx]], "score": score}
            for _src, score, idx in matches
        ]

    def search_concordance(
        self, text: str, top_n: int = 5, max_words: int = 12
    ) -> List[Dict]:
        """Concordance search over the inverted index.

        Candidate generation: token-PREFIX lookup per query word (>=2
        chars) via ``_candidates_for``. Scoring: word coverage descending,
        then segment length ascending (shorter = more precise). KWIC
        excerpts (``kwic_source``/``kwic_target``) are built ONLY for the
        final ``top_n`` — the old implementation built them for every
        matching entry, which together with per-query ``.lower()`` over
        the whole corpus made long queries take seconds.

        Queries longer than ``max_words`` keep their rarest (most
        informative) words, by document frequency.
        """
        self._sync_index()
        words = [w.lower() for w in _TOKEN_RE.findall(text) if len(w) >= 2]
        if not words:
            return []
        uniq = list(dict.fromkeys(words))
        if len(uniq) > max_words:
            # Prefix-aware document frequency: a word that expands to no
            # corpus token can never produce a candidate — drop it rather
            # than let df=0 masquerade as "rarest" and displace real rare
            # words. Keep the max_words rarest of the remainder.
            dfs = {w: len(self._prefix_postings(w)) for w in uniq}
            uniq = sorted(
                (w for w in uniq if dfs[w] > 0),
                key=lambda w: dfs[w],
            )[:max_words]
        words = uniq
        if not words:
            return []

        scored: List[tuple] = []
        for i in self._candidates_for(words):
            sl, tl = self._src_low[i], self._tgt_low[i]
            sp = tp = -1
            matched = 0
            for w in words:
                si = sl.find(w)
                ti = tl.find(w)
                if si < 0 and ti < 0:
                    continue
                matched += 1
                if si >= 0 and (sp < 0 or si < sp):
                    sp = si
                if ti >= 0 and (tp < 0 or ti < tp):
                    tp = ti
            if matched:
                scored.append((
                    -matched / len(words),
                    len(self.entries[i]["source"]),
                    i, sp, tp,
                ))

        scored.sort()
        results: List[Dict] = []
        for neg_rel, seg_len, i, sp, tp in scored[:top_n]:
            e = self.entries[i]
            results.append({
                **e,
                "relevance": -neg_rel,
                "_seg_len": seg_len,
                "kwic_source": _kwic_at(e["source"], sp) if sp >= 0
                               else _kwic_head(e["source"]),
                "kwic_target": _kwic_at(e["target"], tp) if tp >= 0
                               else _kwic_head(e["target"]),
            })
        return results

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
