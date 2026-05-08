import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent))

from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.tm import TranslationMemory
from translate_core.glossary import Glossary

def seed():
    kg = KnowledgeGraph()
    tm = TranslationMemory()
    glossary = Glossary()
    
    # 1. Seed from TM (bulk extraction of terms and co-occurrences)
    if tm.entries:
        print(f"Seeding Knowledge Graph from TM with {len(tm.entries)} entries...")
        kg.seed_from_tm(tm.entries, source_lang="en", target_lang="sl")
    else:
        print("No TM entries found. Skipping TM seeding.")
        
    # 2. Seed verified terminology from Glossary
    if glossary.entries:
        print(f"Seeding Knowledge Graph from Glossary with {len(glossary.entries)} verified terms...")
        added_glossary = 0
        for entry in glossary.entries:
            if entry.get("source_lang") == "en" and entry.get("target_lang") == "sl":
                kg.promote_pair(
                    source_text=entry["source_term"],
                    target_text=entry["target_term"],
                    source_lang="en",
                    target_lang="sl",
                    verified=True,
                    domain="glossary"
                )
                added_glossary += 1
        print(f"Promoted {added_glossary} verified EN->SL pairs from Glossary.")
    else:
        print("No Glossary entries found.")
    
    kg.save()
    print("Knowledge Graph seeded and saved successfully.")

if __name__ == "__main__":
    seed()
