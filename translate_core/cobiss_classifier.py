# translate_core/cobiss_classifier.py
#
# Classifies COBISS bibliography entries into container types
# (book_translation, article_translation, festival_programme, exhibition_catalogue)
# or cited types (book, magazine_article, journal_article, etc.) for
# the curator's own authored works.
#
# Per the pipeline restructuring plan (Phase 3):
# - When the curator is listed as translator → container type (book_translation, etc.)
# - When the curator is listed as first author with no translator role → cited type
#   (own work: book, magazine_article, etc.)
# - When unclassifiable → None (flagged for curator review)

from __future__ import annotations

import unicodedata

from .cobiss_parser import CobissEntry, CobissAgent

# The translator/curator agent
CURATOR_LAST = "BELINA"
CURATOR_FIRST = "Urban"
CURATOR_NAME = f"{CURATOR_FIRST} {CURATOR_LAST.capitalize()}"
CURATOR_SLUG = "urban-belina"

# Valid container project_types (O-16)
CONTAINER_TYPES = {
    "book_translation",
    "article_translation",
    "festival_programme",
    "exhibition_catalogue",
}

# Valid cited project_types for the curator's own works (O-16)
CITED_TYPES = {
    "book",
    "magazine_article",
    "journal_article",
    "festival_programme",
    "exhibition_catalogue",
}

# Valid institution kinds (O-14)
INSTITUTION_KINDS = {
    "publisher",
    "gallery",
    "museum",
    "university",
    "festival",
    "theatre",
    "journal",
    "organization",
    "sponsor",
    "country",
    "other",
}

# Valid agent roles (O-13 / ontology §2.5)
AGENT_ROLES = {
    "author",
    "translator",
    "editor",
    "curator",
    "artist",
    "interviewer",
    "interviewee",
    "choreographer",
    "director",
    "performer",
    "dancer",
    "composer",
    "dramaturg",
    "agent",
}


def _normalize_name(last: str, first: str) -> str:
    """NFKD-normalise and lowercase a name for matching."""
    s = unicodedata.normalize("NFKD", f"{last} {first}")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def is_curator(agent: CobissAgent) -> bool:
    """Check if an agent is the curator (fuzzy name match)."""
    normalized = _normalize_name(agent.last_name, agent.first_name)
    return "belina" in normalized and "urban" in normalized


def _is_journal_article(entry: CobissEntry) -> bool:
    """Check if entry is a journal/magazine article (has ISSN or journal name)."""
    return bool(entry.issn) or bool(entry.journal_name)


def _is_festival_programme(entry: CobissEntry) -> bool:
    """Check if entry is a festival programme."""
    n = entry.title.lower()
    if any(w in n for w in ("festival", "cofestival", "festivalna", "festivalska")):
        return True
    for a in entry.agents:
        if "curator" in (a.roles or []):
            return True
    return False


def _is_exhibition_catalogue(entry: CobissEntry) -> bool:
    """Check if entry is an exhibition catalogue."""
    n = entry.title.lower()
    if any(w in n for w in ("katalog", "catalogue", "razstava", "exhibition")):
        return True
    for a in entry.agents:
        if "curator" in (a.roles or []):
            return True
    return False


def _classify_article_type(entry: CobissEntry) -> str:
    """Classify an article entry into magazine_article or journal_article."""
    if entry.issn:
        return "journal_article"
    if entry.journal_name:
        j = entry.journal_name.lower()
        if any(w in j for w in ("revij", "journal", "študijsk")):
            return "journal_article"
    return "magazine_article"


def classify_entry(entry: CobissEntry) -> tuple[str | None, str | None]:
    """Classify a COBISS entry into (project_type, curator_role).

    Per O-16 and the Phase 3 plan:
    - When the curator is translator → container type
    - When the curator is first author with no translator role → cited type (own work)
    - When unclassifiable → (None, None) for curator review
    """
    # Find the curator among the agents
    curator_agent = None
    curator_is_first = False
    curator_role = None

    for i, agent in enumerate(entry.agents):
        if is_curator(agent):
            curator_agent = agent
            curator_is_first = (i == 0)
            if agent.roles:
                curator_role = agent.roles[0]
            else:
                curator_role = "author"
            break

    # Determine if the curator is a translator (explicitly or implicitly)
    curator_is_translator = False
    if curator_agent:
        if "translator" in (curator_agent.roles or []):
            curator_is_translator = True
        if not curator_is_translator:
            for agent in entry.agents:
                if "translator" in (agent.roles or []):
                    curator_is_translator = True
                    break

    # If the curator is first author with no translator role → own work
    if curator_agent and curator_is_first and not curator_is_translator:
        curator_role_out = curator_role or "author"
        if _is_journal_article(entry):
            return (_classify_article_type(entry), curator_role_out)
        if _is_festival_programme(entry):
            return ("festival_programme", curator_role_out)
        if _is_exhibition_catalogue(entry):
            return ("exhibition_catalogue", curator_role_out)
        if entry.isbn or entry.publisher:
            return ("book", curator_role_out)
        if entry.pages:
            return ("magazine_article", curator_role_out)
        return ("book", curator_role_out)

    # All other cases: container type (the curator translated/edited this work)
    if not curator_agent:
        curator_role_out = "translator"  # Implicit
    elif "translator" in (curator_agent.roles or []):
        curator_role_out = "translator"
    elif "editor" in (curator_agent.roles or []):
        curator_role_out = "editor"
    else:
        curator_role_out = curator_role or "author"

    if _is_festival_programme(entry):
        return ("festival_programme", curator_role_out)
    if _is_exhibition_catalogue(entry):
        return ("exhibition_catalogue", curator_role_out)
    if _is_journal_article(entry):
        return ("article_translation", curator_role_out)

    # Default: book translation
    return ("book_translation", curator_role_out)

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
    "is_curator",
    "CURATOR_SLUG",
    "CURATOR_NAME",
    "CONTAINER_TYPES",
    "CITED_TYPES",
    "INSTITUTION_KINDS",
    "AGENT_ROLES",
]