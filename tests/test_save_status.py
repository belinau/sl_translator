"""Tests for WorkspaceState autosave status feedback."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from ui.state import WorkspaceState


def _make_state(save_callback=None, segments=None):
    ws = {
        "project_id": "test-proj",
        "filename": "test.txt",
        "lang_pair": "en->sl",
        "active_index": 0,
        "segments": segments or [{"source": "hello", "target": "", "status": "pending"}],
    }
    return WorkspaceState(ws, save_callback=save_callback or MagicMock())


def test_initial_save_status_is_saved():
    state = _make_state()
    assert state.save_status == "saved"


@pytest.mark.anyio
async def test_set_target_marks_unsaved_and_notifies():
    state = _make_state()
    notified = []
    state.subscribe("save_status", lambda: notified.append(state.save_status))

    state.set_target("zdravo")

    assert state.save_status == "unsaved"
    assert "unsaved" in notified


@pytest.mark.anyio
async def test_mark_done_marks_unsaved():
    state = _make_state()
    state.mark_done(0)
    assert state.save_status == "unsaved"


@pytest.mark.anyio
async def test_save_after_delay_transitions(monkeypatch):
    saved_payloads = []

    def save_callback(payload):
        saved_payloads.append(payload)

    state = _make_state(save_callback=save_callback)

    async def _instant_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    monkeypatch.setattr(
        asyncio.AbstractEventLoop,
        "run_in_executor",
        lambda _self, _executor, fn, *args: fn(*args),
    )

    notified = []
    state.subscribe("save_status", lambda: notified.append(state.save_status))

    await state._save_after_delay()

    assert state.save_status == "saved"
    assert notified == ["saving", "saved"]
    assert len(saved_payloads) == 1
    assert saved_payloads[0]["project_id"] == "test-proj"


@pytest.mark.anyio
async def test_save_error_reverts_to_unsaved(monkeypatch):
    def bad_callback(_payload):
        raise RuntimeError("disk full")

    state = _make_state(save_callback=bad_callback)

    async def _instant_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)
    monkeypatch.setattr(
        asyncio.AbstractEventLoop,
        "run_in_executor",
        lambda _self, _executor, fn, *args: fn(*args),
    )

    notified = []
    state.subscribe("save_status", lambda: notified.append(state.save_status))

    await state._save_after_delay()

    assert state.save_status == "unsaved"
    assert "unsaved" in notified


@pytest.mark.anyio
async def test_cancellation_does_not_leave_invalid_state():
    state = _make_state()
    state.set_target("a")
    assert state.save_status == "unsaved"

    old_task = state._save_task
    assert old_task is not None
    state.set_target("ab")
    assert state.save_status == "unsaved"
    assert old_task.cancelled() or not old_task.done()
