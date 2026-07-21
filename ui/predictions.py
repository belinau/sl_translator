"""Copilot-style ghost-text prediction engine.

Integrates with NiceGUI through its supported APIs:
  - ui.add_body_html(...)        — inject the client runtime once per page
  - ui.run_javascript(...)        — push candidate bundles when the active
                                    segment changes
  - element.on(..., js_handler=) — attach the client-side keystroke handler

No raw Vue/Quasar templates. The runtime is plain DOM JavaScript scoped to
`window.__sl_predictor` (no framework primitives).
"""
from __future__ import annotations

import asyncio
import json

from nicegui import ui


_RUNTIME_JS = r"""
(function() {
  if (window.__sl_predictor) return;
  const P = window.__sl_predictor = {
    _bundle: { candidates: [], multiword: [], kg: [] },
    _activeTextarea: null,
    _activeOverlay: null,
    _ghost: "",
    _ghostStart: 0,
  };
  const WORD_RE = /[\p{L}\p{M}'\-]+$/u;
  const SPLIT_RE = /\s+/;

  function findActiveOverlay() { return document.getElementById('sl-ghost-overlay'); }
  function findActiveTextarea(id) {
    // NiceGUI's ui.textarea renders a bare <textarea id="c{int}"> — the
    // element with that id IS the textarea, not a wrapper. We still defend
    // against Quasar-wrapped variants by checking tagName first.
    if (id) {
      const el = document.getElementById('c' + id);
      if (el) {
        if (el.tagName === 'TEXTAREA') return el;
        const nested = el.querySelector('textarea');
        if (nested) return nested;
      }
    }
    // Last resort: the only textarea inside the editor card.
    const card = document.getElementById('sl-editor-card');
    return card ? card.querySelector('textarea') : null;
  }

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
    // Initial paint so the overlay reflects the textarea even before the
    // user types a single character.
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

  P._recompute = function() {
    const ta = P._activeTextarea; if (!ta) return;
    const text = ta.value, pos = ta.selectionStart;
    const before = text.slice(0, pos), after = text.slice(pos);
    let suggestion = "";
    const wm = before.match(WORD_RE);
    if (wm) {
      const prefix = wm[0], prefixLow = prefix.toLowerCase();
      for (const c of P._bundle.candidates) {
        if (!c) continue;
        if (c.toLowerCase().startsWith(prefixLow) && c.length > prefix.length) {
          let rem = c.slice(prefix.length);
          const words = rem.split(SPLIT_RE).filter(Boolean);
          if (words.length > 2) {
            const lead = rem.match(/^\s*/)[0];
            rem = lead + words.slice(0, 2).join(' ');
          }
          suggestion = rem; break;
        }
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


async def push_bundle(textarea_id: int, source_text: str, src: str, tgt: str,
                      tm, glossary, kg, client=None) -> None:
    """Compute candidates off the event loop, ship them to the predictor via
    NiceGUI's run_javascript API. When invoked from a background task there
    is no implicit client context, so the caller passes its page client and
    we route the JS through it explicitly (client.run_javascript)."""
    loop = asyncio.get_running_loop()

    def _compute() -> dict:
        candidates: list[str] = []
        kg_hits: list[dict] = []
        # Priority order: verified KG translations → glossary → TM.
        # Same _kg_query source of truth as the visible intel cards.
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
                    # Concept-sibling target terms join the candidate pool so
                    # the ghost predictor completes adjacent established
                    # renditions, not just the direct source→target mapping.
                    # This is the "concept hierarchy matters more than fuzzy
                    # TM" principle made concrete for inline prediction.
                    for sib in h.get("related") or []:
                        if sib.get("lang") != tgt:
                            continue
                        t = sib.get("term")
                        if t and t.lower() not in {c.lower() for c in candidates}:
                            candidates.append(t)
        except Exception as e:
            print(f"[predictions kg] {e}")
        # Background target-language vocabulary so the ghost predictor can
        # complete ANY KG-known target term by prefix, not only those whose
        # source equivalent happens to be in this segment. Source-driven
        # hits come first (most relevant); the vocab pool fills the long
        # tail so a translator typing 'intersek' lands on
        # 'intersekcionalnost' even when 'intersectionality' is nowhere
        # in the current paragraph.
        try:
            if kg and hasattr(kg, 'G'):
                from .intel_panel import _kg_target_vocab
                vocab = _kg_target_vocab(kg, tgt)
                existing = {c.lower() for c in candidates}
                for surface in vocab:
                    key = surface.lower()
                    if key in existing:
                        continue
                    candidates.append(surface)
                    existing.add(key)
        except Exception as e:
            print(f"[predictions vocab] {e}")
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
