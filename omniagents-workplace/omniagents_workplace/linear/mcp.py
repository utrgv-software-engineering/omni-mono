"""The Linear MCP server, served by the workplace.

Same server, as an agent sees it: the 60 tools of Linear's MCP server
(``https://mcp.linear.app/mcp``), definitions verbatim
(``reference/linear_mcp_tools.json``), over Streamable HTTP, authorized with
a token (``Authorization: Bearer <token>``, an API key or an app's OAuth
token) and acting as that user or app. Results are the JSON the real server
returns and failures its three kinds of message (an API error as
``{"error": ..., "message": ...}``, the server's own ``Error: ...``, and
``Input validation error: ...``), captured in
``reference/linear_mcp_responses.json``.

Issues (every field, relations, ``patch`` edits), comments, labels
(retiring them too), statuses, teams, people, attachments, delegation to
agents, projects and their milestones and updates, cycles, documents and
each user's notifications work. Initiatives, project labels, releases,
custom views, templates and sharing are empty here; tools that change them
say they are not available in the workplace.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
import secrets
import time
from importlib import resources
from typing import Any, Callable, Dict, List, Optional

import jsonschema
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from ..wire import Wire
from .state import (
    PRIORITIES,
    PROJECT_STATUSES,
    Comment,
    Cycle,
    Document,
    Issue,
    LinearError,
    LinearState,
    Milestone,
    Notification,
    Project,
    StatusUpdate,
    User,
    iso,
)

__all__ = ["LinearMCP", "load_tool_definitions"]

_UNAVAILABLE = {
    "save_project_label",
    "retire_project_label",
    "restore_project_label",
    "save_release",
    "save_release_note",
    "share_issue",
    "unshare_issue",
    "search_documentation",
}


def apply_patch(content: str, ops: List[Dict[str, Any]]) -> str:
    """Linear's ``patch``: edits applied in order, all or none; each anchor matches exactly once."""
    out = content or ""
    for n, op in enumerate(ops):
        kind = op.get("op")
        if kind in ("prepend", "append"):
            out = op["text"] + out if kind == "prepend" else out + op["text"]
            continue
        anchor = op.get("old_string") if kind == "replace" else op.get("anchor")
        count = out.count(anchor or "")
        if not anchor or count == 0:
            raise ToolError(
                f"Error: patch[{n}]: the text to match was not found in the current content"
            )
        if count > 1 and not (kind == "replace" and op.get("replace_all")):
            raise ToolError(
                f"Error: patch[{n}]: the text to match occurs {count} times; it must match exactly once"
            )
        if kind == "replace":
            out = out.replace(
                anchor, op["new_string"], -1 if op.get("replace_all") else 1
            )
        elif kind == "insert_before":
            out = out.replace(anchor, op["text"] + anchor, 1)
        elif kind == "insert_after":
            out = out.replace(anchor, anchor + op["text"], 1)
        else:
            raise ToolError(f"Error: patch[{n}]: unknown op {kind!r}")
    return out


def _status_id(kind: str) -> str:
    import uuid as _uuid_mod

    return str(
        _uuid_mod.uuid5(_uuid_mod.NAMESPACE_URL, f"workplace-project-status:{kind}")
    )


def load_tool_definitions() -> Dict[str, Any]:
    text = (
        resources.files("omniagents_workplace")
        .joinpath("reference/linear_mcp_tools.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


class ToolError(Exception):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _api_error(message: str) -> ToolError:
    return ToolError(
        _json(
            {
                "error": "invalid_request",
                "message": message,
                "status": 400,
                "requestId": secrets.token_hex(8),
            }
        )
    )


def _error(exc: LinearError) -> ToolError:
    return _api_error(exc.message) if exc.api else ToolError(f"Error: {exc.message}")


class LinearMCP:
    """The MCP endpoint: an ASGI app for ``/linear/mcp``."""

    def __init__(
        self,
        state: LinearState,
        wire: Wire,
        base_url: Callable[[], str],
        upload_url: Optional[Callable[[], str]] = None,
    ):
        self.state = state
        self.wire = wire
        self.base_url = base_url
        self.upload_url = upload_url or base_url
        reference = load_tool_definitions()
        self._definitions: List[Dict[str, Any]] = reference["tools"]
        self._schemas = {d["name"]: d["inputSchema"] for d in self._definitions}
        info = reference.get("serverInfo") or {}
        self.server = Server(
            info.get("name", "linear"),
            version=info.get("version"),
            instructions=reference.get("instructions"),
        )
        self.server.list_tools()(self._list_tools)
        self.server.call_tool(validate_input=False)(self._call_tool)
        self.manager = StreamableHTTPSessionManager(
            self.server, json_response=False, stateless=True
        )
        #: Upload slots: token -> (issue or None, filename, content type, size, title, subtitle).
        self.uploads: Dict[str, Dict[str, Any]] = {}

    @contextlib.asynccontextmanager
    async def run(self):
        async with self.manager.run():
            yield

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if self._caller(headers) is None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (
                            b"www-authenticate",
                            b'Bearer realm="OAuth", error="invalid_token"',
                        ),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"invalid_token","error_description":"Missing or invalid access token"}',
                }
            )
            return
        await self.manager.handle_request(scope, receive, send)

    def _caller(self, headers: Dict[str, str]) -> Optional[User]:
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return self.state.user_for_token(token) if token else None

    def _me(self) -> User:
        request = self.server.request_context.request
        user = (
            self._caller({k.lower(): v for k, v in request.headers.items()})
            if request
            else None
        )
        if user is None:
            raise ToolError("Error: Unauthorized")
        return user

    @property
    def base(self) -> str:
        return self.base_url().rstrip("/")

    async def _list_tools(self) -> List[types.Tool]:
        self._me()
        return [types.Tool.model_validate(d) for d in self._definitions]

    async def _call_tool(self, name: str, arguments: Dict[str, Any]):
        me = self._me()
        started = time.monotonic()
        entry = self.wire.record(
            service="linear",
            direction="in",
            kind="mcp",
            title=name,
            actor=me.name,
            status="pending",
            request={
                k: (v if k != "base64Content" else "<base64>")
                for k, v in (arguments or {}).items()
            },
        )
        try:
            schema = self._schemas.get(name)
            if schema is None:
                raise ToolError(f"MCP error -32602: Tool {name} not found")
            self._validate(name, schema, arguments or {})
            if name in _UNAVAILABLE:
                raise ToolError(f"Error: {name} is not available in the workplace")
            handler = getattr(self, f"_{name}", None)
            if handler is None:
                raise ToolError(f"Error: {name} is not available in the workplace")
            try:
                text = handler(me, arguments or {})
            except LinearError as exc:
                raise _error(exc)
        except ToolError as exc:
            self.wire.update(
                entry,
                status="error",
                response=str(exc),
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], isError=True
            )
        self.wire.update(
            entry,
            status="ok",
            response=_display(text),
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return [types.TextContent(type="text", text=text)]

    def _validate(
        self, name: str, schema: Dict[str, Any], args: Dict[str, Any]
    ) -> None:
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
        if not errors:
            return
        parts = []
        for e in errors:
            path = ".".join(str(p) for p in e.path)
            if e.validator == "required":
                missing = re.search(r"'([^']+)'", e.message)
                path = missing.group(1) if missing else path
                parts.append(
                    f"{path}: Invalid input: expected string, received undefined"
                )
            elif e.validator == "enum":
                options = "|".join(json.dumps(v) for v in e.validator_value)
                parts.append(f"{path}: Invalid option: expected one of {options}")
            elif e.validator == "type":
                parts.append(
                    f"{path}: Invalid input: expected {e.validator_value}, received {type(e.instance).__name__}"
                )
            else:
                parts.append(f"{path}: {e.message}")
        raise ToolError(
            f"Input validation error: Invalid arguments for tool {name}: "
            + "; ".join(parts)
        )

    # -- JSON -------------------------------------------------------------------

    def _user_json(self, u: User, teams: bool = False) -> Dict[str, Any]:
        out = {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "displayName": u.display_name,
            "avatarUrl": None,
            "isAdmin": u.is_admin,
            "isGuest": False,
            "isActive": True,
            "createdAt": iso(u.created_at),
            "updatedAt": iso(u.created_at),
            "status": "Online",
        }
        if teams:
            out["teams"] = [
                {"id": t.id, "name": t.name, "key": t.key}
                for t in self.state.teams.values()
            ]
        return out

    def _team_json(self, t, description: bool = False) -> Dict[str, Any]:
        out = {
            "id": t.id,
            "key": t.key,
            "parentId": None,
            "displayName": t.name,
            "icon": None,
            "name": t.name,
            "visibility": "public",
            "createdAt": iso(t.created_at),
            "updatedAt": iso(t.created_at),
        }
        if description:
            out["description"] = t.description or None
        return out

    def _issue_json(
        self, issue: Issue, *, kind: str = "get", relations: bool = False
    ) -> Dict[str, Any]:
        s = self.state
        state = s.state(issue)
        team = s.teams[issue.team_id]
        creator = s.users.get(issue.creator_id)
        out: Dict[str, Any] = {
            "id": issue.identifier,
            "uuid": issue.id,
            "title": issue.title,
            "description": issue.description,
            "priority": {
                "value": issue.priority,
                "name": PRIORITIES.get(issue.priority, "No priority"),
            },
            "url": s.issue_url(self.base, issue),
            "gitBranchName": s.branch_name(
                issue, s.users.get(issue.assignee_id) or creator
            ),
            "createdAt": iso(issue.created_at),
            "updatedAt": iso(issue.updated_at),
            "archivedAt": iso(issue.archived_at),
            "completedAt": iso(issue.completed_at),
            "startedAt": iso(issue.started_at),
            "canceledAt": iso(issue.canceled_at),
            "dueDate": issue.due_date,
            "slaStartedAt": None,
            "slaMediumRiskAt": None,
            "slaHighRiskAt": None,
            "slaBreachesAt": None,
            "status": state.name,
            "statusType": state.type,
            "labels": [s.labels[i].name for i in issue.label_ids if i in s.labels],
        }
        if kind != "list":
            out["attachments"] = [
                {"id": a.id, "title": a.title, "subtitle": a.subtitle, "url": a.url}
                for a in reversed(issue.attachments)
            ]
            out["documents"] = []
        if kind == "get":
            out["stateHistory"] = [
                {
                    "state": {
                        "id": h["state_id"],
                        "name": next(
                            x.name for x in team.states if x.id == h["state_id"]
                        ),
                        "type": next(
                            x.type for x in team.states if x.id == h["state_id"]
                        ),
                    },
                    "startedAt": iso(h["started_at"]),
                    "endedAt": iso(h["ended_at"]),
                }
                for h in reversed(issue.state_history)
            ]
        out["createdBy"] = creator.name if creator else None
        out["createdById"] = issue.creator_id
        if issue.assignee_id:
            assignee = s.users.get(issue.assignee_id)
            out["assignee"] = assignee.name if assignee else None
            out["assigneeId"] = issue.assignee_id
        if issue.delegate_id:
            delegate = s.users.get(issue.delegate_id)
            out["delegate"] = delegate.name if delegate else None
            out["delegateId"] = issue.delegate_id
        if issue.parent_id:
            parent = s.issues.get(issue.parent_id)
            out["parentId"] = parent.identifier if parent else issue.parent_id
        out["team"] = team.name
        out["teamId"] = team.id
        if issue.estimate is not None:
            out["estimate"] = {
                "value": issue.estimate,
                "name": f"{issue.estimate:g} Points",
            }
        if issue.project_id and issue.project_id in s.projects:
            project = s.projects[issue.project_id]
            out["project"] = project.name
            out["projectId"] = project.id
        if issue.milestone_id and issue.milestone_id in s.milestones:
            out["projectMilestone"] = {
                "id": issue.milestone_id,
                "name": s.milestones[issue.milestone_id].name,
            }
        if issue.cycle_id and issue.cycle_id in s.cycles:
            cycle = s.cycles[issue.cycle_id]
            out["cycleId"] = cycle.id
            out["cycle"] = {"id": cycle.id, "number": cycle.number, "name": cycle.name}
        if relations:
            ident = lambda i: (
                s.issues[i].identifier if i in s.issues else i
            )  # noqa: E731
            out["relations"] = {
                "blocks": [ident(i) for i in issue.blocks],
                "blockedBy": [
                    x.identifier for x in s.issues.values() if issue.id in x.blocks
                ],
                "relatedTo": [ident(i) for i in issue.related],
                "duplicateOf": (
                    ident(issue.duplicate_of) if issue.duplicate_of else None
                ),
            }
        return out

    def _comment_json(self, c: Comment) -> Dict[str, Any]:
        author = self.state.users.get(c.user_id)
        return {
            "id": c.id,
            "body": c.body,
            "attachments": [],
            "createdAt": iso(c.created_at),
            "updatedAt": iso(c.updated_at),
            "parentId": c.parent_id,
            "resolvedAt": None,
            "quotedText": None,
            "author": {"id": c.user_id, "name": author.name if author else None},
            "onBehalfOf": None,
        }

    def _issue(self, ref: str) -> Issue:
        issue = self.state.find_issue(ref)
        if issue is None:
            raise _api_error("Could not find referenced Issue.")
        return issue

    def _fields(
        self, item: Dict[str, Any], fields: Optional[List[str]]
    ) -> Dict[str, Any]:
        if not fields:
            return item
        out = {f: item.get(f) for f in fields if f != "id"}
        out["id"] = item["id"]
        return out

    # -- workspace, teams, people --------------------------------------------------

    def _get_workspace(self, me, args):
        return _json(
            {
                "id": self.state.org_id,
                "name": self.state.url_key,
                "url": f"{self.base}/linear/{self.state.url_key}",
            }
        )

    def _list_teams(self, me, args):
        query = (args.get("query") or "").lower()
        teams = [
            t
            for t in self.state.teams.values()
            if query in t.name.lower() or query in t.key.lower()
        ]
        return _json(
            {"teams": [self._team_json(t) for t in teams], "hasNextPage": False}
        )

    def _get_team(self, me, args):
        team = self.state.find_team(args["query"])
        if team is None:
            raise ToolError(f'Error: Could not find team "{args["query"]}"')
        return _json(self._team_json(team, description=True))

    def _list_users(self, me, args):
        query = (args.get("query") or "").lower()
        users = [
            u
            for u in self.state.users.values()
            if not query
            or query in u.name.lower()
            or query in u.email.lower()
            or query in u.display_name.lower()
        ]
        return _json(
            {"users": [self._user_json(u) for u in users], "hasNextPage": False}
        )

    def _get_user(self, me, args):
        user = self.state.find_user(args["query"], me)
        if user is None:
            raise ToolError(f'Error: Could not find user "{args["query"]}"')
        return _json(self._user_json(user, teams=True))

    def _get_triage_responsibility(self, me, args):
        team = self.state.find_team(args["team"])
        if team is None:
            raise ToolError(f'Error: Could not find team "{args["team"]}"')
        return _json(
            {
                "team": {"id": team.id, "key": team.key, "name": team.name},
                "triageEnabled": False,
                "currentUser": None,
            }
        )

    # -- statuses and labels -----------------------------------------------------

    def _team_arg(self, ref: str):
        team = self.state.find_team(ref)
        if team is None:
            raise ToolError(f'Error: Could not find team "{ref}"')
        return team

    def _list_issue_statuses(self, me, args):
        team = self._team_arg(args["team"])
        order = {
            "started": 0,
            "backlog": 1,
            "unstarted": 2,
            "duplicate": 3,
            "completed": 4,
            "canceled": 5,
        }
        return _json(
            [
                {"id": s.id, "type": s.type, "name": s.name}
                for s in sorted(team.states, key=lambda s: order.get(s.type, 9))
            ]
        )

    def _get_issue_status(self, me, args):
        team = self._team_arg(args["team"])
        status = next(
            (s for s in team.states if s.id == args["id"]), None
        ) or team.state(args.get("name") or "")
        if status is None:
            raise _api_error("Could not find referenced WorkflowState.")
        return _json({"id": status.id, "type": status.type, "name": status.name})

    def _list_issue_labels(self, me, args):
        team = self.state.find_team(args["team"]) if args.get("team") else None
        name = (args.get("name") or "").lower()
        labels = [
            l
            for l in self.state.labels.values()
            if (team is None or l.team_id in (None, team.id))
            and (not name or l.name.lower() == name)
        ]
        return _json(
            {
                "labels": [
                    {
                        "id": l.id,
                        "name": l.name,
                        "color": l.color,
                        "description": l.description,
                        "archivedAt": None,
                        "retiredAt": iso(l.retired_at),
                    }
                    for l in labels
                ],
                "hasNextPage": False,
            }
        )

    def _create_issue_label(self, me, args):
        team_id = args.get("teamId")
        if team_id and team_id not in self.state.teams:
            raise _api_error("teamId must be a UUID.")
        if self.state.find_label(args["name"], team_id):
            raise _api_error("Duplicate label name")
        label = self.state._add_label(
            args["name"],
            args.get("color") or "#bec2c8",
            args.get("description"),
            team_id,
        )
        return _json(
            {
                "id": label.id,
                "name": label.name,
                "color": label.color,
                "isGroup": False,
                "teamId": team_id,
            }
        )

    def _save_issue_label(self, me, args):
        if not args.get("id"):
            return self._create_issue_label(me, args)
        label = self.state.labels.get(args["id"])
        if label is None:
            raise _api_error("Could not find referenced IssueLabel.")
        self.state.update_label(
            label,
            **{
                k: args[k]
                for k in ("name", "description", "color")
                if args.get(k) is not None
            },
        )
        return _json(
            {
                "id": label.id,
                "name": label.name,
                "color": label.color,
                "isGroup": False,
                "teamId": label.team_id,
            }
        )

    def _retire_issue_label(self, me, args):
        return self._label_retired(args, True)

    def _restore_issue_label(self, me, args):
        return self._label_retired(args, False)

    def _label_retired(self, args, retired: bool):
        label = self.state.labels.get(args["id"])
        if label is None:
            raise _api_error("Could not find referenced IssueLabel.")
        self.state.set_label_retired(label, retired)
        return _json(
            {"id": label.id, "name": label.name, "retiredAt": iso(label.retired_at)}
        )

    # -- issues -----------------------------------------------------------------------

    def _list_issues(self, me, args):
        s = self.state
        items = [
            i
            for i in s.issues.values()
            if args.get("includeArchived") or i.archived_at is None
        ]
        if args.get("team"):
            team = self._team_arg(args["team"])
            items = [i for i in items if i.team_id == team.id]
        if args.get("state"):
            ref = args["state"].lower()
            items = [
                i
                for i in items
                if s.state(i).name.lower() == ref
                or s.state(i).type == ref
                or s.state(i).id == args["state"]
            ]
        if args.get("label"):
            label = args["label"].lower()
            items = [
                i
                for i in items
                if any(
                    s.labels[x].name.lower() == label or x == args["label"]
                    for x in i.label_ids
                )
            ]
        for key, attr in (
            ("assignee", "assignee_id"),
            ("creator", "creator_id"),
            ("delegate", "delegate_id"),
        ):
            if key in args and args[key] is not None:
                if args[key] == "null":
                    items = [i for i in items if getattr(i, attr) is None]
                    continue
                user = s.find_user(args[key], me)
                items = [i for i in items if user and getattr(i, attr) == user.id]
        if args.get("priority") is not None:
            items = [i for i in items if i.priority == int(args["priority"])]
        if args.get("parentId"):
            parent = s.find_issue(args["parentId"])
            items = [i for i in items if parent and i.parent_id == parent.id]
        if args.get("query"):
            words = args["query"].lower().split()
            items = [
                i
                for i in items
                if all(w in (i.title + " " + i.description).lower() for w in words)
            ]
        key = "created_at" if args.get("orderBy") == "createdAt" else "updated_at"
        items.sort(key=lambda i: getattr(i, key), reverse=True)
        start = int(args["cursor"]) if str(args.get("cursor") or "").isdigit() else 0
        limit = int(args.get("limit") or 50)
        page = items[start : start + limit]
        out: Dict[str, Any] = {
            "issues": [
                self._fields(self._issue_json(i, kind="list"), args.get("fields"))
                for i in page
            ],
            "hasNextPage": start + limit < len(items),
        }
        if out["hasNextPage"]:
            out["cursor"] = str(start + limit)
        return _json(out)

    def _get_issue(self, me, args):
        issue = self._issue(args["id"])
        return _json(
            self._fields(
                self._issue_json(issue, relations=bool(args.get("includeRelations"))),
                args.get("fields"),
            )
        )

    def _issue_ids(self, refs) -> List[str]:
        out = []
        for ref in refs or []:
            other = self.state.find_issue(ref)
            if other is None:
                raise _api_error("Could not find referenced Issue.")
            out.append(other.id)
        return out

    def _save_issue(self, me, args):
        s = self.state
        changes: Dict[str, Any] = {}
        for key in (
            "title",
            "description",
            "priority",
            "state",
            "labels",
            "addLabels",
            "removeLabels",
            "links",
        ):
            if key in args:
                changes[key] = args[key]
        if args.get("patch"):
            if not args.get("id"):
                raise ToolError(
                    "Error: patch is only valid when updating an existing issue"
                )
            if "description" in args:
                raise ToolError("Error: Provide description or patch, not both")
            current = s.find_issue(args["id"])
            changes["description"] = apply_patch(
                current.description if current else "", args["patch"]
            )
        if "dueDate" in args:
            changes["due_date"] = args["dueDate"]
        if "estimate" in args:
            changes["estimate"] = args["estimate"]
        if "project" in args:
            if args["project"] is None:
                changes["project"] = None
            else:
                project = s.find_project(args["project"])
                if project is None:
                    raise ToolError(
                        f'Error: Could not find project "{args["project"]}"'
                    )
                changes["project"] = project.id
        if args.get("milestone"):
            project_id = changes.get("project")
            if project_id is None:
                existing = s.find_issue(args.get("id") or "")
                project_id = existing.project_id if existing else None
            project = s.projects.get(project_id or "")
            milestone = (
                s.find_milestone(project, args["milestone"])
                if project
                else s.milestones.get(args["milestone"])
            )
            if milestone is None:
                raise ToolError(
                    f'Error: Could not find milestone "{args["milestone"]}"'
                )
            changes["milestone"] = milestone.id
        if "cycle" in args:
            if args["cycle"] is None:
                changes["cycle"] = None
            else:
                existing = s.find_issue(args.get("id") or "")
                team = (
                    s.teams.get(existing.team_id)
                    if existing
                    else (s.find_team(args.get("team") or ""))
                )
                cycle = s.find_cycle(team, str(args["cycle"]))
                if cycle is None:
                    raise ToolError(f'Error: Could not find cycle "{args["cycle"]}"')
                changes["cycle"] = cycle.id
        for key, change in (
            ("blocks", "blocks"),
            ("blockedBy", "blocked_by"),
            ("relatedTo", "related"),
            ("removeBlocks", "remove_blocks"),
            ("removeBlockedBy", "remove_blocked_by"),
            ("removeRelatedTo", "remove_related"),
        ):
            if args.get(key):
                changes[change] = self._issue_ids(args[key])
        if "assignee" in args:
            if args["assignee"] is None:
                changes["assignee"] = None
            else:
                user = s.find_user(args["assignee"], me)
                if user is None:
                    raise ToolError(f'Error: Could not find user "{args["assignee"]}"')
                changes["assignee"] = user.id
        if "delegate" in args:
            if args["delegate"] is None:
                changes["delegate"] = None
            else:
                agent = s.find_user(args["delegate"], me)
                if agent is None or not agent.is_app:
                    raise ToolError(f'Error: Could not find agent "{args["delegate"]}"')
                changes["delegate"] = agent.id
        for key, change in (("parentId", "parent"), ("duplicateOf", "duplicate_of")):
            if key not in args:
                continue
            if args[key] is None:
                changes[change] = None
                continue
            target = s.find_issue(args[key])
            if target is None:
                raise _api_error("Could not find referenced Issue.")
            changes[change] = target.id
        if args.get("id"):
            issue = s.find_issue(args["id"])
            if issue is None:
                raise ToolError(
                    f'Error: Could not find issue "{args["id"]}". To create a new issue, omit the "id" parameter. The "id" parameter should only be used to update an existing issue.'
                )
            s.update_issue(issue, me.id, **changes)
            return _json(self._issue_json(issue, kind="save"))
        if not args.get("team"):
            raise ToolError("Error: team is required when creating an issue")
        team = self._team_arg(args["team"])
        if not args.get("title"):
            raise ToolError("Error: title is required when creating an issue")
        issue = s.create_issue(
            team.id,
            me.id,
            args["title"],
            args.get("description") or "",
            priority=int(args.get("priority") or 0),
            state=args.get("state"),
            labels=list(args.get("labels") or []) + list(args.get("addLabels") or []),
            assignee=changes.get("assignee"),
        )
        rest = {
            k: v
            for k, v in changes.items()
            if k
            not in (
                "title",
                "description",
                "priority",
                "state",
                "labels",
                "addLabels",
                "removeLabels",
                "assignee",
            )
        }
        if rest:
            s.update_issue(issue, me.id, **rest)
        return _json(self._issue_json(issue, kind="save"))

    # -- comments ---------------------------------------------------------------------

    def _list_comments(self, me, args):
        if not args.get("issueId"):
            return _json({"comments": [], "hasNextPage": False})
        issue = self._issue(args["issueId"])
        comments = [c for c in self.state.thread(issue) if c.body]
        return _json(
            {
                "comments": [self._comment_json(c) for c in comments],
                "hasNextPage": False,
            }
        )

    def _save_comment(self, me, args):
        s = self.state
        if args.get("id"):
            comment = s.comments.get(args["id"])
            if comment is None:
                raise _api_error("Could not find referenced Comment.")
            if comment.user_id != me.id:
                raise _api_error("You can only edit your own comments.")
            return _json(self._comment_json(s.edit_comment(comment, args["body"])))
        if args.get("parentId"):
            parent = s.comments.get(args["parentId"])
            if parent is None:
                raise _api_error("Could not find referenced Comment.")
            return _json(
                self._comment_json(
                    s.add_comment(
                        parent.issue_id, me.id, args["body"], parent_id=parent.id
                    )
                )
            )
        parents = [
            k
            for k in (
                "issueId",
                "projectId",
                "initiativeId",
                "documentId",
                "milestoneId",
                "statusUpdateId",
            )
            if args.get(k)
        ]
        if len(parents) != 1:
            raise ToolError(
                "Error: Provide exactly one of issueId, projectId, initiativeId, documentId, milestoneId, or statusUpdateId"
            )
        if parents[0] != "issueId":
            raise ToolError(
                f'Error: Could not find {parents[0][:-2]} "{args[parents[0]]}"'
            )
        issue = self._issue(args["issueId"])
        return _json(self._comment_json(s.add_comment(issue.id, me.id, args["body"])))

    def _delete_comment(self, me, args):
        comment = self.state.comments.get(args["id"])
        if comment is None:
            raise _api_error("Could not find referenced Comment.")
        self.state.delete_comment(comment.id)
        return _json({"success": True})

    # -- attachments ---------------------------------------------------------------------

    def _file_url(self, att_id: str) -> str:
        return (
            f"{self.base}/linear/uploads/{att_id}?signature={secrets.token_urlsafe(12)}"
        )

    def _create_attachment(self, me, args):
        issue = self._issue(args["issue"])
        try:
            content = base64.b64decode(args["base64Content"], validate=True)
        except Exception:
            raise ToolError("Error: base64Content is not valid base64")
        if hashlib.sha256(content).hexdigest() != args["sha256"].lower():
            raise ToolError(
                "Error: SHA-256 checksum mismatch: the decoded content does not match sha256"
            )
        if args.get("size") and int(args["size"]) != len(content):
            raise ToolError(
                f"Error: Size mismatch: expected {args['size']} bytes, received {len(content)}"
            )
        return self._attach(
            issue,
            args.get("title") or args["filename"],
            content,
            args["contentType"],
            args.get("subtitle"),
        )

    def _attach(
        self,
        issue: Issue,
        title: str,
        content: bytes,
        content_type: str,
        subtitle: Optional[str],
    ) -> str:
        att = self.state.add_attachment(
            issue,
            None,
            title,
            content=content,
            content_type=content_type,
            subtitle=subtitle,
            url_for=self._file_url,
        )
        return _json({"id": att.id, "title": att.title, "url": att.url})

    def _prepare_attachment_upload(self, me, args):
        issue = self._issue(args["issue"])
        return self._prepare(args, issue)

    def _prepare_file_upload(self, me, args):
        return self._prepare(args, None)

    def _prepare(self, args, issue: Optional[Issue]) -> str:
        token = secrets.token_urlsafe(16)
        asset = f"{self.base}/linear/uploads/asset/{token}"
        self.uploads[token] = {
            "issue": issue.id if issue else None,
            "filename": args["filename"],
            "content_type": args["contentType"],
            "size": int(args["size"]),
            "title": args.get("title"),
            "subtitle": args.get("subtitle"),
            "content": None,
        }
        return _json(
            {
                "uploadUrl": f"{self.upload_url().rstrip('/')}/linear/uploads/put/{token}",
                "assetUrl": asset,
                "headers": {"Content-Type": args["contentType"]},
                "method": "PUT",
            }
        )

    def _create_attachment_from_upload(self, me, args):
        issue = self._issue(args["issue"])
        token = args["assetUrl"].rstrip("/").rsplit("/", 1)[-1]
        slot = self.uploads.get(token)
        if slot is None or slot["content"] is None:
            raise ToolError(
                "Error: The upload was not found or its bytes were not uploaded"
            )
        return self._attach(
            issue,
            args.get("title") or slot["title"] or slot["filename"],
            slot["content"],
            slot["content_type"],
            args.get("subtitle") or slot["subtitle"],
        )

    def _get_attachment(self, me, args):
        for issue in self.state.issues.values():
            for att in issue.attachments:
                if att.id == args["id"]:
                    out = {
                        "id": att.id,
                        "title": att.title,
                        "subtitle": att.subtitle,
                        "url": att.url,
                        "createdAt": iso(att.created_at),
                    }
                    if (
                        att.content is not None
                        and (args.get("format") or "") != "metadata"
                    ):
                        out["contentType"] = att.content_type
                        if (att.content_type or "").startswith("text/"):
                            out["content"] = att.content.decode(errors="replace")
                    return _json(out)
        raise _api_error("Could not find referenced Attachment.")

    def _delete_attachment(self, me, args):
        self.state.remove_attachment(args["id"])
        return _json({"success": True})

    def _extract_images(self, me, args):
        urls = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", args.get("markdown") or "")
        return _json({"images": [{"url": u} for u in urls]})

    # -- what the workplace has none of ------------------------------------------------------

    # -- projects -----------------------------------------------------------------------------

    def _project(self, ref: str) -> Project:
        project = self.state.find_project(ref)
        if project is None:
            raise ToolError(f'Error: Could not find project "{ref}"')
        return project

    def _team_ref(self, t) -> Dict[str, Any]:
        return {"id": t.id, "name": t.name, "key": t.key}

    def _project_json(
        self,
        p: Project,
        *,
        milestones: bool = False,
        members: bool = False,
        resources: bool = False,
    ) -> Dict[str, Any]:
        s = self.state
        name = next(
            (n for n, kind, _ in PROJECT_STATUSES if kind == p.status), p.status
        )
        lead = s.users.get(p.lead_id or "")
        out: Dict[str, Any] = {
            "id": p.identifier,
            "uuid": p.id,
            "icon": p.icon,
            "color": p.color,
            "name": p.name,
            "summary": p.summary,
            "description": p.description,
            "url": s.project_url(self.base, p),
            "createdAt": iso(p.created_at),
            "updatedAt": iso(p.updated_at),
            "startedAt": iso(p.started_at),
            "completedAt": iso(p.completed_at),
            "canceledAt": iso(p.canceled_at),
            "startDate": p.start_date,
            "startDateResolution": None,
            "targetDate": p.target_date,
            "targetDateResolution": None,
            "priority": {
                "value": p.priority,
                "name": PRIORITIES.get(p.priority, "No priority"),
            },
            "labels": [],
            "initiatives": [],
            "lead": {"id": lead.id, "name": lead.name} if lead else {},
            "leadTeam": (
                self._team_ref(s.teams[p.lead_team_id])
                if p.lead_team_id in s.teams
                else None
            ),
            "status": {"id": _status_id(p.status), "name": name, "type": p.status},
            "teams": [self._team_ref(s.teams[t]) for t in p.team_ids if t in s.teams],
        }
        if milestones:
            out["milestones"] = [self._milestone_json(m) for m in s.milestones_of(p)]
        if members:
            out["members"] = [
                {"id": u, "name": s.users[u].name} for u in p.member_ids if u in s.users
            ]
        if resources:
            out["documents"] = [
                {"id": d.id, "title": d.title, "url": s.document_url(self.base, d)}
                for d in s.documents.values()
                if d.project_id == p.id
            ]
            out["links"] = [
                {"id": a.id, "label": a.title, "url": a.url} for a in p.links
            ]
        return out

    def _list_projects(self, me, args):
        s = self.state
        items = [
            p
            for p in s.projects.values()
            if args.get("includeArchived") or p.archived_at is None
        ]
        if args.get("team"):
            team = self._team_arg(args["team"])
            items = [p for p in items if team.id in p.team_ids]
        if args.get("state"):
            ref = args["state"].lower()
            items = [
                p
                for p in items
                if p.status == ref
                or ref == _status_id(p.status)
                or any(
                    n.lower() == ref and kind == p.status
                    for n, kind, _ in PROJECT_STATUSES
                )
            ]
        if args.get("member"):
            user = s.find_user(args["member"], me)
            items = [p for p in items if user and user.id in p.member_ids]
        if args.get("query"):
            words = args["query"].lower().split()
            items = [
                p
                for p in items
                if all(
                    w in (p.name + " " + p.summary + " " + p.description).lower()
                    for w in words
                )
            ]
        key = "created_at" if args.get("orderBy") == "createdAt" else "updated_at"
        items.sort(key=lambda p: getattr(p, key), reverse=True)
        start = int(args["cursor"]) if str(args.get("cursor") or "").isdigit() else 0
        limit = int(args.get("limit") or 50)
        page = items[start : start + limit]
        out: Dict[str, Any] = {
            "projects": [
                self._fields(
                    self._project_json(
                        p,
                        milestones=bool(args.get("includeMilestones")),
                        members=bool(args.get("includeMembers")),
                    ),
                    args.get("fields"),
                )
                for p in page
            ],
            "hasNextPage": start + limit < len(items),
        }
        if out["hasNextPage"]:
            out["cursor"] = str(start + limit)
        return _json(out)

    def _get_project(self, me, args):
        project = self._project(args["query"])
        return _json(
            self._project_json(
                project,
                milestones=bool(args.get("includeMilestones")),
                members=bool(args.get("includeMembers")),
                resources=bool(args.get("includeResources")),
            )
        )

    def _save_project(self, me, args):
        s = self.state
        if args.get("template"):
            raise ToolError("Error: The workplace has no project templates")
        if (
            args.get("addInitiatives")
            or args.get("setInitiatives")
            or args.get("removeInitiatives")
        ):
            raise ToolError("Error: The workplace has no initiatives")
        if args.get("labels"):
            raise ToolError("Error: The workplace has no project labels")
        if args.get("setTeams") and (args.get("addTeams") or args.get("removeTeams")):
            raise ToolError(
                "Error: setTeams cannot be combined with addTeams or removeTeams"
            )
        teams = lambda refs: [self._team_arg(t).id for t in refs or []]  # noqa: E731
        changes: Dict[str, Any] = {}
        for key, change in (
            ("name", "name"),
            ("summary", "summary"),
            ("description", "description"),
            ("icon", "icon"),
            ("color", "color"),
            ("state", "status"),
            ("priority", "priority"),
            ("links", "links"),
        ):
            if key in args:
                changes[change] = args[key]
        for key, change in (("startDate", "start_date"), ("targetDate", "target_date")):
            if key in args:
                changes[change] = args[key]
        if "lead" in args:
            if args["lead"] is None:
                changes["lead"] = None
            else:
                user = s.find_user(args["lead"], me)
                if user is None:
                    raise ToolError(f'Error: Could not find user "{args["lead"]}"')
                changes["lead"] = user.id
        if args.get("leadTeam"):
            changes["lead_team"] = self._team_arg(args["leadTeam"]).id
        if args.get("id"):
            project = self._project(args["id"])
            if args.get("patch"):
                if "description" in args:
                    raise ToolError("Error: Provide description or patch, not both")
                changes["description"] = apply_patch(project.description, args["patch"])
            if args.get("setTeams"):
                changes["teams"] = teams(args["setTeams"])
            changes["add_teams"] = teams(args.get("addTeams"))
            changes["remove_teams"] = teams(args.get("removeTeams"))
            s.update_project(project, me.id, **changes)
            return _json(self._project_json(project))
        if not args.get("name"):
            raise ToolError("Error: name is required when creating a project")
        team_ids = teams(args.get("setTeams")) or teams(args.get("addTeams"))
        if not team_ids and not args.get("leadTeam"):
            raise ToolError(
                "Error: At least one team is required when creating a project (addTeams or setTeams)"
            )
        changes.pop("name", None)
        lead_team = changes.pop("lead_team", None)
        project = s.create_project(
            me.id, args["name"], team_ids, lead_team_id=lead_team, **changes
        )
        return _json(self._project_json(project))

    # -- milestones -----------------------------------------------------------------------------

    def _milestone_json(self, m: Milestone) -> Dict[str, Any]:
        s = self.state
        issues = [
            i
            for i in s.issues.values()
            if i.milestone_id == m.id
            and s.state(i).type not in ("canceled", "duplicate")
        ]
        done = sum(1 for i in issues if s.state(i).type == "completed")
        return {
            "id": m.id,
            "name": m.name,
            "description": m.description or None,
            "targetDate": m.target_date,
            "progress": done / len(issues) if issues else 0,
            "sortOrder": m.sort_order,
            "createdAt": iso(m.created_at),
            "updatedAt": iso(m.updated_at),
        }

    def _list_milestones(self, me, args):
        project = self._project(args["project"])
        return _json(
            [self._milestone_json(m) for m in self.state.milestones_of(project)]
        )

    def _get_milestone(self, me, args):
        project = self._project(args["project"])
        milestone = self.state.find_milestone(project, args["query"])
        if milestone is None:
            raise ToolError(f'Error: Could not find milestone "{args["query"]}"')
        out = self._milestone_json(milestone)
        out["issues"] = [
            {"id": i.identifier, "title": i.title, "status": self.state.state(i).name}
            for i in self.state.issues.values()
            if i.milestone_id == milestone.id
        ]
        return _json(out)

    def _save_milestone(self, me, args):
        project = self._project(args["project"])
        fields = {k: args[k] for k in ("name", "description") if k in args}
        if "targetDate" in args:
            fields["target_date"] = args["targetDate"]
        existing = None
        if args.get("id"):
            existing = self.state.find_milestone(project, args["id"])
            if existing is None:
                raise ToolError(f'Error: Could not find milestone "{args["id"]}"')
        elif not args.get("name"):
            raise ToolError("Error: name is required when creating a milestone")
        return _json(
            self._milestone_json(self.state.save_milestone(project, existing, **fields))
        )

    # -- cycles ---------------------------------------------------------------------------------

    def _cycle_json(self, c: Cycle) -> Dict[str, Any]:
        s = self.state
        issues = [i for i in s.issues.values() if i.cycle_id == c.id]
        done = sum(1 for i in issues if s.state(i).type == "completed")
        today = time.strftime("%Y-%m-%d", time.gmtime())
        return {
            "id": c.id,
            "number": c.number,
            "name": c.name,
            "description": c.description or None,
            "startsAt": f"{c.starts_at}T00:00:00.000Z",
            "endsAt": f"{c.ends_at}T00:00:00.000Z",
            "completedAt": f"{c.ends_at}T00:00:00.000Z" if c.ends_at <= today else None,
            "progress": done / len(issues) if issues else 0,
            "issueCountHistory": [len(issues)],
            "completedIssueCountHistory": [done],
            "scopeHistory": [len(issues)],
            "completedScopeHistory": [done],
        }

    def _list_cycles(self, me, args):
        team = self.state.teams.get(args["teamId"])
        if team is None:
            raise _api_error("teamId must be a UUID.")
        cycles = self.state.cycles_of(team)
        if args.get("type"):
            current = self.state.current_cycle(team)
            today = time.strftime("%Y-%m-%d", time.gmtime())
            if args["type"] == "current":
                cycles = [current] if current else []
            elif args["type"] == "previous":
                past = [c for c in cycles if c.ends_at <= today]
                cycles = past[-1:]
            else:
                coming = [c for c in cycles if c.starts_at > today]
                cycles = coming[:1]
        return _json([self._cycle_json(c) for c in cycles])

    # -- documents ------------------------------------------------------------------------------

    def _document_json(self, d: Document, content: bool = True) -> Dict[str, Any]:
        s = self.state
        creator = s.users.get(d.creator_id)
        out = {
            "id": d.id,
            "title": d.title,
            "url": s.document_url(self.base, d),
            "slugId": d.slug_id,
            "icon": d.icon,
            "color": d.color,
            "createdAt": iso(d.created_at),
            "updatedAt": iso(d.updated_at),
            "creator": {"id": d.creator_id, "name": creator.name} if creator else None,
        }
        if content:
            out["content"] = d.content
        if d.project_id and d.project_id in s.projects:
            out["project"] = {
                "id": s.projects[d.project_id].identifier,
                "name": s.projects[d.project_id].name,
            }
        if d.issue_id and d.issue_id in s.issues:
            out["issue"] = {
                "id": s.issues[d.issue_id].identifier,
                "title": s.issues[d.issue_id].title,
            }
        if d.team_id and d.team_id in s.teams:
            out["team"] = self._team_ref(s.teams[d.team_id])
        if d.cycle_id and d.cycle_id in s.cycles:
            out["cycle"] = {"id": d.cycle_id, "number": s.cycles[d.cycle_id].number}
        return out

    def _list_documents(self, me, args):
        s = self.state
        items = list(s.documents.values())
        if args.get("projectId"):
            project = self._project(args["projectId"])
            items = [d for d in items if d.project_id == project.id]
        if args.get("teamId"):
            team = self._team_arg(args["teamId"])
            items = [d for d in items if d.team_id == team.id]
        if args.get("creatorId"):
            user = s.find_user(args["creatorId"], me)
            items = [d for d in items if user and d.creator_id == user.id]
        if args.get("query"):
            words = args["query"].lower().split()
            items = [
                d
                for d in items
                if all(w in (d.title + " " + d.content).lower() for w in words)
            ]
        key = "created_at" if args.get("orderBy") == "createdAt" else "updated_at"
        items.sort(key=lambda d: getattr(d, key), reverse=True)
        start = int(args["cursor"]) if str(args.get("cursor") or "").isdigit() else 0
        limit = int(args.get("limit") or 50)
        page = items[start : start + limit]
        out: Dict[str, Any] = {
            "documents": [
                self._fields(self._document_json(d, content=False), args.get("fields"))
                for d in page
            ],
            "hasNextPage": start + limit < len(items),
        }
        if out["hasNextPage"]:
            out["cursor"] = str(start + limit)
        return _json(out)

    def _get_document(self, me, args):
        doc = self.state.find_document(args["id"])
        if doc is None:
            raise _api_error("Could not find referenced Document.")
        return _json(self._document_json(doc))

    def _save_document(self, me, args):
        s = self.state
        if args.get("initiative"):
            raise ToolError("Error: The workplace has no initiatives")
        fields: Dict[str, Any] = {
            k: args[k] for k in ("title", "content", "icon", "color") if k in args
        }
        if args.get("project"):
            fields["project_id"] = self._project(args["project"]).id
        if args.get("issue"):
            fields["issue_id"] = self._issue(args["issue"]).id
        if args.get("cycle"):
            team = s.find_team(args["team"]) if args.get("team") else None
            cycle = s.find_cycle(team, str(args["cycle"]))
            if cycle is None:
                raise ToolError(f'Error: Could not find cycle "{args["cycle"]}"')
            fields["cycle_id"] = cycle.id
        elif args.get("team"):
            fields["team_id"] = self._team_arg(args["team"]).id
        if args.get("id"):
            doc = s.find_document(args["id"])
            if doc is None:
                raise _api_error("Could not find referenced Document.")
            if args.get("patch"):
                if "content" in args:
                    raise ToolError("Error: Provide content or patch, not both")
                fields["content"] = apply_patch(doc.content, args["patch"])
            return _json(self._document_json(s.save_document(me.id, doc, **fields)))
        if not args.get("title"):
            raise ToolError("Error: title is required when creating a document")
        if (
            sum(
                1
                for k in ("project_id", "issue_id", "cycle_id", "team_id")
                if k in fields
            )
            != 1
        ):
            raise ToolError(
                "Error: Provide exactly one of project, issue, initiative, cycle, or team"
            )
        return _json(self._document_json(s.save_document(me.id, None, **fields)))

    # -- notifications ----------------------------------------------------------------------------

    def _notification_json(self, n: Notification) -> Dict[str, Any]:
        s = self.state
        actor = s.users.get(n.actor_id or "")
        out: Dict[str, Any] = {
            "id": n.id,
            "type": n.type,
            "title": n.text,
            "createdAt": iso(n.created_at),
            "readAt": iso(n.read_at),
            "snoozedUntilAt": iso(n.snoozed_until),
            "actor": {"id": actor.id, "name": actor.name} if actor else None,
        }
        issue = s.issues.get(n.issue_id or "")
        if issue is not None:
            out["issue"] = {
                "id": issue.identifier,
                "title": issue.title,
                "url": s.issue_url(self.base, issue),
            }
        if n.comment_id and n.comment_id in s.comments:
            out["comment"] = {"id": n.comment_id, "body": s.comments[n.comment_id].body}
        project = s.projects.get(n.project_id or "")
        if project is not None:
            out["project"] = {"id": project.identifier, "name": project.name}
        return out

    def _get_notifications(self, me, args):
        items = self.state.inbox(me.id, unread_only=bool(args.get("unreadOnly")))
        start = int(args["cursor"]) if str(args.get("cursor") or "").isdigit() else 0
        limit = int(args.get("limit") or 50)
        out: Dict[str, Any] = {
            "notifications": [
                self._notification_json(n) for n in items[start : start + limit]
            ],
            "hasNextPage": start + limit < len(items),
        }
        if out["hasNextPage"]:
            out["cursor"] = str(start + limit)
        return _json(out)

    def _mark_notification(self, me, args):
        note = self.state.notifications.get(args["id"])
        if note is None or note.user_id != me.id:
            raise _api_error("Could not find referenced Notification.")
        snooze: Any = ...
        if "snoozedUntilAt" in args:
            from datetime import datetime

            snooze = (
                datetime.fromisoformat(
                    args["snoozedUntilAt"].replace("Z", "+00:00")
                ).timestamp()
                if args["snoozedUntilAt"]
                else None
            )
        self.state.mark_notification(note, read=args.get("read"), snoozed_until=snooze)
        return _json(self._notification_json(note))

    # -- project updates --------------------------------------------------------------------------

    def _status_update_json(self, u: StatusUpdate) -> Dict[str, Any]:
        s = self.state
        author = s.users.get(u.user_id)
        project = s.projects.get(u.project_id)
        return {
            "id": u.id,
            "type": "project",
            "body": u.body,
            "health": u.health,
            "createdAt": iso(u.created_at),
            "updatedAt": iso(u.updated_at),
            "user": {"id": u.user_id, "name": author.name if author else None},
            "project": (
                {"id": project.identifier, "name": project.name} if project else None
            ),
        }

    def _get_status_updates(self, me, args):
        s = self.state
        if args.get("type") == "initiative" or args.get("initiative"):
            return _json({"statusUpdates": [], "hasNextPage": False})
        if args.get("id"):
            update = s.status_updates.get(args["id"])
            if update is None:
                raise _api_error("Could not find referenced ProjectUpdate.")
            return _json(self._status_update_json(update))
        items = list(s.status_updates.values())
        if args.get("project"):
            project = self._project(args["project"])
            items = [u for u in items if u.project_id == project.id]
        if args.get("user"):
            user = s.find_user(args["user"], me)
            items = [u for u in items if user and u.user_id == user.id]
        key = "created_at" if args.get("orderBy") != "updatedAt" else "updated_at"
        items.sort(key=lambda u: getattr(u, key), reverse=True)
        start = int(args["cursor"]) if str(args.get("cursor") or "").isdigit() else 0
        limit = int(args.get("limit") or 50)
        out: Dict[str, Any] = {
            "statusUpdates": [
                self._status_update_json(u) for u in items[start : start + limit]
            ],
            "hasNextPage": start + limit < len(items),
        }
        if out["hasNextPage"]:
            out["cursor"] = str(start + limit)
        return _json(out)

    def _save_status_update(self, me, args):
        if args["type"] != "project":
            raise ToolError("Error: The workplace has no initiatives")
        if args.get("id"):
            update = self.state.status_updates.get(args["id"])
            if update is None:
                raise _api_error("Could not find referenced ProjectUpdate.")
            self.state.save_status_update(
                me.id, None, update, body=args.get("body"), health=args.get("health")
            )
            return _json(self._status_update_json(update))
        if not args.get("project") or not args.get("body"):
            raise ToolError(
                "Error: body and project are required when creating a project update"
            )
        update = self.state.save_status_update(
            me.id,
            self._project(args["project"]),
            body=args["body"],
            health=args.get("health"),
        )
        return _json(self._status_update_json(update))

    def _delete_status_update(self, me, args):
        update = self.state.status_updates.get(args["id"])
        if update is None or args.get("type") != "project":
            raise _api_error("Could not find referenced ProjectUpdate.")
        self.state.delete_status_update(update)
        return _json({"success": True})

    def _list_custom_views(self, me, args):
        return _json({"customViews": [], "hasNextPage": False})

    def _list_templates(self, me, args):
        return _json([])

    def _get_template(self, me, args):
        raise _api_error("Could not find referenced Template.")

    def _list_agent_skills(self, me, args):
        return _json({"skills": [], "hasNextPage": False})

    def _get_agent_skill(self, me, args):
        raise _api_error("Could not find referenced AgentSkill.")

    def _list_release_pipelines(self, me, args):
        return _json({"releasePipelines": [], "hasNextPage": False})

    def _list_releases(self, me, args):
        return _json({"releases": [], "hasNextPage": False})

    def _get_release(self, me, args):
        raise _api_error("Could not find referenced Release.")

    def _list_release_notes(self, me, args):
        return _json({"releaseNotes": [], "hasNextPage": False})

    def _get_release_note(self, me, args):
        raise _api_error("Could not find referenced ReleaseNote.")


def _display(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text
