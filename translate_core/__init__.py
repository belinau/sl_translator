# translate_core/__init__.py

from .doc_parser import DocumentParser
from .glossary import Glossary
from .knowledge_graph import KnowledgeGraph
from .llm import Translator  # CHANGED: Import the new unified Translator class
from .tm import TranslationMemory
from .qa import QAEngine

__all__ = [
    "TranslationMemory",
    "Glossary",
    "KnowledgeGraph",
    "Translator",
    "DocumentParser",
    "QAEngine",
]
