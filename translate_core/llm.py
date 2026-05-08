# translate_core/llm.py

import threading
from typing import Dict, List, Tuple

import config

# Force the use of the MLX implementation
USE_NLLB = False


class MLXGenericTranslator:
    """Uses mlx-lm for local Instruct models on Apple Silicon."""

    def __init__(self):
        self.model_name = getattr(
            config, "MLX_MODEL_NAME", "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit"
        )
        self._model = None
        self._tokenizer = None
        # CRITICAL FIX: This lock prevents the Mac GPU from crashing if you switch segments too fast
        self._lock = threading.Lock()

    def _ensure_loaded(self):
        if self._model is None:
            from mlx_lm import load

            print(f"\n[SYSTEM] Loading MLX model into memory: {self.model_name}...")
            self._model, self._tokenizer = load(self.model_name)
            print("[SYSTEM] Model loaded successfully!\n")

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        tm_matches: List[Dict] = None,
        glossary_hits: List[Dict] = None,
        concordance_hits: List[Dict] = None,
        kg_hits: List[Dict] = None,
    ) -> Tuple[str, str]:

        with self._lock:  # Only allow one translation at a time on the GPU
            self._ensure_loaded()
            from mlx_lm import generate

            user_content = []

            # 1. GLOSSARY (Strict Enforcement)
            if glossary_hits:
                user_content.append("### REQUIRED GLOSSARY TERMS (MANDATORY)")
                user_content.append("You MUST use these specific translations for the following terms if they appear in the source:")
                for g in glossary_hits:
                    user_content.append(f"- '{g['source_term']}' => '{g['target_term']}'")
                user_content.append("")

            # 2. KNOWLEDGE GRAPH (Contextual Insight)
            if kg_hits:
                user_content.append("### KNOWLEDGE GRAPH CONTEXT (ENTITIES & RELATIONS)")
                for k in kg_hits:
                    label = k.get('term') or k.get('label') or k.get('id')
                    rel_strs = []
                    for rel in k.get('relations', []):
                        rel_label = rel.get('term') or rel.get('label') or rel.get('id')
                        rel_type = rel.get('relation', 'related')
                        rel_strs.append(f"{rel_type} -> {rel_label}")
                    user_content.append(f"- Entity: {label}")
                    if rel_strs:
                        user_content.append(f"  Relations: {', '.join(rel_strs)}")
                user_content.append("")

            # 3. TM MATCHES (Style & Consistency)
            if tm_matches:
                user_content.append("### SIMILAR PAST TRANSLATIONS (STYLE REFERENCE)")
                for m in tm_matches[:2]:
                    user_content.append(f"Old Source: {m['source']}\nOld Target: {m['target']}")
                user_content.append("")

            # 4. CONCORDANCE (Contextual Examples)
            if concordance_hits:
                user_content.append("### CONTEXTUAL EXAMPLES")
                for c in concordance_hits[:2]:
                    user_content.append(f"Sample: {c['source']} -> {c['target']}")
                user_content.append("")

            user_content.append(f"### TEXT TO TRANSLATE NOW:\n{text}")
            full_user_content = "\n".join(user_content)

            # 5. SYSTEM PROMPT (Professional & Strict)
            lang_instruction = f"Translate from {source_lang} to {target_lang}."
            if "slovenian" in target_lang.lower() or "slovenščina" in target_lang.lower():
                lang_instruction = (
                    f"Translate from {source_lang} to standard Slovenian. "
                    "CRITICAL: Use '-ti' infinitives. AVOID Croatian/Serbian vocabulary."
                )

            messages = [
                {
                    "role": "system",
                    "content": (
                        f"You are a professional translator expert in {target_lang}. "
                        f"{lang_instruction}\n\n"
                        "STRICT RULES:\n"
                        "1. If a word is in the GLOSSARY, you MUST use the provided translation exactly.\n"
                        "2. Utilize KNOWLEDGE GRAPH context to ensure accurate terminology and entity consistency.\n"
                        "3. Match the style and vocabulary of the SIMILAR PAST TRANSLATIONS.\n"
                        "4. Output ONLY the translated text, no explanations."
                    ),
                },
                {"role": "user", "content": full_user_content},
            ]

            try:
                formatted_prompt = self._tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            except Exception:
                # Fallback for models without chat template support
                formatted_prompt = f"System: {messages[0]['content']}\nUser: {messages[1]['content']}\nAssistant:"

            response = generate(
                self._model,
                self._tokenizer,
                prompt=formatted_prompt,
                max_tokens=1024,
                verbose=False,
            )
            return full_user_content, response.strip()


class Translator:
    def __init__(self):
        if USE_NLLB:
            raise NotImplementedError("NLLB mode is not implemented.")
        else:
            print("Using Generic MLX LLM (Instruction-based Context Injection)")
            self._impl = MLXGenericTranslator()

    def translate(
        self, text, src, tgt, tm_matches=None, glossary_hits=None, concordance_hits=None, kg_hits=None
    ):
        return self._impl.translate(
            text, src, tgt, tm_matches, glossary_hits, concordance_hits, kg_hits
        )
