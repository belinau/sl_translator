# tests/test_backfill_pdf_manifest.py
#
# Tests for scripts/backfill_pdf_manifest.py — verifies the backfill adds
# manifest keys to an existing project without touching target/status/source/id.

import json

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

fitz = pytest.importorskip("fitz", reason="PyMuPDF required for backfill tests")

from scripts.backfill_pdf_manifest import backfill


@pytest.fixture
def fixture_project(tmp_path):
    """Create a minimal PDF + project JSON with a few translated segments."""
    pdf_path = tmp_path / "test_proj.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Chapter One", fontsize=18, fontname="helv")
    page.insert_text((72, 110), "Body of first paragraph here.", fontsize=10, fontname="helv")
    page.insert_text((72, 130), "Body of second paragraph.", fontsize=10, fontname="helv")
    doc.save(str(pdf_path))
    doc.close()

    segments = [
        {"id": 0, "source": "Chapter One", "target": "Prvo poglavje", "status": "done"},
        {"id": 1, "source": "Body of first paragraph here.", "target": "Telo prvega odstavka.", "status": "done"},
        {"id": 2, "source": "Body of second paragraph.", "target": "Telo drugega odstavka.", "status": "pending"},
    ]
    ws = {
        "id": "test_proj",
        "filename": "test_proj.pdf",
        "lang_pair": "en->sl",
        "pipeline": "academic",
        "project_type": "book_translation",
        "active_index": 0,
        "saved_at": "2025-01-01T00:00:00",
        "total": 3,
        "done": 2,
        "segments": segments,
    }
    json_path = tmp_path / "test_proj.json"
    json_path.write_text(json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path, "test_proj"


class TestBackfill:
    def test_backfill_preserves_target_and_status(self, fixture_project):
        projects_dir, project_id = fixture_project
        backfill(project_id, projects_dir=projects_dir)

        data = json.loads((projects_dir / f"{project_id}.json").read_text(encoding="utf-8"))
        segs = data["segments"]
        # target/status/source/id must be byte-identical
        assert segs[0]["target"] == "Prvo poglavje"
        assert segs[0]["status"] == "done"
        assert segs[0]["source"] == "Chapter One"
        assert segs[0]["id"] == 0
        assert segs[1]["target"] == "Telo prvega odstavka."
        assert segs[1]["status"] == "done"
        assert segs[2]["target"] == "Telo drugega odstavka."
        assert segs[2]["status"] == "pending"

    def test_backfill_adds_manifest_keys(self, fixture_project):
        projects_dir, project_id = fixture_project
        backfill(project_id, projects_dir=projects_dir)

        data = json.loads((projects_dir / f"{project_id}.json").read_text(encoding="utf-8"))
        segs = data["segments"]
        # At least some segments should have pdf_para_idx now
        with_manifest = [s for s in segs if "pdf_para_idx" in s]
        assert len(with_manifest) > 0
        # Heading segment (Chapter One, 18pt) should have heading_level >= 1
        headings = [s for s in segs if s.get("heading_level", 0) >= 1]
        assert len(headings) >= 1

    def test_backfill_creates_backup(self, fixture_project):
        projects_dir, project_id = fixture_project
        backfill(project_id, projects_dir=projects_dir)
        bak = projects_dir / f"{project_id}.json.bak"
        assert bak.exists()

    def test_backfill_is_idempotent(self, fixture_project):
        """Running backfill twice produces the same manifest keys."""
        projects_dir, project_id = fixture_project
        backfill(project_id, projects_dir=projects_dir)
        data1 = json.loads((projects_dir / f"{project_id}.json").read_text(encoding="utf-8"))

        backfill(project_id, projects_dir=projects_dir)
        data2 = json.loads((projects_dir / f"{project_id}.json").read_text(encoding="utf-8"))

        # target/status unchanged across both runs
        for s1, s2 in zip(data1["segments"], data2["segments"]):
            assert s1["target"] == s2["target"]
            assert s1["status"] == s2["status"]
            assert s1.get("pdf_para_idx") == s2.get("pdf_para_idx")

    def test_backfill_adds_segments_meta_if_missing(self, fixture_project):
        projects_dir, project_id = fixture_project
        backfill(project_id, projects_dir=projects_dir)
        data = json.loads((projects_dir / f"{project_id}.json").read_text(encoding="utf-8"))
        assert "segments_meta" in data
        assert len(data["segments_meta"]) == 3

    def test_backfill_adds_house_style_if_missing(self, fixture_project):
        projects_dir, project_id = fixture_project
        backfill(project_id, projects_dir=projects_dir)
        data = json.loads((projects_dir / f"{project_id}.json").read_text(encoding="utf-8"))
        assert "house_style" in data
        assert data["house_style"] == "maska"

    def test_backfill_preserves_existing_house_style(self, fixture_project):
        projects_dir, project_id = fixture_project
        # Set a custom house_style before backfill
        json_path = projects_dir / f"{project_id}.json"
        data = json.loads(json_path.read_text(encoding="utf-8"))
        data["house_style"] = "custom_publisher"
        json_path.write_text(json.dumps(data), encoding="utf-8")
        backfill(project_id, projects_dir=projects_dir)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["house_style"] == "custom_publisher"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])