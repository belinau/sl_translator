"""WorkspaceState — single source of truth for the translation page.

Subscriber pattern: components register callbacks for specific fields; mutations
call notify(field) to fire them. Replaces the ad-hoc `ws` dict + scattered
refresh() calls from the legacy implementation.

state.current is a dict that is created ONCE and never replaced. It is the
binding target for the editor textarea (`bind_value(state.current, "target")`).
When active_index changes, the dict's contents are mutated in place so the
binding stays alive across segment switches — this is the persistent-DOM
mechanism that fixes the freeze.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class WorkspaceState:
    def __init__(self, ws: dict, save_callback: Callable[[dict], None], client: Any = None) -> None:
        self.project_id: str = ws["project_id"]
        self.filename: str = ws["filename"]
        self.lang_pair: str = ws.get("lang_pair", "en->sl")
        self.segments: list[dict] = ws["segments"]
        self.active_index: int = max(0, min(ws.get("active_index", 0), len(self.segments) - 1)) if self.segments else 0

        seg0 = self.segments[self.active_index] if self.segments else {"source": "", "target": ""}
        self.current: dict[str, str] = {"source": seg0["source"], "target": seg0["target"]}

        self.is_dirty: bool = False
        self.is_batch: bool = False

        # NiceGUI Client tied to the current page render. Stored so background
        # tasks can enter the client context (`with state.client:`) before
        # touching UI APIs — without this, the WebSocket may receive payloads
        # for a non-existent slot and drop the connection.
        self.client: Any = client

        self._save_callback = save_callback
        self._subscribers: dict[str, list[Callable[[], None]]] = {}
        self._save_task: asyncio.Task | None = None

    def subscribe(self, field: str, cb: Callable[[], None]) -> None:
        self._subscribers.setdefault(field, []).append(cb)

    def notify(self, field: str) -> None:
        for cb in list(self._subscribers.get(field, [])):
            try:
                cb()
            except Exception as e:
                print(f"[State.notify {field}] {e}")

    def set_active(self, idx: int) -> None:
        if not self.segments:
            return
        if not (0 <= idx < len(self.segments)):
            return
        if idx == self.active_index:
            return
        self.segments[self.active_index]["target"] = self.current["target"]
        self.active_index = idx
        new_seg = self.segments[idx]
        self.current["source"] = new_seg["source"]
        self.current["target"] = new_seg["target"]
        self.notify("active_index")
        self.request_autosave()

    def set_target(self, value: str) -> None:
        if not self.segments:
            return
        if self.segments[self.active_index]["target"] != value:
            self.segments[self.active_index]["target"] = value
            # Keep the bind_value target in sync so the textarea reflects
            # external mutations (intel-panel click-to-insert, AI draft
            # completion, etc.). When the user is typing, bind_value already
            # set state.current["target"] before this method ran, so the
            # write is a harmless no-op.
            if self.current.get("target") != value:
                self.current["target"] = value
            self.is_dirty = True
            self.notify("target")
            self.request_autosave()

    def mark_done(self, idx: int) -> None:
        if not (0 <= idx < len(self.segments)):
            return
        if self.segments[idx]["status"] != "done":
            self.segments[idx]["status"] = "done"
            self.notify("segments")
            self.request_autosave()

    def progress(self) -> float:
        if not self.segments:
            return 0.0
        done = sum(1 for s in self.segments if s["status"] == "done")
        return done / len(self.segments)

    def request_autosave(self) -> None:
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._save_task = loop.create_task(self._save_after_delay())

    async def _save_after_delay(self) -> None:
        try:
            await asyncio.sleep(2.0)
            payload = {
                "project_id": self.project_id,
                "filename": self.filename,
                "lang_pair": self.lang_pair,
                "active_index": self.active_index,
                "segments": [
                    {k: s[k] for k in ("id", "source", "target", "status")}
                    for s in self.segments
                ],
            }
            await asyncio.get_running_loop().run_in_executor(None, self._save_callback, payload)
            self.is_dirty = False
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[State autosave] {e}")


_kg_save_task: asyncio.Task | None = None


def request_kg_save(save_callable: Callable[[], Any], delay: float = 3.0) -> None:
    """Debounced kg.save() — coalesce many promotions into one disk write."""
    global _kg_save_task
    if _kg_save_task and not _kg_save_task.done():
        _kg_save_task.cancel()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _kg_save_task = loop.create_task(_kg_save_loop(save_callable, delay))


async def _kg_save_loop(save_callable: Callable[[], Any], delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        await asyncio.get_running_loop().run_in_executor(None, save_callable)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[KG save] {e}")
