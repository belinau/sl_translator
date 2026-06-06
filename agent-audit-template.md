# Ontology audit — agent instruction template

**This template is identical for every audit agent dispatched in Step 0.
The only thing that varies is the list of files under `# Target`.**

---

## Authority

- `ontology.md` (repo root) is the spec. Every claim of "violation" cites
  the section that defines the rule.
- `translate_core/knowledge_graph.py` (post-update) is the only authorised
  writer. Anything that writes the KG outside its factory methods is a
  violation.

## Target

[varies per agent — list of files to audit, full paths]

## Task

For each function/class/module-level statement in your target files,
classify what it writes (or causes to be written) to the KG, and check
that against `ontology.md`.

A violation is any of:

1. Writes a node with `type` not in {`term`, `concept`,
   `translation_mapping`, `source_text`, `agent`, `institution`}.
2. Writes a forbidden node type {`tm_segment`, `collocation`, `domain`}
   (ontology §6).
3. Required field missing or empty on a write (per §2.1–§2.6 fields
   marked "Required for new writes" or "yes").
4. Field value outside the allowed set:
   - `agent.role` outside the §2.5 allowlist (14 values).
   - `institution.kind` outside the §2.6 allowlist (11 values).
   - `source_text.project_type` outside the §2.4.1 allowlist.
   - `concept` relation outside §3.4 (`extends|critiques|redefines|
     reappropriates|related_to`).
5. Creates an edge `relation` not in the §3 set of thirteen.
6. Creates a `cited_in` self-loop (§3.2 forbids them; the factory
   already drops these — verify the caller doesn't depend on the silent
   drop).
7. Bypasses a factory method (direct `kg.graph.add_node`,
   `kg.graph.add_edge`, direct JSON-write of `data/knowledge.db`, or
   any equivalent). Even one bypass is a HARD violation.
8. Agent write missing the O-12 quartet:
   `dedup_group`, `alt_spellings`, `all_roles`, `mention_count`.
9. Bilingual source_text written as two separate nodes instead of one
   merged record (§2.4.2 / O-5). Both `title_en` AND `title_sl` (or
   `title_orig` + `title_translation`) MUST be on the same node.
10. Container-type source_text written without a `translated_by` edge
    in the same transaction (O-20).
11. `verified=False` written on a `translation_mapping` that was
    previously `verified=True` (O-3 monotonicity).
12. `id` slug not produced by the §0 / §4-invariant-1 algorithm:
    NFKD strip combining → lowercase → re.sub(`[^a-zA-Z0-9]+`, `-`)
    → trim `-` → truncate 80 → fallback `"unknown"`.
13. VL prompt examples contain real names / publishers / works
    (§4 invariant 5).
14. Records below the §4 invariant 9 confidence threshold bypassing the
    review queue (`data/extraction_review.json`).

## Output

Write to `data/ontology_audit/<unit>.md` where `<unit>` is your agent's
unit name (given in the dispatch). Exact format:

```markdown
# Audit: <unit-name>

**Modules audited:**
- <path>
- <path>

## Compliant surface area

For each module, list the symbols that ARE compliant with a one-line
statement of what they write and which ontology section authorises it.

- `<module>:<symbol>` writes <node/edge type> per §<section>. Compliant.

## Violations

One entry per violation. No omissions, no consolidation.

- `<module>:<symbol>` lines <N>–<M> — <exact violation, ≤2 sentences> —
  violates §<section> "<verbatim rule fragment>".

## Repair notes

For each violation, the smallest change that would make it compliant.
If the smallest change requires deletion of the function (the function
exists solely to do the violating thing), say so.

- `<module>:<symbol>` — <smallest repair, ≤2 sentences>.

## Dead code

Functions / classes that are never called by any compliant caller. List
them — Step 1 will delete them.

- `<module>:<symbol>` — last referenced by <caller> which is itself
  non-compliant / removed. Recommend deletion.
```

## Prohibited

- Do NOT edit any code.
- Do NOT run tests, lint, or format.
- Do NOT read the live KG (`data/knowledge.db`).
- Do NOT speculate about what the ontology "probably" means — cite the
  section that contains the rule, or do not raise the violation.
- Do NOT consolidate violations to look cleaner. Every distinct write
  that violates is its own entry.
- Do NOT recommend "future improvements" or "best practices". The only
  output is compliance status vs the current ontology.
- Do NOT use emoji. Plain markdown.

## Verification rubric

I will reject any audit that:
- Lists a violation without an ontology section citation.
- Claims compliance for code that writes the KG without showing which
  factory method it calls.
- Skips functions because they "look obviously correct".
- Reorders or restructures the output template.
