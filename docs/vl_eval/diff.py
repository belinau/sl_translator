#!/usr/bin/env python3
"""
docs/vl_eval/diff.py — Token-level fuzzy diff between VL parser output and golden Markdown.

Usage:
    python docs/vl_eval/diff.py --golden golden/sample.md --actual /tmp/parser_output.md
"""

import argparse
import sys
from difflib import SequenceMatcher


def tokenize(text: str) -> list[str]:
    """Simple whitespace+punctuation tokenizer."""
    import re
    return re.findall(r'\w+|[^\w\s]', text.lower())


def fuzzy_diff(golden_path: str, actual_path: str) -> float:
    golden_text = open(golden_path, encoding='utf-8').read()
    actual_text = open(actual_path, encoding='utf-8').read()

    golden_tokens = tokenize(golden_text)
    actual_tokens = tokenize(actual_text)

    ratio = SequenceMatcher(None, golden_tokens, actual_tokens).ratio()

    # Show offending lines
    golden_lines = golden_text.splitlines()
    actual_lines = actual_text.splitlines()
    sm = SequenceMatcher(None, golden_lines, actual_lines)
    diffs = []
    for op in sm.get_opcodes():
        tag, i1, i2, j1, j2 = op
        if tag != 'equal':
            diffs.append(f"  {tag}: golden[{i1}:{i2}] vs actual[{j1}:{j2}]")
            if tag in ('replace', 'delete'):
                for line in golden_lines[i1:i2]:
                    diffs.append(f"    - {line}")
            if tag in ('replace', 'insert'):
                for line in actual_lines[j1:j2]:
                    diffs.append(f"    + {line}")

    if diffs:
        print(f"\nTop differences (showing first 30):")
        for d in diffs[:30]:
            print(d)

    return ratio


def main():
    ap = argparse.ArgumentParser(description="Fuzzy diff for VL parser evaluation")
    ap.add_argument("--golden", required=True, help="Path to golden Markdown file")
    ap.add_argument("--actual", required=True, help="Path to actual parser output")
    args = ap.parse_args()

    score = fuzzy_diff(args.golden, args.actual)
    print(f"\nToken-level fuzzy match: {score:.1%}")
    if score >= 0.95:
        print("PASS (>= 95%)")
    else:
        print(f"FAIL (< 95%)")
        sys.exit(1)


if __name__ == "__main__":
    main()