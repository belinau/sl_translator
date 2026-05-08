# translate_core/tm.py

import html
import re
from pathlib import Path
from typing import Dict, List

from rapidfuzz import fuzz, process
from translate.storage.tmx import tmxfile

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
        self.entries: List[Dict[str, str]] = []
        self._load_all()

    def _load_all(self):
        if not self.tm_dir.exists():
            self.tm_dir.mkdir(parents=True, exist_ok=True)
            return
        for p in self.tm_dir.glob("*.tmx"):
            self._load_tmx(p)

    def _load_tmx(self, path: Path):
        with open(path, "rb") as f:
            tmx_file = tmxfile(f)
        for unit in tmx_file.unit_iter():
            src = clean_xml(unit.source)
            tgt = clean_xml(unit.target)
            if src and tgt:
                self.entries.append(
                    {
                        "source": src,
                        "target": tgt,
                        "origin": str(path.name),
                    }
                )

    def lookup_fuzzy(
        self, text: str, threshold: float = 75.0, limit: int = 3
    ) -> List[Dict]:
        sources = [e["source"] for e in self.entries if e["source"]]
        if not sources:
            return []

        matches = process.extract(text, sources, scorer=fuzz.ratio, limit=limit)
        results = []
        for src, score, _ in matches:
            if score >= threshold:
                for e in self.entries:
                    if e["source"] == src:
                        results.append({**e, "score": score})
                        break
        return results

    def search_concordance(self, text: str, top_n: int = 5) -> List[Dict]:
        words = [w for w in text.split() if len(w) >= 2]
        if not words:
            return []

        scored_entries = []
        for entry in self.entries:
            src_text = entry["source"]
            tgt_text = entry["target"]
            # Search in both source and target
            count = sum(1 for w in words if w.lower() in src_text.lower() or w.lower() in tgt_text.lower())

            if count > 0:
                relevance = (count / len(words)) * 100
                if relevance > 10:
                    scored_entries.append({**entry, "relevance": relevance})

        scored_entries.sort(key=lambda x: x["relevance"], reverse=True)
        return scored_entries[:top_n]
    def search_prefix(self, prefix: str) -> List[str]:
        """
        Quickly find completions starting with the given prefix. 
        Limits results to short phrases (max 3 words) to avoid 'sausage' predictions.
        """
        if not prefix or len(prefix) < 2: return []
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
            if len(matches) > 10: break
        return list(set(matches))
