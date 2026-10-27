"""Linear's state: the workspace, teams and their workflows, issues, comments
and agent sessions.

Every change emits an event (``subscribe``): the UI follows them, and the
webhooks (:mod:`.webhooks`) turn agent-session changes into Linear's
``AgentSessionEvent`` deliveries.

Seed (``linear:`` in a workplace seed)::

    organization: {name: Acme, url_key: acme}
    users:
      - {name: Priya Shah, display_name: priya, email: priya@acme.test, slack: U0ACME0PRI}
    apps:                                 # agents people can delegate to
      - {name: Acme Factory, display_name: acme-factory, token: lin_oauth_..., slack: U0ACME0FAC}
    teams:
      - key: ENG
        name: Engineering
        labels: [Bug, Feature, "factory:Acme"]
        issues:
          - {title: ..., description: ..., creator: priya, state: Backlog, priority: 2, labels: [Bug]}

A team gets Linear's default workflow (Backlog, Todo, In Progress, Done,
Canceled, Duplicate). An agent session starts when a person delegates an
issue to an app or @-mentions it in a comment; the app reports through
activities (``thought``, ``action``, ``elicitation``, ``response``,
``error``), and the session's status follows the last one, as on Linear.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

__all__ = ["LinearError", "LinearState", "LinearWebhook", "iso", "PROJECT_STATUSES"]

Listener = Callable[[str, Dict[str, Any]], None]

PRIORITIES = {0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}

DEFAULT_STATES = [
    ("Backlog", "backlog", "#bec2c8"),
    ("Todo", "unstarted", "#e2e2e2"),
    ("In Progress", "started", "#f2c94c"),
    ("Done", "completed", "#5e6ad2"),
    ("Canceled", "canceled", "#95a2b3"),
    ("Duplicate", "duplicate", "#95a2b3"),
]

#: An issue status's types, as Linear's workflows have them.
STATE_TYPES = (
    "triage",
    "backlog",
    "unstarted",
    "started",
    "completed",
    "canceled",
    "duplicate",
)

#: Linear's project statuses: (name, type, color).
PROJECT_STATUSES = [
    ("Backlog", "backlog", "#bec2c8"),
    ("Planned", "planned", "#e2e2e2"),
    ("In Progress", "started", "#f2c94c"),
    ("Paused", "paused", "#95a2b3"),
    ("Completed", "completed", "#5e6ad2"),
    ("Canceled", "canceled", "#95a2b3"),
]

HEALTH = ("onTrack", "atRisk", "offTrack")


def iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    return (
        datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{int((t % 1) * 1000):03d}Z"
    )


def _uuid() -> str:
    return str(uuid.uuid4())


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "issue"


class LinearError(Exception):
    """A request Linear refuses; ``message`` is what it says."""

    def __init__(self, message: str, *, api: bool = False):
        super().__init__(message)
        self.message = message
        #: An API error (``{"error": "invalid_request", ...}``) rather than the MCP server's own.
        self.api = api


@dataclass
class User:
    id: str
    name: str
    display_name: str
    email: str
    is_app: bool = False
    is_admin: bool = False
    slack: Optional[str] = None
    token: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    #: ``False`` for a removed agent or a disabled person: history only.
    active: bool = True


@dataclass
class State:
    id: str
    name: str
    type: str
    color: str
    position: int


@dataclass
class Label:
    id: str
    name: str
    color: str
    description: Optional[str] = None
    team_id: Optional[str] = None
    #: Retired: kept on the issues that have it, no longer applied to others.
    retired_at: Optional[float] = None


@dataclass
class Team:
    id: str
    key: str
    name: str
    description: str = ""
    states: List[State] = field(default_factory=list)
    next_number: int = 1
    created_at: float = field(default_factory=time.time)
    member_ids: List[str] = field(default_factory=list)
    next_project_number: int = 1
    next_cycle_number: int = 1

    def state(self, ref: str) -> Optional[State]:
        """By id, name or type, as Linear resolves ``state``."""
        lowered = ref.lower()
        for s in self.states:
            if s.id == ref or s.name.lower() == lowered:
                return s
        return next((s for s in self.states if s.type == lowered), None)


@dataclass
class Attachment:
    id: str
    title: str
    url: str
    subtitle: Optional[str] = None
    content: Optional[bytes] = None
    content_type: Optional[str] = None
    created_at: float = field(default_factory=time.time)


@dataclass
class Comment:
    id: str
    issue_id: str
    user_id: str
    body: str
    created_at: float
    updated_at: float
    parent_id: Optional[str] = None
    agent_session_id: Optional[str] = None


@dataclass
class Activity:
    id: str
    session_id: str
    content: Dict[str, Any]
    created_at: float
    user_id: Optional[str] = None
    signal: Optional[str] = None
    ephemeral: bool = False

    @property
    def type(self) -> str:
        return str(self.content.get("type") or "")


@dataclass
class Session:
    id: str
    issue_id: str
    app_user_id: str
    creator_id: str
    created_at: float
    updated_at: float
    status: str = "pending"
    comment_id: Optional[str] = None
    activities: List[Activity] = field(default_factory=list)
    external_urls: List[Dict[str, str]] = field(default_factory=list)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None


@dataclass
class Issue:
    id: str
    team_id: str
    number: int
    identifier: str
    title: str
    description: str
    creator_id: str
    state_id: str
    created_at: float
    updated_at: float
    priority: int = 0
    label_ids: List[str] = field(default_factory=list)
    assignee_id: Optional[str] = None
    delegate_id: Optional[str] = None
    parent_id: Optional[str] = None
    attachments: List[Attachment] = field(default_factory=list)
    state_history: List[Dict[str, Any]] = field(default_factory=list)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    canceled_at: Optional[float] = None
    archived_at: Optional[float] = None
    due_date: Optional[str] = None
    estimate: Optional[float] = None
    blocks: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    duplicate_of: Optional[str] = None
    project_id: Optional[str] = None
    milestone_id: Optional[str] = None
    cycle_id: Optional[str] = None
    #: Who is notified of its changes: its creator, assignee and commenters, and who subscribed.
    subscriber_ids: List[str] = field(default_factory=list)


@dataclass
class Project:
    id: str
    #: ``P-ENG-1``: its lead team's key and number.
    identifier: str
    slug_id: str
    name: str
    lead_team_id: str
    team_ids: List[str]
    creator_id: str
    created_at: float
    updated_at: float
    summary: str = ""
    description: str = ""
    icon: Optional[str] = None
    color: str = "#bec2c8"
    #: A project status's type (``PROJECT_STATUSES``).
    status: str = "backlog"
    priority: int = 0
    lead_id: Optional[str] = None
    member_ids: List[str] = field(default_factory=list)
    start_date: Optional[str] = None
    target_date: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    canceled_at: Optional[float] = None
    archived_at: Optional[float] = None
    links: List[Attachment] = field(default_factory=list)


@dataclass
class Milestone:
    id: str
    project_id: str
    name: str
    created_at: float
    updated_at: float
    description: str = ""
    target_date: Optional[str] = None
    sort_order: float = 0.0


@dataclass
class Cycle:
    id: str
    team_id: str
    number: int
    #: ISO dates: the first day, and the day after the last.
    starts_at: str
    ends_at: str
    created_at: float
    name: Optional[str] = None
    description: str = ""


@dataclass
class Document:
    id: str
    slug_id: str
    title: str
    content: str
    creator_id: str
    created_at: float
    updated_at: float
    project_id: Optional[str] = None
    issue_id: Optional[str] = None
    team_id: Optional[str] = None
    cycle_id: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    updated_by_id: Optional[str] = None


@dataclass
class StatusUpdate:
    id: str
    project_id: str
    user_id: str
    body: str
    health: str
    created_at: float
    updated_at: float


@dataclass
class Notification:
    """An item in someone's inbox."""

    id: str
    user_id: str
    type: str
    created_at: float
    actor_id: Optional[str] = None
    issue_id: Optional[str] = None
    comment_id: Optional[str] = None
    project_id: Optional[str] = None
    #: A line about what happened, for the inbox.
    text: str = ""
    read_at: Optional[float] = None
    snoozed_until: Optional[float] = None


@dataclass
class LinearWebhook:
    url: str
    secret: str
    #: The app (agent) whose sessions it receives; ``None`` for every app.
    app_user_id: Optional[str] = None
    id: str = ""


_MENTION = re.compile(r"@([A-Za-z0-9._-]+)")


class LinearState:
    def __init__(self) -> None:
        self.org_id = _uuid()
        self.org_name = ""
        self.url_key = ""
        self.users: Dict[str, User] = {}
        self.teams: Dict[str, Team] = {}
        self.labels: Dict[str, Label] = {}
        self.issues: Dict[str, Issue] = {}
        self.comments: Dict[str, Comment] = {}
        self.sessions: Dict[str, Session] = {}
        self.projects: Dict[str, Project] = {}
        self.milestones: Dict[str, Milestone] = {}
        self.cycles: Dict[str, Cycle] = {}
        self.documents: Dict[str, Document] = {}
        self.status_updates: Dict[str, StatusUpdate] = {}
        self.notifications: Dict[str, Notification] = {}
        self.oauth_client_id = _uuid()
        self.hooks: List[LinearWebhook] = []
        self._listeners: List[Listener] = []
        self._lock = threading.RLock()

    # -- events -------------------------------------------------------------

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _emit(self, kind: str, **data: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(kind, data)
            except Exception:
                pass

    # -- seed -----------------------------------------------------------------

    def load_seed(self, seed: Dict[str, Any]) -> None:
        with self._lock:
            org = seed.get("organization") or {}
            self.org_name = str(org.get("name") or "")
            self.url_key = str(
                org.get("url_key") or (slugify(self.org_name) if self.org_name else "")
            )
            (
                self.users,
                self.teams,
                self.labels,
                self.issues,
                self.comments,
                self.sessions,
            ) = ({}, {}, {}, {}, {}, {})
            self.projects, self.milestones, self.cycles, self.documents = {}, {}, {}, {}
            self.status_updates, self.notifications = {}, {}
            started = time.time() - 14 * 86400
            for n, raw in enumerate(seed.get("users") or []):
                self._add_user(raw, started + n, is_app=False)
            for n, raw in enumerate(seed.get("apps") or []):
                self._add_user(raw, started + 100 + n, is_app=True)
            for n, raw in enumerate(seed.get("teams") or []):
                team = Team(
                    id=_uuid(),
                    key=str(raw["key"]),
                    name=str(raw["name"]),
                    description=str(raw.get("description") or ""),
                    created_at=started + 200 + n,
                )
                team.states = [
                    State(_uuid(), name, kind, color, i)
                    for i, (name, kind, color) in enumerate(DEFAULT_STATES)
                ]
                team.member_ids = [u.id for u in self.users.values() if not u.is_app]
                self.teams[team.id] = team
                for label in raw.get("labels") or []:
                    label = {"name": label} if isinstance(label, str) else label
                    self._add_label(
                        label["name"],
                        label.get("color") or "#bec2c8",
                        label.get("description"),
                        team.id,
                    )
                for i, issue in enumerate(raw.get("issues") or []):
                    at = started + 3600 * (i + 1)
                    created = self.create_issue(
                        team.id,
                        self._user_ref(issue.get("creator") or self._first_person()),
                        str(issue["title"]),
                        str(issue.get("description") or ""),
                        priority=int(issue.get("priority") or 0),
                        state=issue.get("state"),
                        labels=issue.get("labels") or [],
                        at=at,
                        emit=False,
                    )
                    for j, comment in enumerate(issue.get("comments") or []):
                        self.add_comment(
                            created.id,
                            self._user_ref(comment.get("author") or created.creator_id),
                            str(comment["body"]),
                            at=at + 60 * (j + 1),
                            emit=False,
                        )
            self.hooks = []
        self._emit("reset")

    def _add_user(self, raw: Dict[str, Any], at: float, *, is_app: bool) -> User:
        display = str(
            raw.get("display_name") or raw.get("login") or slugify(str(raw["name"]))
        )
        user = User(
            id=_uuid(),
            name=str(raw["name"]),
            display_name=display,
            email=str(raw.get("email") or ""),
            is_app=is_app,
            is_admin=bool(raw.get("admin", False)),
            slack=raw.get("slack"),
            created_at=at,
        )
        self.users[user.id] = user
        return user

    def create_label(
        self,
        name: str,
        color: str,
        description: Optional[str] = None,
        team_id: Optional[str] = None,
    ) -> Label:
        """A new label: the team's, or the workspace's without ``team_id``."""
        with self._lock:
            return self._add_label(name, color, description, team_id)

    def _add_label(
        self, name: str, color: str, description: Optional[str], team_id: Optional[str]
    ) -> Label:
        label = Label(
            _uuid(),
            name,
            color if color.startswith("#") else f"#{color}",
            description,
            team_id,
        )
        self.labels[label.id] = label
        self._emit("label_created", label=label)
        return label

    def _first_person(self) -> str:
        return next(u.id for u in self.users.values() if not u.is_app)

    # -- the organization and its people ---------------------------------------------

    def set_organization(self, name: str, url_key: str) -> None:
        with self._lock:
            self.org_name, self.url_key = name, url_key
        self._emit("organization")

    def add_person(
        self,
        name: str,
        display_name: str,
        email: str,
        *,
        admin: bool = False,
        is_app: bool = False,
        slack: Optional[str] = None,
        token: Optional[str] = None,
    ) -> User:
        with self._lock:
            if any(
                u.display_name.lower() == display_name.lower()
                for u in self.users.values()
            ):
                raise LinearError(f'A user named "{display_name}" already exists')
            user = User(
                id=_uuid(),
                name=name,
                display_name=display_name,
                email=email,
                is_app=is_app,
                is_admin=admin,
                slack=slack,
                token=token,
            )
            self.users[user.id] = user
        self._emit("user", user=user)
        return user

    def changed(self) -> None:
        """Something changed that is no event of Linear's (a webhook)."""
        self._emit("changed")

    def update_user(self, user: User, **fields: Any) -> User:
        with self._lock:
            for key, value in fields.items():
                if key not in ("name", "email", "is_admin", "token", "slack", "active"):
                    raise LinearError(f"Cannot change {key}")
                setattr(user, key, value)
        self._emit("user", user=user)
        return user

    def create_team(
        self,
        key: str,
        name: str,
        description: str = "",
        creator_id: Optional[str] = None,
    ) -> Team:
        """A new team, with Linear's default workflow."""
        key, name = (key or "").strip().upper(), (name or "").strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{0,6}", key):
            raise LinearError(
                "A team's key is a letter and up to 6 more letters or digits"
            )
        if not name:
            raise LinearError("A team has a name")
        with self._lock:
            if any(
                t.key == key or t.name.lower() == name.lower()
                for t in self.teams.values()
            ):
                raise LinearError(
                    f'A team "{name}" or with the key {key} already exists'
                )
            team = Team(
                id=_uuid(),
                key=key,
                name=name,
                description=description,
                member_ids=[creator_id] if creator_id else [],
            )
            team.states = [
                State(_uuid(), n, kind, color, i)
                for i, (n, kind, color) in enumerate(DEFAULT_STATES)
            ]
            self.teams[team.id] = team
        self._emit("team", team=team)
        return team

    # -- lookups ----------------------------------------------------------------

    def user_for_token(self, token: str) -> Optional[User]:
        return next(
            (
                u
                for u in self.users.values()
                if u.token and u.token == token and u.active
            ),
            None,
        )

    def _user_ref(self, ref: str) -> str:
        user = self.find_user(ref)
        if user is None:
            raise LinearError(f'Could not find user "{ref}"')
        return user.id

    def find_user(
        self, ref: Optional[str], me: Optional[User] = None
    ) -> Optional[User]:
        if ref is None:
            return None
        if ref == "me" and me is not None:
            return me
        lowered = ref.lower()
        for u in self.users.values():
            if ref == u.id or lowered in (
                u.name.lower(),
                u.display_name.lower(),
                u.email.lower(),
            ):
                return u
        return None

    def find_team(self, ref: str) -> Optional[Team]:
        lowered = (ref or "").lower()
        return next(
            (
                t
                for t in self.teams.values()
                if ref == t.id or lowered in (t.name.lower(), t.key.lower())
            ),
            None,
        )

    def find_issue(self, ref: str) -> Optional[Issue]:
        if ref in self.issues:
            return self.issues[ref]
        upper = (ref or "").upper()
        return next((i for i in self.issues.values() if i.identifier == upper), None)

    def find_label(self, ref: str, team_id: Optional[str]) -> Optional[Label]:
        lowered = ref.lower()
        for label in self.labels.values():
            if label.team_id not in (None, team_id):
                continue
            if ref == label.id or label.name.lower() == lowered:
                return label
        return None

    def state(self, issue: Issue) -> State:
        return next(
            s for s in self.teams[issue.team_id].states if s.id == issue.state_id
        )

    def issue_url(self, base: str, issue: Issue) -> str:
        return f"{base}/linear/{self.url_key}/issue/{issue.identifier}/{slugify(issue.title)}"

    def branch_name(self, issue: Issue, user: Optional[User]) -> str:
        who = (user.display_name if user else "linear").lower().replace(".", "")
        return f"{who}/{issue.identifier.lower()}-{slugify(issue.title)}"

    # -- issues -------------------------------------------------------------------

    def create_issue(
        self,
        team_id: str,
        creator_id: str,
        title: str,
        description: str = "",
        *,
        priority: int = 0,
        state: Optional[str] = None,
        labels: List[str] = (),
        assignee: Optional[str] = None,
        at: Optional[float] = None,
        emit: bool = True,
    ) -> Issue:
        team = self.teams[team_id]
        now = at or time.time()
        target = team.state(state) if state else team.state("Backlog")
        if target is None:
            raise LinearError(f'Could not find state "{state}"')
        label_ids = []
        for name in labels:
            label = self.find_label(name, team.id)
            if label is None:
                raise LinearError(
                    f'Could not find label "{name}" for labels. Each may not exist, or your current permissions may not allow access to it. Provide labels as a JSON array of strings (e.g. ["Bug", "Improvement"]) where each entry is an existing label name or ID. No labels are changed while any entry is unresolved'
                )
            label_ids.append(label.id)
        with self._lock:
            number = team.next_number
            team.next_number += 1
            issue = Issue(
                id=_uuid(),
                team_id=team.id,
                number=number,
                identifier=f"{team.key}-{number}",
                title=title,
                description=normalize_markdown(description),
                creator_id=creator_id,
                state_id=target.id,
                created_at=now,
                updated_at=now,
                priority=priority,
                label_ids=label_ids,
                assignee_id=assignee,
            )
            issue.state_history.append(
                {"state_id": target.id, "started_at": now, "ended_at": None}
            )
            self._apply_state_times(issue, target, now)
            self._subscribe(issue, creator_id)
            self._subscribe(issue, assignee)
            self.issues[issue.id] = issue
        if emit:
            self._emit("issue_created", issue=issue)
            if assignee:
                creator = self.users.get(creator_id)
                self._notify(
                    [assignee],
                    "issueAssignedToYou",
                    creator_id,
                    issue=issue,
                    text=f"{creator.name if creator else 'Someone'} assigned {issue.identifier} to you",
                )
        return issue

    def _apply_state_times(self, issue: Issue, state: State, now: float) -> None:
        if state.type == "started" and issue.started_at is None:
            issue.started_at = now
        if state.type == "completed":
            issue.completed_at = now
        if state.type in ("canceled", "duplicate"):
            issue.canceled_at = now

    def update_issue(
        self, issue: Issue, actor_id: Optional[str], **changes: Any
    ) -> Issue:
        """Change an issue. A key that is present changes its field; ``None`` clears
        what can be cleared (assignee, delegate, parent, duplicate_of, due_date,
        estimate, project, milestone, cycle). Relations add and remove issue ids
        (``blocks``, ``blocked_by``, ``related`` and their ``remove_`` lists)."""
        now = time.time()
        team = self.teams[issue.team_id]
        before_state, before_assignee = issue.state_id, issue.assignee_id
        with self._lock:
            if changes.get("title") is not None:
                if not str(changes["title"]).strip():
                    raise LinearError("An issue has a title")
                issue.title = str(changes["title"]).strip()
            if changes.get("description") is not None:
                issue.description = normalize_markdown(changes["description"])
            if changes.get("priority") is not None:
                if int(changes["priority"]) not in PRIORITIES:
                    raise LinearError("Priority is 0 (none) to 4 (low)")
                issue.priority = int(changes["priority"])
            if "state" in changes and changes["state"]:
                target = team.state(changes["state"])
                if target is None:
                    raise LinearError(f'Could not find state "{changes["state"]}"')
                if target.id != issue.state_id:
                    issue.state_history[-1]["ended_at"] = now
                    issue.state_history.append(
                        {"state_id": target.id, "started_at": now, "ended_at": None}
                    )
                    issue.state_id = target.id
                    self._apply_state_times(issue, target, now)
            for key in ("labels", "addLabels", "removeLabels"):
                if changes.get(key) is None:
                    continue
                resolved = []
                for name in changes[key]:
                    label = self.find_label(name, team.id)
                    if label is None:
                        raise LinearError(
                            f'Could not find label "{name}" for {key}. Each may not exist, or your current permissions may not allow access to it. Provide {key} as a JSON array of strings (e.g. ["Bug", "Improvement"]) where each entry is an existing label name or ID. No labels are changed while any entry is unresolved'
                        )
                    if (
                        label.retired_at
                        and label.id not in issue.label_ids
                        and key != "removeLabels"
                    ):
                        raise LinearError(f'The label "{label.name}" is retired')
                    resolved.append(label.id)
                if key == "labels":
                    issue.label_ids = resolved
                elif key == "addLabels":
                    issue.label_ids += [i for i in resolved if i not in issue.label_ids]
                else:
                    issue.label_ids = [i for i in issue.label_ids if i not in resolved]
            if "assignee" in changes:
                issue.assignee_id = changes["assignee"]
            delegated = False
            if (
                changes.get("delegate") is not None
                and not self.users[changes["delegate"]].active
            ):
                raise LinearError("That agent has been removed")
            if "delegate" in changes and changes["delegate"] != issue.delegate_id:
                issue.delegate_id = changes["delegate"]
                delegated = issue.delegate_id is not None
            for link in changes.get("links") or []:
                issue.attachments.append(
                    Attachment(
                        _uuid(),
                        str(link.get("title") or link["url"]),
                        str(link["url"]),
                        "",
                    )
                )
            if "parent" in changes:
                parent = changes["parent"]
                if parent is not None:
                    if parent == issue.id or parent not in self.issues:
                        raise LinearError("Could not find referenced Issue.", api=True)
                    # No cycles: the parent is not one of this issue's sub-issues.
                    seen, up = set(), self.issues.get(parent)
                    while up is not None and up.id not in seen:
                        if up.id == issue.id:
                            raise LinearError(
                                "An issue cannot be a sub-issue of its own sub-issue"
                            )
                        seen.add(up.id)
                        up = self.issues.get(up.parent_id or "")
                issue.parent_id = parent
            if "duplicate_of" in changes:
                if changes["duplicate_of"] == issue.id:
                    raise LinearError("An issue cannot duplicate itself")
                issue.duplicate_of = changes["duplicate_of"]
            for key, attr in (("blocks", "blocks"), ("related", "related")):
                for other in changes.get(key) or []:
                    if (
                        other != issue.id
                        and other in self.issues
                        and other not in getattr(issue, attr)
                    ):
                        getattr(issue, attr).append(other)
                for other in changes.get(f"remove_{key}") or []:
                    if other in getattr(issue, attr):
                        getattr(issue, attr).remove(other)
            touched = []
            for other in changes.get("blocked_by") or []:
                blocker = self.issues.get(other)
                if (
                    blocker is not None
                    and blocker.id != issue.id
                    and issue.id not in blocker.blocks
                ):
                    blocker.blocks.append(issue.id)
                    touched.append(blocker)
            for other in changes.get("remove_blocked_by") or []:
                blocker = self.issues.get(other)
                if blocker is not None and issue.id in blocker.blocks:
                    blocker.blocks.remove(issue.id)
                    touched.append(blocker)
            if "due_date" in changes:
                issue.due_date = (
                    _date(changes["due_date"]) if changes["due_date"] else None
                )
            if "estimate" in changes:
                issue.estimate = (
                    None if changes["estimate"] is None else float(changes["estimate"])
                )
            if "project" in changes:
                if (
                    changes["project"] is not None
                    and changes["project"] not in self.projects
                ):
                    raise LinearError("Could not find referenced Project.", api=True)
                if changes["project"] != issue.project_id:
                    issue.milestone_id = None
                issue.project_id = changes["project"]
            if "milestone" in changes:
                milestone = self.milestones.get(changes["milestone"] or "")
                if changes["milestone"] is not None and milestone is None:
                    raise LinearError(
                        "Could not find referenced ProjectMilestone.", api=True
                    )
                if milestone is not None:
                    issue.project_id = milestone.project_id
                issue.milestone_id = changes["milestone"]
            if "cycle" in changes:
                cycle = self.cycles.get(changes["cycle"] or "")
                if changes["cycle"] is not None and (
                    cycle is None or cycle.team_id != issue.team_id
                ):
                    raise LinearError("Could not find referenced Cycle.", api=True)
                issue.cycle_id = changes["cycle"]
            if "archived" in changes:
                issue.archived_at = now if changes["archived"] else None
            issue.updated_at = now
            if issue.assignee_id:
                self._subscribe(issue, issue.assignee_id)
        self._emit("issue_updated", issue=issue, actor=actor_id)
        for other in touched:
            self._emit("issue_updated", issue=other, actor=actor_id)
        actor = self.users.get(actor_id or "")
        who = actor.name if actor else "Someone"
        if issue.assignee_id and issue.assignee_id != before_assignee:
            self._notify(
                [issue.assignee_id],
                "issueAssignedToYou",
                actor_id,
                issue=issue,
                text=f"{who} assigned {issue.identifier} to you",
            )
        if issue.state_id != before_state:
            self._notify(
                issue.subscriber_ids,
                "issueStatusChanged",
                actor_id,
                issue=issue,
                text=f"{who} moved {issue.identifier} to {self.state(issue).name}",
            )
        if delegated:
            self.start_session(issue, issue.delegate_id, actor_id)
        return issue

    def delete_issue(self, issue: Issue) -> None:
        """Delete an issue, with its comments and agent sessions; sub-issues lose their parent."""
        with self._lock:
            self.issues.pop(issue.id, None)
            removed = [("linear.issue", issue.id)]
            for cid in [c.id for c in self.comments.values() if c.issue_id == issue.id]:
                del self.comments[cid]
                removed.append(("linear.comment", cid))
            for sid in [x.id for x in self.sessions.values() if x.issue_id == issue.id]:
                del self.sessions[sid]
                removed.append(("linear.session", sid))
            for nid in [
                n.id for n in self.notifications.values() if n.issue_id == issue.id
            ]:
                del self.notifications[nid]
                removed.append(("linear.notification", nid))
            changed = []
            for other in self.issues.values():
                touched = False
                if other.parent_id == issue.id:
                    other.parent_id, touched = None, True
                if other.duplicate_of == issue.id:
                    other.duplicate_of, touched = None, True
                for attr in ("blocks", "related"):
                    if issue.id in getattr(other, attr):
                        getattr(other, attr).remove(issue.id)
                        touched = True
                if touched:
                    changed.append(other)
            for doc in self.documents.values():
                if doc.issue_id == issue.id:
                    doc.issue_id = None
        self._emit("issue_deleted", issue_id=issue.id, removed=removed)
        for other in changed:
            self._emit("issue_updated", issue=other, actor=None)

    def sub_issues(self, issue: Issue) -> List[Issue]:
        return sorted(
            (i for i in self.issues.values() if i.parent_id == issue.id),
            key=lambda i: i.created_at,
        )

    def add_attachment(
        self,
        issue: Issue,
        actor_id: Optional[str],
        title: str,
        url: str = "",
        *,
        content: Optional[bytes] = None,
        content_type: Optional[str] = None,
        subtitle: Optional[str] = None,
        url_for: Optional[Callable[[str], str]] = None,
    ) -> Attachment:
        """A link (``url``) or a file (``content``, its URL from ``url_for(id)``) on an issue."""
        if not title.strip():
            raise LinearError("An attachment has a title")
        if content is None and not re.match(r"^https?://", url or ""):
            raise LinearError("A link starts with http:// or https://")
        with self._lock:
            att = Attachment(
                id=_uuid().replace("-", ""),
                title=title.strip(),
                url=url,
                subtitle=subtitle,
                content=content,
                content_type=content_type,
            )
            if content is not None and url_for is not None:
                att.url = url_for(att.id)
            issue.attachments.append(att)
            issue.updated_at = time.time()
        self._emit("issue_updated", issue=issue, actor=actor_id)
        return att

    def remove_attachment(self, attachment_id: str) -> Issue:
        with self._lock:
            for issue in self.issues.values():
                att = next(
                    (a for a in issue.attachments if a.id == attachment_id), None
                )
                if att is not None:
                    issue.attachments.remove(att)
                    issue.updated_at = time.time()
                    break
            else:
                raise LinearError("Could not find referenced Attachment.", api=True)
        self._emit("issue_updated", issue=issue, actor=None)
        return issue

    def attachment(self, attachment_id: str) -> Optional[Attachment]:
        for issue in self.issues.values():
            for att in issue.attachments:
                if att.id == attachment_id:
                    return att
        return None

    # -- subscriptions and the inbox ------------------------------------------------------

    def _subscribe(self, issue: Issue, user_id: Optional[str]) -> None:
        if user_id and user_id not in issue.subscriber_ids:
            issue.subscriber_ids.append(user_id)

    def set_subscribed(self, issue: Issue, user_id: str, subscribed: bool) -> Issue:
        with self._lock:
            if subscribed:
                self._subscribe(issue, user_id)
            elif user_id in issue.subscriber_ids:
                issue.subscriber_ids.remove(user_id)
        self._emit("issue_updated", issue=issue, actor=user_id)
        return issue

    def _notify(
        self,
        user_ids: List[str],
        kind: str,
        actor_id: Optional[str],
        *,
        issue: Optional[Issue] = None,
        comment: Optional[Comment] = None,
        project: Optional["Project"] = None,
        text: str = "",
    ) -> None:
        """Inbox items for people (not apps), never for their own doing."""
        now = time.time()
        made = []
        with self._lock:
            for user_id in dict.fromkeys(user_ids):
                user = self.users.get(user_id)
                if (
                    user is None
                    or user.is_app
                    or not user.active
                    or user_id == actor_id
                ):
                    continue
                note = Notification(
                    id=_uuid(),
                    user_id=user_id,
                    type=kind,
                    created_at=now,
                    actor_id=actor_id,
                    issue_id=issue.id if issue else None,
                    comment_id=comment.id if comment else None,
                    project_id=project.id if project else None,
                    text=text,
                )
                self.notifications[note.id] = note
                made.append(note)
        for note in made:
            self._emit("notification", notification=note)

    def inbox(self, user_id: str, *, unread_only: bool = False) -> List[Notification]:
        now = time.time()
        items = [
            n
            for n in self.notifications.values()
            if n.user_id == user_id
            and not (n.snoozed_until and n.snoozed_until > now)
            and not (unread_only and n.read_at)
        ]
        return sorted(items, key=lambda n: n.created_at, reverse=True)

    def mark_notification(
        self,
        note: Notification,
        *,
        read: Optional[bool] = None,
        snoozed_until: Any = ...,
    ) -> Notification:
        with self._lock:
            if read is not None:
                note.read_at = time.time() if read else None
            if snoozed_until is not ...:
                note.snoozed_until = snoozed_until
        self._emit("notification", notification=note)
        return note

    def delete_notification(self, note: Notification) -> None:
        with self._lock:
            self.notifications.pop(note.id, None)
        self._emit("notification_deleted", removed=[("linear.notification", note.id)])

    # -- comments -------------------------------------------------------------------

    def add_comment(
        self,
        issue_id: str,
        user_id: str,
        body: str,
        *,
        parent_id: Optional[str] = None,
        at: Optional[float] = None,
        emit: bool = True,
    ) -> Comment:
        if not body.strip():
            raise LinearError("Comment body cannot be empty")
        now = at or time.time()
        with self._lock:
            if parent_id:
                parent = self.comments.get(parent_id)
                if parent is None:
                    raise LinearError("Could not find referenced Comment.", api=True)
                parent_id = parent.parent_id or parent.id
            comment = Comment(
                _uuid(),
                issue_id,
                user_id,
                normalize_markdown(body),
                now,
                now,
                parent_id,
            )
            self.comments[comment.id] = comment
            self.issues[issue_id].updated_at = now
        if not emit:
            return comment
        issue = self.issues[issue_id]
        before = list(issue.subscriber_ids)
        self._subscribe(issue, user_id)
        self._emit("comment_created", comment=comment)
        author_user = self.users.get(user_id)
        who = author_user.name if author_user else "Someone"
        mentioned = [
            u.id
            for name in _MENTION.findall(body)
            for u in self.users.values()
            if not u.is_app and u.display_name.lower() == name.lower()
        ]
        self._notify(
            mentioned,
            "issueCommentMention",
            user_id,
            issue=issue,
            comment=comment,
            text=f"{who} mentioned you on {issue.identifier}",
        )
        self._notify(
            [u for u in before if u not in mentioned],
            "issueNewComment",
            user_id,
            issue=issue,
            comment=comment,
            text=f"{who} commented on {issue.identifier}",
        )
        # A reply in an agent session's thread is a prompt to the agent; a
        # mention of an app in a new comment starts a session with it.
        thread = self.comments[parent_id] if parent_id else comment
        session = next(
            (s for s in self.sessions.values() if s.comment_id == thread.id), None
        )
        author = self.users.get(user_id)
        if session and author and not author.is_app:
            self.prompt_session(session, user_id, body, comment.id)
        elif not session and author and not author.is_app:
            for name in _MENTION.findall(body):
                app = next(
                    (
                        u
                        for u in self.users.values()
                        if u.is_app
                        and u.active
                        and u.display_name.lower() == name.lower()
                    ),
                    None,
                )
                if app:
                    self.start_session(
                        self.issues[issue_id], app.id, user_id, comment_id=thread.id
                    )
                    break
        return comment

    def edit_comment(self, comment: Comment, body: str) -> Comment:
        if not body.strip():
            raise LinearError("Comment body cannot be empty")
        with self._lock:
            comment.body = normalize_markdown(body)
            comment.updated_at = time.time()
        self._emit("comment_updated", comment=comment)
        return comment

    def delete_comment(self, comment_id: str) -> None:
        with self._lock:
            deleted = [
                c.id
                for c in self.comments.values()
                if c.id == comment_id or c.parent_id == comment_id
            ]
            for cid in deleted:
                self.comments.pop(cid, None)
        self._emit("comment_deleted", comment_id=comment_id, deleted=deleted)

    def thread(self, issue: Issue) -> List[Comment]:
        return sorted(
            (c for c in self.comments.values() if c.issue_id == issue.id),
            key=lambda c: c.created_at,
        )

    # -- agent sessions ---------------------------------------------------------------

    def start_session(
        self,
        issue: Issue,
        app_user_id: str,
        creator_id: str,
        comment_id: Optional[str] = None,
    ) -> Session:
        now = time.time()
        with self._lock:
            if comment_id is None:
                # A delegation opens a thread for the session on the issue.
                anchor = Comment(_uuid(), issue.id, app_user_id, "", now, now)
                self.comments[anchor.id] = anchor
                comment_id = anchor.id
            session = Session(
                id=_uuid(),
                issue_id=issue.id,
                app_user_id=app_user_id,
                creator_id=creator_id,
                created_at=now,
                updated_at=now,
                comment_id=comment_id,
            )
            self.comments[comment_id].agent_session_id = session.id
            self.sessions[session.id] = session
        self._emit("session_created", session=session)
        return session

    def prompt_session(
        self, session: Session, user_id: str, body: str, source_comment_id: str
    ) -> Activity:
        activity = Activity(
            _uuid(),
            session.id,
            {"type": "prompt", "body": body},
            time.time(),
            user_id=user_id,
        )
        session.activities.append(activity)
        session.updated_at = activity.created_at
        self._emit("session_prompted", session=session, activity=activity)
        return activity

    def add_activity(
        self,
        session: Session,
        user_id: str,
        content: Dict[str, Any],
        *,
        signal: Optional[str] = None,
        ephemeral: bool = False,
    ) -> Activity:
        kind = content.get("type")
        if kind not in ("thought", "action", "response", "elicitation", "error"):
            raise LinearError(f"Invalid activity content type: {kind}", api=True)
        if kind == "action" and not content.get("action"):
            raise LinearError("An action activity needs an action", api=True)
        if kind != "action" and not str(content.get("body") or "").strip():
            raise LinearError(f"A {kind} activity needs a body", api=True)
        now = time.time()
        with self._lock:
            if session.activities and session.activities[-1].ephemeral:
                session.activities.pop()
            activity = Activity(
                _uuid(),
                session.id,
                dict(content),
                now,
                user_id=user_id,
                signal=signal,
                ephemeral=ephemeral,
            )
            session.activities.append(activity)
            session.started_at = session.started_at or now
            session.status = {
                "thought": "active",
                "action": "active",
                "elicitation": "awaitingInput",
                "response": "complete",
                "error": "error",
            }[kind]
            if session.status in ("complete", "error"):
                session.ended_at = now
            session.updated_at = now
        self._emit("activity_created", session=session, activity=activity)
        if kind in ("response", "elicitation", "error"):
            app = self.users.get(user_id)
            issue = self.issues.get(session.issue_id)
            said = {
                "response": "finished",
                "elicitation": "needs your input",
                "error": "failed",
            }[kind]
            if issue is not None:
                self._notify(
                    [session.creator_id],
                    f"agentSession{kind.capitalize()}",
                    user_id,
                    issue=issue,
                    text=f"{app.name if app else 'The agent'} {said} on {issue.identifier}",
                )
        return activity

    def acknowledged(self, session: Session) -> bool:
        return any(
            a.user_id == session.app_user_id for a in session.activities
        ) or bool(session.external_urls)

    def mark_stale(self, max_wait: float = 10.0) -> None:
        """Sessions their agent did not acknowledge within ``max_wait`` seconds, as Linear marks them."""
        now = time.time()
        for session in self.sessions.values():
            if (
                session.status == "pending"
                and not self.acknowledged(session)
                and now - session.created_at > max_wait
            ):
                session.status = "stale"
                self._emit("session_stale", session=session)

    def prompt_context(self, session: Session) -> str:
        """The ``promptContext`` Linear gives the agent for a new session."""
        issue = self.issues[session.issue_id]
        team = self.teams[issue.team_id]
        labels = [self.labels[i].name for i in issue.label_ids if i in self.labels]
        lines = [
            f'<issue identifier="{issue.identifier}">',
            f"<title>{issue.title}</title>",
        ]
        if issue.description:
            lines.append(f"<description>{issue.description}</description>")
        lines.append(f'<team name="{team.name}"/>')
        lines += [f"<label>{label}</label>" for label in labels]
        if issue.parent_id and issue.parent_id in self.issues:
            parent = self.issues[issue.parent_id]
            lines += [
                f'<parent-issue identifier="{parent.identifier}">',
                f"<title>{parent.title}</title>",
                "</parent-issue>",
            ]
        lines.append("</issue>")
        thread = [
            c
            for c in self.thread(issue)
            if c.body
            and (c.id == session.comment_id or c.parent_id == session.comment_id)
        ]
        if thread:
            lines += [
                "",
                f'<primary-directive-thread comment-id="{session.comment_id}">',
            ]
            for c in thread:
                author = self.users.get(c.user_id)
                stamp = datetime.fromtimestamp(c.created_at, timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                body = c.body
                for u in self.users.values():
                    body = body.replace(
                        f"@{u.display_name}",
                        f'<user id="{u.id}">{u.display_name}</user>',
                    )
                lines += [
                    f'<comment author="{author.name if author else ""}" created-at="{stamp}">',
                    body,
                    "</comment>",
                ]
            lines.append("</primary-directive-thread>")
        others = [
            c
            for c in self.thread(issue)
            if c.body and c.parent_id is None and c.id != session.comment_id
        ]
        if others:
            lines += ["", "<other-thread-comments>"]
            for c in others:
                author = self.users.get(c.user_id)
                lines += [
                    f'<comment author="{author.name if author else ""}">',
                    c.body,
                    "</comment>",
                ]
            lines.append("</other-thread-comments>")
        return "\n".join(lines)

    # -- agent sessions: stopping ---------------------------------------------------------

    def stop_session(self, session: Session, user_id: str) -> Activity:
        """A person stops the agent: a ``stop`` signal in the session, as Linear sends it."""
        if session.status in ("complete", "error", "stale"):
            raise LinearError("The session has ended")
        now = time.time()
        with self._lock:
            activity = Activity(
                _uuid(),
                session.id,
                {"type": "prompt", "body": "Stop"},
                now,
                user_id=user_id,
                signal="stop",
            )
            session.activities.append(activity)
            session.updated_at = now
        self._emit("session_prompted", session=session, activity=activity)
        return activity

    # -- labels --------------------------------------------------------------------------

    def update_label(self, label: Label, **fields: Any) -> Label:
        with self._lock:
            for key, value in fields.items():
                if key not in ("name", "color", "description"):
                    raise LinearError(f"Cannot change {key}")
                if key == "name":
                    value = str(value or "").strip()
                    if not value:
                        raise LinearError("A label has a name")
                    other = self.find_label(value, label.team_id)
                    if other is not None and other.id != label.id:
                        raise LinearError("Duplicate label name")
                if key == "color" and value and not str(value).startswith("#"):
                    value = f"#{value}"
                setattr(label, key, value)
        self._emit("label_updated", label=label)
        return label

    def set_label_retired(self, label: Label, retired: bool) -> Label:
        with self._lock:
            label.retired_at = time.time() if retired else None
        self._emit("label_updated", label=label)
        return label

    def delete_label(self, label: Label) -> None:
        with self._lock:
            self.labels.pop(label.id, None)
            changed = [i for i in self.issues.values() if label.id in i.label_ids]
            for issue in changed:
                issue.label_ids.remove(label.id)
        self._emit("label_deleted", removed=[("linear.label", label.id)])
        for issue in changed:
            self._emit("issue_updated", issue=issue, actor=None)

    # -- teams: settings, workflow and members ----------------------------------------------

    def update_team(
        self,
        team: Team,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Team:
        with self._lock:
            if name is not None:
                name = name.strip()
                if not name:
                    raise LinearError("A team has a name")
                if any(
                    t.id != team.id and t.name.lower() == name.lower()
                    for t in self.teams.values()
                ):
                    raise LinearError(f'A team "{name}" already exists')
                team.name = name
            if description is not None:
                team.description = description
        self._emit("team", team=team)
        return team

    def add_state(self, team: Team, name: str, type: str, color: str) -> State:
        name = name.strip()
        if type not in STATE_TYPES:
            raise LinearError(f"A status's type is one of {', '.join(STATE_TYPES)}")
        if not name:
            raise LinearError("A status has a name")
        with self._lock:
            if any(s.name.lower() == name.lower() for s in team.states):
                raise LinearError(f'The team has a status "{name}"')
            state = State(
                _uuid(),
                name,
                type,
                color if color.startswith("#") else f"#{color}",
                max((s.position for s in team.states), default=-1) + 1,
            )
            team.states.append(state)
        self._emit("team", team=team)
        return state

    def update_state(
        self,
        team: Team,
        state_id: str,
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
        position: Optional[int] = None,
    ) -> State:
        with self._lock:
            state = next((s for s in team.states if s.id == state_id), None)
            if state is None:
                raise LinearError("Could not find referenced WorkflowState.", api=True)
            if name is not None:
                name = name.strip()
                if not name or any(
                    s.id != state.id and s.name.lower() == name.lower()
                    for s in team.states
                ):
                    raise LinearError(
                        f'The team has a status "{name}"'
                        if name
                        else "A status has a name"
                    )
                state.name = name
            if color is not None:
                state.color = color if color.startswith("#") else f"#{color}"
            if position is not None:
                ordered = [
                    s
                    for s in sorted(team.states, key=lambda s: s.position)
                    if s.id != state.id
                ]
                ordered.insert(max(0, min(position, len(ordered))), state)
                for i, s in enumerate(ordered):
                    s.position = i
        self._emit("team", team=team)
        return state

    def delete_state(
        self, team: Team, state_id: str, move_to: Optional[str] = None
    ) -> None:
        """Remove a status; its issues move to ``move_to`` (another of the team's)."""
        state = next((s for s in team.states if s.id == state_id), None)
        if state is None:
            raise LinearError("Could not find referenced WorkflowState.", api=True)
        if sum(1 for s in team.states if s.type == state.type) == 1 and state.type in (
            "backlog",
            "unstarted",
            "started",
            "completed",
            "canceled",
        ):
            raise LinearError(f"A team keeps at least one {state.type} status")
        using = [i for i in self.issues.values() if i.state_id == state.id]
        target = next(
            (s for s in team.states if s.id == move_to and s.id != state.id), None
        )
        if using and target is None:
            raise LinearError(
                f"{len(using)} issues are {state.name}: choose the status they move to"
            )
        for issue in using:
            self.update_issue(issue, None, state=target.id)
        with self._lock:
            team.states.remove(state)
        self._emit("team", team=team)

    def set_team_member(self, team: Team, user_id: str, member: bool) -> Team:
        with self._lock:
            if user_id not in self.users:
                raise LinearError(f'Could not find user "{user_id}"')
            if member and user_id not in team.member_ids:
                team.member_ids.append(user_id)
            if not member and user_id in team.member_ids:
                team.member_ids.remove(user_id)
        self._emit("team", team=team)
        return team

    # -- projects ----------------------------------------------------------------------------

    def project_url(self, base: str, project: Project) -> str:
        return f"{base}/linear/{self.url_key}/project/{slugify(project.name)}-{project.slug_id}"

    def find_project(self, ref: Optional[str]) -> Optional[Project]:
        if not ref:
            return None
        if ref in self.projects:
            return self.projects[ref]
        lowered = ref.lower()
        for project in self.projects.values():
            if lowered in (
                project.identifier.lower(),
                project.name.lower(),
                project.slug_id,
                f"{slugify(project.name)}-{project.slug_id}",
            ):
                return project
        return None

    def create_project(
        self,
        creator_id: str,
        name: str,
        team_ids: List[str],
        *,
        lead_team_id: Optional[str] = None,
        **fields: Any,
    ) -> Project:
        name = (name or "").strip()
        if not name:
            raise LinearError("A project has a name")
        team_ids = list(dict.fromkeys(team_ids))
        if lead_team_id and lead_team_id not in team_ids:
            team_ids.insert(0, lead_team_id)
        if not team_ids or any(t not in self.teams for t in team_ids):
            raise LinearError("A project belongs to at least one team")
        now = time.time()
        with self._lock:
            lead_team = self.teams[lead_team_id or team_ids[0]]
            number = lead_team.next_project_number
            lead_team.next_project_number += 1
            project = Project(
                id=_uuid(),
                identifier=f"P-{lead_team.key}-{number}",
                slug_id=uuid.uuid4().hex[:12],
                name=name,
                lead_team_id=lead_team.id,
                team_ids=team_ids,
                creator_id=creator_id,
                created_at=now,
                updated_at=now,
                member_ids=[creator_id],
            )
            self.projects[project.id] = project
        self.update_project(project, creator_id, **fields)
        self._emit("team", team=lead_team)
        return project

    def update_project(
        self, project: Project, actor_id: Optional[str], **changes: Any
    ) -> Project:
        now = time.time()
        with self._lock:
            if changes.get("name") is not None:
                if not str(changes["name"]).strip():
                    raise LinearError("A project has a name")
                project.name = str(changes["name"]).strip()
            if changes.get("summary") is not None:
                project.summary = str(changes["summary"])[:255]
            if changes.get("description") is not None:
                project.description = normalize_markdown(changes["description"])
            for key in ("icon", "color"):
                if changes.get(key) is not None:
                    setattr(project, key, changes[key])
            if changes.get("status") is not None:
                status = project_status(changes["status"])
                if status is None:
                    raise LinearError(
                        f'Could not find project status "{changes["status"]}"'
                    )
                if status != project.status:
                    project.status = status
                    if status == "started" and project.started_at is None:
                        project.started_at = now
                    project.completed_at = now if status == "completed" else None
                    project.canceled_at = now if status == "canceled" else None
            if changes.get("priority") is not None:
                project.priority = int(changes["priority"])
            if "lead" in changes:
                if changes["lead"] is not None and changes["lead"] not in self.users:
                    raise LinearError(f'Could not find user "{changes["lead"]}"')
                project.lead_id = changes["lead"]
                if project.lead_id and project.lead_id not in project.member_ids:
                    project.member_ids.append(project.lead_id)
            for key, attr in (
                ("start_date", "start_date"),
                ("target_date", "target_date"),
            ):
                if key in changes:
                    setattr(
                        project, attr, _date(changes[key]) if changes[key] else None
                    )
            if changes.get("teams") is not None:
                teams = [t for t in dict.fromkeys(changes["teams"]) if t in self.teams]
                if not teams:
                    raise LinearError("A project belongs to at least one team")
                project.team_ids = teams
                if project.lead_team_id not in teams:
                    project.lead_team_id = teams[0]
            for t in changes.get("add_teams") or []:
                if t in self.teams and t not in project.team_ids:
                    project.team_ids.append(t)
            for t in changes.get("remove_teams") or []:
                if t in project.team_ids and len(project.team_ids) > 1:
                    project.team_ids.remove(t)
                    if project.lead_team_id == t:
                        project.lead_team_id = project.team_ids[0]
            if changes.get("lead_team") is not None:
                if changes["lead_team"] not in self.teams:
                    raise LinearError("Could not find referenced Team.", api=True)
                project.lead_team_id = changes["lead_team"]
                if project.lead_team_id not in project.team_ids:
                    project.team_ids.append(project.lead_team_id)
            for user_id in changes.get("add_members") or []:
                if user_id in self.users and user_id not in project.member_ids:
                    project.member_ids.append(user_id)
            for user_id in changes.get("remove_members") or []:
                if user_id in project.member_ids:
                    project.member_ids.remove(user_id)
            for link in changes.get("links") or []:
                project.links.append(
                    Attachment(
                        _uuid(), str(link.get("title") or link["url"]), str(link["url"])
                    )
                )
            if "archived" in changes:
                project.archived_at = now if changes["archived"] else None
            project.updated_at = now
        self._emit("project", project=project, actor=actor_id)
        return project

    def delete_project(self, project: Project) -> None:
        with self._lock:
            self.projects.pop(project.id, None)
            removed = [("linear.project", project.id)]
            for mid in [
                m.id for m in self.milestones.values() if m.project_id == project.id
            ]:
                del self.milestones[mid]
                removed.append(("linear.milestone", mid))
            for uid in [
                u.id for u in self.status_updates.values() if u.project_id == project.id
            ]:
                del self.status_updates[uid]
                removed.append(("linear.status_update", uid))
            changed = [i for i in self.issues.values() if i.project_id == project.id]
            for issue in changed:
                issue.project_id = issue.milestone_id = None
            docs = [d for d in self.documents.values() if d.project_id == project.id]
            for doc in docs:
                del self.documents[doc.id]
                removed.append(("linear.document", doc.id))
        self._emit("project_deleted", removed=removed)
        for issue in changed:
            self._emit("issue_updated", issue=issue, actor=None)

    def project_issues(self, project: Project) -> List[Issue]:
        return [
            i
            for i in self.issues.values()
            if i.project_id == project.id and i.archived_at is None
        ]

    def project_progress(self, project: Project) -> float:
        """The share of its issues that are done (canceled ones left out), as Linear counts it."""
        issues = [
            i
            for i in self.project_issues(project)
            if self.state(i).type not in ("canceled", "duplicate")
        ]
        if not issues:
            return 0.0
        return sum(1 for i in issues if self.state(i).type == "completed") / len(issues)

    # -- milestones --------------------------------------------------------------------------

    def find_milestone(
        self, project: Project, ref: Optional[str]
    ) -> Optional[Milestone]:
        if not ref:
            return None
        lowered = ref.lower()
        return next(
            (
                m
                for m in self.milestones.values()
                if m.project_id == project.id
                and (m.id == ref or m.name.lower() == lowered)
            ),
            None,
        )

    def milestones_of(self, project: Project) -> List[Milestone]:
        return sorted(
            (m for m in self.milestones.values() if m.project_id == project.id),
            key=lambda m: (m.sort_order, m.created_at),
        )

    def save_milestone(
        self, project: Project, milestone: Optional[Milestone] = None, **fields: Any
    ) -> Milestone:
        now = time.time()
        with self._lock:
            if milestone is None:
                name = str(fields.get("name") or "").strip()
                if not name:
                    raise LinearError("A milestone has a name")
                if self.find_milestone(project, name) is not None:
                    raise LinearError(f'The project has a milestone "{name}"')
                milestone = Milestone(
                    id=_uuid(),
                    project_id=project.id,
                    name=name,
                    created_at=now,
                    updated_at=now,
                    sort_order=float(len(self.milestones_of(project))),
                )
                self.milestones[milestone.id] = milestone
            if fields.get("name") is not None and str(fields["name"]).strip():
                milestone.name = str(fields["name"]).strip()
            if fields.get("description") is not None:
                milestone.description = str(fields["description"])
            if "target_date" in fields:
                milestone.target_date = (
                    _date(fields["target_date"]) if fields["target_date"] else None
                )
            milestone.updated_at = now
        self._emit("milestone", milestone=milestone)
        return milestone

    def delete_milestone(self, milestone: Milestone) -> None:
        with self._lock:
            self.milestones.pop(milestone.id, None)
            changed = [
                i for i in self.issues.values() if i.milestone_id == milestone.id
            ]
            for issue in changed:
                issue.milestone_id = None
        self._emit("milestone_deleted", removed=[("linear.milestone", milestone.id)])
        for issue in changed:
            self._emit("issue_updated", issue=issue, actor=None)

    # -- cycles ------------------------------------------------------------------------------

    def cycles_of(self, team: Team) -> List[Cycle]:
        return sorted(
            (c for c in self.cycles.values() if c.team_id == team.id),
            key=lambda c: c.starts_at,
        )

    def current_cycle(self, team: Team, today: Optional[str] = None) -> Optional[Cycle]:
        today = today or datetime.now(timezone.utc).date().isoformat()
        return next(
            (c for c in self.cycles_of(team) if c.starts_at <= today < c.ends_at), None
        )

    def find_cycle(self, team: Optional[Team], ref: Optional[str]) -> Optional[Cycle]:
        if not ref:
            return None
        for cycle in self.cycles.values():
            if team is not None and cycle.team_id != team.id:
                continue
            if ref in (cycle.id, str(cycle.number)) or (
                cycle.name and cycle.name.lower() == ref.lower()
            ):
                return cycle
        return None

    def create_cycle(
        self,
        team: Team,
        starts_at: str,
        ends_at: str,
        *,
        name: Optional[str] = None,
        description: str = "",
    ) -> Cycle:
        start, end = _date(starts_at), _date(ends_at)
        if end <= start:
            raise LinearError("A cycle ends after it starts")
        with self._lock:
            if any(
                c.starts_at < end and start < c.ends_at for c in self.cycles_of(team)
            ):
                raise LinearError("Cycles of a team do not overlap")
            cycle = Cycle(
                id=_uuid(),
                team_id=team.id,
                number=team.next_cycle_number,
                starts_at=start,
                ends_at=end,
                created_at=time.time(),
                name=(name or "").strip() or None,
                description=description,
            )
            team.next_cycle_number += 1
            self.cycles[cycle.id] = cycle
        self._emit("cycle", cycle=cycle)
        self._emit("team", team=team)
        return cycle

    def update_cycle(self, cycle: Cycle, **fields: Any) -> Cycle:
        team = self.teams[cycle.team_id]
        with self._lock:
            start = (
                _date(fields["starts_at"])
                if fields.get("starts_at")
                else cycle.starts_at
            )
            end = _date(fields["ends_at"]) if fields.get("ends_at") else cycle.ends_at
            if end <= start:
                raise LinearError("A cycle ends after it starts")
            if any(
                c.id != cycle.id and c.starts_at < end and start < c.ends_at
                for c in self.cycles_of(team)
            ):
                raise LinearError("Cycles of a team do not overlap")
            cycle.starts_at, cycle.ends_at = start, end
            if "name" in fields:
                cycle.name = (fields["name"] or "").strip() or None
            if fields.get("description") is not None:
                cycle.description = fields["description"]
        self._emit("cycle", cycle=cycle)
        return cycle

    def delete_cycle(self, cycle: Cycle) -> None:
        with self._lock:
            self.cycles.pop(cycle.id, None)
            changed = [i for i in self.issues.values() if i.cycle_id == cycle.id]
            for issue in changed:
                issue.cycle_id = None
        self._emit("cycle_deleted", removed=[("linear.cycle", cycle.id)])
        for issue in changed:
            self._emit("issue_updated", issue=issue, actor=None)

    # -- documents ---------------------------------------------------------------------------

    def find_document(self, ref: Optional[str]) -> Optional[Document]:
        if not ref:
            return None
        if ref in self.documents:
            return self.documents[ref]
        return next(
            (
                d
                for d in self.documents.values()
                if ref in (d.slug_id, f"{slugify(d.title)}-{d.slug_id}")
            ),
            None,
        )

    def document_url(self, base: str, doc: Document) -> str:
        return (
            f"{base}/linear/{self.url_key}/document/{slugify(doc.title)}-{doc.slug_id}"
        )

    def save_document(
        self, actor_id: str, doc: Optional[Document] = None, **fields: Any
    ) -> Document:
        """Create (``title`` and one parent) or change a document; a parent given on a change moves it."""
        now = time.time()
        parents = {
            k: fields[k]
            for k in ("project_id", "issue_id", "team_id", "cycle_id")
            if fields.get(k)
        }
        with self._lock:
            if doc is None:
                title = str(fields.get("title") or "").strip()
                if not title:
                    raise LinearError("A document has a title")
                if len(parents) != 1:
                    raise LinearError(
                        "A document is in exactly one project, issue, cycle or team"
                    )
                doc = Document(
                    id=_uuid(),
                    slug_id=uuid.uuid4().hex[:12],
                    title=title,
                    content="",
                    creator_id=actor_id,
                    created_at=now,
                    updated_at=now,
                )
                self.documents[doc.id] = doc
            elif parents:
                doc.project_id = doc.issue_id = doc.team_id = doc.cycle_id = None
            for key, value in parents.items():
                setattr(doc, key, value)
            if fields.get("title") is not None and str(fields["title"]).strip():
                doc.title = str(fields["title"]).strip()
            if fields.get("content") is not None:
                doc.content = normalize_markdown(fields["content"])
            for key in ("icon", "color"):
                if fields.get(key) is not None:
                    setattr(doc, key, fields[key])
            doc.updated_at = now
            doc.updated_by_id = actor_id
        self._emit("document", document=doc)
        return doc

    def delete_document(self, doc: Document) -> None:
        with self._lock:
            self.documents.pop(doc.id, None)
        self._emit("document_deleted", removed=[("linear.document", doc.id)])

    # -- project updates ---------------------------------------------------------------------

    def save_status_update(
        self,
        actor_id: str,
        project: Optional[Project],
        update: Optional[StatusUpdate] = None,
        *,
        body: Optional[str] = None,
        health: Optional[str] = None,
    ) -> StatusUpdate:
        if health is not None and health not in HEALTH:
            raise LinearError("Health is onTrack, atRisk or offTrack")
        now = time.time()
        with self._lock:
            if update is None:
                if project is None:
                    raise LinearError("A status update is for a project")
                if not (body or "").strip():
                    raise LinearError("A status update has a body")
                update = StatusUpdate(
                    id=_uuid(),
                    project_id=project.id,
                    user_id=actor_id,
                    body=normalize_markdown(body or ""),
                    health=health or "onTrack",
                    created_at=now,
                    updated_at=now,
                )
                self.status_updates[update.id] = update
                created = True
            else:
                created = False
                if body is not None:
                    update.body = normalize_markdown(body)
                if health is not None:
                    update.health = health
                update.updated_at = now
        self._emit("status_update", status_update=update)
        if created:
            target = self.projects[update.project_id]
            author = self.users.get(actor_id)
            self._notify(
                target.member_ids,
                "projectUpdateCreated",
                actor_id,
                project=target,
                text=f"{author.name if author else 'Someone'} posted an update on {target.name}",
            )
        return update

    def delete_status_update(self, update: StatusUpdate) -> None:
        with self._lock:
            self.status_updates.pop(update.id, None)
        self._emit(
            "status_update_deleted", removed=[("linear.status_update", update.id)]
        )


def project_status(ref: str) -> Optional[str]:
    """A project status's type, from its name or type."""
    lowered = (ref or "").lower()
    for name, kind, _ in PROJECT_STATUSES:
        if lowered in (name.lower(), kind):
            return kind
    return None


def _date(value: Any) -> str:
    """An ISO date (``2026-10-31``) from a date or a timestamp."""
    text = str(value or "").strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        raise LinearError(f'"{text}" is not an ISO date')


def normalize_markdown(text: str) -> str:
    """Linear stores markdown with ``*`` bullets."""
    return re.sub(r"(?m)^(\s*)- ", r"\1* ", text or "")
