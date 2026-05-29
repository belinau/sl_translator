# docs/vl_eval/golden/README.md

Golden Markdown files for VL parser evaluation.

Each golden file is the hand-cleaned expected Markdown output for the
corresponding fixture PDF. The `diff.py` tool compares VL parser output
against these golden files and reports a token-level fuzzy match score.

## Usage

```bash
python docs/vl_eval/diff.py --golden docs/vl_eval/golden/sample.md --actual <path_to_parser_output>
```

A score >= 95% is the target threshold for the live integration test
`test_live_end_to_end_5_pages`.