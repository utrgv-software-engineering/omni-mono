"""Linear's state in the workplace's store: written as it changes, read at start."""

from __future__ import annotations

from typing import Any, Dict

from ..store import Store, encode
from .state import (
    Comment,
    Cycle,
    Document,
    Issue,
    Label,
    LinearState,
    LinearWebhook,
    Milestone,
    Notification,
    Project,
    Session,
    StatusUpdate,
    Team,
    User,
)

__all__ = ["LinearPersistence"]

#: What is stored: the store's kind, the state's attribute, its class, and the key an event names it by.
_ENTITIES = (
    ("linear.user", "users", User, "user"),
    ("linear.team", "teams", Team, "team"),
    ("linear.label", "labels", Label, "label"),
    ("linear.issue", "issues", Issue, "issue"),
    ("linear.comment", "comments", Comment, "comment"),
    ("linear.session", "sessions", Session, "session"),
    ("linear.project", "projects", Project, "project"),
    ("linear.milestone", "milestones", Milestone, "milestone"),
    ("linear.cycle", "cycles", Cycle, "cycle"),
    ("linear.document", "documents", Document, "document"),
    ("linear.status_update", "status_updates", StatusUpdate, "status_update"),
    ("linear.notification", "notifications", Notification, "notification"),
)
_KINDS = tuple(kind for kind, *_ in _ENTITIES)


class LinearPersistence:
    def __init__(self, state: LinearState, store: Store):
        self.state = state
        self.store = store

    def attach(self) -> None:
        self.state.subscribe(self._on_change)

    # -- everything at once -------------------------------------------------------

    def save_all(self) -> None:
        state = self.state
        with self.store.transaction():
            for kind in _KINDS:
                self.store.delete_kind(kind)
            for kind, attr, _, _ in _ENTITIES:
                for key, item in getattr(state, attr).items():
                    self.store.put(kind, key, item)
            self._save_meta()

    def load(self) -> None:
        state = self.state
        org = self.store.meta("linear.organization") or {}
        with state._lock:
            state.org_id = org.get("id", state.org_id)
            state.org_name = org.get("name", "")
            state.url_key = org.get("url_key", "")
            state.oauth_client_id = org.get("oauth_client_id", state.oauth_client_id)
            state.hooks = [
                LinearWebhook(**raw) for raw in self.store.meta("linear.hooks", [])
            ]
            for kind, attr, cls, _ in _ENTITIES:
                setattr(
                    state, attr, {item.id: item for item in self.store.load(kind, cls)}
                )

    # -- as it changes ----------------------------------------------------------------

    def _on_change(self, kind: str, data: Dict[str, Any]) -> None:
        if kind == "reset":
            self.save_all()
            return
        state = self.state
        with self.store.transaction():
            issue = data.get("issue")
            comment = data.get("comment")
            session = data.get("session")
            if issue is not None:
                self.store.put("linear.issue", issue.id, issue)
                # A new issue moves its team's numbering on.
                self.store.put("linear.team", issue.team_id, state.teams[issue.team_id])
            if comment is not None:
                self.store.put("linear.comment", comment.id, comment)
                self._put_issue_of(comment.issue_id)
            if session is not None:
                self.store.put("linear.session", session.id, session)
                anchor = state.comments.get(session.comment_id or "")
                if anchor is not None:
                    self.store.put("linear.comment", anchor.id, anchor)
                self._put_issue_of(session.issue_id)
            if kind == "comment_deleted":
                for cid in data.get("deleted") or [data["comment_id"]]:
                    self.store.delete("linear.comment", cid)
            # Everything else an event names is stored as it is now; what it removed goes.
            for store_kind, _, _, key in _ENTITIES:
                if key in ("issue", "comment", "session"):
                    continue
                item = data.get(key)
                if item is not None and hasattr(item, "id"):
                    self.store.put(store_kind, item.id, item)
            for store_kind, key in data.get("removed") or []:
                self.store.delete(store_kind, key)
            self._save_meta()

    def _put_issue_of(self, issue_id: str) -> None:
        # Comments and sessions touch their issue (its updated_at).
        issue = self.state.issues.get(issue_id)
        if issue is not None:
            self.store.put("linear.issue", issue.id, issue)

    def _save_meta(self) -> None:
        state = self.state
        self.store.set_meta(
            "linear.organization",
            {
                "id": state.org_id,
                "name": state.org_name,
                "url_key": state.url_key,
                "oauth_client_id": state.oauth_client_id,
            },
        )
        self.store.set_meta("linear.hooks", [encode(h) for h in state.hooks])
