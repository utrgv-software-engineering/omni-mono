import hashlib
import json
from contextlib import nullcontext
from dataclasses import dataclass

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from .events import IncomingEvent

EVENTS = {
    "pull_request_opened",
    "pull_request_updated",
    "pull_request_completed",
    "pull_request_abandoned",
    "pull_request_reactivated",
    "pull_request_thread_changed",
    "pull_request_review_changed",
    "pull_request_status_changed",
    "pull_request_policy_changed",
    "push",
    "refs_changed",
    "work_item_created",
    "work_item_updated",
    "work_item_comment_changed",
    "build_updated",
    "build_completed",
    "build_timeline_changed",
}


@dataclass(frozen=True)
class AzureDevOpsConfig:
    markers: tuple[str, ...] = ()
    identity_ids: tuple[str, ...] = ()


class Delivery(BaseModel):
    connection_id: str = Field(min_length=1)
    scope_epoch: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    event: str


def changed_comments(payload, config):
    def comments(threads):
        return {
            (thread.get("id"), comment.get("id")): comment
            for thread in threads
            if isinstance(thread, dict)
            for comment in thread.get("comments", [])
            if thread.get("id") is not None and comment.get("id") is not None
        }

    old = comments(payload.get("previous") or [])
    current = comments(payload.get("resource") or [])
    own = {tuple(value) for value in payload.get("factory_comment_ids", [])}
    found = []
    for key, comment in current.items():
        prior = old.get(key)
        if prior and prior.get("content") == comment.get("content"):
            continue
        if (
            comment.get("isDeleted")
            or comment.get("commentType") not in (1, "text")
            or not comment.get("content")
            or not (comment.get("author") or {}).get("id")
            or (comment.get("author") or {}).get("id") in config.identity_ids
            or key in own
        ):
            continue
        found.append({"thread_id": key[0], **comment})
    return found


def normalize(payload, delivery_id, *, config=None):
    config = config or AzureDevOpsConfig()
    parsed = Delivery.model_validate(payload)
    if parsed.event not in EVENTS or not delivery_id:
        raise ValueError("Unknown event or missing delivery ID")
    repository = payload.get("repository_id", "")
    if parsed.event.startswith("pull_request_"):
        subject = ["pr", payload["pull_request_id"]]
    elif parsed.event.startswith("work_item_"):
        subject = ["work_item", payload["work_item_id"]]
    elif parsed.event.startswith("build_"):
        subject = ["build", payload["build_id"]]
    else:
        subject = ["repository", repository]
    identity = [
        parsed.connection_id,
        parsed.scope_epoch,
        parsed.project_id,
        repository,
        *subject,
    ]
    resource = payload.get("resource") or {}
    detail = (
        payload.get("pull_request")
        or payload.get("build")
        or (resource if isinstance(resource, dict) else {})
    )
    branches = [
        value
        for value in [
            detail.get("sourceRefName"),
            detail.get("targetRefName"),
            detail.get("sourceBranch"),
        ]
        if value
    ]
    if parsed.event == "push":
        branches.extend(
            value["name"] for value in detail.get("refUpdates", []) if value.get("name")
        )
    elif parsed.event == "refs_changed" and isinstance(resource, list):
        branches.extend(value["name"] for value in resource if value.get("name"))
    filters = {
        "connections": [parsed.connection_id],
        "projects": [parsed.project_id],
        "repositories": [repository] if repository else [],
        "pipelines": (
            [str(payload["pipeline_id"])] if payload.get("pipeline_id") else []
        ),
        "branches": sorted(set(branches)),
        "base_branches": (
            [detail["targetRefName"]] if detail.get("targetRefName") else []
        ),
        "source_branches": (
            [detail["sourceRefName"]] if detail.get("sourceRefName") else []
        ),
        "review_eligible": (
            ["true"]
            if detail.get("status") == "active"
            and not detail.get("isDraft")
            and (detail.get("lastMergeSourceCommit") or {}).get("commitId")
            else []
        ),
        "statuses": [detail["status"]] if detail.get("status") else [],
        "results": [detail["result"]] if detail.get("result") else [],
    }
    payload = dict(payload)
    if parsed.event == "pull_request_thread_changed":
        delta = changed_comments(payload, config)
        payload["changed_human_comments"] = delta
        filters["addressed"] = (
            ["true"]
            if any(
                marker and marker in comment["content"]
                for marker in config.markers
                for comment in delta
            )
            else []
        )
    return IncomingEvent(
        provider="azure-devops",
        event=parsed.event,
        delivery_id=delivery_id,
        payload=payload,
        filters=filters,
        thread_key="ado:" + hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
    )


def admit(event, automation, store, *, transaction=True):
    if event.provider != "azure-devops":
        return True
    repository = "azure-devops:" + ":".join(
        str(event.payload.get(key) or "")
        for key in ("connection_id", "organization", "project_id", "repository_id")
    )
    with store._lock if transaction else nullcontext(), (
        store._db if transaction else nullcontext()
    ):
        store._db.execute(
            "CREATE TABLE IF NOT EXISTS azure_admissions (thread_key TEXT NOT NULL, purpose TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(thread_key, purpose))"
        )
        if event.event == "pull_request_thread_changed":
            if not event.payload.get("changed_human_comments"):
                return False
            active = store._db.execute(
                "SELECT 1 FROM azure_admissions WHERE thread_key=? AND purpose='addressed'",
                (event.thread_key,),
            ).fetchone()
            owned = store._db.execute(
                "SELECT 1 FROM run_pull_requests WHERE repository=? AND number=? AND relation='opened'",
                (repository, int(event.payload["pull_request_id"])),
            ).fetchone()
            if not active and not owned and not event.filters.get("addressed"):
                return False
            store._db.execute(
                "INSERT OR IGNORE INTO azure_admissions VALUES (?, 'addressed', 'true')",
                (event.thread_key,),
            )
        review = any(
            "review_eligible" in (trigger.get("filter") or {})
            for trigger in automation.triggers
        )
        if review:
            if not event.filters.get("review_eligible"):
                return False
            detail = event.payload.get("pull_request") or {}
            head = json.dumps(
                [
                    detail.get("sourceRefName"),
                    detail.get("targetRefName"),
                    (detail.get("lastMergeSourceCommit") or {}).get("commitId"),
                    (detail.get("lastMergeTargetCommit") or {}).get("commitId"),
                ]
            )
            purpose = "review:" + automation.name
            previous = store._db.execute(
                "SELECT value FROM azure_admissions WHERE thread_key=? AND purpose=?",
                (event.thread_key, purpose),
            ).fetchone()
            if previous and previous[0] == head:
                return False
            store._db.execute(
                "INSERT OR REPLACE INTO azure_admissions VALUES (?, ?, ?)",
                (event.thread_key, purpose, head),
            )
    return True


def mount_azure_devops_route(app, intake, authorized, config=None):
    @app.post("/integrations/azure-devops/events")
    async def accept(request: Request):
        if not await authorized(request):
            raise HTTPException(401, "Unauthorized")
        try:
            event = normalize(
                await request.json(),
                request.headers.get("x-ado-delivery", ""),
                config=config,
            )
        except (ValueError, ValidationError, KeyError, TypeError):
            raise HTTPException(422, "Invalid Azure DevOps delivery") from None
        return {"accepted": await intake.accept(event)}
