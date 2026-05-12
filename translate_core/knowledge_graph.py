# translate_core/knowledge_graph.py
#
# KG v17 — Domain-aware, lemmatised SL phrases, fixed inline hints
#

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
from flashtext import KeywordProcessor

import config

# ---------------------------------------------------------------------------
# Imports for NLP
# ---------------------------------------------------------------------------
try:
    import spacy

    HAS_SPACY = True
except ImportError:
    HAS_SPACY = False

try:
    import classla

    HAS_CLASSLA = True
except ImportError:
    HAS_CLASSLA = False

try:
    import stanza

    HAS_STANZA = True
except ImportError:
    HAS_STANZA = False

# Optional imports
try:
    from pathlib import Path

    import pyvis
    from jinja2 import Environment, FileSystemLoader
    from pyvis.network import Network

    HAS_PYVIS = True
except ImportError:
    HAS_PYVIS = False

# ---------------------------------------------------------------------------
# Slovenian stop-word / noise filter for SL term extraction
# ---------------------------------------------------------------------------

SL_STOP_LEMMAS = {
    # Function words, conjunctions, particles
    "kot",
    "ali",
    "ne",
    "pa",
    "še",
    "se",
    "je",
    "bil",
    "biti",
    "bi",
    "ta",
    # Pronouns
    "on",
    "ona",
    "ono",
    "oni",
    "one",
    "jaz",
    "ti",
    "mi",
    "vi",
    # Relative / interrogative
    "kdo",
    "kar",
    "ki",
    "da",
    # Prepositions
    "v",
    "na",
    "z",
    "s",
    "po",
    "od",
    "pri",
    "za",
    "o",
    "ob",
    "do",
    "brez",
    "proti",
    "med",
    "nad",
    "pod",
    "pred",
    "čez",
    "skozi",
    "okrog",
    "zunaj",
    "notri",
    "gor",
    "dol",
    # Adverbs / deictics
    "tukaj",
    "tam",
    "zdaj",
    "potem",
    # Fragments / suffixes that are not real lemmas
    "del",
    "delo",
    "anje",
    "eni",
    "ega",
    "emu",
    "ilo",
    "ila",
    "anj",
    "anja",
    "ov",
    "ova",
    "ovo",
    "ev",
    "eva",
    "evo",
    "en",
    "ena",
    "eno",
    "ene",
    "enega",
    "enemu",
    "enim",
}

SL_NOISE = {
    "kot",
    "ali",
    "del",
    "anja",
    "anje",
    "ov",
    "ova",
    "ev",
    "eva",
    "ega",
    "eni",
    "emu",
    "ila",
    "ilo",
    "en",
    "ena",
    "eno",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Strip diacritics for fuzzy fallback matching.
    NOTE: Only used as a last-resort fallback; not applied to SL terms
    in primary lookup paths because SL diacritics are phonemically contrastive.
    """
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _get_timestamp() -> str:
    return datetime.now().isoformat()


def _token_boundary_match(term: str, text: str) -> bool:
    """Check if term appears in text at word boundaries.
    Uses whitespace/punctuation split rather than \\b to handle SL non-ASCII chars correctly.
    """
    term_l = term.lower()
    text_l = text.lower()
    # Fast path: not present at all
    if term_l not in text_l:
        return False
    # Check boundaries: char before start and after end must be non-alpha or string edge
    start = 0
    while True:
        idx = text_l.find(term_l, start)
        if idx == -1:
            return False
        before_ok = idx == 0 or not text_l[idx - 1].isalpha()
        after_ok = (idx + len(term_l)) >= len(text_l) or not text_l[
            idx + len(term_l)
        ].isalpha()
        if before_ok and after_ok:
            return True
        start = idx + 1


# ---------------------------------------------------------------------------
# KnowledgeGraph
# ---------------------------------------------------------------------------


class KnowledgeGraph:
    def __init__(self, db_path: pathlib.Path = config.KG_DB_PATH):
        self.db_path = pathlib.Path(db_path)
        self.G: nx.DiGraph = nx.DiGraph()
        self._exact_kp = KeywordProcessor(case_sensitive=False)
        self._norm_kp = KeywordProcessor(case_sensitive=False)

        self.nlp_en = None
        self.nlp_sl = None

        # 1. Load Spacy for English
        if HAS_SPACY:
            try:
                print("[KG] Loading English model (Spacy)...")
                self.nlp_en = spacy.load("en_core_web_sm", disable=["ner"])
            except Exception as e:
                print(
                    f"[KG Warning] English model missing. Run: python -m spacy download en_core_web_sm. ({e})"
                )

        # 2. Load Classla or Stanza for Slovenian
        if HAS_CLASSLA:
            try:
                print("[KG] Loading Slovenian model (Classla)...")
                self.nlp_sl = classla.Pipeline(
                    "sl",
                    processors="tokenize,pos,lemma,depparse",
                    use_gpu=False,
                    verbose=False,
                )
            except Exception as e:
                print(f"[KG Warning] Classla failed: {e}")
        elif HAS_STANZA:
            try:
                print("[KG] Loading Slovenian model (Stanza fallback)...")

                # --- PATCH FOR PYTORCH 2.6+ ---
                import torch

                _original_torch_load = torch.load

                def _patched_torch_load(*args, **kwargs):
                    kwargs["weights_only"] = False
                    return _original_torch_load(*args, **kwargs)

                torch.load = _patched_torch_load
                # -----------------------------

                self.nlp_sl = stanza.Pipeline(
                    "sl",
                    processors="tokenize,pos,lemma,depparse",
                    use_gpu=False,
                    verbose=False,
                )

                torch.load = _original_torch_load

            except Exception as e:
                print(f"[KG Warning] Stanza fallback failed: {e}")

        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

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

    def _index_term_node(self, node_id: str, data: Dict):
        term = data.get("term", "")
        lang = data.get("lang", "")
        if term:
            self._exact_kp.add_keyword(term, node_id)
            # Only add normalised (diacritic-stripped) index for non-SL terms,
            # since SL diacritics are phonemically contrastive.
            if lang != "sl":
                self._norm_kp.add_keyword(_normalize(term), node_id)
        for variant in data.get("variants", []):
            if variant:
                self._exact_kp.add_keyword(variant, node_id)
                if lang != "sl":
                    self._norm_kp.add_keyword(_normalize(variant), node_id)

    def _rebuild_indices(self):
        self._exact_kp = KeywordProcessor(case_sensitive=False)
        self._norm_kp = KeywordProcessor(case_sensitive=False)
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "term":
                self._index_term_node(node_id, data)

    # ------------------------------------------------------------------
    # Node / Edge Factories
    # ------------------------------------------------------------------

    def add_concept_node(
        self,
        concept_id: str,
        label: str,
        domain: str = "",
        definition: str = "",
        **kwargs,
    ) -> str:
        if not self.G.has_node(concept_id):
            self.G.add_node(
                concept_id,
                id=concept_id,
                type="concept",
                label=label,
                domain=domain,
                definition=definition,
                created_at=_get_timestamp(),
                **kwargs,
            )
        return concept_id

    def add_term_node(
        self,
        term: str,
        lang: str,
        concept_id: str = None,
        is_phrase: bool = False,
        display_form: str = None,
        **kwargs,
    ) -> str:
        """Add or update a term node.

        Args:
            term: The canonical (lemma) form of the term, used as the node key.
            lang: Language code.
            concept_id: Optional concept node to link to.
            is_phrase: Whether this is a multi-word phrase.
            display_form: Optional surface/inflected form for display (SL terms especially).
        """
        node_id = f"term:{lang}:{term.lower()}"
        if not self.G.has_node(node_id):
            node_data = dict(
                id=node_id,
                type="term",
                term=term,
                lang=lang,
                is_phrase=is_phrase,
                frequency=1,
                created_at=_get_timestamp(),
                **kwargs,
            )
            if display_form and display_form.lower() != term.lower():
                node_data["display_form"] = display_form
                node_data["variants"] = [display_form]
            self.G.add_node(node_id, **node_data)
            self._index_term_node(node_id, self.G.nodes[node_id])
        else:
            node = self.G.nodes[node_id]
            node["frequency"] = node.get("frequency", 1) + 1
            # Update is_phrase if this encounter knows it's a phrase
            if is_phrase and not node.get("is_phrase"):
                node["is_phrase"] = True
            # Register new display/surface form as a variant
            if display_form and display_form.lower() != term.lower():
                variants = node.get("variants", [])
                if display_form not in variants:
                    variants.append(display_form)
                    node["variants"] = variants
                    self._exact_kp.add_keyword(display_form, node_id)

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
                created_at=_get_timestamp(),
            )
            self._exact_kp.add_keyword(phrase, node_id)
        else:
            self.G.nodes[node_id]["frequency"] += 1
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
                created_at=_get_timestamp(),
            )
        return seg_id

    def add_domain_node(self, domain_id: str, label: str, parent_id: str = "") -> str:
        if not self.G.has_node(domain_id):
            self.G.add_node(
                domain_id, id=domain_id, type="domain", label=label, parent=parent_id
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
        provenance: str = "auto",
        context: str = None,
        validated_by: str = None,
    ):
        """Link two term nodes as translations of each other.

        When a pair already exists in both directions, both edges are updated
        symmetrically. Confidence boost on re-encounter is capped and only
        applied to the direction that already existed.
        """
        if not (self.G.has_node(src_term_id) and self.G.has_node(tgt_term_id)):
            return

        edge_data = {
            "confidence": confidence,
            "verified": verified,
            "provenance": provenance,
            "last_updated": _get_timestamp(),
        }
        if context:
            edge_data["context"] = context
        if validated_by:
            edge_data["validated_by"] = validated_by

        # Forward direction
        if self.G.has_edge(src_term_id, tgt_term_id):
            old = self.G.edges[src_term_id, tgt_term_id].get("confidence", 0.5)
            self.G.edges[src_term_id, tgt_term_id]["confidence"] = min(0.99, old + 0.05)
            self.G.edges[src_term_id, tgt_term_id]["last_updated"] = _get_timestamp()
        else:
            self.G.add_edge(
                src_term_id, tgt_term_id, relation="translates_to", **edge_data
            )

        # Reverse direction — always kept in sync
        if self.G.has_edge(tgt_term_id, src_term_id):
            old = self.G.edges[tgt_term_id, src_term_id].get("confidence", 0.5)
            self.G.edges[tgt_term_id, src_term_id]["confidence"] = min(0.99, old + 0.05)
            self.G.edges[tgt_term_id, src_term_id]["last_updated"] = _get_timestamp()
        else:
            self.G.add_edge(
                tgt_term_id, src_term_id, relation="translates_to", **edge_data
            )

    def add_variant(self, term_id: str, variant: str):
        if not self.G.has_node(term_id):
            return
        variants = self.G.nodes[term_id].get("variants", [])
        if variant not in variants:
            variants.append(variant)
            self.G.nodes[term_id]["variants"] = variants
            self._exact_kp.add_keyword(variant, term_id)

    # ------------------------------------------------------------------
    # SEEDING: Spacy (EN) + Classla/Stanza (SL)
    # ------------------------------------------------------------------

    def seed_from_tm(
        self,
        tm_entries: List[Dict],
        source_lang: str = "en",
        target_lang: str = "sl",
        min_freq: int = 3,
        max_phrases: int = 15000,
        domain: str = "",
    ):
        """Seed the KG from TM entries.

        SL phrases are stored by lemma form (for deduplication) with the most
        common surface form recorded as display_form. EN phrases use Spacy
        noun chunks plus PMI bigrams/trigrams for better coverage of
        multi-word philosophical and critical-theory terms.

        Args:
            domain: Optional domain tag to attach to all seeded nodes
                    (e.g. 'philosophy', 'contemporary_art', 'queer_theory').
        """
        if not HAS_SPACY or not self.nlp_en:
            print("[KG ERROR] Spacy (EN) missing.")
            return
        if not self.nlp_sl:
            print("[KG ERROR] Slovenian Pipeline (Classla/Stanza) missing.")
            return

        print(f"[KG] Professional Extraction on {len(tm_entries)} segments...")

        src_counts: Counter = Counter()
        # tgt_counts maps lemma form -> Counter of surface forms
        tgt_lemma_counts: Counter = Counter()
        tgt_surface_counts: Dict[str, Counter] = defaultdict(Counter)

        texts_src = [e.get("source", "") for e in tm_entries]
        texts_tgt = [e.get("target", "") for e in tm_entries]

        # 1. Process English (Spacy) — noun chunks + bigram/trigram fallback
        print("  [1/2] Processing Source (EN - Spacy)...")
        for doc in self.nlp_en.pipe(texts_src, batch_size=1000):
            # Noun chunks with grammatical cleaning
            for chunk in doc.noun_chunks:
                start_index = 0
                for i, token in enumerate(chunk):
                    if token.pos_ not in ("DET", "PRON"):
                        start_index = i
                        break
                else:
                    continue

                clean_tokens = [t.text for t in chunk[start_index:]]
                text = " ".join(clean_tokens).lower().strip()
                text = re.sub(r"[.,;:!?)\]]+$", "", text)

                if len(text) < 3 or text.isdigit():
                    continue
                src_counts[text] += 1

            # Bigrams and trigrams from the whole sentence for multi-word terms
            # that noun_chunks misses (e.g. 'affective labour', 'male gaze',
            # 'compulsory heterosexuality', 'conditions of possibility')
            tokens = [
                t.lemma_.lower()
                for t in doc
                if not t.is_stop and not t.is_punct and len(t.text) > 2
            ]
            for n in (2, 3):
                for i in range(len(tokens) - n + 1):
                    gram = " ".join(tokens[i : i + n])
                    if gram not in src_counts:
                        src_counts[gram] += 1  # count once per doc via noun_chunks path

        # 2. Process Slovenian (Classla/Stanza) — lemmatised phrases
        print("  [2/2] Processing Target (SL - Classla/Stanza)...")
        count = 0
        for text in texts_tgt:
            count += 1
            if count % 1000 == 0:
                print(f"    Processed {count}/{len(texts_tgt)}...", end="\r")

            doc = self.nlp_sl(text)
            for lemma_form, surface_form in self._get_dependency_phrases(doc):
                tgt_lemma_counts[lemma_form] += 1
                tgt_surface_counts[lemma_form][surface_form] += 1

        print("\n[KG] Filtering and Linking...")

        src_significant = [p for p, c in src_counts.items() if c >= min_freq]
        tgt_significant = [p for p, c in tgt_lemma_counts.items() if c >= min_freq]

        # Filter out SL function words and fragments
        tgt_significant = [
            p for p in tgt_significant if p not in SL_NOISE and len(p) >= 3
        ]

        src_significant = sorted(
            src_significant, key=lambda p: src_counts[p], reverse=True
        )[:max_phrases]
        tgt_significant = sorted(
            tgt_significant, key=lambda p: tgt_lemma_counts[p], reverse=True
        )[:max_phrases]

        print(
            f"[KG] Identified {len(src_significant)} Source & {len(tgt_significant)} Target concepts."
        )

        # Build KPs
        src_kp = KeywordProcessor()
        for p in src_significant:
            src_kp.add_keyword(p)

        tgt_kp = KeywordProcessor()
        for p in tgt_significant:
            tgt_kp.add_keyword(p)

        cooccur: Counter = Counter()
        # Track per-source-term total co-occurrence count for confidence scoring
        src_cooccur_total: Counter = Counter()

        for i, entry in enumerate(tm_entries):
            if i % 5000 == 0:
                print(f"  Linking {i}...", end="\r")

            src_text = entry.get("source", "").lower()
            tgt_text = entry.get("target", "").lower()

            found_src = src_kp.extract_keywords(src_text)
            found_tgt = tgt_kp.extract_keywords(tgt_text)

            src_ids = []
            for phrase in found_src:
                cid = self.add_concept_node(
                    f"concept:{phrase.replace(' ', '_')}",
                    label=phrase,
                    domain=domain,
                )
                tid = self.add_term_node(
                    phrase, source_lang, concept_id=cid, is_phrase=True
                )
                src_ids.append(tid)

            tgt_ids = []
            for lemma in found_tgt:
                # Use most common surface form as display_form
                best_surface = tgt_surface_counts[lemma].most_common(1)[0][0]
                tid = self.add_term_node(
                    lemma,
                    target_lang,
                    is_phrase=True,
                    display_form=best_surface,
                )
                tgt_ids.append(tid)

            for s in src_ids:
                for t in tgt_ids:
                    cooccur[(s, t)] += 1
                    src_cooccur_total[s] += 1

        for (s, t), count in cooccur.items():
            if count >= min_freq:
                # Confidence = fraction of co-occurrences this specific pair accounts for,
                # relative to the source term's total co-occurrence count across all targets.
                total = max(1, src_cooccur_total[s])
                self.link_translations(
                    s,
                    t,
                    confidence=min(0.95, count / total),
                )

        print(f"\n[KG] Seeding complete: {self.G.number_of_nodes()} nodes.")

    # ------------------------------------------------------------------
    # Dependency Noun Phrase Extractor (For Classla/Stanza)
    # Returns (lemma_form, surface_form) pairs for deduplication
    # ------------------------------------------------------------------

    def _get_dependency_phrases(self, doc) -> List[Tuple[str, str]]:
        """Extract noun phrases from a Classla/Stanza doc.

        Returns:
            List of (lemma_form, surface_form) tuples.
            lemma_form is used as the canonical key; surface_form is for display.

        Notes on deprel choices:
            - 'case' (prepositions) intentionally excluded: they inflate phrases
              with grammatical noise (v, na, z, za, ...).
            - 'nmod:poss' included if labelled by Classla for genitive possessives.
            - 'amod', 'flat', 'compound', 'nummod' retained for rich NP coverage.
        """
        results: List[Tuple[str, str]] = []
        seen_lemmas: set = set()

        VALID_DEPRELS = {
            "amod",
            "nmod",
            "det",
            "flat",
            "compound",
            "nummod",
            "advmod",
            "nmod:poss",
        }

        for sentence in doc.sentences:
            words = {w.id: w for w in sentence.words}
            for word in sentence.words:
                if word.upos in ("NOUN", "PROPN"):
                    phrase_ids = [word.id]
                    for child in sentence.words:
                        if (
                            child.head == word.id
                            and child.deprel in VALID_DEPRELS
                            and child.upos != "PUNCT"
                        ):
                            phrase_ids.append(child.id)
                    phrase_ids.sort()

                    # Surface form (inflected, for display and variants)
                    surface = (
                        " ".join(words[wid].text for wid in phrase_ids).lower().strip()
                    )
                    surface = re.sub(r"[.,;:!?)\]]+$", "", surface)

                    # Lemma form (for deduplication and canonical node key)
                    lemma = (
                        " ".join(words[wid].lemma for wid in phrase_ids).lower().strip()
                    )
                    lemma = re.sub(r"[.,;:!?)\]]+$", "", lemma)

                    # Filter out stop words and short fragments
                    if lemma in SL_STOP_LEMMAS:
                        continue
                    if len(lemma) < 3:
                        continue
                    if lemma not in seen_lemmas:
                        seen_lemmas.add(lemma)
                        results.append((lemma, surface))

        return results

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def extract_entities(self, text: str, target_lang: str = "sl") -> List[Dict]:
        found_ids = set(self._exact_kp.extract_keywords(text.lower()))
        results = []
        for node_id in found_ids:
            if not self.G.has_node(node_id):
                continue
            data = dict(self.G.nodes[node_id])
            data["translations"] = self._get_translations(node_id, target_lang)
            data["example_segments"] = self._get_example_segments(node_id, limit=2)
            results.append(data)
        results.sort(
            key=lambda x: (-int(x.get("is_phrase", False)), -x.get("frequency", 0))
        )
        return results

    def _get_translations(self, term_id: str, target_lang: str = "sl") -> List[Dict]:
        res = []
        seen = set()
        for _, tgt_id, edata in self.G.out_edges(term_id, data=True):
            if edata.get("relation") == "translates_to":
                t_data = self.G.nodes.get(tgt_id, {})
                if t_data.get("lang") == target_lang:
                    # Prefer display_form for SL terms if available
                    term_text = t_data.get("display_form") or t_data.get("term")
                    if term_text not in seen:
                        res.append(
                            {
                                "term": term_text,
                                "lemma": t_data.get("term"),
                                "confidence": edata.get("confidence", 0.5),
                                "verified": edata.get("verified", False),
                            }
                        )
                        seen.add(term_text)
        return sorted(res, key=lambda x: -x["confidence"])

    def _get_example_segments(self, term_id: str, limit: int = 2) -> List[Dict]:
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
    # Visualization
    # ------------------------------------------------------------------

    def visualize(self, output_path: str = "kg_visualization.html", limit: int = 100):
        if not HAS_PYVIS:
            print(
                "[KG] Visualization requires 'pyvis'. Install with: pip install pyvis"
            )
            return

        print(f"[KG] Visualizing top {limit} concepts...")
        nodes = [
            n
            for n, d in self.G.nodes(data=True)
            if d.get("type") == "term" and d.get("is_phrase")
        ]
        nodes = sorted(
            nodes, key=lambda n: self.G.nodes[n].get("frequency", 0), reverse=True
        )[:limit]

        if not nodes:
            print("[KG] No terms found to visualize.")
            return

        sub_g = self.G.subgraph(nodes)
        net = Network(
            height="900px",
            width="100%",
            directed=True,
            notebook=False,
            cdn_resources="in_line",
        )

        # --- ROBUST TEMPLATE FIX ---
        if net.template is None:
            try:
                template_dir = Path(pyvis.__file__).parent / "templates"
                env = Environment(loader=FileSystemLoader(str(template_dir)))
                net.template = env.get_template("template.html")
            except Exception as e:
                print(f"[KG] Critical Error loading Pyvis templates: {e}")
                return
        # ---------------------------

        net.from_nx(sub_g)

        for node in net.nodes:
            display = node.get("display_form") or node.get("term", "")
            node["label"] = display
            node["value"] = node.get("frequency", 1)
            node["title"] = f"{display} (Freq: {node.get('frequency', 0)})"

        try:
            net.write_html(output_path)
            print(f"[KG] Visualization saved to {output_path}")
        except Exception as e:
            print(f"[KG] Error saving visualization: {e}")

    # ------------------------------------------------------------------
    # Helpers (Full Suite)
    # ------------------------------------------------------------------

    def promote_pair(
        self,
        source_text: str,
        target_text: str,
        source_lang: str = "en",
        target_lang: str = "sl",
        verified: bool = True,
        domain: str = "",
        context: str = None,
        validated_by: str = None,
    ):
        src = source_text.strip()
        tgt = target_text.strip()
        cid = self.add_concept_node(
            f"concept:{src.replace(' ', '_')}", label=src, domain=domain
        )
        sid = self.add_term_node(src, source_lang, concept_id=cid, is_phrase=True)
        tid = self.add_term_node(tgt, target_lang, is_phrase=True)
        self.link_translations(
            sid,
            tid,
            confidence=1.0,
            verified=verified,
            provenance="manual",
            context=context,
            validated_by=validated_by,
        )

    def get_inline_hints(
        self,
        word_prefix: str,
        source_text: str,
        source_lang: str = "en",
        target_lang: str = "sl",
        max_hints: int = 6,
        min_prefix_len: int = 2,
    ) -> List[Dict]:
        """Return candidate target terms that match the current word prefix at cursor.

        Args:
            word_prefix: The prefix of the word currently being typed — i.e.
                         the text from the start of the current word up to the
                         cursor position. This is `info["prefix"]` from ZenEditor.getInfo(),
                         NOT the full partial translation text.
            source_text: The full source segment text, used to look up KG entities.
            source_lang: Source language code.
            target_lang: Target language code.
            max_hints: Maximum number of hints to return.
            min_prefix_len: Don't fire for very short prefixes (avoids noise on first char).

        Returns:
            List of hint dicts with keys: term, confidence, source_term, type, verified.
        """
        results = []
        seen: set = set()
        prefix = word_prefix.lower().strip()

        # Don't return a flood of suggestions for very short or empty prefixes
        if len(prefix) < min_prefix_len:
            return []

        src_entities = self.extract_entities(source_text, target_lang=target_lang)
        for entity in src_entities:
            src_term = entity.get("term", "")
            for t in entity.get("translations", []):
                candidate = t["term"]
                if not candidate or candidate in seen:
                    continue

                # Match on ANY word within the candidate, not just the first.
                # This allows mid-phrase completion: typing "prak" will match
                # "posthumanistične prakse", "umetniške prakse", etc.
                candidate_words = candidate.lower().split()
                if not any(w.startswith(prefix) for w in candidate_words):
                    continue

                seen.add(candidate)
                score = t["confidence"]
                # Boost verified terms slightly so they rank first among prefix matches
                if t.get("verified"):
                    score = min(1.0, score + 0.1)

                results.append(
                    {
                        "term": candidate,
                        "confidence": score,
                        "source_term": src_term,
                        "type": "kg_translation",
                        "verified": t.get("verified", False),
                    }
                )

        results.sort(key=lambda x: -x["confidence"])
        return results[:max_hints]

    def get_term_tooltip(
        self, term: str, source_lang: str = "en", target_lang: str = "sl"
    ) -> Optional[Dict]:
        node_id = f"term:{source_lang}:{term.lower()}"
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
        for _, cid, edata in self.G.out_edges(node_id, data=True):
            if edata.get("relation") == "instantiates_concept":
                concept_data = self.G.nodes.get(cid, {})
                data["concept_label"] = concept_data.get("label", "")
                data["concept_definition"] = concept_data.get("definition", "")
                break
        return data

    def get_consistency_report(
        self, segments: List[Dict], source_lang: str = "en", target_lang: str = "sl"
    ) -> List[Dict]:
        usage = defaultdict(list)
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
                    f"term:{source_lang}:{src_term.lower()}", target_lang=target_lang
                )
                if not translations:
                    continue
                tgt_text = seg.get("target", "")
                found_match = False
                for t in translations:
                    # Use token-boundary matching to avoid false positives on
                    # morphological substrings (e.g. 'telo' inside 'telefonski')
                    if _token_boundary_match(t["term"], tgt_text):
                        usage[src_term].append((seg["id"], t["term"]))
                        found_match = True
                        break
                if not found_match:
                    usage[src_term].append((seg["id"], "?"))

        warnings = []
        for src_term, occurrences in usage.items():
            used = {t for _, t in occurrences}
            if len(used) <= 1:
                continue
            translations = self._get_translations(
                f"term:{source_lang}:{src_term.lower()}", target_lang=target_lang
            )
            recommended = translations[0]["term"] if translations else list(used)[0]
            warnings.append(
                {
                    "term": src_term,
                    "used_translations": list(used),
                    "recommended": recommended,
                    "confidence": translations[0]["confidence"]
                    if translations
                    else 0.5,
                    "segment_ids": [sid for sid, _ in occurrences],
                }
            )
        return sorted(warnings, key=lambda x: -x["confidence"])

    def search_prefix(self, prefix: str, target_lang: str) -> List[str]:
        if not prefix:
            return []
        pl = prefix.lower()
        matches = []
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") in ("term", "collocation"):
                if data.get("lang") == target_lang:
                    # Check both lemma (term) and display form
                    for candidate in filter(
                        None,
                        [
                            data.get("term"),
                            data.get("display_form"),
                            data.get("phrase"),
                        ],
                    ):
                        if candidate.lower().startswith(pl):
                            matches.append(candidate)
                            break
        return list(set(matches))

    def search_related(self, term: str, lang: str) -> List[str]:
        node_id = f"term:{lang}:{term.lower()}"
        if not self.G.has_node(node_id):
            return []
        neighbors = self.find_neighbors(node_id, max_depth=1)
        return [
            n.get("display_form") or n["term"]
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

    def stats(self) -> Dict[str, int]:
        by_type = Counter(
            data.get("type", "unknown") for _, data in self.G.nodes(data=True)
        )
        by_rel = Counter(
            data.get("relation", "unknown") for _, _, data in self.G.edges(data=True)
        )
        return {
            "nodes_total": self.G.number_of_nodes(),
            "edges_total": self.G.number_of_edges(),
            **{f"node_{k}": v for k, v in by_type.items()},
            **{f"edge_{k}": v for k, v in by_rel.items()},
        }
