"""Slovenian noun + adjective declension form generator.

Pure-python, no NLP deps, no I/O. Generates declined forms (6 cases x
3 numbers x applicable genders) from a Slovenian noun or adjective lemma.

Purpose: the QA glossary check uses this to recover matches the
stanza/classla lemmatiser loses. stanza routinely mis-lemmatises Slovenian
declined forms (e.g. ``knjižnih -> knjižnih`` instead of ``knjižen``,
``delih -> del`` instead of ``delo``, ``avtorjem -> avtorje`` instead of
``avtor``). By generating every declined form from each glossary term's lemma
and matching surface target tokens against that set, we accept a declined
form even when the lemmatiser fails.

Slovenian declension -- full scope
==================================
Slovenian has four declensions per gender (Slovene-tradition grouping by the
genitive-singular ending) plus two adjective declensions. This module covers
the productive, common paradigms and unions hard/soft stem sub-paradigms
where the distinction is not lexically determined:

Nouns (Wiktionary "Appendix:Slovene_nouns" / Wikipedia "Slovene_declension"):

  Masculine
    1st (o-stem)      gen sg -a/-u  -- hard + soft + j-stem (-r/-vowel) union.
                       Animacy split (animate Acc sg = Gen sg = -a/-ja;
                       inanimate Acc sg = Nom sg = stem).
    2nd (a-stem)      gen sg -e  -- masc nouns ending -a (vodja, sodija).
                       Endings identical to 1st feminine -> reused.
    3rd (no endings)  -- letters, rare interjections; passthrough {lemma}.
    4th (adjectival)  gen sg -ega  -- nominalized adjectives (moški, dežurni).

  Feminine
    1st (a-stem)      gen sg -e  -- miza, knjiga, založba, revija.
    2nd (i-stem)      gen sg -i  -- consonant-final (pesem, stvar, ljubezen,
                       kost, misel, oblast, moč, noč, reč) + -ost abstracts.
    3rd (no endings)  -- female surnames / foreign names; passthrough.
    4th (adjectival)  gen sg -e, gen pl -ih  -- nominalized fem adjectives
                       (dežurna).

  Neuter
    1st (o-stem)      gen sg -a  -- hard -o / soft -e (delo, mesto, znanje).
      n-stem          -- infix -en- (ime, breme).
      s-stem          -- infix -es- (oko, uho, liho).
      t-stem          -- infix -et- (tele, dekle, otroče).
    2nd (a-stem)      -- personal pronouns only; out of scope.
    3rd (no endings)  -- numerals/verbal nouns; passthrough.
    4th (adjectival)  gen sg -ega  -- nominalized neuter adjectives (Krško).

Adjectives (Wikipedia "Slovene_declension" -- two declensions):

  1st adjectival      hard + soft stems, definite + indefinite forms.
    hard indefinite   stem = consonant (nov, lep, dober, trd).
    soft indefinite   n-stem, lemma ends -en (knjižen, moderen).
    definite -i       lemma ends -i (slovenski, neki, mali, veliki, tak).
  2nd adjectival      possessive -ov/-ev/-in (bratov, materin, očetov).

Out of scope (documented): verb conjugation; pronoun irregulars; the 3rd
declension (no-endings, rare); accentual alternations (this generator works
on spellings only -- accent is not written in the glossary/target text).

Safety
======
Forms are built only from the same lemma stem, so unrelated words never
match. A wrong paradigm merely generates forms that don't occur in the target
text -- it never over-matches, it only recovers matches the lemmatiser lost.
Where a lemma's paradigm is genuinely ambiguous (masc hard vs soft o-stem;
r-stem j-insertion) we UNION the candidate endings, which is safe by the same
argument.

Extension model
===============
Two override dicts are the designed extension points -- as the translator
surfaces more false-positive glossary terms, grow them rather than adding
ad-hoc rules:

  ``_NOUN_PARADIGM``   lemma -> explicit noun paradigm key (bypasses
                       ending-based inference, e.g. for consonant-final
                       feminine nouns like pesem/stvar/ljubezen, or n-stem
                       neuters like ime/breme).
  ``_STEM_FIX``        lemma -> productive stem (the part endings attach to),
                       for lemmas carrying an epenthetic fill vowel that
                       disappears in declined forms (dober -> dobr-,
                       pesem -> pesm-, ogenj -> ogn-).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Slovenian alphabet -- lemmas containing other characters (x, y, w, q, ...)
# are foreign / unknown and pass through unchanged.
# ---------------------------------------------------------------------------
_SL_ALPHA: frozenset[str] = frozenset("abcčdefgijklmnoprsštuvzž")

# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
_FORMS_CACHE: dict[str, set[str]] = {}

# ---------------------------------------------------------------------------
# Override dicts -- extension points (see module docstring).
# ---------------------------------------------------------------------------

# lemma -> noun paradigm key. Use when the ending does not determine the
# paradigm (consonant-final feminine nouns; -e/-o neuter n/s/t-stems).
_NOUN_PARADIGM: dict[str, str] = {
    # 2nd feminine (i-stem, consonant-final) -- feminine despite consonant end.
    "pesem": "fem_i",
    "stvar": "fem_i",
    "ljubezen": "fem_i",
    "misel": "fem_i",
    "kost": "fem_i",
    "oblast": "fem_i",
    "moč": "fem_i",
    "noč": "fem_i",
    "reč": "fem_i",
    # 1st neuter, n-stem -- infix -en-.
    "ime": "neuter_n",
    "breme": "neuter_n",
    # 1st neuter, s-stem -- infix -es-.
    "oko": "neuter_s",
    "uho": "neuter_s",
    # 1st neuter, t-stem -- infix -et-.
    "tele": "neuter_t",
}

# lemma -> productive stem (endings attach here). For lemmas whose
# nominative-singular form carries an epenthetic fill vowel that drops in
# the rest of the paradigm.
_STEM_FIX: dict[str, str] = {
    # adjectives: epenthetic -e- before r/l
    "dober": "dobr",
    "boljši": "boljš",  # comparative, just in case
    # feminine i-stem: epenthetic -e- before m/n
    "pesem": "pesm",
    "ljubezen": "ljubezn",
    # masc o-stem: epenthetic -e- before final consonant (ogenj -> ogn-,
    # aktivizem -> aktivizm-). Contrast sistem, where the -e- is
    # stem-inherent and stays -- the drop is lexical, hence listed here.
    "ogenj": "ogn",
    "aktivizem": "aktivizm",
    # neuter s-stem: oko -> oč-, uho -> uš-
    "oko": "oč",
    "uho": "uš",
}

# Lemmas that must be treated as adjectives regardless of ending.
_ADJ_LEMMAS: dict[str, str] = {
    # value is the adjective sub-paradigm key (see _ADJ_PARADIGMS).
    "knjižen": "soft_indef",
    "dober": "hard_indef",
    "lep": "hard_indef",
    "nov": "hard_indef",
}

# ---------------------------------------------------------------------------
# Noun paradigm tables -- lists of endings appended to the stem.
#
# Endings are unioned across hard/soft sub-paradigms where the distinction is
# not lexically determined (masculine o-stem). Over-generation is safe: a
# non-occurring form simply never matches a target token.
# ---------------------------------------------------------------------------

# 1st masculine, o-stem, ANIMATE. Union of:
#   hard  (korak):  -a -u -om -oma -i -ov -om -e -ih -i   (Acc sg = -a)
#   soft  (stric):  -a -u -em -ema -i -ev -em -e -ih -i
#   j-stem (avtor): -ja -ju -jem -jema -ji -jev -jem -je -jih -ji  (r/vowel stem)
# Plus the bare stem (Nom sg). Acc sg animate = Gen sg.
_NOUN_MASC_O_ANIMATE: tuple[str, ...] = (
    "", "a", "u", "om", "em", "oma", "ema", "i", "ov", "ev", "om", "em",
    "e", "ih",
    "ja", "ju", "jem", "jema", "ji", "jev", "je", "jih",
)

# 1st masculine, o-stem, INANIMATE. Acc sg = Nom sg = stem (no ending).
# Hard + soft + j-stem union, minus the animate-only -a/-ja accusative.
_NOUN_MASC_O_INANIMATE: tuple[str, ...] = (
    "", "a", "u", "om", "em", "oma", "ema", "i", "ov", "ev", "e", "ih",
    "ja", "ju", "jem", "jema", "ji", "jev", "je", "jih",
)

# 2nd masculine, a-stem (vodja, sodija, vojvoda). Endings identical to 1st
# feminine a-stem -- reused via _NOUN_FEM_A.

# 4th masculine, adjectival (moški, dežurni, Travnik). Definite masculine
# adjective endings. Stem = lemma minus final -i (or minus -∅ for the few
# indefinite nominators). Nom sg -i; gen sg -ega.
_NOUN_MASC_ADJ: tuple[str, ...] = (
    "i", "ega", "emu", "em", "im", "e", "a", "ih", "im", "imi", "oma",
)

# 1st feminine, a-stem (miza, knjiga, založba). Gen sg -e; gen pl zero.
_NOUN_FEM_A: tuple[str, ...] = (
    "a", "e", "i", "o", "ama", "am", "ah", "ami", "",
)

# 2nd feminine, i-stem / consonant-stem (pesem, kost, misel, oblast, moč).
# Gen sg -i; instr sg -jo/-ijo (r-stems -jo, m/n-stems -ijo).
_NOUN_FEM_I: tuple[str, ...] = (
    "", "i", "jo", "ijo", "ima", "im", "ih", "imi",
)

# 4th feminine, adjectival (dežurna). Definite feminine adjective endings;
# gen pl -ih. Stem = lemma minus -a.
_NOUN_FEM_ADJ: tuple[str, ...] = (
    "a", "e", "i", "o", "ima", "im", "ih", "imi",
)

# 1st neuter, o-stem (delo, mesto, znanje, leto). Hard -o / soft -e nom/acc.
# Gen pl zero (del -> del). Both -o and -e nom/acc emitted (union, safe).
_NOUN_NEUTER_O: tuple[str, ...] = (
    "o", "e", "a", "u", "om", "em", "i", "oma", "ema", "", "ih", "im",
)

# 1st neuter, n-stem (ime, breme). Infix -en-; stem = lemma + "en" (ime ->
# imen). Nom/Acc sg = lemma (zero ending on the short stem); other slots use
# the long stem. We emit lemma + long-stem endings.
_NOUN_NEUTER_N: tuple[str, ...] = (
    "a", "u", "om", "em", "i", "oma", "ema", "", "ih", "im",
)

# 1st neuter, s-stem (oko, uho). Infix -es-; stem via _STEM_FIX (oko -> oč;
# the infix adds -es-: oč + "es" + ending? Actual: oko, očes, očesa, očesu,
# očesom, očesih, očesi. We model stem = očes (stem_fix + "es") and emit
# o-stem endings, plus the lemma and the bare infix-stem.
_NOUN_NEUTER_S: tuple[str, ...] = (
    "a", "u", "om", "em", "i", "oma", "ema", "", "ih", "im",
)

# 1st neuter, t-stem (tele, dekle, otroče). Infix -et-; stem = lemma + "et".
_NOUN_NEUTER_T: tuple[str, ...] = (
    "a", "u", "om", "em", "i", "oma", "ema", "", "ih", "im",
)

# 4th neuter, adjectival (Krško, Dolensko). Definite neuter adjective endings.
_NOUN_NEUTER_ADJ: tuple[str, ...] = (
    "o", "e", "ega", "emu", "em", "im", "a", "ih", "im", "imi",
)

_NOUN_PARADIGMS: dict[str, tuple[str, ...]] = {
    "masc_o_animate": _NOUN_MASC_O_ANIMATE,
    "masc_o_inanimate": _NOUN_MASC_O_INANIMATE,
    "masc_adj": _NOUN_MASC_ADJ,
    "fem_a": _NOUN_FEM_A,
    "fem_i": _NOUN_FEM_I,
    "fem_adj": _NOUN_FEM_ADJ,
    "neuter_o": _NOUN_NEUTER_O,
    "neuter_n": _NOUN_NEUTER_N,
    "neuter_s": _NOUN_NEUTER_S,
    "neuter_t": _NOUN_NEUTER_T,
    "neuter_adj": _NOUN_NEUTER_ADJ,
}

# ---------------------------------------------------------------------------
# Adjective paradigm tables. Stem extraction differs per sub-paradigm; the
# masc nom sg indefinite (or the -i definite form) is always added separately
# as the lemma itself.
# ---------------------------------------------------------------------------

# 1st adjectival, hard indefinite (nov, lep, dober, trd). Stem = consonant
# stem (lemma, or _STEM_FIX for epenthetic-vowel lemmas). No leading -n-.
# Endings cover all genders/numbers/cases (definite -i included).
_ADJ_HARD_INDEF: tuple[str, ...] = (
    "i", "a", "o", "e", "ega", "emu", "em", "im",
    "ih", "im", "imi", "ima",
)

# 1st adjectival, soft indefinite (knjižen, moderen). n-stem: lemma ends -en,
# stem = lemma minus -en; endings carry a leading -n-.
_ADJ_SOFT_INDEF: tuple[str, ...] = (
    "ni", "na", "no", "ne", "nega", "nemu", "nem", "nim",
    "nih", "nim", "nimi", "nima", "en",
)

# 1st adjectival, definite -i (slovenski, neki, mali, veliki, tak). Lemma is
# the masc nom sg definite (-i); stem = lemma minus -i; hard endings.
_ADJ_DEFINITE_I: tuple[str, ...] = _ADJ_HARD_INDEF

# 2nd adjectival, possessive -ov/-ev/-in (bratov, materin, očetov). Stem =
# lemma (the -ov-/-in- stays); hard endings.
_ADJ_POSS: tuple[str, ...] = _ADJ_HARD_INDEF

_ADJ_PARADIGMS: dict[str, tuple[str, ...]] = {
    "hard_indef": _ADJ_HARD_INDEF,
    "soft_indef": _ADJ_SOFT_INDEF,
    "definite_i": _ADJ_DEFINITE_I,
    "poss": _ADJ_POSS,
}

# ---------------------------------------------------------------------------
# Categorisation
# ---------------------------------------------------------------------------


def _categorize(lemma: str) -> tuple[str, str]:
    """Return (kind, key): ('adj', adj_sub) or ('noun', noun_paradigm_key)."""
    if lemma in _ADJ_LEMMAS:
        return ("adj", _ADJ_LEMMAS[lemma])
    if lemma in _NOUN_PARADIGM:
        return ("noun", _NOUN_PARADIGM[lemma])
    # Adjective by ending. Possessive (-ov/-ev/-in) and soft -en first, then
    # definite -i adjectives (slovenski, neki, mali, veliki, tak). Plain -i
    # nouns are rare (3rd-declension passthrough); mistaking one for a
    # definite adjective only over-generates harmless non-occurring forms.
    if lemma.endswith("ov") or lemma.endswith("ev") or lemma.endswith("in"):
        return ("adj", "poss")
    if lemma.endswith("en"):
        return ("adj", "soft_indef")
    if lemma.endswith("i"):
        return ("adj", "definite_i")
    # Noun by ending.
    if lemma.endswith("a"):
        # -a: feminine a-stem (also covers masc 2nd a-stem -- same endings).
        return ("noun", "fem_a")
    if lemma.endswith("em"):
        # Masculine o-stem inanimate whose nom sg carries a fill vowel before
        # a final -m (aktivizem, sistem). The fill e drops before consonant
        # clusters (aktivizem -> aktivizm-) and stays otherwise (sistem ->
        # sistem-); _noun_stem applies that rule. No Slovenian neuter ends in
        # -em (the n-stem neuters ime/breme end in -me and are overridden
        # above), so this default is safe.
        return ("noun", "masc_o_inanimate")
    if lemma.endswith("o") or lemma.endswith("e"):
        return ("noun", "neuter_o")
    # Consonant-final: default masc o-stem animate; _NOUN_PARADIGM overrides
    # to fem_i / neuter_n etc. for known exceptions.
    return ("noun", "masc_o_animate")


def _adj_stem(lemma: str, sub: str) -> str:
    """Return the productive stem for an adjective lemma of sub-paradigm *sub*."""
    if lemma in _STEM_FIX:
        return _STEM_FIX[lemma]
    if sub == "soft_indef":
        return lemma[:-2] if lemma.endswith("en") else lemma
    if sub == "definite_i":
        # Strip the final -i (slovenski -> slovensk, neki -> nek, mali -> mal).
        if lemma.endswith("i"):
            return lemma[:-1]
        return lemma
    if sub == "poss":
        # -ov / -ev / -in stays in the stem (bratov, materin).
        return lemma
    # hard_indef: strip a matched epenthetic-vowel suffix if present, else the
    # lemma is already the consonant stem (lep, nov, trd).
    for suf in ("ek", "ok", "el", "iv", "al", "er"):
        if lemma.endswith(suf):
            return lemma[: -len(suf)]
    return lemma


def _noun_stem(lemma: str, paradigm: str) -> str:
    """Return the productive (long) stem for a noun lemma of the given paradigm."""
    # Neuter infix stems (n/s/t-stem): the lemma is the short nominative form
    # (ends in -e/-o). The long stem = bare stem + infix. The bare stem comes
    # from _STEM_FIX (oko -> oč, uho -> uš) or by stripping the final vowel.
    infix_stems = {"neuter_n": "en", "neuter_s": "es", "neuter_t": "et"}
    if paradigm in infix_stems:
        if lemma in _STEM_FIX:
            bare = _STEM_FIX[lemma]
        else:
            bare = lemma[:-1] if lemma[-1:] in ("o", "e") else lemma
        return bare + infix_stems[paradigm]
    base = _STEM_FIX[lemma] if lemma in _STEM_FIX else lemma
    if paradigm in ("fem_a", "fem_adj"):
        return base[:-1] if base.endswith("a") else base
    if paradigm == "neuter_o":
        return base[:-1] if base[-1:] in ("o", "e") else base
    if paradigm == "masc_adj":
        # Nom sg -i; stem = lemma minus -i (moški -> mošk).
        return base[:-1] if base.endswith("i") else base
    # masc_o_animate / masc_o_inanimate / fem_i: stem = base (consonant-final).
    # -em fill-vowel masculines (aktivizem -> aktivizm-): the e-drop is
    # lexical and not detectable from the ending (contrast sistem, where the
    # e stays), so it is handled via _STEM_FIX overrides, not here.
    return base


def generate_forms(lemma: str) -> set[str]:
    """Return all declined forms of a Slovenian noun or adjective lemma.

    Covers all 6 cases x 3 numbers x applicable genders for the lemma's
    paradigm (see module docstring for the full paradigm list). Returns
    ``{lemma}`` for foreign / uncategorisable lemmas, so callers always get a
    usable set. Results are cached in a module-level dict.
    """
    lemma = lemma.lower()
    key = lemma
    if key in _FORMS_CACHE:
        return _FORMS_CACHE[key]

    # Foreign / unknown lemma -> passthrough.
    if not set(key) <= _SL_ALPHA:
        _FORMS_CACHE[key] = {lemma}
        return {lemma}

    forms: set[str] = {lemma}
    kind, sub = _categorize(lemma)

    if kind == "adj":
        stem = _adj_stem(lemma, sub)
        for end in _ADJ_PARADIGMS[sub]:
            forms.add(stem + end)
    else:
        if sub not in _NOUN_PARADIGMS:
            _FORMS_CACHE[key] = forms
            return forms
        stem = _noun_stem(lemma, sub)
        for end in _NOUN_PARADIGMS[sub]:
            forms.add(stem + end)
        # Neuter s/t/n-stems: also add the bare infix-stem (gen pl zero) and,
        # for n-stems, the lemma is already in the set.
        if sub in ("neuter_s", "neuter_t", "neuter_n"):
            forms.add(stem)  # e.g. imen (rare), očes, telet

    _FORMS_CACHE[key] = forms
    return forms