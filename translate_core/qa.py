# translate_core/qa.py
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from .style_rules import citation_hints, emphasis_integrity, footnote_integrity, orthography_hints

# ---------------------------------------------------------------------------
# Optional NLP dependencies
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Spacy model-name conventions:  <lang>_core_<size>  (e.g. en_core_web_sm)
# ---------------------------------------------------------------------------
_SPACY_MODEL_NAMES: Dict[str, str] = {
    # Standard spaCy model naming – extend as you install more.
    # Only languages with an installed model are actually used;
    # entries here just tell us *which* model name to try for a given code.
    "en": "en_core_web_sm",
    "sl": "sl_core_news_sm",
    "de": "de_core_news_sm",
    "fr": "fr_core_news_sm",
    "es": "es_core_news_sm",
    "it": "it_core_news_sm",
    "pt": "pt_core_news_sm",
    "nl": "nl_core_news_sm",
    "pl": "pl_core_news_sm",
    "ru": "ru_core_news_sm",
    "uk": "uk_core_news_sm",
    "hr": "hr_core_news_sm",
    "cs": "cs_core_news_sm",
    "bg": "bg_core_news_sm",
    "da": "da_core_news_sm",
    "fi": "fi_core_news_sm",
    "nb": "nb_core_news_sm",
    "sv": "sv_core_news_sm",
    "ro": "ro_core_news_sm",
    "el": "el_core_news_sm",
    "hu": "hu_core_news_sm",
    "tr": "tr_core_news_sm",
}

# ---------------------------------------------------------------------------
# Language code normalisation
# ---------------------------------------------------------------------------
_LANG_MAP = {
    "sl": "sl",
    "slovenian": "sl",
    "slovenščina": "sl",
    "en": "en",
    "english": "en",
    "de": "de",
    "german": "de",
    "deutsch": "de",
    "fr": "fr",
    "french": "fr",
    "es": "es",
    "spanish": "es",
    "it": "it",
    "italian": "it",
    "pt": "pt",
    "portuguese": "pt",
    "nl": "nl",
    "dutch": "nl",
    "pl": "pl",
    "polish": "pl",
    "ru": "ru",
    "russian": "ru",
    "uk": "uk",
    "ukrainian": "uk",
    "hr": "hr",
    "croatian": "hr",
    "cs": "cs",
    "czech": "cs",
    "bg": "bg",
    "bulgarian": "bg",
    "da": "da",
    "danish": "da",
    "fi": "fi",
    "finnish": "fi",
    "nb": "nb",
    "norwegian": "nb",
    "sv": "sv",
    "swedish": "sv",
    "ro": "ro",
    "romanian": "ro",
    "el": "el",
    "greek": "el",
    "hu": "hu",
    "hungarian": "hu",
    "tr": "tr",
    "turkish": "tr",
}


def _norm_lang(lang: str) -> str:
    lang = lang.lower().strip()
    return _LANG_MAP.get(lang, lang)


# ---------------------------------------------------------------------------
# Lemmatiser cache – one NLP pipeline per language, lazily loaded
# ---------------------------------------------------------------------------
_nlp_cache: Dict[str, Any] = {}

# Sentinel stored when all backends failed so we don't retry every call
_NOT_AVAILABLE = object()


def _ensure_nlp(lang: str) -> Any:
    """Return an NLP pipeline for *lang*, creating it on first call.

    Priority per language:
      1. classla  – best for Slavic languages (if installed)
      2. stanza   – good multi-language (if installed)
      3. spacy    – fallback, many languages via ``_SPACY_MODEL_NAMES``
    """
    if lang in _nlp_cache:
        cached = _nlp_cache[lang]
        return None if cached is _NOT_AVAILABLE else cached

    nlp = None

    # --- 1. classla (Slavic specialists) ---
    if HAS_CLASSLA and classla is not None:
        try:
            nlp = classla.Pipeline(
                lang,
                processors="tokenize,lemma",
                use_gpu=False,
                verbose=False,
            )
        except Exception:
            nlp = None

    # --- 2. stanza (broad language support) ---
    if nlp is None and HAS_STANZA and stanza is not None:
        try:
            import torch

            _orig_load = torch.load

            def _patched_load(*args: Any, **kwargs: Any) -> Any:
                kwargs["weights_only"] = False
                return _orig_load(*args, **kwargs)

            torch.load = _patched_load
            try:
                nlp = stanza.Pipeline(
                    lang,
                    processors="tokenize,lemma",
                    use_gpu=False,
                    verbose=False,
                )
            finally:
                torch.load = _orig_load
        except Exception:
            nlp = None

    # --- 3. spaCy ---
    if nlp is None and HAS_SPACY and spacy is not None:
        model_name = _SPACY_MODEL_NAMES.get(lang)
        if model_name is not None:
            try:
                nlp = spacy.load(model_name, disable=["ner", "parser"])
            except Exception:
                nlp = None

    if nlp is None:
        logger.debug("No lemmatiser available for lang=%s – using word-split fallback", lang)
        _nlp_cache[lang] = _NOT_AVAILABLE
        return None

    _nlp_cache[lang] = nlp
    return nlp


def _lemmatize(text: str, lang: str) -> List[str]:
    """Return lower-cased lemmas for alphabetic tokens in *text*."""
    nlp = _ensure_nlp(lang)
    if nlp is None:
        # Fallback: extract alphabetic word tokens (strip punctuation)
        return [m.lower() for m in re.findall(r'\w+', text)]

    if HAS_CLASSLA and classla is not None and isinstance(nlp, classla.Pipeline):
        doc = nlp(text)
        return [w.lemma.lower() for s in doc.sentences for w in s.words if w.text.isalpha()]

    if HAS_STANZA and stanza is not None and type(nlp).__module__.startswith("stanza"):
        doc = nlp(text)
        return [w.lemma.lower() for s in doc.sentences for w in s.words if w.text.isalpha()]

    # spaCy path
    doc = nlp(text)
    return [t.lemma_.lower() for t in doc if t.is_alpha]


# ---------------------------------------------------------------------------
# QAEngine
# ---------------------------------------------------------------------------

class QAEngine:
    def __init__(self) -> None:
        # Pre-computed glossary lemma index, keyed by (src_lang, tgt_lang).
        # Each maps term text → tuple of lemmas.
        self._tgt_lemma_index: Dict[Tuple[str, str], Dict[str, Tuple[str, ...]]] = {}
        self._src_lemma_index: Dict[Tuple[str, str], Dict[str, Tuple[str, ...]]] = {}

    # ------------------------------------------------------------------
    # Lemma index – call once at startup with the full glossary
    # ------------------------------------------------------------------
    def build_lemma_index(self, entries: List[Dict]) -> None:
        """Pre-lemmatize all glossary terms for fast lemma-aware checking.

        Call once after glossary load with ``glossary.entries``.
        NLP models are loaded lazily on first call to :func:`_lemmatize`.
        """
        # Group entries by (source_lang, target_lang)
        groups: Dict[Tuple[str, str], List[Dict]] = {}
        for e in entries:
            key = (_norm_lang(e.get("source_lang", "en")),
                   _norm_lang(e.get("target_lang", "sl")))
            groups.setdefault(key, []).append(e)

        for key, group in groups.items():
            tgt_lemmas: Dict[str, Tuple[str, ...]] = {}
            src_lemmas: Dict[str, Tuple[str, ...]] = {}
            for e in group:
                tgt_term = e["target_term"]
                src_term = e["source_term"]
                if tgt_term not in tgt_lemmas:
                    tgt_lemmas[tgt_term] = tuple(_lemmatize(tgt_term, key[1]))
                if src_term not in src_lemmas:
                    src_lemmas[src_term] = tuple(_lemmatize(src_term, key[0]))
            self._tgt_lemma_index[key] = tgt_lemmas
            self._src_lemma_index[key] = src_lemmas

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------
    def check_segment(
        self,
        source: str,
        target: str,
        glossary_hits: Optional[List[Dict]] = None,
        src_lang: str = "en",
        tgt_lang: str = "sl",
        *,
        pipeline: str = "simple",
    ) -> List[Dict]:
        """
        Runs multiple QA checks on a segment.
        Returns a list of warnings: {"type": "warning|error", "message": "..."}
        """
        warnings: List[Dict] = []
        if not target.strip():
            return warnings

        src_lang = _norm_lang(src_lang)
        tgt_lang = _norm_lang(tgt_lang)

        # 1. Number Mismatch
        src_nums = re.findall(r'\d+', source)
        tgt_nums = re.findall(r'\d+', target)
        if Counter(src_nums) != Counter(tgt_nums):
            missing = set(src_nums) - set(tgt_nums)
            extra = set(tgt_nums) - set(src_nums)
            msg = "Number mismatch."
            if missing:
                msg += f" Missing: {', '.join(missing)}."
            if extra:
                msg += f" Extra: {', '.join(extra)}."
            warnings.append({"type": "warning", "message": msg})

        # 2. Glossary Discrepancy (lemma-aware)
        if glossary_hits:
            tgt_text_lemmas = _lemmatize(target, tgt_lang)
            src_text_lemmas = _lemmatize(source, src_lang)

            for g in glossary_hits:
                src_term = g['source_term']
                tgt_term = g['target_term']
                g_key = (_norm_lang(g.get("source_lang", src_lang)),
                         _norm_lang(g.get("target_lang", tgt_lang)))

                # --- Source side: does the source term appear (exact or lemma)? ---
                src_found = bool(re.search(re.escape(src_term), source, re.IGNORECASE))
                if not src_found:
                    src_term_lemmas = self._src_lemma_index.get(g_key, {}).get(src_term)
                    if src_term_lemmas is None:
                        src_term_lemmas = tuple(_lemmatize(src_term, g_key[0]))
                    src_found = all(lm in src_text_lemmas for lm in src_term_lemmas)

                if not src_found:
                    continue

                # --- Target side: does the target term appear (exact or lemma)? ---
                tgt_found = bool(re.search(re.escape(tgt_term), target, re.IGNORECASE))
                if not tgt_found:
                    tgt_term_lemmas = self._tgt_lemma_index.get(g_key, {}).get(tgt_term)
                    if tgt_term_lemmas is None:
                        tgt_term_lemmas = tuple(_lemmatize(tgt_term, g_key[1]))
                    tgt_found = all(lm in tgt_text_lemmas for lm in tgt_term_lemmas)

                if not tgt_found:
                    warnings.append({
                        "type": "error",
                        "message": (
                            f"Glossary violation: '{src_term}' should be "
                            f"translated as '{tgt_term}'."
                        ),
                    })

        # 3. Basic Punctuation/Formatting
        if source.endswith(('.', '!', '?')) and not target.endswith(('.', '!', '?')):
            warnings.append({"type": "warning", "message": "Source ends with punctuation, target does not."})
        elif not source.endswith(('.', '!', '?')) and target.endswith(('.', '!', '?')):
            warnings.append({"type": "warning", "message": "Target ends with punctuation, source does not."})
        # 4. Integrity & style-rule hints
        warnings.extend(footnote_integrity(source, target))
        warnings.extend(emphasis_integrity(source, target))
        is_footnote = source.lstrip().startswith("[^")
        if pipeline == "academic" and is_footnote:
            warnings.extend(citation_hints(target, tgt_lang))
        warnings.extend(orthography_hints(target, tgt_lang))

        return warnings