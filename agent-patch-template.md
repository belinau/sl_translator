# Ontology repair patch — agent instruction template

**Identical for every patch agent dispatched in Step 1. Only the target
file, the audit file to read, and the fixture-test path vary.**

---

## Authority

- `ontology.md` (repo root) is the spec.
- `data/ontology_audit/<unit>.md` (your unit's audit file) lists the
  violations you must fix and the repair notes for each.
- `translate_core/knowledge_graph.py` factory methods are the only
  authorised KG writers.

## Task

For the single file assigned to you:

1. Read `ontology.md` end-to-end.
2. Read `data/ontology_audit/<unit>.md` end-to-end. Every violation in
   the "Violations" section that names a function in YOUR file is part
   of this patch. Every repair note in "Repair notes" that names a
   function in YOUR file is your fix instruction.
3. Read the file you are patching, in full.
4. Apply the smallest possible change that satisfies each repair note.
   No formatting changes. No moving code around. No "while I'm here"
   improvements. No new abstractions.
5. Delete any dead-code symbol from your file that the audit's
   "Dead code" section names AND that has no caller in the repo (check
   with `search`).
6. Write a unit test (path given in your assignment) that exercises
   each fix. The test MUST fail against the pre-patch code and pass
   against the post-patch code. One assertion per violation fixed.
7. Run only the new test file: `.venv/bin/python -m pytest <test path> -v`.
   Do NOT run the full test suite — the caller verifies that.

## Constraints

- **Only edit your assigned file** and your assigned test file. Touching
  any other source file is a rejection condition.
- **Factory-method writes only.** Any `kg.G.add_edge` / `kg.G.add_node`
  / direct JSON write you ADD is a rejection condition.
- **No new prompts.** If the audit calls for a prompt change, the change
  is to the abstract placeholders or schema-as-example, NOT to add real
  names / publishers / works (§4 invariant 5).
- **Preserve the strict-JSON prompt format** for the 1.6B VL model. If
  you touch a VL prompt, output schema and prefill stay identical;
  only routing / refusal logic changes.
- **No project formatting passes** (black, ruff, isort). Don't touch
  line layout for unchanged lines.
- **No `print` debug noise.** If you add diagnostic logging, use the
  module's existing logger (or `sys.stderr` if that's the local
  convention).
- **No new dependencies** added to imports without a concrete need
  driven by an audit fix.

## Output

- The patched source file (in place).
- The new test file at the path given.
- A short JSON summary in your final reply:
  `{"file": "...", "violations_fixed": N, "tests_added": N, "dead_code_removed": [..], "test_run": "PASS"|"FAIL"}`

## Prohibited

- Running the full test suite.
- Running linters / formatters.
- Editing files outside your assignment.
- Adding "future improvement" TODOs.
- Renaming functions or arguments unless an audit repair note explicitly
  calls for it.
- Touching `ontology.md`, `agent-audit-template.md`, this template, or
  any `_summary.md`.
- Reading or writing `data/knowledge.db`.

## Verification rubric (I will reject if any of these fail)

- A diff line touches a file outside your assignment.
- A `kg.G.add_edge` or `kg.G.add_node` you introduced remains in the
  patched file.
- Any violation in your unit audit's "Violations" section is NOT
  addressed by your patch and is NOT explicitly deferred with a one-line
  justification in your final reply.
- The new test runs but does not exercise the specific fix.
- The new test imports `data/knowledge.db` (it must use an in-memory
  `KnowledgeGraph()` instance only).
- You added a real name / publisher / work to a VL prompt example.
