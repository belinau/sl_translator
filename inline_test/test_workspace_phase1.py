"""Phase 1 workspace tests.

Critical invariants:
- state.current dict reference is stable across set_active (so bind_value
  survives segment switches — this is the persistent-DOM mechanism).
- set_target writes back through to segments[active_index]["target"].
- Subscribers fire on field notify.
- Autosave debounce: rapid mutations -> ONE save call within the window.
- Navigator seg_click routes to state.set_active.
"""
from __future__ import annotations

import asyncio

import pytest

from ui.state import WorkspaceState


def _make_ws(n: int = 3) -> dict:
    return {
        "project_id": "test",
        "filename": "fixture.docx",
        "lang_pair": "en->sl",
        "active_index": 0,
        "segments": [
            {"id": i, "source": f"src-{i}", "target": f"tgt-{i}" if i == 0 else "", "status": "pending"}
            for i in range(n)
        ],
    }


def _noop_save(_payload: dict) -> None:
    return None


def test_state_set_active_mutates_in_place():
    state = WorkspaceState(_make_ws(3), save_callback=_noop_save)
    ref_before = id(state.current)
    state.set_active(1)
    assert id(state.current) == ref_before, "state.current must be the same dict after set_active"
    assert state.current["source"] == "src-1"
    assert state.current["target"] == ""


def test_state_set_active_persists_edits():
    state = WorkspaceState(_make_ws(3), save_callback=_noop_save)
    state.current["target"] = "edited"
    state.set_active(1)
    assert state.segments[0]["target"] == "edited", "target must flush into segments[old] before switching"


def test_state_set_target_writes_through():
    state = WorkspaceState(_make_ws(3), save_callback=_noop_save)
    state.set_target("hello")
    assert state.segments[0]["target"] == "hello"
    assert state.is_dirty is True


def test_subscribe_fires_on_active_change():
    state = WorkspaceState(_make_ws(3), save_callback=_noop_save)
    fired: list[int] = []
    state.subscribe("active_index", lambda: fired.append(state.active_index))
    state.set_active(2)
    assert fired == [2]


def test_subscribe_fires_on_mark_done():
    state = WorkspaceState(_make_ws(3), save_callback=_noop_save)
    fired: list[None] = []
    state.subscribe("segments", lambda: fired.append(None))
    state.mark_done(0)
    assert len(fired) == 1
    assert state.segments[0]["status"] == "done"


def test_progress_property():
    state = WorkspaceState(_make_ws(4), save_callback=_noop_save)
    assert state.progress() == 0.0
    state.mark_done(0)
    state.mark_done(1)
    assert state.progress() == 0.5


@pytest.mark.asyncio
async def test_autosave_debounce_coalesces():
    """Multiple set_target calls in <2s produce ONE save_project call."""
    saved: list[dict] = []

    def _capture(payload: dict) -> None:
        saved.append(payload)

    state = WorkspaceState(_make_ws(3), save_callback=_capture)
    for v in ["a", "ab", "abc", "abcd"]:
        state.set_target(v)
        await asyncio.sleep(0.1)
    # No save yet — within debounce window
    assert saved == []
    # Wait past the 2s debounce
    await asyncio.sleep(2.2)
    assert len(saved) == 1, f"expected exactly one save, got {len(saved)}"
    assert saved[0]["segments"][0]["target"] == "abcd"


def test_set_active_out_of_range_noop():
    state = WorkspaceState(_make_ws(3), save_callback=_noop_save)
    state.set_active(99)
    assert state.active_index == 0
    state.set_active(-1)
    assert state.active_index == 0


def test_set_active_same_index_noop():
    state = WorkspaceState(_make_ws(3), save_callback=_noop_save)
    fired: list[None] = []
    state.subscribe("active_index", lambda: fired.append(None))
    state.set_active(0)
    assert fired == []


def test_segments_stay_pure():
    """No UI-internal fields should leak onto segment dicts via state ops."""
    state = WorkspaceState(_make_ws(3), save_callback=_noop_save)
    state.set_active(1)
    state.set_target("foo")
    state.mark_done(0)
    expected_keys = {"id", "source", "target", "status"}
    for s in state.segments:
        assert set(s.keys()) == expected_keys, f"unexpected keys on segment: {set(s.keys()) - expected_keys}"
