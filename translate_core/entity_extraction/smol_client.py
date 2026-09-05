# translate_core/entity_extraction/smol_client.py
#
# Live smol-model client for editor-confirm entity extraction.
#
# Calls a local Ollama server (stdlib urllib, no new dependency) with the
# exact prompt the offline batch pipeline exports (format_extract_prompt)
# and parses the response with the same parser (parse_smol_response), so
# live and batch extractions produce identical record shapes.
#
# Availability is best-effort by design: when Ollama is down, the model is
# missing, or the call times out, extract_entities returns None and the
# segment is left for the offline batch pipeline (working.tmx →
# run_entity_extraction.py). Nothing is lost — the TM is the backstop.

from __future__ import annotations

import json
import logging
import re
import urllib.request

from .smol_extractor import SYSTEM_PROMPT, format_extract_prompt, parse_smol_response

log = logging.getLogger(__name__)

# Log "Ollama unreachable" once per outage, not once per confirmed segment.
_unavailable_logged = False


def extract_entities(
    src: str,
    tgt: str,
    origin: str,
    container_work_id: str = "",
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> list[dict] | None:
    """Extract entities from one bilingual segment via the live smol model.

    Returns a list of parsed entity dicts (possibly empty), or None when
    the LLM endpoint is unavailable. Callers treat None as "leave this
    segment for the offline batch pipeline".
    """
    global _unavailable_logged
    if model is None or base_url is None:
        import config

        model = model or config.SMOL_MODEL
        base_url = base_url or config.OLLAMA_URL

    prompt = format_extract_prompt(
        src=src, tgt=tgt, origin=origin, container_work_id=container_work_id
    )
    payload = json.dumps(
        {
            "model": model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except (OSError, ValueError) as e:
        # OSError covers URLError/connection refused/socket timeouts;
        # ValueError covers a non-JSON response body.
        if not _unavailable_logged:
            log.warning(
                "smol live extraction unavailable (%s) — confirmed segments "
                "will be picked up by the offline batch pipeline",
                e,
            )
            _unavailable_logged = True
        return None
    _unavailable_logged = False
    return parse_smol_response(body.get("response", ""))


_VERIFY_PROMPT_TEMPLATE = """\
You are a strict fact-checker for a translation knowledge graph.
Decide whether the SOURCE/TARGET segment below EXPLICITLY attributes the
concept "{label}" to the person "{author}" as its originator or holder.

SOURCE: {src}
TARGET: {tgt}
Claimed evidence (must occur verbatim in SOURCE or TARGET): "{span}"

Answer "yes" only if the segment genuinely and explicitly attributes this
concept to this person (e.g. "{author}'s notion of {label}", "as {author}
argues, the concept of {label}"). Answer "no" if the attribution is
inferred, coincidental, misattributed, or absent. Reply with exactly one
word: yes or no."""


def verify_attribution(
    src: str,
    tgt: str,
    concept_label: str,
    author_name: str,
    evidence_span: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> bool | None:
    """Second-pass verification of a single concept→agent attribution.

    Returns True (confirmed), False (rejected), or None when the LLM
    endpoint is unavailable (the attribution is then treated as
    unverified and queued for curator review rather than auto-written).
    """
    if model is None or base_url is None:
        import config
        model = model or config.SMOL_MODEL
        base_url = base_url or config.OLLAMA_URL

    prompt = _VERIFY_PROMPT_TEMPLATE.format(
        src=src, tgt=tgt, label=concept_label,
        author=author_name, span=evidence_span,
    )
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except (OSError, ValueError):
        return None  # unavailable → caller queues for review
    raw = (body.get("response") or "").strip().lower()
    if raw.startswith("yes"):
        return True
    if raw.startswith("no"):
        return False
    # Ambiguous response → do not auto-write.
    return None


_BLOCK_LABELS = ("footnotes", "list", "bibliography", "other")


def classify_numbered_block(
    sample_rows: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 30.0,
) -> str | None:
    """Classify an ambiguous numbered block found during book import.

    Used by doc_parser ladder detection when arithmetic structure and the
    citation-shape detectors cannot decide whether a run of numbered rows
    is a notes section, a plain list, or bibliography debris. Returns one
    of "footnotes" | "list" | "bibliography" | "other", or None when the
    LLM endpoint is unavailable — callers treat None as "leave the block
    unconverted and report it" (never guess silently).

    Prompt stays minimal per O-18: a one-line instruction plus the rows.
    """
    global _unavailable_logged
    if model is None or base_url is None:
        import config

        model = model or config.SMOL_MODEL
        base_url = base_url or config.OLLAMA_URL

    rows = "\n".join(r[:160] for r in sample_rows[:3])
    prompt = (
        "Classify this numbered block from a book. "
        "Answer with one word: footnotes, list, bibliography, or other.\n\n"
        f"{rows}"
    )
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except (OSError, ValueError) as e:
        if not _unavailable_logged:
            log.warning(
                "smol block classification unavailable (%s) — ambiguous "
                "numbered blocks left unconverted (see import report)",
                e,
            )
            _unavailable_logged = True
        return None
    _unavailable_logged = False
    answer = body.get("response", "").strip().lower()
    for label in _BLOCK_LABELS:
        if label in answer:
            return label
    return "other"


_PERSON_LABELS = ("PERSON", "OTHER")


def classify_person_names(
    labels: list[str],
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> dict[str, bool] | None:
    """Classify labels as PERSON or OTHER via the smol model.

    Returns a dict mapping each label to ``True`` (person name) or ``False``
    (not a person), or ``None`` if the Ollama endpoint is unreachable —
    callers must abort and mutate nothing in that case.

    Prompt stays minimal per O-18: numbered list, one line per item.
    """
    global _unavailable_logged
    if model is None or base_url is None:
        import config

        model = model or config.SMOL_MODEL
        base_url = base_url or config.OLLAMA_URL

    # Deduplicate to avoid asking about the same label twice.
    unique = sorted(set(labels))
    batch_size = 40
    results: dict[str, bool] = {}

    for start in range(0, len(unique), batch_size):
        batch = unique[start : start + batch_size]
        numbered = "\n".join(f"{i+1}. {lab}" for i, lab in enumerate(batch))
        prompt = (
            "Answer one line per item: <n>. PERSON or <n>. OTHER. "
            "PERSON = the name of a specific human being. "
            "Concepts, common words, places, organisations, work titles, and -isms are OTHER.\n\n"
            f"{numbered}"
        )
        payload = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
        except (OSError, ValueError) as e:
            if not _unavailable_logged:
                log.warning(
                    "smol person-name classification unavailable (%s) — "
                    "no nodes will be deleted",
                    e,
                )
                _unavailable_logged = True
            return None  # Abort entire call — caller must not act on partial data

        _unavailable_logged = False
        response_text = body.get("response", "").strip()

        # Parse response lines: "<n>. PERSON" or "<n>. OTHER"
        for line in response_text.splitlines():
            line = line.strip()
            # Accept "3. PERSON" or "3.PERSON" or "3) PERSON"
            m = re.match(r'(\d+)\s*[.)]\s*(PERSON|OTHER)', line, re.IGNORECASE)
            if not m:
                continue
            idx = int(m.group(1)) - 1  # 1-indexed
            if 0 <= idx < len(batch):
                results[batch[idx]] = m.group(2).upper() == "PERSON"

    # Labels not found in any response line default to False (not a person)
    return {lab: results.get(lab, False) for lab in labels}
