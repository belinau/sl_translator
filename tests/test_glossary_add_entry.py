"""Tests for Glossary.add_entry public API (Step 7)."""
import tempfile
import pathlib


def _fresh_glossary():
    """Return a Glossary backed by a temp dir (no real files)."""
    from translate_core.glossary import Glossary
    tmp = tempfile.mkdtemp()
    return Glossary(glossary_dir=pathlib.Path(tmp))


def test_add_entry_creates_bidirectional_pair():
    g = _fresh_glossary()
    g.add_entry("hello", "zdravo", "en", "sl")
    # Forward lookup
    results = g.lookup_terms("hello", "en", "sl")
    assert len(results) == 1
    assert results[0]["target_term"] == "zdravo"
    # Reverse lookup
    results = g.lookup_terms("zdravo", "sl", "en")
    assert len(results) == 1
    assert results[0]["target_term"] == "hello"


def test_add_entry_preserves_note_and_origin():
    g = _fresh_glossary()
    g.add_entry("cat", "mačka", "en", "sl", note="feline", origin="my.tsv")
    entries = [e for e in g.entries if e["source_term"] == "cat"]
    assert len(entries) == 1
    assert entries[0]["note"] == "feline"
    assert entries[0]["origin"] == "my.tsv"
    # Reverse entry also carries note and origin
    rev = [e for e in g.entries if e["source_term"] == "mačka"]
    assert len(rev) == 1
    assert rev[0]["note"] == "feline"
    assert rev[0]["origin"] == "my.tsv"


def test_add_entry_defaults():
    g = _fresh_glossary()
    g.add_entry("dog", "pes", "en", "sl")
    entries = [e for e in g.entries if e["source_term"] == "dog"]
    assert entries[0]["note"] == ""
    assert entries[0]["origin"] == "custom.tsv"


def test_add_entry_multiple_terms_indexed():
    g = _fresh_glossary()
    g.add_entry("hello", "zdravo", "en", "sl")
    g.add_entry("world", "svet", "en", "sl")
    assert len(g.lookup_terms("hello", "en", "sl")) == 1
    assert len(g.lookup_terms("world", "en", "sl")) == 1
    # Both are in the index
    assert len(g.lookup_terms("zdravo", "sl", "en")) == 1
    assert len(g.lookup_terms("svet", "sl", "en")) == 1