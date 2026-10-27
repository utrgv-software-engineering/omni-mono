"""Linear's API beside its MCP server, and the UI's Linear.

- ``POST /linear/graphql``: the part of Linear's GraphQL API an agent
  reports through: ``agentActivityCreate`` (a thought, action, elicitation,
  response or error in its session) and ``agentSessionUpdate`` (external
  URLs). Authorized as Linear's API is (``Authorization: <api key>`` or
  ``Bearer <oauth token>``).
- ``/linear/uploads/...``: where the MCP server's upload URLs point.
- ``/workplace/api/linear/...``: what the people in the workplace see and do
  in Linear: issues (every field, sub-issues and relations, attachments,
  archiving), comments, agent sessions (replying, stopping), teams (their
  settings, statuses, labels, members and cycles), projects (milestones,
  updates, documents) and each person's inbox.
"""

from __future__ import annotations

import re
import hmac
from urllib.parse import parse_qs, urlsplit
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..accounts import me
from ..wire import Wire
from .mcp import LinearMCP
from .state import PRIORITIES, PROJECT_STATUSES, LinearError, LinearState, User, iso

__all__ = ["linear_router"]


class NewIssue(BaseModel):
    team: str
    title: str
    description: str = ""
    priority: int = 0
    labels: List[str] = []


class EditIssue(BaseModel):
    """What to change; a field sent as ``null`` clears it (``due_date``, ``estimate``,
    ``parent``, ``project``, ``milestone``, ``cycle``, ``duplicate_of``)."""

    title: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    labels: Optional[List[str]] = None
    assignee: Optional[str] = None
    unassign: bool = False
    delegate: Optional[str] = None
    undelegate: bool = False
    priority: Optional[int] = None
    due_date: Optional[str] = None
    estimate: Optional[float] = None
    parent: Optional[str] = None
    project: Optional[str] = None
    milestone: Optional[str] = None
    cycle: Optional[str] = None
    duplicate_of: Optional[str] = None
    blocks: List[str] = []
    blocked_by: List[str] = []
    related: List[str] = []
    remove_blocks: List[str] = []
    remove_blocked_by: List[str] = []
    remove_related: List[str] = []
    archived: Optional[bool] = None
    subscribed: Optional[bool] = None


class EditComment(BaseModel):
    body: str


class NewLink(BaseModel):
    title: str
    url: str


class NewLabel(BaseModel):
    name: str
    color: str = "#bec2c8"
    description: Optional[str] = None


class EditLabel(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    retired: Optional[bool] = None


class EditTeam(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class NewState(BaseModel):
    name: str
    type: str
    color: str = "#bec2c8"


class EditState(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    position: Optional[int] = None


class Member(BaseModel):
    user: str


class NewCycle(BaseModel):
    starts_at: str
    ends_at: str
    name: Optional[str] = None
    description: str = ""


class EditCycle(BaseModel):
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None


class NewProject(BaseModel):
    name: str
    teams: List[str]
    summary: str = ""
    description: str = ""
    status: str = "backlog"
    priority: int = 0
    lead: Optional[str] = None
    start_date: Optional[str] = None
    target_date: Optional[str] = None


class EditProject(BaseModel):
    name: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    lead: Optional[str] = None
    start_date: Optional[str] = None
    target_date: Optional[str] = None
    teams: Optional[List[str]] = None
    archived: Optional[bool] = None
    add_members: List[str] = []
    remove_members: List[str] = []
    links: List[NewLink] = []


class SaveMilestone(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target_date: Optional[str] = None


class NewUpdate(BaseModel):
    body: str
    health: str = "onTrack"


class SaveDocument(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    project: Optional[str] = None
    issue: Optional[str] = None
    team: Optional[str] = None


class MarkNotification(BaseModel):
    read: Optional[bool] = None
    #: Seconds since the epoch; ``null`` unsnoozes.
    snoozed_until: Optional[float] = None


class NewTeam(BaseModel):
    key: str
    name: str
    description: str = ""


class NewComment(BaseModel):
    body: str
    parent_id: Optional[str] = None


def _gql_error(
    message: str, code: str = "INVALID_INPUT", status: int = 200
) -> JSONResponse:
    return JSONResponse(
        {"errors": [{"message": message, "extensions": {"code": code}}], "data": None},
        status_code=status,
    )


def linear_router(
    state: LinearState, mcp: LinearMCP, wire: Wire, base_url, reader=None
) -> APIRouter:
    """``reader(request)`` is the Linear user fetching an uploaded file (a signed-in
    person), else ``None``: a file is for people in the workplace and tokens."""
    r = APIRouter()

    def caller(request: Request) -> Optional[User]:
        auth = request.headers.get("authorization", "").strip()
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth
        return state.user_for_token(token) if token else None

    # -- the agent API ------------------------------------------------------------

    @r.post("/linear/graphql")
    async def graphql(request: Request):
        me = caller(request)
        started = time.monotonic()
        body = await request.json()
        query = str(body.get("query") or "")
        variables = body.get("variables") or {}
        operation = next(
            (
                op
                for op in ("agentActivityCreate", "agentSessionUpdate")
                if re.search(rf"\b{op}\s*\(", query)
            ),
            None,
        )
        entry = wire.record(
            service="linear",
            direction="in",
            kind="api",
            title=operation or "graphql",
            actor=me.name if me else None,
            status="pending",
            request=variables or {"query": query[:300]},
        )

        def done(response: JSONResponse, ok: bool) -> JSONResponse:
            wire.update(
                entry,
                status="ok" if ok else "error",
                response=response.body.decode()[:1000],
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return response

        if me is None:
            return done(
                _gql_error(
                    "Authentication required, not authenticated",
                    "AUTHENTICATION_ERROR",
                    401,
                ),
                False,
            )
        if operation is None:
            return done(
                _gql_error(
                    "The workplace's Linear API answers agentActivityCreate and agentSessionUpdate; agents use the MCP server for the rest"
                ),
                False,
            )
        if operation == "agentActivityCreate":
            data = variables.get("input") or {}
            session = state.sessions.get(str(data.get("agentSessionId") or ""))
            if session is None or session.app_user_id != me.id:
                return done(
                    _gql_error("Entity not found: AgentSession", "ENTITY_NOT_FOUND"),
                    False,
                )
            try:
                activity = state.add_activity(
                    session,
                    me.id,
                    dict(data.get("content") or {}),
                    signal=data.get("signal"),
                    ephemeral=bool(data.get("ephemeral")),
                )
            except LinearError as exc:
                return done(_gql_error(exc.message), False)
            return done(
                JSONResponse(
                    {
                        "data": {
                            "agentActivityCreate": {
                                "success": True,
                                "agentActivity": {"id": activity.id},
                                "lastSyncId": time.time(),
                            }
                        }
                    }
                ),
                True,
            )
        session_id = str(variables.get("agentSessionId") or variables.get("id") or "")
        session = state.sessions.get(session_id)
        if session is None or session.app_user_id != me.id:
            return done(
                _gql_error("Entity not found: AgentSession", "ENTITY_NOT_FOUND"), False
            )
        data = variables.get("data") or variables.get("input") or {}
        if data.get("externalUrls") is not None:
            session.external_urls = list(data["externalUrls"])
        session.external_urls += list(data.get("addedExternalUrls") or [])
        removed = set(data.get("removedExternalUrls") or [])
        session.external_urls = [
            u for u in session.external_urls if u.get("url") not in removed
        ]
        if data.get("externalLink"):
            session.external_urls.append({"label": "Link", "url": data["externalLink"]})
        session.updated_at = time.time()
        state._emit("session_updated", session=session)
        return done(
            JSONResponse({"data": {"agentSessionUpdate": {"success": True}}}), True
        )

    # -- uploads -------------------------------------------------------------------------

    @r.put("/linear/uploads/put/{token}")
    async def upload(token: str, request: Request):
        slot = mcp.uploads.get(token)
        if slot is None:
            return Response(status_code=403)
        content = await request.body()
        if len(content) != slot["size"]:
            return Response(
                "Content length does not match the prepared size", status_code=400
            )
        slot["content"] = content
        wire.record(
            service="linear",
            direction="in",
            kind="api",
            title="file upload",
            actor=slot["filename"],
            request={"bytes": len(content)},
            response={"status": 200},
        )
        return Response(status_code=200)

    @r.get("/linear/uploads/{attachment_id}")
    def uploaded(attachment_id: str, request: Request):
        att = state.attachment(attachment_id)
        if att is None or att.content is None:
            return Response(status_code=404)
        # Its signed URL opens it, as Linear's do; otherwise a token or a signed-in person.
        signature = request.query_params.get("signature") or ""
        expected = parse_qs(urlsplit(att.url or "").query).get("signature", [""])[0]
        signed = bool(signature and expected) and hmac.compare_digest(
            signature.encode(), expected.encode()
        )
        if (
            not signed
            and caller(request) is None
            and (reader is None or reader(request) is None)
        ):
            return Response(status_code=404)
        return Response(
            att.content,
            media_type=att.content_type or "application/octet-stream",
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @r.get("/linear/uploads/asset/{token}")
    def asset(token: str):
        slot = mcp.uploads.get(token)
        if slot is None or slot["content"] is None:
            return Response(status_code=404)
        return Response(
            slot["content"],
            media_type=slot["content_type"],
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    # -- the UI's Linear ------------------------------------------------------------------

    def signed_in(request: Request) -> User:
        user = state.users.get(me(request).linear_id)
        if user is None:
            raise HTTPException(404, "You have no Linear user")
        return user

    def person(actor: str) -> User:
        user = state.find_user(actor)
        if user is None:
            raise HTTPException(404, f"No Linear user {actor}")
        return user

    def issue_of(identifier: str):
        issue = state.find_issue(identifier)
        if issue is None:
            raise HTTPException(404, "Issue not found")
        return issue

    def summary(issue) -> Dict[str, Any]:
        st = state.state(issue)
        assignee = state.users.get(issue.assignee_id or "")
        delegate = state.users.get(issue.delegate_id or "")
        project = state.projects.get(issue.project_id or "")
        cycle = state.cycles.get(issue.cycle_id or "")
        milestone = state.milestones.get(issue.milestone_id or "")
        parent = state.issues.get(issue.parent_id or "")
        return {
            "id": issue.id,
            "identifier": issue.identifier,
            "title": issue.title,
            "priority": issue.priority,
            "priority_name": PRIORITIES.get(issue.priority),
            "state": st.name,
            "state_type": st.type,
            "state_color": st.color,
            "labels": [
                state.labels[i].name for i in issue.label_ids if i in state.labels
            ],
            "assignee": assignee.display_name if assignee else None,
            "assignee_name": assignee.name if assignee else None,
            "delegate": delegate.display_name if delegate else None,
            "creator": (
                state.users[issue.creator_id].display_name
                if issue.creator_id in state.users
                else None
            ),
            "team": state.teams[issue.team_id].key,
            "updated_at": iso(issue.updated_at),
            "created_at": iso(issue.created_at),
            "comments": sum(
                1 for c in state.comments.values() if c.issue_id == issue.id and c.body
            ),
            "url": state.issue_url(base_url().rstrip("/"), issue),
            "due_date": issue.due_date,
            "estimate": issue.estimate,
            "project": {"id": project.id, "name": project.name} if project else None,
            "milestone": (
                {"id": milestone.id, "name": milestone.name} if milestone else None
            ),
            "cycle": (
                {"id": cycle.id, "number": cycle.number, "name": cycle.name}
                if cycle
                else None
            ),
            "parent": (
                {"identifier": parent.identifier, "title": parent.title}
                if parent
                else None
            ),
            "sub_issues": sum(
                1 for i in state.issues.values() if i.parent_id == issue.id
            ),
            "archived": issue.archived_at is not None,
        }

    def _refused(exc: LinearError):
        raise HTTPException(422, exc.message)

    def _try(call):
        try:
            return call()
        except LinearError as exc:
            _refused(exc)

    def team_of(key: str):
        team = state.find_team(key)
        if team is None:
            raise HTTPException(404, "No such team")
        return team

    def project_of(ref: str):
        project = state.find_project(ref)
        if project is None:
            raise HTTPException(404, "No such project")
        return project

    def user_json(u: User) -> Dict[str, Any]:
        return {
            "id": u.id,
            "name": u.name,
            "display_name": u.display_name,
            "is_app": u.is_app,
            "slack": u.slack,
            "email": u.email,
        }

    def team_json(t, viewer: User) -> Dict[str, Any]:
        current = state.current_cycle(t)
        return {
            "id": t.id,
            "key": t.key,
            "name": t.name,
            "description": t.description,
            "member": viewer.id in t.member_ids,
            "members": list(t.member_ids),
            "states": [
                {
                    "id": s.id,
                    "name": s.name,
                    "type": s.type,
                    "color": s.color,
                    "position": s.position,
                }
                for s in sorted(t.states, key=lambda s: s.position)
            ],
            "labels": [
                {
                    "id": l.id,
                    "name": l.name,
                    "color": l.color,
                    "description": l.description,
                    "retired": l.retired_at is not None,
                    "team": l.team_id is not None,
                    "issues": sum(
                        1 for i in state.issues.values() if l.id in i.label_ids
                    ),
                }
                for l in state.labels.values()
                if l.team_id in (None, t.id)
            ],
            "cycles": [
                {
                    "id": c.id,
                    "number": c.number,
                    "name": c.name,
                    "starts_at": c.starts_at,
                    "ends_at": c.ends_at,
                    "current": current is not None and c.id == current.id,
                }
                for c in state.cycles_of(t)
            ],
        }

    def project_summary(p) -> Dict[str, Any]:
        issues = state.project_issues(p)
        lead = state.users.get(p.lead_id or "")
        return {
            "id": p.id,
            "identifier": p.identifier,
            "name": p.name,
            "summary": p.summary,
            "status": p.status,
            "status_name": next(
                (n for n, kind, _ in PROJECT_STATUSES if kind == p.status), p.status
            ),
            "priority": p.priority,
            "lead": lead.name if lead else None,
            "lead_id": p.lead_id,
            "teams": [state.teams[t].key for t in p.team_ids if t in state.teams],
            "start_date": p.start_date,
            "target_date": p.target_date,
            "color": p.color,
            "progress": state.project_progress(p),
            "issues": len(issues),
            "archived": p.archived_at is not None,
            "updated_at": iso(p.updated_at),
        }

    @r.get("/workplace/api/linear/workspace")
    def workspace(request: Request):
        viewer = signed_in(request)
        return {
            "organization": {"name": state.org_name, "url_key": state.url_key},
            "users": [user_json(u) for u in state.users.values() if u.active],
            "teams": [team_json(t, viewer) for t in state.teams.values()],
            "projects": [
                project_summary(p)
                for p in state.projects.values()
                if p.archived_at is None
            ],
            "project_statuses": [
                {"name": n, "type": kind, "color": color}
                for n, kind, color in PROJECT_STATUSES
            ],
            "unread": sum(1 for n in state.inbox(viewer.id) if not n.read_at),
        }

    @r.post("/workplace/api/linear/teams")
    def create_team(body: NewTeam, request: Request):
        viewer = signed_in(request)
        team = _try(
            lambda: state.create_team(
                body.key, body.name, body.description, creator_id=viewer.id
            )
        )
        return {"id": team.id, "key": team.key, "name": team.name}

    @r.patch("/workplace/api/linear/teams/{key}")
    def edit_team(key: str, body: EditTeam, request: Request):
        team = team_of(key)
        _try(
            lambda: state.update_team(
                team, name=body.name, description=body.description
            )
        )
        return team_json(team, signed_in(request))

    @r.post("/workplace/api/linear/teams/{key}/states")
    def add_state(key: str, body: NewState, request: Request):
        signed_in(request)
        team = team_of(key)
        state_ = _try(lambda: state.add_state(team, body.name, body.type, body.color))
        return {"id": state_.id}

    @r.patch("/workplace/api/linear/teams/{key}/states/{state_id}")
    def edit_state(key: str, state_id: str, body: EditState, request: Request):
        signed_in(request)
        team = team_of(key)
        _try(
            lambda: state.update_state(
                team, state_id, name=body.name, color=body.color, position=body.position
            )
        )
        return {"ok": True}

    @r.delete("/workplace/api/linear/teams/{key}/states/{state_id}")
    def delete_state(
        key: str, state_id: str, request: Request, move_to: Optional[str] = None
    ):
        signed_in(request)
        team = team_of(key)
        _try(lambda: state.delete_state(team, state_id, move_to))
        return {"ok": True}

    @r.post("/workplace/api/linear/teams/{key}/members")
    def add_member(key: str, body: Member, request: Request):
        signed_in(request)
        team = team_of(key)
        _try(lambda: state.set_team_member(team, person(body.user).id, True))
        return {"ok": True}

    @r.delete("/workplace/api/linear/teams/{key}/members/{user_id}")
    def remove_member(key: str, user_id: str, request: Request):
        signed_in(request)
        team = team_of(key)
        _try(lambda: state.set_team_member(team, person(user_id).id, False))
        return {"ok": True}

    @r.post("/workplace/api/linear/teams/{key}/labels")
    def add_label(key: str, body: NewLabel, request: Request):
        signed_in(request)
        team = team_of(key)
        if state.find_label(body.name, team.id) is not None:
            raise HTTPException(422, "Duplicate label name")
        if not body.name.strip():
            raise HTTPException(422, "A label has a name")
        label = state.create_label(
            body.name.strip(), body.color, body.description, team.id
        )
        return {"id": label.id}

    @r.patch("/workplace/api/linear/labels/{label_id}")
    def edit_label(label_id: str, body: EditLabel, request: Request):
        signed_in(request)
        label = state.labels.get(label_id)
        if label is None:
            raise HTTPException(404, "No such label")
        fields = {
            k: v
            for k, v in body.model_dump(exclude_unset=True).items()
            if k != "retired"
        }
        if fields:
            _try(lambda: state.update_label(label, **fields))
        if body.retired is not None:
            state.set_label_retired(label, body.retired)
        return {"ok": True}

    @r.delete("/workplace/api/linear/labels/{label_id}")
    def delete_label(label_id: str, request: Request):
        signed_in(request)
        label = state.labels.get(label_id)
        if label is None:
            raise HTTPException(404, "No such label")
        state.delete_label(label)
        return {"ok": True}

    @r.post("/workplace/api/linear/teams/{key}/cycles")
    def add_cycle(key: str, body: NewCycle, request: Request):
        signed_in(request)
        team = team_of(key)
        cycle = _try(
            lambda: state.create_cycle(
                team,
                body.starts_at,
                body.ends_at,
                name=body.name,
                description=body.description,
            )
        )
        return {"id": cycle.id, "number": cycle.number}

    @r.patch("/workplace/api/linear/cycles/{cycle_id}")
    def edit_cycle(cycle_id: str, body: EditCycle, request: Request):
        signed_in(request)
        cycle = state.cycles.get(cycle_id)
        if cycle is None:
            raise HTTPException(404, "No such cycle")
        _try(lambda: state.update_cycle(cycle, **body.model_dump(exclude_unset=True)))
        return {"ok": True}

    @r.delete("/workplace/api/linear/cycles/{cycle_id}")
    def delete_cycle(cycle_id: str, request: Request):
        signed_in(request)
        cycle = state.cycles.get(cycle_id)
        if cycle is None:
            raise HTTPException(404, "No such cycle")
        state.delete_cycle(cycle)
        return {"ok": True}

    @r.get("/workplace/api/linear/issues")
    def issues(
        request: Request,
        team: Optional[str] = None,
        project: Optional[str] = None,
        cycle: Optional[str] = None,
        archived: bool = False,
    ):
        signed_in(request)
        items = [
            i for i in state.issues.values() if (i.archived_at is not None) == archived
        ]
        if team is not None:
            items = [i for i in items if state.teams[i.team_id].key == team]
        if project is not None:
            target = project_of(project)
            items = [i for i in items if i.project_id == target.id]
        if cycle is not None:
            items = [i for i in items if i.cycle_id == cycle]
        items.sort(key=lambda i: i.updated_at, reverse=True)
        return {"issues": [summary(i) for i in items]}

    @r.get("/workplace/api/linear/issues/{identifier}")
    def issue(identifier: str, request: Request):
        viewer = signed_in(request)
        it = issue_of(identifier)
        creator = state.users.get(it.creator_id)
        comments = []
        for c in state.thread(it):
            author = state.users.get(c.user_id)
            comments.append(
                {
                    "id": c.id,
                    "body": c.body,
                    "parent_id": c.parent_id,
                    "at": iso(c.created_at),
                    "edited": c.updated_at - c.created_at > 1,
                    "user_id": c.user_id,
                    "author": author.display_name if author else None,
                    "author_name": author.name if author else None,
                    "is_app": bool(author and author.is_app),
                    "session": c.agent_session_id,
                }
            )
        sessions = []
        for sess in state.sessions.values():
            if sess.issue_id != it.id:
                continue
            app = state.users.get(sess.app_user_id)
            sessions.append(
                {
                    "id": sess.id,
                    "app": app.name if app else None,
                    "status": sess.status,
                    "comment_id": sess.comment_id,
                    "created_at": iso(sess.created_at),
                    "external_urls": sess.external_urls,
                    "stopped": any(a.signal == "stop" for a in sess.activities),
                    "activities": [
                        {
                            "id": a.id,
                            "type": a.type,
                            "content": a.content,
                            "at": iso(a.created_at),
                            "user": (
                                state.users[a.user_id].name
                                if a.user_id in state.users
                                else None
                            ),
                            "ephemeral": a.ephemeral,
                            "signal": a.signal,
                        }
                        for a in sess.activities
                    ],
                }
            )
        ref = lambda i: (
            {
                "identifier": state.issues[i].identifier,
                "title": state.issues[i].title,  # noqa: E731
                "state_type": state.state(state.issues[i]).type,
            }
            if i in state.issues
            else None
        )
        return {
            **summary(it),
            "description": it.description,
            "creator": creator.display_name if creator else None,
            "created_at": iso(it.created_at),
            "attachments": [
                {
                    "id": a.id,
                    "title": a.title,
                    "url": a.url,
                    "content_type": a.content_type,
                    "subtitle": a.subtitle,
                    "file": a.content is not None,
                }
                for a in it.attachments
            ],
            "sub_issues": [summary(i) for i in state.sub_issues(it)],
            "relations": {
                "blocks": [x for x in map(ref, it.blocks) if x],
                "blocked_by": [
                    ref(x.id) for x in state.issues.values() if it.id in x.blocks
                ],
                "related": [x for x in map(ref, it.related) if x]
                + [
                    ref(x.id)
                    for x in state.issues.values()
                    if it.id in x.related and x.id not in it.related
                ],
                "duplicate_of": ref(it.duplicate_of) if it.duplicate_of else None,
                "duplicates": [
                    ref(x.id) for x in state.issues.values() if x.duplicate_of == it.id
                ],
            },
            "subscribed": viewer.id in it.subscriber_ids,
            "documents": [
                {"id": d.id, "title": d.title}
                for d in state.documents.values()
                if d.issue_id == it.id
            ],
            "comments": comments,
            "sessions": sessions,
        }

    @r.post("/workplace/api/linear/issues")
    def create(body: NewIssue, request: Request):
        team = state.find_team(body.team)
        if team is None:
            raise HTTPException(404, "No such team")
        created = _try(
            lambda: state.create_issue(
                team.id,
                signed_in(request).id,
                body.title,
                body.description,
                priority=body.priority,
                labels=body.labels,
            )
        )
        return summary(created)

    def _issue_ref(identifier: Optional[str]) -> Optional[str]:
        if identifier is None:
            return None
        return issue_of(identifier).id

    @r.patch("/workplace/api/linear/issues/{identifier}")
    def edit(identifier: str, body: EditIssue, request: Request):
        it = issue_of(identifier)
        actor = signed_in(request)
        sent = body.model_fields_set
        changes: Dict[str, Any] = {}
        for key in ("title", "description", "labels", "priority"):
            if getattr(body, key) is not None:
                changes[key] = getattr(body, key)
        if body.state:
            changes["state"] = body.state
        if body.assignee:
            changes["assignee"] = person(body.assignee).id
        if body.unassign:
            changes["assignee"] = None
        if body.delegate:
            agent = person(body.delegate)
            if not agent.is_app:
                raise HTTPException(422, f"{agent.name} is not an agent")
            changes["delegate"] = agent.id
        if body.undelegate:
            changes["delegate"] = None
        for key in ("due_date", "estimate", "archived"):
            if key in sent:
                changes[key] = getattr(body, key)
        if "parent" in sent:
            changes["parent"] = _issue_ref(body.parent)
        if "duplicate_of" in sent:
            changes["duplicate_of"] = _issue_ref(body.duplicate_of)
        if "project" in sent:
            changes["project"] = project_of(body.project).id if body.project else None
        for key in ("milestone", "cycle"):
            if key in sent:
                changes[key] = getattr(body, key)
        for key in (
            "blocks",
            "blocked_by",
            "related",
            "remove_blocks",
            "remove_blocked_by",
            "remove_related",
        ):
            if getattr(body, key):
                changes[key] = [issue_of(i).id for i in getattr(body, key)]
        _try(lambda: state.update_issue(it, actor.id, **changes))
        if body.subscribed is not None:
            state.set_subscribed(it, actor.id, body.subscribed)
        return summary(it)

    @r.delete("/workplace/api/linear/issues/{identifier}")
    def delete_issue(identifier: str, request: Request):
        signed_in(request)
        state.delete_issue(issue_of(identifier))
        return {"ok": True}

    @r.post("/workplace/api/linear/issues/{identifier}/attachments")
    async def attach(identifier: str, request: Request):
        it = issue_of(identifier)
        actor = signed_in(request)
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(422, "Attach a file")
            content = await upload.read()
            att = _try(
                lambda: state.add_attachment(
                    it,
                    actor.id,
                    str(form.get("title") or upload.filename or "file"),
                    content=content,
                    content_type=upload.content_type or "application/octet-stream",
                    url_for=mcp._file_url,
                )
            )
        else:
            link = NewLink(**(await request.json()))
            att = _try(lambda: state.add_attachment(it, actor.id, link.title, link.url))
        return {"id": att.id, "url": att.url}

    @r.delete("/workplace/api/linear/attachments/{attachment_id}")
    def detach(attachment_id: str, request: Request):
        signed_in(request)
        _try(lambda: state.remove_attachment(attachment_id))
        return {"ok": True}

    @r.post("/workplace/api/linear/issues/{identifier}/comments")
    def comment(identifier: str, body: NewComment, request: Request):
        it = issue_of(identifier)
        _try(
            lambda: state.add_comment(
                it.id, signed_in(request).id, body.body, parent_id=body.parent_id
            )
        )
        return {"ok": True}

    def _own_comment(comment_id: str, request: Request, *, admins: bool = False):
        viewer = signed_in(request)
        comment = state.comments.get(comment_id)
        if comment is None:
            raise HTTPException(404, "No such comment")
        if comment.user_id != viewer.id and not (admins and me(request).admin):
            raise HTTPException(403, "You can only change your own comments")
        return comment

    @r.patch("/workplace/api/linear/comments/{comment_id}")
    def edit_comment(comment_id: str, body: EditComment, request: Request):
        comment = _own_comment(comment_id, request)
        _try(lambda: state.edit_comment(comment, body.body))
        return {"ok": True}

    @r.delete("/workplace/api/linear/comments/{comment_id}")
    def delete_comment(comment_id: str, request: Request):
        _own_comment(comment_id, request, admins=True)
        state.delete_comment(comment_id)
        return {"ok": True}

    @r.post("/workplace/api/linear/sessions/{session_id}/stop")
    def stop_session(session_id: str, request: Request):
        viewer = signed_in(request)
        session = state.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "No such session")
        _try(lambda: state.stop_session(session, viewer.id))
        return {"ok": True}

    # -- projects --------------------------------------------------------------------------

    @r.get("/workplace/api/linear/projects")
    def projects(request: Request, archived: bool = False):
        signed_in(request)
        return {
            "projects": [
                project_summary(p)
                for p in state.projects.values()
                if (p.archived_at is not None) == archived
            ]
        }

    @r.post("/workplace/api/linear/projects")
    def create_project(body: NewProject, request: Request):
        actor = signed_in(request)
        teams = [team_of(t).id for t in body.teams]
        fields = body.model_dump(exclude={"name", "teams"})
        if fields.get("lead"):
            fields["lead"] = person(fields["lead"]).id
        project = _try(
            lambda: state.create_project(actor.id, body.name, teams, **fields)
        )
        return project_summary(project)

    @r.get("/workplace/api/linear/projects/{ref}")
    def project(ref: str, request: Request):
        signed_in(request)
        p = project_of(ref)
        milestones = []
        for m in state.milestones_of(p):
            mine = [i for i in state.project_issues(p) if i.milestone_id == m.id]
            done = sum(1 for i in mine if state.state(i).type == "completed")
            milestones.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "description": m.description,
                    "target_date": m.target_date,
                    "issues": len(mine),
                    "done": done,
                }
            )
        updates = sorted(
            (u for u in state.status_updates.values() if u.project_id == p.id),
            key=lambda u: u.created_at,
            reverse=True,
        )
        return {
            **project_summary(p),
            "description": p.description,
            "members": [u for u in p.member_ids if u in state.users],
            "links": [{"id": a.id, "title": a.title, "url": a.url} for a in p.links],
            "milestones": milestones,
            "issues": [
                summary(i)
                for i in sorted(state.project_issues(p), key=lambda i: i.created_at)
            ],
            "documents": [
                {"id": d.id, "title": d.title, "updated_at": iso(d.updated_at)}
                for d in state.documents.values()
                if d.project_id == p.id
            ],
            "updates": [
                {
                    "id": u.id,
                    "body": u.body,
                    "health": u.health,
                    "at": iso(u.created_at),
                    "user_id": u.user_id,
                    "author": (
                        state.users[u.user_id].name
                        if u.user_id in state.users
                        else None
                    ),
                }
                for u in updates
            ],
        }

    @r.patch("/workplace/api/linear/projects/{ref}")
    def edit_project(ref: str, body: EditProject, request: Request):
        actor = signed_in(request)
        p = project_of(ref)
        changes = body.model_dump(exclude_unset=True)
        if "teams" in changes and changes["teams"] is not None:
            changes["teams"] = [team_of(t).id for t in changes["teams"]]
        if changes.get("lead"):
            changes["lead"] = person(changes["lead"]).id
        changes["add_members"] = [person(u).id for u in body.add_members]
        changes["remove_members"] = [person(u).id for u in body.remove_members]
        changes["links"] = [link.model_dump() for link in body.links]
        _try(lambda: state.update_project(p, actor.id, **changes))
        return project_summary(p)

    @r.delete("/workplace/api/linear/projects/{ref}")
    def delete_project(ref: str, request: Request):
        signed_in(request)
        state.delete_project(project_of(ref))
        return {"ok": True}

    @r.post("/workplace/api/linear/projects/{ref}/milestones")
    def add_milestone(ref: str, body: SaveMilestone, request: Request):
        signed_in(request)
        p = project_of(ref)
        milestone = _try(
            lambda: state.save_milestone(p, None, **body.model_dump(exclude_unset=True))
        )
        return {"id": milestone.id}

    @r.patch("/workplace/api/linear/milestones/{milestone_id}")
    def edit_milestone(milestone_id: str, body: SaveMilestone, request: Request):
        signed_in(request)
        milestone = state.milestones.get(milestone_id)
        if milestone is None:
            raise HTTPException(404, "No such milestone")
        _try(
            lambda: state.save_milestone(
                state.projects[milestone.project_id],
                milestone,
                **body.model_dump(exclude_unset=True),
            )
        )
        return {"ok": True}

    @r.delete("/workplace/api/linear/milestones/{milestone_id}")
    def delete_milestone(milestone_id: str, request: Request):
        signed_in(request)
        milestone = state.milestones.get(milestone_id)
        if milestone is None:
            raise HTTPException(404, "No such milestone")
        state.delete_milestone(milestone)
        return {"ok": True}

    @r.post("/workplace/api/linear/projects/{ref}/updates")
    def post_update(ref: str, body: NewUpdate, request: Request):
        actor = signed_in(request)
        update = _try(
            lambda: state.save_status_update(
                actor.id, project_of(ref), body=body.body, health=body.health
            )
        )
        return {"id": update.id}

    @r.delete("/workplace/api/linear/updates/{update_id}")
    def delete_update(update_id: str, request: Request):
        viewer = signed_in(request)
        update = state.status_updates.get(update_id)
        if update is None:
            raise HTTPException(404, "No such update")
        if update.user_id != viewer.id and not me(request).admin:
            raise HTTPException(403, "You can only delete your own updates")
        state.delete_status_update(update)
        return {"ok": True}

    # -- documents ---------------------------------------------------------------------------

    def _doc_parents(body: SaveDocument) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if body.project:
            out["project_id"] = project_of(body.project).id
        if body.issue:
            out["issue_id"] = issue_of(body.issue).id
        if body.team:
            out["team_id"] = team_of(body.team).id
        return out

    def doc_json(d) -> Dict[str, Any]:
        creator = state.users.get(d.creator_id)
        editor = state.users.get(d.updated_by_id or "")
        project = state.projects.get(d.project_id or "")
        issue_ = state.issues.get(d.issue_id or "")
        team = state.teams.get(d.team_id or "")
        return {
            "id": d.id,
            "title": d.title,
            "content": d.content,
            "created_at": iso(d.created_at),
            "updated_at": iso(d.updated_at),
            "creator": creator.name if creator else None,
            "updated_by": editor.name if editor else None,
            "project": {"id": project.id, "name": project.name} if project else None,
            "issue": (
                {"identifier": issue_.identifier, "title": issue_.title}
                if issue_
                else None
            ),
            "team": team.key if team else None,
        }

    @r.get("/workplace/api/linear/documents")
    def documents(request: Request):
        signed_in(request)
        return {
            "documents": [
                {k: v for k, v in doc_json(d).items() if k != "content"}
                for d in sorted(
                    state.documents.values(), key=lambda d: d.updated_at, reverse=True
                )
            ]
        }

    @r.get("/workplace/api/linear/documents/{doc_id}")
    def document(doc_id: str, request: Request):
        signed_in(request)
        d = state.find_document(doc_id)
        if d is None:
            raise HTTPException(404, "No such document")
        return doc_json(d)

    @r.post("/workplace/api/linear/documents")
    def create_document(body: SaveDocument, request: Request):
        actor = signed_in(request)
        d = _try(
            lambda: state.save_document(
                actor.id,
                None,
                title=body.title,
                content=body.content or "",
                **_doc_parents(body),
            )
        )
        return doc_json(d)

    @r.patch("/workplace/api/linear/documents/{doc_id}")
    def edit_document(doc_id: str, body: SaveDocument, request: Request):
        actor = signed_in(request)
        d = state.find_document(doc_id)
        if d is None:
            raise HTTPException(404, "No such document")
        _try(
            lambda: state.save_document(
                actor.id,
                d,
                title=body.title,
                content=body.content,
                **_doc_parents(body),
            )
        )
        return doc_json(d)

    @r.delete("/workplace/api/linear/documents/{doc_id}")
    def delete_document(doc_id: str, request: Request):
        signed_in(request)
        d = state.find_document(doc_id)
        if d is None:
            raise HTTPException(404, "No such document")
        state.delete_document(d)
        return {"ok": True}

    # -- the inbox ---------------------------------------------------------------------------

    @r.get("/workplace/api/linear/inbox")
    def inbox(request: Request):
        viewer = signed_in(request)
        out = []
        for n in state.inbox(viewer.id):
            actor = state.users.get(n.actor_id or "")
            it = state.issues.get(n.issue_id or "")
            project = state.projects.get(n.project_id or "")
            out.append(
                {
                    "id": n.id,
                    "type": n.type,
                    "text": n.text,
                    "at": iso(n.created_at),
                    "read": bool(n.read_at),
                    "actor": actor.name if actor else None,
                    "issue": (
                        {"identifier": it.identifier, "title": it.title} if it else None
                    ),
                    "project": (
                        {"id": project.id, "name": project.name} if project else None
                    ),
                    "comment": (
                        state.comments[n.comment_id].body
                        if n.comment_id in state.comments
                        else None
                    ),
                }
            )
        return {"notifications": out}

    def _note(note_id: str, request: Request):
        viewer = signed_in(request)
        note = state.notifications.get(note_id)
        if note is None or note.user_id != viewer.id:
            raise HTTPException(404, "No such notification")
        return note

    @r.patch("/workplace/api/linear/inbox/{note_id}")
    def mark(note_id: str, body: MarkNotification, request: Request):
        note = _note(note_id, request)
        snooze = body.snoozed_until if "snoozed_until" in body.model_fields_set else ...
        state.mark_notification(note, read=body.read, snoozed_until=snooze)
        return {"ok": True}

    @r.delete("/workplace/api/linear/inbox/{note_id}")
    def dismiss(note_id: str, request: Request):
        state.delete_notification(_note(note_id, request))
        return {"ok": True}

    @r.post("/workplace/api/linear/inbox/read")
    def read_all(request: Request):
        viewer = signed_in(request)
        for note in state.inbox(viewer.id):
            if not note.read_at:
                state.mark_notification(note, read=True)
        return {"ok": True}

    return r
