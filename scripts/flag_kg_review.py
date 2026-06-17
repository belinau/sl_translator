from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.validate_kg import validate

# Invariants whose offending entries name a fixable live NODE (exclude pure edge invariants).
NODE_LEVEL = {
    "ghost_node",
    "forbidden_node_type",
    "unknown_node_type",
    "agent_missing_required",
    "bad_role",
    "bad_kind",
    "source_no_title",
    "bad_project_type",
    "container_missing_translated_by",
    "fragment_title",
    "legacy_bilingual_field",
    "mapping_low_quality",
    "duplicate_source_stem",
    "duplicate_agent_token_set",
}

_ID_RE = re.compile(r"(?:agent|source|institution|concept|term|map):[^\s,'\]\[]+")


def flag_dubious(nodes: list[dict], edges: list[dict], dismissed: set[str]) -> list[dict]:
    """Return dubious live KG nodes for the /kg/kg-review scanner.

    Each item is a dict with ``id``, ``type``, and ``reason``.
    """
    by_id = {n["id"]: n for n in nodes if n.get("id")}
    violations = validate(nodes, edges)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for reason, entries in violations.items():
        if reason not in NODE_LEVEL:
            continue
        for entry in entries:
            for nid in _ID_RE.findall(str(entry)):
                if nid in by_id and nid not in dismissed and (nid, reason) not in seen:
                    seen.add((nid, reason))
                    out.append({"id": nid, "type": by_id[nid].get("type"), "reason": reason})
    return out
