# Plan audit — 2026-06-06

Reviewer: ruthless re-read of `docs/superpowers/plans/2026-06-06-parsing-simplification.md` against the user's mission (single neutral KG ontology, COBISS as authoritative containers, no new deps, no language hardcoding) and all supporting docs.

---

## 1. Executive summary

The plan is **80% ready to execute** and Phase 4 can start after two surgical fixes plus a Phase 5/Phase 4 reconciliation. Three blocking inconsistencies:

1. **Phase 5 does not accept `curator_extra` for `translated_work` containers** (line 876, 884), but Phase 4 explicitly creates the `curator_extra` provenance for extra-container ingest (lines 672, 677). Phase 4 says "Phase 5's routing chokepoint accepts `cobiss_personal` OR `curator_extra` as valid provenance for `kind=translated_work`" — Phase 5 itself contradicts this and would route every curator-extra container to review.
2. **Phase 4 verification gate (line 827) is a vestige of the discarded `lingua-py` approach.** Reads: *"COBISS ingest writes ONLY neutral fields; values come from `lang_detect.detect_language` on per-entry text."* The rest of Phase 4 explicitly rejects text-level language detection. This single line will mislead the green-phase subagent.
3. **Phase 12 language-neutrality assertion (lines 1602-1604) checks the live KG for `title_en` / `title_sl`** as legacy-fallback evidence. After Phase 4's migration strips these fields, the assertion is dead code — but the wording suggests they may legitimately survive. This contradicts Phase 4's "STRIPS legacy field names from nodes after copying values" gate.

Otherwise the plan is internally coherent. Mission understanding is correct. Constraints 7/9/10 are well-stated. No `lingua-py` / `langdetect` dependency appears anywhere in the plan. No review-queue routing for COBISS direction appears in the plan body (the residual `lang_detect` mention is the only vestige). The Phase 11 SUNSET items (1, 2, 3) are legitimate Phase-11 work.

**Verdict:** ready to execute Phase 4 after the three fixes below are applied to the plan. Do NOT dispatch Phase 4 subagents against the current text — the `lang_detect.detect_language` line will be copy-pasted into the green subagent's prompt and resurface the discarded approach.

---

## 2. Per-phase verdict

| Phase | Verdict | Note |
|---|---|---|
| Phase 0 | OK coherent | Done, baseline matches audit. |
| Phase 1 | OK coherent (done) | Committed; replaced by Phase 1B's compat shim. |
| Phase 2 | OK coherent (done) | Committed at `cd121b3`. |
| Phase 3 | OK coherent (done) | Committed at `7d2e5c2`. |
| Phase 1B | OK coherent (done) | Committed at `d152a20`; compat shim works. |
| Phase 4 | WARN — has issue | Vestige `lang_detect.detect_language` in gate (line 827). Otherwise scope is correct: kwarg-rename + TODO completion + migration + ontology + reader sweep + validator. |
| Phase 5 | BLOCKED — fix required | Does not accept `curator_extra` for translated_work. Direct contradiction with Phase 4 line 677. |
| Phase 6 | OK coherent | Chronological-anchor algorithm; reads neutral edge names; multi-pair test required. |
| Phase 7 | OK coherent | VL deletion; out-of-scope list respected. |
| Phase 8 | OK coherent | seed_kg + factories deletion. |
| Phase 9 | OK coherent | Drain noise concepts; `is_noise_concept` predicate well-specified. |
| Phase 10 | OK coherent | Lineage ingest; missing-agent routing correct. |
| Phase 11 | OK coherent | Three SUNSET items legitimate. |
| Phase 12 | WARN — has issue | Lang-neutrality assertion still references `title_en`/`title_sl` (lines 1602-1604); contradicts Phase 4 migration strip-behaviour. |

---

## 3. Per-check findings

### A. Coherence across phases

**A1. Phase 4 work scope (correct).** Lines 556, 577, 599-653: Phase 4 COBISS work is correctly scoped as kwarg rename + finished publisher TODO. Lines 599-653 explicitly say "the existing ingest script ALREADY parses and writes correctly; Phase 4 just renames the kwargs". This matches the user's "already done" framing.

**A2. Phase 4 migration evidence basis (correct).** Lines 681 ("Step 4.0c was removed — earlier draft proposed a `lingua-py` survey; after user pushback, Phase 4 no longer adds a language-detection dependency"), 695-700: migration uses field NAMES + edge attribution as evidence. Correct, no text-level detection.

**A3. Phase 11 SUNSET items (correct).** Lines 1441-1447 enumerate three legitimate Phase-11 items: (1) smol prompt-level `slovenian_edition` JSON-schema key rename — legitimate because prompt-rename risks model regression; (2) `CobissEntry.title_en` dataclass field rename — legitimate because it's name-only and structural; (3) sweep of remaining tags. None of these are Phase-4 work miscategorized.

**A4. Phase 5 does NOT accept `curator_extra` — BLOCKING.** Lines 840 ("Containers (`kind=translated_work`) ONLY accept `provenance=cobiss_personal`"), 876 ("containers ONLY accept `provenance=cobiss_personal`. NO other provenance value is allowed for `kind=translated_work`"), 884 (test case (a) only covers `cobiss_personal`). Phase 4 lines 672, 677 require Phase 5 to accept `curator_extra` for containers. **Direct contradiction.** Without fixing this, every extra-container record from Phase 4 routes to review, defeating the purpose of the extra-container path.

**A5. Phase 12 KG delta queries use neutral edge names (mostly correct).** Lines 1571-1577: counters use generic `relation` / `type` fields — neutral. Line 1583 spot-check mentions canonical `title_orig`/`title_translation` correctly. BUT lines 1602-1604 assert against `title_en` / `title_sl` (see check K).

### B. Constraints check

**B1. Constraint 7 wording is coherent.** Lines 19-26 cleanly define the neutral shape, including the carve-out for source-side code writing language-code values as data (line 24).

**B2. Constraint 9 wording is coherent.** Lines 29-33 explicitly forbid hardcoded `"sl"`/`"en"` flow control and silent defaults.

**B3. Constraint 10 wording is coherent.** Lines 35-42 clearly define the bibliography bright line with a table.

**B4. No phase writes legacy field names.** Searched the plan: `title_en` / `title_sl` writes appear only in:
- Line 750 (Phase 4 reviewer brief — confirming these strings appear ONLY in migration input, tests, validator forbidden list, and ONE SUNSET tag) — legitimate
- Line 698 (Phase 4 migration logic — input scan of pre-migration nodes) — legitimate
- Line 1602-1604 (Phase 12 — legacy fallback check) — see K
- Line 1490 (Phase 11 reviewer brief — `grep -rn 'slovenian_edition' returns either zero hits OR only the one-phase transitional fallback`) — note: this allows a TRANSITION fallback in the smol builder, which is reasonable for a one-phase grace window.

No phase actively writes `title_en` / `title_sl` / `slovenian_edition` / `sl_published_by` to the KG. Constraint 7 is honored in the writer paths.

### C. COBISS understanding

**C1. Parser convention trusted (correct).** Lines 556, 603: plan explicitly trusts `entry.title` = SL, `entry.title_en` = EN per `cobiss_parser.py:42-43` comment.

**C2. `belina_role` direction (correct).** Lines 610-635 give correct branch logic per role:
- `author`/`editor` → SL is original (Belina wrote/edited in SL)
- `translator` → SL is translation (Belina translated INTO SL)

**C3. No review-queue routing for COBISS direction (correct).** Phase 4 explicitly says (line 550) "No review-queue routing for COBISS — COBISS is your curated authoritative container source; its purpose is to anchor the container side so TM segments map to the correct work. Routing COBISS entries to review defeats that purpose."

**C4. No new dependencies (correct).** Lines 556, 577, 681, 703: explicit "No new dependency" annotations multiple times.

**C5. (None, None) classification fallback (correct).** Line 636 says unclassified entries continue to land in `data/cobiss_unclassified_entries.json` unchanged — that's the existing review path, not the KG review queue.

### D. Bibliography bright line (constraint 10)

**D1. Personal/COBISS produces containers + self-authored (correct).** Phase 4 line 599: COBISS containers via `translated_by`; self-authored via `written_by` (per `belina_role == author/editor`).

**D2. Book bibliography deletion (correct).** Phase 11 deletes `ingest_book_bibliography.py`, `ingest_book_footnotes.py`. No leftover `book_bibliography` provenance value in Phase 5 (verified by grep).

**D3. Strict separation maintained.** Phase 5 lines 840 ("The legacy `book_bibliography` provenance from `ingest_book_*.py` is NOT in scope — those scripts are deleted in Phase 11; their value never reaches the router") — correct.

### E. No new dependencies

`grep -rn "lingua\|langdetect\|fasttext" docs/superpowers/plans/` returns ZERO hits. Clean.

Step 4.0c (line 681) explicitly documents the removed `lingua-py` survey. The vestige issue (F1 below) is unrelated to the dependency surface — it's a leftover line in the verification gate.

### F. Existing code respect

**F1. CRITICAL VESTIGE — Phase 4 line 827.** Reads: `- [ ] COBISS ingest writes ONLY neutral fields; values come from lang_detect.detect_language on per-entry text.` This is a leftover from the discarded `lingua-py` approach. The rest of Phase 4 explicitly says no text-level language detection and no new dependencies. This single line in the verification gate would mislead the green-phase subagent. **MUST be fixed.**

**F2. Phase 4 scope respects existing script.** Lines 577, 599: Phase 4 changes to `scripts/ingest_personal_bibliography.py` are limited to (a) kwarg rename per `belina_role` branch, (b) finishing the publisher TODO at lines 269-271. Phase 4 does NOT propose to rewrite the script.

### G. Reader migration sanity

**G1. 75+ sites correctly grouped.** Line 582 references `docs/phase4_reader_inventory.md` which categorizes by surface (editor UI, visualization, KG ingest, etc.).

**G2. Backward-compat fallback BANNED (correct).** Lines 550 ("No backward-compat fallbacks. No 'legacy' parallel path"), 705 ("No backward-compat fallbacks (`d.get(\"title_orig\") or d.get(\"title_en\")` is BANNED)"), 830 ("NO `d.get(\"title_orig\") or d.get(\"title_en\")` fallback patterns").

  **BUT note:** the reader inventory doc (`docs/phase4_reader_inventory.md` lines 28, 46, 127, 391) recommends `d.get("title_orig") or d.get("title_en")` fallbacks as the migration approach. This is the OPPOSITE of what the plan says. The inventory doc was written under different assumptions and is **stale relative to the plan**. The plan correctly overrides: NO fallbacks. The architect's blueprint (Step 4.1) must explicitly contradict the inventory doc's recommendations on this point.

**G3. Migration runs FIRST (correct).** Line 708 specifies strict order: ontology edit → migration script → migration dry-run → migration apply → writers → extra-container ingest → smol cleanup → readers → validator → full test suite.

### H. Phase 11 SUNSET legitimacy

All three items legitimate:

1. **Smol prompt-level `slovenian_edition` JSON-schema key rename** (line 1443). Legitimate Phase 11: prompt rename risks model regression and needs separate testing. Phase 4 leaves a `# SUNSET: Phase 11` tag at the prompt site.
2. **`CobissEntry.title_en` dataclass field rename** (line 1445). Legitimate Phase 11: name-only structural rename; tightly scoped to COBISS layer.
3. **Sweep of remaining `# SUNSET: Phase 11` tags** (line 1447). Legitimate maintenance pass.

### I. Agent assignments

Verified per the per-phase Agent mix sections:

- `Explore` used for impact surveys (Steps 1B.0, 4.0a, 4.0b, 5.0, 6.0, 7.0, 8.0, 11.0) ✓
- `feature-dev:code-architect` for design steps (4.1, 5.1, 6.2, 1B.1) ✓
- `python-development:python-pro` for TDD red/green (all TDD steps) ✓
- `pythonista-reviewer` for diff review (1B.4, 3 review, 4.4, 5.4, 6.5, 7.2, 8.2, 9.3a, 10.4a, 11.4) ✓
- Line 1660: explicit "Independent review pass: `pythonista-reviewer`" — committed at `0916e70` (replaces `feature-dev:code-reviewer`).

### J. Vestiges from discarded approaches

**J1. `lingua-py` / `lang_detect.py`:** ONE residual mention at line 827 (the `lang_detect.detect_language` text in the Phase 4 verification gate). Otherwise removed.

**J2. Review-queue routing for COBISS:** NONE found.

**J3. Legacy encoding as compat shim for COBISS:** NONE found.

**J4. Backward-compat `d.get` fallback in reader migration:** NONE in plan; explicitly banned (G2). Note that the inventory doc recommends them, but plan overrides.

**J5. `book_bibliography` provenance in Phase 5:** NONE in plan body. Phase 5 explicitly notes this as out of scope (line 840).

### K. Phase log accuracy

`git log --oneline` matches phase log:
- Phase 0: documented (preflight + advisor revisions); commits `87b3dba`, `70db298` ✓
- Phase 1: `b723134` matches phase log line 167 ✓
- Phase 2: `cd121b3` matches phase log line 221 ✓
- Phase 3: `7d2e5c2` matches phase log line 310 ✓
- Phase 1B: `d152a20` matches phase log line 431 ✓
- Plan revisions: `4046cf6` (Phase 1B insert), `51c9df5` (lang-neutrality audit), `4425590` / `7e42014` / `cc9f5c3` (Phase 4 rewrites), `0916e70` (reviewer rename), `44b7157` / `d8e8287` (agent mix + sanitization) ✓

**Note:** Phase 12 step 12.5 (lines 1593-1608) still asserts on `title_en` / `title_sl` as a "sunset miss" check. After Phase 4 migration strips these fields, this check is either (a) trivially true (zero hits, the check passes by saying "no leftover legacy") OR (b) misleading if it's expected to legitimately find legacy fields. The assertion `not violations` is correct intent. Wording is ambiguous: should be tightened to "after Phase 4 migration, this MUST return zero".

---

## 4. Recommended edits

Apply these THREE edits before dispatching Phase 4 subagents.

### Edit 1 (BLOCKING): Phase 5 acceptance of `curator_extra`

**File:** `docs/superpowers/plans/2026-06-06-parsing-simplification.md`

**Location 1 — line 840 (Phase 5 scope reminder):**

Replace:
> Containers (`kind="translated_work"`) ONLY accept `provenance="cobiss_personal"`. All other provenance values route a `translated_work` record to review — they are the audit's "seeded-book" bug class (audit §10.4).

With:
> Containers (`kind="translated_work"`) accept `provenance="cobiss_personal"` (the COBISS-curated authoritative source) OR `provenance="curator_extra"` (user-curated extras for works not in COBISS, populated via Phase 4's `scripts/ingest_extra_containers.py`). All other provenance values (`tm_smol`, `doc_pair`, unset, unknown) route a `translated_work` record to review — they are the audit's "seeded-book" bug class (audit §10.4).

**Location 2 — line 876 (Step 5.1 architect brief constraint reminder):**

Replace:
> Constraint reminder: containers ONLY accept `provenance="cobiss_personal"`. NO other provenance value is allowed for `kind="translated_work"`.

With:
> Constraint reminder: containers accept `provenance="cobiss_personal"` OR `provenance="curator_extra"`. NO other provenance value is allowed for `kind="translated_work"`. Both producers (Phase 4's `scripts/ingest_personal_bibliography.py` and `scripts/ingest_extra_containers.py`) are authoritative sources; the routing chokepoint enforces "container-class provenance" not "COBISS provenance".

**Location 3 — line 884 (Step 5.2 TDD red test brief):**

Replace test case (a):
> (a) `kind="translated_work"` + `provenance="cobiss_personal"` → ACCEPTED; container node written with `translated_by` edge. Use a non-SL/EN example title pair to surface any latent language-pair bug.

With:
> (a) `kind="translated_work"` + `provenance="cobiss_personal"` → ACCEPTED; container node written with `translated_by` edge. Use a non-SL/EN example title pair to surface any latent language-pair bug.
> (a') `kind="translated_work"` + `provenance="curator_extra"` → ACCEPTED; container node written with `translated_by` edge. Cover one entry with explicit `orig_lang="de"` + `translation_lang="en"` to confirm non-SL pairs work via the extra-container path.

### Edit 2 (BLOCKING): Remove `lang_detect.detect_language` vestige

**File:** same plan.

**Location — line 827 (Phase 4 verification gate):**

Replace:
> - [ ] COBISS ingest writes ONLY neutral fields; values come from `lang_detect.detect_language` on per-entry text.

With:
> - [ ] COBISS ingest writes ONLY neutral fields; values come from the `belina_role` branch (SL data from `entry.title`, EN data from `entry.title_en`) per the COBISS parser convention. No text-level language detection.

### Edit 3 (CLARIFY): Phase 12 lang-neutrality assertion wording

**File:** same plan.

**Location — lines 1602-1604 (Phase 12 lang-neutrality check):**

Replace:
```python
    # Legacy title_en/title_sl without canonical is a sunset miss
    if (d.get('title_en') or d.get('title_sl')) and not d.get('title_orig'):
        violations.append((nid, 'has legacy title_en/title_sl but no canonical title_orig'))
```

With:
```python
    # After Phase 4 migration, NO node should carry title_en/title_sl/slovenian_edition.
    # The migration strips them after copying values. Any survivor is a migration miss.
    if d.get('title_en') or d.get('title_sl') or d.get('slovenian_edition'):
        violations.append((nid, 'legacy field survived Phase 4 migration'))
```

### Edit 4 (RECOMMENDED): Note that reader-inventory doc is stale

**Location — Step 4.1 architect brief (line 687 area):**

Add to the read-first list:
> Read first: ... `docs/phase4_reader_inventory.md`. NOTE: that document was written before the "no backward-compat fallback" rule landed and recommends `d.get("title_orig") or d.get("title_en")` fallbacks throughout. IGNORE those recommendations. The plan constraint (line 705) is authoritative: NO fallbacks. The inventory doc's value is the FILE LISTINGS and READ SITES per surface; ignore its migration-strategy section.

---

## 5. Top-level verdict

**Plan is ready to execute Phase 4 after Edits 1, 2, and 3 are applied to the plan text.** Edit 4 is recommended hygiene.

**The two things the coordinator must remember not to lose track of as Phase 4 begins:**

1. **Phase 4 is small, not a rewrite.** The COBISS ingest changes are exactly two: (a) kwarg rename per `belina_role` branch; (b) finish the bilingual publisher `: =` split TODO at `scripts/ingest_personal_bibliography.py:269-271`. Everything else in that script stays as-is. If the architect's blueprint proposes more, push back — that's scope creep.

2. **Phase 5 accepts TWO container provenances after Phase 4's edits.** `cobiss_personal` AND `curator_extra`. Without Edit 1, Phase 5 silently kills the extra-container path Phase 4 built. The user's mission requires both paths to write containers; Phase 5 is the chokepoint that must permit both.

**Secondary reminder:** the reader-inventory doc's "use fallback chains" guidance is OBSOLETE under the new plan. The migration runs first; readers then read only neutral fields. The doc is useful for its file-by-file site inventory; the inventory's migration-strategy section must be ignored.
