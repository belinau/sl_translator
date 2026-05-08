# translate_core/knowledge_graph.py
#
# KG v2 — Rich schema for professional translation systems
#
# Node types:
#   term        — language-specific surface form (+ lemma, pos, variants, frequency)
#   concept     — language-independent semantic anchor (+ domain, definition, external_ids)
#   collocation — multi-word expression (+ component_terms, frequency)
#   tm_segment  — actual TM segment linked to term nodes (source/target text + quality)
#   domain      — subject matter taxonomy node
#
# Edge types (relation field):
#   instantiates_concept  — term -> concept
#   translates_to         — term:en -> term:sl  (+ confidence, verified)
#   has_variant           — term -> term (inflection / case form)
#   hyponym_of            — IS-A hierarchy
#   contains_term         — collocation -> term
#   appears_in_segment    — term -> tm_segment
#   yields_term           — tm_segment -> term
#   belongs_to_domain     — term/concept -> domain
#   related_to            — generic fallback

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
from flashtext import KeywordProcessor

import config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase + strip accents — used for building the lookup index."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _tokenize_simple(text: str) -> List[str]:
    """
    Lightweight tokenizer that handles both EN and SL without external deps.
    Returns lowercase tokens ≥3 chars, stripped of punctuation.
    Falls back gracefully when classla / spaCy are not installed.
    """
    tokens = re.findall(r"[\wčšžČŠŽ]{3,}", text.lower())
    # De-duplicate while preserving order
    seen: set = set()
    out = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ---------------------------------------------------------------------------
# KnowledgeGraph
# ---------------------------------------------------------------------------

class KnowledgeGraph:
    """
    Rich bilingual knowledge graph stored as NetworkX DiGraph + JSON.

    Quick-start
    -----------
    kg = KnowledgeGraph()
    kg.seed_from_tm(tm.entries)   # populate from existing TM
    kg.save()

    entity_hits = kg.extract_entities(source_text, domain_hint="agriculture")
    # → list of rich dicts ready to inject into LLM prompt
    """

    # ------------------------------------------------------------------
    # Construction / persistence
    # ------------------------------------------------------------------

    def __init__(self, db_path: pathlib.Path = config.KG_DB_PATH):
        self.db_path = pathlib.Path(db_path)
        self.G: nx.DiGraph = nx.DiGraph()

        # Primary lookup: exact surface form → node_id
        self._exact_kp = KeywordProcessor(case_sensitive=False)
        # Secondary lookup: normalized form → node_id  (handles SL accents)
        self._norm_kp  = KeywordProcessor(case_sensitive=False)

        self._load()

    def _load(self):
        if not self.db_path.exists():
            return
        try:
            raw = json.loads(self.db_path.read_text(encoding="utf-8"))
            for node in raw.get("nodes", []):
                self.G.add_node(node["id"], **node)
                if node.get("type") == "term":
                    self._index_term_node(node["id"], node)
            for edge in raw.get("edges", []):
                self.G.add_edge(
                    edge["source"],
                    edge["target"],
                    **{k: v for k, v in edge.items() if k not in ("source", "target")},
                )
        except Exception as exc:
            print(f"[KG] Warning: could not load graph — starting fresh. ({exc})")
            self.G.clear()

    def save(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "nodes": [{"id": n, **self.G.nodes[n]} for n in self.G.nodes],
            "edges": [
                {"source": u, "target": v, **self.G.edges[u, v]}
                for u, v in self.G.edges
            ],
        }
        self.db_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _index_term_node(self, node_id: str, data: Dict):
        """Register all surface forms of a term node in both KPs."""
        term = data.get("term", "")
        if term:
            self._exact_kp.add_keyword(term, node_id)
            self._norm_kp.add_keyword(_normalize(term), node_id)
        for variant in data.get("variants", []):
            if variant:
                self._exact_kp.add_keyword(variant, node_id)
                self._norm_kp.add_keyword(_normalize(variant), node_id)

    def _rebuild_indices(self):
        """Full rebuild — call after bulk operations."""
        self._exact_kp = KeywordProcessor(case_sensitive=False)
        self._norm_kp  = KeywordProcessor(case_sensitive=False)
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "term":
                self._index_term_node(node_id, data)

    # ------------------------------------------------------------------
    # Node / edge factories
    # ------------------------------------------------------------------

    def add_concept_node(
        self,
        concept_id: str,
        label: str,
        domain: str = "",
        definition: str = "",
        external_ids: Dict[str, str] = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        if not self.G.has_node(concept_id):
            self.G.add_node(
                concept_id,
                id=concept_id,
                type="concept",
                label=label,
                domain=domain,
                definition=definition,
                external_ids=external_ids or {},
                **(metadata or {}),
            )
        return concept_id

    def add_term_node(
        self,
        term: str,
        lang: str,
        concept_id: str = None,
        lemma: str = "",
        pos: str = "",
        domain: str = "",
        register: str = "neutral",
        variants: List[str] = None,
        confidence: float = 0.8,
        source: str = "manual",
        metadata: Dict[str, Any] = None,
    ) -> str:
        node_id = f"term:{lang}:{term.lower()}"
        if not self.G.has_node(node_id):
            node_data: Dict[str, Any] = dict(
                id=node_id,
                type="term",
                term=term,
                lemma=lemma or term,
                lang=lang,
                pos=pos,
                domain=domain,
                register=register,
                variants=variants or [],
                confidence=confidence,
                frequency=1,
                source=source,
                **(metadata or {}),
            )
            self.G.add_node(node_id, **node_data)
            self._index_term_node(node_id, node_data)
        else:
            # Increment frequency on re-encounter
            self.G.nodes[node_id]["frequency"] = (
                self.G.nodes[node_id].get("frequency", 1) + 1
            )
            # Merge in new variants
            existing = set(self.G.nodes[node_id].get("variants", []))
            for v in (variants or []):
                if v and v not in existing:
                    existing.add(v)
                    self._exact_kp.add_keyword(v, node_id)
                    self._norm_kp.add_keyword(_normalize(v), node_id)
            self.G.nodes[node_id]["variants"] = list(existing)

        if concept_id and self.G.has_node(concept_id):
            if not self.G.has_edge(node_id, concept_id):
                self.G.add_edge(node_id, concept_id, relation="instantiates_concept")

        return node_id

    def add_collocation_node(
        self,
        phrase: str,
        lang: str,
        component_ids: List[str] = None,
        domain: str = "",
        frequency: int = 1,
    ) -> str:
        safe_key = re.sub(r"\s+", "_", phrase.lower())
        node_id = f"coll:{lang}:{safe_key}"
        if not self.G.has_node(node_id):
            self.G.add_node(
                node_id,
                id=node_id,
                type="collocation",
                phrase=phrase,
                lang=lang,
                domain=domain,
                frequency=frequency,
            )
            self._exact_kp.add_keyword(phrase, node_id)
            self._norm_kp.add_keyword(_normalize(phrase), node_id)
        else:
            self.G.nodes[node_id]["frequency"] += 1

        for cid in (component_ids or []):
            if self.G.has_node(cid) and not self.G.has_edge(node_id, cid):
                self.G.add_edge(node_id, cid, relation="contains_term")
        return node_id

    def add_segment_node(
        self,
        seg_id: str,
        source_text: str,
        target_text: str,
        source_lang: str,
        target_lang: str,
        origin: str = "",
        quality_score: float = 1.0,
    ) -> str:
        if not self.G.has_node(seg_id):
            self.G.add_node(
                seg_id,
                id=seg_id,
                type="tm_segment",
                source_text=source_text,
                target_text=target_text,
                source_lang=source_lang,
                target_lang=target_lang,
                origin=origin,
                quality_score=quality_score,
            )
        return seg_id

    def add_domain_node(self, domain_id: str, label: str, parent_id: str = "") -> str:
        if not self.G.has_node(domain_id):
            self.G.add_node(
                domain_id,
                id=domain_id,
                type="domain",
                label=label,
                parent=parent_id,
            )
        if parent_id and self.G.has_node(parent_id):
            if not self.G.has_edge(domain_id, parent_id):
                self.G.add_edge(domain_id, parent_id, relation="subclass_of")
        return domain_id

    def link_translations(
        self,
        src_term_id: str,
        tgt_term_id: str,
        confidence: float = 0.8,
        verified: bool = False,
        source: str = "auto",
    ):
        """Bidirectional translation edge with confidence."""
        if self.G.has_node(src_term_id) and self.G.has_node(tgt_term_id):
            # Forward
            if self.G.has_edge(src_term_id, tgt_term_id):
                # Increase confidence if we see it again
                old = self.G.edges[src_term_id, tgt_term_id].get("confidence", 0.5)
                self.G.edges[src_term_id, tgt_term_id]["confidence"] = min(
                    0.99, old + (1 - old) * 0.1
                )
            else:
                self.G.add_edge(
                    src_term_id,
                    tgt_term_id,
                    relation="translates_to",
                    confidence=confidence,
                    verified=verified,
                    source=source,
                )
            # Reverse
            if not self.G.has_edge(tgt_term_id, src_term_id):
                self.G.add_edge(
                    tgt_term_id,
                    src_term_id,
                    relation="translates_to",
                    confidence=confidence,
                    verified=verified,
                    source=source,
                )

    def add_variant(self, term_id: str, variant: str):
        """Register a surface variant (e.g. inflected form) for an existing term node."""
        if not self.G.has_node(term_id):
            return
        variants = self.G.nodes[term_id].get("variants", [])
        if variant not in variants:
            variants.append(variant)
            self.G.nodes[term_id]["variants"] = variants
            self._exact_kp.add_keyword(variant, term_id)
            self._norm_kp.add_keyword(_normalize(variant), term_id)

    # ------------------------------------------------------------------
    # TM-driven population  (the main population path)
    # ------------------------------------------------------------------

    def seed_from_tm(
        self,
        tm_entries: List[Dict],
        source_lang: str = "en",
        target_lang: str = "sl",
        min_token_len: int = 3,
        max_phrase_words: int = 4,
    ):
        """
        Walk all TM segments, extract frequent tokens/phrases, and wire them
        into the KG with frequency counts and segment provenance.

        After this call you should call kg.save().
        """
        # --- Pass 1: count token/phrase frequency across entire corpus --------
        src_freq: Counter = Counter()
        tgt_freq: Counter = Counter()
        src_bigrams: Counter = Counter()
        tgt_bigrams: Counter = Counter()

        for entry in tm_entries:
            src_tokens = _tokenize_simple(entry.get("source", ""))
            tgt_tokens = _tokenize_simple(entry.get("target", ""))

            for t in src_tokens:
                if len(t) >= min_token_len:
                    src_freq[t] += 1
            for t in tgt_tokens:
                if len(t) >= min_token_len:
                    tgt_freq[t] += 1

            # Bigrams (2-word MWEs)
            for i in range(len(src_tokens) - 1):
                bg = f"{src_tokens[i]} {src_tokens[i+1]}"
                src_bigrams[bg] += 1
            for i in range(len(tgt_tokens) - 1):
                bg = f"{tgt_tokens[i]} {tgt_tokens[i+1]}"
                tgt_bigrams[bg] += 1

        # --- Pass 2: build nodes for sufficiently frequent terms ---------------
        # Only include tokens that appear ≥2 times (avoid hapax legomena noise)
        significant_src = {t for t, c in src_freq.items() if c >= 2}
        significant_tgt = {t for t, c in tgt_freq.items() if c >= 2}
        significant_src_bi = {b for b, c in src_bigrams.items() if c >= 2}
        significant_tgt_bi = {b for b, c in tgt_bigrams.items() if c >= 2}

        # Build term nodes with frequency
        for token, freq in src_freq.items():
            if token in significant_src:
                nid = f"term:{source_lang}:{token}"
                if not self.G.has_node(nid):
                    self.add_term_node(token, source_lang, source="tm_seed")
                self.G.nodes[nid]["frequency"] = freq

        for token, freq in tgt_freq.items():
            if token in significant_tgt:
                nid = f"term:{target_lang}:{token}"
                if not self.G.has_node(nid):
                    self.add_term_node(token, target_lang, source="tm_seed")
                self.G.nodes[nid]["frequency"] = freq

        # Bigram collocation nodes
        for phrase, freq in src_bigrams.items():
            if phrase in significant_src_bi:
                self.add_collocation_node(phrase, source_lang, frequency=freq)
        for phrase, freq in tgt_bigrams.items():
            if phrase in significant_tgt_bi:
                self.add_collocation_node(phrase, target_lang, frequency=freq)

        # --- Pass 3: segment nodes + provenance edges + co-occurrence pairs ----
        # We accumulate co-occurring (src_token, tgt_token) pairs to estimate
        # translation probability via pointwise mutual information (PMI) proxy.
        cooccur: Counter = Counter()

        for i, entry in enumerate(tm_entries):
            src_text = entry.get("source", "")
            tgt_text = entry.get("target", "")
            origin   = entry.get("origin", "tm")
            seg_id   = f"seg:{origin}:{i}"

            self.add_segment_node(
                seg_id, src_text, tgt_text, source_lang, target_lang,
                origin=origin, quality_score=1.0,
            )

            src_tokens = [t for t in _tokenize_simple(src_text) if t in significant_src]
            tgt_tokens = [t for t in _tokenize_simple(tgt_text) if t in significant_tgt]

            for st in src_tokens:
                sid = f"term:{source_lang}:{st}"
                if self.G.has_node(sid) and not self.G.has_edge(sid, seg_id):
                    self.G.add_edge(sid, seg_id, relation="appears_in_segment")

            for tt in tgt_tokens:
                tid = f"term:{target_lang}:{tt}"
                if self.G.has_node(tid) and not self.G.has_edge(seg_id, tid):
                    self.G.add_edge(seg_id, tid, relation="yields_term")

            # Co-occurrence: every (src_token, tgt_token) pair in this segment
            for st in src_tokens:
                for tt in tgt_tokens:
                    cooccur[(st, tt)] += 1

        # --- Pass 4: derive high-confidence translation edges via PMI ----------
        # Simple heuristic: if cooccur(s,t) / freq(s) >= 0.4 it's likely a translation
        for (st, tt), count in cooccur.items():
            if count < 2:
                continue
            src_f = src_freq.get(st, 1)
            confidence = min(0.95, count / src_f)
            if confidence >= 0.35:
                sid = f"term:{source_lang}:{st}"
                tid = f"term:{target_lang}:{tt}"
                self.link_translations(sid, tid, confidence=confidence, source="tm_pmi")

        print(
            f"[KG] seed_from_tm complete: "
            f"{self.G.number_of_nodes()} nodes, "
            f"{self.G.number_of_edges()} edges"
        )

    # ------------------------------------------------------------------
    # Lookup / entity extraction
    # ------------------------------------------------------------------

    def extract_entities(
        self,
        text: str,
        domain_hint: str = None,
        target_lang: str = "sl",
    ) -> List[Dict]:
        """
        Find KG entities in *text*.
        Returns rich dicts ready to inject into the LLM system prompt.

        Each result contains:
          - All standard node fields (term, lang, domain, frequency…)
          - 'translations': ranked list of translation candidates
          - 'example_segments': up to 2 TM segments containing this term
          - 'domain_match': bool (True when node domain matches domain_hint)
        """
        # Match via exact KP first, then normalized KP for accented variants
        found_ids: set = set(self._exact_kp.extract_keywords(text))
        found_ids |= set(self._norm_kp.extract_keywords(_normalize(text)))

        results = []
        for node_id in found_ids:
            if not self.G.has_node(node_id):
                continue
            node_data = dict(self.G.nodes[node_id])

            # Domain scoring
            if domain_hint:
                node_data["domain_match"] = (
                    node_data.get("domain", "") == domain_hint
                )
            else:
                node_data["domain_match"] = False

            # Translation candidates
            node_data["translations"] = self._get_translations(
                node_id, target_lang=target_lang
            )

            # Exemplar TM segments
            node_data["example_segments"] = self._get_example_segments(node_id, limit=2)

            results.append(node_data)

        # Sort: domain matches first, then by frequency desc
        results.sort(
            key=lambda x: (-int(x.get("domain_match", False)), -x.get("frequency", 0))
        )
        return results

    def _get_translations(
        self, term_id: str, target_lang: str = "sl"
    ) -> List[Dict]:
        """
        Return ranked translation candidates for *term_id*.

        Strategy:
        1. Direct 'translates_to' edges → highest confidence
        2. Concept-pivot (term → concept ← sibling term in target_lang)
        3. TM co-occurrence path (term → segment → target term)
        """
        candidates: List[Dict] = []
        seen_terms: set = set()

        def _add(term: str, lang: str, confidence: float, verified: bool, path: str):
            key = (term, lang)
            if key in seen_terms:
                # Boost confidence if we see same candidate via multiple paths
                for c in candidates:
                    if c["term"] == term and c["lang"] == lang:
                        c["confidence"] = min(0.99, c["confidence"] + 0.05)
                return
            seen_terms.add(key)
            candidates.append(
                {
                    "term": term,
                    "lang": lang,
                    "confidence": confidence,
                    "verified": verified,
                    "path": path,
                }
            )

        if not self.G.has_node(term_id):
            return candidates

        # 1. Direct translation edges
        for _, tgt_id, edata in self.G.out_edges(term_id, data=True):
            if edata.get("relation") == "translates_to":
                t_data = self.G.nodes.get(tgt_id, {})
                if t_data.get("lang") == target_lang:
                    _add(
                        t_data.get("term", tgt_id),
                        target_lang,
                        edata.get("confidence", 0.5),
                        edata.get("verified", False),
                        "direct",
                    )

        # 2. Concept pivot
        for _, concept_id, edata in self.G.out_edges(term_id, data=True):
            if edata.get("relation") == "instantiates_concept":
                for _, sibling_id, _ in self.G.out_edges(concept_id, data=True):
                    if sibling_id == term_id:
                        continue
                    s_data = self.G.nodes.get(sibling_id, {})
                    if (
                        s_data.get("type") == "term"
                        and s_data.get("lang") == target_lang
                    ):
                        _add(
                            s_data.get("term", sibling_id),
                            target_lang,
                            s_data.get("confidence", 0.4),
                            False,
                            "concept_pivot",
                        )

        # 3. TM co-occurrence path (term → segment → target term)
        for _, seg_id, _ in self.G.out_edges(term_id, data=True):
            seg_data = self.G.nodes.get(seg_id, {})
            if seg_data.get("type") != "tm_segment":
                continue
            for _, tgt_term_id, ydata in self.G.out_edges(seg_id, data=True):
                if ydata.get("relation") != "yields_term":
                    continue
                t_data = self.G.nodes.get(tgt_term_id, {})
                if t_data.get("lang") == target_lang:
                    # Use the edge confidence if present, else frequency-based estimate
                    edge_conf = 0.0
                    if self.G.has_edge(term_id, tgt_term_id):
                        edge_conf = self.G.edges[term_id, tgt_term_id].get(
                            "confidence", 0.0
                        )
                    if edge_conf >= 0.35:
                        _add(
                            t_data.get("term", tgt_term_id),
                            target_lang,
                            edge_conf,
                            False,
                            "tm_cooccur",
                        )

        return sorted(candidates, key=lambda x: -x["confidence"])

    def _get_example_segments(self, term_id: str, limit: int = 2) -> List[Dict]:
        """Return TM segment nodes that contain this term."""
        segments = []
        for _, seg_id, edata in self.G.out_edges(term_id, data=True):
            if edata.get("relation") == "appears_in_segment":
                seg_data = self.G.nodes.get(seg_id, {})
                if seg_data.get("type") == "tm_segment":
                    segments.append(
                        {
                            "source": seg_data.get("source_text", ""),
                            "target": seg_data.get("target_text", ""),
                            "quality": seg_data.get("quality_score", 1.0),
                        }
                    )
                if len(segments) >= limit:
                    break
        return segments

    # ------------------------------------------------------------------
    # Editing-experience helpers
    # ------------------------------------------------------------------

    def get_inline_hints(
        self,
        partial_target: str,
        source_text: str,
        source_lang: str = "en",
        target_lang: str = "sl",
        max_hints: int = 6,
    ) -> List[Dict]:
        """
        Real-time inline hints for the translation editor.

        Given what the translator has typed so far (partial_target) and the
        source segment, return ranked term-completion candidates drawn from:
          - KG term nodes in target_lang that translate source entities
          - Collocation nodes whose phrase starts with the last partial word

        Return format:
          [{"term": str, "confidence": float, "source_term": str, "type": "kg|coll"}]
        """
        results: List[Dict] = []
        seen: set = set()

        # Identify last partial word the translator is typing
        words = partial_target.rstrip().split()
        last_word = words[-1].lower() if words else ""

        # 1. Source entities → their translation candidates
        src_entities = self.extract_entities(
            source_text, target_lang=target_lang
        )
        for entity in src_entities:
            src_term = entity.get("term", "")
            for t in entity.get("translations", []):
                candidate = t["term"]
                if candidate in seen:
                    continue
                # Only surface candidates that complete the current partial word
                if last_word and not candidate.lower().startswith(last_word):
                    continue
                seen.add(candidate)
                results.append(
                    {
                        "term": candidate,
                        "confidence": t["confidence"],
                        "source_term": src_term,
                        "type": "kg_translation",
                        "verified": t.get("verified", False),
                    }
                )

        # 2. Collocation completions
        if last_word and len(last_word) >= 2:
            for node_id, data in self.G.nodes(data=True):
                if data.get("type") == "collocation" and data.get("lang") == target_lang:
                    phrase = data.get("phrase", "")
                    if phrase.lower().startswith(last_word):
                        if phrase not in seen:
                            seen.add(phrase)
                            results.append(
                                {
                                    "term": phrase,
                                    "confidence": min(
                                        0.9,
                                        data.get("frequency", 1) / 10,
                                    ),
                                    "source_term": "",
                                    "type": "collocation",
                                    "verified": False,
                                }
                            )

        results.sort(key=lambda x: -x["confidence"])
        return results[:max_hints]

    def get_term_tooltip(
        self,
        term: str,
        source_lang: str = "en",
        target_lang: str = "sl",
    ) -> Optional[Dict]:
        """
        Return rich tooltip data for a term underlined in the source panel.

        Includes: known translations ranked by confidence, domain, definition,
        and up to 2 example TM segments.
        """
        node_id = f"term:{source_lang}:{term.lower()}"

        # Also try via lookup (handles variants)
        if not self.G.has_node(node_id):
            found = self._exact_kp.extract_keywords(term)
            if found:
                node_id = found[0]
            else:
                return None

        if not self.G.has_node(node_id):
            return None

        data = dict(self.G.nodes[node_id])
        data["translations"] = self._get_translations(node_id, target_lang)
        data["example_segments"] = self._get_example_segments(node_id, limit=3)

        # Concept label
        for _, cid, edata in self.G.out_edges(node_id, data=True):
            if edata.get("relation") == "instantiates_concept":
                concept_data = self.G.nodes.get(cid, {})
                data["concept_label"] = concept_data.get("label", "")
                data["concept_definition"] = concept_data.get("definition", "")
                break

        return data

    def get_consistency_report(
        self,
        segments: List[Dict],
        source_lang: str = "en",
        target_lang: str = "sl",
    ) -> List[Dict]:
        """
        Scan confirmed segments for translation inconsistencies.

        Returns a list of warnings:
          {
            "term": str,
            "used_translations": [str, ...],
            "recommended": str,
            "confidence": float,
            "segment_ids": [int, ...]
          }

        Useful for a project-wide consistency panel in the editor.
        """
        # Map: source_term → [(segment_id, target_translation_used)]
        usage: Dict[str, List[Tuple[int, str]]] = defaultdict(list)

        for seg in segments:
            if seg.get("status") != "done":
                continue
            src_entities = self.extract_entities(
                seg.get("source", ""), target_lang=target_lang
            )
            for entity in src_entities:
                src_term = entity.get("term", "")
                if not src_term:
                    continue
                translations = self._get_translations(
                    f"term:{source_lang}:{src_term.lower()}",
                    target_lang=target_lang,
                )
                if not translations:
                    continue
                recommended = translations[0]["term"]
                # Check what was actually used in target
                tgt_text = seg.get("target", "").lower()
                for t in translations:
                    if t["term"].lower() in tgt_text:
                        usage[src_term].append((seg["id"], t["term"]))
                        break
                else:
                    usage[src_term].append((seg["id"], "?"))

        warnings = []
        for src_term, occurrences in usage.items():
            used = {t for _, t in occurrences}
            if len(used) <= 1:
                continue  # Consistent

            translations = self._get_translations(
                f"term:{source_lang}:{src_term.lower()}",
                target_lang=target_lang,
            )
            recommended = translations[0]["term"] if translations else list(used)[0]

            warnings.append(
                {
                    "term": src_term,
                    "used_translations": list(used),
                    "recommended": recommended,
                    "confidence": translations[0]["confidence"] if translations else 0.5,
                    "segment_ids": [sid for sid, _ in occurrences],
                }
            )

        return sorted(warnings, key=lambda x: -x["confidence"])

    # ------------------------------------------------------------------
    # Manual management helpers (used by main.py _add_to_kg etc.)
    # ------------------------------------------------------------------

    def promote_pair(
        self,
        source_text: str,
        target_text: str,
        source_lang: str = "en",
        target_lang: str = "sl",
        verified: bool = True,
        domain: str = "",
    ):
        """
        Promote a confirmed translation pair into the KG.
        Creates term nodes, a concept anchor, and a high-confidence
        translation edge — for use when a translator confirms a segment.
        """
        src_tokens = _tokenize_simple(source_text)
        tgt_tokens = _tokenize_simple(target_text)

        # Whole-segment concept anchor
        concept_id = f"concept:{source_text[:32].replace(' ', '_').lower()}"
        self.add_concept_node(concept_id, label=source_text[:64], domain=domain)

        # Individual content-word term nodes
        for st in src_tokens:
            if len(st) >= 3:
                self.add_term_node(
                    st, source_lang, concept_id=concept_id,
                    domain=domain, source="confirmed", confidence=0.9,
                )
        for tt in tgt_tokens:
            if len(tt) >= 3:
                self.add_term_node(
                    tt, target_lang, concept_id=concept_id,
                    domain=domain, source="confirmed", confidence=0.9,
                )

        # Link every src token to every tgt token (simplified; PMI would be better)
        # In practice for short segments (≤6 tokens) this is fine
        if len(src_tokens) <= 6 and len(tgt_tokens) <= 6:
            for st in src_tokens:
                for tt in tgt_tokens:
                    sid = f"term:{source_lang}:{st}"
                    tid = f"term:{target_lang}:{tt}"
                    if self.G.has_node(sid) and self.G.has_node(tid):
                        self.link_translations(
                            sid, tid,
                            confidence=0.9,
                            verified=verified,
                            source="confirmed",
                        )

    def search_prefix(self, prefix: str, target_lang: str) -> List[str]:
        """Autocomplete: terms in KG that start with prefix."""
        if not prefix:
            return []
        pl = prefix.lower()
        matches = []
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") in ("term", "collocation"):
                if data.get("lang") == target_lang:
                    term = data.get("term") or data.get("phrase", "")
                    if term.lower().startswith(pl):
                        matches.append(term)
        return list(set(matches))

    def search_related(self, term: str, lang: str) -> List[str]:
        node_id = f"term:{lang}:{term.lower()}"
        if not self.G.has_node(node_id):
            return []
        neighbors = self.find_neighbors(node_id, max_depth=1)
        return [
            n["term"]
            for n in neighbors
            if n.get("type") == "term" and n.get("lang") == lang and n.get("term")
        ]

    def find_neighbors(self, node_id: str, max_depth: int = 1) -> List[Dict]:
        if not self.G.has_node(node_id):
            return []
        lengths = nx.single_source_shortest_path_length(
            self.G, node_id, cutoff=max_depth
        )
        results = []
        for other_id, depth in lengths.items():
            if other_id == node_id:
                continue
            nd = dict(self.G.nodes[other_id])
            nd["id"] = other_id
            nd["depth"] = depth
            nd["relation"] = (
                self.G.edges[node_id, other_id].get("relation", "related_to")
                if self.G.has_edge(node_id, other_id)
                else None
            )
            results.append(nd)
        return results

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        by_type: Counter = Counter(
            data.get("type", "unknown")
            for _, data in self.G.nodes(data=True)
        )
        by_rel: Counter = Counter(
            data.get("relation", "unknown")
            for _, _, data in self.G.edges(data=True)
        )
        return {
            "nodes_total": self.G.number_of_nodes(),
            "edges_total": self.G.number_of_edges(),
            **{f"node_{k}": v for k, v in by_type.items()},
            **{f"edge_{k}": v for k, v in by_rel.items()},
        }
