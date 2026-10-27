"""The workplace server: its services (Slack, GitHub, Linear), and the UI over them.

``create_app`` serves:

- ``/slack/...``: Slack, as a factory and its agents reach it (:mod:`.slack.api`).
- ``/github/mcp``: GitHub's MCP server (:mod:`.github.mcp`), and
  ``/github/<owner>/<repo>``: its repositories over git (:mod:`.github.git`).
  Changes go to the factory as GitHub's webhooks (:mod:`.github.webhooks`).
- ``/linear/mcp``: Linear's MCP server (:mod:`.linear.mcp`), and
  ``/linear/graphql``: the agent-session part of its API. Agent sessions go
  to the factory as Linear's ``AgentSessionEvent`` webhooks.
- ``/workplace/api/...``: what the UI uses, for signed-in people (setting a
  new workplace up, signing in, invites): the workplace as the person sees
  it, acting as them, people (for admins), apps and their settings, the
  person's own tokens, the wire, and a live stream of changes
  (``/workplace/api/stream``, server-sent events).
- everything else: the UI (``static/``).
"""

from __future__ import annotations

import asyncio
import os
import contextlib
import json
import re
import secrets
import time
import uuid
from urllib.parse import urlparse
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.routing import Route

from .github.api import github_ui_router
from .github.git import git_router
from .github.actions import ActionsRunner
from .github.mcp import GitHubMCP
from .github.persist import GitHubPersistence
from .github.serialize import Serializer
from .github.state import GitHubError, GitHubState
from .github.webhooks import WebhookDelivery
from .linear.api import linear_router
from .library.state import Library
from .library.api import library_router
from .canvas.api import canvas_router
from .config import ConfigError
from .memory.webhooks import MemoryWebhooks
from .security import RequestLimits, audit
from .memory.state import MemoryState
from .memory.mcp import MemoryMCP
from .memory.api import memory_router
from .canvas.mcp import CanvasMCP
from .canvas.persist import CanvasPersistence
from .canvas.state import CanvasState, read_canvas_dir
from .github.spaces import ContainerSettings
from .linear.mcp import LinearMCP
from .linear.persist import LinearPersistence
from .linear.state import LinearState
from .linear.webhooks import LinearWebhookDelivery
from .slack.api import channel_json, message_json, slack_router, user_json
from .slack.render import plain_text
from .slack.events import EventDelivery
from .slack.mcp import SlackMCP
from .slack.persist import SlackPersistence
from .slack.workspace import App as SlackApp
from .slack.workspace import SlackError, SlackWorkspace, valid_tz
from .accounts import (
    MIN_PASSWORD,
    SESSION_COOKIE,
    SESSION_SECONDS,
    Account,
    AccountError,
    Accounts,
    admin,
    me,
    valid_username,
)
from .apps import (
    AppRegistry,
    canvas_token,
    github_token,
    linear_api_key,
    slack_user_token,
)
from .store import Store, encode
from .wire import Wire

__all__ = ["Workplace", "create_app", "load_seed"]


def bundled_seed() -> Path:
    """The Acme workplace's seed file."""
    return Path(str(resources.files("omniagents_workplace").joinpath("seeds/acme.yml")))


def load_seed(path: Optional[Path] = None) -> Dict[str, Any]:
    """A seed file; the bundled Acme workplace when ``path`` is ``None``."""
    path = Path(path) if path is not None else bundled_seed()
    seed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(seed, dict):
        raise ValueError(f"{path}: a seed is a mapping")
    return seed


class Workplace:
    """The services, wired together, with their state in ``data_dir``.

    A data directory holds the workplace's SQLite file (``workplace.db``) and
    its repositories (``git/``). An empty one starts as ``seed`` has it (or
    empty, without one); one that has a workplace in it carries on with it.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        base_url: str,
        sandbox_url: Optional[str] = None,
        seed: Optional[Dict[str, Any]] = None,
        seed_dir: Optional[Path] = None,
        resume: bool = True,
        run_workflows: bool = True,
        workflow_container: Optional["ContainerSettings"] = None,
        self_hosted_runner: Optional["ContainerSettings"] = None,
        internal_url: Optional[str] = None,
        environment: str = "development",
    ):
        """``sandbox_url`` is where a factory's sandboxes reach the workplace
        (default: ``host.docker.internal`` on ``base_url``'s port); paths in
        the seed are relative to ``seed_dir`` (default: the bundled seed's).
        ``resume=False`` opens it to read (workflow runs a restart
        interrupted stay as they are). ``run_workflows`` runs repositories' workflow files on
        this machine (``github/actions.py``)."""
        # Absolute: git runs from other directories (a clone, an import) with paths under it.
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base_url = base_url
        self.additional_origins = tuple(
            value.strip()
            for value in os.environ.get("WORKPLACE_ALLOWED_ORIGINS", "").split(",")
            if value.strip()
        )
        #: Where a factory beside the workplace reaches it (its apps' environment); else ``base_url``.
        self.internal_url = internal_url
        #: development, test, staging or production (``--environment``, ``WORKPLACE_ENV``).
        self.environment = environment
        #: The factory's workplace.yml, when ``up`` serves it (``config.FactoryConfig``).
        self.factory_config = None
        port = base_url.rstrip("/").rsplit(":", 1)[-1]
        self.sandbox_url = (
            sandbox_url
            or f"http://host.docker.internal:{port if port.isdigit() else 80}"
        )
        self.wire = Wire()
        self.store = Store(self.data_dir / "workplace.db")
        # Before anything is written to it.
        fresh = self.store.is_empty()
        #: This workplace, among others on the machine (its job containers say whose they are).
        self.instance = self.store.meta("workplace.instance")
        if not self.instance:
            self.instance = uuid.uuid4().hex
            self.store.set_meta("workplace.instance", self.instance)
        self.github = GitHubState(
            self.data_dir / "git",
            noreply_domain=f"users.noreply.{urlparse(base_url).hostname}",
        )
        if run_workflows and resume:
            self.github.actions = ActionsRunner(
                self.github,
                lambda: self.base_url,
                workflow_container,
                self_hosted_runner,
                instance=self.instance,
            )
        self.github_json = Serializer(self.github, lambda: self.base_url)
        self.webhooks = WebhookDelivery(self.github, self.github_json, self.wire)
        self.github_mcp = GitHubMCP(self.github, self.wire, lambda: self.base_url)
        self.linear = LinearState()
        self.linear_mcp = LinearMCP(
            self.linear,
            self.wire,
            lambda: self.base_url,
            upload_url=lambda: self.sandbox_url,
        )
        self.linear_hooks = LinearWebhookDelivery(
            self.linear, self.wire, lambda: self.base_url
        )
        self.slack = SlackWorkspace(team_id="", name="", domain="")
        self.delivery = EventDelivery(self.slack, self.wire)
        self.mcp = SlackMCP(
            self.slack,
            self.wire,
            lambda: self.base_url,
            upload_url=lambda: self.sandbox_url,
        )
        self.memory = MemoryState(self.store)
        self.memory_mcp = MemoryMCP(self)

        def expire_memory(kind, data):
            if kind in ("issue_closed", "pull_request_closed"):
                self.memory.expire_tag(
                    f"{data['repo'].full_name}#{data['issue'].number}"
                )

        self.github.subscribe(expire_memory)
        self.library = Library(self.store, self.data_dir / "library")
        self.canvas = CanvasState()
        self.canvas_mcp = CanvasMCP(self.canvas, self.wire, lambda: self.base_url)
        self._persisted = [
            SlackPersistence(self.slack, self.store),
            GitHubPersistence(self.github, self.store),
            LinearPersistence(self.linear, self.store),
            CanvasPersistence(self.canvas, self.store),
        ]
        self.accounts = Accounts(
            self.store,
            min_password=(
                15 if environment in ("production", "staging") else MIN_PASSWORD
            ),
            session_seconds=int(
                os.environ.get(
                    "WORKPLACE_SESSION_SECONDS",
                    (
                        12 * 3600
                        if environment in ("production", "staging")
                        else SESSION_SECONDS
                    ),
                )
            ),
        )
        self.apps = AppRegistry(self, self.store)
        from .repository_settings import RepositorySettings

        self.repository_settings = RepositorySettings(self)
        self.memory.remove_legacy_admin_grants(
            set(self.accounts.accounts),
            owned_stores={(a.owner_id, f"agent:{a.id}") for a in self.apps.active()},
        )
        self.memory_hooks = MemoryWebhooks(self)
        #: Who may set up a new workplace: the token its server prints at start.
        self.setup_token = secrets.token_urlsafe(16)
        if fresh:
            self.seed = seed or {}
            self.seed_dir = seed_dir or bundled_seed().parent
            self.store.set_meta(
                "workplace.created", {"at": time.time(), "seeded": bool(seed)}
            )
            self._load_seed()
            for persisted in self._persisted:
                persisted.save_all()
                persisted.attach()
            self._seed_accounts()
            self._seed_apps()
            self._seed_canvases()
        else:
            for persisted in self._persisted:
                if isinstance(persisted, GitHubPersistence):
                    persisted.load(resume=resume)
                else:
                    persisted.load()
                persisted.attach()
            self._canvas_members()

    def _canvas_members(self) -> None:
        """Everyone in the workplace, and every app, acts on canvases as a member."""
        for account in self.accounts.people():
            member = self.canvas.members.get(account.id)
            if (
                member is None
                or member.name != account.name
                or member.active == account.disabled
            ):
                self.canvas.add_member(account.id, account.name, "person")
                self.canvas.update_member(
                    self.canvas.members[account.id], active=not account.disabled
                )
        for app in self.apps.apps.values():
            if app.id not in self.canvas.members:
                self.apps.join_canvas(app)

    def _seed_canvases(self) -> None:
        """A seed's canvases: each a title, the directory its files are in, and its owner (a username)."""
        for raw in self.seed.get("canvases") or []:
            owner = self.accounts.by_username(
                str(raw.get("owner") or "")
            ) or self.apps.by_username(str(raw.get("owner") or ""))
            if owner is None:
                raise ValueError(
                    f"canvas {raw.get('title')!r}: its owner {raw.get('owner')!r} is no one here"
                )
            source = self.seed_dir / str(raw["from"])
            self.canvas.create(
                owner.id, str(raw["title"]), files=read_canvas_dir(source)
            )

    def _load_seed(self) -> None:
        self.slack.load_seed(self.seed.get("slack") or {})
        self.github.load_seed(self.seed.get("github") or {}, self.seed_dir)
        self.linear.load_seed(self.seed.get("linear") or {})

    # -- people ------------------------------------------------------------------

    def setup(
        self,
        *,
        organization: str,
        slug: str,
        username: str,
        name: str,
        email: str,
        password: str,
        tz: str,
    ) -> Tuple[Account, str]:
        """Set up a new workplace: its organization (unless a seed named it),
        and its first person, an admin: whoever set it up."""
        with self.accounts._lock:
            return self._setup(
                organization=organization,
                slug=slug,
                username=username,
                name=name,
                email=email,
                password=password,
                tz=tz,
            )

    def _setup(self, *, organization, slug, username, name, email, password, tz):
        if not self.accounts.needs_setup():
            raise AccountError("This workplace is set up", 409)
        named = bool(self.slack.name)
        organization, slug = (organization or "").strip(), (slug or "").strip().lower()
        if not named and not organization:
            raise AccountError("Name the organization")
        if not named and not valid_username(slug):
            raise AccountError(
                "The organization's short name is lowercase letters, digits, - . and _"
            )
        self.accounts.validate_password(password)
        try:
            valid_tz(tz)
        except SlackError:
            raise AccountError(
                "Give a time zone, as its IANA name (America/Chicago, Europe/Paris)"
            )
        if not named:
            self.slack.set_team(organization, slug, tz)
            self.linear.set_organization(organization, slug)
        account = self.add_person(
            username=username,
            name=name,
            email=email,
            admin=True,
            reserved=() if named else (slug,),
        )
        self.slack.update_user(account.slack_id, tz=tz)
        if not named:
            self.github.add_user(slug, name=organization, type="Organization")
        if self.slack.general() is None:
            self.slack.create_channel(account.slack_id, "general", is_general=True)
        self.accounts.set_password(account, password)
        return account, self.accounts.start_session(account)

    def add_person(
        self,
        *,
        username: str,
        name: str,
        email: str,
        admin: bool = False,
        reserved: Tuple[str, ...] = (),
    ) -> Account:
        """A new person, in Slack, GitHub and Linear."""
        username = (username or "").strip().lower()
        name, email = (name or "").strip(), (email or "").strip()
        if not valid_username(username):
            raise AccountError(
                "A username is lowercase letters, digits, - . and _ (up to 39)"
            )
        if not name:
            raise AccountError("A person has a name")
        if "@" not in email:
            raise AccountError("A person has an email address")
        taken = (
            username in reserved
            or self.accounts.by_username(username) is not None
            or any(u.name == username for u in self.slack.users.values())
            or username in self.github.users
            or self.linear.find_user(username) is not None
        )
        if taken:
            raise AccountError(f"{username} is taken")
        slack = self.slack.add_user(username, name, email, is_admin=admin)
        github = self.github.add_user(username, name=name, email=email, slack=slack.id)
        linear = self.linear.add_person(
            name, username, email, admin=admin, slack=slack.id
        )
        account = self.accounts.add(
            Account(
                id=Accounts.new_id(),
                username=username,
                name=name,
                email=email,
                slack_id=slack.id,
                github_login=github.login,
                linear_id=linear.id,
                admin=admin,
            )
        )
        self.canvas.add_member(account.id, name, "person")
        return account

    def update_person(self, account: Account, **fields: Any) -> Account:
        with self.accounts._lock:
            return self._update_person(account, **fields)

    def _update_person(self, account: Account, **fields: Any) -> Account:
        self.accounts.update(account, **fields)
        if account.disabled:
            for service in ("slack", "github", "linear", "canvas", "memory"):
                self.revoke_personal_token(account, service)
        self.slack.update_user(
            account.slack_id,
            real_name=account.name,
            email=account.email,
            is_admin=account.admin,
            deleted=account.disabled,
        )
        self.github.add_user(
            account.github_login, name=account.name, email=account.email
        )
        self.github.set_suspended(
            self.github.users[account.github_login], account.disabled
        )
        linear = self.linear.users.get(account.linear_id)
        if linear is not None:
            self.linear.update_user(
                linear,
                name=account.name,
                email=account.email,
                is_admin=account.admin,
                active=not account.disabled,
            )
        member = self.canvas.add_member(account.id, account.name, "person")
        self.canvas.update_member(member, active=not account.disabled)
        return account

    def unclaimed(self) -> Optional[Account]:
        """The admin a new workplace was made for (``up``), until they choose a password:
        while no admin has one, they choose it on this machine."""
        admins = [a for a in self.accounts.people() if a.admin and not a.disabled]
        if any(a.password is not None for a in admins):
            return None
        return admins[0] if admins else None

    def _seed_accounts(self) -> None:
        """A seed's people (its Slack members) as accounts, linked to their GitHub and Linear users."""
        admin = (self.seed.get("workplace") or {}).get("admin")
        passwords = (self.seed.get("workplace") or {}).get("passwords") or {}
        apps = {raw.get("slack_user") for raw in self.seed.get("apps") or []}
        for user in self.slack.users.values():
            if user.is_bot or user.deleted or user.id in apps:
                continue
            github = next(
                (u for u in self.github.users.values() if u.slack == user.id), None
            )
            linear = next(
                (u for u in self.linear.users.values() if u.slack == user.id), None
            )
            if github is None:
                github = self.github.add_user(
                    user.name, name=user.real_name, email=user.email, slack=user.id
                )
            if linear is None:
                linear = self.linear.add_person(
                    user.real_name, user.name, user.email, slack=user.id
                )
            account = self.accounts.add(
                Account(
                    id=Accounts.new_id(),
                    username=user.name,
                    name=user.real_name,
                    email=user.email,
                    slack_id=user.id,
                    github_login=github.login,
                    linear_id=linear.id,
                    admin=user.id == admin,
                )
            )
            password = passwords.get(user.name)
            if password:
                self.accounts.set_password(account, str(password))
            self.canvas.add_member(account.id, account.name, "person")

    def close(self) -> None:
        if self.github.actions is not None:
            self.github.actions.stop()
        self.store.close()

    def state(self) -> Dict[str, Any]:
        """Everything the workplace holds, as JSON (bytes as their hash)."""
        ws, gh, li = self.slack, self.github, self.linear
        return encode(
            {
                "slack": {
                    "team": [ws.team_id, ws.name, ws.domain],
                    "next_id": ws.next_id,
                    "last_ts": ws._last_ts,
                    "users": ws.users,
                    "channels": ws.channels,
                    "apps": ws.apps,
                    "messages": ws._messages,
                    "drafts": ws.drafts,
                    "scheduled": ws.scheduled,
                    "files": ws.files,
                },
                "github": {
                    "next_id": gh.next_id,
                    "hooks": gh.hooks,
                    "users": gh.users,
                    "repos": {
                        name: encode(r, exclude=("bare",))
                        for name, r in gh.repos.items()
                    },
                },
                "linear": {
                    "org": [li.org_id, li.org_name, li.url_key, li.oauth_client_id],
                    "hooks": li.hooks,
                    "users": li.users,
                    "teams": li.teams,
                    "labels": li.labels,
                    "issues": li.issues,
                    "comments": li.comments,
                    "sessions": li.sessions,
                    "projects": li.projects,
                    "milestones": li.milestones,
                    "cycles": li.cycles,
                    "documents": li.documents,
                    "status_updates": li.status_updates,
                    "notifications": li.notifications,
                },
                "canvas": self.canvas.snapshot(),
            }
        )

    def _seed_apps(self) -> None:
        """A seed's apps, registered by their owners, with fresh credentials."""
        for raw in self.seed.get("apps") or []:
            # Without an owner, the workplace's admins manage it.
            owner = self.accounts.by_username(str(raw.get("owner") or ""))
            app = self.apps.register(
                owner,
                name=str(raw["name"]),
                username=str(raw["username"]),
                description=str(raw.get("description") or ""),
                slack_user_id=raw.get("slack_user"),
            )
            slack = raw.get("slack") or {}
            self.apps.configure(
                app,
                slack_delivery=slack.get("delivery"),
                slack_request_url=slack.get("request_url"),
                github_webhook_url=(raw.get("github") or {}).get("webhook"),
                linear_webhook_url=(raw.get("linear") or {}).get("webhook"),
            )

    # -- people's own tokens ---------------------------------------------------------

    def personal_tokens(self, account: Account) -> Dict[str, bool]:
        """Which tokens ``account`` has made: to connect their own MCP clients as themselves."""
        slack = self._personal_slack_app(account)
        member = self.canvas.members.get(account.id)
        return {
            "github": bool(self.github.users[account.github_login].token),
            "linear": bool(self.linear.users[account.linear_id].token),
            "slack": bool(slack and slack.user_token),
            "canvas": bool(member and member.token),
            "memory": bool(dict(self.store.docs("memory.token")).get(account.id)),
        }

    def make_personal_token(self, account: Account, service: str) -> str:
        """A new token for ``service`` (any earlier one stops working)."""
        with self.accounts._lock:
            return self._make_personal_token(account, service)

    def _make_personal_token(self, account, service):
        if account.disabled:
            raise AccountError("Account disabled", 403)
        if service == "github":
            token = github_token()
            self.github.set_token(self.github.users[account.github_login], token)
        elif service == "linear":
            token = linear_api_key()
            self.linear.update_user(self.linear.users[account.linear_id], token=token)
        elif service == "slack":
            token = slack_user_token()
            app = self._personal_slack_app(account)
            if app is None:
                self.slack.add_app(
                    SlackApp(
                        id=self.slack._new_id("A"),
                        name=f"{account.name}'s token",
                        user=account.slack_id,
                        user_token=token,
                        app_token=None,
                        signing_secret=None,
                        socket_mode=False,
                        personal=True,
                    )
                )
            else:
                self.slack.update_app(app.id, user_token=token)
        elif service == "memory":
            token = secrets.token_urlsafe(32)
            self.memory.provision(account.id, "personal")
            self.store.put("memory.token", account.id, {"token": token})
        elif service == "canvas":
            token = canvas_token()
            self.canvas.update_member(
                self.canvas.add_member(account.id, account.name, "person"), token=token
            )
        else:
            raise AccountError(f"No service {service}")
        audit("token.created", account_id=account.id, service=service)
        return token

    def revoke_personal_token(self, account: Account, service: str) -> None:
        if service == "github":
            self.github.set_token(self.github.users[account.github_login], None)
        elif service == "linear":
            self.linear.update_user(self.linear.users[account.linear_id], token=None)
        elif service == "slack":
            app = self._personal_slack_app(account)
            if app is not None:
                self.slack.update_app(app.id, user_token=None)
        elif service == "memory":
            self.store.delete("memory.token", account.id)
        elif service == "canvas":
            member = self.canvas.members.get(account.id)
            if member is not None:
                self.canvas.update_member(member, token=None)
        else:
            raise AccountError(f"No service {service}")

        audit("token.revoked", account_id=account.id, service=service)

    def _personal_slack_app(self, account: Account) -> Optional[SlackApp]:
        return next(
            (
                a
                for a in self.slack.apps.values()
                if a.personal and a.user == account.slack_id
            ),
            None,
        )


class PersonalChatCreate(BaseModel):
    model_config = {"extra": "forbid"}
    title: str = Field(default="New chat", min_length=1, max_length=100)


class PersonalChatUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    title: str | None = Field(default=None, min_length=1, max_length=100)
    archived: bool | None = None


class PersonalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20000)
    submission_id: uuid.UUID


class PersonalStop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=200)


class PersonalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=20)
    value: Optional[Dict[str, Any]] = None


class PostMessage(BaseModel):
    channel: str
    text: str
    thread_ts: Optional[str] = None
    reply_broadcast: bool = False


class React(BaseModel):
    channel: str
    ts: str
    emoji: str
    remove: bool = False


class OpenDM(BaseModel):
    others: List[str]


class EditMessage(BaseModel):
    text: str


class EditChannel(BaseModel):
    name: Optional[str] = None
    topic: Optional[str] = None
    purpose: Optional[str] = None
    archived: Optional[bool] = None
    private: Optional[bool] = None
    default: Optional[bool] = None


class Invite(BaseModel):
    users: List[str]


class Read(BaseModel):
    ts: str


class NewBookmark(BaseModel):
    title: str
    link: str


class EditDraft(BaseModel):
    text: str


class EditProfile(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    title: Optional[str] = None
    tz: Optional[str] = None
    status_text: Optional[str] = None
    status_emoji: Optional[str] = None


class EditTeam(BaseModel):
    name: Optional[str] = None
    tz: Optional[str] = None


class FactoryConfigBody(BaseModel):
    config: Dict[str, Any]


class ProviderProbe(BaseModel):
    kind: str
    api_key: str = ""
    base_url: str = ""


class NewProvider(ProviderProbe):
    model: Dict[str, Any]
    make_default: bool = True


class DefaultModel(BaseModel):
    model: str


class LoginId(BaseModel):
    login_id: str


class NewAdmin(BaseModel):
    name: str
    username: str
    email: str
    password: str


class CreateChannel(BaseModel):
    name: str
    is_private: bool = False
    members: List[str] = []


class Setup(BaseModel):
    token: str = ""
    #: The organization's (and its first person's) time zone: the browser's.
    tz: str
    organization: str = ""
    slug: str = ""
    username: str
    name: str
    email: str
    password: str


class SignIn(BaseModel):
    username: str
    password: str
    #: The browser's time zone: a person's times follow them.
    tz: str = ""


class AcceptInvite(BaseModel):
    password: str
    #: The person's time zone: their browser's.
    tz: str = ""


class NewPerson(BaseModel):
    username: str
    name: str
    email: str
    admin: bool = False


class EditPerson(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    admin: Optional[bool] = None
    disabled: Optional[bool] = None


class NewApp(BaseModel):
    name: str
    username: str
    description: str = ""


class ConfigureApp(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    slack_delivery: Optional[str] = None
    slack_request_url: Optional[str] = None
    github_webhook_url: Optional[str] = None
    linear_webhook_url: Optional[str] = None
    memory_webhook_url: Optional[str] = None


class ChangePassword(BaseModel):
    current: str
    new: str


#: The UI's API a person reaches before signing in.
_OPEN_API = re.compile(
    r"^/workplace/api/(session|setup|claim|signin|signout|invites/[^/]+)$"
)


class SignedIn:
    """The UI's API (``/workplace/api/...``) is for signed-in people: it acts
    as the account the session cookie names (``request.state.account``).

    Browser mutations require the configured public origin. JSON content
    types and SameSite cookies provide additional protection. Non-browser
    clients without browser provenance headers may use their session cookie.
    """

    def __init__(self, app, accounts: Accounts, base_url: str, additional_origins=()):
        self.app = app
        self.accounts = accounts
        self.base_url = base_url
        self.additional_origins = additional_origins

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        original_send = send

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                names = {k.lower() for k, _ in headers}
                defaults = {
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"no-referrer",
                    b"x-frame-options": b"SAMEORIGIN",
                }
                if self.base_url.startswith("https://"):
                    defaults[b"strict-transport-security"] = b"max-age=31536000"
                if path.startswith("/workplace/api/"):
                    defaults[b"cache-control"] = b"no-store"
                for key, value in defaults.items():
                    if key not in names:
                        headers.append((key, value))
                message = {**message, "headers": headers}
            await original_send(message)

        if scope["type"] == "http":
            send = secure_send
        if scope["type"] == "http" and path.startswith("/workplace/api/"):
            headers = {
                k.decode().lower(): v.decode() for k, v in scope.get("headers") or []
            }
            method = scope.get("method", "GET")
            if method not in ("GET", "HEAD", "OPTIONS"):
                from .factory_runtime import trusted_origin

                origin = headers.get("origin") or headers.get("referer")
                if (
                    origin
                    and not trusted_origin(
                        origin, self.base_url, self.additional_origins
                    )
                ) or (
                    not origin
                    and headers.get("sec-fetch-site") in ("cross-site", "same-site")
                ):
                    await _answer(
                        send, 403, {"detail": "Request must come from this workplace"}
                    )
                    return
            kind = headers.get("content-type", "")
            if method not in ("GET", "HEAD", "OPTIONS") and not (
                kind.startswith("application/json")
                or kind.startswith("multipart/form-data")
                or headers.get("content-length", "0") == "0"
            ):
                await _answer(send, 415, {"detail": "Send JSON"})
                return
            if not _OPEN_API.match(path):
                account = self.accounts.signed_in(
                    _cookie(headers.get("cookie", ""), SESSION_COOKIE)
                )
                if account is None:
                    await _answer(send, 401, {"detail": "Sign in"})
                    return
                scope.setdefault("state", {})["account"] = account
        await self.app(scope, receive, send)


def _cookie(header: str, name: str) -> Optional[str]:
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


async def _answer(send, status: int, body: Dict[str, Any]) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": json.dumps(body).encode()})


def person_json(account: Account, *, private: bool = False) -> Dict[str, Any]:
    out = {
        "id": account.id,
        "username": account.username,
        "name": account.name,
        "email": account.email,
        "admin": account.admin,
        "disabled": account.disabled,
        "slack_id": account.slack_id,
        "github_login": account.github_login,
        "linear_id": account.linear_id,
    }
    if private:
        out["has_password"] = account.password is not None
    return out


# What a sandbox may reach: git, and the upload URLs a tool call hands out.
# Everything else (the UI and its API, which act as whoever they are told,
# and the MCP servers, which the factory calls from outside its sandboxes)
# is for this machine.
_SANDBOX_PATHS = re.compile(
    r"^/github/[^/]+/[^/]+\.git/(info/refs|git-upload-pack|git-receive-pack)$"
    r"|^/slack/upload/[^/]+$"
    r"|^/linear/uploads/put/[^/]+$"
)


class SandboxBoundary:
    """Answers requests that arrive on a sandbox-facing address (Docker's
    bridge) or port (``serve --sandbox-port``) only for :data:`_SANDBOX_PATHS`;
    403 for the rest."""

    def __init__(self, app, addresses, ports=()):
        self.app = app
        self.addresses = set(addresses)
        self.ports = set(ports)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and (self.addresses or self.ports):
            server = scope.get("server") or ("", 0)
            facing = server[0] in self.addresses or server[1] in self.ports
            if facing and not _SANDBOX_PATHS.match(scope.get("path", "")):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                    return
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Sandboxes reach the workplace for git and uploads only.\n",
                    }
                )
                return
        await self.app(scope, receive, send)


def create_app(
    workplace: Workplace,
    *,
    static_dir: Optional[Path] = None,
    sandbox_addresses: List[str] = (),
    sandbox_ports: List[int] = (),
) -> FastAPI:
    """The workplace's app. ``sandbox_addresses`` are the local addresses
    sandboxes reach it on (Docker's bridge), and ``sandbox_ports`` the ports
    (a deployment's sandbox-only listener): those serve only git and uploads."""
    ws = workplace.slack
    from .personal import PersonalConversations

    personal = PersonalConversations(workplace.store)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        loop = asyncio.get_running_loop()
        workplace.delivery.bind(loop)
        workplace.webhooks.bind(loop)
        workplace.linear_hooks.bind(loop)
        workplace.memory_hooks.bind(loop)
        scheduler = asyncio.create_task(_post_scheduled(ws))
        repository_refresh = asyncio.create_task(
            workplace.repository_settings.maintain()
        )
        async with workplace.mcp.run(), workplace.github_mcp.run(), workplace.linear_mcp.run(), workplace.canvas_mcp.run(), workplace.memory_mcp.run():
            try:
                yield
            finally:
                scheduler.cancel()
                repository_refresh.cancel()
                with workplace.repository_settings.lock:
                    workplace.repository_settings.device.close()
                await asyncio.gather(repository_refresh, return_exceptions=True)
                await workplace.memory_hooks.close()
                await personal.close()

    app = FastAPI(title="OmniAgents Workplace", lifespan=lifespan)
    app.state.workplace = workplace
    from .repository_settings import repository_router, RepositoryProviderBoundary

    app.include_router(repository_router(workplace))
    app.add_middleware(RepositoryProviderBoundary, workplace=workplace)
    app.add_middleware(RequestLimits)
    app.add_middleware(
        SignedIn,
        accounts=workplace.accounts,
        base_url=workplace.base_url,
        additional_origins=workplace.additional_origins,
    )
    # Added last, so it runs first: a sandbox never reaches the UI's API.
    app.add_middleware(
        SandboxBoundary, addresses=list(sandbox_addresses), ports=list(sandbox_ports)
    )

    @app.get("/up")
    @app.get("/healthz")
    def healthz():
        """For a load balancer or a deployment's health check (``/up``, as Rails apps
        and Hatchbox have it): the workplace answers, its store with it."""
        workplace.store.meta("workplace.created")
        return {"ok": True}

    app.router.routes.append(
        Route(
            "/github/mcp",
            endpoint=workplace.github_mcp,
            methods=["GET", "POST", "DELETE"],
        )
    )
    app.router.routes.append(
        Route(
            "/linear/mcp",
            endpoint=workplace.linear_mcp,
            methods=["GET", "POST", "DELETE"],
        )
    )
    app.router.routes.append(
        Route(
            "/canvas/mcp",
            endpoint=workplace.canvas_mcp,
            methods=["GET", "POST", "DELETE"],
        )
    )
    app.router.routes.append(
        Route(
            "/memory/mcp",
            endpoint=workplace.memory_mcp,
            methods=["GET", "POST", "DELETE"],
        )
    )
    app.include_router(memory_router(workplace))
    app.include_router(library_router(workplace))
    app.include_router(
        canvas_router(
            workplace.canvas, lambda: workplace.base_url, lambda account: account.id
        )
    )
    app.include_router(
        linear_router(
            workplace.linear,
            workplace.linear_mcp,
            workplace.wire,
            lambda: workplace.base_url,
            reader=lambda request: workplace.accounts.signed_in(
                request.cookies.get(SESSION_COOKIE)
            ),
        )
    )
    app.include_router(git_router(workplace.github, workplace.wire))
    app.include_router(
        github_ui_router(workplace.github, workplace.github_json, workplace.webhooks)
    )

    def _file_reader(request: Request) -> Optional[str]:
        """Who is fetching a file: a signed-in person, or an app's (or person's) token."""
        account = workplace.accounts.signed_in(request.cookies.get(SESSION_COOKIE))
        if account is not None and not account.disabled:
            return account.slack_id
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        slack_app = ws.app_for_token(token)
        return slack_app.user if slack_app is not None else None

    app.include_router(
        slack_router(
            ws,
            workplace.wire,
            workplace.delivery,
            lambda: workplace.base_url,
            reader=_file_reader,
        )
    )
    # A route, not a mount: clients POST to /slack/mcp exactly, without the
    # redirect to /slack/mcp/ a mount would answer with.
    app.router.routes.append(
        Route("/slack/mcp", endpoint=workplace.mcp, methods=["GET", "POST", "DELETE"])
    )

    def _fail(exc: SlackError):
        raise HTTPException(status_code=400, detail=exc.code)

    def _refused(exc: AccountError):
        raise HTTPException(status_code=exc.status, detail=exc.message)

    def _signed_in(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=workplace.accounts.session_seconds,
            httponly=True,
            samesite="lax",
            secure=workplace.base_url.startswith("https://"),
            path="/",
        )

    def _invite_url(token: str) -> str:
        return f"{workplace.base_url.rstrip('/')}/invite/{token}"

    # -- signing in --------------------------------------------------------------

    @app.get("/workplace/api/session")
    def session(request: Request):
        account = workplace.accounts.signed_in(request.cookies.get(SESSION_COOKIE))
        return {
            "account": person_json(account, private=True) if account else None,
            "setup": workplace.accounts.needs_setup(),
            "organization": ws.name or None,
            # On this machine, a new workplace is set up without the printed link.
            "setup_here": workplace.accounts.needs_setup()
            and _on_this_machine(request),
            "environment": workplace.environment,
            # Development, as Jumpstart's /jumpstart: the factory's workplace.yml is edited here.
            "configurable": workplace.environment == "development"
            and workplace.factory_config is not None,
            # Served with a factory's workplace.yml: its model is configured here (admins).
            "factory": workplace.factory_config is not None,
            "architecture": personal.architecture,
            # A workplace `up` made: its admin chooses a password on this machine.
            "claim_here": _claim(request),
        }

    def _claim(request: Request):
        account = workplace.unclaimed()
        if account is None or not _on_this_machine(request):
            return None
        return {
            "name": account.name,
            "username": account.username,
            "organization": ws.name,
        }

    def _on_this_machine(request: Request) -> bool:
        if workplace.environment in ("staging", "production"):
            return False  # a server: no one is on its machine
        host = request.client.host if request.client else ""
        proxied = any(
            h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip")
        )
        return host in ("127.0.0.1", "::1") and not proxied

    @app.post("/workplace/api/setup")
    def setup(body: Setup, request: Request, response: Response):
        if not _on_this_machine(request) and not secrets.compare_digest(
            body.token, workplace.setup_token
        ):
            raise HTTPException(
                status_code=403,
                detail="The setup link is the one the workplace printed when it started",
            )
        try:
            account, token = workplace.setup(
                organization=body.organization,
                slug=body.slug,
                username=body.username,
                name=body.name,
                email=body.email,
                password=body.password,
                tz=body.tz,
            )
        except AccountError as exc:
            _refused(exc)
        if workplace.factory_config is not None:
            # The config waited for its first admin: now it makes the rest (and the factory's credentials).
            try:
                for made in workplace.factory_config.apply(workplace).made:
                    print(f"  made {made}", flush=True)
            except ConfigError as exc:
                raise HTTPException(
                    status_code=500, detail=f"{workplace.factory_config.path}: {exc}"
                )
        _signed_in(response, token)
        return {"account": person_json(account, private=True)}

    @app.post("/workplace/api/claim")
    def claim(body: AcceptInvite, request: Request, response: Response):
        account = workplace.unclaimed()
        if account is None or not _on_this_machine(request):
            raise HTTPException(status_code=403, detail="Sign in instead")
        try:
            workplace.accounts.set_password(account, body.password)
        except AccountError as exc:
            _refused(exc)
        if body.tz:
            try:
                ws.update_user(account.slack_id, tz=body.tz)
            except SlackError:
                pass
        _signed_in(response, workplace.accounts.start_session(account))
        return {"account": person_json(account, private=True)}

    @app.post("/workplace/api/signin")
    def sign_in(body: SignIn, response: Response):
        try:
            account, token = workplace.accounts.sign_in(body.username, body.password)
        except AccountError as exc:
            _refused(exc)
        if body.tz and ws.users[account.slack_id].tz != body.tz:
            try:
                ws.update_user(account.slack_id, tz=body.tz)
            except SlackError:
                pass
        _signed_in(response, token)
        return {"account": person_json(account, private=True)}

    @app.post("/workplace/api/signout")
    def sign_out(request: Request, response: Response):
        workplace.accounts.sign_out(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/workplace/api/invites/{token}")
    def invite(token: str):
        account = workplace.accounts.invited(token)
        if account is None:
            raise HTTPException(
                status_code=404,
                detail="This invite link has expired or was already used",
            )
        return {
            "username": account.username,
            "name": account.name,
            "organization": ws.name,
        }

    @app.post("/workplace/api/invites/{token}")
    def accept_invite(token: str, body: AcceptInvite, response: Response):
        try:
            account, session_token = workplace.accounts.accept_invite(
                token, body.password
            )
        except AccountError as exc:
            _refused(exc)
        if body.tz:
            try:
                ws.update_user(account.slack_id, tz=body.tz)
            except SlackError:
                pass  # not a time zone: the workspace's stays theirs
        _signed_in(response, session_token)
        return {"account": person_json(account, private=True)}

    @app.post("/workplace/api/me/password")
    def change_password(body: ChangePassword, request: Request, response: Response):
        account = me(request)
        try:
            token = workplace.accounts.change_password(account, body.current, body.new)
        except AccountError as exc:
            if exc.status == 401:
                raise HTTPException(
                    status_code=403, detail="That is not your current password"
                )
            _refused(exc)
        _signed_in(response, token)
        return {"ok": True}

    # -- people (admins) ----------------------------------------------------------

    @app.get("/workplace/api/people")
    def people(request: Request):
        me(request)
        return {
            "people": [
                person_json(a, private=me(request).admin)
                for a in workplace.accounts.people()
            ]
        }

    @app.post("/workplace/api/people")
    def add_person(body: NewPerson, request: Request):
        admin(request)
        try:
            account = workplace.add_person(
                username=body.username,
                name=body.name,
                email=body.email,
                admin=body.admin,
            )
        except AccountError as exc:
            _refused(exc)
        return {
            "person": person_json(account, private=True),
            "invite_url": _invite_url(workplace.accounts.invite(account)),
        }

    @app.post("/workplace/api/people/{account_id}/invite")
    def reinvite(account_id: str, request: Request):
        admin(request)
        account = workplace.accounts.accounts.get(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="No such person")
        return {"invite_url": _invite_url(workplace.accounts.invite(account))}

    @app.patch("/workplace/api/people/{account_id}")
    def edit_person(account_id: str, body: EditPerson, request: Request):
        acting = admin(request)
        account = workplace.accounts.accounts.get(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="No such person")
        if account.id == acting.id and (body.admin is False or body.disabled):
            raise HTTPException(
                status_code=422, detail="An admin cannot remove their own access"
            )
        try:
            workplace.update_person(account, **body.model_dump(exclude_none=True))
        except AccountError as exc:
            _refused(exc)
        return {"person": person_json(account, private=True)}

    # -- the workplace, as the signed-in person sees it ------------------------------

    @app.get("/workplace/api/state")
    def state(request: Request):
        account = me(request)
        return {
            "me": person_json(account, private=True),
            "github": {
                "users": [
                    {"login": u.login, "name": u.name, "slack": u.slack}
                    for u in workplace.github.users.values()
                    if u.type != "Organization" and not u.suspended
                ],
                "repositories": [
                    {
                        "full_name": r.full_name,
                        "description": r.description,
                        "default_branch": r.default_branch,
                        "clone_url": workplace.github_json.clone_url(r),
                        "branches": [
                            {"name": n, "sha": sha}
                            for n, sha in sorted(r.bare.branches().items())
                        ],
                        "open_issues": sum(
                            1
                            for i in r.issues.values()
                            if not i.pull and i.state == "open"
                        ),
                        "open_pulls": sum(
                            1 for i in r.issues.values() if i.pull and i.state == "open"
                        ),
                    }
                    for r in workplace.github.repos.values()
                    if workplace.repository_settings.settings()["provider"] == "standin"
                ],
            },
            "slack": {
                "team": {
                    "id": ws.team_id,
                    "name": ws.name,
                    "domain": ws.domain,
                    "tz": ws.tz,
                },
                "users": [user_json(ws, u) for u in ws.users.values()],
                "channels": [
                    {**channel_json(c), "is_default": c.is_default}
                    for c in ws.visible_channels(account.slack_id)
                ],
                "unread": {
                    c.id: ws.unread(account.slack_id, c.id)
                    for c in ws.visible_channels(account.slack_id)
                    if account.slack_id in c.members
                },
                "drafts": sum(
                    1 for d in ws.drafts.values() if d.user == account.slack_id
                )
                + sum(1 for q in ws.scheduled.values() if q.user == account.slack_id),
                "apps": [
                    {
                        "id": a.id,
                        "name": a.name,
                        "user": a.user,
                        "delivery": "socket" if a.socket_mode else "http",
                        "request_url": a.request_url,
                        "connected": workplace.delivery.is_connected(a.id),
                    }
                    for a in ws.apps.values()
                    if not a.personal
                ],
            },
        }

    def _visible(account: Account, channel_id: str):
        try:
            return ws.resolve_channel(account.slack_id, channel_id)
        except SlackError:
            raise HTTPException(status_code=404, detail="channel_not_found")

    @app.get("/workplace/api/slack/channels/{channel_id}/messages")
    def channel_messages(channel_id: str, request: Request):
        channel = _visible(me(request), channel_id)
        return {
            "messages": [message_json(ws, m) for m in reversed(ws.history(channel.id))]
        }

    @app.get("/workplace/api/slack/channels/{channel_id}/threads/{ts}")
    def thread(channel_id: str, ts: str, request: Request):
        channel = _visible(me(request), channel_id)
        try:
            parent = ws.message(channel.id, ts)
        except SlackError as exc:
            _fail(exc)
        return {
            "parent": message_json(ws, parent),
            "replies": [message_json(ws, m) for m in ws.replies(channel.id, ts)],
        }

    @app.post("/workplace/api/slack/messages")
    def post_message(body: PostMessage, request: Request):
        account = me(request)
        try:
            message = ws.post_message(
                account.slack_id,
                body.channel,
                link_mentions(ws, body.text),
                thread_ts=body.thread_ts,
                reply_broadcast=body.reply_broadcast,
            )
        except SlackError as exc:
            _fail(exc)
        return {"message": message_json(ws, message)}

    @app.post("/workplace/api/slack/reactions")
    def react(body: React, request: Request):
        account = me(request)
        try:
            if body.remove:
                message = ws.remove_reaction(
                    account.slack_id, body.channel, body.ts, body.emoji
                )
            else:
                message = ws.add_reaction(
                    account.slack_id, body.channel, body.ts, body.emoji
                )
        except SlackError as exc:
            _fail(exc)
        return {"message": message_json(ws, message)}

    @app.post("/workplace/api/slack/dms")
    def open_dm(body: OpenDM, request: Request):
        account = me(request)
        try:
            channel = ws.open_dm(account.slack_id, body.others)
        except SlackError as exc:
            _fail(exc)
        return {"channel": channel_json(channel)}

    @app.post("/workplace/api/slack/channels")
    def create_channel(body: CreateChannel, request: Request):
        account = me(request)
        try:
            channel = ws.create_channel(
                account.slack_id,
                body.name,
                is_private=body.is_private,
                invite=body.members,
            )
        except SlackError as exc:
            _fail(exc)
        return {"channel": channel_json(channel)}

    def _slack(call):
        try:
            return call()
        except SlackError as exc:
            _fail(exc)

    @app.patch("/workplace/api/slack/messages/{channel_id}/{ts}")
    def edit_message(channel_id: str, ts: str, body: EditMessage, request: Request):
        account = me(request)
        message = _slack(
            lambda: ws.update_message(
                account.slack_id, channel_id, ts, link_mentions(ws, body.text)
            )
        )
        return {"message": message_json(ws, message)}

    @app.delete("/workplace/api/slack/messages/{channel_id}/{ts}")
    def delete_message(channel_id: str, ts: str, request: Request):
        account = me(request)
        _slack(lambda: ws.delete_message(account.slack_id, channel_id, ts))
        return {"ok": True}

    @app.post("/workplace/api/slack/messages/{channel_id}/{ts}/pin")
    def pin(channel_id: str, ts: str, request: Request):
        account = me(request)
        return {
            "message": message_json(
                ws, _slack(lambda: ws.pin(account.slack_id, channel_id, ts))
            )
        }

    @app.delete("/workplace/api/slack/messages/{channel_id}/{ts}/pin")
    def unpin(channel_id: str, ts: str, request: Request):
        account = me(request)
        return {
            "message": message_json(
                ws, _slack(lambda: ws.unpin(account.slack_id, channel_id, ts))
            )
        }

    @app.get("/workplace/api/slack/channels/{channel_id}")
    def channel_details(channel_id: str, request: Request):
        channel = _visible(me(request), channel_id)
        return {
            "channel": {**channel_json(channel), "is_default": channel.is_default},
            "pins": [
                {**message_json(ws, m), "pinned_by": m.pinned_by}
                for m in ws.pins(channel.id)
            ],
            "bookmarks": [
                {
                    "id": b.id,
                    "title": b.title,
                    "link": b.link,
                    "created_by": b.created_by,
                }
                for b in channel.bookmarks
            ],
        }

    @app.patch("/workplace/api/slack/channels/{channel_id}")
    def edit_channel(channel_id: str, body: EditChannel, request: Request):
        account = me(request)
        user = account.slack_id
        changes = body.model_dump(exclude_none=True)
        # Unarchiving comes first (an archived channel takes no other change), archiving last.
        if changes.get("archived") is False:
            _slack(lambda: ws.unarchive(user, channel_id))
        if "name" in changes:
            _slack(lambda: ws.rename(user, channel_id, body.name))
        if "topic" in changes:
            _slack(lambda: ws.set_topic(user, channel_id, body.topic))
        if "purpose" in changes:
            _slack(lambda: ws.set_purpose(user, channel_id, body.purpose))
        if "private" in changes:
            _slack(lambda: ws.set_private(user, channel_id, body.private))
        if "default" in changes:
            _slack(lambda: ws.set_default(user, channel_id, body.default))
        if changes.get("archived") is True:
            _slack(lambda: ws.archive(user, channel_id))
        return {"channel": channel_json(_visible(account, channel_id))}

    @app.post("/workplace/api/slack/channels/{channel_id}/join")
    def join_channel(channel_id: str, request: Request):
        account = me(request)
        return {
            "channel": channel_json(
                _slack(lambda: ws.join_channel(account.slack_id, channel_id))
            )
        }

    @app.post("/workplace/api/slack/channels/{channel_id}/leave")
    def leave_channel(channel_id: str, request: Request):
        account = me(request)
        return {
            "channel": channel_json(
                _slack(lambda: ws.leave(account.slack_id, channel_id))
            )
        }

    @app.post("/workplace/api/slack/channels/{channel_id}/members")
    def invite_members(channel_id: str, body: Invite, request: Request):
        account = me(request)
        return {
            "channel": channel_json(
                _slack(lambda: ws.invite(account.slack_id, channel_id, body.users))
            )
        }

    @app.delete("/workplace/api/slack/channels/{channel_id}/members/{user_id}")
    def remove_member(channel_id: str, user_id: str, request: Request):
        account = me(request)
        return {
            "channel": channel_json(
                _slack(lambda: ws.kick(account.slack_id, channel_id, user_id))
            )
        }

    @app.post("/workplace/api/slack/channels/{channel_id}/read")
    def mark_read(channel_id: str, body: Read, request: Request):
        account = me(request)
        _slack(lambda: ws.mark(account.slack_id, channel_id, body.ts))
        return {"ok": True}

    @app.post("/workplace/api/slack/channels/{channel_id}/bookmarks")
    def add_bookmark(channel_id: str, body: NewBookmark, request: Request):
        account = me(request)
        bookmark = _slack(
            lambda: ws.add_bookmark(account.slack_id, channel_id, body.title, body.link)
        )
        return {
            "bookmark": {
                "id": bookmark.id,
                "title": bookmark.title,
                "link": bookmark.link,
            }
        }

    @app.delete("/workplace/api/slack/channels/{channel_id}/bookmarks/{bookmark_id}")
    def remove_bookmark(channel_id: str, bookmark_id: str, request: Request):
        account = me(request)
        _slack(lambda: ws.remove_bookmark(account.slack_id, channel_id, bookmark_id))
        return {"ok": True}

    @app.post("/workplace/api/slack/files")
    async def upload_file(request: Request):
        account = me(request)
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=422, detail="Attach a file")
        content = await upload.read()
        channel = str(form.get("channel") or "")
        thread_ts = str(form.get("thread_ts") or "") or None
        comment = link_mentions(ws, str(form.get("comment") or ""))
        name = upload.filename or "file"

        def share():
            file = ws.start_upload(account.slack_id, name, len(content))
            ws.receive_upload(file.upload_token, content)
            return ws.complete_upload(
                account.slack_id,
                file.id,
                channel_id=channel,
                thread_ts=thread_ts,
                initial_comment=comment,
            )

        file = _slack(share)
        return {"file": {"id": file.id, "name": file.name}}

    @app.get("/workplace/api/slack/search")
    def search(q: str, request: Request):
        account = me(request)
        words = [w.lower() for w in q.split() if w.strip()]
        if not words:
            return {"messages": [], "channels": [], "people": []}
        user = account.slack_id

        def hit(text: str) -> bool:
            low = text.lower()
            return all(w in low for w in words)

        found = [
            {**message_json(ws, m), "channel": c.id}
            for c in ws.visible_channels(user)
            if not (c.is_private or c.is_im or c.is_mpim) or user in c.members
            for m in ws.messages(c.id)
            if not m.subtype and hit(plain_text(m.text))
        ]
        found.sort(key=lambda m: float(m["ts"]), reverse=True)
        channels = [
            channel_json(c)
            for c in ws.visible_channels(user)
            if c.name and hit(c.name + " " + c.purpose + " " + c.topic)
        ]
        people = [
            user_json(ws, u)
            for u in ws.users.values()
            if not u.deleted
            and hit(" ".join([u.name, u.real_name, u.display_name, u.title]))
        ]
        return {
            "messages": found[:50],
            "channels": channels[:20],
            "people": people[:20],
        }

    @app.get("/workplace/api/slack/drafts")
    def drafts(request: Request):
        user = me(request).slack_id
        return {
            "drafts": [
                {
                    "id": d.id,
                    "channel": d.channel,
                    "text": d.text,
                    "thread_ts": d.thread_ts,
                }
                for d in ws.drafts.values()
                if d.user == user
            ],
            "scheduled": [
                {
                    "id": q.id,
                    "channel": q.channel,
                    "text": q.text,
                    "post_at": q.post_at,
                    "thread_ts": q.thread_ts,
                }
                for q in sorted(ws.scheduled.values(), key=lambda q: q.post_at)
                if q.user == user
            ],
        }

    @app.patch("/workplace/api/slack/drafts/{draft_id}")
    def edit_draft(draft_id: str, body: EditDraft, request: Request):
        account = me(request)
        draft = _slack(lambda: ws.update_draft(account.slack_id, draft_id, body.text))
        return {"draft": {"id": draft.id, "text": draft.text}}

    @app.post("/workplace/api/slack/drafts/{draft_id}/send")
    def send_draft(draft_id: str, request: Request):
        account = me(request)
        return {
            "message": message_json(
                ws, _slack(lambda: ws.send_draft(account.slack_id, draft_id))
            )
        }

    @app.delete("/workplace/api/slack/drafts/{draft_id}")
    def delete_draft(draft_id: str, request: Request):
        account = me(request)
        _slack(lambda: ws.delete_draft(account.slack_id, draft_id))
        return {"ok": True}

    @app.delete("/workplace/api/slack/scheduled/{scheduled_id}")
    def delete_scheduled(scheduled_id: str, request: Request):
        account = me(request)
        _slack(lambda: ws.delete_scheduled(account.slack_id, scheduled_id))
        return {"ok": True}

    @app.patch("/workplace/api/me/profile")
    def edit_profile(body: EditProfile, request: Request):
        account = me(request)
        changes = body.model_dump(exclude_none=True)
        if "name" in changes:
            try:
                workplace.update_person(account, name=changes.pop("name"))
            except AccountError as exc:
                _refused(exc)
        if "status_emoji" in changes:
            changes["status_emoji"] = changes["status_emoji"].strip().strip(":")
        if changes:
            _slack(lambda: ws.update_user(account.slack_id, **changes))
        return {
            "user": user_json(ws, ws.users[account.slack_id]),
            "account": person_json(account, private=True),
        }

    def _configurable(request: Request):
        admin(request)
        if workplace.environment != "development" or workplace.factory_config is None:
            raise HTTPException(
                status_code=404,
                detail="The configuration page is development's (bin/dev, "
                "`omniagents-workplace up`): commit the file, then deploy",
            )
        return workplace.factory_config

    @app.get("/workplace/api/config")
    def factory_config(request: Request):
        config = _configurable(request)
        try:
            raw = config.raw()
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "path": str(config.path),
            "environment": workplace.environment,
            "config": raw,
            "credentials": bool(config.credentials and config.credentials.exists()),
        }

    @app.put("/workplace/api/config")
    def save_factory_config(body: FactoryConfigBody, request: Request):
        config = _configurable(request)
        try:
            applied = config.save(workplace, body.config)
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "made": applied.made,
            "env_file": str(config.env_file) if config.env_file else None,
        }

    @app.post("/workplace/api/config/admins")
    def create_admin(body: NewAdmin, request: Request):
        _configurable(request)
        try:
            account = workplace.add_person(
                username=body.username, name=body.name, email=body.email, admin=True
            )
            workplace.accounts.set_password(account, body.password)
        except AccountError as exc:
            _refused(exc)
        return {"person": person_json(account, private=True)}

    # -- the factory's model (Apps → the factory → Model), as omni-desktop's AI settings -------

    def _factory(request: Request):
        """The factory's model settings and runtime, from workplace.yml's ``factory:``."""
        from .factory_models import FactoryModels, FactoryRpc

        admin(request)
        if workplace.factory_config is None:
            raise HTTPException(
                status_code=404,
                detail="The workplace was not started with a factory's "
                "workplace.yml (`omniagents-workplace up`)",
            )
        try:
            factory = workplace.factory_config.load().get("factory") or {}
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if not factory.get("config_dir"):
            raise HTTPException(
                status_code=404,
                detail="workplace.yml's factory: has no config_dir "
                "(where the factory's models.json is)",
            )
        models = FactoryModels(Path(str(factory["config_dir"])))
        # Model account changes affect the whole factory. This credential stays
        # behind admin(request); chat and transcript gateways use auth_token.
        rpc = (
            FactoryRpc(
                str(factory["url"]),
                str(factory.get("admin_auth_token") or factory.get("auth_token") or "")
                or None,
            )
            if factory.get("url")
            else None
        )
        return models, rpc

    def _models_error(exc: Exception):
        raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/workplace/api/factory/model")
    async def factory_model(request: Request):
        from .factory_models import (
            CHATGPT_PROVIDER_ID,
            FactoryRpcError,
            ModelsError,
            model_ref,
        )

        models, rpc = _factory(request)
        try:
            summary = models.summary()
        except ModelsError as exc:
            _models_error(exc)
        out: Dict[str, Any] = {
            **summary,
            "chatgpt": None,
            "runtime": None,
            "factory_error": None,
        }
        if rpc is None:
            out["factory_error"] = "workplace.yml's factory: has no url"
            return out
        try:
            status = await rpc.call("account_status")
            chatgpt = next(
                (
                    p
                    for p in (status or {}).get("providers") or []
                    if p.get("id") == CHATGPT_PROVIDER_ID
                ),
                None,
            )
            if chatgpt is not None:
                identity = chatgpt.get("identity") or {}
                out["chatgpt"] = {
                    "state": chatgpt.get("state"),
                    "email": identity.get("email"),
                    "plan": identity.get("plan_type") or identity.get("plan"),
                }
            listed = await rpc.call("list_models")
            out["runtime"] = {
                "default": listed.get("default_model"),
                "models": [
                    {
                        "ref": model_ref(m),
                        "label": m.get("label") or m.get("id"),
                        "provider": (m.get("provider") or {}).get("name"),
                        "realtime": bool(m.get("realtime")),
                        "available": bool(
                            (m.get("availability") or {}).get("available")
                        ),
                        "why": "; ".join(
                            r.get("message", "")
                            for r in (m.get("availability") or {}).get("reasons") or []
                        ),
                        "default": bool(m.get("is_default")),
                    }
                    for m in listed.get("models") or []
                ],
            }
        except FactoryRpcError as exc:
            out["factory_error"] = str(exc)
        return out

    @app.post("/workplace/api/factory/model/validate")
    def validate_model_provider(body: ProviderProbe, request: Request):
        from .factory_models import resolve_model_choices, validate_provider

        _factory(request)
        result = validate_provider(body.kind, body.api_key, body.base_url)
        if result.get("ok"):
            result["choices"] = resolve_model_choices(
                body.kind, result.get("models") or []
            )
        return result

    @app.post("/workplace/api/factory/model/providers")
    def add_model_provider(body: NewProvider, request: Request):
        from .factory_models import ModelsError

        models, _ = _factory(request)
        try:
            ref = models.add_provider(
                body.kind,
                api_key=body.api_key,
                base_url=body.base_url,
                model=body.model,
                make_default=body.make_default,
            )
        except ModelsError as exc:
            _models_error(exc)
        return {"ref": ref, **models.summary()}

    @app.delete("/workplace/api/factory/model/providers/{name}")
    def remove_model_provider(name: str, request: Request):
        from .factory_models import ModelsError

        models, _ = _factory(request)
        try:
            models.remove_provider(name)
        except ModelsError as exc:
            _models_error(exc)
        return models.summary()

    @app.put("/workplace/api/factory/model/default")
    async def set_default_model(body: DefaultModel, request: Request):
        from .factory_models import FactoryRpcError, ModelsError

        models, rpc = _factory(request)
        try:
            models.set_default(body.model)
        except ModelsError as exc:
            if rpc is None:
                _models_error(exc)
            try:
                listed = await rpc.call("list_models")
                models.set_default(body.model, listed.get("models") or [])
            except (FactoryRpcError, ModelsError) as discovered_error:
                _models_error(discovered_error)
        return models.summary()

    @app.post("/workplace/api/factory/model/chatgpt/start")
    async def chatgpt_start(request: Request):
        from .factory_models import CHATGPT_PROVIDER_ID, FactoryRpcError

        _, rpc = _factory(request)
        if rpc is None:
            raise HTTPException(
                status_code=422, detail="workplace.yml's factory: has no url"
            )
        try:
            started = await rpc.call(
                "account_login_start",
                {"provider": CHATGPT_PROVIDER_ID, "mode": "device_code"},
            )
        except FactoryRpcError as exc:
            _models_error(exc)
        return {
            k: started.get(k)
            for k in ("state", "login_id", "user_code", "verification_url", "interval")
        }

    @app.post("/workplace/api/factory/model/chatgpt/complete")
    async def chatgpt_complete(body: LoginId, request: Request):
        from .factory_models import FactoryRpcError, ModelsError

        models, rpc = _factory(request)
        if rpc is None:
            raise HTTPException(
                status_code=422, detail="workplace.yml's factory: has no url"
            )
        try:
            completed = await rpc.call(
                "account_login_complete", {"login_id": body.login_id}
            )
            if (completed or {}).get("state") != "completed":
                return {"state": (completed or {}).get("state") or "pending"}
            listed = await rpc.call("list_models")
            made = models.add_chatgpt(listed.get("models") or [])
        except (FactoryRpcError, ModelsError) as exc:
            _models_error(exc)
        return {"state": "completed", "default": made, **models.summary()}

    @app.post("/workplace/api/factory/model/chatgpt/logout")
    async def chatgpt_logout(request: Request):
        from .factory_models import CHATGPT_PROVIDER_ID, FactoryRpcError

        _, rpc = _factory(request)
        if rpc is None:
            raise HTTPException(
                status_code=422, detail="workplace.yml's factory: has no url"
            )
        try:
            await rpc.call("account_logout", {"provider": CHATGPT_PROVIDER_ID})
        except FactoryRpcError as exc:
            _models_error(exc)
        return {"ok": True}

    # -- the factory's schedules (Apps → the factory → Schedules): run one now ---------------

    def _factory_rpc(request: Request, *, admins_only: bool = True):
        from .factory_models import FactoryRpc

        admin(request) if admins_only else me(request)
        if workplace.factory_config is None:
            raise HTTPException(
                status_code=404,
                detail="The workplace was not started with a factory's "
                "workplace.yml (`omniagents-workplace up`)",
            )
        try:
            factory = workplace.factory_config.load().get("factory") or {}
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if not factory.get("url"):
            raise HTTPException(
                status_code=422, detail="workplace.yml's factory: has no url"
            )
        return FactoryRpc(
            str(factory["url"]), str(factory.get("auth_token") or "") or None
        )

    def _factory_repository() -> Optional[str]:
        """The factory's own repository here (workplace.yml's factory.repository)."""
        try:
            factory = (
                (workplace.factory_config.load().get("factory") or {})
                if workplace.factory_config
                else {}
            )
        except ConfigError:
            return None
        source = factory.get("repository")
        name = (
            (source.get("name") if isinstance(source, dict) else source)
            if source
            else None
        )
        org = workplace.github.organization()
        return f"{org.login}/{name}" if name and org else None

    @app.get("/workplace/api/factory/benchmarks")
    def factory_benchmarks(request: Request):
        """The factory's benchmark suites (its repository's main) and their results
        (EVALUATIONS_ARCHIVE/benchmarks, where the benchmark workflow keeps them)."""
        import json as _json
        import os as _os

        me(request)
        suites = []
        repo = workplace.github.repos.get(_factory_repository() or "")
        if repo is not None:
            for path in repo.bare.files(repo.default_branch):
                if path.startswith("evaluations/benchmarks/") and path.endswith(".yml"):
                    found = repo.bare.blob(repo.default_branch, path)
                    data = (yaml.safe_load(found[1]) or {}) if found else {}
                    suites.append(
                        {
                            "name": Path(path).stem,
                            "path": path,
                            "tasks": len(data.get("scenarios") or []),
                        }
                    )
        results = []
        archive = _os.environ.get("EVALUATIONS_ARCHIVE")
        folder = Path(archive) / "benchmarks" if archive else None
        if folder is not None and folder.is_dir():
            for file in sorted(
                folder.glob("**/*.json"), key=lambda f: f.stat().st_mtime, reverse=True
            )[:50]:
                try:
                    found = _json.loads(file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(found, dict) and isinstance(found.get("configs"), list):
                    results.append(
                        found
                    )  # a benchmark's result, not another file there
        return {
            "suites": suites,
            "results": results,
            "archive": bool(archive),
            "repository": _factory_repository(),
        }

    @app.post("/workplace/api/factory/benchmarks")
    async def add_benchmark(body: Dict[str, Any], request: Request):
        """A run's task into a benchmark suite: a pull request on the factory's repository."""
        from .benchmark_export import BenchmarkError, export_benchmark
        from .factory_models import FactoryRpcError

        account = me(request)
        rpc = _factory_rpc(request, admins_only=False)
        factory_repo = _factory_repository()
        if not factory_repo:
            raise HTTPException(
                status_code=422, detail="workplace.yml's factory has no repository"
            )
        try:
            case = await rpc.http(
                "GET", f"/integrations/runs/{int(body.get('run_id') or 0)}/case"
            )
            return export_benchmark(
                workplace,
                factory_repo,
                case,
                suite=str(body.get("suite") or ""),
                session_id=body.get("session_id") or None,
                actor=account.github_login,
            )
        except (FactoryRpcError, BenchmarkError, GitHubError) as exc:
            raise HTTPException(
                status_code=422, detail=str(getattr(exc, "message", None) or exc)
            )

    # -- the factory's agents' sessions, for the chat UI (factory_runtime.py) ----------

    def _factory_endpoint() -> Tuple[Optional[str], Optional[str]]:
        try:
            factory = (
                (workplace.factory_config.load().get("factory") or {})
                if workplace.factory_config
                else {}
            )
        except ConfigError:
            return None, None
        return (
            str(factory["url"]).rstrip("/") if factory.get("url") else None,
            str(factory.get("auth_token") or "") or None,
        )

    async def personal_connection(request: Request):
        account = admin(request)
        url, token = _factory_endpoint()
        return await personal.for_account(
            account, url, token, request.path_params.get("chat_id", "factory-personal")
        )

    async def personal_result(operation):
        from .factory_models import FactoryRpcError

        try:
            return await operation
        except FactoryRpcError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/workplace/api/personal/chats")
    async def personal_chats(request: Request):
        await personal_connection(request)
        url, token = _factory_endpoint()
        return {"chats": await personal.summaries(admin(request), url, token)}

    @app.post("/workplace/api/personal/chats")
    async def personal_chat_create(body: PersonalChatCreate, request: Request):
        await personal_connection(request)
        if not body.title.strip():
            raise HTTPException(422, "Enter a chat title.")
        return personal.create(
            body.title.strip(), auto_title="title" not in body.model_fields_set
        )

    @app.delete("/workplace/api/personal/chats/{chat_id}")
    async def personal_chat_delete(chat_id: str, request: Request):
        connection = await personal_connection(request)
        return await personal_result(personal.delete(connection))

    @app.patch("/workplace/api/personal/chats/{chat_id}")
    async def personal_chat_update(
        chat_id: str, body: PersonalChatUpdate, request: Request
    ):
        await personal_connection(request)
        changes = body.model_dump(exclude_none=True)
        if "title" in changes:
            changes["title"] = changes["title"].strip()
            if not changes["title"]:
                raise HTTPException(422, "Enter a chat title.")
        return personal.update(chat_id, changes)

    @app.get("/workplace/api/personal/chats/{chat_id}")
    @app.get("/workplace/api/personal")
    async def personal_snapshot(request: Request):
        connection = await personal_connection(request)
        return await personal_result(connection.snapshot())

    @app.post("/workplace/api/personal/chats/{chat_id}/messages")
    @app.post("/workplace/api/personal/messages")
    async def personal_send(body: PersonalMessage, request: Request):
        connection = await personal_connection(request)
        if not body.text.strip():
            raise HTTPException(422, "Write a message first.")
        return await personal_result(
            connection.send(
                f"[{admin(request).username}]\n{body.text.strip()}",
                str(body.submission_id),
            )
        )

    @app.post("/workplace/api/personal/chats/{chat_id}/stop")
    @app.post("/workplace/api/personal/stop")
    async def personal_stop(body: PersonalStop, request: Request):
        connection = await personal_connection(request)
        return await personal_result(connection.stop(body.run_id))

    @app.post("/workplace/api/personal/chats/{chat_id}/decisions")
    @app.post("/workplace/api/personal/decisions")
    async def personal_decision(body: PersonalDecision, request: Request):
        connection = await personal_connection(request)
        return await personal_result(
            connection.decide(body.item_id, body.action, body.value)
        )

    @app.websocket("/workplace/api/personal/chats/{chat_id}/runtime/ws")
    @app.websocket("/workplace/api/personal/runtime/ws")
    async def personal_runtime_ws(websocket: WebSocket):
        from .factory_runtime import relay, trusted_origin
        from .personal_runtime import PersonalRpcPolicy
        from .factory_models import FactoryRpcError

        cookie = _cookie(websocket.headers.get("cookie", ""), SESSION_COOKIE)

        def authorized():
            account = workplace.accounts.signed_in(cookie)
            return bool(
                account
                and account.admin
                and not account.disabled
                and personal.architecture == "personal"
                and personal.exists(
                    websocket.path_params.get("chat_id", "factory-personal")
                )
            )

        if not authorized() or not trusted_origin(
            websocket.headers.get("origin"),
            workplace.base_url,
            workplace.additional_origins,
        ):
            await websocket.close(code=1008)
            return
        account = workplace.accounts.signed_in(cookie)
        url, token = _factory_endpoint()
        try:
            connection = await personal.for_account(
                account,
                url,
                token,
                websocket.path_params.get("chat_id", "factory-personal"),
            )
            await connection.connect()
        except (HTTPException, FactoryRpcError):
            await websocket.close(code=1013)
            return
        await websocket.accept()
        await relay(
            websocket,
            connection.url,
            token,
            authorized=authorized,
            policy=PersonalRpcPolicy(connection, account.username),
        )

    @app.websocket("/workplace/api/personal/chats/{chat_id}/runtime/ws/realtime")
    @app.websocket("/workplace/api/personal/runtime/ws/realtime")
    async def personal_voice_ws(websocket: WebSocket):
        from .factory_runtime import relay, trusted_origin
        from .personal_runtime import PersonalVoicePolicy
        from .factory_models import FactoryRpcError

        cookie = _cookie(websocket.headers.get("cookie", ""), SESSION_COOKIE)

        def authorized():
            account = workplace.accounts.signed_in(cookie)
            return bool(
                account
                and account.admin
                and not account.disabled
                and personal.architecture == "personal"
                and personal.exists(
                    websocket.path_params.get("chat_id", "factory-personal")
                )
            )

        if not authorized() or not trusted_origin(
            websocket.headers.get("origin"),
            workplace.base_url,
            workplace.additional_origins,
        ):
            await websocket.close(code=1008)
            return
        account = workplace.accounts.signed_in(cookie)
        url, token = _factory_endpoint()
        try:
            connection = await personal.for_account(
                account,
                url,
                token,
                websocket.path_params.get("chat_id", "factory-personal"),
            )
            await connection.connect()
        except (HTTPException, FactoryRpcError):
            await websocket.close(code=1013)
            return
        await websocket.accept()
        await relay(
            websocket,
            connection.url + "/realtime",
            token,
            authorized=authorized,
            policy=PersonalVoicePolicy(connection, account.username),
        )

    @app.get("/workplace/api/factory/runs/{run_id}")
    async def factory_run(run_id: int, request: Request):
        """A run: what started it, its sessions (its own, and the agents' it called)."""
        from .factory_models import FactoryRpcError

        rpc = _factory_rpc(request)
        try:
            return await rpc.http("GET", f"/integrations/runs/{run_id}/case")
        except FactoryRpcError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.websocket("/workplace/api/factory/runtime/agents/{key}/ws")
    async def factory_runtime_ws(websocket: WebSocket, key: str):
        from .factory_runtime import relay, trusted_origin

        account = workplace.accounts.signed_in(
            _cookie(websocket.headers.get("cookie", ""), SESSION_COOKIE)
        )
        if (
            account is None
            or not account.admin
            or not trusted_origin(
                websocket.headers.get("origin"),
                workplace.base_url,
                workplace.additional_origins,
            )
        ):
            await websocket.close(code=1008)
            return
        url, token = _factory_endpoint()
        if not url or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await relay(
            websocket,
            re.sub(r"^http", "ws", url) + f"/agents/{key}/ws",
            token,
            authorized=lambda: account.admin
            and workplace.accounts.signed_in(
                _cookie(websocket.headers.get("cookie", ""), SESSION_COOKIE)
            )
            is not None,
        )

    @app.get("/workplace/api/factory/runtime/agents/{key}/{path:path}")
    async def factory_runtime_get(key: str, path: str, request: Request):
        import httpx

        admin(request)
        url, token = _factory_endpoint()
        if not url or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise HTTPException(status_code=404)
        if path not in {"health", "healthz"}:
            raise HTTPException(
                status_code=403, detail="Only runtime health endpoints are available"
            )
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(timeout=30) as client:
            upstream = await client.get(
                f"{url}/agents/{key}/{path}",
                params=dict(request.query_params),
                headers=headers,
            )
        return Response(
            upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    @app.get("/workplace/api/factory/report")
    async def factory_report_view(request: Request, days: float = 7):
        """The factory's runs and pull requests over the last ``days``, with their
        costs, human touches and time to ship (``factory_report``): for everyone."""
        from .factory_models import FactoryRpcError
        from .factory_report import factory_report

        rpc = _factory_rpc(request, admins_only=False)
        since = time.time() - max(0.0, float(days)) * 86400
        try:
            data = await rpc.http("GET", f"/integrations/runs?since={since}")
        except FactoryRpcError as exc:
            return {"factory_error": str(exc)}
        factory_repo = _factory_repository()
        return {
            **factory_report(
                workplace,
                data.get("runs") or [],
                priced=bool(data.get("priced")),
                bucket="day" if days <= 14 else "week",
                factory_repo=factory_repo,
                since=since,
            ),
            "since": since,
            "factory_error": None,
        }

    @app.get("/workplace/api/factory/schedules")
    async def factory_schedules(request: Request):
        from .factory_models import FactoryRpcError

        rpc = _factory_rpc(request)
        try:
            return {
                "schedules": (await rpc.http("GET", "/integrations/schedules"))[
                    "schedules"
                ],
                "factory_error": None,
            }
        except FactoryRpcError as exc:
            return {"schedules": [], "factory_error": str(exc)}

    @app.post("/workplace/api/factory/schedules/{name}/run")
    async def run_factory_schedule(name: str, request: Request):
        from urllib.parse import quote

        from .factory_models import FactoryRpcError

        rpc = _factory_rpc(request)
        try:
            return await rpc.http(
                "POST", f"/integrations/schedules/{quote(name, safe='')}/run"
            )
        except FactoryRpcError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.patch("/workplace/api/team")
    def edit_team(body: EditTeam, request: Request):
        admin(request)
        name = (body.name if body.name is not None else ws.name).strip()
        tz = body.tz if body.tz is not None else ws.tz
        if not name:
            raise HTTPException(status_code=422, detail="The workspace has a name")
        _slack(lambda: ws.set_team(name, ws.domain, tz))
        workplace.linear.set_organization(name, workplace.linear.url_key)
        return {"team": {"name": ws.name, "domain": ws.domain, "tz": ws.tz}}

    @app.get("/workplace/api/wire")
    def wire(request: Request, limit: int = 200):
        admin(request)
        return {"entries": [e.to_dict() for e in workplace.wire.recent(limit)]}

    @app.delete("/workplace/api/wire")
    def clear_wire(request: Request):
        admin(request)
        workplace.wire.clear()
        return {"ok": True}

    # -- apps ---------------------------------------------------------------------

    def _app_of(app_id: str, account: Account):
        registered = workplace.apps.apps.get(app_id)
        if registered is None or registered.removed:
            raise HTTPException(status_code=404, detail="No such app")
        if not workplace.apps.may_manage(account, registered):
            raise HTTPException(
                status_code=403, detail="Only the app's owner or an admin can do that"
            )
        return registered

    @app.get("/workplace/api/apps")
    def apps(request: Request):
        account = me(request)
        return {
            "apps": [
                (
                    workplace.apps.settings(a)
                    if workplace.apps.may_manage(account, a)
                    else {
                        "id": a.id,
                        "name": a.name,
                        "username": a.username,
                        "description": a.description,
                    }
                )
                for a in workplace.apps.active()
            ]
        }

    @app.post("/workplace/api/apps")
    def register_app(body: NewApp, request: Request):
        # An app's webhooks are requests the workplace sends where it is told: admins say where.
        try:
            registered = workplace.apps.register(
                admin(request),
                name=body.name,
                username=body.username,
                description=body.description,
            )
        except AccountError as exc:
            _refused(exc)
        return workplace.apps.settings(registered)

    @app.get("/workplace/api/apps/{app_id}")
    def app_settings(app_id: str, request: Request):
        return workplace.apps.settings(_app_of(app_id, me(request)))

    @app.patch("/workplace/api/apps/{app_id}")
    def configure_app(app_id: str, body: ConfigureApp, request: Request):
        registered = _app_of(app_id, admin(request))
        try:
            workplace.apps.configure(registered, **body.model_dump(exclude_none=True))
        except AccountError as exc:
            _refused(exc)
        return workplace.apps.settings(registered)

    @app.post("/workplace/api/apps/{app_id}/rotate/{credential}")
    def rotate(app_id: str, credential: str, request: Request):
        registered = _app_of(app_id, me(request))
        try:
            workplace.apps.rotate(registered, credential)
        except AccountError as exc:
            _refused(exc)
        return workplace.apps.settings(registered)

    @app.delete("/workplace/api/apps/{app_id}")
    def remove_app(app_id: str, request: Request):
        workplace.apps.remove(_app_of(app_id, me(request)))
        return {"ok": True}

    # -- your own tokens ------------------------------------------------------------

    @app.get("/workplace/api/me/tokens")
    def tokens(request: Request):
        return {
            "tokens": workplace.personal_tokens(me(request)),
            "mcp": {
                "slack": f"{workplace.base_url.rstrip('/')}/slack/mcp",
                "github": f"{workplace.base_url.rstrip('/')}/github/mcp",
                "linear": f"{workplace.base_url.rstrip('/')}/linear/mcp",
                "canvas": f"{workplace.base_url.rstrip('/')}/canvas/mcp",
                "memory": f"{workplace.base_url.rstrip('/')}/memory/mcp",
            },
        }

    @app.post("/workplace/api/me/tokens/{service}")
    def make_token(service: str, request: Request):
        try:
            return {"token": workplace.make_personal_token(me(request), service)}
        except AccountError as exc:
            _refused(exc)

    @app.delete("/workplace/api/me/tokens/{service}")
    def revoke_token(service: str, request: Request):
        try:
            workplace.revoke_personal_token(me(request), service)
        except AccountError as exc:
            _refused(exc)
        return {"ok": True}

    @app.get("/workplace/api/stream")
    async def stream(request: Request):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

        def push(kind: str, data: Dict[str, Any]) -> None:
            def put():
                if not queue.full():
                    queue.put_nowait((kind, data))

            loop.call_soon_threadsafe(put)

        account = me(request)

        def on_slack(kind: str, data: Dict[str, Any]) -> None:
            channel = data.get("channel")
            if (
                channel is not None
                and (channel.is_private or channel.is_im or channel.is_mpim)
                and account.slack_id not in channel.members
            ):
                return
            payload: Dict[str, Any] = {"kind": kind}
            if "channel" in data:
                payload["channel"] = channel_json(data["channel"])
            if "message" in data:
                payload["message"] = message_json(ws, data["message"])
            push("slack", payload)

        def on_github(kind: str, data: Dict[str, Any]) -> None:
            repo = data.get("repo")
            issue = data.get("issue")
            push(
                "github",
                {
                    "kind": kind,
                    "repo": repo.full_name if repo else None,
                    "number": issue.number if issue else None,
                },
            )

        unsubscribe_slack = ws.subscribe(on_slack)
        unsubscribe_github = workplace.github.subscribe(on_github)

        def on_linear(kind: str, data: Dict[str, Any]) -> None:
            issue = data.get("issue")
            session = data.get("session")
            comment = data.get("comment")
            identifier = None
            if issue is not None:
                identifier = issue.identifier
            elif session is not None and session.issue_id in workplace.linear.issues:
                identifier = workplace.linear.issues[session.issue_id].identifier
            elif comment is not None and comment.issue_id in workplace.linear.issues:
                identifier = workplace.linear.issues[comment.issue_id].identifier
            push("linear", {"kind": kind, "issue": identifier})

        unsubscribe_linear = workplace.linear.subscribe(on_linear)

        def on_canvas(kind: str, data: Dict[str, Any]) -> None:
            canvas = data.get("canvas")
            comment = data.get("comment")
            push(
                "canvas",
                {
                    "kind": kind,
                    "canvas": (
                        canvas.id
                        if canvas
                        else (comment.canvas_id if comment else None)
                    ),
                },
            )

        unsubscribe_canvas = workplace.canvas.subscribe(on_canvas)
        unsubscribe_wire = workplace.wire.subscribe(
            lambda e: (
                push("wire", e.to_dict())
                if account.admin and not account.disabled
                else None
            )
        )

        async def events():
            try:
                yield "event: ready\ndata: {}\n\n"
                while (
                    workplace.accounts.signed_in(request.cookies.get(SESSION_COOKIE))
                    is account
                ):
                    try:
                        kind, data = await asyncio.wait_for(queue.get(), 1)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keepalive\n\n"
                        continue
                    if (
                        workplace.accounts.signed_in(
                            request.cookies.get(SESSION_COOKIE)
                        )
                        is not account
                    ):
                        return
                    if kind == "wire" and not account.admin:
                        continue
                    yield f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
            finally:
                unsubscribe_slack()
                unsubscribe_github()
                unsubscribe_linear()
                unsubscribe_canvas()
                unsubscribe_wire()

        return StreamingResponse(events(), media_type="text/event-stream")

    _serve_ui(
        app,
        static_dir
        or Path(str(resources.files("omniagents_workplace").joinpath("static"))),
    )
    return app


def link_mentions(ws: SlackWorkspace, text: str) -> str:
    """``@username`` and ``#channel`` as a person types them, as Slack stores them."""

    def user(match: re.Match) -> str:
        name = match.group(1)
        found = next((u for u in ws.users.values() if u.name == name), None)
        return f"<@{found.id}>" if found else match.group(0)

    def channel(match: re.Match) -> str:
        found = ws.channel_by_name(match.group(1))
        return f"<#{found.id}|{found.name}>" if found else match.group(0)

    text = re.sub(r"(?<![<\w])@([a-z0-9][a-z0-9._-]*)", user, text)
    return re.sub(r"(?<![<\w&])#([a-z0-9][a-z0-9_-]*)", channel, text)


async def _post_scheduled(ws: SlackWorkspace) -> None:
    while True:
        await asyncio.sleep(5)
        ws.post_due()


def _serve_ui(app: FastAPI, static_dir: Path) -> None:
    index = static_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str):
        candidate = (static_dir / path).resolve()
        if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
            # Built assets are named by their content: they never change.
            immutable = path.startswith("assets/")
            return FileResponse(
                candidate,
                headers={
                    "Cache-Control": (
                        "public, max-age=31536000, immutable"
                        if immutable
                        else "no-cache"
                    )
                },
            )
        if index.is_file():
            # Always revalidated, so an upgraded workplace's UI is the one loaded.
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse(
            {
                "error": "the workplace UI is not built: npm run build in omniagents-workplace/web"
            },
            status_code=404,
        )
