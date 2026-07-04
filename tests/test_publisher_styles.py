# tests/test_publisher_styles.py
#
# Tests for translate_core/publisher_styles.py — user-managed publisher
# typography profiles (load/save/resolve/seed).

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from translate_core import publisher_styles


@pytest.fixture
def styles_file(tmp_path, monkeypatch):
    """Point publisher_styles at a temp file for isolation."""
    tmp = tmp_path / "publisher_styles.json"
    monkeypatch.setattr(publisher_styles, "STYLES_PATH", tmp)
    return tmp


class TestLoadStyles:
    def test_seeds_maska_on_first_run(self, styles_file):
        assert not styles_file.exists()
        styles = publisher_styles.load_styles()
        assert "maska" in styles
        assert styles["maska"]["body_font"] == "Times New Roman"
        assert styles["maska"]["body_size_pt"] == 12
        assert styles_file.exists()  # seeded file written

    def test_load_returns_existing(self, styles_file):
        # First run seeds
        publisher_styles.load_styles()
        # Manually add a second profile
        styles = json.loads(styles_file.read_text(encoding="utf-8"))
        styles["custom"] = {"label": "Custom", "body_font": "Arial", "body_size_pt": 11}
        styles_file.write_text(json.dumps(styles), encoding="utf-8")
        # Reload — both present
        loaded = publisher_styles.load_styles()
        assert "maska" in loaded
        assert "custom" in loaded
        assert loaded["custom"]["body_font"] == "Arial"

    def test_always_ensures_maska_present(self, styles_file):
        # Write a file without maska
        styles_file.write_text(json.dumps({"other": {"label": "Other"}}), encoding="utf-8")
        loaded = publisher_styles.load_styles()
        assert "maska" in loaded  # re-seeded
        assert "other" in loaded   # preserved


class TestSaveStyles:
    def test_save_round_trips(self, styles_file):
        styles = {"test_pub": {"label": "Test", "body_font": "Calibri", "body_size_pt": 10}}
        publisher_styles.save_styles(styles)
        loaded = json.loads(styles_file.read_text(encoding="utf-8"))
        assert loaded == styles

    def test_save_creates_parent_dir(self, tmp_path, monkeypatch):
        nested = tmp_path / "nested" / "publisher_styles.json"
        monkeypatch.setattr(publisher_styles, "STYLES_PATH", nested)
        publisher_styles.save_styles({"maska": {"label": "M"}})
        assert nested.exists()


class TestResolveTypography:
    def test_resolves_known_key(self, styles_file):
        publisher_styles.load_styles()  # seed
        typo = publisher_styles.resolve_typography("maska")
        assert typo["body_font"] == "Times New Roman"
        assert typo["body_size_pt"] == 12

    def test_unknown_key_falls_back_to_default(self, styles_file):
        publisher_styles.load_styles()  # seed
        typo = publisher_styles.resolve_typography("nonexistent")
        # Falls back to Maska (the default)
        assert typo["body_font"] == "Times New Roman"

    def test_none_key_falls_back_to_default(self, styles_file):
        publisher_styles.load_styles()  # seed
        typo = publisher_styles.resolve_typography(None)
        assert typo["body_font"] == "Times New Roman"


class TestGetStyleLabel:
    def test_returns_label_for_existing(self, styles_file):
        publisher_styles.load_styles()
        label = publisher_styles.get_style_label("maska")
        assert "Maska" in label

    def test_returns_key_for_unknown(self, styles_file):
        publisher_styles.load_styles()
        label = publisher_styles.get_style_label("unknown")
        assert label == "unknown"


class TestGetStyleOptions:
    def test_returns_dict_of_slug_to_label(self, styles_file):
        publisher_styles.load_styles()
        opts = publisher_styles.get_style_options()
        assert "maska" in opts
        assert "Maska" in opts["maska"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])