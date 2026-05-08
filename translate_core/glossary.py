# translate_core/glossary.py

import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List

from flashtext import KeywordProcessor

import config


class Glossary:
    """
    Bilingual glossary loader supporting:
    1. TBX / XML (robustly finds <termEntry> tags anywhere in the file)
    2. TSV (Tab-Separated Values: source\ttarget)
    3. CSV (Comma-Separated with headers)
    """

    def __init__(self, glossary_dir: Path = config.GLOSSARY_DIR):
        self.glossary_dir = Path(glossary_dir)
        self.entries: List[Dict] = []
        self._index_by_lang: Dict[tuple, KeywordProcessor] = {}
        self._load_all()

    def _load_all(self):
        if not self.glossary_dir.exists():
            return

        # Iterate over all files in the glossary directory
        for p in self.glossary_dir.glob("*"):
            if not p.is_file():
                continue

            # Decide parsing strategy based on content/extension
            content = p.read_text(encoding="utf-8-sig")  # utf-8-sig handles the ﻿ BOM

            if "<termEntry" in content:
                # It's a TBX/XML file (or contains it)
                self._parse_tbx_content(content, p.name)

            elif p.suffix == ".csv":
                self._load_csv(p)

            elif "\t" in content and "<" not in content:
                # Simple TSV file
                self._parse_tsv(content, p.name)

        # Build the fast lookup indices
        self._build_indices()

    def _parse_tbx_content(self, content: str, filename: str):
        """
        Parses TBX XML.
        Uses regex to find <termEntry> blocks to handle malformed XML
        (like tags appearing after the closing root element).
        """
        # Regex to find <termEntry>...</termEntry> blocks
        # re.DOTALL allows the dot to match newlines
        pattern = re.compile(r"<termEntry>(.*?)</termEntry>", re.DOTALL)
        matches = pattern.findall(content)

        for entry_xml in matches:
            try:
                # Parse the isolated XML fragment
                # We wrap it in a dummy root just in case, though fromstring handles fragments
                entry_node = ET.fromstring(f"<termEntry>{entry_xml}</termEntry>")

                terms = {}  # Dictionary to hold {'en': 'word', 'sl': 'beseda'}
                note = ""

                # 1. Extract Languages and Terms
                # Structure: <langSet xml:lang="en"><tig><term>Word</term></tig></langSet>
                for lang_set in entry_node.findall("langSet"):
                    # Get language code (handling xml: namespace)
                    lang = lang_set.get(
                        "{http://www.w3.org/XML/1998/namespace}lang"
                    ) or lang_set.get("lang")

                    # Find the term
                    term_node = lang_set.find(".//term")
                    if lang and term_node is not None and term_node.text:
                        terms[lang] = term_node.text.strip()

                # 2. Extract Notes (Descriptions)
                # <descrip type="DESCRIPTION">Note text</descrip>
                descrip_node = entry_node.find(".//descrip")
                if descrip_node is not None and descrip_node.text:
                    note = descrip_node.text.strip()

                # 3. Store Entry (EN -> SL)
                if "en" in terms and "sl" in terms:
                    self.entries.append(
                        {
                            "source_term": terms["en"],
                            "target_term": terms["sl"],
                            "source_lang": "en",
                            "target_lang": "sl",
                            "note": note,
                            "origin": filename,
                        }
                    )
                    # Store reverse (SL -> EN)
                    self.entries.append(
                        {
                            "source_term": terms["sl"],
                            "target_term": terms["en"],
                            "source_lang": "sl",
                            "target_lang": "en",
                            "note": note,
                            "origin": filename,
                        }
                    )

            except Exception as e:
                # Skip malformed entries
                continue

        # Handle TSV lines that might be at the bottom of an XML file (mixed format)
        # We look for lines with tabs that were not part of XML tags
        lines = content.split("\n")
        for line in lines:
            if "<" in line or ">" in line:
                continue
            if "\t" in line:
                parts = line.split("\t")
                if len(parts) >= 2:
                    src, tgt = parts[0].strip(), parts[1].strip()
                    if src and tgt:
                        self._add_simple_entry(src, tgt, "en", "sl", filename)

    def _parse_tsv(self, content: str, filename: str):
        """Parses simple Tab-Separated files."""
        lines = content.split("\n")
        for line in lines:
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                src, tgt = parts[0].strip(), parts[1].strip()
                if src and tgt:
                    self._add_simple_entry(src, tgt, "en", "sl", filename)

    def _load_csv(self, path: Path):
        """Parses CSV files."""
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                st = row.get("source_term") or row.get("source") or ""
                tt = row.get("target_term") or row.get("target") or ""
                sl = row.get("source_lang") or row.get("sl") or "en"
                tl = row.get("target_lang") or row.get("tl") or "sl"
                note = row.get("note") or ""

                if not (st and tt):
                    continue

                self._add_simple_entry(st, tt, sl, tl, path.name, note)

    def _add_simple_entry(self, src, tgt, src_lang, tgt_lang, origin, note=""):
        """Helper to add entries in both directions."""
        self.entries.append(
            {
                "source_term": src,
                "target_term": tgt,
                "source_lang": src_lang,
                "target_lang": tgt_lang,
                "note": note,
                "origin": origin,
            }
        )
        # Reverse
        self.entries.append(
            {
                "source_term": tgt,
                "target_term": src,
                "source_lang": tgt_lang,
                "target_lang": src_lang,
                "note": note,
                "origin": origin,
            }
        )

    def _build_indices(self):
        """Builds FlashText indices for fast lookup."""
        # Group by (source_lang, target_lang)
        temp: Dict[tuple, List[str]] = {}
        for e in self.entries:
            key = (e["source_lang"], e["target_lang"])
            temp.setdefault(key, []).append(e["source_term"])

        for key, terms in temp.items():
            kp = KeywordProcessor()
            kp.add_keywords_from_list(terms)
            self._index_by_lang[key] = kp

    def lookup_terms(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> List[Dict]:
        """
        Find glossary terms that appear in `text` for the given language pair.
        Returns full entries.
        """
        kp = self._index_by_lang.get((source_lang, target_lang))
        if not kp:
            return []

        extracted = kp.extract_keywords(text)
        results = []
        # Use a set to avoid duplicates if the same term appears twice in text
        found_terms = set()

        for term in extracted:
            if term in found_terms:
                continue
            found_terms.add(term)

            # Find the corresponding entry data
            # Note: In case of duplicate source terms, we take the first match found
            for e in self.entries:
                if (
                    e["source_term"] == term
                    and e["source_lang"] == source_lang
                    and e["target_lang"] == target_lang
                ):
                    results.append(e)
                    break
        return results
    def search_prefix(self, prefix: str, source_lang: str, target_lang: str) -> List[str]:
        """
        Finds target terms whose source term (or target term itself in editing mode) 
        starts with the given prefix.
        """
        if not prefix: return []
        prefix_low = prefix.lower()
        matches = []
        for e in self.entries:
            if e["source_lang"] == source_lang and e["target_lang"] == target_lang:
                if e["source_term"].lower().startswith(prefix_low) or e["target_term"].lower().startswith(prefix_low):
                    matches.append(e["source_term"])
                    matches.append(e["target_term"])
        return list(set(matches)) # Unique results
