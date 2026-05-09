import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from translate_core.glossary import Glossary
from translate_core.knowledge_graph import KnowledgeGraph
from translate_core.tm import TranslationMemory


def seed():
    kg = KnowledgeGraph()
    tm = TranslationMemory()
    glossary = Glossary()

    # 1. Seed from TM
    if tm.entries:
        print(f"Seeding Knowledge Graph from TM with {len(tm.entries)} entries...")
        kg.seed_from_tm(tm.entries, source_lang="en", target_lang="sl")
    else:
        print("No TM entries found.")

    # 2. Seed from Glossary
    if glossary.entries:
        print(
            f"Seeding Knowledge Graph from Glossary with {len(glossary.entries)} terms..."
        )
        # ... glossary loop ...
    else:
        print("No Glossary entries.")

    kg.save()
    print("Knowledge Graph seeded and saved.")


if __name__ == "__main__":
    seed()
