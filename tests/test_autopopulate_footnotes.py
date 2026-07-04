# tests/test_autopopulate_footnotes.py
#
# Tests for scripts/autopopulate_footnotes.py — mechanical Maska conversion
# and target autopopulation.

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import autopopulate_footnotes as ap


def _make_project(tmp_path, segments):
    """Write a minimal project JSON with the given segments."""
    data = {
        "id": "test", "filename": "test.pdf", "lang_pair": "en-sl",
        "segments": segments,
    }
    p = tmp_path / "test.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


class TestAutopopulate:
    """autopopulate converts footnote sources and writes to targets."""

    def test_dry_run_no_changes(self, tmp_path, monkeypatch):
        """Dry run produces report but doesn't modify the file."""
        segs = [
            {"id": 0, "source": "Body text [^1].", "target": "", "status": "pending"},
            {"id": 1, "source": '[^1]: Author, "Title," *Journal,* October 24, 2007, 5.',
             "target": "", "status": "pending"},
        ]
        p = _make_project(tmp_path, segs)
        monkeypatch.setattr(ap, "PROJECTS_DIR", tmp_path)
        ap.autopopulate("test", apply=False)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["segments"][1]["target"] == ""

    def test_apply_populates_target(self, tmp_path, monkeypatch):
        """Apply writes converted text into target field."""
        segs = [
            {"id": 0, "source": "Body text [^1].", "target": "", "status": "pending"},
            {"id": 1, "source": '[^1]: Author, "Title," *Journal,* October 24, 2007, 5.',
             "target": "", "status": "pending"},
        ]
        p = _make_project(tmp_path, segs)
        monkeypatch.setattr(ap, "PROJECTS_DIR", tmp_path)
        ap.autopopulate("test", apply=True)
        data = json.loads(p.read_text(encoding="utf-8"))
        tgt = data["segments"][1]["target"]
        assert "»Title«" in tgt
        assert "*Journal,*" in tgt
        assert "24. 10. 2007" in tgt

    def test_preserves_existing_target(self, tmp_path, monkeypatch):
        """Footnotes with existing non-empty targets are skipped."""
        existing = "[^1]: Avtor, »Naslov«, 5."
        segs = [
            {"id": 0, "source": "Body text [^1].", "target": "Telo [^1].", "status": "done"},
            {"id": 1, "source": '[^1]: Author, "Title," Journal, 5.',
             "target": existing, "status": "done"},
        ]
        p = _make_project(tmp_path, segs)
        monkeypatch.setattr(ap, "PROJECTS_DIR", tmp_path)
        ap.autopopulate("test", apply=True)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["segments"][1]["target"] == existing

    def test_status_set_to_pending(self, tmp_path, monkeypatch):
        """Populated footnotes get status=pending for review."""
        segs = [
            {"id": 0, "source": '[^1]: Author, "Title," 5.', "target": "", "status": "pending"},
        ]
        p = _make_project(tmp_path, segs)
        monkeypatch.setattr(ap, "PROJECTS_DIR", tmp_path)
        ap.autopopulate("test", apply=True)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["segments"][0]["status"] == "pending"
        assert data["segments"][0]["target"] != ""

    def test_multi_segment_footnote(self, tmp_path, monkeypatch):
        """Footnote spanning multiple segments: each segment's target is
        converted from its own source, preserving 1:1 source/target alignment."""
        segs = [
            {"id": 0, "source": '[^1]: Author, "Title," in *Book*,', "target": "", "status": "pending"},
            {"id": 1, "source": "ed. Editor (City: Publisher, 2009), 5.", "target": "", "status": "pending"},
        ]
        p = _make_project(tmp_path, segs)
        monkeypatch.setattr(ap, "PROJECTS_DIR", tmp_path)
        ap.autopopulate("test", apply=True)
        data = json.loads(p.read_text(encoding="utf-8"))
        # Seg 0: quote swap + v: conversion
        assert "»Title«" in data["segments"][0]["target"]
        assert "v: *Book*" in data["segments"][0]["target"]
        # Seg 1: ed. → ur. conversion
        assert "ur. Editor" in data["segments"][1]["target"]
        # Source and target lengths should be close (no full-joined dump)
        assert len(data["segments"][0]["target"]) < len(data["segments"][1]["source"]) * 5

    def test_backup_created(self, tmp_path, monkeypatch):
        """Apply creates a .bak backup."""
        segs = [{"id": 0, "source": '[^1]: Author, 5.', "target": "", "status": "pending"}]
        _make_project(tmp_path, segs)
        monkeypatch.setattr(ap, "PROJECTS_DIR", tmp_path)
        ap.autopopulate("test", apply=True)
        bak = tmp_path / "test.json.bak"
        assert bak.exists()

    def test_idempotent(self, tmp_path, monkeypatch):
        """Re-running apply doesn't change already-populated targets."""
        segs = [{"id": 0, "source": '[^1]: Author, "Title," 5.', "target": "", "status": "pending"}]
        p = _make_project(tmp_path, segs)
        monkeypatch.setattr(ap, "PROJECTS_DIR", tmp_path)
        ap.autopopulate("test", apply=True)
        first = json.loads(p.read_text(encoding="utf-8"))["segments"][0]["target"]
        ap.autopopulate("test", apply=True)
        second = json.loads(p.read_text(encoding="utf-8"))["segments"][0]["target"]
        assert first == second


if __name__ == "__main__":
    pytest.main([__file__, "-v"])