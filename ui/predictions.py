"""Copilot-style ghost-text prediction engine.

Integrates with NiceGUI through its supported APIs:
  - ui.add_body_html(...)        — inject the client runtime once per page
  - ui.run_javascript(...)        — push candidate bundles when the active
                                    segment changes
  - element.on(..., js_handler=) — attach the client-side keystroke handler

No raw Vue/Quasar templates. The runtime is plain DOM JavaScript scoped to
`window.__sl_predictor` (no framework primitives).

Performance architecture:
  - ``push_vocab`` sends the full target-language vocabulary ONCE on page
    load. The JS runtime stores it as a case-insensitively sorted array
    for O(log n) binary-search prefix matching.
  - ``push_bundle`` sends only the ~40 source-aligned candidates per
    segment switch — a few hundred bytes, not 132 KB.
  - ``_recompute`` checks source-aligned candidates first (exact prefix
    match on a small array), then falls back to the sorted vocab pool
    via binary search — O(log n) per keystroke instead of O(n).
"""
from __future__ import annotations

import asyncio
import json

from nicegui import ui


_RUNTIME_JS = r"""
(function() {
  if (window.__sl_predictor) return;
  const P = window.__sl_predictor = {
    // Small, per-segment candidates from source-aligned KG/glossary/TM.
    _bundle: { candidates: [], multiword: [], kg: [] },
    // Large, sent-once vocabulary pool (sorted for binary search).
    _vocab: [],           // original-case surfaces
    _vocabLow: [],        // lowercased, sorted — binary search target
    _activeTextarea: null,
    _activeOverlay: null,
    _ghost: "",
    _ghostStart: 0,
  };
  const WORD_RE = /[\p{L}\p{M}'\-]+$/u;
  const SPLIT_RE = /\s+/;

  function findActiveOverlay() { return document.getElementById('sl-ghost-overlay'); }
  function findActiveTextarea(id) {
    if (id) {
      const el = document.getElementById('c' + id);
      if (el) {
        if (el.tagName === 'TEXTAREA') return el;
        const nested = el.querySelector('textarea');
        if (nested) return nested;
      }
    }
    const card = document.getElementById('sl-editor-card');
    return card ? card.querySelector('textarea') : null;
  }

  // ── Binary search on sorted _vocabLow for first entry starting with prefix ──
  function _bsearchFirstPrefix(arr, prefix) {
    let lo = 0, hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (arr[mid] < prefix) lo = mid + 1; else hi = mid;
    }
    return lo;
  }

  P.setVocab = function(vocab) {
    // Dedup case-insensitively, sort lowercased for binary search.
    const seen = new Set();
    const clean = [];
    for (const v of vocab) {
      if (!v) continue;
      const low = v.toLowerCase();
      if (seen.has(low)) continue;
      seen.add(low);
      clean.push(v);
    }
    // Sort by lowercased form for binary-search prefix matching.
    const idx = clean.map((v, i) => [v.toLowerCase(), i]);
    idx.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    P._vocab = idx.map(([_, i]) => clean[i]);
    P._vocabLow = idx.map(([low]) => low);
  };

  P.setBundle = function(bundle, textareaId) {
    P._bundle = bundle || { candidates: [], multiword: [], kg: [] };
    const ta = findActiveTextarea(textareaId);
    if (!ta) {
      console.warn('[sl_predictor] textarea not found (id=', textareaId, ')');
      return;
    }
    P._activeTextarea = ta;
    P._activeOverlay = findActiveOverlay();
    if (!ta.__sl_bound) {
      ta.addEventListener('input', P._onInput);
      ta.addEventListener('click', P._onInput);
      ta.addEventListener('keyup', P._onKeyup);
      ta.addEventListener('keydown', P._onKeydown);
      ta.__sl_bound = true;
    }
    P._recompute();
  };

  P._onInput = function() { P._recompute(); };
  P._onKeyup = function(e) { if (e.key && e.key.startsWith('Arrow')) P._recompute(); };
  P._onKeydown = function(e) {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && (e.key === 'e' || e.key === 'E')) {
      e.preventDefault(); e.stopPropagation();
      if (e.shiftKey) P.acceptAll(); else P.acceptWord();
    } else if (e.key === 'Escape') { P.dismiss(); }
  };

  // ── Find a prefix match: source-aligned candidates first, then vocab pool ──
  P._findMatch = function(prefixLow, prefixLen) {
    // 1. Source-aligned candidates — small array, linear scan is fine (~40).
    for (const c of P._bundle.candidates) {
      if (!c) continue;
      if (c.length > prefixLen && c.toLowerCase().startsWith(prefixLow)) {
        return c;
      }
    }
    // 2. Vocab pool — binary search for first entry with this prefix.
    if (P._vocabLow.length === 0) return null;
    const start = _bsearchFirstPrefix(P._vocabLow, prefixLow);
    if (start >= P._vocabLow.length) return null;
    if (!P._vocabLow[start].startsWith(prefixLow)) return null;
    // Return the first match (vocab is frequency-sorted before lowercasing
    // in Python, so the original order is preserved among equal prefixes).
    return P._vocab[start];
  };

  P._recompute = function() {
    const ta = P._activeTextarea; if (!ta) return;
    const text = ta.value, pos = ta.selectionStart;
    const before = text.slice(0, pos), after = text.slice(pos);
    let suggestion = "";
    const wm = before.match(WORD_RE);
    if (wm) {
      const prefix = wm[0], prefixLow = prefix.toLowerCase();
      const match = P._findMatch(prefixLow, prefix.length);
      if (match) {
        let rem = match.slice(prefix.length);
        const words = rem.split(SPLIT_RE).filter(Boolean);
        if (words.length > 2) {
          const lead = rem.match(/^\s*/)[0];
          rem = lead + words.slice(0, 2).join(' ');
        }
        suggestion = rem;
      }
    } else {
      const typed = before.trimEnd().split(SPLIT_RE).filter(Boolean);
      for (const phrase of P._bundle.multiword) {
        if (!Array.isArray(phrase) || phrase.length <= typed.length) continue;
        let ok = true;
        for (let i = 0; i < typed.length; i++) {
          if ((phrase[i] || '').toLowerCase() !== typed[i].toLowerCase()) { ok = false; break; }
        }
        if (ok) {
          const next = phrase.slice(typed.length, typed.length + 2).join(' ');
          if (next) { suggestion = (before.endsWith(' ') ? '' : ' ') + next; break; }
        }
      }
    }
    if (suggestion && after.startsWith(' ') && suggestion.endsWith(' '))
      suggestion = suggestion.replace(/\s+$/, '');
    P._ghost = suggestion;
    P._ghostStart = pos;
    P._paint(before, suggestion, after);
  };

  P._paintCurrent = function() {
    const ta = P._activeTextarea; if (!ta) return;
    P._paint(ta.value, '', '');
  };

  P._paint = function(before, ghost, after) {
    const ov = P._activeOverlay || findActiveOverlay();
    P._activeOverlay = ov; if (!ov) return;
    const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br/>');
    ov.innerHTML =
      '<span>' + esc(before) + '</span>' +
      '<span class="ghost-fragment">' + esc(ghost) + '</span>' +
      '<span>' + esc(after) + '</span>';
  };

  P.acceptWord = function() {
    if (!P._ghost) return; const ta = P._activeTextarea; if (!ta) return;
    const lead = (P._ghost.match(/^\s+/) || [''])[0];
    const rest = P._ghost.slice(lead.length);
    const fw = rest.match(/^\S+/); if (!fw) return;
    const chunk = lead + fw[0], pos = P._ghostStart;
    ta.value = ta.value.slice(0, pos) + chunk + ta.value.slice(pos);
    const np = pos + chunk.length;
    ta.setSelectionRange(np, np); ta.focus();
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  };

  P.acceptAll = function() {
    if (!P._ghost) return; const ta = P._activeTextarea; if (!ta) return;
    const pos = P._ghostStart, chunk = P._ghost;
    ta.value = ta.value.slice(0, pos) + chunk + ta.value.slice(pos);
    const np = pos + chunk.length;
    ta.setSelectionRange(np, np); ta.focus();
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    P._ghost = '';
  };

  P.dismiss = function() { P._ghost = ''; P._paintCurrent(); };

  P.insertAtCursor = function(text) {
    const ta = P._activeTextarea || findActiveTextarea(); if (!ta || !text) return;
    const pos = ta.selectionStart, cur = ta.value;
    const sep = (pos > 0 && !/\s$/.test(cur.slice(0, pos))) ? ' ' : '';
    const chunk = sep + text;
    ta.value = cur.slice(0, pos) + chunk + cur.slice(pos);
    const np = pos + chunk.length;
    ta.setSelectionRange(np, np); ta.focus();
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  };
})();
"""


def inject_runtime() -> None:
    """Inject the predictor runtime once per page via ui.add_body_html."""
    ui.add_body_html("<script>\n" + _RUNTIME_JS + "\n</script>")


# ── Vocab cache: invalidated when KG term nodes are added/modified ──────────
_vocab_cache: dict[str, list[str]] = {}   # keyed by id(kg) + tgt_lang
_vocab_cache_keys: dict[str, int] = {}     # track kg object identity


def _get_vocab(kg, tgt_lang: str) -> list[str]:
    """Return cached target vocab for this KG, rebuilding only when the KG's
    internal term count changes (cheap stat, avoids 40K-node iteration on
    every segment switch)."""
    if kg is None or not hasattr(kg, "G"):
        return []
    cache_key = f"{id(kg)}:{tgt_lang}"
    # Invalidate if the term count changed since we cached.
    current_term_count = sum(
        1 for _, nd in kg.G.nodes(data=True)
        if nd.get("type") == "term" and nd.get("lang") == tgt_lang
    )
    if _vocab_cache_keys.get(cache_key) != current_term_count:
        from .intel_panel import _kg_target_vocab
        _vocab_cache[cache_key] = _kg_target_vocab(kg, tgt_lang)
        _vocab_cache_keys[cache_key] = current_term_count
    return _vocab_cache[cache_key]


async def push_vocab(kg, tgt_lang: str, client=None) -> None:
    """Send the full target-language vocabulary to the browser ONCE on page
    load. The JS runtime stores it as a sorted array for O(log n) binary
    search. Subsequent segment switches only need push_bundle (tiny)."""
    loop = asyncio.get_running_loop()

    def _compute() -> list[str]:
        return _get_vocab(kg, tgt_lang)

    try:
        vocab = await loop.run_in_executor(None, _compute)
    except Exception as e:
        print(f"[predictions push_vocab] {e}")
        return
    js = (
        f"window.__sl_predictor && window.__sl_predictor.setVocab("
        f"{json.dumps(vocab)});"
    )
    try:
        if client is not None:
            client.run_javascript(js)
        else:
            ui.run_javascript(js)
    except Exception as e:
        print(f"[predictions push_vocab js] {e}")


async def push_bundle(textarea_id: int, source_text: str, src: str, tgt: str,
                      tm, glossary, kg, client=None) -> None:
    """Compute source-aligned candidates and ship them to the predictor.

    Lightweight: only sends ~40 candidates (a few hundred bytes), NOT the
    full 7K+ vocab pool — that is sent once via push_vocab on page load.
    The JS runtime merges both at match time.
    """
    loop = asyncio.get_running_loop()

    def _compute() -> dict:
        candidates: list[str] = []
        kg_hits: list[dict] = []
        # Priority order: verified KG translations → glossary → TM.
        try:
            if kg and hasattr(kg, 'G'):
                from .intel_panel import _kg_query as _kq
                for h in _kq(source_text, src, tgt, kg):
                    if h.get("tgt_term"):
                        candidates.append(h["tgt_term"])
                        kg_hits.append({
                            "term": h["tgt_term"],
                            "confidence": h.get("confidence", 0.5),
                        })
                    for alt in h.get("alt_translations", []):
                        t = alt.get("term")
                        if t and t.lower() not in {c.lower() for c in candidates}:
                            candidates.append(t)
                    for sib in h.get("related") or []:
                        if sib.get("lang") != tgt:
                            continue
                        t = sib.get("term")
                        if t and t.lower() not in {c.lower() for c in candidates}:
                            candidates.append(t)
        except Exception as e:
            print(f"[predictions kg] {e}")
        try:
            if glossary:
                for h in glossary.lookup_terms(source_text, src, tgt) or []:
                    t = h.get("target_term")
                    if t:
                        candidates.append(t)
        except Exception as e:
            print(f"[predictions glossary] {e}")
        try:
            if tm:
                for m in tm.lookup_fuzzy(source_text, src, tgt, threshold=70.0, limit=3) or []:
                    t = m.get("target")
                    if t:
                        candidates.append(t)
        except Exception as e:
            print(f"[predictions tm] {e}")

        seen: set[str] = set()
        deduped: list[str] = []
        for c in candidates:
            if c and c.lower() not in seen:
                seen.add(c.lower())
                deduped.append(c)
        multiword: list[list[str]] = []
        for c in deduped:
            words = c.split()
            if 2 <= len(words) <= 5:
                multiword.append(words)
        return {"candidates": deduped, "multiword": multiword, "kg": kg_hits}

    try:
        bundle = await loop.run_in_executor(None, _compute)
    except Exception as e:
        print(f"[predictions push_bundle] {e}")
        return
    js = (
        f"window.__sl_predictor && window.__sl_predictor.setBundle("
        f"{json.dumps(bundle)}, {textarea_id});"
    )
    try:
        if client is not None:
            client.run_javascript(js)
        else:
            ui.run_javascript(js)
    except Exception as e:
        print(f"[predictions run_javascript] {e}")