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
    spacy = None  # type: ignore[assignment]
    HAS_SPACY = False

try:
    import classla

    HAS_CLASSLA = True
except ImportError:
    classla = None  # type: ignore[assignment]
    HAS_CLASSLA = False

try:
    import stanza

    HAS_STANZA = True
except ImportError:
    stanza = None  # type: ignore[assignment]
    HAS_STANZA = False

# Optional imports
try:
    from pathlib import Path

    import pyvis
    from jinja2 import Environment, FileSystemLoader
    from pyvis.network import Network

    HAS_PYVIS = True
except ImportError:
    Path = None  # type: ignore[assignment,misc]
    pyvis = None  # type: ignore[assignment]
    Environment = None  # type: ignore[assignment,misc]
    FileSystemLoader = None  # type: ignore[assignment,misc]
    Network = None  # type: ignore[assignment,misc]
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

        if HAS_SPACY and spacy is not None:
            try:
                print("[KG] Loading English model (Spacy)...")
                self.nlp_en = spacy.load("en_core_web_sm", disable=["ner"])
            except Exception as e:
                print(
                    f"[KG Warning] English model missing. Run: python -m spacy download en_core_web_sm. ({e})"
                )

        if HAS_CLASSLA and classla is not None:
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
        elif HAS_STANZA and stanza is not None:
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
        author_id: Optional[str] = None,
        year: Optional[int] = None,
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
        concept_id: Optional[str] = None,
        is_phrase: bool = False,
        display_form: Optional[str] = None,
        is_animate: bool = False,
        gender_strategies: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> str:
        node_id = f"term:{lang}:{term.lower()}"

        strategies = gender_strategies or {}
        if lang == "sl" and not strategies:
            strategies = self._parse_gender_strategies(display_form or term)
            if strategies:
                is_animate = True

        if not self.G.has_node(node_id):
            node_data: Dict[str, Any] = dict(
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
        component_ids: Optional[List[str]] = None,
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
        gloss: Optional[str] = None,
        source_text_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        year: Optional[int] = None,
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
            # Verified is monotonic: once a curator has blessed this
            # mapping the flag stays True, even if a later auto-seed call
            # passes verified=False. The confirm pipeline depends on this.
            if verified:
                node["verified"] = True

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
        elif verified:
            # Existing legacy edge — still mirror the verified bump so
            # downstream code that reads translates_to edges sees the
            # curator's blessing.
            for u, v in ((src_term_id, tgt_term_id), (tgt_term_id, src_term_id)):
                if self.G.has_edge(u, v):
                    d = self.G.edges[u, v]
                    d["verified"] = True
                    d["last_updated"] = _get_timestamp()

        return mapping_id

    def link_translations(
        self,
        src_term_id: str,
        tgt_term_id: str,
        confidence: float = 0.8,
        verified: bool = False,
        provenance: str = "auto",
        context: Optional[str] = None,
        validated_by: Optional[str] = None,
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
    # SEEDING: Concept-Centered Bilingual Seeding
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
        default_source_id: Optional[str] = None,
        default_agent_id: Optional[str] = None,
        default_year: Optional[int] = None,
        lineage_rules: Optional[Dict[str, Dict[str, str]]] = None,
    ):
        """Seed the KG from TM entries.

        The TM parser (tm.py / parse_tmx_file) has already normalized all
        entries so that ``source`` is always English text and ``target`` is
        always Slovenian text, regardless of the original TMX direction.
        We MUST NOT re-swap based on source_lang / target_lang — those
        parameters are kept for API compatibility but the data flow is
        always EN→SL.
        """
        if not HAS_SPACY or not self.nlp_en:
            print("[KG ERROR] Spacy (EN) missing.")
            return
        if not self.nlp_sl:
            print("[KG ERROR] Slovenian Pipeline (Classla/Stanza) missing.")
            return

        print(
            f"[KG] Concept-Centered Seeding (EN→SL) on {len(tm_entries)} segments..."
        )

        # ── NLP Phase: extract phrases from both languages ────────────

        en_counts: Counter = Counter()
        sl_lemma_counts: Counter = Counter()
        sl_surface_counts: Dict[str, Counter] = defaultdict(Counter)
        gender_profiles_found: Dict[str, Dict[str, str]] = defaultdict(dict)

        # Entries are always EN source, SL target (normalized by parser)
        texts_en = [e.get("source", "") for e in tm_entries]
        texts_sl = [e.get("target", "") for e in tm_entries]

        # 1. English NLP Analysis (SpaCy)
        print("  [1/3] Parsing English Segments...")
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

        # 2. Slovenian NLP Analysis (batched)
        CHUNK_SIZE = 200
        print(
            f"  [2/3] Parsing Slovenian Segments (batches of {CHUNK_SIZE})..."
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
                    f"\n[KG Warning] Batch at index {chunk_idx} failed, "
                    f"falling back to sequential: {e}"
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
                f"    Processed SL segments: {min(chunk_idx + CHUNK_SIZE, len(texts_sl))}/{len(texts_sl)}...",
                end="\r",
            )

        # ── Filtering: keep only significant phrases ──────────────────

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

        # ── Co-occurrence with Dice coefficient ────────────────────────
        #
        # Instead of cartesian product (every EN × every SL per segment),
        # we count segment-level co-occurrence and use the Dice coefficient
        # for confidence scoring. This eliminates the noise that comes from
        # pairing every phrase with every other phrase in the same segment.
        #
        #   Dice(A, B) = 2 × |segments_with_both| / (|segments_with_A| + |segments_with_B|)
        #
        # A pair that always appears together scores 1.0. A frequent word
        # that co-occurs with everything scores near 0.

        print("\n  [3/3] Building concept-centered graph with Dice scoring...")

        pair_count: Counter = Counter()      # (en_id, sl_id) → segment count
        en_seg_count: Counter = Counter()     # en_id → segment count
        sl_seg_count: Counter = Counter()     # sl_id → segment count

        for i, entry in enumerate(tm_entries):
            en_text = entry.get("source", "").lower()
            sl_text = entry.get("target", "").lower()

            found_en = en_kp.extract_keywords(en_text)
            found_sl = sl_kp.extract_keywords(sl_text)

            # Create term nodes and collect IDs for this segment
            en_ids = []
            for phrase in found_en:
                tid = self.add_term_node(phrase, "en", is_phrase=True)
                en_ids.append(tid)
                en_seg_count[tid] += 1

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
                sl_seg_count[tid] += 1

            # Only pair EN terms with SL terms found in the same segment.
            # Confidence will be computed via Dice coefficient later.
            for s in en_ids:
                for t in sl_ids:
                    pair_count[(s, t)] += 1

        # ── Create concept + mapping edges with Dice confidence ────────

        for (s, t), count in pair_count.items():
            if count < min_freq:
                continue

            s_total = en_seg_count.get(s, 0)
            t_total = sl_seg_count.get(t, 0)
            if s_total == 0 or t_total == 0:
                continue

            # Dice coefficient: 0.0–1.0, higher = more exclusive pairing
            dice = (2.0 * count) / (s_total + t_total)

            # Skip noise: pairs that co-occur but aren't meaningfully related
            if dice < 0.05:
                continue

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
                            try:
                                current_year = int(overrides["year"])
                            except (TypeError, ValueError):
                                current_year = default_year
                        break

            # ── Create a concept node for this translation pair ────
            # Both EN and SL terms link to the SAME concept, so
            # _related_via_concept() can find cross-language siblings.
            concept_label = src_term_text
            concept_id = self.add_concept_node(
                f"concept:{concept_label.replace(' ', '_')}",
                label=concept_label,
                domain=domain,
            )
            # Link EN term → concept
            if not self.G.has_edge(s, concept_id):
                self.G.add_edge(s, concept_id, relation="instantiates_concept")
            # Link SL term → concept
            if not self.G.has_edge(t, concept_id):
                self.G.add_edge(t, concept_id, relation="instantiates_concept")

            # Create translation mapping with Dice-based confidence
            self.link_translations_with_context(
                src_term_id=s,
                tgt_term_id=t,
                confidence=min(0.95, dice),
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
    # Visualization: Concept Relations Graph
    # ------------------------------------------------------------------
    def visualize(self, output_path: str = "kg_visualization.html", limit: int = 80, min_confidence: float = 0.15):
        """Render an interactive concept-to-concept relation graph.

        Each concept is a single node. Two concepts are connected when an EN term
        under one concept has a high-confidence mapping to an SL term under the
        other concept. Node size reflects how many bilingual connections the
        concept has. Color indicates domain.
        """
        if not HAS_PYVIS:
            print("[KG] Visualization requires 'pyvis'. Install with: pip install pyvis")
            return
        assert (
            Network is not None
            and Path is not None
            and pyvis is not None
            and Environment is not None
            and FileSystemLoader is not None
        ), "HAS_PYVIS is True but optional symbols are unbound"

        # ── Build concept → terms lookup ──
        concept_terms: Dict[str, Dict[str, List[str]]] = {}
        # {concept_id: {"en": [term_id, ...], "sl": [term_id, ...]}}
        for nid, nd in self.G.nodes(data=True):
            if nd.get("type") != "concept":
                continue
            concept_terms[nid] = {"en": [], "sl": []}

        for term_id, cid, d in self.G.edges(data=True):
            if d.get("relation") != "instantiates_concept":
                continue
            if cid not in concept_terms:
                continue
            lang = self.G.nodes[term_id].get("lang", "")
            if lang in ("en", "sl"):
                concept_terms[cid][lang].append(term_id)

        # ── Find cross-concept relations via mappings ──
        # A mapping from an EN term under concept A to an SL term under concept B
        # creates a relation edge A → B.
        concept_relations: Dict[tuple, Dict] = {}  # (src_concept, tgt_concept) → metadata

        # Reverse lookup: term → concept
        term_to_concept: Dict[str, str] = {}
        for cid, langs in concept_terms.items():
            for lang in ("en", "sl"):
                for tid in langs[lang]:
                    term_to_concept[tid] = cid

        for nid, nd in self.G.nodes(data=True):
            if nd.get("type") != "translation_mapping":
                continue
            if nd.get("confidence", 0) < min_confidence:
                continue

            # Find source EN term and target SL term
            src_term = None
            tgt_term = None
            for pred in self.G.predecessors(nid):
                if self.G.edges[pred, nid].get("relation") == "has_mapping":
                    src_term = pred
            for succ in self.G.successors(nid):
                if self.G.edges[nid, succ].get("relation") == "maps_to":
                    tgt_term = succ

            if not src_term or not tgt_term:
                continue

            src_concept = term_to_concept.get(src_term)
            tgt_concept = term_to_concept.get(tgt_term)
            if not src_concept or not tgt_concept:
                continue

            key = (src_concept, tgt_concept)
            if key not in concept_relations or nd["confidence"] > concept_relations[key].get("confidence", 0):
                concept_relations[key] = {
                    "confidence": nd.get("confidence", 0),
                    "lineage": nd.get("lineage", ""),
                    "verified": nd.get("verified", False),
                }

        # ── Also add rhizomatic concept→concept edges ──
        for u, v, d in self.G.edges(data=True):
            if d.get("relation") in ("extends", "critiques", "redefines", "reappropriates", "related_to"):
                if u in concept_terms and v in concept_terms:
                    key = (u, v)
                    if key not in concept_relations:
                        concept_relations[key] = {"confidence": 1.0, "lineage": d.get("relation", ""), "verified": True}

        # ── Score concepts by number of high-conf connections ──
        concept_scores: Dict[str, int] = {}
        for (ca, cb), meta in concept_relations.items():
            concept_scores[ca] = concept_scores.get(ca, 0) + 1
            concept_scores[cb] = concept_scores.get(cb, 0) + 1

        # Also count concepts with terms even if no cross-relation
        for cid in concept_terms:
            if cid not in concept_scores:
                en_count = len(concept_terms[cid]["en"])
                sl_count = len(concept_terms[cid]["sl"])
                if en_count > 0 and sl_count > 0:
                    concept_scores[cid] = en_count + sl_count

        # Take top concepts
        top_concepts = sorted(concept_scores, key=concept_scores.get, reverse=True)[:limit]
        top_set = set(top_concepts)

        if not top_concepts:
            print("[KG] No concepts with qualifying mappings found to visualize.")
            return

        print(f"[KG] Visualizing {len(top_concepts)} concepts (confidence >= {min_confidence})...")

        # ── Build concept-only graph ──
        sub_g = nx.DiGraph()
        for cid in top_concepts:
            nd = self.G.nodes[cid]
            sub_g.add_node(cid, **nd)

        edge_count = 0
        for (ca, cb), meta in concept_relations.items():
            if ca in top_set and cb in top_set:
                sub_g.add_edge(ca, cb, **meta)
                edge_count += 1

        # ── Style ──
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

        # Color concepts by domain
        domain_colors = {
            "humanities": "#E67E22",
            "disability studies": "#8E44AD",
            "feminist philosophy": "#C0392B",
            "art": "#2980B9",
            "politics": "#27AE60",
        }
        default_color = "#3498DB"

        for node in net.nodes:
            label = node.get("label", node.get("id", ""))
            domain = node.get("domain", "")
            en_count = len(concept_terms.get(node["id"], {}).get("en", []))
            sl_count = len(concept_terms.get(node["id"], {}).get("sl", []))
            score = concept_scores.get(node["id"], 0)

            node["label"] = label
            node["color"] = domain_colors.get(domain, default_color)
            node["shape"] = "dot"
            node["size"] = max(12, min(40, 10 + score * 2))
            node["title"] = (
                f"{label}\n"
                f"Domain: {domain or '(none)'}\n"
                f"EN terms: {en_count} | SL terms: {sl_count}\n"
                f"Connections: {score}"
            )

        for edge in net.edges:
            conf = edge.get("confidence", 0)
            lineage = edge.get("lineage", "")
            verified = edge.get("verified", False)
            # Thicker edges for higher confidence
            edge["width"] = max(0.5, conf * 3)
            edge["color"] = {"color": "#34495E", "opacity": max(0.2, conf)}
            edge["arrows"] = "to"
            edge["title"] = f"confidence: {conf:.3f}\nlineage: {lineage}\nverified: {verified}"

        try:
            net.write_html(output_path)
            print(f"[KG] Visualization saved to {output_path}")
            print(f"    {len(top_concepts)} concepts, {edge_count} relations")
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
        context: Optional[str] = None,
        validated_by: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        """Ingest a translator-confirmed segment via the full NLP pipeline.

        Runs Spacy on the source (EN noun chunks + 2-3-grams of content
        lemmas) and Stanza/Classla on the target (SL dependency phrases),
        upserts term nodes for each extracted phrase, then promotes
        translation_mapping edges between co-occurring src/tgt terms.

        Crucially, the **segment itself is never stored as a node**.
        Segments live in the TM; the KG receives the semantic structure
        extracted from them.

        Mapping promotion has two regimes:
          - Existing mapping (any lineage) → mark `verified=True`,
            confidence bumps via the existing upsert path, gloss is set
            to the source segment for context.
          - No prior mapping → create a new one ONLY when the pair is
            unambiguous within this segment (exactly one EN term + one SL
            term extracted). Cartesian pairing in larger segments is too
            noisy without Dice corpus inference; the seeders earn that
            signal across many TUs, single-segment confirms cannot.

        Concepts: every promoted pair (existing or new) is linked to a
        concept named after the EN term (the same scheme the seeders use).
        Concept ids are deterministic so re-confirms hit the existing
        concept rather than duplicating.

        Returns a delta dict for instrumentation:
          {
            "src_terms": [...], "tgt_terms": [...],
            "verified": [...mapping_ids...],
            "created": [...mapping_ids...],
          }
        """
        src_text = (source_text or "").strip()
        tgt_text = (target_text or "").strip()
        delta: Dict[str, List[str]] = {
            "src_terms": [],
            "tgt_terms": [],
            "verified": [],
            "created": [],
        }
        if not src_text or not tgt_text:
            return delta
        if not HAS_SPACY or self.nlp_en is None:
            print("[KG promote_pair] Spacy (EN) not loaded — skipping.")
            return delta
        if self.nlp_sl is None:
            print("[KG promote_pair] Slovenian pipeline not loaded — skipping.")
            return delta

        # ── EN extraction (same logic as seed_from_tm step 1) ─────────
        en_phrases: List[str] = []
        seen_en: set = set()
        doc_en = self.nlp_en(src_text)
        for chunk in doc_en.noun_chunks:
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
            if text in seen_en:
                continue
            seen_en.add(text)
            en_phrases.append(text)
        # Content lemma 2-3-grams
        tokens = [
            t.lemma_.lower()
            for t in doc_en
            if not t.is_stop and not t.is_punct and len(t.text) > 2
        ]
        for n in (2, 3):
            for i in range(len(tokens) - n + 1):
                gram = " ".join(tokens[i : i + n])
                if gram and gram not in seen_en and len(gram) >= 3:
                    seen_en.add(gram)
                    en_phrases.append(gram)

        # ── SL extraction (same logic as seed_from_tm step 2) ─────────
        protected, mappings = self._protect_gender_tokens(tgt_text)
        sl_pairs: List[Tuple[str, str]] = []   # (lemma, surface)
        seen_sl: set = set()
        try:
            doc_sl = self.nlp_sl(protected)
            for lemma, surface in self._get_dependency_phrases(doc_sl):
                lemma = self._restore_gender_tokens(lemma, mappings)
                surface = self._restore_gender_tokens(surface, mappings)
                if lemma in SL_NOISE or len(lemma) < 3 or lemma in seen_sl:
                    continue
                seen_sl.add(lemma)
                sl_pairs.append((lemma, surface))
        except Exception as e:
            print(f"[KG promote_pair] SL extraction failed: {e}")

        if not en_phrases or not sl_pairs:
            # Nothing to wire — the segment had no extractable terminology
            # on one side. That's fine; the TM still has the segment.
            return delta

        # ── Term filter + upsert ────────────────────────────────────
        # A single-segment NLP pass cannot match the corpus-level
        # min_freq filtering that seed_from_tm earns over thousands of
        # segments. So we restrict to phrases the KG ALREADY knows
        # about: an EN candidate becomes a term-promotion target only
        # if `term:en:{phrase}` already exists. The seeders' vocabulary
        # is the authority; the confirm path strengthens that
        # vocabulary rather than expanding it with one-off n-gram noise.
        #
        # Exception: a brand-new term IS created when the segment is
        # unambiguous (exactly one EN phrase + one SL phrase survive
        # this filter or come through it after fallback). That gives
        # the translator a way to introduce truly new terminology — a
        # heading like "Time → Čas" — without bloating from sentences.
        en_ids: List[str] = []
        en_known = [
            p for p in en_phrases
            if self.G.has_node(f"term:{source_lang}:{p}")
        ]
        for phrase in en_known:
            tid = self.add_term_node(phrase, source_lang, is_phrase=" " in phrase)
            en_ids.append(tid)
            if tid not in delta["src_terms"]:
                delta["src_terms"].append(tid)

        sl_ids: List[str] = []
        sl_known: List[Tuple[str, str]] = [
            (lemma, surface) for lemma, surface in sl_pairs
            if self.G.has_node(f"term:{target_lang}:{lemma}")
        ]
        for lemma, surface in sl_known:
            profile = self._parse_gender_strategies(surface)
            tid = self.add_term_node(
                lemma,
                target_lang,
                is_phrase=" " in lemma,
                display_form=surface if surface != lemma else None,
                is_animate=bool(profile),
                gender_strategies=profile,
            )
            sl_ids.append(tid)
            if tid not in delta["tgt_terms"]:
                delta["tgt_terms"].append(tid)

        # Unambiguous new-term path: one EN candidate + one SL candidate
        # in the segment AND neither is in the KG yet — admit the pair
        # as fresh vocabulary. Only kicks in when known-term filtering
        # produced nothing on at least one side.
        if (not en_known or not sl_known) and len(en_phrases) == 1 and len(sl_pairs) == 1:
            phrase = en_phrases[0]
            tid = self.add_term_node(phrase, source_lang, is_phrase=" " in phrase)
            if tid not in delta["src_terms"]:
                delta["src_terms"].append(tid)
                en_ids.append(tid)
            sl_lemma, sl_surface = sl_pairs[0]
            profile = self._parse_gender_strategies(sl_surface)
            tid_sl = self.add_term_node(
                sl_lemma,
                target_lang,
                is_phrase=" " in sl_lemma,
                display_form=sl_surface if sl_surface != sl_lemma else None,
                is_animate=bool(profile),
                gender_strategies=profile,
            )
            if tid_sl not in delta["tgt_terms"]:
                delta["tgt_terms"].append(tid_sl)
                sl_ids.append(tid_sl)

        # ── Mapping promotion ────────────────────────────────────────
        # A pair may already have one or more mappings under different
        # lineages (e.g. "performance" + "2022"). The curator's confirm
        # blesses all of them. We do NOT create a competing "manual"
        # mapping when prior lineage-tagged mappings exist — that would
        # split the provenance trail.

        def _find_mappings_between(src_id: str, tgt_id: str) -> List[str]:
            out: List[str] = []
            for _u, mid, d in self.G.out_edges(src_id, data=True):
                if d.get("relation") != "has_mapping":
                    continue
                for _mu, mv, md in self.G.out_edges(mid, data=True):
                    if md.get("relation") == "maps_to" and mv == tgt_id:
                        out.append(mid)
                        break
            return out

        gloss_value = context if context is not None else src_text[:240]
        unambiguous = len(en_ids) == 1 and len(sl_ids) == 1
        for s in en_ids:
            for t in sl_ids:
                existing_maps = _find_mappings_between(s, t)
                if existing_maps:
                    # Strengthen every existing lineage in place.
                    for mid in existing_maps:
                        nd = self.G.nodes[mid]
                        nd["confidence"] = min(
                            0.99, float(nd.get("confidence") or 0.5) + 0.05,
                        )
                        if verified:
                            nd["verified"] = True
                        if gloss_value:
                            nd["gloss"] = gloss_value
                        delta["verified"].append(mid)
                    # Mirror the verified bump on any legacy
                    # translates_to edges so downstream readers agree.
                    if verified:
                        for u, v in ((s, t), (t, s)):
                            if self.G.has_edge(u, v) and (
                                self.G.edges[u, v].get("relation") == "translates_to"
                            ):
                                self.G.edges[u, v]["verified"] = True
                                self.G.edges[u, v]["last_updated"] = _get_timestamp()
                    self._link_concept_for_pair(s, t, domain=domain)
                elif unambiguous:
                    # No prior mapping anywhere — single EN term + single
                    # SL term in this segment is a clean alignment signal,
                    # so we create a new mapping at high confidence with
                    # lineage="manual" so the curator can find it later.
                    map_id = self.link_translations_with_context(
                        src_term_id=s,
                        tgt_term_id=t,
                        confidence=0.85,
                        lineage="manual",
                        gloss=gloss_value,
                        verified=verified,
                    )
                    if map_id:
                        delta["created"].append(map_id)
                        self._link_concept_for_pair(s, t, domain=domain)
                # Larger cartesian pairs without prior mappings are
                # intentionally skipped — single-segment co-occurrence is
                # not enough signal to invent a new translation pair.

        return delta

    def _link_concept_for_pair(
        self, src_term_id: str, tgt_term_id: str, *, domain: str = "",
    ) -> str:
        """Ensure both terms in a confirmed pair instantiate the same
        concept node. Concept ids are deterministic (derived from the
        source term text) so re-confirms reuse the existing concept
        rather than duplicating it. Never uses sentence text as a label
        — only the term itself."""
        src_data = self.G.nodes.get(src_term_id, {})
        label = src_data.get("term") or ""
        if not label:
            return ""
        # Match the seeders' slug scheme so confirms hit the same nodes.
        slug = label.replace(" ", "_")
        cid = self.add_concept_node(
            f"concept:{slug}", label=label, domain=domain,
        )
        for tid in (src_term_id, tgt_term_id):
            if self.G.has_node(tid) and not self.G.has_edge(tid, cid):
                self.G.add_edge(tid, cid, relation="instantiates_concept")
        return cid

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
        label: Optional[str] = None,
        domain: Optional[str] = None,
        definition: Optional[str] = None,
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
        lineage: Optional[str] = None,
        register: Optional[str] = None,
        gloss: Optional[str] = None,
        confidence: Optional[float] = None,
        year: Optional[int] = None,
        verified: Optional[bool] = None,
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
