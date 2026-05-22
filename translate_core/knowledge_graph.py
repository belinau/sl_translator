# translate_core/knowledge_graph.py
#
# KG v22 — Bidirectional-Aware, Intertextual, Rhizomatic, Gender-Inclusive & Curation-Ready
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
    "on",
    "ona",
    "ono",
    "oni",
    "one",
    "jaz",
    "ti",
    "mi",
    "vi",
    "kdo",
    "kar",
    "ki",
    "da",
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
    "tukaj",
    "tam",
    "zdaj",
    "potem",
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

# Regex to detect Slovenian non-binary spellings, including trailing underscores (e.g., "prevajalke_")
SL_GENDER_INCLUSIVE_RE = re.compile(
    r"\b([a-zA-ZčšžČŠŽ]+)([_/*])([a-zA-ZčšžČŠŽ]*)\b", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    """Strip diacritics for fuzzy fallback matching."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _get_timestamp() -> str:
    return datetime.now().isoformat()


def _token_boundary_match(term: str, text: str) -> bool:
    """Check if term appears in text at word boundaries."""
    term_l = term.lower()
    text_l = text.lower()
    if term_l not in text_l:
        return False
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

        if HAS_SPACY:
            try:
                print("[KG] Loading English model (Spacy)...")
                self.nlp_en = spacy.load("en_core_web_sm", disable=["ner"])
            except Exception as e:
                print(
                    f"[KG Warning] English model missing. Run: python -m spacy download en_core_web_sm. ({e})"
                )

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
            if lang != "sl":
                self._norm_kp.add_keyword(_normalize(term), node_id)

        strategies = data.get("gender_strategies", {})
        for strat_val in strategies.values():
            if strat_val:
                self._exact_kp.add_keyword(strat_val, node_id)

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
    def add_agent_node(
        self, agent_id: str, name: str, role: str = "author", **kwargs
    ) -> str:
        node_id = f"agent:{agent_id.lower()}"
        if not self.G.has_node(node_id):
            self.G.add_node(
                node_id,
                id=node_id,
                type="agent",
                name=name,
                role=role,
                created_at=_get_timestamp(),
                **kwargs,
            )
        return node_id

    def add_source_text_node(
        self,
        text_id: str,
        title: str,
        author_id: str = None,
        year: int = None,
        **kwargs,
    ) -> str:
        node_id = f"source:{text_id.lower()}"
        if not self.G.has_node(node_id):
            self.G.add_node(
                node_id,
                id=node_id,
                type="source_text",
                title=title,
                year=year,
                created_at=_get_timestamp(),
                **kwargs,
            )
        if author_id:
            auth_node = f"agent:{author_id.lower()}"
            if self.G.has_node(auth_node) and not self.G.has_edge(node_id, auth_node):
                self.G.add_edge(node_id, auth_node, relation="written_by")
        return node_id

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

    def link_concepts_rhizomatic(
        self, concept_a: str, concept_b: str, relation_type: str = "extends"
    ):
        valid_relations = {
            "critiques",
            "extends",
            "redefines",
            "reappropriates",
            "related_to",
        }
        rel = relation_type if relation_type in valid_relations else "related_to"
        if self.G.has_node(concept_a) and self.G.has_node(concept_b):
            self.G.add_edge(
                concept_a, concept_b, relation=rel, last_updated=_get_timestamp()
            )

    def add_term_node(
        self,
        term: str,
        lang: str,
        concept_id: str = None,
        is_phrase: bool = False,
        display_form: str = None,
        is_animate: bool = False,
        gender_strategies: Dict[str, str] = None,
        **kwargs,
    ) -> str:
        node_id = f"term:{lang}:{term.lower()}"

        strategies = gender_strategies or {}
        if lang == "sl" and not strategies:
            strategies = self._parse_gender_strategies(display_form or term)
            if strategies:
                is_animate = True

        if not self.G.has_node(node_id):
            node_data = dict(
                id=node_id,
                type="term",
                term=term,
                lang=lang,
                is_phrase=is_phrase,
                is_animate=is_animate,
                gender_strategies=strategies,
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
            if is_phrase and not node.get("is_phrase"):
                node["is_phrase"] = True
            if is_animate:
                node["is_animate"] = True

            existing_strat = node.get("gender_strategies", {})
            existing_strat.update(strategies)
            node["gender_strategies"] = existing_strat

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

    def _parse_gender_strategies(self, text: str) -> Dict[str, str]:
        if not text:
            return {}
        match = SL_GENDER_INCLUSIVE_RE.search(text)
        if not match:
            return {}

        base, marker, suffix = match.groups()
        strategies = {}
        if marker == "_":
            if suffix == "":
                strategies["female_trailing_underscore"] = text
            else:
                strategies["underscore_inclusivity"] = text
        elif marker == "/":
            strategies["slash_inclusivity"] = text
        elif marker == "*":
            strategies["asterisk_inclusivity"] = text
        return strategies

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

    # ------------------------------------------------------------------
    # Context-Aware Translation Mapping Node
    # ------------------------------------------------------------------
    def link_translations_with_context(
        self,
        src_term_id: str,
        tgt_term_id: str,
        confidence: float = 0.8,
        lineage: str = "general",
        register: str = "academic",
        gloss: str = None,
        source_text_id: str = None,
        agent_id: str = None,
        year: int = None,
        verified: bool = False,
    ) -> str:
        if not (self.G.has_node(src_term_id) and self.G.has_node(tgt_term_id)):
            return ""

        safe_lineage = lineage.lower().replace(" ", "_")
        mapping_id = f"map:{src_term_id}>>{tgt_term_id}:{safe_lineage}"

        if not self.G.has_node(mapping_id):
            self.G.add_node(
                mapping_id,
                id=mapping_id,
                type="translation_mapping",
                confidence=confidence,
                lineage=lineage,
                register=register,
                gloss=gloss,
                year=year,
                verified=verified,
                created_at=_get_timestamp(),
            )
        else:
            node = self.G.nodes[mapping_id]
            node["confidence"] = min(0.99, node.get("confidence", 0.5) + 0.05)
            if gloss:
                node["gloss"] = gloss
            if year:
                node["year"] = year

        if not self.G.has_edge(src_term_id, mapping_id):
            self.G.add_edge(src_term_id, mapping_id, relation="has_mapping")
        if not self.G.has_edge(mapping_id, tgt_term_id):
            self.G.add_edge(mapping_id, tgt_term_id, relation="maps_to")

        if source_text_id:
            src_node = f"source:{source_text_id.lower()}"
            if self.G.has_node(src_node) and not self.G.has_edge(mapping_id, src_node):
                self.G.add_edge(mapping_id, src_node, relation="instantiated_in")

        if agent_id:
            agent_node = f"agent:{agent_id.lower()}"
            if self.G.has_node(agent_node) and not self.G.has_edge(
                mapping_id, agent_node
            ):
                self.G.add_edge(mapping_id, agent_node, relation="attributed_to")

        legacy_data = {
            "confidence": confidence,
            "verified": verified,
            "provenance": lineage,
            "last_updated": _get_timestamp(),
        }
        if not self.G.has_edge(src_term_id, tgt_term_id):
            self.G.add_edge(
                src_term_id, tgt_term_id, relation="translates_to", **legacy_data
            )
            self.G.add_edge(
                tgt_term_id, src_term_id, relation="translates_to", **legacy_data
            )

        return mapping_id

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
        self.link_translations_with_context(
            src_term_id=src_term_id,
            tgt_term_id=tgt_term_id,
            confidence=confidence,
            lineage=provenance,
            gloss=context,
            verified=verified,
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
    # SEEDING: Bidirectional Language-Direction Aware Seeding Logic
    # ------------------------------------------------------------------
    def seed_from_tm(
        self,
        tm_entries: List[Dict],
        source_lang: str = "en",
        target_lang: str = "sl",
        min_freq: int = 3,
        max_phrases: int = 15000,
        domain: str = "",
        default_lineage: str = "general",
        default_source_id: str = None,
        default_agent_id: str = None,
        default_year: int = None,
        lineage_rules: Dict[str, Dict[str, str]] = None,
    ):
        if not HAS_SPACY or not self.nlp_en:
            print("[KG ERROR] Spacy (EN) missing.")
            return
        if not self.nlp_sl:
            print("[KG ERROR] Slovenian Pipeline (Classla/Stanza) missing.")
            return

        print(
            f"[KG] Analytical Seeding ({source_lang.upper()} -> {target_lang.upper()}) on {len(tm_entries)} segments..."
        )

        en_counts: Counter = Counter()
        sl_lemma_counts: Counter = Counter()
        sl_surface_counts: Dict[str, Counter] = defaultdict(Counter)
        gender_profiles_found: Dict[str, Dict[str, str]] = defaultdict(dict)

        if source_lang == "en":
            texts_en = [e.get("source", "") for e in tm_entries]
            texts_sl = [e.get("target", "") for e in tm_entries]
        else:
            texts_en = [e.get("target", "") for e in tm_entries]
            texts_sl = [e.get("source", "") for e in tm_entries]

        # 1. English NLP Analysis (SpaCy)
        print("  [1/2] Parsing English Segments...")
        for doc in self.nlp_en.pipe(texts_en, batch_size=1000):
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
                en_counts[text] += 1

            tokens = [
                t.lemma_.lower()
                for t in doc
                if not t.is_stop and not t.is_punct and len(t.text) > 2
            ]
            for n in (2, 3):
                for i in range(len(tokens) - n + 1):
                    gram = " ".join(tokens[i : i + n])
                    if gram not in en_counts:
                        en_counts[gram] += 1

        # 2. Slovenian NLP Analysis with Optimized Batched Execution
        CHUNK_SIZE = 200
        print(
            f"  [2/2] Parsing Slovenian Segments (Protecting Inflective Marks in batches of {CHUNK_SIZE})..."
        )

        for chunk_idx in range(0, len(texts_sl), CHUNK_SIZE):
            chunk = texts_sl[chunk_idx : chunk_idx + CHUNK_SIZE]

            mappings = {}
            prot_idx = 0
            protected_segments = []

            for text in chunk:

                def repl(match):
                    nonlocal prot_idx
                    placeholder = f"GENDERINCLPROT{prot_idx}"
                    mappings[placeholder] = match.group(0)
                    prot_idx += 1
                    return placeholder

                protected_text = SL_GENDER_INCLUSIVE_RE.sub(repl, text)
                protected_segments.append(protected_text)

            combined_text = "\n\n".join(protected_segments)

            try:
                doc = self.nlp_sl(combined_text)
                for lemma_form, surface_form in self._get_dependency_phrases(doc):
                    lemma_form = self._restore_gender_tokens(lemma_form, mappings)
                    surface_form = self._restore_gender_tokens(surface_form, mappings)

                    sl_lemma_counts[lemma_form] += 1
                    sl_surface_counts[lemma_form][surface_form] += 1

                    profile = self._parse_gender_strategies(surface_form)
                    if profile:
                        gender_profiles_found[lemma_form].update(profile)
            except Exception as e:
                print(
                    f"\n[KG Warning] Batch at index {chunk_idx} failed, falling back to sequential processing: {e}"
                )
                for raw_text in chunk:
                    protected_text, mappings_single = self._protect_gender_tokens(
                        raw_text
                    )
                    try:
                        doc = self.nlp_sl(protected_text)
                        for lemma_form, surface_form in self._get_dependency_phrases(
                            doc
                        ):
                            lemma_form = self._restore_gender_tokens(
                                lemma_form, mappings_single
                            )
                            surface_form = self._restore_gender_tokens(
                                surface_form, mappings_single
                            )

                            sl_lemma_counts[lemma_form] += 1
                            sl_surface_counts[lemma_form][surface_form] += 1

                            profile = self._parse_gender_strategies(surface_form)
                            if profile:
                                gender_profiles_found[lemma_form].update(profile)
                    except Exception:
                        pass

            print(
                f"    Processed Slovenian segments: {min(chunk_idx + CHUNK_SIZE, len(texts_sl))}/{len(texts_sl)}...",
                end="\r",
            )

        print("\n[KG] Organizing mappings and linking contextual nodes...")

        en_significant = [p for p, c in en_counts.items() if c >= min_freq]
        sl_significant = [
            p
            for p, c in sl_lemma_counts.items()
            if p not in SL_NOISE and len(p) >= 3 and c >= min_freq
        ]

        en_significant = sorted(
            en_significant, key=lambda p: en_counts[p], reverse=True
        )[:max_phrases]
        sl_significant = sorted(
            sl_significant, key=lambda p: sl_lemma_counts[p], reverse=True
        )[:max_phrases]

        en_kp = KeywordProcessor()
        for p in en_significant:
            en_kp.add_keyword(p)

        sl_kp = KeywordProcessor()
        for p in sl_significant:
            sl_kp.add_keyword(p)

        cooccur: Counter = Counter()
        src_cooccur_total: Counter = Counter()

        for i, entry in enumerate(tm_entries):
            src_text = entry.get("source", "").lower()
            tgt_text = entry.get("target", "").lower()

            en_text = src_text if source_lang == "en" else tgt_text
            sl_text = tgt_text if source_lang == "en" else src_text

            found_en = en_kp.extract_keywords(en_text)
            found_sl = sl_kp.extract_keywords(sl_text)

            en_ids = []
            for phrase in found_en:
                cid = self.add_concept_node(
                    f"concept:{phrase.replace(' ', '_')}", label=phrase, domain=domain
                )
                tid = self.add_term_node(phrase, "en", concept_id=cid, is_phrase=True)
                en_ids.append(tid)

            sl_ids = []
            for lemma in found_sl:
                best_surface = sl_surface_counts[lemma].most_common(1)[0][0]
                has_gender_strat = lemma in gender_profiles_found
                tid = self.add_term_node(
                    lemma,
                    "sl",
                    is_phrase=True,
                    display_form=best_surface,
                    is_animate=has_gender_strat,
                    gender_strategies=gender_profiles_found.get(lemma, {}),
                )
                sl_ids.append(tid)

            if source_lang == "en":
                for s in en_ids:
                    for t in sl_ids:
                        cooccur[(s, t)] += 1
                        src_cooccur_total[s] += 1
            else:
                for s in sl_ids:
                    for t in en_ids:
                        cooccur[(s, t)] += 1
                        src_cooccur_total[s] += 1

        for (s, t), count in cooccur.items():
            if count >= min_freq:
                total = max(1, src_cooccur_total[s])

                src_term_text = self.G.nodes[s].get("term", "")
                tgt_term_text = self.G.nodes[t].get("term", "")

                current_lineage = default_lineage
                current_source_id = default_source_id
                current_agent_id = default_agent_id
                current_year = default_year

                combined_text_lower = f"{src_term_text} {tgt_term_text}".lower()
                if lineage_rules:
                    for keyword, overrides in lineage_rules.items():
                        if keyword.lower() in combined_text_lower:
                            if "lineage" in overrides:
                                current_lineage = overrides["lineage"]
                            if "source_id" in overrides:
                                current_source_id = overrides["source_id"]
                            if "agent_id" in overrides:
                                current_agent_id = overrides["agent_id"]
                            if "year" in overrides:
                                current_year = overrides["year"]
                            break

                self.link_translations_with_context(
                    src_term_id=s,
                    tgt_term_id=t,
                    confidence=min(0.95, count / total),
                    lineage=current_lineage,
                    source_text_id=current_source_id,
                    agent_id=current_agent_id,
                    year=current_year,
                )

        print(
            f"\n[KG] Seeding complete: Graph contains {self.G.number_of_nodes()} nodes."
        )

    def _protect_gender_tokens(self, text: str) -> Tuple[str, Dict[str, str]]:
        mappings = {}
        idx = 0

        def repl(match):
            nonlocal idx
            placeholder = f"GENDERINCLPROT{idx}"
            mappings[placeholder] = match.group(0)
            idx += 1
            return placeholder

        protected = SL_GENDER_INCLUSIVE_RE.sub(repl, text)
        return protected, mappings

    def _restore_gender_tokens(self, text: str, mappings: Dict[str, str]) -> str:
        restored = text
        for placeholder, original in mappings.items():
            restored = restored.replace(placeholder.lower(), original)
            restored = restored.replace(placeholder, original)
        return restored

    def _get_dependency_phrases(self, doc) -> List[Tuple[str, str]]:
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

                    surface = (
                        " ".join(words[wid].text for wid in phrase_ids).lower().strip()
                    )
                    surface = re.sub(r"[.,;:!?)\]]+$", "", surface)

                    lemma = (
                        " ".join(words[wid].lemma for wid in phrase_ids).lower().strip()
                    )
                    lemma = re.sub(r"[.,;:!?)\]]+$", "", lemma)

                    if lemma in SL_STOP_LEMMAS or len(lemma) < 3:
                        continue
                    if lemma not in seen_lemmas:
                        seen_lemmas.add(lemma)
                        results.append((lemma, surface))
        return results

    # ------------------------------------------------------------------
    # Query & Editor Integration
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

        for _, mapping_id, edata in self.G.out_edges(term_id, data=True):
            if edata.get("relation") == "has_mapping":
                map_node = self.G.nodes.get(mapping_id, {})
                for _, tgt_id, map_edge in self.G.out_edges(mapping_id, data=True):
                    if map_edge.get("relation") == "maps_to":
                        t_data = self.G.nodes.get(tgt_id, {})
                        if t_data.get("lang") == target_lang:
                            term_text = t_data.get("display_form") or t_data.get("term")

                            source_texts = []
                            agents = []
                            for _, ctx_id, rel_edge in self.G.out_edges(
                                mapping_id, data=True
                            ):
                                if rel_edge.get("relation") == "instantiated_in":
                                    st = self.G.nodes.get(ctx_id, {})
                                    source_texts.append(st.get("title", ""))
                                elif rel_edge.get("relation") == "attributed_to":
                                    ag = self.G.nodes.get(ctx_id, {})
                                    agents.append(ag.get("name", ""))

                            res.append(
                                {
                                    "term": term_text,
                                    "lemma": t_data.get("term"),
                                    "confidence": map_node.get("confidence", 0.5),
                                    "verified": map_node.get("verified", False),
                                    "lineage": map_node.get("lineage", "general"),
                                    "register": map_node.get("register", "academic"),
                                    "gloss": map_node.get("gloss", ""),
                                    "sources": source_texts,
                                    "agents": agents,
                                    "gender_strategies": t_data.get(
                                        "gender_strategies", {}
                                    ),
                                }
                            )
                            seen.add((term_text, map_node.get("lineage", "general")))

        for _, tgt_id, edata in self.G.out_edges(term_id, data=True):
            if edata.get("relation") == "translates_to":
                t_data = self.G.nodes.get(tgt_id, {})
                if t_data.get("lang") == target_lang:
                    term_text = t_data.get("display_form") or t_data.get("term")
                    if not any(r["term"] == term_text for r in res):
                        res.append(
                            {
                                "term": term_text,
                                "lemma": t_data.get("term"),
                                "confidence": edata.get("confidence", 0.5),
                                "verified": edata.get("verified", False),
                                "lineage": "general",
                                "register": "academic",
                                "gloss": "",
                                "sources": [],
                                "agents": [],
                                "gender_strategies": t_data.get(
                                    "gender_strategies", {}
                                ),
                            }
                        )
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

        if net.template is None:
            try:
                template_dir = Path(pyvis.__file__).parent / "templates"
                env = Environment(loader=FileSystemLoader(str(template_dir)))
                net.template = env.get_template("template.html")
            except Exception as e:
                print(f"[KG] Critical Error loading Pyvis templates: {e}")
                return

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
        self.link_translations_with_context(
            src_term_id=sid,
            tgt_term_id=tid,
            confidence=1.0,
            verified=verified,
            lineage="manual",
            gloss=context,
        )

    def get_inline_hints(
        self,
        word_prefix: str,
        source_text: str,
        source_lang: str = "en",
        target_lang: str = "sl",
        max_hints: int = 6,
        min_prefix_len: int = 2,
        preferred_gender_strategy: str = "underscore_inclusivity",
    ) -> List[Dict]:
        results = []
        seen: set = set()
        prefix = word_prefix.lower().strip()

        if len(prefix) < min_prefix_len:
            return []

        src_entities = self.extract_entities(source_text, target_lang=target_lang)
        for entity in src_entities:
            src_term = entity.get("term", "")
            for t in entity.get("translations", []):
                candidate = t["term"]

                strats = t.get("gender_strategies", {})
                if strats and preferred_gender_strategy in strats:
                    candidate = strats[preferred_gender_strategy]

                if not candidate or candidate in seen:
                    continue

                candidate_words = candidate.lower().split()
                if not any(w.startswith(prefix) for w in candidate_words):
                    continue

                seen.add(candidate)
                score = t["confidence"]
                if t.get("verified"):
                    score = min(1.0, score + 0.1)

                results.append(
                    {
                        "term": candidate,
                        "confidence": score,
                        "source_term": src_term,
                        "type": "kg_translation",
                        "lineage": t.get("lineage", "general"),
                        "gloss": t.get("gloss", ""),
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

                related_concepts = []
                for _, neighbor_id, c_edge in self.G.out_edges(cid, data=True):
                    rel = c_edge.get("relation")
                    if rel in ("critiques", "extends", "redefines", "reappropriates"):
                        c_node = self.G.nodes.get(neighbor_id, {})
                        related_concepts.append(
                            {"label": c_node.get("label", ""), "relation": rel}
                        )
                data["related_concepts"] = related_concepts
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
                    if _token_boundary_match(t["term"], tgt_text):
                        usage[src_term].append(
                            (seg["id"], t["term"], t.get("lineage", "general"))
                        )
                        found_match = True
                        break
                if not found_match:
                    usage[src_term].append((seg["id"], "?", "unknown"))

        warnings = []
        for src_term, occurrences in usage.items():
            used = {t for _, t, _ in occurrences}
            lineages = {lin for _, _, lin in occurrences if lin != "unknown"}

            if len(used) > 1 or len(lineages) > 1:
                translations = self._get_translations(
                    f"term:{source_lang}:{src_term.lower()}", target_lang=target_lang
                )
                recommended = translations[0]["term"] if translations else list(used)[0]
                warnings.append(
                    {
                        "term": src_term,
                        "used_translations": list(used),
                        "used_lineages": list(lineages),
                        "recommended": recommended,
                        "confidence": translations[0]["confidence"]
                        if translations
                        else 0.5,
                        "segment_ids": [sid for sid, _, _ in occurrences],
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

    # ------------------------------------------------------------------
    # Curation API
    # ------------------------------------------------------------------
    def remove_node(self, node_id: str) -> bool:
        """Safely remove any node (concept, term, mapping, agent, source) and rebuild search indices."""
        if not self.G.has_node(node_id):
            return False

        node_type = self.G.nodes[node_id].get("type")
        self.G.remove_node(node_id)

        # If we deleted a term, rebuild search indices to prevent dead reference hits
        if node_type == "term":
            self._rebuild_indices()

        return True

    def update_concept_metadata(
        self,
        concept_id: str,
        label: str = None,
        domain: str = None,
        definition: str = None,
    ) -> bool:
        """Update fields on an existing conceptual container."""
        if not self.G.has_node(concept_id):
            return False

        node = self.G.nodes[concept_id]
        if label is not None:
            node["label"] = label
        if domain is not None:
            node["domain"] = domain
        if definition is not None:
            node["definition"] = definition
        return True

    def update_translation_mapping(
        self,
        mapping_id: str,
        lineage: str = None,
        register: str = None,
        gloss: str = None,
        confidence: float = None,
        year: int = None,
        verified: bool = None,
    ) -> bool:
        """Update qualitative or quantitative parameters on a context mapping."""
        if not self.G.has_node(mapping_id):
            return False

        node = self.G.nodes[mapping_id]
        if lineage is not None:
            node["lineage"] = lineage
        if register is not None:
            node["register"] = register
        if gloss is not None:
            node["gloss"] = gloss
        if confidence is not None:
            node["confidence"] = confidence
        if year is not None:
            node["year"] = year
        if verified is not None:
            node["verified"] = verified
        return True

    def get_all_by_type(self, node_type: str) -> List[Dict]:
        """Fetch all nodes of a specific structural type (e.g. 'concept', 'agent', 'source_text')."""
        results = []
        for n, d in self.G.nodes(data=True):
            if d.get("type") == node_type:
                results.append(dict(d))
        return results

    def get_all_lineages(self) -> List[str]:
        """List all distinct theoretical lineages currently registered in the graph's mappings."""
        lineages = set()
        for n, d in self.G.nodes(data=True):
            if d.get("type") == "translation_mapping":
                lin = d.get("lineage")
                if lin:
                    lineages.add(lin)
        return sorted(list(lineages))

    def merge_lineages(self, old_lineages: List[str], new_lineage: str) -> int:
        """Globally rename/merge multiple messy lineages into a single clean theoretical lineage."""
        updated_count = 0
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "translation_mapping":
                current_lineage = data.get("lineage", "general")
                if current_lineage in old_lineages:
                    data["lineage"] = new_lineage
                    updated_count += 1
        return updated_count

    def bulk_align_lineages_with_glossary(self, glossary_entries: List[Dict]) -> int:
        """Scan all chaotic imported mappings, find matches in your clean glossary,
        and bulk-align their theoretical lineages to the glossary standards.
        """
        updated_count = 0
        glossary_lookup = {}
        for entry in glossary_entries:
            src = entry.get("source", "").strip().lower()
            tgt = entry.get("target", "").strip().lower()
            if src and tgt:
                glossary_lookup[(src, tgt)] = entry.get("lineage", "General")

        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "translation_mapping":
                src_term = None
                tgt_term = None

                for u, v, edata in self.G.in_edges(node_id, data=True):
                    if edata.get("relation") == "has_mapping":
                        src_term = self.G.nodes[u].get("term", "").lower()
                for u, v, edata in self.G.out_edges(node_id, data=True):
                    if edata.get("relation") == "maps_to":
                        tgt_term = self.G.nodes[v].get("term", "").lower()

                if src_term and tgt_term:
                    match_key = (src_term, tgt_term)
                    if match_key in glossary_lookup:
                        correct_lineage = glossary_lookup[match_key]
                        if data.get("lineage") != correct_lineage:
                            data["lineage"] = correct_lineage
                            updated_count += 1
        return updated_count
