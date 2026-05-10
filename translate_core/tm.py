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
        # Detect source language from TMX header srclang attribute
        raw = path.read_text(encoding="utf-8")
        srclang = "en"  # default assumption
        m = re.search(r'srclang\s*=\s*"([^"]+)"', raw)
        if m:
            srclang = m.group(1).lower().split("-")[0]  # "EN-GB" -> "en", "SL" -> "sl"

        with open(path, "rb") as f:
            tmx_file = tmxfile(f)
        for unit in tmx_file.unit_iter():
            src = clean_xml(unit.source)
            tgt = clean_xml(unit.target)
            if src and tgt:
                # Normalize: app convention is always EN source → SL target
                if srclang == "sl":
                    src, tgt = tgt, src

                self.entries.append(
                    {
                        "source": src,
                        "target": tgt,
                        "origin": str(path.name),
                        "source_lang": "en",
                        "target_lang": "sl",
                    }
                )

    def lookup_fuzzy(
        self, text: str, threshold: float = 90.0, limit: int = 3
    ) -> List[Dict]:
        """
        Fuzzy lookup in TM.
        Enforces a high threshold (default 90%) and penalizes extreme length differences.
        """
        sources = [e["source"] for e in self.entries if e["source"]]
        if not sources:
            return []

        # We use a slightly lower initial limit for process.extract to filter ourselves later
        matches = process.extract(text, sources, scorer=fuzz.ratio, limit=limit * 5)
        results = []
        input_len = len(text)

        for src, score, _ in matches:
            if score >= threshold:
                # Length check: avoid segments that are vastly different in length
                src_len = len(src)
                len_ratio = (
                    max(src_len, input_len) / min(src_len, input_len)
                    if min(src_len, input_len) > 0
                    else 10
                )

                if (
                    len_ratio > 2.5
                ):  # If one is more than 2.5x longer than the other, skip
                    continue

                for e in self.entries:
                    if e["source"] == src:
                        results.append({**e, "score": score})
                        break
            if len(results) >= limit:
                break
        return results

    def search_concordance(self, text: str, top_n: int = 5) -> List[Dict]:
        """
        Search for word matches.
        Penalizes suggestions that are much longer than the input text.
        """
        words = [w for w in text.split() if len(w) >= 2]
        if not words:
            return []

        input_len = len(text)
        scored_entries = []
        for entry in self.entries:
            src_text = entry["source"]
            tgt_text = entry["target"]
            # Search in both source and target
            count = sum(
                1
                for w in words
                if w.lower() in src_text.lower() or w.lower() in tgt_text.lower()
            )

            if count > 0:
                # Base relevance: percentage of query words found
                relevance = (count / len(words)) * 100

                # Length penalty: if hit is much longer than input, it's less relevant
                hit_len = len(src_text)
                len_penalty = 1.0
                if input_len > 0:
                    ratio = hit_len / input_len
                    if ratio > 2.0:
                        len_penalty = 0.6
                    if ratio > 4.0:
                        len_penalty = 0.3
                    if ratio > 10.0:
                        len_penalty = 0.0  # Ignore massive segments for tiny inputs

                final_relevance = relevance * len_penalty

                if final_relevance > 20:  # Slightly higher threshold for concordance
                    scored_entries.append({**entry, "relevance": final_relevance})

        scored_entries.sort(key=lambda x: x["relevance"], reverse=True)
        return scored_entries[:top_n]

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
