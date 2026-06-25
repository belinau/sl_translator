"""Tests for translate_core.sl_morph -- Slovenian declension form generator.

Covers the major noun + adjective paradigms (masculine o-stem animate with
j-insertion, feminine a-stem and i-stem, neuter o-stem and n/s/t-stem,
nominalized adjectives, hard/soft/possessive/definite-i adjectives) plus the
passthrough and caching behaviour.
"""

from translate_core.sl_morph import generate_forms, _FORMS_CACHE


def _assert_all(lemma: str, expected: list[str]) -> None:
    forms = generate_forms(lemma)
    missing = [e for e in expected if e not in forms]
    assert not missing, f"{lemma!r}: missing {missing} in {sorted(forms)}"


# ---------------------------------------------------------------------------
# Nouns -- masculine
# ---------------------------------------------------------------------------

class TestMascOAnimateAvtor:
    def test_forms(self):
        # Explicitly asserts the form stanza got wrong (avtorjem -> avtorje).
        _assert_all("avtor", [
            "avtor", "avtorja", "avtorju", "avtorjem",
            "avtorji", "avtorjev", "avtorjih",
        ])


class TestMascAStemVodja:
    def test_forms(self):
        # 2nd masculine a-stem (vodja) shares endings with 1st feminine.
        _assert_all("vodja", ["vodja", "vodje", "vodji", "vodjo", "vodjama"])


class TestMascAdjectivalMoski:
    def test_forms(self):
        # 4th masculine -- nominalized adjective (moški).
        _assert_all("moški", [
            "moški", "moškega", "moškemu", "moškem", "moškim",
            "moških", "moškimi", "moške",
        ])


# ---------------------------------------------------------------------------
# Nouns -- feminine
# ---------------------------------------------------------------------------

class TestFemAStemMiza:
    def test_forms(self):
        _assert_all("miza", ["miza", "mize", "mizi", "mizo", "mizah", "mizami"])


class TestFemIStemPesem:
    def test_forms(self):
        # pesem is feminine despite a consonant ending -- _NOUN_PARADIGM override
        # plus _STEM_FIX (pesem -> pesm-).
        _assert_all("pesem", ["pesem", "pesmi", "pesmijo", "pesmih", "pesmim"])


class TestMascOStemAktivizem:
    def test_forms(self):
        # Masc o-stem inanimate with epenthetic fill -e- before final -m
        # (aktivizem -> aktivizm-). The declined form aktivizma (gen sg) is
        # the one the lemmatiser loses.
        _assert_all("aktivizem", [
            "aktivizem", "aktivizma", "aktivizmu", "aktivizmom",
            "aktivizmov", "aktivizmih", "aktivizmi",
        ])

    def test_sistem_keeps_e(self):
        # Contrast: in sistem the -e- is stem-inherent and stays (sistema,
        # not sistma). The e-drop is lexical, detected via _STEM_FIX only.
        forms = generate_forms("sistem")
        assert "sistema" in forms
        assert "sistemu" in forms
        assert "sistma" not in forms


class TestFemIStemKost:
    def test_forms(self):
        # r-stem consonant feminine: instrumental singular -jo (no -ijo).
        _assert_all("kost", ["kost", "kosti", "kostjo", "kostih", "kostim"])


# ---------------------------------------------------------------------------
# Nouns -- neuter
# ---------------------------------------------------------------------------

class TestNeuterOStemDelo:
    def test_forms(self):
        _assert_all("delo", [
            "delo", "dela", "delu", "delih", "delom", "deli", "del",
        ])


class TestNeuterOStemMesto:
    def test_forms(self):
        # Stem with consonant cluster (mest-) preserved; gen pl zero -> mest.
        _assert_all("mesto", ["mesto", "mesta", "mestu", "mestom", "mesti", "mest", "mestih"])


class TestNeuterNStemIme:
    def test_forms(self):
        # n-stem infix -en- (ime -> imen-).
        _assert_all("ime", ["ime", "imena", "imenu", "imenom", "imeni", "imen", "imenih"])


class TestNeuterSStemOko:
    def test_forms(self):
        # s-stem infix -es- with k->č alternation (oko -> očes-).
        _assert_all("oko", ["oko", "očesa", "očesu", "očesom", "očesi", "očes", "očesih"])


class TestNeuterTStemTele:
    def test_forms(self):
        # t-stem infix -et- (tele -> telet-).
        _assert_all("tele", ["tele", "teleta", "teletu", "teletom", "teleti", "telet", "teletih"])


# ---------------------------------------------------------------------------
# Adjectives
# ---------------------------------------------------------------------------

class TestAdjSoftKnjizen:
    def test_forms(self):
        # knjižnih is the form stanza failed to lemmatise.
        _assert_all("knjižen", [
            "knjižen", "knjižna", "knjižno", "knjižnega",
            "knjižnemu", "knjižnih", "knjižnimi",
        ])


class TestAdjHardDober:
    def test_forms(self):
        # Exercises _STEM_FIX (dober -> dobr-).
        _assert_all("dober", ["dober", "dobra", "dobro", "dobrega", "dobrim", "dobrimi"])


class TestAdjHardNov:
    def test_forms(self):
        # Hard adjective whose lemma is already the consonant stem.
        _assert_all("nov", ["nov", "nova", "novo", "novega", "novim", "novimi", "novih"])


class TestAdjDefiniteISlovenski:
    def test_forms(self):
        # Definite -i adjective (slovenski -> slovensk-).
        _assert_all("slovenski", [
            "slovenski", "slovenska", "slovensko", "slovenskega",
            "slovenskih", "slovenskim", "slovenskimi",
        ])


class TestAdjPossessiveBratov:
    def test_forms(self):
        # Possessive -ov adjective; -ov- stays in the stem.
        _assert_all("bratov", [
            "bratov", "bratova", "bratovo", "bratovega",
            "bratovih", "bratovim", "bratovimi",
        ])


# ---------------------------------------------------------------------------
# Passthrough + caching
# ---------------------------------------------------------------------------

class TestUnknownLemmaPassthrough:
    def test_unknown_lemma_passthrough(self):
        assert generate_forms("xyzqwerty") == {"xyzqwerty"}


class TestCaching:
    def test_caching_returns_same_object(self):
        _FORMS_CACHE.pop("delo", None)
        first = generate_forms("delo")
        second = generate_forms("delo")
        assert first is second
        assert "delo" in _FORMS_CACHE