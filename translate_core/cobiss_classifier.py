# translate_core/cobiss_classifier.py
#
# Classifies COBISS bibliography entries into container types
# (book_translation, article_translation, festival_programme, exhibition_catalogue)
# or cited types (book, magazine_article, journal_article, etc.) for
# Belina's own authored works.
#
# Per the pipeline restructuring plan (Phase 3):
# - When Belina is listed as translator → container type (book_translation, etc.)
# - When Belina is listed as first author with no translator role → cited type
#   (his own work: book, magazine_article, etc.)
# - When unclassifiable → None (flagged for curator review)

from __future__ import annotations

import unicodedata
import re

from .cobiss_parser import CobissEntry, CobissAgent

# The translator agent — Urban Belina
BELINA_LAST = "BELINA"
BELINA_SLUG = "urban-belina"

# Valid container project_types (O-16)
CONTAINER_TYPES = {
    "book_translation",
    "article_translation",
    "festival_programme",
    "exhibition_catalogue",
}

# Valid cited project_types for Belina's own works (O-16)
CITED_TYPES = {
    "book",
    "magazine_article",
    "journal_article",
    "book_chapter",
    "newspaper_article",
    "web_source",
    "interview",
    "thesis_dissertation",
}

# Valid institution kinds (O-14)
INSTITUTION_KINDS = {
    "publisher", "gallery", "museum", "university",
    "festival", "theatre", "journal", "organization",
    "sponsor", "country", "other",
}

# Valid agent roles (O-13 / ontology §2.5)
AGENT_ROLES = {
    "author", "translator", "editor", "curator", "artist",
    "interviewer", "interviewee", "choreographer", "director",
    "performer", "dancer", "composer", "dramaturg", "agent",
}


def _normalize_name(last: str, first: str) -> str:
    """NFKD-normalise and lowercase a name for matching."""
    parts = [last]
    if first:
        parts.append(first)
    combined = " ".join(parts)
    nfkd = unicodedata.normalize("NFKD", combined)
    s = "".join(c for c in nfkd if not unicodedata.combining(c))
    return s.lower().strip()


def is_belina(agent: CobissAgent) -> bool:
    """Check if an agent is Urban Belina (fuzzy name match)."""
    normalized = _normalize_name(agent.last_name, agent.first_name)
    return "belina" in normalized and "urban" in normalized
def is_belina(agent: CobissAgent) -> bool:
    """Check if an agent is Urban Belina (fuzzy name match)."""
    normalized = _normalize_name(agent.last_name, agent.first_name)
    return "belina" in normalized and "urban" in normalized


def _is_journal_article(entry: CobissEntry) -> bool:
    """Check if entry is a journal/magazine article (has ISSN or journal name)."""
    return bool(entry.issn) or bool(entry.journal_name)


def _is_festival_programme(entry: CobissEntry) -> bool:
    """Check if entry is a festival programme."""
    title_lower = (entry.title + " " + entry.title_en).lower()
    festival_words = {"festival", "cofestival", "programme", "program"}
    if any(w in title_lower for w in festival_words):
        return True
    pub_lower = (entry.publisher + " " + entry.publisher_city).lower()
    if "festival" in pub_lower or "cofestival" in pub_lower:
        return True
    return False


def _is_exhibition_catalogue(entry: CobissEntry) -> bool:
    """Check if entry is an exhibition catalogue."""
    title_lower = (entry.title + " " + entry.title_en).lower()
    exhibition_words = {
        "razstava", "exhibition", "retrospektivna", "retrospective",
        "katalog", "catalogue", "catalog", "zgibanka",
    }
    if any(w in title_lower for w in exhibition_words):
        return True
    pub_lower = (entry.publisher + " " + entry.publisher_city).lower()
    if any(w in pub_lower for w in ("muzej", "museum", "galerij", "gallery")):
        return True
    return False


def _classify_article_type(entry: CobissEntry) -> str:
    """Classify an article entry into magazine_article or journal_article."""
    jname = entry.journal_name.lower()
    magazine_names = {
        "vpogled", "maska", "i.d.i.o.t", "otočjeo", "neodvisni",
        "dialogi", "emzin", "gledališki list",
    }
    for mag in magazine_names:
        if mag in jname:
            return "magazine_article"
    return "magazine_article"


def classify_entry(entry: CobissEntry) -> tuple[str | None, str | None]:
    """Classify a COBISS entry into (project_type, belina_role).

    Per O-16 and the Phase 3 plan:
    - When Belina is translator → container type
    - When Belina is first author with no translator role → cited type (own work)
    - When unclassifiable → (None, None) for curator review
    """
    # Find Belina among the agents
    belina_agent = None
    belina_is_first = False
    belina_role = None

    for i, agent in enumerate(entry.agents):
        if is_belina(agent):
            belina_agent = agent
            belina_is_first = (i == 0)
            if agent.roles:
                belina_role = agent.roles[0]
            else:
                belina_role = "author"
            break

    # Determine if Belina is a translator (explicitly or implicitly)
    belina_is_translator = False
    if belina_agent:
        if "translator" in (belina_agent.roles or []):
            belina_is_translator = True
        if not belina_is_translator:
            for agent in entry.agents:
                if "translator" in (agent.roles or []):
                    belina_is_translator = True
                    break

    # If Belina is first author with no translator role → his own work
    if belina_agent and belina_is_first and not belina_is_translator:
        belina_role_out = belina_role or "author"
        if _is_journal_article(entry):
            return (_classify_article_type(entry), belina_role_out)
        if _is_festival_programme(entry):
            return ("festival_programme", belina_role_out)
        if _is_exhibition_catalogue(entry):
            return ("exhibition_catalogue", belina_role_out)
        if entry.isbn or entry.publisher:
            return ("book", belina_role_out)
        if entry.pages:
            return ("magazine_article", belina_role_out)
        return ("book", belina_role_out)

    # All other cases: container type (Belina translated/edited this work)
    if not belina_agent:
        belina_role_out = "translator"  # Implicit
    elif "translator" in (belina_agent.roles or []):
        belina_role_out = "translator"
    elif "editor" in (belina_agent.roles or []):
        belina_role_out = "editor"
    else:
        belina_role_out = belina_role or "author"

    if _is_festival_programme(entry):
        return ("festival_programme", belina_role_out)
    if _is_exhibition_catalogue(entry):
        return ("exhibition_catalogue", belina_role_out)
    if _is_journal_article(entry):
        return ("article_translation", belina_role_out)

    # Default: book translation
    return ("book_translation", belina_role_out)

def classify_institution_kind(name: str) -> str:
    """Classify an institution name into an O-14 kind.

    Uses keyword heuristics on the institution name (lowercase).
    """
    n = name.lower()

    # Museum/gallery
    if any(w in n for w in ("muzej", "museum", "galerij", "gallery", "galerie")):
        return "museum" if any(w in n for w in ("muzej", "museum")) else "gallery"

    # University
    if any(w in n for w in ("univerz", "university", "univ.", "znamstveno-raziskovalno")):
        return "university"

    # Festival
    if any(w in n for w in ("festival", "cofestival")):
        return "festival"

    # Theatre
    if any(w in n for w in ("gledališč", "theatr", "drama")):
        return "theatre"

    # Journal/publisher keywords
    if any(w in n for w in ("revij", "journal", "študijsk")):
        return "journal"

    # Publisher (fallback for most Slovenian publishers)
    publisher_keywords = (
        "založb", "založba", "društvo", "publishing", "press",
        "knjig", "center", "inštitut", "fund", "sklad",
    )
    if any(w in n for w in publisher_keywords):
        return "publisher"

    # Sponsor
    if any(w in n for w in ("sponsor", "podpir")):
        return "sponsor"

    # Country
    if any(w in n for w in ("republik", "ministrstv")):
        return "country"

    return "other"


__all__ = [
    "classify_entry",
    "classify_institution_kind",
    "is_belina",
    "BELINA_SLUG",
    "CONTAINER_TYPES",
    "CITED_TYPES",
    "INSTITUTION_KINDS",
    "AGENT_ROLES",
]