# translate_core/knowledge_graph.py
#
# KG v22 — Bidirectional-Aware, Intertextual, Rhizomatic, Gender-Inclusive & Curation-Ready
#

from __future__ import annotations

import json
import os
import pathlib
import re
import tempfile
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

        # NLP models (Spacy EN, Stanza/Classla SL) were previously loaded here
        # for promote_pair's NLP term-extraction. That extraction is removed:
        # concepts come from the Ollama smol pipeline; terms come from glossary
        # entries pushed to the KG via the glossary UI. No NLP loading on init.
        self.nlp_en = None
        self.nlp_sl = None

        self._disk_mtime: float | None = None
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
        try:
            self._disk_mtime = self.db_path.stat().st_mtime
        except OSError:
            self._disk_mtime = None

    def reload_if_changed(self) -> bool:
        """If data/knowledge.db was modified on disk by another process (e.g. a
        maintenance script) since we last read/wrote it, discard the in-memory
        graph and reload the current disk state. Returns True if a reload
        happened. This makes the editor's saves write ON TOP of external edits
        instead of clobbering them."""
        try:
            if not self.db_path.exists():
                return False
            m = self.db_path.stat().st_mtime
        except OSError:
            return False
        if self._disk_mtime is not None and abs(m - self._disk_mtime) < 1e-6:
            return False
        # Build into fresh structures, then swap. Keep the old graph so a failed
        # read (or one producing an empty graph) never blanks the live KG.
        prev_G, prev_ex, prev_nm = self.G, self._exact_kp, self._norm_kp
        self.G = nx.DiGraph()
        self._exact_kp = KeywordProcessor(case_sensitive=False)
        self._norm_kp = KeywordProcessor(case_sensitive=False)
        self._load()
        if self.G.number_of_nodes() == 0 and prev_G.number_of_nodes() > 0:
            self.G, self._exact_kp, self._norm_kp = prev_G, prev_ex, prev_nm
            return False
        return True

    def save(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "nodes": [{"id": n, **self.G.nodes[n]} for n in self.G.nodes],
            "edges": [
                {"source": u, "target": v, **self.G.edges[u, v]}
                for u, v in self.G.edges
            ],
        }
        payload = json.dumps(data, ensure_ascii=False, indent=2)

        # Create a .bak copy as secondary protection
        if self.db_path.exists():
            bak_path = self.db_path.with_suffix(self.db_path.suffix + ".bak")
            os.replace(str(self.db_path), str(bak_path))

        # Atomic write: write to temp file in same dir, then replace
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.db_path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, str(self.db_path))
            try:
                self._disk_mtime = self.db_path.stat().st_mtime
            except OSError:
                self._disk_mtime = None
        except BaseException:
            # Clean up the temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

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
            # O-12: all agent nodes must carry dedup_group, alt_spellings,
            # all_roles, and mention_count. Fill defaults when caller omits them.
            from .entity_extraction.name_dedup import dedup_group_key

            kwargs.setdefault("dedup_group", dedup_group_key(name))
            kwargs.setdefault("alt_spellings", [name])
            kwargs.setdefault("all_roles", [role])
            kwargs.setdefault("mention_count", 1)
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

    # ------------------------------------------------------------------
    # Institution node + bibliographic / translation edges
    # Added for the entity-extraction rework: lets us model publishers,
    # galleries, festival venues, and the citation tree (translated work
    # → cited works) that the TM extractor builds.
    # ------------------------------------------------------------------
    def add_institution_node(
        self, inst_id: str, name: str, kind: str = "publisher", **kwargs
    ) -> str:
        node_id = f"institution:{inst_id.lower()}"
        if not self.G.has_node(node_id):
            self.G.add_node(
                node_id,
                id=node_id,
                type="institution",
                name=name,
                kind=kind,
                created_at=_get_timestamp(),
                **kwargs,
            )
        return node_id

    def link_translated_by(self, source_text_id: str, agent_id: str) -> bool:
        src_node = source_text_id if source_text_id.startswith("source:") else f"source:{source_text_id.lower()}"
        agent_node = agent_id if agent_id.startswith("agent:") else f"agent:{agent_id.lower()}"
        if not (self.G.has_node(src_node) and self.G.has_node(agent_node)):
            return False
        if not self.G.has_edge(src_node, agent_node):
            self.G.add_edge(src_node, agent_node, relation="translated_by")
        return True

    def link_cited_in(self, cited_source_id: str, container_source_id: str) -> bool:
        cited = cited_source_id if cited_source_id.startswith("source:") else f"source:{cited_source_id.lower()}"
        container = container_source_id if container_source_id.startswith("source:") else f"source:{container_source_id.lower()}"
        if cited == container:
            return False
        if not (self.G.has_node(cited) and self.G.has_node(container)):
            return False
        if not self.G.has_edge(cited, container):
            self.G.add_edge(cited, container, relation="cited_in")
        return True

    def link_appears_in(self, chapter_source_id: str, book_source_id: str) -> bool:
        """Link a chapter/article (source_text) to the container source_text.

        Relation "appears_in": the cited work appears in the container book.
        Idempotent and direction-preserving (chapter -> book).
        """
        chapter = chapter_source_id if chapter_source_id.startswith("source:") else f"source:{chapter_source_id.lower()}"
        book = book_source_id if book_source_id.startswith("source:") else f"source:{book_source_id.lower()}"
        if chapter == book:
            return False
        if not (self.G.has_node(chapter) and self.G.has_node(book)):
            return False
        if not self.G.has_edge(chapter, book):
            self.G.add_edge(chapter, book, relation="appears_in")
        return True

    def link_published_by(self, source_text_id: str, institution_id: str) -> bool:
        src_node = source_text_id if source_text_id.startswith("source:") else f"source:{source_text_id.lower()}"
        inst_node = institution_id if institution_id.startswith("institution:") else f"institution:{institution_id.lower()}"
        if not (self.G.has_node(src_node) and self.G.has_node(inst_node)):
            return False
        if not self.G.has_edge(src_node, inst_node):
            self.G.add_edge(src_node, inst_node, relation="published_by")
        return True

    def link_translation_published_by(self, source_text_id: str, institution_id: str) -> bool:
        src_node = source_text_id if source_text_id.startswith("source:") else f"source:{source_text_id.lower()}"
        inst_node = institution_id if institution_id.startswith("institution:") else f"institution:{institution_id.lower()}"
        if not (self.G.has_node(src_node) and self.G.has_node(inst_node)):
            return False
        if not self.G.has_edge(src_node, inst_node):
            self.G.add_edge(src_node, inst_node, relation="translation_published_by")
        return True

    def link_hosted_by(self, source_text_id: str, institution_id: str) -> bool:
        src_node = source_text_id if source_text_id.startswith("source:") else f"source:{source_text_id.lower()}"
        inst_node = institution_id if institution_id.startswith("institution:") else f"institution:{institution_id.lower()}"
        if not (self.G.has_node(src_node) and self.G.has_node(inst_node)):
            return False
        if not self.G.has_edge(src_node, inst_node):
            self.G.add_edge(src_node, inst_node, relation="hosted_by")
        return True

    def link_written_by(self, source_text_id: str, agent_id: str) -> bool:
        """author of a written work, or artist/creator of an artwork/performance."""
        src = source_text_id if source_text_id.startswith("source:") else f"source:{source_text_id.lower()}"
        ag = agent_id if agent_id.startswith("agent:") else f"agent:{agent_id.lower()}"
        if not (self.G.has_node(src) and self.G.has_node(ag)):
            return False
        if not self.G.has_edge(src, ag):
            self.G.add_edge(src, ag, relation="written_by")
        return True

    def link_edited_by(self, source_text_id: str, agent_id: str) -> bool:
        """editor of an anthology/chapter, or curator of an exhibition."""
        src = source_text_id if source_text_id.startswith("source:") else f"source:{source_text_id.lower()}"
        ag = agent_id if agent_id.startswith("agent:") else f"agent:{agent_id.lower()}"
        if not (self.G.has_node(src) and self.G.has_node(ag)):
            return False
        if not self.G.has_edge(src, ag):
            self.G.add_edge(src, ag, relation="edited_by")
        return True

    def link_performed_by(self, source_text_id: str, agent_id: str) -> bool:
        """performer / dancer / cast member appearing in a performance (the
        performance's creators — choreographer/director — use written_by)."""
        src = source_text_id if source_text_id.startswith("source:") else f"source:{source_text_id.lower()}"
        ag = agent_id if agent_id.startswith("agent:") else f"agent:{agent_id.lower()}"
        if not (self.G.has_node(src) and self.G.has_node(ag)):
            return False
        if not self.G.has_edge(src, ag):
            self.G.add_edge(src, ag, relation="performed_by")
        return True

    def link_attributed_to(self, source_id: str, agent_id: str) -> bool:
        """Bridge edge (ontology §3.3): a `translation_mapping` OR `concept`
        node is attributable to the theorist/curator agent who originated or
        uses the concept."""
        ag = agent_id if agent_id.startswith("agent:") else f"agent:{agent_id.lower()}"
        if not (self.G.has_node(source_id) and self.G.has_node(ag)):
            return False
        if not self.G.has_edge(source_id, ag):
            self.G.add_edge(source_id, ag, relation="attributed_to")
        return True
    def link_instantiated_in(
        self, mapping_id: str, source_text_id: str
    ) -> bool:
        """Bridge edge (ontology §3.3): a translation_mapping was attested
        while translating the given source_text (the container work).
        Idempotent. Does NOT create or modify the mapping node — use this
        when the mapping already exists and you only need the bridge.
        """
        src = (
            source_text_id
            if source_text_id.startswith("source:")
            else f"source:{source_text_id.lower()}"
        )
        if not (self.G.has_node(mapping_id) and self.G.has_node(src)):
            return False
        if mapping_id == src:  # O-17 self-loop guard (defence-in-depth)
            return False
        if not self.G.has_edge(mapping_id, src):
            self.G.add_edge(mapping_id, src, relation="instantiated_in")
        return True
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

    def _protect_gender_tokens(self, text: str) -> Tuple[str, Dict[str, str]]:
        mappings = {}
        idx = 0

        def repl(match):
            nonlocal idx
            # \x02 (STX) / \x03 (ETX) control-character fences guarantee
            # the placeholder can never collide with natural text.
            placeholder = f"\x02GEND{idx}\x03"
            mappings[placeholder] = match.group(0)
            idx += 1
            return placeholder

        protected = SL_GENDER_INCLUSIVE_RE.sub(repl, text)
        return protected, mappings

    def _restore_gender_tokens(self, text: str, mappings: Dict[str, str]) -> str:
        restored = text
        for placeholder, original in mappings.items():
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
                                    "mapping_id": mapping_id,
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
                                "mapping_id": None,  # legacy edge, no explicit mapping node
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
        *,
        source_text_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        """No-op: NLP-based term/concept extraction removed.

        Concepts are extracted by the Ollama smol pipeline (extract_and_ingest).
        Terms enter the KG via glossary entries (workspace glossary UI → KG sync).
        The TM save that used to accompany this call is handled separately in
        workspace.py before this method is invoked.
        """
        return {"src_terms": [], "tgt_terms": [], "verified": [], "created": []}


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
        # Link ONLY to a concept that already exists. Auto-creating a concept
        # per extracted term (the old behaviour) is what flooded the graph with
        # generic-word noise ("tree", "heart"). Real concepts are curated;
        # confirms attach terms to them but never mint new generic ones.
        slug = label.replace(" ", "_")
        cid = f"concept:{slug}"
        if not self.G.has_node(cid):
            return ""
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

    def remove_concept_node(self, concept_id: str) -> bool:
        """Remove a concept node and all incident edges.

        Phase 9 drain factory. Returns False (without mutating) when:
          * `concept_id` is not in the graph, or
          * the node is present but its `type` is not `"concept"`.

        NetworkX `DiGraph.remove_node(n)` deletes `n` together with every
        edge that touches it — incoming AND outgoing — so there is no need
        to enumerate incident `instantiates_concept`, `extends`, etc. edges
        manually. The guarantee callers rely on (no dangling edges from
        term nodes after the concept goes away) follows directly from that
        semantics; the test suite verifies it.

        This factory is the SINGLE legal entry point for concept deletion
        in the drain script. No raw `kg.G.remove_node(concept_id)` is
        permitted outside this method.
        """
        if not self.G.has_node(concept_id):
            return False
        if self.G.nodes[concept_id].get("type") != "concept":
            return False
        self.G.remove_node(concept_id)
        return True


    # ------------------------------------------------------------------
    # Agent dedup factory
    # ------------------------------------------------------------------
    def merge_agent_nodes(self, canonical_id: str, duplicate_id: str) -> bool:
        """Merge *duplicate_id* into *canonical_id*.

        Both must exist and be type ``"agent"``.  Re-points every edge
        incident on *duplicate_id* onto *canonical_id* (skipping
        self-loops and edges that already exist on the canonical node),
        unions metadata (alt_spellings, all_roles), sums mention_count,
        and removes *duplicate_id* from the graph.

        Returns ``True`` if the merge was performed, ``False`` on any
        precondition failure (missing node, wrong type, same id).
        """
        if canonical_id == duplicate_id:
            return False
        if not self.G.has_node(canonical_id) or not self.G.has_node(duplicate_id):
            return False
        if self.G.nodes[canonical_id].get("type") != "agent":
            return False
        if self.G.nodes[duplicate_id].get("type") != "agent":
            return False

        can_data = self.G.nodes[canonical_id]
        dup_data = self.G.nodes[duplicate_id]

        # ── Re-point out-edges (duplicate → target) ──
        for _, tgt, edata in list(self.G.out_edges(duplicate_id, data=True)):
            if tgt == canonical_id:
                continue  # drop self-loop
            if not self.G.has_edge(canonical_id, tgt):
                self.G.add_edge(canonical_id, tgt, **edata)

        # ── Re-point in-edges (source → duplicate) ──
        for src, _, edata in list(self.G.in_edges(duplicate_id, data=True)):
            if src == canonical_id:
                continue  # drop self-loop
            if not self.G.has_edge(src, canonical_id):
                self.G.add_edge(src, canonical_id, **edata)

        # ── Merge metadata ──
        def _union_list(a: list | None, b: list | None) -> list:
            seen: set[str] = set()
            result: list[str] = []
            for item in (a or []) + (b or []):
                if item not in seen:
                    seen.add(item)
                    result.append(item)
            return result

        # alt_spellings: union + add duplicate's own name
        dup_name = dup_data.get("name", "")
        can_alt = can_data.get("alt_spellings", []) or []
        dup_alt = dup_data.get("alt_spellings", []) or []
        merged_alt = _union_list(can_alt, dup_alt)
        if dup_name and dup_name not in merged_alt:
            merged_alt.append(dup_name)
        can_data["alt_spellings"] = merged_alt

        # all_roles: union
        can_data["all_roles"] = _union_list(
            can_data.get("all_roles", []), dup_data.get("all_roles", [])
        )

        # mention_count: sum
        can_data["mention_count"] = (
            (can_data.get("mention_count") or 0) + (dup_data.get("mention_count") or 0)
        )

        # role: prefer specific over generic "agent"
        can_role = can_data.get("role", "")
        dup_role = dup_data.get("role", "")
        if can_role in ("", "agent") and dup_role not in ("", "agent"):
            can_data["role"] = dup_role

        # ── Remove duplicate node ──
        self.G.remove_node(duplicate_id)
        return True

    def update_concept_metadata(
        self,
        concept_id: str,
        label: Optional[str] = None,
        domain: Optional[str] = None,
        definition: Optional[str] = None,
        *,
        label_orig: Optional[str] = ...,
        label_translation: Optional[str] = ...,
        orig_lang: Optional[str] = ...,
        translation_lang: Optional[str] = ...,
    ) -> bool:
        """Update fields on an existing conceptual container.

        Bilingual fields (label_orig, label_translation, orig_lang,
        translation_lang) use a sentinel default so that ``None`` means
        "don't change" while explicit ``None`` is not a useful value.
        Pass a real string to set, or omit to leave unchanged.
        """
        if not self.G.has_node(concept_id):
            return False

        node = self.G.nodes[concept_id]
        if label is not None:
            node["label"] = label
        if domain is not None:
            node["domain"] = domain
        if definition is not None:
            node["definition"] = definition
        # Ellipsis (...) is used as the default sentinel so that omitted
        # parameters are distinguishable from ``None`` (which means "clear
        # the field"). Since ``...`` is a singleton, ``is not ...`` works.
        for field, value in [
            ("label_orig", label_orig),
            ("label_translation", label_translation),
            ("orig_lang", orig_lang),
            ("translation_lang", translation_lang),
        ]:
            if value is not ...:
                node[field] = value
        return True

    def update_term_node(
        self,
        term_id: str,
        display_form: Optional[str] = None,
        is_animate: Optional[bool] = None,
        is_phrase: Optional[bool] = None,
    ) -> bool:
        """Update mutable fields on an existing term node."""
        if not self.G.has_node(term_id):
            return False

        node = self.G.nodes[term_id]
        if display_form is not None:
            old_display = node.get("display_form")
            node["display_form"] = display_form
            # Maintain variants list: remove old, add new
            variants = node.get("variants", [])
            if old_display and old_display in variants:
                variants.remove(old_display)
                self._exact_kp.remove_keyword(old_display)
            if display_form and display_form.lower() != node.get("term", "").lower():
                if display_form not in variants:
                    variants.append(display_form)

    def update_agent_node(
        self,
        agent_id: str,
        name: Optional[str] = None,
        role: Optional[str] = None,
    ) -> bool:
        """Update mutable fields on an existing agent node.

        Also keeps the O-12 quartet (dedup_group, alt_spellings, all_roles,
        mention_count) coherent, backfilling it for legacy bare agents.
        """
        if not self.G.has_node(agent_id):
            return False

        from .entity_extraction.name_dedup import dedup_group_key

        node = self.G.nodes[agent_id]
        if name is not None:
            node["name"] = name
            node["dedup_group"] = dedup_group_key(name)
            alt_spellings = node.setdefault("alt_spellings", [])
            if name not in alt_spellings:
                alt_spellings.append(name)
        if role is not None:
            node["role"] = role
            all_roles = node.setdefault("all_roles", [])
            if role not in all_roles:
                all_roles.append(role)
        # Backfill any missing O-12 fields (handles legacy bare-agent nodes).
        node.setdefault("dedup_group", dedup_group_key(node.get("name", "")))
        node.setdefault("alt_spellings", [node.get("name", "")])
        node.setdefault("all_roles", [node.get("role", "agent")])
        node.setdefault("mention_count", node.get("mention_count", 1))
        return True

    def update_source_text_node(
        self,
        source_id: str,
        title: Optional[str] = None,
        year: Optional[int] = None,
        author_id: Optional[str] = None,
        *,
        title_orig: Optional[str] = ...,
        title_translation: Optional[str] = ...,
        orig_lang: Optional[str] = ...,
        translation_lang: Optional[str] = ...,
        translation_edition: Optional[dict] = ...,
        project_type: Optional[str] = None,
    ) -> bool:
        """Update mutable fields on an existing source_text node.

        Only the canonical bilingual fields (title_orig, title_translation,
        orig_lang, translation_lang) and translation_edition may be written.
        The legacy fields title_en, title_sl, and slovenian_edition are
        forbidden per ontology invariant #4 and are not accepted by this
        method.
        """
        if not self.G.has_node(source_id):
            return False

        node = self.G.nodes[source_id]
        if title is not None:
            node["title"] = title
        if year is not None:
            node["year"] = year
        if project_type is not None:
            node["project_type"] = project_type
        # Ellipsis (...) is used as the default sentinel so that omitted
        # parameters are distinguishable from ``None`` (which means "clear the
        # field"). Since ``...`` is a singleton, ``is not ...`` works correctly.
        for field, value in [
            ("title_orig", title_orig),
            ("title_translation", title_translation),
            ("orig_lang", orig_lang),
            ("translation_lang", translation_lang),
        ]:
            if value is not ...:
                node[field] = value
        if translation_edition is not ...:
            node["translation_edition"] = translation_edition
        if author_id is not None:
            # Remove old author edge, add new one
            auth_node = f"agent:{author_id.lower()}" if not author_id.startswith("agent:") else author_id
            for _, target, edata in list(self.G.out_edges(source_id, data=True)):
                if edata.get("relation") == "written_by":
                    self.G.remove_edge(source_id, target)
            if self.G.has_node(auth_node):
                if not self.G.has_edge(source_id, auth_node):
                    self.G.add_edge(source_id, auth_node, relation="written_by")
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
