# translate_core/knowledge_graph.py
#
# KG v16 — Final Correct Version (Grammar Logic + PyTorch/Jinja Fixes)
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
# Helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _get_timestamp() -> str:
    return datetime.now().isoformat()


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
        if term:
            self._exact_kp.add_keyword(term, node_id)
            self._norm_kp.add_keyword(_normalize(term), node_id)
        for variant in data.get("variants", []):
            if variant:
                self._exact_kp.add_keyword(variant, node_id)
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
        **kwargs,
    ) -> str:
        node_id = f"term:{lang}:{term.lower()}"
        if not self.G.has_node(node_id):
            self.G.add_node(
                node_id,
                id=node_id,
                type="term",
                term=term,
                lang=lang,
                is_phrase=is_phrase,
                frequency=1,
                created_at=_get_timestamp(),
                **kwargs,
            )
            self._index_term_node(node_id, self.G.nodes[node_id])
        else:
            self.G.nodes[node_id]["frequency"] = (
                self.G.nodes[node_id].get("frequency", 1) + 1
            )

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
        source: str = "auto",
        context: str = None,
        validated_by: str = None,
    ):
        if self.G.has_node(src_term_id) and self.G.has_node(tgt_term_id):
            edge_data = {
                "confidence": confidence,
                "verified": verified,
                "source": source,
                "last_updated": _get_timestamp(),
            }
            if context:
                edge_data["context"] = context
            if validated_by:
                edge_data["validated_by"] = validated_by

            if self.G.has_edge(src_term_id, tgt_term_id):
                old = self.G.edges[src_term_id, tgt_term_id].get("confidence", 0.5)
                self.G.edges[src_term_id, tgt_term_id]["confidence"] = min(
                    0.99, old + 0.1
                )
            else:
                self.G.add_edge(
                    src_term_id, tgt_term_id, relation="translates_to", **edge_data
                )

            if not self.G.has_edge(tgt_term_id, src_term_id):
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
        min_freq: int = 2,
        max_phrases: int = 15000,
    ):
        if not HAS_SPACY or not self.nlp_en:
            print("[KG ERROR] Spacy (EN) missing.")
            return
        if not self.nlp_sl:
            print("[KG ERROR] Slovenian Pipeline (Classla/Stanza) missing.")
            return

        print(f"[KG] Professional Extraction on {len(tm_entries)} segments...")

        src_counts = Counter()
        tgt_counts = Counter()

        texts_src = [e.get("source", "") for e in tm_entries]
        texts_tgt = [e.get("target", "") for e in tm_entries]

        # 1. Process English (Spacy)
        print("  [1/2] Processing Source (EN - Spacy)...")
        for doc in self.nlp_en.pipe(texts_src, batch_size=1000):
            for chunk in doc.noun_chunks:
                # --- GRAMMATICAL CLEANING ---
                # Skip chunks that are purely Pronouns or Determiners (e.g. "that", "this")
                # Find start index of the actual noun phrase
                start_index = 0
                for i, token in enumerate(chunk):
                    # If word is DET (the, a) or PRON (that, it), skip it
                    if token.pos_ not in ("DET", "PRON"):
                        start_index = i
                        break
                else:
                    # If loop finished, it's all determiners/pronouns
                    continue

                # Reconstruct phrase from the first valid token
                clean_tokens = [t.text for t in chunk[start_index:]]
                text = " ".join(clean_tokens)

                text = text.lower().strip()
                text = re.sub(r"[.,;:!?)\]]+$", "", text)

                if len(text) < 3 or text.isdigit():
                    continue

                src_counts[text] += 1

        # 2. Process Slovenian (Classla/Stanza)
        print("  [2/2] Processing Target (SL - Classla/Stanza)...")
        count = 0
        for text in texts_tgt:
            count += 1
            if count % 1000 == 0:
                print(f"    Processed {count}/{len(texts_tgt)}...", end="\r")

            doc = self.nlp_sl(text)
            for phrase in self._get_dependency_phrases(doc):
                tgt_counts[phrase] += 1

        print("\n[KG] Filtering and Linking...")

        src_significant = [p for p, c in src_counts.items() if c >= min_freq]
        tgt_significant = [p for p, c in tgt_counts.items() if c >= min_freq]

        src_significant = sorted(
            src_significant, key=lambda p: src_counts[p], reverse=True
        )[:max_phrases]
        tgt_significant = sorted(
            tgt_significant, key=lambda p: tgt_counts[p], reverse=True
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

        cooccur = Counter()

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
                    f"concept:{phrase.replace(' ', '_')}", label=phrase
                )
                tid = self.add_term_node(
                    phrase, source_lang, concept_id=cid, is_phrase=True
                )
                src_ids.append(tid)

            tgt_ids = []
            for phrase in found_tgt:
                tid = self.add_term_node(phrase, target_lang, is_phrase=True)
                tgt_ids.append(tid)

            for s in src_ids:
                for t in tgt_ids:
                    cooccur[(s, t)] += 1

        for (s, t), count in cooccur.items():
            if count >= min_freq:
                self.link_translations(
                    s,
                    t,
                    confidence=min(0.95, count / self.G.nodes[s].get("frequency", 1)),
                )

        print(f"\n[KG] Seeding complete: {self.G.number_of_nodes()} nodes.")

    # ------------------------------------------------------------------
    # Dependency Noun Phrase Extractor (For Classla/Stanza)
    # ------------------------------------------------------------------

    def _get_dependency_phrases(self, doc):
        phrases = set()
        for sentence in doc.sentences:
            words = {w.id: w for w in sentence.words}
            for word in sentence.words:
                if word.upos in ("NOUN", "PROPN"):
                    phrase_ids = [word.id]
                    for child in sentence.words:
                        if child.head == word.id:
                            if child.deprel in (
                                "amod",
                                "nmod",
                                "case",
                                "det",
                                "flat",
                                "compound",
                                "nummod",
                                "advmod",
                            ):
                                if child.upos != "PUNCT":
                                    phrase_ids.append(child.id)
                    phrase_ids.sort()
                    phrase_text = " ".join([words[wid].text for wid in phrase_ids])
                    phrase_text = phrase_text.lower().strip()
                    phrase_text = re.sub(r"[.,;:!?)\]]+$", "", phrase_text)
                    if len(phrase_text) >= 3:
                        phrases.add(phrase_text)
        return list(phrases)

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
                    if t_data.get("term") not in seen:
                        res.append(
                            {
                                "term": t_data.get("term"),
                                "confidence": edata.get("confidence", 0.5),
                                "verified": edata.get("verified", False),
                            }
                        )
                        seen.add(t_data.get("term"))
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
            node["label"] = node.get("term", "")
            node["value"] = node.get("frequency", 1)
            node["title"] = f"{node.get('term')} (Freq: {node.get('frequency', 0)})"

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
            sid, tid, confidence=1.0, verified=verified, source="manual"
        )

    def get_inline_hints(
        self,
        partial_target: str,
        source_text: str,
        source_lang: str = "en",
        target_lang: str = "sl",
        max_hints: int = 6,
    ) -> List[Dict]:
        results = []
        seen = set()
        words = partial_target.rstrip().split()
        last_word = words[-1].lower() if words else ""

        src_entities = self.extract_entities(source_text, target_lang=target_lang)
        for entity in src_entities:
            src_term = entity.get("term", "")
            for t in entity.get("translations", []):
                candidate = t["term"]
                if candidate in seen:
                    continue
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
                tgt_text = seg.get("target", "").lower()
                found_match = False
                for t in translations:
                    if t["term"].lower() in tgt_text:
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
