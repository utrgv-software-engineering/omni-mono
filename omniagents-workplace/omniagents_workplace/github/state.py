"""GitHub's state: people, repositories, issues, pull requests, reviews and Actions.

The repositories' content is git (:mod:`.repos`); everything else lives
here. Every change emits an event (``subscribe``): the UI follows them, and
the webhooks (:mod:`.webhooks`) turn them into GitHub's webhook deliveries.

Seed (``github:`` in a workplace seed)::

    users:
      - {login: priya, name: Priya Shah, slack: U0ACME0PRI}
    repositories:
      - name: acme/api-service
        from: acme/repos/api-service    # the default branch's content
        labels: [{name: "factory:Acme", color: "5319e7"}]
        issues:
          - {number: 12, title: ..., author: priya, labels: [], body: ...}
        pull_requests:
          - {number: 33, title: ..., author: dana, head: dana/pagination,
             from: acme/repos/api-service-pagination,   # the head's content
             # or diff: <a unified diff applied to the base>
             body: ..., labels: []}

A pull request's ``base_from`` sets the base branch's content when it
differs from the repository's (the evaluation fixtures' ``base_fixture``);
``base_unapply`` is a diff the head made, undone from ``base_from`` to get
the base (a fixture that is the head, and the diff that made it).
Authors and assignees that ``users`` does not list are created.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .repos import NULL_SHA, BareRepo, GitError, identity

__all__ = [
    "GitHubError",
    "GitHubState",
    "Issue",
    "Repo",
    "User",
    "Webhook",
    "iso",
]

Listener = Callable[[str, Dict[str, Any]], None]

REACTIONS = ("+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes")


def iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GitHubError(Exception):
    """A request GitHub refuses: ``status`` and ``message`` as GitHub answers."""

    def __init__(
        self, status: int, message: str, errors: Optional[List[Dict[str, str]]] = None
    ):
        super().__init__(message)
        self.status = status
        self.message = message
        self.errors = errors or []


@dataclass
class User:
    login: str
    id: int
    name: str = ""
    email: str = ""
    type: str = "User"
    slack: Optional[str] = None
    token: Optional[str] = None
    #: A removed app's account, or a disabled person's: keeps its history,
    #: can no longer act, and is offered nowhere.
    suspended: bool = False

    @property
    def node_id(self) -> str:
        return f"U_kgDO{self.id:08d}"


@dataclass
class Label:
    id: int
    name: str
    color: str = "ededed"
    description: str = ""


@dataclass
class Comment:
    id: int
    user: str
    body: str
    created_at: float
    updated_at: float
    reactions: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class ReviewComment:
    id: int
    user: str
    body: str
    path: str
    line: Optional[int]
    start_line: Optional[int]
    side: str
    commit_id: str
    created_at: float
    updated_at: float
    review_id: int
    thread_id: str
    in_reply_to_id: Optional[int] = None
    original_line: Optional[int] = None
    reactions: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class ReviewThread:
    id: str
    path: str
    comments: List[ReviewComment] = field(default_factory=list)
    resolved: bool = False
    resolved_by: Optional[str] = None


@dataclass
class Review:
    id: int
    user: str
    state: str  # PENDING, COMMENTED, APPROVED, CHANGES_REQUESTED, DISMISSED
    body: str
    commit_id: str
    submitted_at: Optional[float] = None


@dataclass
class Pull:
    head: str
    base: str
    draft: bool = False
    merged: bool = False
    merged_at: Optional[float] = None
    merged_by: Optional[str] = None
    merge_commit_sha: Optional[str] = None
    maintainer_can_modify: bool = False
    requested_reviewers: List[str] = field(default_factory=list)
    reviews: List[Review] = field(default_factory=list)
    threads: List[ReviewThread] = field(default_factory=list)
    #: The head's sha when the pull request was closed (its branch may move on).
    closed_head_sha: Optional[str] = None


@dataclass
class Issue:
    """An issue, or a pull request (``pull`` set): GitHub numbers them together."""

    number: int
    id: int
    title: str
    body: str
    user: str
    created_at: float
    updated_at: float
    state: str = "open"
    state_reason: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    comments: List[Comment] = field(default_factory=list)
    closed_at: Optional[float] = None
    closed_by: Optional[str] = None
    reactions: Dict[str, List[str]] = field(default_factory=dict)
    pull: Optional[Pull] = None

    @property
    def is_pull(self) -> bool:
        return self.pull is not None

    @property
    def node_id(self) -> str:
        return f"{'PR' if self.pull else 'I'}_kwDO{self.id:08d}"


@dataclass
class Step:
    number: int
    name: str
    status: str
    conclusion: Optional[str]
    started_at: Optional[float]
    completed_at: Optional[float]


@dataclass
class Job:
    id: int
    run_id: int
    name: str
    status: str
    conclusion: Optional[str]
    head_sha: str
    head_branch: str
    created_at: float
    started_at: Optional[float]
    completed_at: Optional[float]
    steps: List[Step] = field(default_factory=list)
    log: str = ""


@dataclass
class WorkflowRun:
    id: int
    workflow_id: int
    name: str
    path: str
    run_number: int
    event: str
    status: str
    conclusion: Optional[str]
    head_branch: str
    head_sha: str
    head_message: str
    actor: str
    created_at: float
    updated_at: float
    run_started_at: float
    pull_requests: List[int] = field(default_factory=list)
    jobs: List[Job] = field(default_factory=list)
    run_attempt: int = 1


@dataclass
class Repo:
    full_name: str
    id: int
    bare: BareRepo
    description: str = ""
    default_branch: str = "main"
    private: bool = False
    created_at: float = field(default_factory=time.time)
    labels: Dict[str, Label] = field(default_factory=dict)
    issues: Dict[int, Issue] = field(default_factory=dict)
    runs: List[WorkflowRun] = field(default_factory=list)
    protected: List[str] = field(default_factory=list)
    #: Per protected branch, the checks (workflow job names) a pull request into
    #: it must pass before it merges: GitHub's required status checks.
    required_checks: Dict[str, List[str]] = field(default_factory=dict)
    #: Each branch's last push: ``{"actor": login, "at": time}`` (GitHub's
    #: "had recent pushes" prompt).
    pushes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Each workflow file's id, by path.
    workflow_ids: Dict[str, int] = field(default_factory=dict)
    #: An admin's: its jobs may run on the self-hosted runner (``runs-on:
    #: self-hosted``), which has Docker and what the admin gave it.
    self_hosted: bool = False

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]

    @property
    def node_id(self) -> str:
        return f"R_kgDO{self.id:08d}"

    def pulls(self) -> List[Issue]:
        return [i for i in self.issues.values() if i.pull]

    def next_number(self) -> int:
        return max(self.issues, default=0) + 1


@dataclass
class Webhook:
    url: str
    secret: str = ""
    #: The events it receives; ``*`` is every event.
    events: List[str] = field(default_factory=lambda: ["*"])
    id: int = 0
    active: bool = True


_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_MENTION = re.compile(r"(?<![\w/`])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))")


def mentions(text: str) -> List[str]:
    """The logins ``text`` @-mentions, outside code."""
    without_code = re.sub(r"```.*?```|`[^`]*`", "", text or "", flags=re.S)
    return list(dict.fromkeys(m.group(1) for m in _MENTION.finditer(without_code)))


class GitHubState:
    def __init__(self, root: Path, noreply_domain: str):
        self.root = Path(root)
        #: Where commit emails of accounts without one point (GitHub's users.noreply.github.com).
        self.noreply_domain = noreply_domain
        self.users: Dict[str, User] = {}
        self.repos: Dict[str, Repo] = {}
        self.hooks: List[Webhook] = []
        #: Runs workflow files (``actions.ActionsRunner``); without one, none run.
        self.actions: Optional[Any] = None
        #: The next id: ids are unique for the state's life (persisted with it).
        self.next_id = 1001
        self._listeners: List[Listener] = []
        self._lock = threading.RLock()

    # -- ids, events ------------------------------------------------------

    def _id(self) -> int:
        with self._lock:
            n, self.next_id = self.next_id, self.next_id + 1
        return n

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _emit(self, kind: str, **data: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, data)
            except Exception:  # a listener never breaks the change itself
                pass

    def changed(self, **data: Any) -> None:
        """Something changed that is no event of GitHub's (a pending review, a
        setting): ``repo``, ``issue``, or ``hooks=True``."""
        self._emit("changed", **data)

    # -- seed -------------------------------------------------------------

    def load_seed(self, seed: Dict[str, Any], seed_dir: Path) -> None:
        with self._lock:
            self.users = {}
            self.repos = {}
            self.next_id = 1001
            self.hooks = []
            for raw in seed.get("users") or []:
                self.add_user(
                    raw["login"],
                    name=raw.get("name", ""),
                    email=raw.get("email", ""),
                    slack=raw.get("slack"),
                    type=raw.get("type", "User"),
                )
            for raw in seed.get("repositories") or []:
                self._seed_repo(raw, Path(seed_dir))
        self._emit("reset")

    def add_user(self, login: str, **fields: Any) -> User:
        existing = self.users.get(login)
        if existing is not None:
            changed = False
            for key, value in fields.items():
                if value and getattr(existing, key) != value:
                    setattr(existing, key, value)
                    changed = True
            if changed:
                self._emit("user", user=existing)
            return existing
        user = User(
            login=login,
            id=self._id(),
            **{k: v for k, v in fields.items() if v is not None},
        )
        if not user.email:
            user.email = f"{login}@{self.noreply_domain}"
        if not user.name:
            user.name = login
        self.users[login] = user
        self._emit("user", user=user)
        return user

    def set_token(self, user: User, token: Optional[str]) -> None:
        """``user``'s token (``None`` revokes it)."""
        with self._lock:
            if token is not None and any(u.token == token for u in self.users.values()):
                raise ValueError("token in use")
            user.token = token
        self._emit("user", user=user)

    def user(self, login: str) -> User:
        return self.users.get(login) or self.add_user(login)

    def user_for_token(self, token: str) -> Optional[User]:
        return next(
            (
                u
                for u in self.users.values()
                if u.token and u.token == token and not u.suspended
            ),
            None,
        )

    def set_suspended(self, user: User, suspended: bool) -> None:
        with self._lock:
            user.suspended = suspended
        self._emit("user", user=user)

    def _who(self, login: str, at: Optional[float] = None) -> Dict[str, str]:
        user = self.user(login)
        date = iso(at) if at else None
        return identity(user.name or login, user.email, date)

    def organization(self) -> Optional[User]:
        """The workplace's organization, which owns its repositories."""
        return next((u for u in self.users.values() if u.type == "Organization"), None)

    def create_repo(
        self,
        owner: str,
        name: str,
        actor: str,
        *,
        description: str = "",
        private: bool = False,
        auto_init: bool = True,
        protect_default_branch: bool = False,
    ) -> Repo:
        """A new repository: empty, or with a README (``auto_init``) on ``main``."""
        if not _REPO_NAME.match(name or "") or name in (".", ".."):
            raise GitHubError(
                422,
                "Validation Failed",
                [{"resource": "Repository", "field": "name", "code": "invalid"}],
            )
        full_name = f"{owner}/{name}"
        if full_name in self.repos:
            raise GitHubError(
                422,
                "Repository creation failed.",
                [
                    {
                        "resource": "Repository",
                        "code": "custom",
                        "field": "name",
                        "message": "name already exists on this account",
                    }
                ],
            )
        if owner not in self.users:
            raise GitHubError(404, "Not Found")
        with self._lock:
            bare = BareRepo.create(self.root / owner / f"{name}.git", "main")
            repo = Repo(
                full_name=full_name,
                id=self._id(),
                bare=bare,
                description=description or "",
                private=private,
                protected=["main"] if protect_default_branch else [],
            )
            self.repos[full_name] = repo
        if auto_init:
            body = f"# {name}\n" + (f"\n{description}\n" if description else "")
            bare.write_files(
                "main",
                [("README.md", body.encode())],
                "Initial commit",
                self._who(actor),
            )
        self._emit("repo_created", repo=repo, actor=actor)
        return repo

    def import_directory(self, repo: Repo, source: Path, actor: str) -> None:
        """``source``'s files as the first commit of ``repo``, a new empty one."""
        repo.bare.commit_directory(
            repo.default_branch, source, "Initial commit", self._who(actor)
        )

    def _seed_repo(self, raw: Dict[str, Any], seed_dir: Path) -> None:
        full_name = str(raw["name"])
        if full_name.count("/") != 1:
            raise ValueError(f"github repository {full_name!r} is not owner/name")
        owner, name = full_name.split("/", 1)
        default_branch = str(raw.get("default_branch") or "main")
        bare = BareRepo.create(self.root / owner / f"{name}.git", default_branch)
        repo = Repo(
            full_name=full_name,
            id=self._id(),
            bare=bare,
            description=str(raw.get("description") or ""),
            default_branch=default_branch,
            private=bool(raw.get("private", False)),
            protected=list(raw.get("protected_branches") or [default_branch]),
        )
        self.add_user(owner, type="Organization")
        started = time.time() - 7 * 86400
        source = raw.get("from")
        bare.commit_directory(
            default_branch,
            (seed_dir / source) if source else None,
            "Initial commit",
            self._who(str(raw.get("author") or owner), started),
        )
        for label in raw.get("labels") or []:
            label = {"name": label} if isinstance(label, str) else label
            repo.labels[label["name"]] = Label(
                self._id(),
                label["name"],
                label.get("color", "ededed"),
                label.get("description", ""),
            )
        self.repos[full_name] = repo
        for n, issue in enumerate(raw.get("issues") or []):
            self._seed_issue(repo, issue, started + 3600 * (n + 1))
        for n, pr in enumerate(raw.get("pull_requests") or []):
            self._seed_pull(repo, pr, seed_dir, started + 86400 + 3600 * (n + 1))

    def _seed_issue(self, repo: Repo, raw: Dict[str, Any], at: float) -> Issue:
        author = str(raw.get("author") or raw.get("user") or repo.owner)
        self.user(author)
        for login in raw.get("assignees") or []:
            self.user(login)
        issue = Issue(
            number=int(raw.get("number") or repo.next_number()),
            id=self._id(),
            title=str(raw["title"]),
            body=str(raw.get("body") or ""),
            user=author,
            created_at=at,
            updated_at=at,
            state=str(raw.get("state") or "open"),
            labels=[
                self._ensure_label(repo, label) for label in raw.get("labels") or []
            ],
            assignees=list(raw.get("assignees") or []),
        )
        if issue.state == "closed":
            issue.closed_at = at
        for n, comment in enumerate(raw.get("comments") or []):
            login = str(comment.get("author") or comment.get("user") or author)
            self.user(login)
            issue.comments.append(
                Comment(
                    self._id(),
                    login,
                    str(comment["body"]),
                    at + 60 * (n + 1),
                    at + 60 * (n + 1),
                )
            )
        repo.issues[issue.number] = issue
        return issue

    def _seed_pull(
        self, repo: Repo, raw: Dict[str, Any], seed_dir: Path, at: float
    ) -> Issue:
        author = str(raw.get("author") or raw.get("user") or repo.owner)
        head = str(raw["head"])
        base = str(raw.get("base") or repo.default_branch)
        base_sha = repo.bare.ref_sha(base)
        if raw.get("base_from"):
            base_sha = repo.bare.commit_directory(
                base,
                seed_dir / raw["base_from"],
                "Initial commit",
                self._who(repo.owner, at - 3600),
                patch=raw.get("base_unapply"),
                reverse=True,
            )
        source = raw.get("from")
        repo.bare.commit_directory(
            head,
            (seed_dir / source) if source else None,
            str(raw.get("commit_message") or raw["title"]),
            self._who(author, at),
            parent=base_sha,
            patch=raw.get("diff") if not source else None,
        )
        issue = self._seed_issue(repo, {**raw, "author": author}, at)
        issue.pull = Pull(head=head, base=base, draft=bool(raw.get("draft", False)))
        if raw.get("merged"):
            issue.state = "open"
            self.merge_pull(repo, issue, author, bypass_protection=True)
        return issue

    def _ensure_label(self, repo: Repo, name: str) -> str:
        if name not in repo.labels:
            repo.labels[name] = Label(self._id(), name)
        return name

    # -- lookups ----------------------------------------------------------

    def repo(self, owner: str, name: str) -> Repo:
        repo = self.repos.get(f"{owner}/{name}")
        if repo is None:
            raise GitHubError(404, "Not Found")
        return repo

    def issue(self, repo: Repo, number: int) -> Issue:
        issue = repo.issues.get(int(number))
        if issue is None:
            raise GitHubError(404, "Not Found")
        return issue

    def pull(self, repo: Repo, number: int) -> Issue:
        issue = self.issue(repo, number)
        if not issue.pull:
            raise GitHubError(404, "Not Found")
        return issue

    def head_sha(self, repo: Repo, issue: Issue) -> str:
        assert issue.pull
        if issue.state == "closed" and issue.pull.closed_head_sha:
            return issue.pull.closed_head_sha
        return repo.bare.ref_sha(issue.pull.head) or NULL_SHA

    def base_sha(self, repo: Repo, issue: Issue) -> str:
        assert issue.pull
        return repo.bare.ref_sha(issue.pull.base) or NULL_SHA

    def comment(self, repo: Repo, comment_id: int) -> Tuple[Issue, Comment]:
        for issue in repo.issues.values():
            for comment in issue.comments:
                if comment.id == int(comment_id):
                    return issue, comment
        raise GitHubError(404, "Not Found")

    def review_comment(
        self, repo: Repo, comment_id: int
    ) -> Tuple[Issue, ReviewThread, ReviewComment]:
        for issue in repo.pulls():
            for thread in issue.pull.threads:
                for comment in thread.comments:
                    if comment.id == int(comment_id):
                        return issue, thread, comment
        raise GitHubError(404, "Not Found")

    def find_thread(self, thread_id: str) -> Tuple[Repo, Issue, ReviewThread]:
        for repo in self.repos.values():
            for issue in repo.pulls():
                for thread in issue.pull.threads:
                    if thread.id == thread_id:
                        return repo, issue, thread
        raise GitHubError(
            404, f"Could not resolve to a node with the global id of '{thread_id}'"
        )

    # -- issues -----------------------------------------------------------

    def create_issue(
        self,
        repo: Repo,
        actor: str,
        title: str,
        body: str = "",
        labels: Iterable[str] = (),
        assignees: Iterable[str] = (),
    ) -> Issue:
        if not title:
            raise GitHubError(
                422,
                "Validation Failed",
                [{"resource": "Issue", "field": "title", "code": "missing_field"}],
            )
        now = time.time()
        with self._lock:
            issue = Issue(
                repo.next_number(), self._id(), title, body or "", actor, now, now
            )
            issue.labels = [self._ensure_label(repo, label) for label in labels]
            issue.assignees = [self.user(a).login for a in assignees]
            repo.issues[issue.number] = issue
        self._emit("issue_opened", repo=repo, issue=issue, actor=actor)
        for login in issue.assignees:
            self._emit(
                "issue_assigned", repo=repo, issue=issue, actor=actor, assignee=login
            )
        for name in issue.labels:
            self._emit("issue_labeled", repo=repo, issue=issue, actor=actor, label=name)
        return issue

    def update_issue(
        self,
        repo: Repo,
        issue: Issue,
        actor: str,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        state_reason: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> Issue:
        changes: List[Tuple[str, Dict[str, Any]]] = []
        with self._lock:
            if title is not None and title != issue.title:
                changes.append(
                    ("edited", {"changes": {"title": {"from": issue.title}}})
                )
                issue.title = title
            if body is not None and body != issue.body:
                changes.append(("edited", {"changes": {"body": {"from": issue.body}}}))
                issue.body = body
            if labels is not None:
                new = [self._ensure_label(repo, name) for name in dict.fromkeys(labels)]
                for name in issue.labels:
                    if name not in new:
                        changes.append(("unlabeled", {"label": name}))
                for name in new:
                    if name not in issue.labels:
                        changes.append(("labeled", {"label": name}))
                issue.labels = new
            if assignees is not None:
                new = [self.user(a).login for a in dict.fromkeys(assignees)]
                for login in issue.assignees:
                    if login not in new:
                        changes.append(("unassigned", {"assignee": login}))
                for login in new:
                    if login not in issue.assignees:
                        changes.append(("assigned", {"assignee": login}))
                issue.assignees = new
            if state is not None and state != issue.state:
                if state == "closed":
                    issue.state, issue.closed_at, issue.closed_by = (
                        "closed",
                        time.time(),
                        actor,
                    )
                    issue.state_reason = state_reason or (
                        "completed" if not issue.pull else None
                    )
                    if issue.pull:
                        issue.pull.closed_head_sha = repo.bare.ref_sha(issue.pull.head)
                    changes.append(("closed", {}))
                elif state == "open":
                    if issue.pull and issue.pull.merged:
                        raise GitHubError(
                            422,
                            "Validation Failed",
                            [
                                {
                                    "message": "state cannot be changed. The pull request cannot be reopened."
                                }
                            ],
                        )
                    issue.state, issue.closed_at, issue.closed_by = "open", None, None
                    issue.state_reason = "reopened" if not issue.pull else None
                    if issue.pull:
                        issue.pull.closed_head_sha = None
                    changes.append(("reopened", {}))
            elif state_reason is not None and issue.state == "closed":
                issue.state_reason = state_reason
            issue.updated_at = time.time()
        prefix = "pull_request" if issue.pull else "issue"
        for action, extra in changes:
            self._emit(
                f"{prefix}_{action}", repo=repo, issue=issue, actor=actor, **extra
            )
        if not changes:
            self.changed(repo=repo, issue=issue)
        return issue

    def add_comment(self, repo: Repo, issue: Issue, actor: str, body: str) -> Comment:
        if not body:
            raise GitHubError(
                422,
                "Validation Failed",
                [
                    {
                        "resource": "IssueComment",
                        "field": "body",
                        "code": "missing_field",
                    }
                ],
            )
        now = time.time()
        with self._lock:
            comment = Comment(self._id(), actor, body, now, now)
            issue.comments.append(comment)
            issue.updated_at = now
        self._emit(
            "comment_created", repo=repo, issue=issue, comment=comment, actor=actor
        )
        return comment

    def edit_comment(
        self, repo: Repo, comment_id: int, actor: str, body: str
    ) -> Tuple[Issue, Comment]:
        issue, comment = self.comment(repo, comment_id)
        previous = comment.body
        comment.body, comment.updated_at = body, time.time()
        self._emit(
            "comment_edited",
            repo=repo,
            issue=issue,
            comment=comment,
            actor=actor,
            previous=previous,
        )
        return issue, comment

    def unreact(self, target: Any, actor: str, content: str) -> None:
        users = target.reactions.get(content) or []
        if actor in users:
            users.remove(actor)
            if not users:
                target.reactions.pop(content, None)
            self._emit("reaction", target=target, actor=actor, content=content)

    def delete_comment(self, repo: Repo, comment_id: int, actor: str) -> None:
        issue, comment = self.comment(repo, comment_id)
        issue.comments.remove(comment)
        self._emit(
            "comment_deleted", repo=repo, issue=issue, comment=comment, actor=actor
        )

    def react(self, target: Any, actor: str, content: str) -> int:
        if content not in REACTIONS:
            raise GitHubError(422, "Validation Failed")
        users = target.reactions.setdefault(content, [])
        if actor not in users:
            users.append(actor)
        reaction_id = self._id()
        self._emit("reaction", target=target, actor=actor, content=content)
        return reaction_id

    # -- pull requests ----------------------------------------------------

    def create_pull(
        self,
        repo: Repo,
        actor: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
        maintainer_can_modify: bool = False,
    ) -> Issue:
        head_ref = head.split(":", 1)[1] if ":" in head else head
        head_sha = repo.bare.ref_sha(head_ref)
        base_sha = repo.bare.ref_sha(base)
        if base_sha is None:
            raise GitHubError(
                422,
                "Validation Failed",
                [{"resource": "PullRequest", "field": "base", "code": "invalid"}],
            )
        if head_sha is None:
            raise GitHubError(
                422,
                "Validation Failed",
                [{"resource": "PullRequest", "field": "head", "code": "invalid"}],
            )
        for existing in repo.pulls():
            if (
                existing.state == "open"
                and existing.pull.head == head_ref
                and existing.pull.base == base
            ):
                raise GitHubError(
                    422,
                    "Validation Failed",
                    [
                        {
                            "resource": "PullRequest",
                            "code": "custom",
                            "message": f"A pull request already exists for {repo.owner}:{head_ref}.",
                        }
                    ],
                )
        if head_sha == base_sha or not repo.bare.changes(base_sha, head_sha):
            raise GitHubError(
                422,
                "Validation Failed",
                [
                    {
                        "resource": "PullRequest",
                        "code": "custom",
                        "message": f"No commits between {base} and {head_ref}",
                    }
                ],
            )
        now = time.time()
        with self._lock:
            issue = Issue(
                repo.next_number(), self._id(), title, body or "", actor, now, now
            )
            issue.pull = Pull(
                head=head_ref,
                base=base,
                draft=draft,
                maintainer_can_modify=maintainer_can_modify,
            )
            repo.issues[issue.number] = issue
        self._emit("pull_request_opened", repo=repo, issue=issue, actor=actor)
        self._start_ci(
            repo, head_ref, head_sha, "pull_request", actor, pull=issue, action="opened"
        )
        return issue

    def update_pull(
        self,
        repo: Repo,
        issue: Issue,
        actor: str,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        base: Optional[str] = None,
        draft: Optional[bool] = None,
        maintainer_can_modify: Optional[bool] = None,
    ) -> Issue:
        assert issue.pull
        if base is not None and base != issue.pull.base:
            if repo.bare.ref_sha(base) is None:
                raise GitHubError(
                    422,
                    "Validation Failed",
                    [{"resource": "PullRequest", "field": "base", "code": "invalid"}],
                )
            issue.pull.base = base
            self._emit(
                "pull_request_edited",
                repo=repo,
                issue=issue,
                actor=actor,
                changes={"base": {}},
            )
        if draft is not None and draft != issue.pull.draft:
            issue.pull.draft = draft
            self._emit(
                (
                    "pull_request_converted_to_draft"
                    if draft
                    else "pull_request_ready_for_review"
                ),
                repo=repo,
                issue=issue,
                actor=actor,
            )
        if maintainer_can_modify is not None:
            issue.pull.maintainer_can_modify = maintainer_can_modify
        return self.update_issue(
            repo, issue, actor, title=title, body=body, state=state
        )

    def request_reviewers(
        self, repo: Repo, issue: Issue, actor: str, reviewers: Iterable[str]
    ) -> None:
        assert issue.pull
        for login in reviewers:
            if login == issue.user:
                raise GitHubError(
                    422,
                    "Reviews may only be requested from collaborators. One or more of the users or teams you specified is not a collaborator of the "
                    f"{repo.full_name} repository.",
                )
            self.user(login)
            if login not in issue.pull.requested_reviewers:
                issue.pull.requested_reviewers.append(login)
                self._emit(
                    "pull_request_review_requested",
                    repo=repo,
                    issue=issue,
                    actor=actor,
                    reviewer=login,
                )

    def remove_review_requests(
        self, repo: Repo, issue: Issue, actor: str, reviewers: Iterable[str]
    ) -> None:
        assert issue.pull
        for login in reviewers:
            if login in issue.pull.requested_reviewers:
                issue.pull.requested_reviewers.remove(login)
                self._emit(
                    "pull_request_review_request_removed",
                    repo=repo,
                    issue=issue,
                    actor=actor,
                    reviewer=login,
                )

    def pending_review(self, issue: Issue, actor: str) -> Optional[Review]:
        assert issue.pull
        return next(
            (r for r in issue.pull.reviews if r.user == actor and r.state == "PENDING"),
            None,
        )

    def create_review(
        self,
        repo: Repo,
        issue: Issue,
        actor: str,
        *,
        event: str = "",
        body: str = "",
        commit_id: Optional[str] = None,
    ) -> Review:
        """A pending review (no ``event``), or a submitted one."""
        assert issue.pull
        if self.pending_review(issue, actor) is not None:
            raise GitHubError(
                422, "User can only have one pending review per pull request"
            )
        commit = commit_id or self.head_sha(repo, issue)
        review = Review(self._id(), actor, "PENDING", body or "", commit)
        issue.pull.reviews.append(review)
        if event:
            try:
                self.submit_review(repo, issue, actor, review, event, body)
            except GitHubError:
                # A review GitHub refuses (your own pull request's) is not made.
                issue.pull.reviews.remove(review)
                self.changed(repo=repo, issue=issue)
                raise
        else:
            self.changed(repo=repo, issue=issue)
        return review

    def submit_review(
        self,
        repo: Repo,
        issue: Issue,
        actor: str,
        review: Review,
        event: str,
        body: str = "",
    ) -> Review:
        assert issue.pull
        state = {
            "APPROVE": "APPROVED",
            "REQUEST_CHANGES": "CHANGES_REQUESTED",
            "COMMENT": "COMMENTED",
        }.get(event)
        if state is None:
            raise GitHubError(422, f"Unknown review event: {event}")
        if actor == issue.user and state == "APPROVED":
            raise GitHubError(422, "Review Can not approve your own pull request")
        if actor == issue.user and state == "CHANGES_REQUESTED":
            raise GitHubError(
                422, "Review Can not request changes on your own pull request"
            )
        if (
            state in ("COMMENTED", "CHANGES_REQUESTED")
            and not (body or review.body)
            and not self._review_comments(issue, review)
        ):
            raise GitHubError(
                422,
                (
                    "Review Body is required"
                    if state == "CHANGES_REQUESTED"
                    else "Review Body can't be blank"
                ),
            )
        review.state = state
        review.body = body or review.body
        review.submitted_at = time.time()
        if actor in issue.pull.requested_reviewers:
            issue.pull.requested_reviewers.remove(actor)
        issue.updated_at = review.submitted_at
        self._emit(
            "pull_request_review_submitted",
            repo=repo,
            issue=issue,
            actor=actor,
            review=review,
        )
        for thread in issue.pull.threads:
            for comment in thread.comments:
                if comment.review_id == review.id:
                    self._emit(
                        "review_comment_created",
                        repo=repo,
                        issue=issue,
                        actor=actor,
                        comment=comment,
                        review=review,
                    )
        return review

    def delete_pending_review(self, issue: Issue, review: Review) -> None:
        assert issue.pull
        issue.pull.reviews.remove(review)
        issue.pull.threads = [
            t
            for t in issue.pull.threads
            if not all(c.review_id == review.id for c in t.comments)
        ]
        self.changed(issue=issue)

    def _review_comments(self, issue: Issue, review: Review) -> List[ReviewComment]:
        assert issue.pull
        return [
            c
            for t in issue.pull.threads
            for c in t.comments
            if c.review_id == review.id
        ]

    def add_review_comment(
        self,
        repo: Repo,
        issue: Issue,
        review: Review,
        actor: str,
        *,
        path: str,
        body: str,
        line: Optional[int],
        start_line: Optional[int],
        side: str = "RIGHT",
        subject_type: str = "LINE",
    ) -> ReviewThread:
        assert issue.pull
        change = next(
            (
                c
                for c in repo.bare.changes(
                    self.base_sha(repo, issue), self.head_sha(repo, issue)
                )
                if c.filename == path
            ),
            None,
        )
        if change is None:
            raise GitHubError(422, "Path could not be resolved")
        if subject_type != "FILE":
            if line is None or not _line_in_patch(change.patch, line, side):
                raise GitHubError(422, "Line could not be resolved")
        now = time.time()
        thread = ReviewThread(id=f"PRRT_kwDO{self._id():08d}", path=path)
        thread.comments.append(
            ReviewComment(
                self._id(),
                actor,
                body,
                path,
                line if subject_type != "FILE" else None,
                start_line,
                side,
                review.commit_id,
                now,
                now,
                review.id,
                thread.id,
                original_line=line,
            )
        )
        issue.pull.threads.append(thread)
        self.changed(repo=repo, issue=issue)
        return thread

    def reply_to_review_comment(
        self, repo: Repo, issue: Issue, actor: str, comment_id: int, body: str
    ) -> ReviewComment:
        _, thread, parent = self.review_comment(repo, comment_id)
        now = time.time()
        reply = ReviewComment(
            self._id(),
            actor,
            body,
            parent.path,
            parent.line,
            parent.start_line,
            parent.side,
            self.head_sha(repo, issue),
            now,
            now,
            self._id(),
            thread.id,
            in_reply_to_id=parent.id,
            original_line=parent.original_line,
        )
        review = Review(reply.review_id, actor, "COMMENTED", "", reply.commit_id, now)
        assert issue.pull
        issue.pull.reviews.append(review)
        thread.comments.append(reply)
        self._emit(
            "review_comment_created",
            repo=repo,
            issue=issue,
            actor=actor,
            comment=reply,
            review=review,
        )
        return reply

    def resolve_thread(
        self, thread_id: str, actor: str, resolved: bool
    ) -> ReviewThread:
        _, _, thread = self.find_thread(thread_id)
        thread.resolved = resolved
        thread.resolved_by = actor if resolved else None
        self._emit("thread_resolved", thread=thread, actor=actor)
        return thread

    def check_states(self, repo: Repo, sha: str) -> Dict[str, str]:
        """Each check (job name) on ``sha``, its latest: ``success``, ``failure``,
        ``cancelled``, ``skipped`` or ``pending``."""
        out: Dict[str, Tuple[float, str]] = {}
        for run in self.runs_for(repo, sha):
            for job in run.jobs:
                state = job.conclusion if job.status == "completed" else "pending"
                stamp = job.started_at or job.created_at
                if job.name not in out or stamp >= out[job.name][0]:
                    out[job.name] = (stamp, state or "pending")
        return {name: state for name, (_, state) in out.items()}

    def unmet_checks(self, repo: Repo, issue: Issue) -> List[Tuple[str, str]]:
        """The base's required checks the head has not passed: ``(name, "expected", "in progress" or
        "failing")``."""
        assert issue.pull
        required = (
            repo.required_checks.get(issue.pull.base)
            if issue.pull.base in repo.protected
            else None
        )
        if not required:
            return []
        states = self.check_states(repo, self.head_sha(repo, issue))
        unmet = []
        for name in required:
            state = states.get(name)
            if state == "success":
                continue
            unmet.append(
                (
                    name,
                    (
                        "expected"
                        if state is None
                        else "in progress" if state == "pending" else "failing"
                    ),
                )
            )
        return unmet

    def mergeable_state(self, repo: Repo, issue: Issue) -> str:
        assert issue.pull
        if issue.state == "closed":
            return "unknown"
        if issue.pull.draft:
            return "draft"
        if not repo.bare.mergeable(
            self.base_sha(repo, issue), self.head_sha(repo, issue)
        ):
            return "dirty"
        if issue.pull.base in repo.protected and (
            not self._approved(issue) or self.unmet_checks(repo, issue)
        ):
            return "blocked"
        runs = self.runs_for(repo, self.head_sha(repo, issue))
        if any(r.conclusion == "failure" for r in runs):
            return "unstable"
        return "clean"

    def _approved(self, issue: Issue) -> bool:
        assert issue.pull
        latest: Dict[str, str] = {}
        for review in issue.pull.reviews:
            if review.state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                latest[review.user] = review.state
        return (
            "APPROVED" in latest.values() and "CHANGES_REQUESTED" not in latest.values()
        )

    def merge_pull(
        self,
        repo: Repo,
        issue: Issue,
        actor: str,
        *,
        method: str = "merge",
        title: Optional[str] = None,
        message: Optional[str] = None,
        expected_sha: Optional[str] = None,
        bypass_protection: bool = False,
    ) -> str:
        """Merge; ``bypass_protection`` (an admin's choice) merges without the approving review."""
        assert issue.pull
        if issue.state != "open":
            raise GitHubError(405, "Pull Request is not mergeable")
        head_sha = self.head_sha(repo, issue)
        if expected_sha and expected_sha != head_sha:
            raise GitHubError(
                409, "Head branch was modified. Review and try the merge again."
            )
        if issue.pull.draft:
            raise GitHubError(405, "Pull Request is still a draft")
        if (
            issue.pull.base in repo.protected
            and not self._approved(issue)
            and not bypass_protection
        ):
            raise GitHubError(
                405,
                "At least 1 approving review is required by reviewers with write access.",
            )
        unmet = self.unmet_checks(repo, issue)
        if unmet and not bypass_protection:
            name, state = unmet[0]
            raise GitHubError(405, f'Required status check "{name}" is {state}.')
        if method not in ("merge", "squash", "rebase"):
            raise GitHubError(422, "Validation Failed")
        if not repo.bare.mergeable(self.base_sha(repo, issue), head_sha):
            raise GitHubError(405, "Pull Request is not mergeable")
        if method == "merge":
            subject = (
                title
                or f"Merge pull request #{issue.number} from {repo.owner}/{issue.pull.head}"
            )
            text = subject + "\n\n" + (message if message is not None else issue.title)
        else:
            subject = title or f"{issue.title} (#{issue.number})"
            text = subject + ("\n\n" + message if message else "")
        before = repo.bare.ref_sha(issue.pull.base) or NULL_SHA
        sha = repo.bare.merge(
            issue.pull.base, issue.pull.head, method, text, self._who(actor)
        )
        now = time.time()
        issue.pull.merged, issue.pull.merged_at, issue.pull.merged_by = True, now, actor
        issue.pull.merge_commit_sha, issue.pull.closed_head_sha = sha, head_sha
        issue.state, issue.closed_at, issue.closed_by, issue.updated_at = (
            "closed",
            now,
            actor,
            now,
        )
        self._emit("pull_request_closed", repo=repo, issue=issue, actor=actor)
        self.pushed(repo, {f"refs/heads/{issue.pull.base}": (before, sha)}, actor)
        self._close_linked_issues(repo, issue, actor)
        return sha

    def _close_linked_issues(self, repo: Repo, pull: Issue, actor: str) -> None:
        for number in closing_references(pull.body):
            linked = repo.issues.get(number)
            if linked and not linked.pull and linked.state == "open":
                self.update_issue(
                    repo, linked, actor, state="closed", state_reason="completed"
                )

    def closing_pulls(self, repo: Repo, issue: Issue) -> List[Issue]:
        return [
            p
            for p in repo.pulls()
            if issue.number in closing_references(p.body)
            and not (p.state == "closed" and not p.pull.merged)
        ]

    # -- branches and files -------------------------------------------------

    def create_branch(
        self, repo: Repo, actor: str, branch: str, from_ref: Optional[str]
    ) -> str:
        source = repo.bare.ref_sha(from_ref or repo.default_branch)
        if source is None:
            raise GitHubError(404, "Not Found")
        if repo.bare.ref_sha(f"refs/heads/{branch}"):
            raise GitHubError(422, "Reference already exists")
        repo.bare.set_ref(f"refs/heads/{branch}", source)
        self._emit("branch_created", repo=repo, actor=actor, branch=branch)
        self.pushed(repo, {f"refs/heads/{branch}": (NULL_SHA, source)}, actor)
        return source

    # -- repository settings -------------------------------------------------

    def update_repo(
        self,
        repo: Repo,
        actor: str,
        *,
        description: Optional[str] = None,
        default_branch: Optional[str] = None,
        protected: Optional[List[str]] = None,
        self_hosted: Optional[bool] = None,
        required_checks: Optional[Dict[str, List[str]]] = None,
    ) -> Repo:
        with self._lock:
            if default_branch is not None and default_branch != repo.default_branch:
                if repo.bare.ref_sha(f"refs/heads/{default_branch}") is None:
                    raise GitHubError(422, f"No branch {default_branch}")
                repo.bare.set_head(default_branch)
                repo.default_branch = default_branch
            if description is not None:
                repo.description = description
            if protected is not None:
                repo.protected = list(
                    dict.fromkeys(b.strip() for b in protected if b.strip())
                )
                # A branch no longer protected requires nothing.
                repo.required_checks = {
                    b: c for b, c in repo.required_checks.items() if b in repo.protected
                }
            if required_checks is not None:
                for branch in required_checks:
                    if branch not in repo.protected:
                        raise GitHubError(
                            422,
                            f"{branch} is not protected: protect it to require checks",
                        )
                repo.required_checks = {
                    b: list(dict.fromkeys(n.strip() for n in names if n.strip()))
                    for b, names in required_checks.items()
                }
                repo.required_checks = {
                    b: c for b, c in repo.required_checks.items() if c
                }
            if self_hosted is not None:
                repo.self_hosted = self_hosted
        self._emit("repo_updated", repo=repo, actor=actor)
        return repo

    def delete_repo(self, repo: Repo, actor: str) -> None:
        import shutil

        if self.actions is not None:
            for run in repo.runs:
                if run.status != "completed":
                    self.actions.cancel(run.id)
            self.actions.wait([run.id for run in repo.runs])
        with self._lock:
            self.repos.pop(repo.full_name, None)
        shutil.rmtree(repo.bare.path, ignore_errors=True)
        self._emit("repo_deleted", repo=repo, actor=actor)

    # -- labels ------------------------------------------------------------------

    def create_label(
        self, repo: Repo, name: str, color: str = "ededed", description: str = ""
    ) -> Label:
        name = name.strip()
        if not name:
            raise GitHubError(
                422,
                "Validation Failed",
                [{"resource": "Label", "field": "name", "code": "missing_field"}],
            )
        if any(n.lower() == name.lower() for n in repo.labels):
            raise GitHubError(
                422,
                "Validation Failed",
                [
                    {
                        "resource": "Label",
                        "code": "already_exists",
                        "field": "name",
                        "message": f"A label named {name} already exists",
                    }
                ],
            )
        with self._lock:
            label = Label(self._id(), name, color.lstrip("#") or "ededed", description)
            repo.labels[name] = label
        self.changed(repo=repo)
        return label

    def update_label(
        self,
        repo: Repo,
        name: str,
        *,
        new_name: Optional[str] = None,
        color: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Label:
        label = repo.labels.get(name)
        if label is None:
            raise GitHubError(404, "Not Found")
        renamed = new_name is not None and new_name.strip() and new_name.strip() != name
        if renamed and any(
            n.lower() == new_name.strip().lower() for n in repo.labels if n != name
        ):
            raise GitHubError(
                422,
                "Validation Failed",
                [
                    {
                        "resource": "Label",
                        "code": "already_exists",
                        "field": "name",
                        "message": f"A label named {new_name.strip()} already exists",
                    }
                ],
            )
        touched = []
        with self._lock:
            if color is not None:
                label.color = color.lstrip("#")
            if description is not None:
                label.description = description
            if renamed:
                label.name = new_name.strip()
                repo.labels = {
                    (label.name if n == name else n): l for n, l in repo.labels.items()
                }
                for issue in repo.issues.values():
                    if name in issue.labels:
                        issue.labels = [
                            label.name if x == name else x for x in issue.labels
                        ]
                        touched.append(issue)
        self.changed(repo=repo)
        for issue in touched:
            self.changed(repo=repo, issue=issue)
        return label

    def delete_label(self, repo: Repo, name: str) -> None:
        if name not in repo.labels:
            raise GitHubError(404, "Not Found")
        touched = []
        with self._lock:
            del repo.labels[name]
            for issue in repo.issues.values():
                if name in issue.labels:
                    issue.labels = [x for x in issue.labels if x != name]
                    touched.append(issue)
        self.changed(repo=repo)
        for issue in touched:
            self.changed(repo=repo, issue=issue)

    def update_branch(self, repo: Repo, issue: Issue, actor: str) -> str:
        """Merge the base into a pull request's branch (GitHub's "Update branch")."""
        assert issue.pull
        base, head = self.base_sha(repo, issue), self.head_sha(repo, issue)
        if repo.bare.merge_base(base, head) == base:
            raise GitHubError(422, "There are no new commits on the base branch.")
        try:
            sha = repo.bare.merge(
                issue.pull.head,
                issue.pull.base,
                "merge",
                f"Merge branch '{issue.pull.base}' into {issue.pull.head}",
                self._who(actor),
            )
        except GitError:
            raise GitHubError(422, "merge conflict between base and head")
        self.pushed(repo, {f"refs/heads/{issue.pull.head}": (head, sha)}, actor)
        return sha

    def delete_branch(self, repo: Repo, actor: str, branch: str) -> str:
        """Delete ``branch`` (not the default or a protected one); its last sha.
        A pull request still open from it closes, as on GitHub."""
        if branch == repo.default_branch:
            raise GitHubError(422, "Cannot delete the default branch")
        if branch in repo.protected:
            raise GitHubError(422, f"Cannot delete the protected branch {branch}")
        sha = repo.bare.ref_sha(f"refs/heads/{branch}")
        if sha is None:
            raise GitHubError(422, "Reference does not exist")
        for issue in repo.pulls():
            if issue.state == "open" and issue.pull.head == branch:
                self.update_issue(repo, issue, actor, state="closed")
        repo.bare.delete_ref(f"refs/heads/{branch}")
        self._emit("branch_deleted", repo=repo, actor=actor, branch=branch)
        self.pushed(repo, {f"refs/heads/{branch}": (sha, NULL_SHA)}, actor)
        return sha

    def restore_branch(self, repo: Repo, actor: str, branch: str, sha: str) -> None:
        """A deleted branch back, at ``sha``."""
        if repo.bare.ref_sha(f"refs/heads/{branch}"):
            raise GitHubError(422, "Reference already exists")
        if repo.bare.commit(sha) is None:
            raise GitHubError(422, "Object does not exist")
        repo.bare.set_ref(f"refs/heads/{branch}", sha)
        self._emit("branch_created", repo=repo, actor=actor, branch=branch)
        self.pushed(repo, {f"refs/heads/{branch}": (NULL_SHA, sha)}, actor)

    def write_files(
        self,
        repo: Repo,
        actor: str,
        branch: str,
        files: List[Tuple[str, Optional[bytes]]],
        message: str,
    ) -> str:
        before = repo.bare.ref_sha(branch)
        if before is None:
            raise GitHubError(404, "Branch not found")
        if branch in repo.protected:
            raise GitHubError(
                409,
                f"Changes must be made through a pull request. Cannot update the protected branch {branch}.",
            )
        try:
            after = repo.bare.write_files(branch, files, message, self._who(actor))
        except GitError as exc:
            raise GitHubError(422, str(exc))
        self.pushed(repo, {f"refs/heads/{branch}": (before, after)}, actor)
        return after

    def pushed(
        self, repo: Repo, updates: Dict[str, Tuple[str, str]], actor: str
    ) -> None:
        """Refs moved (a push, or a change GitHub made): sync pull requests, run workflows, tell everyone."""
        for ref, (before, after) in updates.items():
            if not ref.startswith("refs/heads/"):
                continue
            branch = ref[len("refs/heads/") :]
            commits = []
            if after != NULL_SHA:
                rev = after if before == NULL_SHA else f"{before}..{after}"
                commits = repo.bare.commits(rev, max_count=20)
            if after == NULL_SHA:
                repo.pushes.pop(branch, None)
            else:
                repo.pushes[branch] = {"actor": actor, "at": time.time()}
            self._emit(
                "push",
                repo=repo,
                ref=ref,
                before=before,
                after=after,
                actor=actor,
                commits=commits,
            )
            if after == NULL_SHA:
                continue
            synced = []
            for issue in repo.pulls():
                if (
                    issue.state == "open"
                    and issue.pull.head == branch
                    and before != NULL_SHA
                ):
                    issue.updated_at = time.time()
                    synced.append(issue)
                    self._emit(
                        "pull_request_synchronize",
                        repo=repo,
                        issue=issue,
                        actor=actor,
                        before=before,
                        after=after,
                    )
            # Workflow files run as their triggers say: a push to any branch, and a pull request's update.
            self._start_ci(repo, branch, after, "push", actor, before=before)
            for issue in synced:
                self._start_ci(
                    repo,
                    branch,
                    after,
                    "pull_request",
                    actor,
                    pull=issue,
                    action="synchronize",
                )

    # -- Actions ----------------------------------------------------------

    def runs_for(self, repo: Repo, sha: str) -> List[WorkflowRun]:
        return [r for r in repo.runs if r.head_sha == sha]

    def run(self, repo: Repo, run_id: int) -> WorkflowRun:
        found = next((r for r in repo.runs if r.id == int(run_id)), None)
        if found is None:
            raise GitHubError(404, "Not Found")
        return found

    def job(self, repo: Repo, job_id: int) -> Tuple[WorkflowRun, Job]:
        for run in repo.runs:
            for job in run.jobs:
                if job.id == int(job_id):
                    return run, job
        raise GitHubError(404, "Not Found")

    # -- workflow files (actions.py runs them) ---------------------------------

    def _workflow_files(self, repo: Repo, sha: str) -> List[Any]:
        if self.actions is None:
            return []
        from .actions import workflows_at

        return workflows_at(repo, sha)

    def workflows(self, repo: Repo) -> List[Dict[str, Any]]:
        """The repository's workflows: its workflow files on the default branch."""
        sha = repo.bare.ref_sha(repo.default_branch)
        files = self._workflow_files(repo, sha) if sha else []
        if files:
            out = []
            for wf in files:
                if wf.path not in repo.workflow_ids:
                    repo.workflow_ids[wf.path] = self._id()
                    self.changed(repo=repo)
                out.append(
                    {
                        "id": repo.workflow_ids[wf.path],
                        "name": wf.name,
                        "path": wf.path,
                        "dispatch": "workflow_dispatch" in wf.on,
                    }
                )
            return out
        return []

    def _start_workflow(
        self,
        repo: Repo,
        workflow: Any,
        sha: str,
        branch: str,
        event: str,
        actor: str,
        pull: Optional[Issue] = None,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRun:
        from .actions import job_names

        now = time.time()
        with self._lock:
            if workflow.path not in repo.workflow_ids:
                repo.workflow_ids[workflow.path] = self._id()
            workflow_id = repo.workflow_ids[workflow.path]
            commit = repo.bare.commit(sha)
            pulls = (
                [pull.number]
                if pull
                else [
                    i.number
                    for i in repo.pulls()
                    if i.state == "open" and i.pull.head == branch
                ]
            )
            run = WorkflowRun(
                id=self._id(),
                workflow_id=workflow_id,
                name=workflow.name,
                path=workflow.path,
                run_number=len([r for r in repo.runs if r.workflow_id == workflow_id])
                + 1,
                event=event,
                status="queued",
                conclusion=None,
                head_branch=branch,
                head_sha=sha,
                head_message=commit.message if commit else "",
                actor=actor,
                created_at=now,
                updated_at=now,
                run_started_at=now,
                pull_requests=pulls,
            )
            run.jobs = [
                Job(
                    self._id(),
                    run.id,
                    name,
                    "queued",
                    None,
                    sha,
                    branch,
                    now,
                    None,
                    None,
                )
                for name in job_names(workflow)
            ]
            repo.runs.append(run)
        self._emit("workflow_run", repo=repo, run=run, action="requested")
        self.actions.start(
            repo, run, workflow, self._github_context(repo, run, pull, inputs)
        )
        return run

    def _github_context(
        self,
        repo: Repo,
        run: WorkflowRun,
        pull: Optional[Issue],
        inputs: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        server = self.actions.base_url().rstrip("/")
        event: Dict[str, Any] = {"inputs": inputs or {}}
        if pull is not None and pull.pull:
            event["pull_request"] = {
                "number": pull.number,
                "title": pull.title,
                "head": {"ref": pull.pull.head, "sha": run.head_sha},
                "base": {"ref": pull.pull.base},
            }
            event["number"] = pull.number
        return {
            "event_name": run.event,
            "event": event,
            "sha": run.head_sha,
            "ref": f"refs/heads/{run.head_branch}",
            "ref_name": run.head_branch,
            "head_ref": pull.pull.head if pull and pull.pull else "",
            "base_ref": pull.pull.base if pull and pull.pull else "",
            "repository": repo.full_name,
            "repository_owner": repo.owner,
            "actor": run.actor,
            "server_url": server,
            "api_url": f"{server}/github/api",
            "run_id": run.id,
            "run_number": run.run_number,
            "run_attempt": run.run_attempt,
            "workflow": run.name,
            "job": "",
            "workspace": "",
            "token": "",
        }

    # What the runner reports.

    def run_started(self, repo: Repo, run: WorkflowRun) -> None:
        with self._lock:
            run.status, run.updated_at, run.run_started_at = (
                "in_progress",
                time.time(),
                time.time(),
            )
        self._emit("workflow_run", repo=repo, run=run, action="in_progress")

    def job_started(self, repo: Repo, run: WorkflowRun, job: Job) -> None:
        with self._lock:
            job.status, job.started_at = "in_progress", time.time()
        self._emit("workflow_job", repo=repo, run=run, job=job)

    def job_log(self, repo: Repo, run: WorkflowRun, job: Job, log: str) -> None:
        job.log = log  # read live; saved with the job's next change

    def job_steps(
        self, repo: Repo, run: WorkflowRun, job: Job, steps: List[Step]
    ) -> None:
        with self._lock:
            job.steps = list(steps)
        self._emit("workflow_job", repo=repo, run=run, job=job)

    def job_finished(
        self,
        repo: Repo,
        run: WorkflowRun,
        job: Job,
        conclusion: str,
        log: str,
        steps: List[Step],
    ) -> None:
        now = time.time()
        with self._lock:
            job.status, job.conclusion, job.completed_at = "completed", conclusion, now
            job.started_at = job.started_at or now
            if log:
                job.log = log
            if steps:
                job.steps = list(steps)
        self._emit("workflow_job", repo=repo, run=run, job=job)

    def run_finished(self, repo: Repo, run: WorkflowRun, conclusion: str) -> None:
        with self._lock:
            run.status, run.conclusion, run.updated_at = (
                "completed",
                conclusion,
                time.time(),
            )
        self._emit("workflow_run", repo=repo, run=run, action="completed")

    def dispatch(
        self,
        repo: Repo,
        workflow_ref: str,
        ref: str,
        actor: str,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> WorkflowRun:
        """Run a workflow that has ``workflow_dispatch``, on ``ref``."""
        sha = repo.bare.ref_sha(ref)
        if sha is None:
            raise GitHubError(422, f"No ref found for: {ref}")
        for wf in self._workflow_files(repo, sha):
            ids = {
                str(repo.workflow_ids.get(wf.path, "")),
                wf.path,
                wf.path.rsplit("/", 1)[-1],
            }
            if workflow_ref in ids:
                if "workflow_dispatch" not in wf.on:
                    raise GitHubError(
                        422, "Workflow does not have 'workflow_dispatch' trigger"
                    )
                # As GitHub: the workflow's declared inputs, their defaults where none is given.
                declared = (wf.on.get("workflow_dispatch") or {}).get("inputs") or {}
                given = dict(inputs or {})
                for key, spec in declared.items():
                    spec = spec or {}
                    if key not in given and spec.get("default") is not None:
                        given[key] = spec["default"]
                    if spec.get("required") and given.get(key) in (None, ""):
                        raise GitHubError(422, f"Required input '{key}' not provided")
                return self._start_workflow(
                    repo, wf, sha, ref, "workflow_dispatch", actor, inputs=given
                )
        raise GitHubError(404, "Not Found")

    def cancel_run(self, repo: Repo, run: WorkflowRun) -> None:
        if run.status == "completed":
            raise GitHubError(409, "Cannot cancel a workflow run that is completed.")
        if self.actions is None or not self.actions.cancel(run.id):
            # Its runner is gone (a restart): it ends here.
            self.run_finished(repo, run, "cancelled")

    def _start_ci(
        self,
        repo: Repo,
        branch: str,
        sha: str,
        event: str,
        actor: str,
        *,
        pull: Optional[Issue] = None,
        action: str = "",
        before: Optional[str] = None,
    ) -> Optional[WorkflowRun]:
        files = self._workflow_files(repo, sha)
        if files:
            from .actions import changed_paths, triggered

            target = pull.pull.base if pull is not None and pull.pull else branch
            if event == "pull_request" and pull is not None:
                base = self.base_sha(repo, pull)
                paths = changed_paths(
                    repo, repo.bare.merge_base(base, sha) or base, sha
                )
            else:
                paths = changed_paths(
                    repo, before if before and before != NULL_SHA else None, sha
                )
            # A file that cannot be read runs too, as GitHub shows it: a failed run saying why.
            started = [
                self._start_workflow(repo, wf, sha, branch, event, actor, pull=pull)
                for wf in files
                if (wf.doc.get("_error") and event in ("push", "pull_request"))
                or triggered(wf, event, target, paths, action)
            ]
            return started[0] if started else None
        return None

    def rerun(self, repo: Repo, run: WorkflowRun, actor: str) -> WorkflowRun:
        if self.actions is not None:
            from .actions import job_names

            workflow = next(
                (
                    wf
                    for wf in self._workflow_files(repo, run.head_sha)
                    if wf.path == run.path
                ),
                None,
            )
            if workflow is None:
                raise GitHubError(404, f"{run.path} is not at {run.head_sha[:7]}")
            if run.status != "completed":
                raise GitHubError(403, "This workflow is already running")
            now = time.time()
            with self._lock:
                run.run_attempt += 1
                run.status, run.conclusion, run.updated_at = "queued", None, now
                run.jobs = [
                    Job(
                        self._id(),
                        run.id,
                        name,
                        "queued",
                        None,
                        run.head_sha,
                        run.head_branch,
                        now,
                        None,
                        None,
                    )
                    for name in job_names(workflow)
                ]
            self._emit("workflow_run", repo=repo, run=run, action="requested")
            pull = next(
                (i for i in repo.pulls() if i.number in run.pull_requests), None
            )
            self.actions.start(
                repo, run, workflow, self._github_context(repo, run, pull, None)
            )
            return run
        raise GitHubError(409, "Workflows do not run in this workplace")


_CLOSING = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b", re.I)


def closing_references(body: str) -> List[int]:
    return [int(n) for n in _CLOSING.findall(body or "")]


def _line_in_patch(patch: str, line: int, side: str) -> bool:
    """Whether ``line`` (in the new file for RIGHT, the old for LEFT) is in the patch's hunks."""
    old = new = 0
    for row in patch.splitlines():
        if row.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
            if m:
                old, new = int(m.group(1)), int(m.group(2))
            continue
        if row.startswith("+"):
            if side == "RIGHT" and new == line:
                return True
            new += 1
        elif row.startswith("-"):
            if side == "LEFT" and old == line:
                return True
            old += 1
        else:
            if (side == "RIGHT" and new == line) or (side == "LEFT" and old == line):
                return True
            old += 1
            new += 1
    return False
