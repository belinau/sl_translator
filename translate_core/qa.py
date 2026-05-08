# translate_core/qa.py

import re
from typing import List, Dict

class QAEngine:
    def __init__(self):
        pass

    def check_segment(self, source: str, target: str, glossary_hits: List[Dict] = None) -> List[Dict]:
        """
        Runs multiple QA checks on a segment.
        Returns a list of warnings: {"type": "warning|error", "message": "..."}
        """
        warnings = []
        if not target.strip():
            return warnings

        # 1. Number Mismatch
        src_nums = re.findall(r'\d+', source)
        tgt_nums = re.findall(r'\d+', target)
        if set(src_nums) != set(tgt_nums):
            missing = set(src_nums) - set(tgt_nums)
            extra = set(tgt_nums) - set(src_nums)
            msg = "Number mismatch."
            if missing: msg += f" Missing: {', '.join(missing)}."
            if extra: msg += f" Extra: {', '.join(extra)}."
            warnings.append({"type": "warning", "message": msg})

        # 2. Glossary Discrepancy
        if glossary_hits:
            for g in glossary_hits:
                src_term = g['source_term']
                tgt_term = g['target_term']
                # Case-insensitive check if src_term is in source
                if re.search(re.escape(src_term), source, re.IGNORECASE):
                    # Check if tgt_term is in target
                    if not re.search(re.escape(tgt_term), target, re.IGNORECASE):
                        warnings.append({
                            "type": "error",
                            "message": f"Glossary violation: '{src_term}' should be translated as '{tgt_term}'."
                        })

        # 3. Basic Punctuation/Formatting
        if source.endswith(('.', '!', '?')) and not target.endswith(('.', '!', '?')):
             warnings.append({"type": "warning", "message": "Source ends with punctuation, target does not."})
        elif not source.endswith(('.', '!', '?')) and target.endswith(('.', '!', '?')):
             warnings.append({"type": "warning", "message": "Target ends with punctuation, source does not."})

        return warnings
