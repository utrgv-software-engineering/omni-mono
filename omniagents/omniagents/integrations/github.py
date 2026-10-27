"""GitHub as an event source: webhooks.

GitHub POSTs each event to the factory's ``/integrations/github/events``
(a repository or organization webhook, or a GitHub App's), signed with the
webhook's secret (``X-Hub-Signature-256`` = ``sha256=`` HMAC-SHA256 of the
body), named by ``X-GitHub-Event`` and identified by ``X-GitHub-Delivery``.
The server must be reachable from GitHub; on a laptop, ``gh webhook
forward`` relays a repository's webhooks to it.

The factory works on GitHub as one account (``account``): its token is the
one its agents' GitHub MCP server acts with. Webhooks become
:class:`IncomingEvent` with the names automations use:

- ``issue_mentioned``, ``pull_request_mentioned``: the account is
  @-mentioned in an issue or pull request, its comments or its reviews.
- ``issue_assigned``, ``pull_request_assigned``: it is assigned.
- ``issue_labeled``, ``pull_request_labeled``: a label is added.
- ``pull_request_opened`` (drafts too), ``pull_request_ready`` (a draft
  marked ready for review), ``pull_request_synchronized`` (a push to its
  branch), ``pull_request_review_requested``, ``pull_request_closed``
  (without merging), ``pull_request_merged``.
- ``push``, ``workflow_run_completed``.

Filters: ``repos``, ``labels`` (the item's labels; the added one for a
``*_labeled`` event), ``mentioned``, ``assignees`` (the one assigned),
``base_branches``, ``branches``, ``conclusions``, ``paths`` (the files a
push changed), ``reviewers`` and ``reviewer_teams`` (who a review was
requested from). An issue or pull request is the thread: events on it
continue the conversation it started. The account mentioning itself is not
an event.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from .events import IncomingEvent

__all__ = [
    "GITHUB_EVENTS_PATH",
    "GitHubWebhookConfig",
    "github_signature",
    "normalize_github_event",
    "verify_github_request",
]

GITHUB_EVENTS_PATH = "/integrations/github/events"


@dataclass(frozen=True)
class GitHubWebhookConfig:
    """``integrations: [{type: github, webhook_secret: ..., account: ...}]``."""

    webhook_secret: str
    account: str = ""


def github_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_github_request(secret: str, headers: Mapping[str, str], body: bytes) -> bool:
    signature = headers.get("x-hub-signature-256") or ""
    return bool(secret) and hmac.compare_digest(
        signature, github_signature(secret, body)
    )


_MENTION = re.compile(r"(?<![\w/`])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))")


def _mentions(text: Optional[str]) -> List[str]:
    without_code = re.sub(r"```.*?```|`[^`]*`", "", text or "", flags=re.S)
    return list(dict.fromkeys(m.group(1) for m in _MENTION.finditer(without_code)))


def _login(user: Any) -> str:
    return str((user or {}).get("login") or "")


def _item(item: Mapping[str, Any], *, pull: bool) -> Dict[str, Any]:
    out = {
        "number": item.get("number"),
        "title": item.get("title"),
        "html_url": item.get("html_url"),
        "state": item.get("state"),
        "user": _login(item.get("user")),
        "labels": [label.get("name") for label in item.get("labels") or []],
        "assignees": [_login(a) for a in item.get("assignees") or []],
        "body": item.get("body") or "",
    }
    if pull:
        out.update(
            head=(item.get("head") or {}).get("ref"),
            base=(item.get("base") or {}).get("ref"),
            draft=item.get("draft", False),
            merged=item.get("merged", False),
        )
    return out


def normalize_github_event(
    event_name: str, delivery_id: str, payload: Mapping[str, Any], account: str = ""
) -> List[IncomingEvent]:
    """A webhook as the :class:`IncomingEvent` automations match (none, one or more)."""
    action = payload.get("action")
    repo = str((payload.get("repository") or {}).get("full_name") or "")
    sender = _login(payload.get("sender"))
    issue = payload.get("issue")
    pr = payload.get("pull_request")
    # An issue comment on a pull request carries the pull request as an issue.
    is_pull = bool(pr) or bool(issue and issue.get("pull_request"))
    item = pr or issue
    kind = "pull_request" if is_pull else "issue"
    summary: Dict[str, Any] = {
        "event": event_name,
        "action": action,
        "repository": repo,
        "sender": sender,
    }
    if item:
        summary[kind] = _item(item, pull=bool(pr))
    texts: List[str] = []
    for key in ("comment", "review"):
        if payload.get(key):
            part = payload[key]
            summary[key] = {
                "body": part.get("body") or "",
                "html_url": part.get("html_url"),
                "user": _login(part.get("user")),
                **({"state": part.get("state")} if key == "review" else {}),
                **(
                    {
                        "path": part.get("path"),
                        "line": part.get("line"),
                        "id": part.get("id"),
                    }
                    if event_name == "pull_request_review_comment"
                    else {"id": part.get("id")}
                ),
            }
            texts.append(part.get("body") or "")
    if payload.get("label"):
        summary["label"] = payload["label"].get("name")
    if payload.get("assignee"):
        summary["assignee"] = _login(payload["assignee"])

    labels = [label.get("name") for label in (item or {}).get("labels") or []]
    base: Dict[str, List[str]] = {"repos": [repo]} if repo else {}
    if labels:
        base["labels"] = labels
    if pr and (pr.get("base") or {}).get("ref"):
        base["base_branches"] = [pr["base"]["ref"]]
    thread = f"{repo}#{item.get('number')}" if item and repo else None

    out: List[IncomingEvent] = []

    def add(
        name: str, extra: Optional[Dict[str, List[str]]] = None, key: str = ""
    ) -> None:
        out.append(
            IncomingEvent(
                provider="github",
                event=name,
                delivery_id=f"{delivery_id}:{name}{key}",
                payload={**summary, "trigger": name},
                thread_key=thread,
                filters={**base, **(extra or {})},
            )
        )

    def mentioned(text_parts: List[str]) -> None:
        logins = list(
            dict.fromkeys(login for t in text_parts for login in _mentions(t))
        )
        if logins and not (account and sender == account and account in logins):
            add(f"{kind}_mentioned", {"mentioned": logins})

    if event_name in ("issues", "pull_request"):
        if action == "opened":
            if event_name == "pull_request":
                add("pull_request_opened")
            mentioned([item.get("body") or ""])
        elif action == "assigned" and payload.get("assignee"):
            add(f"{kind}_assigned", {"assignees": [_login(payload["assignee"])]})
        elif action == "labeled" and payload.get("label"):
            add(f"{kind}_labeled", {"labels": [payload["label"].get("name")]})
        elif action == "closed" and event_name == "pull_request":
            add("pull_request_merged" if pr.get("merged") else "pull_request_closed")
        elif action == "ready_for_review" and event_name == "pull_request":
            add("pull_request_ready")
        elif action == "synchronize" and event_name == "pull_request":
            add("pull_request_synchronized")
        elif action == "review_requested" and event_name == "pull_request":
            reviewer = _login(payload.get("requested_reviewer"))
            team = str((payload.get("requested_team") or {}).get("slug") or "")
            add(
                "pull_request_review_requested",
                {
                    "reviewers": [reviewer] if reviewer else [],
                    "reviewer_teams": [team] if team else [],
                },
                key=f":{reviewer or team}",
            )
    elif event_name in (
        "issue_comment",
        "pull_request_review_comment",
        "pull_request_review",
    ):
        if action in ("created", "submitted"):
            mentioned(texts)
    elif event_name == "push":
        ref = str(payload.get("ref") or "")
        if ref.startswith("refs/heads/") and not payload.get("deleted"):
            commits = payload.get("commits") or []
            paths = list(
                dict.fromkeys(
                    p
                    for c in commits
                    for p in (c.get("added") or [])
                    + (c.get("removed") or [])
                    + (c.get("modified") or [])
                )
            )
            summary.update(
                ref=ref,
                before=payload.get("before"),
                after=payload.get("after"),
                compare=payload.get("compare"),
                commits=[
                    {
                        "id": c.get("id"),
                        "message": c.get("message"),
                        "author": (c.get("author") or {}).get("username")
                        or (c.get("author") or {}).get("name"),
                    }
                    for c in commits
                ],
                paths=paths,
            )
            add("push", {"branches": [ref[len("refs/heads/") :]], "paths": paths})
    elif event_name == "workflow_run" and action == "completed":
        run = payload.get("workflow_run") or {}
        summary["workflow_run"] = {
            k: run.get(k)
            for k in (
                "id",
                "name",
                "run_number",
                "event",
                "status",
                "conclusion",
                "head_branch",
                "head_sha",
                "html_url",
            )
        }
        summary["workflow_run"]["pull_requests"] = [
            p.get("number") for p in run.get("pull_requests") or []
        ]
        add(
            "workflow_run_completed",
            {
                "branches": [str(run.get("head_branch") or "")],
                "conclusions": [str(run.get("conclusion") or "")],
            },
        )
    return out
