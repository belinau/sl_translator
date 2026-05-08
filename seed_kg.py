import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent))

from translate_core.knowledge_graph import KnowledgeGraph

def seed():
    kg = KnowledgeGraph()
    # Add some sample entities
    kg.add_term_node("Apple", "en", concept_id="c_apple")
    kg.add_term_node("jabolko", "sl", concept_id="c_apple")
    kg.add_concept_node("c_apple", "Apple (Fruit)")
    
    kg.add_term_node("Transformer", "en", concept_id="c_transformer")
    kg.add_term_node("transformator", "sl", concept_id="c_transformer")
    kg.add_concept_node("c_transformer", "Transformer Architecture")
    
    kg.link_terms("term:en:Apple", "term:sl:jabolko", relation="equivalent")
    kg.link_terms("term:en:Transformer", "term:sl:transformator", relation="equivalent")
    
    kg.save()
    print("Knowledge Graph seeded with sample data.")

if __name__ == "__main__":
    seed()
