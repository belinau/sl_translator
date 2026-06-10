# conftest.py — pytest configuration for the in-process NiceGUI tests.
#
# Skip the heavy KG/Spacy/Stanza loads that fire from main.py's
# @app.on_startup. Without this, every test takes 20-30s while NLP
# models warm up.
import pytest

pytest_plugins = ["nicegui.testing.plugin"]


@pytest.fixture(autouse=True)
def _fast_backends(monkeypatch):
    """No-op heavy backend constructors so each test spins up in well
    under a second."""
    # KnowledgeGraph: skip Spacy/Classla/Stanza loads and the JSON read.
    try:
        from translate_core import knowledge_graph as _kg

        def _fast_kg_init(self, db_path=None):
            import networkx as nx
            from flashtext import KeywordProcessor
            self.db_path = db_path
            self.G = nx.DiGraph()
            self._exact_kp = KeywordProcessor(case_sensitive=False)
            self._norm_kp = KeywordProcessor(case_sensitive=False)
            self.nlp_en = None
            self.nlp_sl = None

        monkeypatch.setattr(_kg.KnowledgeGraph, "__init__", _fast_kg_init)
    except Exception:
        pass

    # TranslationMemory + Glossary: skip TMX/TSV scans.
    try:
        from translate_core import tm as _tm
        monkeypatch.setattr(
            _tm.TranslationMemory, "__init__",
            lambda self, tm_dir=None: setattr(self, "entries", []),
        )
    except Exception:
        pass
    try:
        from translate_core import glossary as _gl
        def _fast_gl_init(self, glossary_dir=None):
            self.entries = []
            self._kp_src = None
            self._kp_tgt = None
        monkeypatch.setattr(_gl.Glossary, "__init__", _fast_gl_init)
    except Exception:
        pass

    # Live smol extraction: never call Ollama from UI tests.
    try:
        import config as _config
        monkeypatch.setattr(_config, "SMOL_LIVE_EXTRACTION", False)
    except Exception:
        pass

    yield
