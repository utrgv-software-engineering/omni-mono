"""GitHub's webhooks: every change, delivered to the factory as GitHub delivers it.

A webhook (``github.webhooks`` in the seed, or set from the UI) receives a
POST per event with GitHub's headers (``X-GitHub-Event``,
``X-GitHub-Delivery``, ``X-Hub-Signature-256``) and payloads: ``issues``,
``issue_comment``, ``pull_request``, ``pull_request_review``,
``pull_request_review_comment``, ``push``, ``create`` and ``workflow_run``.
As on GitHub, a failed delivery is not retried; the wire shows it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Dict, Optional, Set, Tuple

from ..wire import Wire
from .repos import NULL_SHA
from .serialize import Serializer
from .state import GitHubState, Webhook

__all__ = ["Webhook", "WebhookDelivery", "sign"]


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookDelivery:
    def __init__(self, state: GitHubState, serializer: Serializer, wire: Wire):
        self.state = state
        self.s = serializer
        self.wire = wire
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._tasks: Set[asyncio.Task] = set()
        state.subscribe(self._on_change)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- events -> payloads ---------------------------------------------------

    def _on_change(self, kind: str, data: Dict[str, Any]) -> None:
        if not self.state.hooks or self._loop is None:
            return
        try:
            built = self.payload(kind, data)
        except Exception:
            return
        if built is None:
            return
        event, payload = built
        for hook in self.state.hooks:
            if hook.active and ("*" in hook.events or event in hook.events):
                self._loop.call_soon_threadsafe(self._start, hook, event, payload)

    def payload(
        self, kind: str, data: Dict[str, Any]
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        repo = data.get("repo")
        actor = data.get("actor")
        base = (
            {"repository": self.s.repo(repo), "sender": self.s.user(actor)}
            if repo
            else {}
        )
        if (
            repo
            and self.state.users.get(repo.owner)
            and self.state.users[repo.owner].type == "Organization"
        ):
            owner = self.s.user(repo.owner)
            base["organization"] = {
                "login": owner["login"],
                "id": owner["id"],
                "node_id": owner["node_id"],
                "url": f"{self.s.api}/orgs/{repo.owner}",
            }
        issue = data.get("issue")
        if kind.startswith("issue_") and kind != "issue_comment":
            action = kind[len("issue_") :]
            payload = {"action": action, "issue": self.s.issue(repo, issue), **base}
            if action in ("labeled", "unlabeled"):
                payload["label"] = self.s.label(repo, data["label"])
            if action in ("assigned", "unassigned"):
                payload["assignee"] = self.s.user(data["assignee"])
            if action == "edited":
                payload["changes"] = data.get("changes") or {}
            return "issues", payload
        if kind.startswith("pull_request_") and kind not in (
            "pull_request_review_submitted",
        ):
            action = kind[len("pull_request_") :]
            payload = {
                "action": action,
                "number": issue.number,
                "pull_request": self.s.pull(repo, issue),
                **base,
            }
            if action in ("labeled", "unlabeled"):
                payload["label"] = self.s.label(repo, data["label"])
            if action in ("assigned", "unassigned"):
                payload["assignee"] = self.s.user(data["assignee"])
            if action in ("review_requested", "review_request_removed"):
                payload["requested_reviewer"] = self.s.user(data["reviewer"])
            if action == "synchronize":
                payload["before"], payload["after"] = data["before"], data["after"]
            if action == "edited":
                payload["changes"] = data.get("changes") or {}
            return "pull_request", payload
        if kind in ("comment_created", "comment_edited", "comment_deleted"):
            payload = {
                "action": kind.split("_", 1)[1],
                "issue": self.s.issue(repo, issue),
                "comment": self.s.comment(repo, issue, data["comment"]),
                **base,
            }
            if kind == "comment_edited":
                payload["changes"] = {"body": {"from": data.get("previous", "")}}
            return "issue_comment", payload
        if kind == "pull_request_review_submitted":
            return "pull_request_review", {
                "action": "submitted",
                "review": self.s.review(repo, issue, data["review"]),
                "pull_request": self.s.pull(repo, issue),
                **base,
            }
        if kind == "review_comment_created":
            return "pull_request_review_comment", {
                "action": "created",
                "comment": self.s.review_comment(repo, issue, data["comment"]),
                "pull_request": self.s.pull(repo, issue),
                **base,
            }
        if kind == "push":
            return "push", self._push(repo, data, base)
        if kind == "branch_created":
            return "create", {
                "ref": data["branch"],
                "ref_type": "branch",
                "master_branch": repo.default_branch,
                "description": repo.description or None,
                "pusher_type": "user",
                **base,
            }
        if kind == "branch_deleted":
            return "delete", {
                "ref": data["branch"],
                "ref_type": "branch",
                "pusher_type": "user",
                **base,
            }
        if kind == "workflow_run":
            run = data["run"]
            workflow = {
                "id": run.workflow_id,
                "name": run.name,
                "path": run.path,
                "state": "active",
            }
            return "workflow_run", {
                "action": data["action"],
                "workflow_run": self.s.workflow_run(repo, run),
                "workflow": workflow,
                "repository": self.s.repo(repo),
                "sender": self.s.user(run.actor),
            }
        return None

    def _push(self, repo, data: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
        before, after = data["before"], data["after"]
        commits = []
        for commit in reversed(data.get("commits") or []):
            added, removed, modified = repo.bare.commit_paths(commit.sha)
            commits.append(
                {
                    "id": commit.sha,
                    "tree_id": "",
                    "distinct": True,
                    "message": commit.message,
                    "timestamp": commit.author_date,
                    "url": self.s.html(repo, "commit", commit.sha),
                    "author": {
                        "name": commit.author_name,
                        "email": commit.author_email,
                        "username": self._login_for(commit.author_email),
                    },
                    "committer": {
                        "name": commit.committer_name,
                        "email": commit.committer_email,
                        "username": self._login_for(commit.committer_email),
                    },
                    "added": added,
                    "removed": removed,
                    "modified": modified,
                }
            )
        actor = self.state.user(data["actor"]) if data.get("actor") else None
        return {
            "ref": data["ref"],
            "before": before,
            "after": after,
            "created": before == NULL_SHA,
            "deleted": after == NULL_SHA,
            "forced": False,
            "base_ref": None,
            "compare": self.s.html(repo, "compare", f"{before[:12]}...{after[:12]}"),
            "commits": commits,
            "head_commit": commits[-1] if commits else None,
            "pusher": {
                "name": actor.login if actor else "",
                "email": actor.email if actor else "",
            },
            **base,
        }

    def _login_for(self, email: str) -> Optional[str]:
        user = next((u for u in self.state.users.values() if u.email == email), None)
        return user.login if user else None

    # -- delivery ---------------------------------------------------------------

    def _start(self, hook: Webhook, event: str, payload: Dict[str, Any]) -> None:
        task = asyncio.ensure_future(self.deliver(hook, event, payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def deliver(self, hook: Webhook, event: str, payload: Dict[str, Any]) -> bool:
        import httpx

        body = json.dumps(payload).encode()
        delivery = str(uuid.uuid4())
        action = payload.get("action")
        title = f"{event}.{action}" if action else event
        target = _target(payload)
        entry = self.wire.record(
            service="github",
            direction="out",
            kind="event",
            title=title,
            actor=target,
            status="pending",
            request=payload,
            detail={"url": hook.url, "delivery": delivery},
        )
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "GitHub-Hookshot/workplace",
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-GitHub-Hook-ID": str(hook.id),
            "X-GitHub-Hook-Installation-Target-Type": "repository",
        }
        if hook.secret:
            headers["X-Hub-Signature-256"] = sign(hook.secret, body)
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(hook.url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            self.wire.update(
                entry,
                status="error",
                response=f"{type(exc).__name__}: could not reach {hook.url}",
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return False
        ok = 200 <= response.status_code < 300
        self.wire.update(
            entry,
            status="ok" if ok else "error",
            response={"status": response.status_code, "body": response.text[:500]},
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return ok

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _target(payload: Dict[str, Any]) -> str:
    repo = (payload.get("repository") or {}).get("full_name", "")
    item = payload.get("issue") or payload.get("pull_request")
    if item:
        return f"{repo}#{item.get('number')}"
    if payload.get("ref"):
        return f"{repo} {payload['ref']}"
    run = payload.get("workflow_run")
    if run:
        return f"{repo} {run.get('name')} #{run.get('run_number')}"
    return repo
