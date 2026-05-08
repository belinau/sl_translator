# translate_core/knowledge_graph.py

import json
import pathlib
from typing import Any, Dict, List

import networkx as nx

import config


from flashtext import KeywordProcessor

class KnowledgeGraph:
    """
    Tiny knowledge graph stored as NetworkX + JSON.
    Nodes: {"id": "...", "type": "term|concept|doc|...", "label": "..."}
    Edges: {"source": "...", "target": "...", "relation": "..."}
    """

    def __init__(self, db_path: pathlib.Path = config.KG_DB_PATH):
        self.db_path = pathlib.Path(db_path)
        self.G = nx.DiGraph()
        self.kp = KeywordProcessor()
        self._load()

    def _load(self):
        if not self.db_path.exists():
            return

        try:
            data = json.loads(self.db_path.read_text(encoding="utf-8"))
            for node in data.get("nodes", []):
                self.G.add_node(node["id"], **node)
                if node.get("type") == "term":
                    self.kp.add_keyword(node["term"], node["id"])
            for edge in data.get("edges", []):
                self.G.add_edge(
                    edge["source"],
                    edge["target"],
                    relation=edge.get("relation", "related_to"),
                )
        except Exception:
            # If DB is corrupted, start fresh
            self.G.clear()

    def extract_entities(self, text: str) -> List[Dict]:
        """
        Finds terms in the text that are nodes in the KG.
        Returns a list of nodes with their relations (including translations via concepts).
        """
        found_ids = self.kp.extract_keywords(text)
        results = []
        for node_id in set(found_ids):
            node_data = self.G.nodes[node_id].copy()
            # Increase depth to 2 to find sibling terms sharing a concept
            neighbors = self.find_neighbors(node_id, max_depth=2)
            node_data["relations"] = neighbors
            results.append(node_data)
        return results

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
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_term_node(
        self,
        term: str,
        lang: str,
        concept_id: str = None,
        metadata: Dict[str, Any] = None,
    ):
        node_id = f"term:{lang}:{term}"
        if not self.G.has_node(node_id):
            self.G.add_node(
                node_id,
                type="term",
                term=term,
                lang=lang,
                **(metadata or {}),
            )
        if concept_id:
            self.G.add_edge(node_id, concept_id, relation="belongs_to_concept")

    def add_concept_node(
        self, concept_id: str, label: str, metadata: Dict[str, Any] = None
    ):
        if not self.G.has_node(concept_id):
            self.G.add_node(
                concept_id,
                type="concept",
                label=label,
                **(metadata or {}),
            )

    def link_terms(self, term1_id: str, term2_id: str, relation: str = "equivalent"):
        self.G.add_edge(term1_id, term2_id, relation=relation)

    def search_related(self, term: str, lang: str) -> List[str]:
        """
        Finds semantic neighbors of a term in the same language.
        """
        node_id = f"term:{lang}:{term}"
        if not self.G.has_node(node_id):
            return []
        
        neighbors = self.find_neighbors(node_id, max_depth=1)
        results = []
        for n in neighbors:
            if n.get("type") == "term" and n.get("lang") == lang:
                results.append(n.get("term"))
        return results

    def search_prefix(self, prefix: str, target_lang: str) -> List[str]:
        """
        Finds terms in the KG that start with the prefix in the target language.
        """
        if not prefix: return []
        prefix_low = prefix.lower()
        matches = []
        for node_id, data in self.G.nodes(data=True):
            if data.get("type") == "term" and data.get("lang") == target_lang:
                term = data.get("term", "")
                if term.lower().startswith(prefix_low):
                    matches.append(term)
        return list(set(matches))

    def find_neighbors(self, node_id: str, max_depth: int = 1) -> List[Dict]:
        """
        Return neighbor nodes up to max_depth.
        """
        if not self.G.has_node(node_id):
            return []

        lengths = nx.single_source_shortest_path_length(
            self.G, node_id, cutoff=max_depth
        )
        results = []
        for other_id, depth in lengths.items():
            if other_id == node_id:
                continue
            results.append(
                {
                    "id": other_id,
                    "depth": depth,
                    **self.G.nodes[other_id],
                    "relation": self.G.edges[node_id, other_id].get(
                        "relation", "related_to"
                    )
                    if self.G.has_edge(node_id, other_id)
                    else None,
                }
            )
        return results
