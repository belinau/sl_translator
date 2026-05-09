import config
from translate_core.knowledge_graph import KnowledgeGraph

# 1. Initialize the Knowledge Graph
kg = KnowledgeGraph()

# 2. (Optional) Populate it if it's empty, or it will load from config.KG_DB_PATH automatically
# kg.seed_from_tm(my_tm_entries)
# kg.save()

# 3. Generate the visualization
# This will create a file named 'kg_inspect.html' in your current directory
kg.visualize(output_path="kg_inspect.html", limit=50)

print("Open 'kg_inspect.html' in your browser to explore the graph.")
