# conftest.py — pytest configuration for the in-process NiceGUI tests.
#
# Two needs:
#   1) Skip the heavy KG/Spacy/Stanza + MLX-model loads that fire from
#      main.py's @app.on_startup. Without this, every test takes 20-30s
#      while NLP models warm up.
#   2) Disable the AI auto-draft feature in tests by default. Tests that
#      assert on the editor's empty-target state would otherwise race with
#      _ai_draft populating the target via the stub translator.
import pytest

pytest_plugins = ["nicegui.testing.plugin"]


@pytest.fixture(autouse=True)
def _fast_backends(monkeypatch):
    """No-op heavy backend constructors and the MLX model load so each test
    spins up in well under a second."""
    # MLX model loading
    try:
        from translate_core import llm as _llm
        monkeypatch.setattr(
            _llm.MLXGenericTranslator, "_ensure_loaded", lambda self: None
        )
    except Exception:
        pass

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
        monkeypatch.setattr(_tm.TranslationMemory, "__init__",
                            lambda self, tm_dir=None: setattr(self, "entries", []))
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

    yield


@pytest.fixture(autouse=True)
def _disable_ai_pretranslate():
    """Each test starts with AI auto-draft OFF so the editor doesn't fire
    _ai_draft on empty segments. Tests that want it on can re-enable
    via app.storage.user explicitly."""
    from nicegui import app
    # Best-effort; storage may not exist in all test contexts.
    try:
        app.storage.user["ai_pretranslate"] = False
    except Exception:
        pass
    # Master switch stays ON in tests so the per-session toggle is the
    # only control. Tests that want AI fully disabled can set both.
    try:
        app.storage.general["ai_master_enabled"] = True
    except Exception:
        pass
    yield
