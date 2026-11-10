import hashlib
import json
import time
from datetime import datetime, timezone

from fastapi import HTTPException

from .client import segment


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def source_key(connection, source):
    return connection["id"] + ":" + connection["scope_epoch"] + ":" + source


def factory_comments(connections, connection, project, repository, pr_id):
    found = []
    for identifier, operation in connections.w.store.docs("ado.operation"):
        resource = operation.get("resource") or {}
        if (
            not identifier.startswith(connection["id"] + ":")
            or operation.get("status") != "succeeded"
        ):
            continue
        if (
            resource.get("project_id") != project
            or resource.get("repository_id") != repository
            or str(resource.get("pull_request_id")) != str(pr_id)
        ):
            continue
        result = operation.get("result") or {}
        if operation.get("tool") == "ado_comment_pull_request":
            found.extend(
                [result.get("id"), comment.get("id")]
                for comment in result.get("comments", [])
                if result.get("id") is not None and comment.get("id") is not None
            )
        elif (
            operation.get("tool") == "ado_reply_pull_request_thread"
            and result.get("id") is not None
        ):
            found.append([resource.get("thread_id"), result["id"]])
    return found


def observe(connections, connection, source, observations, *, cursor=None):
    store = connections.w.store
    key = source_key(connection, source)
    with store.transaction():
        connections.assert_current(connection["id"], connection["revision"])
        state = store.meta("ado.source." + key) or {}
        baseline = bool(state.get("initialized"))
        for resource, event, snapshot, context in observations:
            identifier = key + ":" + resource
            previous = dict(store.docs("ado.observation")).get(identifier)
            serialized = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(serialized.encode()).hexdigest()
            if previous and previous["digest"] == digest:
                continue
            sequence = (previous or {}).get("sequence", 0) + 1
            value = {"digest": digest, "sequence": sequence, "snapshot": snapshot}
            store.put("ado.observation", identifier, value)
            if not baseline:
                continue
            if event == "pull_request_updated":
                old = (previous or {}).get("snapshot") or {}
                status = snapshot.get("status")
                if previous is None:
                    event = {
                        "active": "pull_request_opened",
                        "completed": "pull_request_completed",
                        "abandoned": "pull_request_abandoned",
                    }.get(status, event)
                elif status != old.get("status"):
                    event = {
                        "completed": "pull_request_completed",
                        "abandoned": "pull_request_abandoned",
                        "active": "pull_request_reactivated",
                    }.get(status, event)
            if event == "work_item_updated" and snapshot.get("rev") == 1:
                event = "work_item_created"
            if event == "build_updated" and snapshot.get("status") == "completed":
                event = "build_completed"
            if event not in connection["events"]:
                continue
            delivery = hashlib.sha256(
                (identifier + ":" + str(sequence)).encode()
            ).hexdigest()
            payload = {
                "connection_id": connection["id"],
                "scope_epoch": connection["scope_epoch"],
                "organization": connection["organization"],
                "event": event,
                "resource_id": resource,
                "observed_at": time.time(),
                "previous": (previous or {}).get("snapshot"),
                "resource": snapshot,
                **context,
            }
            connections.w.event_gateway.enqueue(
                "ado:" + connection["id"],
                "azure-devops",
                delivery,
                json.dumps(payload).encode(),
                {},
            )
        state.update(initialized=True, polled_at=time.time())
        if cursor is not None:
            state["cursor"] = cursor
        store.set_meta("ado.source." + key, state)


async def pull_requests(connections, connection, scope, repository):
    client = connections.client(connection)
    project = scope["project"]
    base = f"/{segment(project)}/_apis/git/repositories/{segment(repository)}"
    source = "prs:" + project + ":" + repository
    key = source_key(connection, source)
    state = connections.w.store.meta("ado.source." + key) or {}
    active = await client.all(
        base + "/pullrequests",
        params={"searchCriteria.status": "active", "$top": 100},
        offset=True,
    )
    closed = await client.all(
        base + "/pullrequests",
        params={
            "searchCriteria.status": "all",
            "searchCriteria.queryTimeRangeType": "Closed",
            "searchCriteria.minTime": iso(
                state.get("polled_at", connection["activated_at"]) - 300
            ),
            "$top": 100,
        },
        offset=True,
    )
    ids = {str(value["pullRequestId"]) for value in active + closed}
    prefix = key + ":pr:"
    for identifier, value in connections.w.store.docs("ado.observation"):
        if (
            identifier.startswith(prefix)
            and value["snapshot"].get("status") == "active"
        ):
            ids.add(identifier[len(prefix) :])
    observations = []
    for pr_id in sorted(ids):
        pr_base = base + "/pullrequests/" + segment(pr_id)
        detail = await client.json("GET", pr_base)
        threads = await client.all(pr_base + "/threads")
        statuses = await client.all(
            pr_base + "/statuses", params={"$top": 100}, offset=True
        )
        policies = await client.all(
            f"/{segment(project)}/_apis/policy/evaluations",
            params={
                "artifactId": f"vstfs:///CodeReview/CodeReviewId/{project}/{pr_id}"
            },
            version="7.1-preview.1",
        )
        iterations = await client.all(pr_base + "/iterations")
        context = {
            "project_id": project,
            "repository_id": repository,
            "pull_request_id": pr_id,
            "pull_request": detail,
            "threads": threads,
            "statuses": statuses,
            "policies": policies,
            "iterations": iterations,
            "factory_comment_ids": factory_comments(
                connections, connection, project, repository, pr_id
            ),
        }
        snapshot = {
            key: value
            for key, value in detail.items()
            if key not in ("reviewers", "_links", "url")
        }
        observations.append(("pr:" + pr_id, "pull_request_updated", snapshot, context))
        observations.append(
            (
                "reviewers:" + pr_id,
                "pull_request_review_changed",
                detail.get("reviewers", []),
                context,
            )
        )
        observations.append(
            ("threads:" + pr_id, "pull_request_thread_changed", threads, context)
        )
        observations.append(
            ("statuses:" + pr_id, "pull_request_status_changed", statuses, context)
        )
        observations.append(
            ("policies:" + pr_id, "pull_request_policy_changed", policies, context)
        )
    observe(connections, connection, source, observations)


async def pushes(connections, connection, scope, repository):
    client = connections.client(connection)
    project = scope["project"]
    base = f"/{segment(project)}/_apis/git/repositories/{segment(repository)}"
    source = "pushes:" + project + ":" + repository
    state = (
        connections.w.store.meta("ado.source." + source_key(connection, source)) or {}
    )
    since = state.get("cursor", connection["activated_at"])
    until = time.time()
    values = await client.all(
        base + "/pushes",
        params={
            "searchCriteria.fromDate": iso(since - 300),
            "searchCriteria.toDate": iso(until),
            "searchCriteria.includeRefUpdates": "true",
            "$top": 100,
        },
        offset=True,
    )
    observations = []
    for value in values:
        observations.append(
            (
                str(value["pushId"]),
                "push",
                value,
                {"project_id": project, "repository_id": repository},
            )
        )
    refs = await client.all(base + "/refs", params={"$top": 1000})
    observations.append(
        (
            "refs",
            "refs_changed",
            sorted(refs, key=lambda value: value["name"]),
            {"project_id": project, "repository_id": repository},
        )
    )
    observe(connections, connection, source, observations, cursor=until)


async def work_items(connections, connection, scope):
    client = connections.client(connection)
    project = scope["project"]
    base = f"/{segment(project)}/_apis/wit"
    source = "boards:" + project
    key = source_key(connection, source)
    state = connections.w.store.meta("ado.source." + key) or {}
    query = {
        "includeDeleted": "true",
        "includeIdentityRef": "true",
        "includeLatestOnly": "false",
        "$maxPageSize": 200,
    }
    if state.get("cursor"):
        query["continuationToken"] = state["cursor"]
    else:
        query["startDateTime"] = iso(connection["activated_at"])
    observations = []
    seen = set()
    cursor = state.get("cursor")
    while True:
        result = await client.json(
            "GET", base + "/reporting/workitemrevisions", params=query
        )
        for revision in result.get("values", []):
            item_id = str(revision["id"])
            observations.append(
                (
                    "item:" + item_id,
                    "work_item_updated",
                    revision,
                    {"project_id": project, "work_item_id": item_id},
                )
            )
        next_cursor = result.get("continuationToken")
        if not next_cursor:
            raise HTTPException(
                502, "Azure DevOps work revision response omitted its watermark"
            )
        cursor = next_cursor
        if result.get("isLastBatch"):
            break
        if cursor in seen:
            raise HTTPException(502, "Azure DevOps repeated a work revision cursor")
        seen.add(cursor)
        query.pop("startDateTime", None)
        query["continuationToken"] = cursor
    item_ids = set()
    last_id = 0
    while True:
        result = await client.json(
            "POST",
            base + "/wiql",
            params={"$top": 200},
            json_body={
                "query": f"SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.Id] > {last_id} ORDER BY [System.Id]"
            },
        )
        batch = result.get("workItems", [])
        item_ids.update(str(item["id"]) for item in batch)
        if len(batch) < 200:
            break
        next_id = max(int(item["id"]) for item in batch)
        if next_id <= last_id:
            raise HTTPException(502, "Azure DevOps repeated a work item page")
        last_id = next_id
    for item_id in sorted(item_ids):
        comments = await client.all(
            base + "/workItems/" + segment(item_id) + "/comments",
            params={"includeDeleted": "true", "$top": 200},
            version="7.1-preview.4",
        )
        observations.append(
            (
                "comments:" + item_id,
                "work_item_comment_changed",
                comments,
                {"project_id": project, "work_item_id": item_id},
            )
        )
    observe(connections, connection, source, observations, cursor=cursor)


async def builds(connections, connection, scope):
    if not scope["pipelines"]:
        return
    client = connections.client(connection)
    project = scope["project"]
    source = "builds:" + project
    key = source_key(connection, source)
    state = connections.w.store.meta("ado.source." + key) or {}
    base = f"/{segment(project)}/_apis/build/builds"
    values = await client.all(
        base,
        params={
            "definitions": ",".join(map(str, scope["pipelines"])),
            "minTime": iso(state.get("polled_at", connection["activated_at"]) - 300),
            "queryOrder": "queueTimeAscending",
            "$top": 100,
        },
    )
    ids = {str(value["id"]) for value in values}
    if not state.get("initialized"):
        for status in ("notStarted", "inProgress", "postponed"):
            pending = await client.all(
                base,
                params={
                    "definitions": ",".join(map(str, scope["pipelines"])),
                    "statusFilter": status,
                    "$top": 100,
                },
            )
            ids.update(str(value["id"]) for value in pending)
    prefix = key + ":build:"
    for identifier, previous in connections.w.store.docs("ado.observation"):
        if (
            identifier.startswith(prefix)
            and previous["snapshot"].get("status") != "completed"
        ):
            ids.add(identifier[len(prefix) :])
    observations = []
    for build_id in sorted(ids):
        value = await client.json("GET", base + "/" + segment(build_id))
        if value.get("definition", {}).get("id") not in scope["pipelines"]:
            continue
        try:
            timeline = await client.json(
                "GET", base + "/" + segment(build_id) + "/timeline"
            )
        except HTTPException as exc:
            if exc.status_code != 404 or value.get("status") not in {
                "notStarted",
                "postponed",
            }:
                raise
            timeline = None
        context = {
            "project_id": project,
            "build_id": build_id,
            "pipeline_id": value["definition"]["id"],
            "build": value,
            "timeline": timeline,
        }
        observations.append(("build:" + build_id, "build_updated", value, context))
        if timeline is not None:
            observations.append(
                ("timeline:" + build_id, "build_timeline_changed", timeline, context)
            )
    observe(connections, connection, source, observations)


async def poll_connection(connections, connection):
    for scope in connection["scopes"]:
        for repository in scope["repositories"]:
            await pull_requests(connections, connection, scope, repository)
            await pushes(connections, connection, scope, repository)
        if scope["boards"]:
            await work_items(connections, connection, scope)
        await builds(connections, connection, scope)
