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
