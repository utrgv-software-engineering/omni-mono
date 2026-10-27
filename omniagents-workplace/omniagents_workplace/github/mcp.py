"""The GitHub MCP server, served by the workplace.

Same server, as an agent sees it: the tools of GitHub's MCP server
(github/github-mcp-server, the code behind ``https://api.githubcopilot.com/mcp/``)
with the ``default`` and ``actions`` toolsets, definitions verbatim
(``reference/github_mcp_tools.json``), over Streamable HTTP, authorized with
a token (``Authorization: Bearer <token>``) and acting as that account.
``X-MCP-Toolsets``, ``X-MCP-Tools`` and ``X-MCP-Readonly`` narrow the tools
as they do on the remote server.

Results are the text the server returns (its ``Minimal*`` types as JSON, a
file as an embedded resource), and failures the text it reports, so an
agent moved from the workplace to GitHub sees the same thing. Releases,
tags, teams, Copilot and repository creation have nothing to act on here
and answer as GitHub does for a repository without them.
"""

from __future__ import annotations

import base64
import contextlib
import fnmatch
import json
import re
import time
from importlib import resources
from typing import Any, Callable, Dict, List, Optional, Tuple

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from ..wire import Wire
from .serialize import Serializer
from .state import GitHubError, GitHubState, Issue, Repo, User, iso

__all__ = ["GitHubMCP", "load_tool_definitions"]


def load_tool_definitions() -> Dict[str, Any]:
    text = (
        resources.files("omniagents_workplace")
        .joinpath("reference/github_mcp_tools.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


class ToolError(Exception):
    """A tool failure: the text the server reports, with ``isError``."""


def _json(value: Any) -> str:
    # Go's encoding/json: compact, HTML characters escaped.
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def api_error(
    prefix: str, method: str, path: str, exc: GitHubError, api: str
) -> ToolError:
    """``NewGitHubAPIErrorResponse``: go-github's error under the tool's message."""
    if exc.status == 422 and (exc.errors or exc.message):
        parts = [exc.message] if exc.message else []
        for e in exc.errors:
            location = ".".join(
                p for p in (e.get("resource", ""), e.get("field", "")) if p
            )
            code = e.get("code", "")
            if location and code:
                location += f" ({code})"
            elif not location:
                location = code
            message = e.get("message", "")
            parts.append(
                f"{location}: {message}"
                if location and message
                else (message or location)
            )
        return ToolError(f"{prefix}: " + "\n".join(p for p in parts if p))
    reason = {
        404: "Not Found",
        403: "Forbidden",
        405: "Method Not Allowed",
        409: "Conflict",
    }.get(exc.status, "")
    message = exc.message if exc.message != reason else reason
    return ToolError(f"{prefix}: {method} {api}/{path}: {exc.status} {message} []")


def _int(
    args: Dict[str, Any], key: str, required: bool = True, default: Optional[int] = None
) -> Optional[int]:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolError(f"missing required parameter: {key}")
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ToolError(
            f"parameter {key} is not of type number, is {type(value).__name__}"
        )
    if number != int(number):
        raise ToolError(f"parameter {key} is not an integer")
    return int(number)


def _str(args: Dict[str, Any], key: str, required: bool = True) -> str:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolError(f"missing required parameter: {key}")
        return ""
    if not isinstance(value, str):
        raise ToolError(
            f"parameter {key} is not of type string, is {type(value).__name__}"
        )
    return value


def _strings(args: Dict[str, Any], key: str) -> Optional[List[str]]:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise ToolError(
            f"parameter {key} could not be coerced to []string, is {type(value).__name__}"
        )
    return [str(v) for v in value]


def _page(
    items: List[Any], args: Dict[str, Any], per_page_default: int = 30
) -> List[Any]:
    page = _int(args, "page", required=False, default=1) or 1
    per_page = min(
        _int(args, "perPage", required=False, default=per_page_default)
        or per_page_default,
        100,
    )
    start = (page - 1) * per_page
    return items[start : start + per_page]


def _cursor(n: int) -> str:
    return base64.b64encode(f"cursor:{n}".encode()).decode()


def _uncursor(after: str) -> int:
    try:
        return int(base64.b64decode(after).decode().split(":", 1)[1])
    except Exception:
        raise ToolError("invalid pagination parameters: invalid after cursor")


class GitHubMCP:
    """The MCP endpoint: an ASGI app for ``/github/mcp``."""

    def api_error(
        self, prefix: str, method: str, path: str, exc: GitHubError
    ) -> ToolError:
        """GitHub's API error, as the MCP server reports it (with the API URL it called)."""
        return api_error(prefix, method, path, exc, self.s.api)

    def __init__(self, state: GitHubState, wire: Wire, base_url: Callable[[], str]):
        self.state = state
        self.wire = wire
        self.s = Serializer(state, base_url)
        reference = load_tool_definitions()
        self._definitions: List[Dict[str, Any]] = reference["tools"]
        self._toolsets: Dict[str, str] = reference.get("toolsets") or {}
        info = reference.get("serverInfo") or {}
        self.server = Server(
            info.get("name", "github-mcp-server"),
            version=info.get("version"),
            instructions=reference.get("instructions"),
        )
        self.server.list_tools()(self._list_tools)
        self.server.call_tool(validate_input=False)(self._call_tool)
        self.manager = StreamableHTTPSessionManager(
            self.server, json_response=False, stateless=True
        )

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
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"www-authenticate", b'Bearer error="invalid_token"'),
                    ],
                }
            )
            await send(
                {"type": "http.response.body", "body": b"bad request: unauthorized\n"}
            )
            return
        await self.manager.handle_request(scope, receive, send)

    def _caller(self, headers: Dict[str, str]) -> Optional[User]:
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        return self.state.user_for_token(auth[7:].strip())

    def _request(self) -> Tuple[User, Dict[str, str]]:
        request = self.server.request_context.request
        headers = {k.lower(): v for k, v in request.headers.items()} if request else {}
        user = self._caller(headers)
        if user is None:
            raise ToolError("failed to get GitHub client: unauthorized")
        return user, headers

    def _enabled(self, headers: Dict[str, str]) -> List[Dict[str, Any]]:
        toolsets = {
            t.strip() for t in headers.get("x-mcp-toolsets", "").split(",") if t.strip()
        }
        tools = {
            t.strip() for t in headers.get("x-mcp-tools", "").split(",") if t.strip()
        }
        readonly = headers.get("x-mcp-readonly", "").lower() in ("1", "true")
        if "default" in toolsets:
            toolsets |= {
                "context",
                "repos",
                "issues",
                "pull_requests",
                "users",
                "copilot",
            }
        if "all" in toolsets:
            toolsets |= set(self._toolsets.values())
        out = []
        for definition in self._definitions:
            name = definition["name"]
            toolset = self._toolsets.get(name, "context")
            if toolsets or tools:
                if not (toolset in toolsets or name in tools):
                    continue
            elif toolset == "actions":
                continue  # the remote server's default leaves actions out
            if readonly and not (definition.get("annotations") or {}).get(
                "readOnlyHint"
            ):
                continue
            out.append(definition)
        return out

    # -- tools/list ----------------------------------------------------------

    async def _list_tools(self) -> List[types.Tool]:
        _, headers = self._request()
        return [types.Tool.model_validate(d) for d in self._enabled(headers)]

    # -- tools/call ----------------------------------------------------------

    async def _call_tool(self, name: str, arguments: Dict[str, Any]):
        user, headers = self._request()
        started = time.monotonic()
        entry = self.wire.record(
            service="github",
            direction="in",
            kind="mcp",
            title=name,
            actor=user.login,
            status="pending",
            request=arguments,
        )
        try:
            if name not in {d["name"] for d in self._enabled(headers)}:
                raise ToolError(f"unknown tool {name!r}")
            handler = getattr(self, f"_{name}", None)
            if handler is None:
                raise ToolError(f"{name} is not available in the workplace")
            result = handler(user, arguments or {})
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
        content = (
            result
            if isinstance(result, list)
            else [types.TextContent(type="text", text=result)]
        )
        shown = next((c.text for c in content if isinstance(c, types.TextContent)), "")
        self.wire.update(
            entry,
            status="ok",
            response=_display(shown),
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return content

    # -- helpers ---------------------------------------------------------------

    def _repo(self, args: Dict[str, Any], prefix: str, path: str = "") -> Repo:
        owner, repo = _str(args, "owner"), _str(args, "repo")
        try:
            return self.state.repo(owner, repo)
        except GitHubError as exc:
            raise self.api_error(prefix, "GET", f"repos/{owner}/{repo}{path}", exc)

    def _graphql_repo(self, args: Dict[str, Any], prefix: str) -> Repo:
        owner, repo = _str(args, "owner"), _str(args, "repo")
        try:
            return self.state.repo(owner, repo)
        except GitHubError:
            raise ToolError(
                f"{prefix}: Could not resolve to a Repository with the name '{owner}/{repo}'."
            )

    def _issue(self, repo: Repo, number: int, prefix: str) -> Issue:
        try:
            return self.state.issue(repo, number)
        except GitHubError as exc:
            raise self.api_error(
                prefix, "GET", f"repos/{repo.full_name}/issues/{number}", exc
            )

    def _pull(
        self, repo: Repo, number: int, prefix: str = "failed to get pull request"
    ) -> Issue:
        try:
            return self.state.pull(repo, number)
        except GitHubError as exc:
            raise self.api_error(
                prefix, "GET", f"repos/{repo.full_name}/pulls/{number}", exc
            )

    # -- context ---------------------------------------------------------------

    def _get_me(self, user: User, args):
        rest = self.s.user(user.login)
        return _json(
            {
                "login": user.login,
                "id": user.id,
                "profile_url": rest["html_url"],
                "avatar_url": rest["avatar_url"],
                "details": {
                    "name": user.name,
                    "email": user.email,
                    "public_repos": 0,
                    "public_gists": 0,
                    "followers": 0,
                    "following": 0,
                    "created_at": "2024-01-15T09:00:00Z",
                    "updated_at": "2024-01-15T09:00:00Z",
                },
            }
        )

    def _get_teams(self, user: User, args):
        return _json([])

    def _get_team_members(self, user: User, args):
        org, slug = _str(args, "org"), _str(args, "team_slug")
        raise ToolError(
            f"Failed to get team members: Could not resolve to an Organization with the login of '{org}'."
        )

    # -- issues ----------------------------------------------------------------

    def _issue_read(self, user: User, args):
        method = _str(args, "method")
        repo = self._repo(args, "failed to get issue")
        number = _int(args, "issue_number")
        if method == "get":
            issue = self._issue(repo, number, "failed to get issue")
            m = self.s.minimal_issue(repo, issue)
            m["has_parent"] = False
            m["has_children"] = False
            closing = self.state.closing_pulls(repo, issue) if not issue.pull else []
            m["closed_by_pull_requests"] = {
                "total_count": len(closing),
                "references": [
                    {
                        "number": p.number,
                        "title": p.title,
                        "state": "MERGED" if p.pull.merged else p.state.upper(),
                        "url": self.s.issue_html(repo, p),
                        "repository": repo.full_name,
                    }
                    for p in closing[:5]
                ],
            }
            return _json(m)
        if method == "get_comments":
            issue = self._issue(repo, number, "failed to get issue comments")
            return _json(
                [
                    self.s.minimal_comment(repo, issue, c)
                    for c in _page(issue.comments, args)
                ]
            )
        if method == "get_labels":
            issue = self._issue(repo, number, "Failed to get issue labels")
            labels = [
                {
                    "id": f"LA_kwDO{repo.labels[n].id:08d}",
                    "name": n,
                    "color": repo.labels[n].color,
                    "description": repo.labels[n].description,
                }
                for n in issue.labels
            ]
            return _json({"labels": labels, "totalCount": len(labels)})
        if method == "get_sub_issues":
            self._issue(repo, number, "failed to list sub-issues")
            return _json([])
        if method == "get_parent":
            self._issue(repo, number, "failed to get parent issue")
            return _json(None)
        raise ToolError(f"unknown method: {method}")

    def _issue_write(self, user: User, args):
        method = _str(args, "method")
        repo = self._repo(
            args,
            (
                "failed to create issue"
                if method == "create"
                else "failed to update issue"
            ),
        )
        labels = _strings(args, "labels")
        assignees = _strings(args, "assignees")
        if method == "create":
            title = _str(args, "title", required=False)
            if not title:
                raise ToolError("missing required parameter: title")
            try:
                issue = self.state.create_issue(
                    repo,
                    user.login,
                    title,
                    _str(args, "body", required=False),
                    labels or [],
                    assignees or [],
                )
            except GitHubError as exc:
                raise self.api_error(
                    "failed to create issue",
                    "POST",
                    f"repos/{repo.full_name}/issues",
                    exc,
                )
            return _json({"id": str(issue.id), "url": self.s.issue_html(repo, issue)})
        if method == "update":
            number = _int(args, "issue_number")
            issue = self._issue(repo, number, "failed to update issue")
            state = _str(args, "state", required=False) or None
            reason = _str(args, "state_reason", required=False) or None
            if state and state not in ("open", "closed"):
                raise ToolError("invalid state: must be 'open' or 'closed'")
            if reason == "duplicate" and not args.get("duplicate_of"):
                raise ToolError(
                    "duplicate_of must be provided when state_reason is 'duplicate'"
                )
            try:
                self.state.update_issue(
                    repo,
                    issue,
                    user.login,
                    title=_str(args, "title", required=False) or None,
                    body=_str(args, "body", required=False) or None,
                    state=state,
                    state_reason=reason,
                    labels=labels,
                    assignees=assignees,
                )
            except GitHubError as exc:
                raise self.api_error(
                    "failed to update issue",
                    "PATCH",
                    f"repos/{repo.full_name}/issues/{number}",
                    exc,
                )
            return _json({"id": str(issue.id), "url": self.s.issue_html(repo, issue)})
        raise ToolError("invalid method, must be either 'create' or 'update'")

    def _add_issue_comment(self, user: User, args):
        repo = self._repo(args, "failed to create comment")
        number = _int(args, "issue_number")
        body = args.get("body")
        reaction = args.get("reaction")
        comment_id = args.get("comment_id")
        if comment_id is not None and body is not None:
            raise ToolError("comment_id cannot be combined with body")
        if comment_id is not None and reaction is None:
            raise ToolError("comment_id can only be provided when reaction is provided")
        if body is None and reaction is None:
            raise ToolError("at least one of body or reaction is required")
        if body is not None and body == "":
            raise ToolError("body cannot be empty when provided")
        if reaction is not None and reaction not in (
            "+1",
            "-1",
            "laugh",
            "confused",
            "heart",
            "hooray",
            "rocket",
            "eyes",
        ):
            raise ToolError(
                "reaction must be one of +1, -1, laugh, confused, heart, hooray, rocket, eyes"
            )
        issue = self._issue(repo, number, "failed to create comment")
        reaction_out = None
        if reaction is not None:
            if comment_id is not None:
                try:
                    owning, comment = self.state.comment(repo, int(comment_id))
                except GitHubError as exc:
                    raise self.api_error(
                        "failed to get issue comment",
                        "GET",
                        f"repos/{repo.full_name}/issues/comments/{comment_id}",
                        exc,
                    )
                if owning.number != number:
                    raise ToolError(
                        f"comment_id does not belong to issue_number {number}"
                    )
                rid = self.state.react(comment, user.login, reaction)
                reaction_out = {
                    "id": str(rid),
                    "url": f"{self.s.api}/repos/{repo.full_name}/issues/comments/{comment_id}/reactions/{rid}",
                }
            else:
                rid = self.state.react(issue, user.login, reaction)
                reaction_out = {
                    "id": str(rid),
                    "url": f"{self.s.api}/repos/{repo.full_name}/issues/{number}/reactions/{rid}",
                }
        comment_out = None
        if body is not None:
            try:
                comment = self.state.add_comment(repo, issue, user.login, str(body))
            except GitHubError as exc:
                raise self.api_error(
                    "failed to create comment",
                    "POST",
                    f"repos/{repo.full_name}/issues/{number}/comments",
                    exc,
                )
            comment_out = {
                "id": str(comment.id),
                "url": self.s.comment(repo, issue, comment)["html_url"],
            }
        if comment_out and reaction_out:
            return _json({"comment": comment_out, "reaction": reaction_out})
        return _json(comment_out or reaction_out)

    def _update_issue_comment(self, user: User, args):
        repo = self._repo(args, "failed to update issue comment")
        comment_id = _int(args, "comment_id")
        body = _str(args, "body")
        try:
            issue, comment = self.state.comment(repo, comment_id)
            if comment.user != user.login:
                raise GitHubError(404, "Not Found")
            self.state.edit_comment(repo, comment_id, user.login, body)
        except GitHubError as exc:
            raise self.api_error(
                "failed to update issue comment",
                "PATCH",
                f"repos/{repo.full_name}/issues/comments/{comment_id}",
                exc,
            )
        return _json(
            {
                "id": str(comment.id),
                "url": self.s.comment(repo, issue, comment)["html_url"],
            }
        )

    def _list_issues(self, user: User, args):
        repo = self._graphql_repo(args, "failed to list issues")
        state = (_str(args, "state", required=False) or "").upper()
        states = ["OPEN", "CLOSED"] if not state else [state]
        if any(s not in ("OPEN", "CLOSED") for s in states):
            raise ToolError("invalid state: must be OPEN or CLOSED")
        labels = _strings(args, "labels") or []
        order_by = (_str(args, "orderBy", required=False) or "CREATED_AT").upper()
        direction = (_str(args, "direction", required=False) or "DESC").upper()
        since = _str(args, "since", required=False)
        issues = [
            i for i in repo.issues.values() if not i.pull and i.state.upper() in states
        ]
        if labels:
            issues = [i for i in issues if any(label in i.labels for label in labels)]
        if since:
            issues = [i for i in issues if (iso(i.updated_at) or "") >= since]
        key = {
            "CREATED_AT": lambda i: i.created_at,
            "UPDATED_AT": lambda i: i.updated_at,
            "COMMENTS": lambda i: len(i.comments),
        }.get(order_by, lambda i: i.created_at)
        issues.sort(key=key, reverse=direction == "DESC")
        first = min(_int(args, "perPage", required=False, default=30) or 30, 100)
        start = _uncursor(args["after"]) if args.get("after") else 0
        window = issues[start : start + first]
        payload: Dict[str, Any] = {
            "issues": [self.s.minimal_issue_from_graphql(repo, i) for i in window],
            "totalCount": len(issues),
            "pageInfo": {
                "hasNextPage": start + first < len(issues),
                "hasPreviousPage": start > 0,
                **(
                    {
                        "startCursor": _cursor(start),
                        "endCursor": _cursor(start + len(window)),
                    }
                    if window
                    else {}
                ),
            },
        }
        fields = _strings(args, "fields")
        if fields:
            payload["issues"] = [
                {k: v for k, v in i.items() if k in fields} for i in payload["issues"]
            ]
        return _json(payload)

    def _search_issues(self, user: User, args):
        return self._search(user, args, "issue", "failed to search issues")

    def _search_pull_requests(self, user: User, args):
        return self._search(user, args, "pr", "failed to search pull requests")

    def _search(self, user: User, args, kind: str, prefix: str):
        query = _str(args, "query", required=False)
        if not query:
            raise ToolError("missing required parameter: query")
        if not re.search(rf"(^|\s|\W)is:{kind}($|\s|\W)", query):
            query = f"is:{kind} {query}"
        owner, name = args.get("owner"), args.get("repo")
        if owner and name and not re.search(r"(^|\s|\W)repo:\S+", query):
            query = f"repo:{owner}/{name} {query}"
        try:
            hits = self._match_issues(query, user)
        except ValueError as exc:
            raise ToolError(
                f"{prefix}: GET {self.s.api}/search/issues: 422 Validation Failed [{exc}]"
            )
        sort = _str(args, "sort", required=False)
        order = _str(args, "order", required=False) or "desc"
        if sort in ("created", "updated", "comments"):
            key = {
                "created": lambda h: h[1].created_at,
                "updated": lambda h: h[1].updated_at,
                "comments": lambda h: len(h[1].comments),
            }[sort]
            hits.sort(key=key, reverse=order == "desc")
        page = _page(hits, args)
        items = []
        for repo, issue in page:
            item = self.s.issue(repo, issue)
            item["score"] = 1.0
            items.append(item)
        fields = _strings(args, "fields")
        if fields:
            items = [{k: v for k, v in item.items() if k in fields} for item in items]
        return _json(
            {"total_count": len(hits), "incomplete_results": False, "items": items}
        )

    def _match_issues(self, query: str, user: User) -> List[Tuple[Repo, Issue]]:
        tokens = re.findall(r'-?[\w.-]+:"[^"]*"|-?[\w.-]+:\S+|"[^"]*"|\S+', query)
        quals: List[Tuple[bool, str, str]] = []
        words: List[str] = []
        for token in tokens:
            negate = token.startswith("-")
            raw = token[1:] if negate else token
            if ":" in raw and not raw.startswith('"'):
                key, value = raw.split(":", 1)
                quals.append((negate, key.lower(), value.strip('"')))
            else:
                words.append(raw.strip('"').lower())
        out = []
        for repo in self.state.repos.values():
            for issue in repo.issues.values():
                if all(
                    self._qualifier(repo, issue, key, value, user) != negate
                    for negate, key, value in quals
                ):
                    text = " ".join(
                        [issue.title, issue.body] + [c.body for c in issue.comments]
                    ).lower()
                    if all(word in text for word in words):
                        out.append((repo, issue))
        out.sort(key=lambda h: h[1].created_at, reverse=True)
        return out

    def _qualifier(
        self, repo: Repo, issue: Issue, key: str, value: str, user: User
    ) -> bool:
        login = user.login if value == "@me" else value
        if key == "is" or key == "type":
            return {
                "issue": not issue.pull,
                "pr": bool(issue.pull),
                "pull-request": bool(issue.pull),
                "open": issue.state == "open",
                "closed": issue.state == "closed",
                "merged": bool(issue.pull and issue.pull.merged),
                "unmerged": bool(issue.pull and not issue.pull.merged),
                "draft": bool(issue.pull and issue.pull.draft),
            }.get(value.lower(), False)
        if key == "state":
            return issue.state == value.lower()
        if key == "repo":
            return repo.full_name.lower() == value.lower()
        if key in ("org", "user", "owner"):
            return repo.owner.lower() == value.lower()
        if key == "author":
            return issue.user == login
        if key == "assignee":
            return login in issue.assignees
        if key == "mentions":
            from .state import mentions

            return login in mentions(issue.body) or any(
                login in mentions(c.body) for c in issue.comments
            )
        if key == "commenter":
            return any(c.user == login for c in issue.comments)
        if key == "involves":
            return (
                issue.user == login
                or login in issue.assignees
                or any(c.user == login for c in issue.comments)
            )
        if key == "label":
            return any(v in issue.labels for v in value.split(","))
        if key == "no":
            return {"label": not issue.labels, "assignee": not issue.assignees}.get(
                value, False
            )
        if key == "head":
            return bool(issue.pull and issue.pull.head == value)
        if key == "base":
            return bool(issue.pull and issue.pull.base == value)
        if key == "review-requested":
            return bool(issue.pull and login in issue.pull.requested_reviewers)
        if key == "in":
            return True
        if key in ("created", "updated", "closed"):
            stamp = (
                iso(
                    {
                        "created": issue.created_at,
                        "updated": issue.updated_at,
                        "closed": issue.closed_at,
                    }[key]
                )
                or ""
            )
            m = re.match(r"([<>]=?)?(\S+)", value)
            op, date = (m.group(1) or ""), m.group(2)
            if ".." in date:
                lo, hi = date.split("..", 1)
                return (lo == "*" or stamp >= lo) and (
                    hi == "*" or stamp[: len(hi)] <= hi
                )
            return {
                "": stamp.startswith(date),
                ">": stamp > date,
                ">=": stamp >= date,
                "<": stamp < date,
                "<=": stamp[: len(date)] <= date,
            }[op]
        raise ValueError(f"unsupported qualifier {key}")

    def _get_label(self, user: User, args):
        repo = self._graphql_repo(args, "Failed to find label")
        name = _str(args, "name")
        label = repo.labels.get(name)
        if label is None:
            raise ToolError(f"label '{name}' not found in {repo.full_name}")
        return _json(
            {
                "id": f"LA_kwDO{label.id:08d}",
                "name": label.name,
                "color": label.color,
                "description": label.description,
            }
        )

    def _list_issue_types(self, user: User, args):
        repo = self._repo(args, "failed to list issue types")
        return _json([])

    def _list_issue_fields(self, user: User, args):
        self._graphql_repo(args, "failed to list issue fields")
        return _json([])

    def _sub_issue_write(self, user: User, args):
        raise ToolError(
            "failed to add sub-issue: sub-issues are not available in the workplace"
        )

    def _assign_copilot_to_issue(self, user: User, args):
        raise ToolError(
            "copilot isn't available as an assignee for this issue. Please inform the user to visit https://docs.github.com/en/copilot/using-github-copilot/using-copilot-coding-agent-to-work-on-tasks/about-assigning-tasks-to-copilot for more information."
        )

    def _request_copilot_review(self, user: User, args):
        raise ToolError(
            "failed to request copilot review: Copilot code review is not available in the workplace"
        )

    # -- pull requests -----------------------------------------------------------

    def _pull_request_read(self, user: User, args):
        method = _str(args, "method")
        repo = self._repo(args, "failed to get pull request")
        number = _int(args, "pullNumber")
        if method == "get":
            return _json(self.s.minimal_pull(repo, self._pull(repo, number)))
        if method == "get_diff":
            pr = self._pull(repo, number, "failed to get pull request diff")
            return repo.bare.diff(
                self.state.base_sha(repo, pr), self.state.head_sha(repo, pr)
            )
        if method == "get_files":
            pr = self._pull(repo, number, "failed to get pull request files")
            files = repo.bare.changes(
                self.state.base_sha(repo, pr), self.state.head_sha(repo, pr)
            )
            return _json([_file(f) for f in _page(files, args)])
        if method == "get_commits":
            pr = self._pull(repo, number, "failed to get pull request commits")
            head = self.state.head_sha(repo, pr)
            base = repo.bare.merge_base(
                self.state.base_sha(repo, pr), head
            ) or self.state.base_sha(repo, pr)
            commits = list(reversed(repo.bare.commits(f"{base}..{head}")))
            return _json(
                [
                    {
                        "sha": c.sha,
                        "html_url": self.s.html(repo, "commit", c.sha),
                        "message": c.message,
                        "author": {
                            "name": c.author_name,
                            "email": c.author_email,
                            "date": c.author_date,
                        },
                    }
                    for c in _page(commits, args)
                ]
            )
        if method == "get_status":
            pr = self._pull(repo, number)
            return _json(
                {
                    "state": "pending",
                    "sha": self.state.head_sha(repo, pr),
                    "total_count": 0,
                    "statuses": [],
                }
            )
        if method == "get_check_runs":
            pr = self._pull(repo, number)
            runs = self.state.runs_for(repo, self.state.head_sha(repo, pr))
            checks = [
                {
                    "id": job.id,
                    "name": job.name,
                    "status": job.status,
                    "conclusion": job.conclusion or "",
                    "html_url": self.s.html(
                        repo, "actions", "runs", run.id, "job", job.id
                    ),
                    "details_url": self.s.html(
                        repo, "actions", "runs", run.id, "job", job.id
                    ),
                    "started_at": iso(job.started_at) or "",
                    "completed_at": iso(job.completed_at) or "",
                }
                for run in runs
                for job in run.jobs
            ]
            checks = [
                {k: v for k, v in c.items() if v != "" or k in ("id", "name", "status")}
                for c in checks
            ]
            return _json(
                {"total_count": len(checks), "check_runs": _page(checks, args)}
            )
        if method == "get_reviews":
            pr = self._pull(repo, number, "failed to get pull request reviews")
            reviews = [
                r
                for r in pr.pull.reviews
                if r.state != "PENDING" or r.user == user.login
            ]
            return _json(
                [self.s.minimal_review(repo, pr, r) for r in _page(reviews, args)]
            )
        if method == "get_review_comments":
            pr = self._pull(repo, number)
            visible = [
                t
                for t in pr.pull.threads
                if any(
                    self._submitted(pr, c.review_id) or c.user == user.login
                    for c in t.comments
                )
            ]
            first = min(_int(args, "perPage", required=False, default=30) or 30, 100)
            start = _uncursor(args["after"]) if args.get("after") else 0
            window = visible[start : start + first]
            return _json(
                {
                    "review_threads": [
                        self.s.minimal_thread(repo, pr, t) for t in window
                    ],
                    "totalCount": len(visible),
                    "pageInfo": {
                        "hasNextPage": start + first < len(visible),
                        "hasPreviousPage": start > 0,
                        **(
                            {
                                "startCursor": _cursor(start),
                                "endCursor": _cursor(start + len(window)),
                            }
                            if window
                            else {}
                        ),
                    },
                }
            )
        if method == "get_comments":
            pr = self._pull(repo, number, "failed to get issue comments")
            return _json(
                [self.s.minimal_comment(repo, pr, c) for c in _page(pr.comments, args)]
            )
        raise ToolError(f"unknown method: {method}")

    def _submitted(self, pr: Issue, review_id: int) -> bool:
        return any(r.id == review_id and r.state != "PENDING" for r in pr.pull.reviews)

    def _create_pull_request(self, user: User, args):
        repo = self._repo(args, "failed to create pull request")
        title = _str(args, "title", required=False)
        if not title:
            raise ToolError("missing required parameter: title")
        head = _str(args, "head", required=False)
        if not head:
            raise ToolError("missing required parameter: head")
        base = _str(args, "base", required=False)
        if not base:
            raise ToolError("missing required parameter: base")
        try:
            pr = self.state.create_pull(
                repo,
                user.login,
                title,
                head,
                base,
                _str(args, "body", required=False),
                draft=bool(args.get("draft")),
                maintainer_can_modify=bool(args.get("maintainer_can_modify")),
            )
        except GitHubError as exc:
            raise self.api_error(
                "failed to create pull request",
                "POST",
                f"repos/{repo.full_name}/pulls",
                exc,
            )
        reviewers = _strings(args, "reviewers") or []
        if reviewers:
            try:
                self.state.request_reviewers(repo, pr, user.login, reviewers)
            except GitHubError as exc:
                raise self.api_error(
                    "failed to request reviewers",
                    "POST",
                    f"repos/{repo.full_name}/pulls/{pr.number}/requested_reviewers",
                    exc,
                )
        return _json({"id": str(pr.id), "url": self.s.issue_html(repo, pr)})

    def _update_pull_request(self, user: User, args):
        repo = self._repo(args, "failed to update pull request")
        number = _int(args, "pullNumber")
        pr = self._pull(repo, number, "failed to update pull request")
        state = _str(args, "state", required=False) or None
        try:
            self.state.update_pull(
                repo,
                pr,
                user.login,
                title=_str(args, "title", required=False) or None,
                body=_str(args, "body", required=False) or None,
                state=state,
                base=_str(args, "base", required=False) or None,
                draft=(
                    args.get("draft") if isinstance(args.get("draft"), bool) else None
                ),
                maintainer_can_modify=(
                    args.get("maintainer_can_modify")
                    if isinstance(args.get("maintainer_can_modify"), bool)
                    else None
                ),
            )
            reviewers = _strings(args, "reviewers") or []
            if reviewers:
                self.state.request_reviewers(repo, pr, user.login, reviewers)
        except GitHubError as exc:
            raise self.api_error(
                "failed to update pull request",
                "PATCH",
                f"repos/{repo.full_name}/pulls/{number}",
                exc,
            )
        return _json({"id": str(pr.id), "url": self.s.issue_html(repo, pr)})

    def _update_pull_request_branch(self, user: User, args):
        repo = self._repo(args, "failed to update pull request branch")
        number = _int(args, "pullNumber")
        pr = self._pull(repo, number, "failed to update pull request branch")
        try:
            self.state.update_branch(repo, pr, user.login)
        except GitHubError as exc:
            raise ToolError(
                f"failed to update pull request branch: PUT {self.s.api}/repos/{repo.full_name}/pulls/{number}/update-branch: 422 {exc.message} []"
            )
        return _json(
            {
                "message": "Updating pull request branch.",
                "url": self.s.html(repo, "pull", number),
            }
        )

    def _merge_pull_request(self, user: User, args):
        repo = self._repo(args, "failed to merge pull request")
        number = _int(args, "pullNumber")
        pr = self._pull(repo, number, "failed to merge pull request")
        try:
            sha = self.state.merge_pull(
                repo,
                pr,
                user.login,
                method=_str(args, "merge_method", required=False) or "merge",
                title=_str(args, "commit_title", required=False) or None,
                message=_str(args, "commit_message", required=False) or None,
                expected_sha=_str(args, "expectedHeadSha", required=False) or None,
            )
        except GitHubError as exc:
            raise self.api_error(
                "failed to merge pull request",
                "PUT",
                f"repos/{repo.full_name}/pulls/{number}/merge",
                exc,
            )
        return _json(
            {"sha": sha, "merged": True, "message": "Pull Request successfully merged"}
        )

    def _list_pull_requests(self, user: User, args):
        repo = self._repo(args, "failed to list pull requests", "/pulls")
        state = _str(args, "state", required=False) or "open"
        head = _str(args, "head", required=False)
        base = _str(args, "base", required=False)
        prs = [i for i in repo.pulls() if state == "all" or i.state == state]
        if head:
            prs = [i for i in prs if i.pull.head == head.split(":", 1)[-1]]
        if base:
            prs = [i for i in prs if i.pull.base == base]
        sort = _str(args, "sort", required=False) or "created"
        direction = _str(args, "direction", required=False) or (
            "desc" if sort != "long-running" else "asc"
        )
        key = {
            "created": lambda i: i.created_at,
            "updated": lambda i: i.updated_at,
            "popularity": lambda i: len(i.comments),
            "long-running": lambda i: i.created_at,
        }.get(sort, lambda i: i.created_at)
        prs.sort(key=key, reverse=direction == "desc")
        items = [self.s.minimal_pull(repo, i) for i in _page(prs, args)]
        fields = _strings(args, "fields")
        if fields:
            items = [{k: v for k, v in item.items() if k in fields} for item in items]
        return _json(items)

    def _pull_request_review_write(self, user: User, args):
        method = _str(args, "method")
        if method in ("resolve_thread", "unresolve_thread"):
            thread_id = _str(args, "threadId", required=False)
            if not thread_id:
                raise ToolError(
                    "threadId is required for resolve_thread and unresolve_thread methods"
                )
            try:
                self.state.resolve_thread(
                    thread_id, user.login, method == "resolve_thread"
                )
            except GitHubError as exc:
                raise ToolError(
                    f"failed to {method.replace('_thread', '')} review thread: {exc.message}"
                )
            return (
                "review thread resolved successfully"
                if method == "resolve_thread"
                else "review thread unresolved successfully"
            )
        repo = self._graphql_repo(args, "failed to get pull request")
        number = _int(args, "pullNumber")
        try:
            pr = self.state.pull(repo, number)
        except GitHubError:
            raise ToolError(
                f"failed to get pull request: Could not resolve to a PullRequest with the number of {number}."
            )
        if method == "create":
            event = _str(args, "event", required=False)
            try:
                self.state.create_review(
                    repo,
                    pr,
                    user.login,
                    event=event,
                    body=_str(args, "body", required=False),
                    commit_id=_str(args, "commitID", required=False) or None,
                )
            except GitHubError as exc:
                raise ToolError(exc.message)
            return (
                "pending pull request created"
                if not event
                else "pull request review submitted successfully"
            )
        pending = self.state.pending_review(pr, user.login)
        if method == "submit_pending":
            if pending is None:
                raise ToolError("No pending review found for the viewer")
            try:
                self.state.submit_review(
                    repo,
                    pr,
                    user.login,
                    pending,
                    _str(args, "event"),
                    _str(args, "body", required=False),
                )
            except GitHubError as exc:
                raise ToolError(f"failed to submit pull request review: {exc.message}")
            return "pending pull request review successfully submitted"
        if method == "delete_pending":
            if pending is None:
                raise ToolError("No pending review found for the viewer")
            self.state.delete_pending_review(pr, pending)
            return "pending pull request review successfully deleted"
        raise ToolError(f"unknown method: {method}")

    def _add_comment_to_pending_review(self, user: User, args):
        repo = self._graphql_repo(args, "failed to get pull request")
        number = _int(args, "pullNumber")
        try:
            pr = self.state.pull(repo, number)
        except GitHubError:
            raise ToolError(
                f"failed to get pull request: Could not resolve to a PullRequest with the number of {number}."
            )
        pending = self.state.pending_review(pr, user.login)
        if pending is None:
            raise ToolError("No pending review found for the viewer")
        subject = _str(args, "subjectType", required=False) or "LINE"
        try:
            self.state.add_review_comment(
                repo,
                pr,
                pending,
                user.login,
                path=_str(args, "path"),
                body=_str(args, "body"),
                line=_int(args, "line", required=False),
                start_line=_int(args, "startLine", required=False),
                side=_str(args, "side", required=False) or "RIGHT",
                subject_type=subject,
            )
        except GitHubError as exc:
            if exc.message in (
                "Line could not be resolved",
                "Path could not be resolved",
            ):
                raise ToolError(
                    "Failed to add comment to pending review. Possible reasons:\n"
                    "\t- The line number doesn't exist in the pull request diff\n"
                    "\t- The file path is incorrect\n"
                    "\t- The side (LEFT/RIGHT) is invalid for the specified line\n"
                )
            raise ToolError(exc.message)
        return "pull request review comment successfully added to pending review"

    def _add_reply_to_pull_request_comment(self, user: User, args):
        repo = self._repo(args, "failed to add reply to pull request comment")
        number = _int(args, "pullNumber")
        comment_id = _int(args, "commentId")
        body = args.get("body")
        reaction = args.get("reaction")
        if body is None and reaction is None:
            raise ToolError("at least one of body or reaction is required")
        pr = self._pull(repo, number, "failed to add reply to pull request comment")
        reaction_out = comment_out = None
        try:
            _, _, parent = self.state.review_comment(repo, comment_id)
        except GitHubError as exc:
            raise self.api_error(
                "failed to add reply to pull request comment",
                "POST",
                f"repos/{repo.full_name}/pulls/{number}/comments/{comment_id}/replies",
                exc,
            )
        if reaction is not None:
            try:
                rid = self.state.react(parent, user.login, str(reaction))
            except GitHubError:
                raise ToolError(
                    "reaction must be one of +1, -1, laugh, confused, heart, hooray, rocket, eyes"
                )
            reaction_out = {
                "id": str(rid),
                "url": f"{self.s.api}/repos/{repo.full_name}/pulls/comments/{comment_id}/reactions/{rid}",
            }
        if body is not None:
            reply = self.state.reply_to_review_comment(
                repo, pr, user.login, comment_id, str(body)
            )
            comment_out = {
                "id": str(reply.id),
                "url": self.s.review_comment(repo, pr, reply)["html_url"],
            }
        if comment_out and reaction_out:
            return _json({"comment": comment_out, "reaction": reaction_out})
        return _json(comment_out or reaction_out)

    # -- repositories ------------------------------------------------------------

    def _get_file_contents(self, user: User, args):
        repo = self._repo(args, "failed to get file contents")
        path = (_str(args, "path", required=False) or "/").strip()
        ref = _str(args, "ref", required=False)
        sha = _str(args, "sha", required=False)
        bare = repo.bare
        note = ""
        if sha:
            rev, ref_full = sha, ""
        elif not ref:
            rev, ref_full = repo.default_branch, f"refs/heads/{repo.default_branch}"
        elif re.fullmatch(r"[0-9a-f]{40}", ref):
            rev, ref_full, sha = ref, "", ref
        else:
            name = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
            name = name[len("heads/") :] if name.startswith("heads/") else name
            if bare.ref_sha(f"refs/heads/{name}"):
                rev, ref_full = name, f"refs/heads/{name}"
            elif bare.ref_sha(f"refs/tags/{name}"):
                rev, ref_full = f"refs/tags/{name}", f"refs/tags/{name}"
            elif name == "main":
                rev, ref_full = repo.default_branch, f"refs/heads/{repo.default_branch}"
                note = f" Note: the provided ref 'main' does not exist, default branch '{ref_full}' was used instead."
            else:
                raise ToolError(
                    f"failed to resolve git reference: could not resolve ref {ref!r} as a branch or a tag"
                )
        rel = path.strip("/")
        entries = bare.tree(rev, rel)
        if entries is not None:
            listing = []
            for kind, name, blob_sha, size in entries:
                entry_path = f"{rel}/{name}" if rel else name
                listing.append(
                    {
                        "type": kind,
                        "size": size,
                        "name": name,
                        "path": entry_path,
                        "sha": blob_sha,
                        "url": f"{self.s.api}/repos/{repo.full_name}/contents/{entry_path}?ref={rev}",
                        "git_url": f"{self.s.api}/repos/{repo.full_name}/git/{'trees' if kind == 'dir' else 'blobs'}/{blob_sha}",
                        "html_url": self.s.html(
                            repo, "tree" if kind == "dir" else "blob", rev, entry_path
                        ),
                        **(
                            {
                                "download_url": f"{self.s.base}/workplace/api/github/raw/{repo.full_name}/{rev}/{entry_path}"
                            }
                            if kind == "file"
                            else {}
                        ),
                    }
                )
            fields = _strings(args, "fields")
            if fields:
                listing = [{k: v for k, v in e.items() if k in fields} for e in listing]
            return _json(listing)
        found = bare.blob(rev, rel)
        if found is None:
            raise ToolError(
                "Failed to get file contents. The path does not point to a file or directory, or the file does not exist in the repository."
            )
        blob_sha, content = found
        parts = "/".join(rel.split("/"))
        if sha:
            uri = f"repo://{repo.full_name}/sha/{sha}/contents/{parts}"
        elif ref_full.startswith("refs/heads/"):
            uri = f"repo://{repo.full_name}/refs/heads/{ref_full[11:]}/contents/{parts}"
        elif ref_full.startswith("refs/tags/"):
            uri = f"repo://{repo.full_name}/refs/tags/{ref_full[10:]}/contents/{parts}"
        else:
            uri = f"repo://{repo.full_name}/contents/{parts}"
        if not content:
            resource = types.TextResourceContents(
                uri=uri, text="", mimeType="text/plain"
            )
            message = f"successfully downloaded empty file (SHA: {blob_sha}){note}"
        elif b"\x00" not in content[:8000]:
            text = content.decode("utf-8", errors="replace")
            mime = (
                "text/html; charset=utf-8"
                if text.lstrip().lower().startswith(("<!doctype html", "<html"))
                else "text/plain; charset=utf-8"
            )
            resource = types.TextResourceContents(uri=uri, text=text, mimeType=mime)
            message = f"successfully downloaded text file (SHA: {blob_sha}){note}"
        else:
            resource = types.BlobResourceContents(
                uri=uri,
                blob=base64.b64encode(content).decode(),
                mimeType="application/octet-stream",
            )
            message = f"successfully downloaded binary file (SHA: {blob_sha}){note}"
        return [
            types.TextContent(type="text", text=message),
            types.EmbeddedResource(type="resource", resource=resource),
        ]

    def _list_branches(self, user: User, args):
        repo = self._repo(args, "failed to list branches", "/branches")
        branches = sorted(repo.bare.branches().items())
        return _json(
            [
                {"name": n, "sha": s, "protected": n in repo.protected}
                for n, s in _page(branches, args)
            ]
        )

    def _list_commits(self, user: User, args):
        repo = self._repo(args, "failed to list commits", "/commits")
        rev = _str(args, "sha", required=False) or repo.default_branch
        if repo.bare.ref_sha(rev) is None:
            raise ToolError(
                f"failed to list commits: GET {self.s.api}/repos/{repo.full_name}/commits?sha={rev}: 404 No commit found for SHA: {rev} []"
            )
        page = _int(args, "page", required=False, default=1) or 1
        per_page = min(_int(args, "perPage", required=False, default=30) or 30, 100)
        commits = repo.bare.commits(
            rev,
            path=_str(args, "path", required=False) or None,
            max_count=per_page,
            skip=(page - 1) * per_page,
            author=_str(args, "author", required=False) or None,
            since=_str(args, "since", required=False) or None,
            until=_str(args, "until", required=False) or None,
        )
        items = [self.s.minimal_commit(repo, c) for c in commits]
        fields = _strings(args, "fields")
        if fields:
            items = [{k: v for k, v in item.items() if k in fields} for item in items]
        return _json(items)

    def _get_commit(self, user: User, args):
        repo = self._repo(args, "failed to get commit")
        ref = _str(args, "sha")
        detail = _str(args, "detail", required=False) or "stats"
        if detail not in ("none", "stats", "full_patch"):
            raise ToolError(
                f'invalid detail "{detail}": must be one of "none", "stats", "full_patch"'
            )
        sha = repo.bare.ref_sha(ref)
        commit = repo.bare.commit(sha) if sha else None
        if commit is None:
            raise ToolError(
                f"failed to get commit: {ref}: GET {self.s.api}/repos/{repo.full_name}/commits/{ref}: 422 No commit found for SHA: {ref} []"
            )
        if detail == "none":
            return _json(self.s.minimal_commit(repo, commit))
        parent = commit.parents[0] if commit.parents else None
        changes = (
            repo.bare.changes(parent, commit.sha, three_dot=False)
            if parent
            else [
                _added(repo, commit.sha, path) for path in repo.bare.files(commit.sha)
            ]
        )
        files = []
        for f in _page(changes, args):
            entry = {
                "filename": f.filename,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
                "changes": f.changes,
            }
            if detail == "full_patch":
                entry["patch"] = f.patch
            files.append(
                {k: v for k, v in entry.items() if v not in (0, "") or k == "filename"}
            )
        return _json(self.s.minimal_commit(repo, commit, files=files))

    def _create_branch(self, user: User, args):
        repo = self._repo(args, "failed to create branch")
        branch = _str(args, "branch")
        source = _str(args, "from_branch", required=False) or repo.default_branch
        try:
            sha = self.state.create_branch(repo, user.login, branch, source)
        except GitHubError as exc:
            raise self.api_error(
                "failed to create branch",
                "POST",
                f"repos/{repo.full_name}/git/refs",
                exc,
            )
        return _json(self._ref(repo, branch, sha))

    def _ref(self, repo: Repo, branch: str, sha: str) -> Dict[str, Any]:
        return {
            "ref": f"refs/heads/{branch}",
            "url": f"{self.s.api}/repos/{repo.full_name}/git/refs/heads/{branch}",
            "object": {
                "type": "commit",
                "sha": sha,
                "url": f"{self.s.api}/repos/{repo.full_name}/git/commits/{sha}",
            },
            "node_id": f"REF_kwDO{abs(hash((repo.full_name, branch))) % 10**8:08d}",
        }

    def _push_files(self, user: User, args):
        repo = self._repo(args, "failed to push files")
        branch = _str(args, "branch")
        message = _str(args, "message")
        files = args.get("files")
        if not isinstance(files, list):
            raise ToolError(
                "files parameter must be an array of objects with path and content"
            )
        entries = []
        for f in files:
            if not isinstance(f, dict) or not f.get("path"):
                raise ToolError("each file must have a path")
            if "content" not in f:
                raise ToolError("each file must have content")
            entries.append((str(f["path"]).lstrip("/"), str(f["content"]).encode()))
        if repo.bare.ref_sha(f"refs/heads/{branch}") is None:
            self.state.create_branch(repo, user.login, branch, repo.default_branch)
        try:
            sha = self.state.write_files(repo, user.login, branch, entries, message)
        except GitHubError as exc:
            raise self.api_error(
                "failed to update reference",
                "PATCH",
                f"repos/{repo.full_name}/git/refs/heads/{branch}",
                exc,
            )
        return _json(self._ref(repo, branch, sha))

    def _create_or_update_file(self, user: User, args):
        repo = self._repo(args, "failed to create/update file")
        path = _str(args, "path").lstrip("/")
        content = args.get("content")
        if content is None:
            raise ToolError("missing required parameter: content")
        message = _str(args, "message")
        branch = _str(args, "branch")
        given = _str(args, "sha", required=False)
        existing = repo.bare.blob(branch, path)
        if repo.bare.tree(branch, path) is not None:
            raise ToolError(
                f"Path {path} is a directory, not a file. This tool only works with files."
            )
        if existing and not given:
            raise ToolError(
                f"File already exists at {path}. You must provide the current file's SHA when updating. "
                f"Call get_file_contents with owner={repo.owner!r}, repo={repo.name!r}, path={path!r}, and ref={branch!r}; "
                "its first text result reports the blob SHA for the requested path. "
                "Then retry with the sha parameter set to the blob SHA that call reports.".replace(
                    "'", '"'
                )
            )
        if existing and given and existing[0] != given:
            raise ToolError(
                f"SHA mismatch: provided SHA {given} is stale. Current file SHA is {existing[0]}. "
                f'The file changed since you read it. Call get_file_contents with owner="{repo.owner}", repo="{repo.name}", path="{path}", and ref="{branch}"; '
                "its first text result reports the blob SHA for the requested path. "
                "Rebuild your content against what it returns, and retry with the sha parameter set to the SHA that call reports."
            )
        try:
            sha = self.state.write_files(
                repo, user.login, branch, [(path, str(content).encode())], message
            )
        except GitHubError as exc:
            raise self.api_error(
                "failed to create/update file",
                "PUT",
                f"repos/{repo.full_name}/contents/{path}",
                exc,
            )
        blob = repo.bare.blob(sha, path)
        commit = repo.bare.commit(sha)
        return _json(
            {
                "content": {
                    "name": path.rsplit("/", 1)[-1],
                    "path": path,
                    "sha": blob[0] if blob else "",
                    "size": len(blob[1]) if blob else 0,
                    "html_url": self.s.html(repo, "blob", branch, path),
                },
                "commit": {
                    "sha": sha,
                    "message": message,
                    "html_url": self.s.html(repo, "commit", sha),
                    "author": {
                        "name": commit.author_name,
                        "email": commit.author_email,
                        "date": commit.author_date,
                    },
                },
            }
        )

    def _delete_file(self, user: User, args):
        repo = self._repo(args, "failed to delete file")
        path = _str(args, "path").lstrip("/")
        message = _str(args, "message")
        branch = _str(args, "branch")
        if repo.bare.blob(branch, path) is None:
            raise ToolError(
                f"failed to get file SHA: GET {self.s.api}/repos/{repo.full_name}/contents/{path}?ref={branch}: 404 Not Found []"
            )
        try:
            sha = self.state.write_files(
                repo, user.login, branch, [(path, None)], message
            )
        except GitHubError as exc:
            raise self.api_error(
                "failed to delete file",
                "DELETE",
                f"repos/{repo.full_name}/contents/{path}",
                exc,
            )
        commit = repo.bare.commit(sha)
        return _json(
            {
                "commit": {
                    "sha": sha,
                    "message": message,
                    "html_url": self.s.html(repo, "commit", sha),
                    "author": {
                        "name": commit.author_name,
                        "email": commit.author_email,
                        "date": commit.author_date,
                    },
                },
                "content": None,
            }
        )

    def _search_code(self, user: User, args):
        query = _str(args, "query", required=False)
        if not query:
            raise ToolError("missing required parameter: query")
        quals = dict(
            re.findall(r"(repo|path|language|filename|extension):(\S+)", query)
        )
        text = (
            re.sub(r"\b(repo|path|language|filename|extension|org|user):\S+", "", query)
            .strip()
            .strip('"')
        )
        hits = []
        for repo in self.state.repos.values():
            if "repo" in quals and repo.full_name.lower() != quals["repo"].lower():
                continue
            for path, number, line in (
                repo.bare.grep(repo.default_branch, text) if text else []
            ):
                if "path" in quals and not fnmatch.fnmatch(
                    path, quals["path"].strip("/") + "*"
                ):
                    continue
                if "filename" in quals and path.rsplit("/", 1)[-1] != quals["filename"]:
                    continue
                if "extension" in quals and not path.endswith("." + quals["extension"]):
                    continue
                hits.append((repo, path, line))
        files: Dict[Tuple[str, str], List[str]] = {}
        for repo, path, line in hits:
            files.setdefault((repo.full_name, path), []).append(line)
        items = []
        for (full_name, path), lines in files.items():
            repo = self.state.repos[full_name]
            blob = repo.bare.blob(repo.default_branch, path)
            items.append(
                {
                    "name": path.rsplit("/", 1)[-1],
                    "path": path,
                    "sha": blob[0] if blob else "",
                    "repository": full_name,
                    "text_matches": [{"fragment": "\n".join(lines[:3])}],
                }
            )
        return _json(
            {
                "total_count": len(items),
                "incomplete_results": False,
                "items": _page(items, args),
            }
        )

    def _search_commits(self, user: User, args):
        query = _str(args, "query", required=False)
        if not query:
            raise ToolError("missing required parameter: query")
        quals = dict(re.findall(r"(repo|author):(\S+)", query))
        text = re.sub(r"\b(repo|author|org|user):\S+", "", query).strip().lower()
        items = []
        for repo in self.state.repos.values():
            if "repo" in quals and repo.full_name.lower() != quals["repo"].lower():
                continue
            for commit in repo.bare.commits(repo.default_branch, max_count=200):
                if text and text not in commit.message.lower():
                    continue
                item = self.s.minimal_commit(repo, commit)
                item["repository"] = {
                    "full_name": repo.full_name,
                    "html_url": self.s.html(repo),
                }
                items.append(item)
        return _json(
            {
                "total_count": len(items),
                "incomplete_results": False,
                "items": _page(items, args),
            }
        )

    def _search_repositories(self, user: User, args):
        query = _str(args, "query", required=False)
        if not query:
            raise ToolError("missing required parameter: query")
        words = [w.lower() for w in re.sub(r"\b\w+:\S+", "", query).split()]
        owner = (re.search(r"\b(?:org|user):(\S+)", query) or [None, None])[1]
        repos = [
            r
            for r in self.state.repos.values()
            if (not owner or r.owner == owner)
            and all(w in (r.full_name + " " + r.description).lower() for w in words)
        ]
        items = []
        for r in _page(repos, args):
            rest = self.s.repo(r)
            items.append(
                {
                    k: v
                    for k, v in {
                        "id": r.id,
                        "name": r.name,
                        "full_name": r.full_name,
                        "description": r.description,
                        "html_url": rest["html_url"],
                        "stargazers_count": 0,
                        "forks_count": 0,
                        "open_issues_count": rest["open_issues_count"],
                        "updated_at": rest["updated_at"],
                        "created_at": rest["created_at"],
                        "private": r.private,
                        "fork": False,
                        "archived": False,
                        "default_branch": r.default_branch,
                    }.items()
                    if v not in ("", None) or k in ("private", "fork", "archived")
                }
            )
        return _json(
            {"total_count": len(repos), "incomplete_results": False, "items": items}
        )

    def _search_users(self, user: User, args):
        query = _str(args, "query", required=False)
        if not query:
            raise ToolError("missing required parameter: query")
        words = [w.lower() for w in re.sub(r"\btype:\S+", "", query).split()]
        people = [
            u
            for u in self.state.users.values()
            if u.type != "Organization"
            and all(w in (u.login + " " + u.name).lower() for w in words)
        ]
        return _json(
            {
                "total_count": len(people),
                "incomplete_results": False,
                "items": [self.s.minimal_user(u.login) for u in _page(people, args)],
            }
        )

    def _list_repository_collaborators(self, user: User, args):
        repo = self._repo(args, "failed to list collaborators", "/collaborators")
        people = [u for u in self.state.users.values() if u.type == "User"]
        return _json(
            [
                {
                    "login": u.login,
                    "id": u.id,
                    "role_name": "admin" if u.login == repo.owner else "write",
                }
                for u in _page(people, args)
            ]
        )

    def _list_tags(self, user: User, args):
        repo = self._repo(args, "failed to list tags", "/tags")
        tags = sorted(
            (ref[len("refs/tags/") :], sha)
            for ref, sha in repo.bare.refs().items()
            if ref.startswith("refs/tags/")
        )
        return _json([{"name": n, "sha": s} for n, s in _page(tags, args)])

    def _get_tag(self, user: User, args):
        repo = self._repo(args, "failed to get tag reference")
        tag = _str(args, "tag")
        raise self.api_error(
            "failed to get tag reference",
            "GET",
            f"repos/{repo.full_name}/git/ref/tags/{tag}",
            GitHubError(404, "Not Found"),
        )

    def _list_releases(self, user: User, args):
        self._repo(args, "failed to list releases", "/releases")
        return _json([])

    def _get_latest_release(self, user: User, args):
        repo = self._repo(args, "failed to get latest release")
        raise self.api_error(
            "failed to get latest release",
            "GET",
            f"repos/{repo.full_name}/releases/latest",
            GitHubError(404, "Not Found"),
        )

    def _get_release_by_tag(self, user: User, args):
        repo = self._repo(args, "failed to get release by tag")
        tag = _str(args, "tag")
        raise self.api_error(
            f"failed to get release by tag: {tag}",
            "GET",
            f"repos/{repo.full_name}/releases/tags/{tag}",
            GitHubError(404, "Not Found"),
        )

    def _create_repository(self, user: User, args):
        raise ToolError(
            f"failed to create repository: POST {self.s.api}/user/repos: 403 Repository creation is not available in the workplace []"
        )

    def _fork_repository(self, user: User, args):
        repo = self._repo(args, "failed to fork repository")
        raise ToolError(
            f"failed to fork repository: POST {self.s.api}/repos/{repo.full_name}/forks: 403 Forking is not available in the workplace []"
        )

    # -- actions -------------------------------------------------------------------

    def _workflow(self, repo: Repo, wf: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": wf["id"],
            "node_id": f"W_kwDO{wf['id']:08d}",
            "name": wf["name"],
            "path": wf["path"],
            "state": "active",
            "created_at": iso(repo.created_at),
            "updated_at": iso(repo.created_at),
            "url": f"{self.s.api}/repos/{repo.full_name}/actions/workflows/{wf['id']}",
            "html_url": self.s.html(repo, "blob", repo.default_branch, wf["path"]),
            "badge_url": self.s.html(repo, "workflows", wf["name"], "badge.svg"),
        }

    def _find_workflow(self, repo: Repo, ref: str) -> Optional[Dict[str, Any]]:
        return next(
            (
                wf
                for wf in self.state.workflows(repo)
                if ref in (str(wf["id"]), wf["path"], wf["path"].rsplit("/", 1)[-1])
            ),
            None,
        )

    def _actions_list(self, user: User, args):
        method = _str(args, "method")
        repo = self._repo(args, "failed to list workflows", "/actions/workflows")
        resource = str(args.get("resource_id") or "")
        if method == "list_workflows":
            workflows = [self._workflow(repo, wf) for wf in self.state.workflows(repo)]
            return _json({"total_count": len(workflows), "workflows": workflows})
        if method == "list_workflow_runs":
            runs = list(reversed(repo.runs))
            if resource:
                wf = self._find_workflow(repo, resource)
                if wf is None:
                    raise self.api_error(
                        "failed to list workflow runs",
                        "GET",
                        f"repos/{repo.full_name}/actions/workflows/{resource}/runs",
                        GitHubError(404, "Not Found"),
                    )
                runs = [r for r in runs if r.workflow_id == wf["id"]]
            f = args.get("workflow_runs_filter") or {}
            for key, attr in (
                ("branch", "head_branch"),
                ("event", "event"),
                ("status", "status"),
                ("actor", "actor"),
            ):
                if f.get(key):
                    runs = [
                        r
                        for r in runs
                        if (
                            r.conclusion
                            if key == "status"
                            and f[key] in ("success", "failure", "cancelled")
                            else getattr(r, attr)
                        )
                        == f[key]
                    ]
            return _json(
                {
                    "total_count": len(runs),
                    "workflow_runs": [
                        self.s.minimal_run(repo, r) for r in _page(runs, args)
                    ],
                }
            )
        if method == "list_workflow_jobs":
            try:
                run = self.state.run(repo, int(resource or 0))
            except (GitHubError, ValueError):
                raise self.api_error(
                    "failed to list workflow jobs",
                    "GET",
                    f"repos/{repo.full_name}/actions/runs/{resource}/jobs",
                    GitHubError(404, "Not Found"),
                )
            return _json(
                {
                    "total_count": len(run.jobs),
                    "jobs": [self.s.minimal_job(repo, run, j) for j in run.jobs],
                }
            )
        if method == "list_workflow_run_artifacts":
            return _json({"total_count": 0, "artifacts": []})
        raise ToolError(f"unknown method: {method}")

    def _actions_get(self, user: User, args):
        method = _str(args, "method")
        repo = self._repo(args, "failed to get workflow")
        resource = _str(args, "resource_id")
        if method == "get_workflow":
            wf = self._find_workflow(repo, resource)
            if wf is None:
                raise self.api_error(
                    "failed to get workflow",
                    "GET",
                    f"repos/{repo.full_name}/actions/workflows/{resource}",
                    GitHubError(404, "Not Found"),
                )
            return _json(self._workflow(repo, wf))
        try:
            run_or_job = int(resource)
        except ValueError:
            raise ToolError(
                f"invalid resource_id, must be an integer for method {method}"
            )
        if method == "get_workflow_run":
            try:
                return _json(self.s.minimal_run(repo, self.state.run(repo, run_or_job)))
            except GitHubError as exc:
                raise self.api_error(
                    "failed to get workflow run",
                    "GET",
                    f"repos/{repo.full_name}/actions/runs/{run_or_job}",
                    exc,
                )
        if method == "get_workflow_job":
            try:
                run, job = self.state.job(repo, run_or_job)
            except GitHubError as exc:
                raise self.api_error(
                    "failed to get workflow job",
                    "GET",
                    f"repos/{repo.full_name}/actions/jobs/{run_or_job}",
                    exc,
                )
            return _json(self.s.minimal_job(repo, run, job))
        if method == "get_workflow_run_logs_url":
            run = self.state.run(repo, run_or_job)
            return _json(
                {
                    "logs_url": f"{self.s.base}/workplace/api/github/logs/{repo.full_name}/{run.id}",
                    "message": "Workflow run logs are available for download",
                    "note": "The logs_url provides a download link for the complete workflow run logs as a ZIP archive. The archive contains separate log files for each job in the workflow run.",
                }
            )
        if method == "get_workflow_run_usage":
            return _json({"billable": {}, "run_duration_ms": 0})
        if method == "download_workflow_run_artifact":
            raise self.api_error(
                "failed to get artifact download URL",
                "GET",
                f"repos/{repo.full_name}/actions/artifacts/{run_or_job}/zip",
                GitHubError(404, "Not Found"),
            )
        raise ToolError(f"unknown method: {method}")

    def _actions_run_trigger(self, user: User, args):
        method = _str(args, "method")
        repo = self._repo(args, "failed to run workflow")
        if method == "run_workflow":
            workflow_id = str(args.get("workflow_id") or "")
            ref = str(args.get("ref") or repo.default_branch)
            try:
                self.state.dispatch(
                    repo, workflow_id, ref, user.login, args.get("inputs") or {}
                )
            except GitHubError as exc:
                raise ToolError(
                    f"failed to run workflow: POST {self.s.api}/repos/{repo.full_name}/actions/workflows/{workflow_id}/dispatches: {exc.status} {exc.message} []"
                )
            return _json(
                {
                    "message": "Workflow run has been queued",
                    "workflow_type": "workflow_id",
                    "workflow": workflow_id,
                    "ref": ref,
                    "inputs": args.get("inputs") or {},
                }
            )
        run_id = _int(args, "run_id")
        try:
            run = self.state.run(repo, run_id)
        except GitHubError as exc:
            raise self.api_error(
                f"failed to {method.replace('_', ' ')}",
                "POST",
                f"repos/{repo.full_name}/actions/runs/{run_id}",
                exc,
            )
        if method in ("rerun_workflow_run", "rerun_failed_jobs"):
            try:
                self.state.rerun(repo, run, user.login)
            except GitHubError as exc:
                raise ToolError(
                    f"failed to rerun workflow run: POST {self.s.api}/repos/{repo.full_name}/actions/runs/{run_id}/rerun: {exc.status} {exc.message} []"
                )
            message = (
                "Workflow run has been queued for re-run"
                if method == "rerun_workflow_run"
                else "Failed jobs have been queued for re-run"
            )
            return _json(
                {
                    "message": message,
                    "run_id": run_id,
                    "status": "Created",
                    "status_code": 201,
                }
            )
        if method == "cancel_workflow_run":
            try:
                self.state.cancel_run(repo, run)
            except GitHubError as exc:
                raise ToolError(
                    f"failed to cancel workflow run: POST {self.s.api}/repos/{repo.full_name}/actions/runs/{run_id}/cancel: {exc.status} {exc.message} []"
                )
            return _json(
                {
                    "message": "Workflow run has been cancelled",
                    "run_id": run_id,
                    "status": "Accepted",
                    "status_code": 202,
                }
            )
        if method == "delete_workflow_run_logs":
            for job in run.jobs:
                job.log = ""
            return _json(
                {
                    "message": "Workflow run logs have been deleted",
                    "run_id": run_id,
                    "status": "No Content",
                    "status_code": 204,
                }
            )
        raise ToolError(f"unknown method: {method}")

    def _get_job_logs(self, user: User, args):
        repo = self._repo(args, "failed to get job logs")
        job_id = _int(args, "job_id", required=False)
        run_id = _int(args, "run_id", required=False)
        failed_only = bool(args.get("failed_only"))
        content = bool(args.get("return_content"))
        tail = _int(args, "tail_lines", required=False, default=500) or 500
        if failed_only and not run_id:
            raise ToolError("run_id is required when failed_only is true")
        if not failed_only and not job_id:
            raise ToolError("job_id is required when failed_only is false")

        def one(run, job, name: str):
            if content:
                lines = job.log.split("\n")
                return {
                    "job_id": job.id,
                    **({"job_name": name} if name else {}),
                    "logs_content": "\n".join(lines[-tail:]),
                    "message": "Job logs content retrieved successfully",
                    "original_length": len(lines),
                }
            return {
                "job_id": job.id,
                **({"job_name": name} if name else {}),
                "logs_url": f"{self.s.base}/workplace/api/github/logs/{repo.full_name}/{run.id}/{job.id}",
                "message": "Job logs are available for download",
                "note": "The logs_url provides a download link for the individual job logs in plain text format. Use return_content=true to get the actual log content.",
            }

        if failed_only:
            try:
                run = self.state.run(repo, run_id)
            except GitHubError as exc:
                raise self.api_error(
                    "failed to list workflow jobs",
                    "GET",
                    f"repos/{repo.full_name}/actions/runs/{run_id}/jobs",
                    exc,
                )
            failed = [j for j in run.jobs if j.conclusion == "failure"]
            if not failed:
                return _json(
                    {
                        "failed_jobs": 0,
                        "message": "No failed jobs found in this workflow run",
                        "run_id": run_id,
                        "total_jobs": len(run.jobs),
                    }
                )
            return _json(
                {
                    "failed_jobs": len(failed),
                    "logs": [one(run, j, j.name) for j in failed],
                    "message": f"Retrieved logs for {len(failed)} failed jobs",
                    "return_format": {"content": content, "urls": not content},
                    "run_id": run_id,
                    "total_jobs": len(run.jobs),
                }
            )
        try:
            run, job = self.state.job(repo, job_id)
        except GitHubError as exc:
            raise self.api_error(
                "failed to get job logs",
                "GET",
                f"repos/{repo.full_name}/actions/jobs/{job_id}/logs",
                exc,
            )
        return _json(one(run, job, ""))

    def _ui_get(self, user: User, args):
        raise ToolError("ui_get is only available to MCP Apps hosts")


def _file(f) -> Dict[str, Any]:
    out = {
        "filename": f.filename,
        "status": f.status,
        "additions": f.additions,
        "deletions": f.deletions,
        "changes": f.changes,
        "patch": f.patch,
        "previous_filename": f.previous_filename or "",
    }
    return {k: v for k, v in out.items() if v not in (0, "", None) or k == "filename"}


def _added(repo: Repo, sha: str, path: str):
    from .repos import FileChange

    blob = repo.bare.blob(sha, path)
    lines = blob[1].decode(errors="replace").splitlines() if blob else []
    patch = f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join("+" + line for line in lines)
    return FileChange(path, "added", len(lines), 0, patch)


def _display(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text
