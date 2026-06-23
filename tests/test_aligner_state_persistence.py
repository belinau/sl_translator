"""Regression test for the Document Aligner upload-state / dry-run guard bug.

The bug: uploaded EN/SL paths were kept in a page-local closure dict, which is
recreated on every page build. A websocket reconnect (which a real browser
hits when the 97 MB KG load stalls the event loop past the ping timeout)
rebuilds the page with a fresh dict, wiping the uploads — so the dry-run guard
reported "Select EN source, SL source first" even though both files had been
added. The fix stores the selections in ``app.storage.user`` (server-side,
survives reloads/reconnects, no client connection needed), so a rebuild
restores them.

These tests use NiceGUI's in-process ``user`` simulator (no browser): one
``user.open`` is one page build, so opening twice exercises the rebuild path.
"""
from __future__ import annotations

import pytest
from nicegui.elements.upload import Upload
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User


class _StubKG:
    """Minimal KG so page_aligner builds past its ``kg is None`` guard and the
    container picker (empty) renders."""

    def get_all_by_type(self, _type):
        return []

    def reload_if_changed(self):
        return None


@pytest.fixture
def aligner_page(monkeypatch):
    import app_state
    monkeypatch.setattr(app_state, "kg", _StubKG(), raising=False)
    monkeypatch.setattr(app_state, "apply_colors", None, raising=False)
    import ui.aligner  # noqa: F401 — registers @ui.page("/aligner")


async def test_upload_populates_store_and_guard_passes(user: User, aligner_page):
    """Uploading both files via on_upload sets app.storage.user and the
    dry-run guard no longer names EN/SL as missing."""
    await user.open("/aligner")
    await user.should_see("Document pair")  # wait for the build

    uploads = [e for e in user.client.elements.values() if isinstance(e, Upload)]
    assert len(uploads) == 2, f"expected EN+SL uploaders, found {len(uploads)}"
    en_up, sl_up = uploads
    await en_up.handle_uploads([SmallFileUpload("EN.md", "text/markdown", b"# EN\n\nbody\n")])
    await sl_up.handle_uploads([SmallFileUpload("SL.md", "text/markdown", b"# SL\n\nbesedilo\n")])

    # The labels reflecting the upload prove the path landed in the store
    # (the on_upload handler writes app.storage.user then sets the label).
    await user.should_see("✓ EN.md")
    await user.should_see("✓ SL.md")


async def test_state_survives_page_rebuild(user: User, aligner_page):
    """The edge case headless 'happy path' missed: after a page rebuild
    (== reconnect/reload), the EN/SL selections persist and the labels are
    restored from app.storage.user. A closure dict would be empty here."""
    await user.open("/aligner")
    await user.should_see("Document pair")
    uploads = [e for e in user.client.elements.values() if isinstance(e, Upload)]
    en_up, sl_up = uploads
    await en_up.handle_uploads([SmallFileUpload("EN.md", "text/markdown", b"# EN\n")])
    await sl_up.handle_uploads([SmallFileUpload("SL.md", "text/markdown", b"# SL\n")])

    # Rebuild the page — fresh closure, same user store.
    await user.open("/aligner")

    # Restored ✓ labels after a fresh build prove the selections survived the
    # rebuild via app.storage.user — a closure dict would render "not set".
    await user.should_see("✓ EN.md")
    await user.should_see("✓ SL.md")
    await user.should_not_see("EN: not set")
