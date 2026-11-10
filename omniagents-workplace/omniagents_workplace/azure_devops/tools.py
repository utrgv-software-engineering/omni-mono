import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from .client import segment


@dataclass(frozen=True)
class Tool:
    title: str
    domain: str
    fields: tuple[str, ...] = ()
    write: bool = False
    destructive: bool = False


TOOLS = {
    "ado_list_connections": Tool(
        "List authorized connections and resource IDs", "connection"
    ),
    "ado_list_repositories": Tool("List selected repositories", "project"),
    "ado_get_file": Tool(
        "Read repository file at a commit or branch", "repo", ("path", "ref")
    ),
    "ado_list_commits": Tool(
        "List commits on a branch", "repo", ("ref", "limit", "offset")
    ),
    "ado_list_pull_requests": Tool(
        "List pull requests in a selected repository", "repo", ("limit", "offset")
    ),
    "ado_get_pull_request": Tool(
        "Get a pull request and review context", "repo", ("pull_request_id",)
    ),
    "ado_get_pull_request_changes": Tool(
        "Get paginated changes for a pull request iteration",
        "repo",
        (
            "pull_request_id",
            "iteration_id",
            "comparing_iteration_id",
            "limit",
            "offset",
        ),
    ),
    "ado_list_pull_request_threads": Tool(
        "Read pull request discussion threads", "repo", ("pull_request_id",)
    ),
    "ado_list_pull_request_policies": Tool(
        "Read pull request policy evaluations", "repo", ("pull_request_id",)
    ),
    "ado_list_pull_request_statuses": Tool(
        "Read pull request validation statuses", "repo", ("pull_request_id",)
    ),
    "ado_create_branch": Tool(
        "Create a branch at a known commit", "repo", ("ref", "commit_id"), True
    ),
    "ado_create_pull_request": Tool(
        "Create a pull request",
        "repo",
        ("source_ref", "target_ref", "title", "description"),
        True,
    ),
    "ado_update_pull_request": Tool(
        "Update pull request title, description or draft state",
        "repo",
        ("pull_request_id", "changes"),
        True,
    ),
    "ado_complete_pull_request": Tool(
        "Complete a pull request at an expected source commit without policy bypass",
        "repo",
        ("pull_request_id", "commit_id"),
        True,
        True,
    ),
    "ado_abandon_pull_request": Tool(
        "Abandon a pull request", "repo", ("pull_request_id",), True, True
    ),
    "ado_reactivate_pull_request": Tool(
        "Reactivate an abandoned pull request", "repo", ("pull_request_id",), True
    ),
    "ado_set_pull_request_autocomplete": Tool(
        "Enable or disable pull request autocomplete without policy bypass",
        "repo",
        ("pull_request_id", "identity_id", "enabled"),
        True,
    ),
    "ado_add_pull_request_reviewer": Tool(
        "Add a pull request reviewer", "repo", ("pull_request_id", "identity_id"), True
    ),
    "ado_vote_pull_request": Tool(
        "Cast a review vote", "repo", ("pull_request_id", "identity_id", "vote"), True
    ),
    "ado_comment_pull_request": Tool(
        "Start a pull request discussion or anchored advisory finding",
        "repo",
        (
            "pull_request_id",
            "text",
            "file_path",
            "line",
            "end_line",
            "side",
            "iteration_id",
            "change_tracking_id",
            "comparing_iteration_id",
        ),
        True,
    ),
    "ado_reply_pull_request_thread": Tool(
        "Reply to a pull request thread",
        "repo",
        ("pull_request_id", "thread_id", "text"),
        True,
    ),
    "ado_resolve_pull_request_thread": Tool(
        "Resolve a pull request thread", "repo", ("pull_request_id", "thread_id"), True
    ),
    "ado_list_work_items": Tool(
        "Find work items by selected project and field equality",
        "boards",
        ("filters", "limit"),
    ),
    "ado_get_work_item": Tool(
        "Read a work item with revisions and comments", "boards", ("work_item_id",)
    ),
    "ado_create_work_item": Tool(
        "Create a work item in the selected project",
        "boards",
        ("work_item_type", "changes"),
        True,
    ),
    "ado_update_work_item": Tool(
        "Update work item fields with expected revision",
        "boards",
        ("work_item_id", "revision", "changes"),
        True,
    ),
    "ado_comment_work_item": Tool(
        "Comment on a work item", "boards", ("work_item_id", "text"), True
    ),
    "ado_list_builds": Tool(
        "List builds for selected pipelines", "pipelines", ("limit",)
    ),
    "ado_get_build": Tool(
        "Read a build and its stage/job timeline", "pipelines", ("build_id",)
    ),
    "ado_get_build_log": Tool("Read a build log", "pipelines", ("build_id", "log_id")),
    "ado_list_build_artifacts": Tool(
        "List build artifact metadata", "pipelines", ("build_id",)
    ),
    "ado_queue_build": Tool(
        "Queue a selected pipeline", "pipelines", ("pipeline_id", "source_ref"), True
    ),
    "ado_cancel_build": Tool(
        "Cancel a selected pipeline build", "pipelines", ("build_id",), True, True
    ),
}
WRITE_TOOLS = tuple(name for name, tool in TOOLS.items() if tool.write)

FIELDS = {
    "response_format": (
        Literal["json", "markdown"],
        Field(default="json", description="JSON data or readable Markdown"),
    ),
    "connection_id": (
        str,
        Field(description="Connection ID from ado_list_connections", min_length=1),
    ),
    "project_id": (
        str,
        Field(description="Selected project GUID", pattern=r"^[0-9a-fA-F-]{36}$"),
    ),
    "repository_id": (
        str,
        Field(description="Selected repository GUID", pattern=r"^[0-9a-fA-F-]{36}$"),
    ),
    "operation_id": (
        str,
        Field(
            description="Unique operation intent ID; reuse only to reconcile the same operation",
            min_length=1,
            max_length=128,
        ),
    ),
    "path": (str, Field(description="Repository file path", min_length=1)),
    "ref": (str, Field(description="Branch name or refs/heads/ branch", min_length=1)),
    "source_ref": (
        str,
        Field(description="Source refs/heads/ branch", pattern=r"^refs/heads/[^\s]+$"),
    ),
    "target_ref": (
        str,
        Field(description="Target refs/heads/ branch", pattern=r"^refs/heads/[^\s]+$"),
    ),
    "title": (
        str,
        Field(description="Pull request title", min_length=1, max_length=400),
    ),
    "description": (str, Field(default="", description="Pull request description")),
    "text": (str, Field(description="Comment text", min_length=1)),
    "commit_id": (
        str,
        Field(
            description="Expected 40-character source commit SHA",
            pattern=r"^[a-fA-F0-9]{40}$",
        ),
    ),
    "identity_id": (
        str,
        Field(description="Azure DevOps identity GUID", pattern=r"^[0-9a-fA-F-]{36}$"),
    ),
    "vote": (
        int,
        Field(
            description="Vote: -10 rejected, -5 waiting, 0 no vote, 5 suggestions, 10 approved"
        ),
    ),
    "enabled": (bool, Field(description="Enable autocomplete")),
    "changes": (
        dict[str, object],
        Field(
            description="Business fields to change; protected resource fields and policy bypass are forbidden"
        ),
    ),
    "filters": (
        dict[str, str],
        Field(
            default_factory=dict,
            description="Work item field equality filters, e.g. System.State=Active",
        ),
    ),
    "work_item_type": (
        str,
        Field(description="Work item type, e.g. Task or Bug", min_length=1),
    ),
    "limit": (int, Field(default=100, description="Page size", ge=1, le=200)),
    "offset": (int, Field(default=0, description="Page offset", ge=0)),
}
FIELDS.update(
    {
        "file_path": (
            str | None,
            Field(
                default=None,
                pattern=r"^/[^\r\n\x00]+$",
                description="Changed repository file path for an inline finding",
            ),
        ),
        "line": (int | None, Field(default=None, gt=0)),
        "end_line": (int | None, Field(default=None, gt=0)),
        "side": (Literal["left", "right"], Field(default="right")),
        "change_tracking_id": (int | None, Field(default=None, gt=0)),
        "comparing_iteration_id": (int, Field(default=0, ge=0)),
    }
)

for field in (
    "pull_request_id",
    "iteration_id",
    "thread_id",
    "work_item_id",
    "build_id",
    "pipeline_id",
    "log_id",
    "revision",
):
    FIELDS[field] = (int, Field(description=field.replace("_", " "), gt=0))


class CommentInput(BaseModel):
    @model_validator(mode="after")
    def anchors(self):
        values = [self.file_path, self.line, self.iteration_id, self.change_tracking_id]
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError(
                "Inline findings require file_path, line, iteration_id and change_tracking_id"
            )
        if self.end_line is not None and (
            self.line is None or self.end_line < self.line
        ):
            raise ValueError("end_line requires a valid start line")
        if (
            self.iteration_id is not None
            and self.comparing_iteration_id > self.iteration_id
        ):
            raise ValueError(
                "Comparing iteration must not exceed the reviewed iteration"
            )
        return self


def model_for(name):
    tool = TOOLS[name]
    fields = [] if tool.domain == "connection" else ["connection_id", "project_id"]
    if tool.domain == "repo":
        fields.append("repository_id")
    if tool.write:
        fields.append("operation_id")
    fields.extend(tool.fields)
    fields.append("response_format")
    definitions = {field: FIELDS[field] for field in fields}
    if name == "ado_comment_pull_request":
        definitions["iteration_id"] = (int | None, Field(default=None, gt=0))
    return create_model(
        name + "Input",
        __base__=CommentInput if name == "ado_comment_pull_request" else BaseModel,
        __config__=ConfigDict(extra="forbid"),
        **definitions,
    )


def work_fields(changes):
    import re

    if not changes or any(
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]+", key)
        or key
        in {
            "System.Id",
            "System.TeamProject",
            "System.Rev",
            "System.CreatedBy",
            "System.CreatedDate",
            "System.ChangedBy",
            "System.ChangedDate",
        }
        for key in changes
    ):
        raise HTTPException(422, "Only business work item fields may be changed")
    return [
        {"op": "add", "path": "/fields/" + key, "value": value}
        for key, value in changes.items()
    ]


async def execute(connections, app_id, name, arguments):
    if name not in TOOLS:
        raise HTTPException(404, "Unknown Azure DevOps tool")
    args = model_for(name).model_validate(arguments or {}).model_dump()
    tool = TOOLS[name]
    if tool.domain == "connection":
        return {
            "connections": [
                {
                    "id": value["id"],
                    "organization": value["organization"],
                    "scopes": value["scopes"],
                    "write_tools": value["write_tools"],
                }
                for value in connections.records().values()
                if value["enabled"] and app_id in value["app_ids"]
            ]
        }
    connection = connections.authorize(
        args["connection_id"],
        app_id,
        args["project_id"],
        args.get("repository_id"),
        name if tool.write else None,
    )
    scope = next(
        scope
        for scope in connection["scopes"]
        if scope["project"] == args["project_id"]
    )
    if tool.domain == "boards" and not scope["boards"]:
        raise HTTPException(403, "Boards is not selected")
    if tool.domain == "pipelines" and not scope["pipelines"]:
        raise HTTPException(403, "No pipelines are selected")
    intent_key = None
    if tool.write:
        intent_key = args["connection_id"] + ":" + app_id + ":" + args["operation_id"]
        fingerprint = hashlib.sha256(
            json.dumps([name, args], sort_keys=True).encode()
        ).hexdigest()
        with connections.w.store.transaction():
            previous = dict(connections.w.store.docs("ado.operation")).get(intent_key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "Operation ID was used with different arguments"
                    )
                if previous["status"] == "succeeded":
                    return previous["result"]
                raise HTTPException(
                    409,
                    "Operation outcome is uncertain or rejected; reconcile in Azure DevOps before creating a new intent",
                )
            connections.w.store.put(
                "ado.operation",
                intent_key,
                {
                    "status": "pending",
                    "fingerprint": fingerprint,
                    "tool": name,
                    "resource": {
                        key: args.get(key)
                        for key in (
                            "project_id",
                            "repository_id",
                            "pull_request_id",
                            "thread_id",
                        )
                    },
                },
            )
    try:
        result = await perform(
            connections.client(connection), connection, scope, name, args
        )
    except BaseException:
        if intent_key:
            connections.w.store.put(
                "ado.operation",
                intent_key,
                {"status": "uncertain", "fingerprint": fingerprint, "tool": name},
            )
        raise
    if intent_key:
        connections.w.store.put(
            "ado.operation",
            intent_key,
            {
                "status": "succeeded",
                "fingerprint": fingerprint,
                "tool": name,
                "result": result,
                "resource": {
                    key: args.get(key)
                    for key in (
                        "project_id",
                        "repository_id",
                        "pull_request_id",
                        "thread_id",
                    )
                },
            },
        )
    return result


async def perform(client, connection, scope, name, args):
    import fnmatch
    import re

    project = segment(args["project_id"])
    base = f"/{project}/_apis"
    repo = base + "/git/repositories/" + segment(args.get("repository_id", ""))
    pr = repo + "/pullrequests/" + str(args.get("pull_request_id", ""))
    page = {"$top": args.get("limit", 100), "$skip": args.get("offset", 0)}
    if name == "ado_list_repositories":
        values = await client.all(base + "/git/repositories")
        broker = client.connections.w.sandbox_url.rstrip("/")
        return {
            "repositories": [
                {
                    **value,
                    "clone_url": f"{broker}/azure-devops/git/{connection['id']}/{args['project_id']}/{value['id']}",
                    "credential_env": "AZURE_DEVOPS_MCP_TOKEN",
                }
                for value in values
                if value["id"] in scope["repositories"]
            ]
        }
    if name == "ado_get_file":
        return await client.json(
            "GET",
            repo + "/items",
            params={
                "path": args["path"],
                "versionDescriptor.version": args["ref"].removeprefix("refs/heads/"),
                "versionDescriptor.versionType": (
                    "commit"
                    if re.fullmatch(r"[a-fA-F0-9]{40}", args["ref"])
                    else "branch"
                ),
                "includeContent": "true",
            },
        )
    if name == "ado_list_commits":
        return await client.json(
            "GET",
            repo + "/commits",
            params={
                **page,
                "searchCriteria.itemVersion.version": args["ref"].removeprefix(
                    "refs/heads/"
                ),
            },
        )
    if name == "ado_list_pull_requests":
        return await client.json(
            "GET",
            repo + "/pullrequests",
            params={**page, "searchCriteria.status": "all"},
        )
    if name == "ado_get_pull_request":
        return {
            "pull_request": await client.json("GET", pr),
            "threads": await client.all(pr + "/threads"),
            "iterations": await client.all(pr + "/iterations"),
            "statuses": await client.all(
                pr + "/statuses", params={"$top": 100}, offset=True
            ),
        }
    if name == "ado_get_pull_request_changes":
        return await client.json(
            "GET",
            pr + f"/iterations/{args['iteration_id']}/changes",
            params={**page, "$compareTo": args.get("comparing_iteration_id", 0)},
        )
    if name == "ado_list_pull_request_threads":
        return {"threads": await client.all(pr + "/threads")}
    if name == "ado_list_pull_request_policies":
        return {
            "policies": await client.all(
                base + "/policy/evaluations",
                params={
                    "artifactId": f"vstfs:///CodeReview/CodeReviewId/{args['project_id']}/{args['pull_request_id']}"
                },
                version="7.1-preview.1",
            )
        }
    if name == "ado_list_pull_request_statuses":
        return {
            "statuses": await client.all(
                pr + "/statuses", params={"$top": 100}, offset=True
            )
        }
    if name == "ado_create_branch":
        ref = args["ref"]
        if not ref.startswith("refs/heads/") or not any(
            fnmatch.fnmatchcase(ref, pattern) for pattern in connection["push_branches"]
        ):
            raise HTTPException(403, "Branch is outside the configured push patterns")
        return await client.json(
            "POST",
            repo + "/refs",
            json_body=[
                {"name": ref, "oldObjectId": "0" * 40, "newObjectId": args["commit_id"]}
            ],
        )
    if name == "ado_create_pull_request":
        return await client.json(
            "POST",
            repo + "/pullrequests",
            json_body={
                "sourceRefName": args["source_ref"],
                "targetRefName": args["target_ref"],
                "title": args["title"],
                "description": args["description"],
            },
        )
    if name == "ado_update_pull_request":
        if not args["changes"] or set(args["changes"]) - {
            "title",
            "description",
            "isDraft",
        }:
            raise HTTPException(
                422, "Only title, description and isDraft can be updated"
            )
        if any(
            not isinstance(value, bool if key == "isDraft" else str)
            for key, value in args["changes"].items()
        ):
            raise HTTPException(
                422, "Draft state must be boolean and title/description must be text"
            )
        return await client.json("PATCH", pr, json_body=args["changes"])
    if name in {
        "ado_complete_pull_request",
        "ado_abandon_pull_request",
        "ado_reactivate_pull_request",
        "ado_set_pull_request_autocomplete",
    }:
        status = {
            "ado_complete_pull_request": "completed",
            "ado_abandon_pull_request": "abandoned",
            "ado_reactivate_pull_request": "active",
        }
        payload = (
            {"status": status[name]}
            if name in status
            else {
                "autoCompleteSetBy": (
                    {"id": args["identity_id"]} if args["enabled"] else None
                )
            }
        )
        if name == "ado_complete_pull_request":
            payload["lastMergeSourceCommit"] = {"commitId": args["commit_id"]}
        payload["completionOptions"] = {
            "bypassPolicy": False,
            "deleteSourceBranch": False,
        }
        return await client.json("PATCH", pr, json_body=payload)
    if name in {"ado_add_pull_request_reviewer", "ado_vote_pull_request"}:
        if name == "ado_vote_pull_request" and args["vote"] not in {-10, -5, 0, 5, 10}:
            raise HTTPException(422, "Invalid review vote")
        return await client.json(
            "PUT",
            pr + "/reviewers/" + segment(args["identity_id"]),
            json_body={
                "id": args["identity_id"],
                **({"vote": args["vote"]} if "vote" in args else {}),
            },
        )
    if name == "ado_comment_pull_request":
        payload = {
            "comments": [
                {"parentCommentId": 0, "content": args["text"], "commentType": 1}
            ],
            "status": 1,
        }
        if args.get("file_path"):
            side = args["side"]
            payload["threadContext"] = {
                "filePath": args["file_path"],
                side + "FileStart": {"line": args["line"], "offset": 1},
                side
                + "FileEnd": {
                    "line": args.get("end_line") or args["line"],
                    "offset": 1,
                },
            }
            payload["pullRequestThreadContext"] = {
                "changeTrackingId": args["change_tracking_id"],
                "iterationContext": {
                    "firstComparingIteration": args["comparing_iteration_id"],
                    "secondComparingIteration": args["iteration_id"],
                },
            }
        return await client.json(
            "POST",
            pr + "/threads",
            json_body=payload,
        )
    if name == "ado_reply_pull_request_thread":
        return await client.json(
            "POST",
            pr + f"/threads/{args['thread_id']}/comments",
            json_body={"content": args["text"], "commentType": 1},
        )
    if name == "ado_resolve_pull_request_thread":
        return await client.json(
            "PATCH", pr + f"/threads/{args['thread_id']}", json_body={"status": 2}
        )
    if name == "ado_list_work_items":
        filters = args["filters"]
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]+", field) for field in filters):
            raise HTTPException(422, "Invalid work item field")
        conditions = ["[System.TeamProject] = @project"] + [
            "[" + field + "] = '" + value.replace("'", "''") + "'"
            for field, value in filters.items()
        ]
        return await client.json(
            "POST",
            base + "/wit/wiql",
            params={"$top": args["limit"]},
            json_body={
                "query": "SELECT [System.Id] FROM WorkItems WHERE "
                + " AND ".join(conditions)
                + " ORDER BY [System.Id]"
            },
        )
    if tool_domain(name) == "boards":
        item = base + "/wit/workitems/" + str(args.get("work_item_id", ""))
        if "work_item_id" in args:
            value = await client.json("GET", item)
            project_data = await client.json("GET", "/_apis/projects/" + project)
            if (
                value.get("fields", {}).get("System.TeamProject")
                != project_data["name"]
            ):
                raise HTTPException(403, "Work item belongs to another project")
        if name == "ado_get_work_item":
            return {
                "work_item": value,
                "revisions": await client.all(item + "/revisions"),
                "comments": await client.all(
                    item + "/comments", version="7.1-preview.4"
                ),
            }
        if name == "ado_create_work_item":
            return await client.json(
                "POST",
                base + "/wit/workitems/$" + segment(args["work_item_type"]),
                json_body=work_fields(args["changes"]),
                content_type="application/json-patch+json",
            )
        if name == "ado_update_work_item":
            return await client.json(
                "PATCH",
                item,
                json_body=[
                    {"op": "test", "path": "/rev", "value": args["revision"]},
                    *work_fields(args["changes"]),
                ],
                content_type="application/json-patch+json",
            )
        if name == "ado_comment_work_item":
            return await client.json(
                "POST",
                item + "/comments",
                json_body={"text": args["text"]},
                version="7.1-preview.4",
            )
    if tool_domain(name) == "pipelines":
        if "build_id" in args:
            build = base + f"/build/builds/{args['build_id']}"
            value = await client.json("GET", build)
            if value.get("definition", {}).get("id") not in scope["pipelines"]:
                raise HTTPException(403, "Build belongs to another pipeline")
        if name == "ado_list_builds":
            return await client.json(
                "GET",
                base + "/build/builds",
                params={
                    "definitions": ",".join(map(str, scope["pipelines"])),
                    "$top": args["limit"],
                },
            )
        if name == "ado_get_build":
            return {
                "build": value,
                "timeline": await client.json("GET", build + "/timeline"),
            }
        if name == "ado_get_build_log":
            response = await client.request("GET", build + f"/logs/{args['log_id']}")
            return {"text": response.text}
        if name == "ado_list_build_artifacts":
            return {"artifacts": await client.all(build + "/artifacts")}
        if name == "ado_queue_build":
            if args["pipeline_id"] not in scope["pipelines"]:
                raise HTTPException(403, "Pipeline is not selected")
            return await client.json(
                "POST",
                base + "/build/builds",
                json_body={
                    "definition": {"id": args["pipeline_id"]},
                    "sourceBranch": args["source_ref"],
                },
            )
        if name == "ado_cancel_build":
            return await client.json("PATCH", build, json_body={"status": "cancelling"})
    raise HTTPException(404, "Tool operation is not implemented")


def tool_domain(name):
    return TOOLS[name].domain
