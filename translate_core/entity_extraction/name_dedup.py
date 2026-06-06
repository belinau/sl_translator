"""Conservative person-name canonicalisation.

DOES NOT auto-merge variants. Instead, each name gets a
`dedup_candidate_group` key so the kg_editor can present merge candidates
to the curator. Names are identity-bearing — auto-merge is dangerous.
"""

from __future__ import annotations

import re
import unicodedata


# Tokens we strip when normalising (titles, suffixes)
NAME_AFFIXES = {
    "dr", "dr.", "prof", "prof.", "phd", "ph.d.", "ph.d", "m.a.", "ma",
    "mr", "mr.", "mrs", "mrs.", "ms", "ms.", "sir", "lady",
    "jr", "jr.", "sr", "sr.", "ii", "iii", "iv",
}

ORG_TAILS = {
    "Arts", "Studies", "Music", "Movement", "Tendencies", "Theory",
    "Press", "Books", "Press,", "Editions",
    "Museum", "Gallery", "Galleries", "Galerija", "Muzej", "Foundation",
    "Festival", "Biennale", "Bienale", "Institute", "University", "School",
    "Academy", "Theatre", "Theater", "Centre", "Center", "Productions",
    "Library", "Archive", "Association", "Collective", "Cooperative",
}

COMMON_NOUN_PAIRS = {
    # Phrases that pattern-match "Capitalised Capitalised" but are not names
    "Modern Art", "Contemporary Art", "Cold War", "World War",
    "Graphic Arts", "Performing Arts", "Visual Arts", "Fine Arts",
    "Art History", "Cultural Studies", "Critical Theory",
    "New Tendencies", "Aligned Movement", "Non-Aligned Movement",
    "City Museum", "Modern Movement", "Dance Academy", "Performance Studies",
    "Urban Culture", "Out Of", "Based On", "More Than", "At Least",
    "In Order", "Of Course", "The New", "This Is", "It Is",
    "Eastern Europe", "Western Europe", "Soviet Union", "Yugoslav Federation",
    "Iron Curtain", "Berlin Wall",
}


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_person_name(name: str) -> str:
    """Lowercase, strip diacritics, strip titles and trailing punctuation.

    Used as a key to detect candidate matches — NOT as the canonical form.
    """
    s = name.strip().rstrip(",.;:")
    # Strip titles
    tokens = [t for t in re.split(r"\s+", s) if t]
    tokens = [t for t in tokens if t.lower().rstrip(".") not in NAME_AFFIXES]
    s = " ".join(tokens)
    s = _strip_diacritics(s).lower()
    s = re.sub(r"[^a-z\s.-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def looks_like_organization(name: str) -> bool:
    """Heuristic: looks like an org / movement / period rather than a person."""
    s = name.strip()
    if s in COMMON_NOUN_PAIRS:
        return True
    tokens = s.split()
    if not tokens:
        return True
    # ANY org-tail token in the phrase → organization (was only first/last)
    if any(t.rstrip(",.;:") in ORG_TAILS for t in tokens):
        return True
    # All-caps phrases (3+ tokens) are often org headings
    if all(t.isupper() and len(t) > 1 for t in tokens) and len(tokens) >= 3:
        return True
    return False


def dedup_group_key(name: str) -> str:
    """Stable key for grouping candidate matches.

    Same key → flagged as a merge candidate group; the curator decides
    whether to actually merge.

    Strategy:
    - Strip diacritics, lowercase
    - Token-shuffle (sort) the last names (handle "Michel Foucault" vs
      hypothetical reordering)
    - Keep first-initial + lastname canonical form: "m.foucault"
    - This collapses "M. Foucault", "Michel Foucault", "Foucault, M." into
      the same group, but keeps "Mark Foucault" separate (different initial).
    """
    norm = normalize_person_name(name)
    if not norm:
        return ""
    parts = [p for p in norm.replace(",", " ").split() if p]
    if not parts:
        return ""
    # Drop affixes already stripped, find lastname + first-initial
    # Heuristic: last token = lastname; first token = first-name (full or initial)
    if len(parts) == 1:
        return parts[0]
    first = parts[0].rstrip(".")
    last = parts[-1].rstrip(".")
    first_initial = first[0] if first else ""
    return f"{first_initial}.{last}"


def is_plausible_person_name(name: str) -> bool:
    """Final gate before emitting a person candidate.

    Rejects obvious organizations, very short tokens, and noise.
    """
    s = name.strip()
    if len(s) < 5:
        return False
    if looks_like_organization(s):
        return False
    tokens = s.split()
    if len(tokens) < 2 or len(tokens) > 5:
        # 1-token names are too ambiguous; 5+ is suspect
        return False
    if all(t.isupper() for t in tokens):
        # Pure all-caps is an artist heading, not a regular person line
        # (we treat artist_header separately)
        return False
    # Every token should have at least one alpha
    if not all(any(c.isalpha() for c in t) for t in tokens):
        return False
    return True
