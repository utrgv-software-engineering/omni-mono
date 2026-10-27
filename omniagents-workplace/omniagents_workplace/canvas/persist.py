"""Canvases in the workplace's store: written as they change, read at start.

Members, canvases, versions and comments are documents; file contents are
the store's blobs (content-addressed, so a file a publish leaves as it was
costs nothing).
"""

from __future__ import annotations

from typing import Any, Dict

from ..store import Store
from .state import Canvas, CanvasComment, CanvasState, CanvasVersion, Member

__all__ = ["CanvasPersistence"]

_ENTITIES = (
    ("canvas.member", "members", Member, "member"),
    ("canvas.canvas", "canvases", Canvas, "canvas"),
    ("canvas.comment", "comments", CanvasComment, "comment"),
)


class CanvasPersistence:
    def __init__(self, state: CanvasState, store: Store):
        self.state = state
        self.store = store

    def attach(self) -> None:
        self.state.subscribe(self._on_change)

    def save_all(self) -> None:
        state = self.state
        with self.store.transaction():
            for kind in (
                "canvas.member",
                "canvas.canvas",
                "canvas.version",
                "canvas.comment",
            ):
                self.store.delete_kind(kind)
            for content in state.blobs.values():
                self.store.put_blob(content)
            for kind, attr, _, _ in _ENTITIES:
                for key, item in getattr(state, attr).items():
                    self.store.put(kind, key, item)
            for key, version in state.versions.items():
                self.store.put("canvas.version", key, version)

    def load(self) -> None:
        state = self.state
        with state._lock:
            for kind, attr, cls, _ in _ENTITIES:
                setattr(
                    state, attr, {item.id: item for item in self.store.load(kind, cls)}
                )
            state.versions = {
                f"{v.canvas_id}:{v.number}": v
                for v in self.store.load("canvas.version", CanvasVersion)
            }
            shas = {sha for v in state.versions.values() for sha in v.files.values()}
            state.blobs = {sha: self.store.blob(sha) for sha in shas}

    def _on_change(self, kind: str, data: Dict[str, Any]) -> None:
        with self.store.transaction():
            for content in (data.get("blobs") or {}).values():
                self.store.put_blob(content)
            version = data.get("version")
            if version is not None:
                self.store.put(
                    "canvas.version", f"{version.canvas_id}:{version.number}", version
                )
            for store_kind, _, _, key in _ENTITIES:
                item = data.get(key)
                if item is not None:
                    self.store.put(store_kind, item.id, item)
            for store_kind, key in data.get("removed") or []:
                self.store.delete(store_kind, key)
