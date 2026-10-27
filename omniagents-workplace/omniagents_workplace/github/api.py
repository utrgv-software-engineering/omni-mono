"""The UI's GitHub: ``/workplace/api/github/...``.

What the people in the workplace see and do on GitHub, as themselves:
browse code, branches and commits; open, edit and review issues and pull
requests; manage labels and a repository's settings; follow, dispatch,
re-run and cancel workflow runs. Everything goes through the same state the
MCP server and webhooks use.
"""

from __future__ import annotations

import html
import time
import zlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from ..accounts import admin, me
from .repos import NULL_SHA
from .serialize import Serializer
from .state import GitHubError, GitHubState, Issue, Repo, Webhook, iso
from .webhooks import WebhookDelivery

__all__ = ["github_ui_router"]


class NewIssue(BaseModel):
    title: str
    body: str = ""
    labels: List[str] = []
    assignees: List[str] = []


class EditIssue(BaseModel):
    #: A pull request's: ``False`` marks it ready for review, ``True`` converts it to a draft.
    draft: Optional[bool] = None
    title: Optional[str] = None
    body: Optional[str] = None
    state: Optional[str] = None
    state_reason: Optional[str] = None
    labels: Optional[List[str]] = None
    assignees: Optional[List[str]] = None


class NewComment(BaseModel):
    body: str


class ReviewCommentIn(BaseModel):
    path: str
    line: int
    side: str = "RIGHT"
    body: str


class NewReview(BaseModel):
    event: str  # APPROVE, REQUEST_CHANGES, COMMENT
    body: str = ""
    comments: List[ReviewCommentIn] = []


class Merge(BaseModel):
    method: str = "squash"
    #: An admin's "merge without waiting for requirements to be met".
    bypass: bool = False


class NewRepo(BaseModel):
    name: str
    description: str = ""
    private: bool = False
    auto_init: bool = True
    protect_default_branch: bool = False


class NewPull(BaseModel):
    title: str
    head: str
    base: str
    body: str = ""
    draft: bool = False


class RepoSettings(BaseModel):
    description: Optional[str] = None
    default_branch: Optional[str] = None
    protected: Optional[List[str]] = None
    self_hosted: Optional[bool] = None
    #: Per protected branch, the checks a pull request into it must pass.
    required_checks: Optional[Dict[str, List[str]]] = None


class DeleteRepo(BaseModel):
    #: The repository's full name, typed to confirm.
    confirm: str


class LabelIn(BaseModel):
    name: str
    color: str = "ededed"
    description: str = ""


class LabelEdit(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None


class EditComment(BaseModel):
    body: str


class Reaction(BaseModel):
    content: str


class Resolve(BaseModel):
    resolved: bool = True


class RestoreBranch(BaseModel):
    branch: str
    sha: str


class Reviewers(BaseModel):
    reviewers: List[str]


class Dispatch(BaseModel):
    ref: str = ""
    inputs: Dict[str, Any] = {}


class WebhookIn(BaseModel):
    url: str
    secret: str = ""


def _stamp(commit: Optional[Dict[str, Any]]) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(commit["date"]).timestamp() if commit else 0.0
    except (KeyError, ValueError):
        return 0.0


def _fail(exc: GitHubError):
    # GitHub's "Validation Failed" says why in its errors.
    reasons = [e.get("message") for e in exc.errors if e.get("message")]
    detail = (
        "; ".join(reasons)
        if exc.message == "Validation Failed" and reasons
        else exc.message
    )
    raise HTTPException(
        status_code=exc.status if exc.status != 405 else 409, detail=detail
    )


def github_ui_router(
    state: GitHubState, s: Serializer, hooks: WebhookDelivery
) -> APIRouter:
    r = APIRouter(prefix="/workplace/api/github")

    def repo_of(owner: str, name: str) -> Repo:
        try:
            return state.repo(owner, name)
        except GitHubError as exc:
            _fail(exc)

    def issue_of(repo: Repo, number: int) -> Issue:
        try:
            return state.issue(repo, number)
        except GitHubError as exc:
            _fail(exc)

    def summary(repo: Repo, issue: Issue) -> Dict[str, Any]:
        out = {
            "number": issue.number,
            "title": issue.title,
            "state": issue.state,
            "user": issue.user,
            "labels": issue.labels,
            "assignees": issue.assignees,
            "comments": len(issue.comments),
            "created_at": iso(issue.created_at),
            "updated_at": iso(issue.updated_at),
            "is_pull": bool(issue.pull),
            "html_url": s.issue_html(repo, issue),
        }
        if issue.pull:
            out.update(
                draft=issue.pull.draft,
                merged=issue.pull.merged,
                head=issue.pull.head,
                base=issue.pull.base,
            )
        return out

    @r.get("/repos")
    def repos():
        return {
            "repositories": [
                {
                    "full_name": repo.full_name,
                    "description": repo.description,
                    "default_branch": repo.default_branch,
                    "clone_url": s.clone_url(repo),
                    "branches": [
                        {
                            "name": n,
                            "sha": sha,
                            "subject": (
                                repo.bare.commit(sha).subject
                                if repo.bare.commit(sha)
                                else ""
                            ),
                        }
                        for n, sha in sorted(repo.bare.branches().items())
                    ],
                    "open_issues": sum(
                        1
                        for i in repo.issues.values()
                        if not i.pull and i.state == "open"
                    ),
                    "open_pulls": sum(
                        1 for i in repo.issues.values() if i.pull and i.state == "open"
                    ),
                    "labels": [
                        {
                            "name": label.name,
                            "color": label.color,
                            "description": label.description,
                        }
                        for label in repo.labels.values()
                    ],
                    "protected": list(repo.protected),
                    "self_hosted": repo.self_hosted,
                    "required_checks": dict(repo.required_checks),
                    # The checks seen on its branches, to choose required ones from.
                    "check_names": sorted({j.name for r in repo.runs for j in r.jobs}),
                }
                for repo in state.repos.values()
            ],
            "users": [
                {"login": u.login, "name": u.name, "slack": u.slack, "type": u.type}
                for u in state.users.values()
                if u.type != "Organization" and not u.suspended
            ],
        }

    @r.post("/repos")
    def create_repo(body: NewRepo, request: Request):
        actor = me(request).github_login
        org = state.organization()
        if org is None:
            raise HTTPException(409, "The workplace has no GitHub organization")
        try:
            repo = state.create_repo(
                org.login,
                body.name.strip(),
                actor,
                description=body.description,
                private=body.private,
                auto_init=body.auto_init,
                protect_default_branch=body.protect_default_branch,
            )
        except GitHubError as exc:
            _fail(exc)
        return {"full_name": repo.full_name, "clone_url": s.clone_url(repo)}

    @r.get("/repos/{owner}/{name}/tree")
    def tree(owner: str, name: str, ref: str = "", path: str = ""):
        repo = repo_of(owner, name)
        rev = ref or repo.default_branch
        if repo.bare.ref_sha(rev) is None:
            raise HTTPException(404, "No such branch")
        path = path.strip("/")
        entries = repo.bare.tree(rev, path)
        last = repo.bare.commits(rev, path=path or None, max_count=1)
        if entries is not None:
            readme = next(
                (
                    n
                    for kind, n, *_ in entries
                    if kind == "file" and n.lower().startswith("readme")
                ),
                None,
            )
            readme_text = None
            if readme:
                blob = repo.bare.blob(rev, f"{path}/{readme}" if path else readme)
                readme_text = blob[1].decode(errors="replace") if blob else None
            changed = repo.bare.last_commits(rev, path, [n for _, n, *_ in entries])
            return {
                "type": "dir",
                "ref": rev,
                "path": path,
                "entries": [
                    {
                        "type": kind,
                        "name": n,
                        "path": f"{path}/{n}" if path else n,
                        "size": size,
                        "last_commit": (
                            _commit(repo, changed[n]) if n in changed else None
                        ),
                    }
                    for kind, n, _, size in sorted(
                        entries, key=lambda e: (e[0] != "dir", e[1].lower())
                    )
                ],
                "readme": readme_text,
                "last_commit": _commit(repo, last[0]) if last else None,
            }
        blob = repo.bare.blob(rev, path)
        if blob is None:
            raise HTTPException(404, "Not found")
        binary = b"\x00" in blob[1][:8000]
        return {
            "type": "file",
            "ref": rev,
            "path": path,
            "size": len(blob[1]),
            "binary": binary,
            "content": None if binary else blob[1].decode(errors="replace"),
            "last_commit": _commit(repo, last[0]) if last else None,
        }

    @r.get("/repos/{owner}/{name}/commits")
    def commits(owner: str, name: str, ref: str = ""):
        repo = repo_of(owner, name)
        return {
            "commits": [
                _commit(repo, c)
                for c in repo.bare.commits(ref or repo.default_branch, max_count=100)
            ]
        }

    @r.get("/repos/{owner}/{name}/commits/{sha}")
    def commit(owner: str, name: str, sha: str):
        repo = repo_of(owner, name)
        found = repo.bare.commit(sha)
        if found is None:
            raise HTTPException(404, "No commit found")
        parent = found.parents[0] if found.parents else None
        files = repo.bare.changes(parent, found.sha, three_dot=False) if parent else []
        return {**_commit(repo, found), "files": [_file(f) for f in files]}

    @r.get("/repos/{owner}/{name}/recent-pushes")
    def recent_pushes(owner: str, name: str, request: Request):
        """Branches the signed-in person pushed in the last hour that have no
        open pull request: GitHub's "had recent pushes" prompt."""
        repo = repo_of(owner, name)
        login = me(request).github_login
        branches = repo.bare.branches()
        open_heads = {i.pull.head for i in repo.pulls() if i.state == "open"}
        recent = [
            {"branch": branch, "at": iso(push["at"])}
            for branch, push in repo.pushes.items()
            if push.get("actor") == login
            and branch in branches
            and branch != repo.default_branch
            and branch not in open_heads
            and time.time() - float(push.get("at") or 0) < 3600
        ]
        return {"pushes": sorted(recent, key=lambda p: p["at"], reverse=True)}

    @r.get("/repos/{owner}/{name}/compare")
    def compare(owner: str, name: str, base: str, head: str):
        """What a pull request from ``head`` into ``base`` would hold."""
        repo = repo_of(owner, name)
        base_sha, head_sha = repo.bare.ref_sha(base), repo.bare.ref_sha(head)
        if base_sha is None or head_sha is None:
            raise HTTPException(404, "No such branch")
        existing = next(
            (
                i.number
                for i in repo.pulls()
                if i.state == "open" and i.pull.head == head and i.pull.base == base
            ),
            None,
        )
        commits = repo.bare.commits(f"{base_sha}..{head_sha}", max_count=250)
        return {
            "base": base,
            "head": head,
            "commits": [_commit(repo, c) for c in reversed(commits)],
            "files": (
                [_file(f) for f in repo.bare.changes(base_sha, head_sha)]
                if commits
                else []
            ),
            "mergeable": repo.bare.mergeable(base_sha, head_sha),
            "existing": existing,
        }

    @r.post("/repos/{owner}/{name}/pulls")
    def create_pull(owner: str, name: str, body: NewPull, request: Request):
        actor = me(request).github_login
        repo = repo_of(owner, name)
        if not body.title.strip():
            raise HTTPException(422, "A pull request has a title")
        try:
            pull = state.create_pull(
                repo,
                actor,
                body.title.strip(),
                body.head,
                body.base,
                body.body,
                draft=body.draft,
            )
        except GitHubError as exc:
            _fail(exc)
        return summary(repo, pull)

    @r.get("/repos/{owner}/{name}/issues")
    def issues(owner: str, name: str, kind: str = "issue", state_filter: str = "open"):
        repo = repo_of(owner, name)
        items = [
            i
            for i in repo.issues.values()
            if (kind == "pull") == bool(i.pull)
            and (state_filter == "all" or i.state == state_filter)
        ]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return {"items": [summary(repo, i) for i in items]}

    @r.get("/repos/{owner}/{name}/issues/{number}")
    def issue(owner: str, name: str, number: int, request: Request):
        login = me(request).github_login
        repo = repo_of(owner, name)
        item = issue_of(repo, number)

        def reactions(target) -> Dict[str, Any]:
            return {
                "reactions": {k: len(v) for k, v in target.reactions.items() if v},
                "reacted": [k for k, v in target.reactions.items() if login in v],
            }

        timeline: List[Dict[str, Any]] = [
            {
                "type": "comment",
                "id": c.id,
                "user": c.user,
                "body": c.body,
                "at": iso(c.created_at),
                "edited": c.updated_at > c.created_at,
                **reactions(c),
            }
            for c in item.comments
        ]
        out: Dict[str, Any] = {
            **summary(repo, item),
            "body": item.body,
            "state_reason": item.state_reason,
            "closed_by": item.closed_by,
            "closed_at": iso(item.closed_at),
            **reactions(item),
        }
        if item.pull:
            p = item.pull
            for review in p.reviews:
                if review.state == "PENDING":
                    continue
                comments = [
                    {
                        "id": c.id,
                        "user": c.user,
                        "body": c.body,
                        "path": c.path,
                        "line": c.line,
                        "side": c.side,
                        "in_reply_to": c.in_reply_to_id,
                        "thread": c.thread_id,
                        "at": iso(c.created_at),
                    }
                    for t in p.threads
                    for c in t.comments
                    if c.review_id == review.id
                ]
                timeline.append(
                    {
                        "type": "review",
                        "id": review.id,
                        "user": review.user,
                        "state": review.state,
                        "body": review.body,
                        "at": iso(review.submitted_at),
                        "comments": comments,
                    }
                )
            head = state.head_sha(repo, item)
            base = state.base_sha(repo, item)
            runs = state.runs_for(repo, head)
            out.update(
                head_sha=head,
                head_exists=repo.bare.ref_sha(f"refs/heads/{p.head}") is not None,
                behind_by=(
                    repo.bare.ahead_behind(base, head)[1]
                    if head != NULL_SHA and base != NULL_SHA
                    else 0
                ),
                mergeable_state=state.mergeable_state(repo, item),
                unmet_checks=[
                    {"name": n, "state": st} for n, st in state.unmet_checks(repo, item)
                ],
                merged_by=p.merged_by,
                requested_reviewers=p.requested_reviewers,
                files=(
                    [_file(f) for f in repo.bare.changes(base, head)]
                    if head != NULL_SHA
                    else []
                ),
                commits=(
                    [
                        _commit(repo, c)
                        for c in reversed(
                            repo.bare.commits(
                                f"{repo.bare.merge_base(base, head) or base}..{head}"
                            )
                        )
                    ]
                    if head != NULL_SHA
                    else []
                ),
                checks=[
                    {
                        "run": run.id,
                        "name": run.name,
                        "status": run.status,
                        "conclusion": run.conclusion,
                        "at": iso(run.updated_at),
                    }
                    for run in runs
                ],
                threads=[
                    {
                        "id": t.id,
                        "path": t.path,
                        "resolved": t.resolved,
                        "comments": [
                            {
                                "id": c.id,
                                "user": c.user,
                                "body": c.body,
                                "line": c.line,
                                "side": c.side,
                                "at": iso(c.created_at),
                            }
                            for c in t.comments
                            if any(
                                rv.id == c.review_id and rv.state != "PENDING"
                                for rv in p.reviews
                            )
                        ],
                    }
                    for t in p.threads
                ],
            )
        timeline.sort(key=lambda e: e["at"] or "")
        out["timeline"] = timeline
        return out

    @r.post("/repos/{owner}/{name}/issues")
    def create_issue(owner: str, name: str, body: NewIssue, request: Request):
        actor = me(request).github_login
        repo = repo_of(owner, name)
        try:
            created = state.create_issue(
                repo, actor, body.title, body.body, body.labels, body.assignees
            )
        except GitHubError as exc:
            _fail(exc)
        return summary(repo, created)

    @r.patch("/repos/{owner}/{name}/issues/{number}")
    def edit_issue(
        owner: str, name: str, number: int, body: EditIssue, request: Request
    ):
        account = me(request)
        actor = account.github_login
        repo = repo_of(owner, name)
        item = issue_of(repo, number)
        if (
            (body.title is not None or body.body is not None)
            and actor != item.user
            and not account.admin
        ):
            raise HTTPException(403, "Only its author or an admin can edit it")
        try:
            if body.draft is not None:
                if not item.pull:
                    raise HTTPException(422, "Only a pull request is a draft")
                state.update_pull(repo, item, actor, draft=body.draft)
            state.update_issue(
                repo,
                item,
                actor,
                title=body.title,
                body=body.body,
                state=body.state,
                state_reason=body.state_reason,
                labels=body.labels,
                assignees=body.assignees,
            )
        except GitHubError as exc:
            _fail(exc)
        return summary(repo, item)

    def _own_comment(repo: Repo, comment_id: int, request: Request):
        account = me(request)
        try:
            item, comment = state.comment(repo, comment_id)
        except GitHubError as exc:
            _fail(exc)
        if comment.user != account.github_login and not account.admin:
            raise HTTPException(403, "Only its author or an admin can change a comment")
        return account, item, comment

    @r.patch("/repos/{owner}/{name}/issues/comments/{comment_id}")
    def edit_comment(
        owner: str, name: str, comment_id: int, body: EditComment, request: Request
    ):
        repo = repo_of(owner, name)
        account, _, _ = _own_comment(repo, comment_id, request)
        if not body.body.strip():
            raise HTTPException(422, "A comment has a body")
        state.edit_comment(repo, comment_id, account.github_login, body.body)
        return {"ok": True}

    @r.delete("/repos/{owner}/{name}/issues/comments/{comment_id}")
    def delete_comment(owner: str, name: str, comment_id: int, request: Request):
        repo = repo_of(owner, name)
        account, _, _ = _own_comment(repo, comment_id, request)
        state.delete_comment(repo, comment_id, account.github_login)
        return {"ok": True}

    def _toggle_reaction(target, login: str, content: str) -> None:
        try:
            if login in (target.reactions.get(content) or []):
                state.unreact(target, login, content)
            else:
                state.react(target, login, content)
        except GitHubError as exc:
            _fail(exc)

    @r.post("/repos/{owner}/{name}/issues/{number}/reactions")
    def react_to_issue(
        owner: str, name: str, number: int, body: Reaction, request: Request
    ):
        """Add the signed-in person's reaction, or take it back."""
        item = issue_of(repo_of(owner, name), number)
        _toggle_reaction(item, me(request).github_login, body.content)
        return {"ok": True}

    @r.post("/repos/{owner}/{name}/issues/comments/{comment_id}/reactions")
    def react_to_comment(
        owner: str, name: str, comment_id: int, body: Reaction, request: Request
    ):
        repo = repo_of(owner, name)
        try:
            _, comment = state.comment(repo, comment_id)
        except GitHubError as exc:
            _fail(exc)
        _toggle_reaction(comment, me(request).github_login, body.content)
        return {"ok": True}

    @r.put("/repos/{owner}/{name}/pulls/{number}/threads/{thread_id}")
    def resolve(
        owner: str,
        name: str,
        number: int,
        thread_id: str,
        body: Resolve,
        request: Request,
    ):
        repo = repo_of(owner, name)
        issue_of(repo, number)
        try:
            state.resolve_thread(thread_id, me(request).github_login, body.resolved)
        except GitHubError as exc:
            _fail(exc)
        return {"ok": True}

    @r.post("/repos/{owner}/{name}/issues/{number}/comments")
    def comment(owner: str, name: str, number: int, body: NewComment, request: Request):
        actor = me(request).github_login
        repo = repo_of(owner, name)
        item = issue_of(repo, number)
        try:
            state.add_comment(repo, item, actor, body.body)
        except GitHubError as exc:
            _fail(exc)
        return {"ok": True}

    @r.get("/repos/{owner}/{name}/branches")
    def branches(owner: str, name: str):
        """Every branch: its last commit, how it compares to the default branch, its pull request."""
        repo = repo_of(owner, name)
        default_sha = repo.bare.ref_sha(repo.default_branch)
        out = []
        for branch, sha in sorted(repo.bare.branches().items()):
            commit = repo.bare.commit(sha)
            ahead, behind = (
                repo.bare.ahead_behind(default_sha, sha)
                if default_sha and branch != repo.default_branch
                else (0, 0)
            )
            pull = next(
                (
                    i
                    for i in sorted(repo.pulls(), key=lambda i: -i.number)
                    if i.pull.head == branch
                ),
                None,
            )
            out.append(
                {
                    "name": branch,
                    "sha": sha,
                    "commit": _commit(repo, commit) if commit else None,
                    "ahead": ahead,
                    "behind": behind,
                    "default": branch == repo.default_branch,
                    "protected": branch in repo.protected,
                    "pull": (
                        {
                            "number": pull.number,
                            "state": pull.state,
                            "merged": pull.pull.merged,
                            "draft": pull.pull.draft,
                        }
                        if pull
                        else None
                    ),
                }
            )
        out.sort(key=lambda b: (not b["default"], -(_stamp(b["commit"]))))
        return {"branches": out}

    @r.patch("/repos/{owner}/{name}/settings")
    def settings(owner: str, name: str, body: RepoSettings, request: Request):
        account = admin(request)
        repo = repo_of(owner, name)
        try:
            state.update_repo(
                repo, account.github_login, **body.model_dump(exclude_none=True)
            )
        except GitHubError as exc:
            _fail(exc)
        return {"ok": True}

    @r.delete("/repos/{owner}/{name}")
    def delete_repo(owner: str, name: str, body: DeleteRepo, request: Request):
        account = admin(request)
        repo = repo_of(owner, name)
        if body.confirm.strip() != repo.full_name:
            raise HTTPException(422, f"Type {repo.full_name} to confirm")
        state.delete_repo(repo, account.github_login)
        return {"ok": True}

    @r.post("/repos/{owner}/{name}/labels")
    def create_label(owner: str, name: str, body: LabelIn, request: Request):
        me(request)
        repo = repo_of(owner, name)
        try:
            label = state.create_label(repo, body.name, body.color, body.description)
        except GitHubError as exc:
            _fail(exc)
        return {
            "name": label.name,
            "color": label.color,
            "description": label.description,
        }

    @r.patch("/repos/{owner}/{name}/labels/{label}")
    def edit_label(
        owner: str, name: str, label: str, body: LabelEdit, request: Request
    ):
        me(request)
        repo = repo_of(owner, name)
        try:
            edited = state.update_label(
                repo,
                label,
                new_name=body.name,
                color=body.color,
                description=body.description,
            )
        except GitHubError as exc:
            _fail(exc)
        return {
            "name": edited.name,
            "color": edited.color,
            "description": edited.description,
        }

    @r.delete("/repos/{owner}/{name}/labels/{label}")
    def delete_label(owner: str, name: str, label: str, request: Request):
        me(request)
        try:
            state.delete_label(repo_of(owner, name), label)
        except GitHubError as exc:
            _fail(exc)
        return {"ok": True}

    @r.post("/repos/{owner}/{name}/pulls/{number}/update-branch")
    def update_branch(owner: str, name: str, number: int, request: Request):
        repo = repo_of(owner, name)
        item = issue_of(repo, number)
        if not item.pull:
            raise HTTPException(404, "Not a pull request")
        try:
            sha = state.update_branch(repo, item, me(request).github_login)
        except GitHubError as exc:
            _fail(exc)
        return {"sha": sha}

    @r.delete("/repos/{owner}/{name}/branches/{branch:path}")
    def delete_branch(owner: str, name: str, branch: str, request: Request):
        actor = me(request).github_login
        repo = repo_of(owner, name)
        try:
            sha = state.delete_branch(repo, actor, branch)
        except GitHubError as exc:
            _fail(exc)
        return {"branch": branch, "sha": sha}

    @r.post("/repos/{owner}/{name}/branches")
    def restore_branch(owner: str, name: str, body: RestoreBranch, request: Request):
        actor = me(request).github_login
        repo = repo_of(owner, name)
        try:
            state.restore_branch(repo, actor, body.branch, body.sha)
        except GitHubError as exc:
            _fail(exc)
        return {"branch": body.branch, "sha": body.sha}

    @r.put("/repos/{owner}/{name}/pulls/{number}/reviewers")
    def set_reviewers(
        owner: str, name: str, number: int, body: Reviewers, request: Request
    ):
        """The people a pull request's review is requested from (added and removed to match)."""
        actor = me(request).github_login
        repo = repo_of(owner, name)
        item = issue_of(repo, number)
        if not item.pull:
            raise HTTPException(404, "Not a pull request")
        wanted = list(dict.fromkeys(body.reviewers))
        try:
            state.remove_review_requests(
                repo,
                item,
                actor,
                [r for r in item.pull.requested_reviewers if r not in wanted],
            )
            state.request_reviewers(
                repo,
                item,
                actor,
                [r for r in wanted if r not in item.pull.requested_reviewers],
            )
        except GitHubError as exc:
            _fail(exc)
        return {"requested_reviewers": item.pull.requested_reviewers}

    @r.post("/repos/{owner}/{name}/pulls/{number}/reviews")
    def review(owner: str, name: str, number: int, body: NewReview, request: Request):
        actor = me(request).github_login
        repo = repo_of(owner, name)
        item = issue_of(repo, number)
        if not item.pull:
            raise HTTPException(404, "Not a pull request")
        try:
            pending = state.pending_review(item, actor) or state.create_review(
                repo, item, actor
            )
            for c in body.comments:
                state.add_review_comment(
                    repo,
                    item,
                    pending,
                    actor,
                    path=c.path,
                    body=c.body,
                    line=c.line,
                    start_line=None,
                    side=c.side,
                )
            state.submit_review(repo, item, actor, pending, body.event, body.body)
        except GitHubError as exc:
            pending = state.pending_review(item, actor)
            if pending is not None:
                state.delete_pending_review(item, pending)
            _fail(exc)
        return {"ok": True}

    @r.post("/repos/{owner}/{name}/pulls/{number}/comments/{comment_id}/replies")
    def reply(
        owner: str,
        name: str,
        number: int,
        comment_id: int,
        body: NewComment,
        request: Request,
    ):
        actor = me(request).github_login
        repo = repo_of(owner, name)
        item = issue_of(repo, number)
        try:
            state.reply_to_review_comment(repo, item, actor, comment_id, body.body)
        except GitHubError as exc:
            _fail(exc)
        return {"ok": True}

    @r.post("/repos/{owner}/{name}/pulls/{number}/merge")
    def merge(owner: str, name: str, number: int, body: Merge, request: Request):
        account = me(request)
        actor = account.github_login
        if body.bypass and not account.admin:
            raise HTTPException(403, "Only an admin can bypass branch protections")
        repo = repo_of(owner, name)
        item = issue_of(repo, number)
        try:
            sha = state.merge_pull(
                repo, item, actor, method=body.method, bypass_protection=body.bypass
            )
        except GitHubError as exc:
            _fail(exc)
        return {"sha": sha}

    @r.get("/repos/{owner}/{name}/runs")
    def runs(owner: str, name: str):
        repo = repo_of(owner, name)
        return {
            "runs": [
                {
                    "id": run.id,
                    "name": run.name,
                    "run_number": run.run_number,
                    "event": run.event,
                    "status": run.status,
                    "conclusion": run.conclusion,
                    "head_branch": run.head_branch,
                    "head_sha": run.head_sha,
                    "title": run.head_message.split("\n", 1)[0],
                    "actor": run.actor,
                    "at": iso(run.created_at),
                    "pull_requests": run.pull_requests,
                    "workflow_id": run.workflow_id,
                    "path": run.path,
                    "run_attempt": run.run_attempt,
                    "jobs": [
                        {
                            "id": j.id,
                            "name": j.name,
                            "status": j.status,
                            "conclusion": j.conclusion,
                            "log": j.log,
                            "steps": [
                                {
                                    "number": st.number,
                                    "name": st.name,
                                    "status": st.status,
                                    "conclusion": st.conclusion,
                                }
                                for st in j.steps
                            ],
                        }
                        for j in run.jobs
                    ],
                }
                for run in reversed(repo.runs)
            ]
        }

    @r.get("/repos/{owner}/{name}/workflows")
    def workflows(owner: str, name: str):
        repo = repo_of(owner, name)
        return {"workflows": state.workflows(repo)}

    @r.post("/repos/{owner}/{name}/workflows/{workflow}/dispatches")
    def dispatch(
        owner: str, name: str, workflow: str, body: Dispatch, request: Request
    ):
        repo = repo_of(owner, name)
        try:
            run = state.dispatch(
                repo,
                workflow,
                body.ref or repo.default_branch,
                me(request).github_login,
                body.inputs,
            )
        except GitHubError as exc:
            _fail(exc)
        return {"run": run.id}

    @r.post("/repos/{owner}/{name}/runs/{run_id}/rerun")
    def rerun(owner: str, name: str, run_id: int, request: Request):
        repo = repo_of(owner, name)
        try:
            state.rerun(repo, state.run(repo, run_id), me(request).github_login)
        except GitHubError as exc:
            _fail(exc)
        return {"ok": True}

    @r.post("/repos/{owner}/{name}/runs/{run_id}/cancel")
    def cancel(owner: str, name: str, run_id: int, request: Request):
        me(request)
        repo = repo_of(owner, name)
        try:
            state.cancel_run(repo, state.run(repo, run_id))
        except GitHubError as exc:
            _fail(exc)
        return {"ok": True}

    @r.put("/webhook")
    def webhook(body: WebhookIn, request: Request):
        admin(request)
        state.hooks = (
            [
                Webhook(
                    url=body.url,
                    secret=body.secret
                    or (state.hooks[0].secret if state.hooks else ""),
                    id=900000,
                )
            ]
            if body.url
            else []
        )
        state.changed(hooks=True)
        return {"ok": True}

    @r.get("/raw/{owner}/{name}/{ref}/{path:path}")
    def raw(owner: str, name: str, ref: str, path: str):
        repo = repo_of(owner, name)
        blob = repo.bare.blob(ref, path)
        if blob is None:
            raise HTTPException(404, "Not found")
        return Response(blob[1], media_type="text/plain; charset=utf-8")

    @r.get("/logs/{owner}/{name}/{run_id}/{job_id}")
    def job_log(owner: str, name: str, run_id: int, job_id: int):
        repo = repo_of(owner, name)
        try:
            _, job = state.job(repo, job_id)
        except GitHubError as exc:
            _fail(exc)
        return PlainTextResponse(job.log)

    @r.get("/avatars/{login}")
    def avatar(login: str):
        hue = zlib.crc32(login.encode()) % 360
        letter = html.escape((login[:1] or "?").upper())
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="32" '
            f'fill="hsl({hue},55%,55%)"/><text x="32" y="42" font-size="28" text-anchor="middle" fill="white" '
            f'font-family="sans-serif">{letter}</text></svg>'
        )
        return Response(svg, media_type="image/svg+xml")

    return r


def _commit(repo: Repo, c) -> Dict[str, Any]:
    return {
        "sha": c.sha,
        "message": c.message,
        "subject": c.subject,
        "author": c.author_name,
        "email": c.author_email,
        "date": c.author_date,
        "parents": c.parents,
    }


def _file(f) -> Dict[str, Any]:
    return {
        "filename": f.filename,
        "status": f.status,
        "additions": f.additions,
        "deletions": f.deletions,
        "patch": f.patch,
        "previous_filename": f.previous_filename,
    }
