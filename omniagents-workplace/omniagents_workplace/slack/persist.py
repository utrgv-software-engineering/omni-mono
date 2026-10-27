"""Slack's state in the workplace's store: written as it changes, read at start."""

from __future__ import annotations

from typing import Any, Dict

from ..store import Store
from .workspace import (
    App,
    Channel,
    Draft,
    File,
    Message,
    ScheduledMessage,
    SlackWorkspace,
    User,
)

__all__ = ["SlackPersistence"]


class SlackPersistence:
    def __init__(self, ws: SlackWorkspace, store: Store):
        self.ws = ws
        self.store = store

    def attach(self) -> None:
        self.ws.subscribe(self._on_change)

    # -- everything at once -------------------------------------------------------

    def save_all(self) -> None:
        ws = self.ws
        with self.store.transaction():
            for kind in (
                "slack.user",
                "slack.channel",
                "slack.app",
                "slack.message",
                "slack.draft",
                "slack.scheduled",
                "slack.file",
            ):
                self.store.delete_kind(kind)
            for user in ws.users.values():
                self.store.put("slack.user", user.id, user)
            for channel in ws.channels.values():
                self.store.put("slack.channel", channel.id, channel)
            for app in ws.apps.values():
                self.store.put("slack.app", app.id, app)
            for message in ws.all_messages():
                self._put_message(message)
            for draft in ws.drafts.values():
                self.store.put("slack.draft", draft.id, draft)
            for scheduled in ws.scheduled.values():
                self.store.put("slack.scheduled", scheduled.id, scheduled)
            for file in ws.files.values():
                self.store.put("slack.file", file.id, file)
            self._save_meta()

    def load(self) -> None:
        ws = self.ws
        team = self.store.meta("slack.team") or {}
        with ws._lock:
            ws.team_id, ws.name, ws.domain = (
                team.get("id", ""),
                team.get("name", ""),
                team.get("domain", ""),
            )
            ws.tz = team.get("tz", "")
            ws.next_id = int(self.store.meta("slack.next_id", 1))
            ws._last_ts = float(self.store.meta("slack.last_ts", 0.0))
            ws.users = {u.id: u for u in self.store.load("slack.user", User)}
            ws.channels = {c.id: c for c in self.store.load("slack.channel", Channel)}
            ws.apps = {a.id: a for a in self.store.load("slack.app", App)}
            ws.drafts = {d.id: d for d in self.store.load("slack.draft", Draft)}
            ws.scheduled = {
                s.id: s for s in self.store.load("slack.scheduled", ScheduledMessage)
            }
            ws.files = {f.id: f for f in self.store.load("slack.file", File)}
            ws._messages = {c: [] for c in ws.channels}
            for message in sorted(
                self.store.load("slack.message", Message), key=lambda m: float(m.ts)
            ):
                ws._messages.setdefault(message.channel, []).append(message)

    # -- as it changes ----------------------------------------------------------------

    def _on_change(self, kind: str, data: Dict[str, Any]) -> None:
        if kind == "reset":
            self.save_all()
            return
        with self.store.transaction():
            if "channel" in data:
                self.store.put("slack.channel", data["channel"].id, data["channel"])
            if kind == "message_deleted" and not data["tombstone"]:
                self.store.delete(
                    "slack.message", f"{data['message'].channel}:{data['message'].ts}"
                )
            elif "message" in data:
                self._put_message(data["message"])
            if kind == "draft_removed":
                self.store.delete("slack.draft", data["draft"].id)
            if kind == "draft":
                self.store.put("slack.draft", data["draft"].id, data["draft"])
            if kind == "file":
                self.store.put("slack.file", data["file"].id, data["file"])
            if kind == "scheduled":
                self.store.put(
                    "slack.scheduled", data["scheduled"].id, data["scheduled"]
                )
            if kind == "scheduled_removed":
                self.store.delete("slack.scheduled", data["scheduled"].id)
            if kind in ("user", "read"):
                self.store.put("slack.user", data["user"].id, data["user"])
            if kind == "app":
                self.store.put("slack.app", data["app"].id, data["app"])
            if kind == "app_removed":
                self.store.delete("slack.app", data["app"].id)
            self._save_meta()

    def _put_message(self, message: Message) -> None:
        self.store.put("slack.message", f"{message.channel}:{message.ts}", message)

    def _save_meta(self) -> None:
        ws = self.ws
        self.store.set_meta(
            "slack.team",
            {"id": ws.team_id, "name": ws.name, "domain": ws.domain, "tz": ws.tz},
        )
        self.store.set_meta("slack.next_id", ws.next_id)
        self.store.set_meta("slack.last_ts", ws._last_ts)
