"""Apps: the identities agents act as (a factory, a student's agent).

An app is registered by a person (its owner) and is an account in all three
services, as the services' own apps are:

- **Slack**: a member account and the app authorized by it. Slack's MCP
  server acts with user tokens, so the app's user token acts as that member
  ("Sent using <app>"); its events reach it over Socket Mode (its app-level
  token) or at a request URL (signed with its signing secret).
- **GitHub**: a machine account with a token, which the MCP server and git
  take; a webhook sends it the organization's events, signed with a secret.
- **Linear**: an agent people delegate issues to, with an OAuth token;
  a webhook sends it its agent sessions, signed with a secret.
- **Canvases**: a member with a token for the canvas MCP server.

Every credential is generated when the app is registered and can be
rotated; removing an app revokes them all. ``env`` is what a factory
connects with: its MCP servers, tokens, git remote and webhook secrets.
"""

from __future__ import annotations

import os
import secrets
import string
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .accounts import Account, AccountError, valid_username
from .github.state import Webhook
from .linear.state import LinearWebhook
from .slack.workspace import App as SlackApp
from .store import Store

if TYPE_CHECKING:
    from .server import Workplace

__all__ = ["App", "AppRegistry", "CREDENTIALS"]

_ALNUM = string.ascii_letters + string.digits


def github_token() -> str:
    return "ghp_" + "".join(secrets.choice(_ALNUM) for _ in range(36))


def linear_oauth_token() -> str:
    return "lin_oauth_" + secrets.token_hex(32)


def linear_api_key() -> str:
    return "lin_api_" + "".join(secrets.choice(_ALNUM) for _ in range(40))


def slack_user_token() -> str:
    return (
        "xoxp-"
        + "-".join(str(secrets.randbelow(10**12)).zfill(12) for _ in range(3))
        + "-"
        + secrets.token_hex(16)
    )


def slack_app_token() -> str:
    return "xapp-1-" + secrets.token_hex(24)


def webhook_secret() -> str:
    return secrets.token_hex(20)


def canvas_token() -> str:
    return "cnv_" + secrets.token_urlsafe(32)


#: What can be rotated, and how a new one is made.
CREDENTIALS = {
    "slack_user_token": slack_user_token,
    "slack_app_token": slack_app_token,
    "slack_signing_secret": webhook_secret,
    "github_token": github_token,
    "github_webhook_secret": webhook_secret,
    "linear_token": linear_oauth_token,
    "linear_webhook_secret": webhook_secret,
    "canvas_token": canvas_token,
    "memory_token": lambda: secrets.token_urlsafe(32),
    "memory_webhook_secret": webhook_secret,
}


@dataclass
class App:
    id: str
    name: str
    username: str
    owner_id: str
    slack_app_id: str
    slack_user_id: str
    github_login: str
    linear_user_id: str
    github_hook_id: Optional[int] = None
    linear_hook_id: Optional[str] = None
    description: str = ""
    memory_webhook: str = ""
    memory_webhook_secret: str = field(default_factory=webhook_secret)
    memory_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    removed: bool = False
    created_at: float = field(default_factory=time.time)


class AppRegistry:
    def __init__(self, workplace: "Workplace", store: Store):
        self.w = workplace
        self.store = store
        self._lock = threading.RLock()
        self.apps: Dict[str, App] = {a.id: a for a in store.load("app", App)}
        for app in self.apps.values():
            self._save(app)  # Persist credentials assigned while upgrading older apps.

    def active(self) -> List[App]:
        return sorted(
            (a for a in self.apps.values() if not a.removed), key=lambda a: a.created_at
        )

    def by_username(self, username: str) -> Optional[App]:
        return next(
            (a for a in self.apps.values() if a.username == username and not a.removed),
            None,
        )

    def may_manage(self, account: Account, app: App) -> bool:
        return account.admin or app.owner_id == account.id

    # -- registering --------------------------------------------------------------

    def register(
        self,
        owner: Optional[Account],
        *,
        name: str,
        username: str,
        description: str = "",
        slack_user_id: Optional[str] = None,
    ) -> App:
        """A new app, in Slack, GitHub and Linear, with fresh credentials.

        ``slack_user_id`` names an existing Slack member to be the app's
        account (a seed's, whose messages already mention it)."""
        w = self.w
        name, username = (name or "").strip(), (username or "").strip().lower()
        if not name:
            raise AccountError("An app has a name")
        if not valid_username(username):
            raise AccountError(
                "A username is lowercase letters, digits, - . and _ (up to 39)"
            )
        if (
            w.accounts.by_username(username) is not None
            or self.by_username(username) is not None
        ):
            raise AccountError(f"{username} is taken")
        with self._lock:
            if slack_user_id is not None:
                slack_user = w.slack.user(slack_user_id)
            elif any(u.name == username for u in w.slack.users.values()):
                raise AccountError(f"{username} is taken")
            else:
                slack_user = w.slack.add_user(username, name, "", title=description)
            slack_app = w.slack.add_app(
                SlackApp(
                    id=w.slack._new_id("A"),
                    name=name,
                    user=slack_user.id,
                    user_token=slack_user_token(),
                    app_token=slack_app_token(),
                    signing_secret=webhook_secret(),
                    socket_mode=True,
                )
            )
            github = w.github.add_user(username, name=name, slack=slack_user.id)
            w.github.set_token(github, github_token())
            linear = w.linear.find_user(username)
            if linear is not None and not linear.is_app:
                raise AccountError(f"{username} is a person in Linear")
            if linear is None:
                linear = w.linear.add_person(
                    name, username, "", is_app=True, slack=slack_user.id
                )
            w.linear.update_user(
                linear, token=linear_oauth_token(), slack=slack_user.id
            )
            app = App(
                id=str(uuid.uuid4()),
                name=name,
                username=username,
                owner_id=owner.id if owner else "",
                slack_app_id=slack_app.id,
                slack_user_id=slack_user.id,
                github_login=github.login,
                linear_user_id=linear.id,
                description=description,
            )
            self.apps[app.id] = app
            self._save(app)
            self.join_canvas(app)
            own = w.memory.provision(app.id, "agent")
            if owner is not None:
                w.memory.grant(owner.id, own, "write")
        return app

    def join_canvas(self, app: App) -> None:
        """The app as a canvas member, with its token (none once it is removed)."""
        member = self.w.canvas.add_member(app.id, app.name, "app")
        if app.removed:
            self.w.canvas.update_member(member, token=None, active=False)
        elif not member.token:
            self.w.canvas.update_member(member, token=canvas_token())

    # -- settings -------------------------------------------------------------------

    def configure(
        self,
        app: App,
        *,
        slack_delivery: Optional[str] = None,
        slack_request_url: Optional[str] = None,
        github_webhook_url: Optional[str] = None,
        linear_webhook_url: Optional[str] = None,
        memory_webhook_url: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> App:
        """Change where the app's events go (an empty URL turns a webhook off)."""
        if memory_webhook_url is not None:
            app.memory_webhook = _url(memory_webhook_url)
        w = self.w
        with self._lock:
            if slack_delivery is not None:
                if slack_delivery not in ("socket", "http"):
                    raise AccountError(
                        "Slack delivers events over Socket Mode (socket) or to a URL (http)"
                    )
                w.slack.update_app(
                    app.slack_app_id, socket_mode=slack_delivery == "socket"
                )
            if slack_request_url is not None:
                w.slack.update_app(
                    app.slack_app_id, request_url=_url(slack_request_url)
                )
            if github_webhook_url is not None:
                url = _url(github_webhook_url)
                hooks = [h for h in w.github.hooks if h.id != app.github_hook_id]
                if url:
                    old = next(
                        (h for h in w.github.hooks if h.id == app.github_hook_id), None
                    )
                    hook = Webhook(
                        url=url,
                        secret=old.secret if old else webhook_secret(),
                        id=old.id if old else w.github._id(),
                    )
                    hooks.append(hook)
                    app.github_hook_id = hook.id
                else:
                    app.github_hook_id = None
                w.github.hooks = hooks
                w.github.changed(hooks=True)
            if linear_webhook_url is not None:
                url = _url(linear_webhook_url)
                hooks = [h for h in w.linear.hooks if h.id != app.linear_hook_id]
                if url:
                    old = next(
                        (h for h in w.linear.hooks if h.id == app.linear_hook_id), None
                    )
                    hook = LinearWebhook(
                        url=url,
                        secret=old.secret if old else webhook_secret(),
                        app_user_id=app.linear_user_id,
                        id=old.id if old else str(uuid.uuid4()),
                    )
                    hooks.append(hook)
                    app.linear_hook_id = hook.id
                else:
                    app.linear_hook_id = None
                w.linear.hooks = hooks
                w.linear.changed()
            if name is not None and name.strip():
                app.name = name.strip()
                w.slack.update_app(app.slack_app_id, name=app.name)
                w.canvas.add_member(app.id, app.name, "app")
            if description is not None:
                app.description = description
            self._save(app)
        return app

    def rotate(self, app: App, credential: str) -> App:
        make = CREDENTIALS.get(credential)
        if make is None:
            raise AccountError(f"No credential {credential}")
        w = self.w
        value = make()
        with self._lock:
            if credential == "slack_user_token":
                w.slack.update_app(app.slack_app_id, user_token=value)
            elif credential == "slack_app_token":
                w.slack.update_app(app.slack_app_id, app_token=value)
                w.delivery.disconnect_app(app.slack_app_id)
            elif credential == "slack_signing_secret":
                w.slack.update_app(app.slack_app_id, signing_secret=value)
            elif credential == "github_token":
                w.github.set_token(w.github.users[app.github_login], value)
            elif credential == "linear_token":
                w.linear.update_user(w.linear.users[app.linear_user_id], token=value)
            elif credential == "memory_webhook_secret":
                app.memory_webhook_secret = value
                self._save(app)
            elif credential == "memory_token":
                app.memory_token = value
                self._save(app)
            elif credential == "canvas_token":
                w.canvas.update_member(w.canvas.members[app.id], token=value)
            elif credential == "github_webhook_secret":
                hook = self._github_hook(app)
                if hook is None:
                    raise AccountError("The app has no GitHub webhook")
                hook.secret = value
                w.github.changed(hooks=True)
            elif credential == "linear_webhook_secret":
                hook = self._linear_hook(app)
                if hook is None:
                    raise AccountError("The app has no Linear webhook")
                hook.secret = value
                w.linear.changed()
        return app

    def remove(self, app: App) -> None:
        """Revoke every credential and webhook; the app's accounts keep their history."""
        w = self.w
        with self._lock:
            self.configure(app, github_webhook_url="", linear_webhook_url="")
            w.slack.update_app(
                app.slack_app_id,
                user_token=None,
                app_token=None,
                signing_secret=None,
                socket_mode=False,
                request_url=None,
            )
            w.delivery.disconnect_app(app.slack_app_id)
            w.slack.update_user(app.slack_user_id, deleted=True)
            w.github.set_token(w.github.users[app.github_login], None)
            w.github.set_suspended(w.github.users[app.github_login], True)
            w.linear.update_user(
                w.linear.users[app.linear_user_id], token=None, active=False
            )
            app.removed = True
            self._save(app)
            self.join_canvas(app)

    # -- what a factory connects with ---------------------------------------------------

    def _github_hook(self, app: App) -> Optional[Webhook]:
        return next(
            (h for h in self.w.github.hooks if h.id == app.github_hook_id), None
        )

    def _linear_hook(self, app: App) -> Optional[LinearWebhook]:
        return next(
            (h for h in self.w.linear.hooks if h.id == app.linear_hook_id), None
        )

    def settings(self, app: App) -> Dict[str, Any]:
        w = self.w
        slack = w.slack.apps[app.slack_app_id]
        github_hook, linear_hook = self._github_hook(app), self._linear_hook(app)
        owner = w.accounts.accounts.get(app.owner_id)
        return {
            "id": app.id,
            "name": app.name,
            "username": app.username,
            "description": app.description,
            "owner": owner.username if owner else None,
            "removed": app.removed,
            "slack": {
                "app_id": slack.id,
                "user_id": app.slack_user_id,
                "delivery": "socket" if slack.socket_mode else "http",
                "request_url": slack.request_url,
                "connected": w.delivery.is_connected(slack.id),
            },
            "github": {
                "login": app.github_login,
                "webhook_url": github_hook.url if github_hook else None,
            },
            "linear": {
                "user_id": app.linear_user_id,
                "webhook_url": linear_hook.url if linear_hook else None,
            },
            "env": {
                k: (
                    "[configured on server]"
                    if k in ("GITHUB_MCP_TOKEN", "GITHUB_WEBHOOK_SECRET")
                    and w.repository_settings.settings()["provider"] == "github"
                    else v
                )
                for k, v in self.env(app).items()
            },
        }

    def env(self, app: App) -> Dict[str, str]:
        """The environment a factory acting as ``app`` connects to the workplace with."""
        w = self.w
        base = (w.internal_url or w.base_url).rstrip("/")
        slack = w.slack.apps[app.slack_app_id]
        github_hook, linear_hook = self._github_hook(app), self._linear_hook(app)
        env = {
            "SLACK_MCP_URL": f"{base}/slack/mcp",
            "SLACK_MCP_TOKEN": slack.user_token or "",
            "SLACK_API_URL": f"{base}/slack/api",
            "GITHUB_MCP_URL": f"{base}/github/mcp",
            "GITHUB_MCP_TOKEN": w.github.users[app.github_login].token or "",
            "GITHUB_GIT_URL": f"{w.sandbox_url.rstrip('/')}/github",
            "LINEAR_MCP_URL": f"{base}/linear/mcp",
            "LINEAR_MCP_TOKEN": w.linear.users[app.linear_user_id].token or "",
            "LINEAR_API_URL": f"{base}/linear/graphql",
            "LINEAR_ACCESS_TOKEN": w.linear.users[app.linear_user_id].token or "",
            "MEMORY_MCP_URL": f"{base}/memory/mcp",
            "MEMORY_MCP_TOKEN": app.memory_token,
            "MEMORY_WEBHOOK_SECRET": app.memory_webhook_secret,
            "MEMORY_AGENT_STORE": w.memory.provision(app.id, "agent"),
            "LIBRARY_API_URL": f"{base}/library/api",
            "LIBRARY_FILES_HOST_PATH": os.environ.get("LIBRARY_FILES_HOST_PATH")
            or str(w.library.root.resolve()),
            "CANVAS_MCP_URL": f"{base}/canvas/mcp",
            "CANVAS_MCP_TOKEN": (
                w.canvas.members.get(app.id).token
                if app.id in w.canvas.members
                else None
            )
            or "",
        }
        if slack.socket_mode:
            env["SLACK_APP_TOKEN"] = slack.app_token or ""
        else:
            env["SLACK_SIGNING_SECRET"] = slack.signing_secret or ""
        if github_hook is not None:
            env["GITHUB_WEBHOOK_SECRET"] = github_hook.secret
        if linear_hook is not None:
            env["LINEAR_WEBHOOK_SECRET"] = linear_hook.secret
        # A member's own app must never inherit the factory's real GitHub identity.
        config = w.factory_config.load() if w.factory_config else {}
        if (config.get("app") or {}).get("username") == app.username:
            env.update(w.repository_settings.environment())
            env["FACTORY_SETTINGS_TOKEN"] = app.memory_token
            env["FACTORY_BOOTSTRAP_TOKEN"] = (
                w.github.users[app.github_login].token or ""
            )
        return env

    def _save(self, app: App) -> None:
        self.store.put("app", app.id, app)


def _url(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    if not (value.startswith("http://") or value.startswith("https://")):
        raise AccountError("A URL starts with http:// or https://")
    return value
