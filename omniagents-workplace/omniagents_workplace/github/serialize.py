"""GitHub's JSON for the state: REST objects (webhooks carry them) and the
MCP server's trimmed types (``pkg/github/minimal_types.go`` in
github/github-mcp-server).

Links point at the workplace (``base``), laid out as github.com lays them
out (``/acme/api-service/pull/3``), so they open in the workplace's UI.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .state import (
    Comment,
    GitHubState,
    Issue,
    Repo,
    Review,
    ReviewComment,
    ReviewThread,
    User,
    WorkflowRun,
    iso,
)

__all__ = ["Serializer"]


def _drop_empty(d: Dict[str, Any], keep: tuple = ()) -> Dict[str, Any]:
    """Go's ``omitempty``: drop zero values (keys in ``keep`` stay)."""
    return {
        k: v for k, v in d.items() if k in keep or v not in (None, "", 0, [], {}, False)
    }


def _reactions_count(reactions: Dict[str, List[str]]) -> Dict[str, int]:
    counts = {
        k: len(reactions.get(k, []))
        for k in ("+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes")
    }
    return {"total_count": sum(counts.values()), **counts}


class Serializer:
    def __init__(self, state: GitHubState, base: Callable[[], str]):
        self.state = state
        self._base = base

    @property
    def base(self) -> str:
        return self._base().rstrip("/")

    @property
    def api(self) -> str:
        """Where the API's URLs point: the workplace's GitHub (as GitHub
        Enterprise's are under its own host)."""
        return f"{self.base}/github/api"

    def clone_url(self, repo: Repo) -> str:
        return f"{self.base}/github/{repo.full_name}.git"

    # -- links --------------------------------------------------------------

    def html(self, repo: Repo, *parts: Any) -> str:
        return "/".join([self.base, repo.full_name, *(str(p) for p in parts)])

    def issue_html(self, repo: Repo, issue: Issue) -> str:
        return self.html(repo, "pull" if issue.pull else "issues", issue.number)

    # -- REST ---------------------------------------------------------------

    def user(self, login: Optional[str]) -> Optional[Dict[str, Any]]:
        if not login:
            return None
        u: User = self.state.user(login)
        return {
            "login": u.login,
            "id": u.id,
            "node_id": u.node_id,
            "avatar_url": f"{self.base}/workplace/api/github/avatars/{u.login}",
            "html_url": f"{self.base}/{u.login}",
            "url": f"{self.api}/users/{u.login}",
            "type": u.type,
            "site_admin": False,
        }

    def repo(self, repo: Repo) -> Dict[str, Any]:
        open_issues = sum(1 for i in repo.issues.values() if i.state == "open")
        return {
            "id": repo.id,
            "node_id": repo.node_id,
            "name": repo.name,
            "full_name": repo.full_name,
            "private": repo.private,
            "owner": self.user(repo.owner),
            "html_url": self.html(repo),
            "description": repo.description,
            "fork": False,
            "url": f"{self.api}/repos/{repo.full_name}",
            "clone_url": self.clone_url(repo),
            "created_at": iso(repo.created_at),
            "updated_at": iso(repo.created_at),
            "pushed_at": iso(repo.created_at),
            "stargazers_count": 0,
            "watchers_count": 0,
            "language": None,
            "forks_count": 0,
            "archived": False,
            "disabled": False,
            "open_issues_count": open_issues,
            "topics": [],
            "visibility": "private" if repo.private else "public",
            "default_branch": repo.default_branch,
        }

    def label(self, repo: Repo, name: str) -> Dict[str, Any]:
        label = repo.labels.get(name)
        return {
            "id": label.id if label else 0,
            "node_id": f"LA_kwDO{label.id if label else 0:08d}",
            "url": f"{self.api}/repos/{repo.full_name}/labels/{name}",
            "name": name,
            "color": label.color if label else "ededed",
            "default": False,
            "description": label.description if label else "",
        }

    def issue(self, repo: Repo, issue: Issue) -> Dict[str, Any]:
        out = {
            "url": f"{self.api}/repos/{repo.full_name}/issues/{issue.number}",
            "repository_url": f"{self.api}/repos/{repo.full_name}",
            "html_url": self.issue_html(repo, issue),
            "id": issue.id,
            "node_id": issue.node_id,
            "number": issue.number,
            "title": issue.title,
            "user": self.user(issue.user),
            "labels": [self.label(repo, n) for n in issue.labels],
            "state": issue.state,
            "locked": False,
            "assignee": self.user(issue.assignees[0]) if issue.assignees else None,
            "assignees": [self.user(a) for a in issue.assignees],
            "milestone": None,
            "comments": len(issue.comments),
            "created_at": iso(issue.created_at),
            "updated_at": iso(issue.updated_at),
            "closed_at": iso(issue.closed_at),
            "closed_by": self.user(issue.closed_by) if issue.closed_by else None,
            "author_association": self.association(repo, issue.user),
            "body": issue.body,
            "reactions": _reactions_count(issue.reactions),
            "state_reason": issue.state_reason,
        }
        if issue.pull:
            out["draft"] = issue.pull.draft
            out["pull_request"] = {
                "url": f"{self.api}/repos/{repo.full_name}/pulls/{issue.number}",
                "html_url": self.issue_html(repo, issue),
                "diff_url": self.issue_html(repo, issue) + ".diff",
                "patch_url": self.issue_html(repo, issue) + ".patch",
                "merged_at": iso(issue.pull.merged_at),
            }
        return out

    def association(self, repo: Repo, login: str) -> str:
        if login == repo.owner:
            return "OWNER"
        if login in self.state.users and self.state.users[login].type != "Organization":
            return "MEMBER"
        return "NONE"

    def branch_ref(self, repo: Repo, ref: str, sha: str) -> Dict[str, Any]:
        return {
            "label": f"{repo.owner}:{ref}",
            "ref": ref,
            "sha": sha,
            "user": self.user(repo.owner),
            "repo": self.repo(repo),
        }

    def pull(self, repo: Repo, issue: Issue) -> Dict[str, Any]:
        assert issue.pull
        p = issue.pull
        head_sha = self.state.head_sha(repo, issue)
        base_sha = self.state.base_sha(repo, issue)
        changes = repo.bare.changes(base_sha, head_sha) if head_sha != base_sha else []
        commits = (
            repo.bare.commits(f"{base_sha}..{head_sha}")
            if not p.merged
            else repo.bare.commits(
                f"{repo.bare.merge_base(base_sha, head_sha) or base_sha}..{head_sha}"
            )
        )
        out = {
            "url": f"{self.api}/repos/{repo.full_name}/pulls/{issue.number}",
            "id": issue.id,
            "node_id": issue.node_id,
            "html_url": self.issue_html(repo, issue),
            "diff_url": self.issue_html(repo, issue) + ".diff",
            "patch_url": self.issue_html(repo, issue) + ".patch",
            "issue_url": f"{self.api}/repos/{repo.full_name}/issues/{issue.number}",
            "number": issue.number,
            "state": issue.state,
            "locked": False,
            "title": issue.title,
            "user": self.user(issue.user),
            "body": issue.body,
            "created_at": iso(issue.created_at),
            "updated_at": iso(issue.updated_at),
            "closed_at": iso(issue.closed_at),
            "merged_at": iso(p.merged_at),
            "merge_commit_sha": p.merge_commit_sha,
            "assignee": self.user(issue.assignees[0]) if issue.assignees else None,
            "assignees": [self.user(a) for a in issue.assignees],
            "requested_reviewers": [self.user(r) for r in p.requested_reviewers],
            "requested_teams": [],
            "labels": [self.label(repo, n) for n in issue.labels],
            "milestone": None,
            "draft": p.draft,
            "head": self.branch_ref(repo, p.head, head_sha),
            "base": self.branch_ref(repo, p.base, base_sha),
            "author_association": self.association(repo, issue.user),
            "merged": p.merged,
            "mergeable": (
                None
                if issue.state == "closed"
                else self.state.mergeable_state(repo, issue) != "dirty"
            ),
            "mergeable_state": self.state.mergeable_state(repo, issue),
            "merged_by": self.user(p.merged_by) if p.merged_by else None,
            "comments": len(issue.comments),
            "review_comments": sum(len(t.comments) for t in p.threads),
            "maintainer_can_modify": p.maintainer_can_modify,
            "commits": len(commits),
            "additions": sum(c.additions for c in changes),
            "deletions": sum(c.deletions for c in changes),
            "changed_files": len(changes),
        }
        return out

    def comment(self, repo: Repo, issue: Issue, comment: Comment) -> Dict[str, Any]:
        return {
            "url": f"{self.api}/repos/{repo.full_name}/issues/comments/{comment.id}",
            "html_url": f"{self.issue_html(repo, issue)}#issuecomment-{comment.id}",
            "issue_url": f"{self.api}/repos/{repo.full_name}/issues/{issue.number}",
            "id": comment.id,
            "node_id": f"IC_kwDO{comment.id:08d}",
            "user": self.user(comment.user),
            "created_at": iso(comment.created_at),
            "updated_at": iso(comment.updated_at),
            "author_association": self.association(repo, comment.user),
            "body": comment.body,
            "reactions": _reactions_count(comment.reactions),
        }

    def review(self, repo: Repo, issue: Issue, review: Review) -> Dict[str, Any]:
        return {
            "id": review.id,
            "node_id": f"PRR_kwDO{review.id:08d}",
            "user": self.user(review.user),
            "body": review.body,
            "state": review.state,
            "html_url": f"{self.issue_html(repo, issue)}#pullrequestreview-{review.id}",
            "pull_request_url": f"{self.api}/repos/{repo.full_name}/pulls/{issue.number}",
            "author_association": self.association(repo, review.user),
            "submitted_at": iso(review.submitted_at),
            "commit_id": review.commit_id,
        }

    def review_comment(
        self, repo: Repo, issue: Issue, comment: ReviewComment
    ) -> Dict[str, Any]:
        return {
            "url": f"{self.api}/repos/{repo.full_name}/pulls/comments/{comment.id}",
            "pull_request_review_id": comment.review_id,
            "id": comment.id,
            "node_id": f"PRRC_kwDO{comment.id:08d}",
            "path": comment.path,
            "commit_id": comment.commit_id,
            "original_commit_id": comment.commit_id,
            "user": self.user(comment.user),
            "body": comment.body,
            "created_at": iso(comment.created_at),
            "updated_at": iso(comment.updated_at),
            "html_url": f"{self.issue_html(repo, issue)}#discussion_r{comment.id}",
            "pull_request_url": f"{self.api}/repos/{repo.full_name}/pulls/{issue.number}",
            "author_association": self.association(repo, comment.user),
            "start_line": comment.start_line,
            "original_start_line": comment.start_line,
            "line": comment.line,
            "original_line": comment.original_line,
            "side": comment.side,
            "in_reply_to_id": comment.in_reply_to_id,
            "subject_type": "line" if comment.line is not None else "file",
        }

    def workflow_run(self, repo: Repo, run: WorkflowRun) -> Dict[str, Any]:
        return {
            "id": run.id,
            "name": run.name,
            "node_id": f"WFR_kwLO{run.id:08d}",
            "head_branch": run.head_branch,
            "head_sha": run.head_sha,
            "path": run.path,
            "display_title": run.head_message.split("\n", 1)[0],
            "run_number": run.run_number,
            "event": run.event,
            "status": run.status,
            "conclusion": run.conclusion,
            "workflow_id": run.workflow_id,
            "html_url": self.html(repo, "actions", "runs", run.id),
            "pull_requests": [
                {"number": n, "url": f"{self.api}/repos/{repo.full_name}/pulls/{n}"}
                for n in run.pull_requests
            ],
            "created_at": iso(run.created_at),
            "updated_at": iso(run.updated_at),
            "actor": self.user(run.actor),
            "triggering_actor": self.user(run.actor),
            "run_attempt": run.run_attempt,
            "run_started_at": iso(run.run_started_at),
            "head_commit": {"id": run.head_sha, "message": run.head_message},
            "repository": self.repo(repo),
        }

    # -- the MCP server's minimal types --------------------------------------

    def minimal_user(self, login: Optional[str]) -> Optional[Dict[str, Any]]:
        if not login:
            return None
        u = self.user(login)
        return {
            "login": u["login"],
            "id": u["id"],
            "profile_url": u["html_url"],
            "avatar_url": u["avatar_url"],
        }

    def minimal_reactions(self, reactions: Dict[str, List[str]]) -> Dict[str, int]:
        return _reactions_count(reactions)

    def minimal_issue(self, repo: Repo, issue: Issue) -> Dict[str, Any]:
        """``convertToMinimalIssue`` of the REST issue (issue_read's ``get``)."""
        m = {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "state": issue.state,
            "state_reason": issue.state_reason or "",
            "draft": bool(issue.pull and issue.pull.draft),
            "locked": False,
            "html_url": self.issue_html(repo, issue),
            "user": self.minimal_user(issue.user),
            "author_association": self.association(repo, issue.user),
            "labels": list(issue.labels),
            "assignees": list(issue.assignees),
            "milestone": "",
            "comments": len(issue.comments),
            "reactions": self.minimal_reactions(issue.reactions),
            "created_at": iso(issue.created_at),
            "updated_at": iso(issue.updated_at),
            "closed_at": iso(issue.closed_at) or "",
            "closed_by": issue.closed_by or "",
        }
        return _drop_empty(
            m, keep=("number", "title", "state", "assignees", "reactions")
        )

    def minimal_issue_from_graphql(self, repo: Repo, issue: Issue) -> Dict[str, Any]:
        """``fragmentToMinimalIssue`` (list_issues): GraphQL's states are upper case."""
        m = {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "state": issue.state.upper(),
            "user": {"login": issue.user},
            "labels": list(issue.labels),
            "assignees": list(issue.assignees),
            "comments": len(issue.comments),
            "created_at": iso(issue.created_at),
            "updated_at": iso(issue.updated_at),
        }
        return _drop_empty(m, keep=("number", "title", "state", "assignees"))

    def minimal_comment(
        self, repo: Repo, issue: Issue, comment: Comment
    ) -> Dict[str, Any]:
        rest = self.comment(repo, issue, comment)
        return _drop_empty(
            {
                "id": comment.id,
                "body": comment.body,
                "html_url": rest["html_url"],
                "user": self.minimal_user(comment.user),
                "author_association": rest["author_association"],
                "reactions": self.minimal_reactions(comment.reactions),
                "created_at": rest["created_at"],
                "updated_at": rest["updated_at"],
            },
            keep=("id", "html_url", "reactions"),
        )

    def minimal_pull(self, repo: Repo, issue: Issue) -> Dict[str, Any]:
        rest = self.pull(repo, issue)
        branch = lambda b: {
            "ref": b["ref"],
            "sha": b["sha"],  # noqa: E731
            "repo": _drop_empty(
                {"full_name": repo.full_name, "description": repo.description},
                keep=("full_name",),
            ),
        }
        m = {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body,
            "state": issue.state,
            "draft": rest["draft"],
            "merged": rest["merged"],
            "mergeable_state": rest["mergeable_state"],
            "html_url": rest["html_url"],
            "user": self.minimal_user(issue.user),
            "labels": list(issue.labels),
            "assignees": list(issue.assignees),
            "requested_reviewers": list(issue.pull.requested_reviewers),
            "merged_by": issue.pull.merged_by or "",
            "head": branch(rest["head"]),
            "base": branch(rest["base"]),
            "additions": rest["additions"],
            "deletions": rest["deletions"],
            "changed_files": rest["changed_files"],
            "commits": rest["commits"],
            "comments": rest["comments"],
            "created_at": rest["created_at"],
            "updated_at": rest["updated_at"],
            "closed_at": rest["closed_at"] or "",
            "merged_at": rest["merged_at"] or "",
        }
        return _drop_empty(
            m, keep=("number", "title", "state", "draft", "merged", "html_url")
        )

    def minimal_review(
        self, repo: Repo, issue: Issue, review: Review
    ) -> Dict[str, Any]:
        rest = self.review(repo, issue, review)
        return _drop_empty(
            {
                "id": review.id,
                "state": review.state,
                "body": review.body,
                "html_url": rest["html_url"],
                "user": self.minimal_user(review.user),
                "commit_id": review.commit_id,
                "submitted_at": rest["submitted_at"],
                "author_association": rest["author_association"],
            },
            keep=("id", "state", "html_url"),
        )

    def minimal_thread(
        self, repo: Repo, issue: Issue, thread: ReviewThread
    ) -> Dict[str, Any]:
        head = self.state.head_sha(repo, issue)
        comments = []
        for c in thread.comments:
            comments.append(
                _drop_empty(
                    {
                        "body": c.body,
                        "path": c.path,
                        "line": c.line if c.commit_id == head else None,
                        "original_line": c.original_line,
                        "start_line": c.start_line,
                        "original_start_line": c.start_line,
                        "author": c.user,
                        "created_at": iso(c.created_at),
                        "updated_at": iso(c.updated_at),
                        "html_url": f"{self.issue_html(repo, issue)}#discussion_r{c.id}",
                    },
                    keep=("path", "html_url"),
                )
            )
        return {
            "id": thread.id,
            "is_resolved": thread.resolved,
            "is_outdated": any(c.commit_id != head for c in thread.comments[:1]),
            "is_collapsed": thread.resolved,
            "comments": comments,
            "total_count": len(comments),
        }

    def minimal_commit(self, repo: Repo, commit, *, files=None) -> Dict[str, Any]:
        def author(name, email, date):
            return _drop_empty({"name": name, "email": email, "date": date})

        def account(email: str) -> Optional[Dict[str, Any]]:
            user = next(
                (u for u in self.state.users.values() if u.email == email), None
            )
            return self.minimal_user(user.login) if user else None

        m = {
            "sha": commit.sha,
            "html_url": self.html(repo, "commit", commit.sha),
            "commit": {
                "message": commit.message,
                "author": author(
                    commit.author_name, commit.author_email, commit.author_date
                ),
                "committer": author(
                    commit.committer_name, commit.committer_email, commit.committer_date
                ),
            },
            "author": account(commit.author_email),
            "committer": account(commit.committer_email),
        }
        if files is not None:
            m["stats"] = _drop_empty(
                {
                    "additions": sum(f.additions for f in files),
                    "deletions": sum(f.deletions for f in files),
                    "total": sum(f.changes for f in files),
                }
            )
            m["files"] = files
        return _drop_empty(m, keep=("sha", "html_url"))

    def minimal_run(self, repo: Repo, run: WorkflowRun) -> Dict[str, Any]:
        rest = self.workflow_run(repo, run)
        return _drop_empty(
            {
                "id": run.id,
                "name": run.name,
                "display_title": rest["display_title"],
                "workflow_id": run.workflow_id,
                "run_number": run.run_number,
                "run_attempt": run.run_attempt,
                "event": run.event,
                "status": run.status,
                "conclusion": run.conclusion or "",
                "head_branch": run.head_branch,
                "head_sha": run.head_sha,
                "head_commit": (
                    {"message": run.head_message} if run.head_message else None
                ),
                "path": run.path,
                "html_url": rest["html_url"],
                "pull_requests": run.pull_requests,
                "actor": self.minimal_user(run.actor),
                "triggering_actor": self.minimal_user(run.actor),
                "created_at": rest["created_at"],
                "updated_at": rest["updated_at"],
                "run_started_at": rest["run_started_at"],
            },
            keep=("id", "name", "workflow_id", "run_number", "run_attempt", "status"),
        )

    def minimal_job(self, repo: Repo, run: WorkflowRun, job) -> Dict[str, Any]:
        return _drop_empty(
            {
                "id": job.id,
                "run_id": run.id,
                "name": job.name,
                "workflow_name": run.name,
                "status": job.status,
                "conclusion": job.conclusion or "",
                "head_branch": job.head_branch,
                "head_sha": job.head_sha,
                "html_url": self.html(repo, "actions", "runs", run.id, "job", job.id),
                "run_attempt": run.run_attempt,
                "runner_name": "GitHub Actions 2",
                "labels": ["ubuntu-latest"],
                "steps": [
                    _drop_empty(
                        {
                            "name": s.name,
                            "status": s.status,
                            "conclusion": s.conclusion or "",
                            "number": s.number,
                            "started_at": iso(s.started_at),
                            "completed_at": iso(s.completed_at),
                        },
                        keep=("name", "status", "number"),
                    )
                    for s in job.steps
                ],
                "created_at": iso(job.created_at),
                "started_at": iso(job.started_at),
                "completed_at": iso(job.completed_at),
            },
            keep=("id", "run_id", "name", "status"),
        )
