"""A factory's workplace, made from its ``workplace.yml``: ``omniagents-workplace up``.

The file says what a factory needs in a workplace: the organization, the
factory's app (and where its events go), the repositories it works in, its
Slack channels, Linear teams and canvases. ``apply`` makes what is missing
and leaves
what is there, so running it again adds what the file gained; it never
deletes. A new workplace's first admin is ``admin:`` (from the credentials),
signing in with its password; with none, ``apply`` makes the organization and
waits for the setup page (the first visit, or the link ``up`` prints) to make
the admin, then makes the rest (``FactoryConfig.apply`` again).

The app's credentials go to the file ``env_file`` names, which the factory's
``omniagents run`` loads (a ``.env`` beside its ``project.yml``). Strings
may name environment variables (``${VAR}``, ``${VAR:-default}``,
``${VAR:?message}``): a deployment's config takes its admin and its
factory's address from where it runs.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yaml

from .accounts import AccountError, valid_username
from .credentials import Credentials, CredentialsError
from .linear.state import LinearError
from .slack.workspace import SlackError, valid_tz

if TYPE_CHECKING:
    from .apps import App
    from .server import Workplace

__all__ = [
    "ConfigError",
    "Applied",
    "FactoryConfig",
    "load_config",
    "apply",
    "write_env",
    "machine_tz",
]


class ConfigError(Exception):
    pass


@dataclass
class Applied:
    """What ``apply`` made (what was there already is not listed)."""

    #: The factory's app; ``None`` while it waits for its first admin.
    app: Optional["App"]
    made: List[str] = field(default_factory=list)
    #: No admin yet: the setup page makes one, then ``apply`` runs again.
    waiting: bool = False


_VARIABLE = re.compile(
    r"\$\{(credentials(?:\.[A-Za-z0-9_-]+)+|[A-Za-z_][A-Za-z0-9_]*)(?:(:-|:\?)([^}]*))?\}"
)


def substitute(
    value: Any,
    environ: Optional[Dict[str, str]] = None,
    credentials: Optional["Credentials"] = None,
) -> Any:
    """``${VAR}`` (required), ``${VAR:-default}`` and ``${VAR:?message}`` in every string,
    from the environment, and ``${credentials.a.b}`` from the environment's
    credentials: one config for every environment a factory runs in. A
    string that is one reference takes the value as it is (a mapping, a
    number)."""
    environ = os.environ if environ is None else environ
    if isinstance(value, dict):
        return {k: substitute(v, environ, credentials) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, environ, credentials) for v in value]
    if not isinstance(value, str):
        return value

    def lookup(match: "re.Match[str]") -> Any:
        name, kind, rest = match.group(1), match.group(2), match.group(3) or ""
        if name.startswith("credentials."):
            if credentials is None:
                raise ConfigError(
                    f"The config names {name}, and there are no credentials to read it from"
                )
            try:
                return credentials.get(name.removeprefix("credentials."))
            except CredentialsError as exc:
                if kind == ":-":
                    return rest
                raise ConfigError(str(exc))
        if environ.get(name):
            return environ[name]
        if kind == ":-":
            return rest
        raise ConfigError(rest or f"The environment has no {name}")

    whole = _VARIABLE.fullmatch(value.strip())
    if whole:
        return lookup(whole)
    return _VARIABLE.sub(lambda m: str(lookup(m)), value)


def load_config(
    path: Path, credentials: Optional["Credentials"] = None
) -> Dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"No {path}")
    config = substitute(
        yaml.safe_load(path.read_text(encoding="utf-8")) or {}, credentials=credentials
    )
    if not isinstance(config, dict):
        raise ConfigError(f"{path}: a workplace config is a mapping")
    for key in ("organization", "app"):
        if not isinstance(config.get(key), dict):
            raise ConfigError(f"{path}: it has no {key}:")
    return config


def machine_tz() -> Optional[str]:
    """This machine's time zone, as its IANA name (``TZ``, else ``/etc/localtime``'s)."""
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in candidates:
        try:
            if name:
                valid_tz(name)
                return name
        except SlackError:
            continue
    return None


def _admin(config: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The first admin ``admin:`` names; ``None`` when it names no one (the setup page makes them)."""
    raw = config.get("admin") or {}
    admin = {
        key: str(raw.get(key) or "").strip() for key in ("name", "username", "email")
    }
    if not any(admin.values()):
        return None
    missing = [key for key, value in admin.items() if not value]
    if missing:
        raise ConfigError(
            f"admin: needs {', '.join(missing)} too (or nothing, and the setup page makes the admin)"
        )
    admin["username"] = admin["username"].lower()
    return admin


def apply(
    workplace: "Workplace", config: Dict[str, Any], *, config_dir: Path
) -> Applied:
    w = workplace
    made: List[str] = []
    org = config["organization"]
    name, slug = (
        str(org.get("name") or "").strip(),
        str(org.get("slug") or "").strip().lower(),
    )
    if not name or not valid_username(slug):
        raise ConfigError(
            "organization: needs a name and a slug (lowercase letters, digits, - . and _)"
        )

    # The organization: a new workplace's, or this one's if it is the same.
    if not w.slack.name:
        tz = str(org.get("tz") or machine_tz() or "")
        if not tz:
            raise ConfigError(
                "organization: needs a tz (an IANA name, America/Chicago): this machine has none"
            )
        try:
            w.slack.set_team(name, slug, tz)
        except SlackError:
            raise ConfigError(f"organization: {tz} is not a time zone")
        w.linear.set_organization(name, slug)
        w.github.add_user(slug, name=name, type="Organization")
        made.append(f"the organization {name} ({slug})")
    elif w.slack.domain != slug:
        raise ConfigError(
            f"This workplace is {w.slack.name} ({w.slack.domain}); the config is for {slug}. "
            "Give `up` another --data, or change organization: (and the factory's repositories)"
        )
    owner = w.github.organization()
    if owner is None:
        raise ConfigError(f"The workplace {w.slack.name} has no GitHub organization")

    # Its first admin: the config's, or (naming none) whoever completes the setup page,
    # after which this runs again for the rest.
    if w.accounts.needs_setup():
        named = _admin(config)
        if named is None:
            return Applied(app=None, made=made, waiting=True)
        try:
            account = w.add_person(
                username=named["username"],
                name=named["name"],
                email=named["email"],
                admin=True,
                reserved=(slug,),
            )
        except AccountError as exc:
            raise ConfigError(f"admin: {exc.message}")
        w.slack.update_user(account.slack_id, tz=w.slack.tz)
        made.append(f"{account.name} ({account.username}), its admin")
    admin = next(a for a in w.accounts.people() if a.admin and not a.disabled)
    # Seeded, as Jumpstart's admin is: the first admin signs in with the config's
    # password (from the credentials) until they choose their own.
    password = str((config.get("admin") or {}).get("password") or "")
    unclaimed = w.unclaimed()
    if password and unclaimed is not None:
        try:
            w.accounts.set_password(unclaimed, password)
        except AccountError as exc:
            raise ConfigError(f"admin.password: {exc.message}")
        made.append(f"{unclaimed.name}'s password, from the config")

    if w.slack.general() is None:
        w.slack.create_channel(
            admin.slack_id,
            "general",
            is_general=True,
            invite=[a.slack_id for a in w.accounts.people() if not a.disabled],
        )
        made.append("#general")

    # The factory's app, and where its events go.
    raw = config["app"]
    app = w.apps.by_username(str(raw.get("username") or "").strip().lower())
    try:
        if app is None:
            app = w.apps.register(
                None,
                name=str(raw.get("name") or ""),
                username=str(raw.get("username") or ""),
                description=str(raw.get("description") or ""),
            )
            made.append(f"the app {app.name} ({app.username})")
        w.apps.configure(
            app,
            name=raw.get("name"),
            description=raw.get("description"),
            slack_delivery=raw.get("slack_delivery"),
            slack_request_url=raw.get("slack_request_url"),
            github_webhook_url=raw.get("github_webhook"),
            linear_webhook_url=raw.get("linear_webhook"),
            memory_webhook_url=raw.get("memory_webhook"),
        )
    except AccountError as exc:
        raise ConfigError(f"app: {exc.message}")

    w.memory.provision(app.id, "agent")
    for store_name, access in (config.get("memory") or {}).get("grants", {}).items():
        w.memory.grant(app.id, str(store_name), str(access))

    # Slack: its channels are default ones, which people join; the app is in each.
    for raw in (config.get("slack") or {}).get("channels") or []:
        raw = {"name": raw} if isinstance(raw, str) else raw
        channel = w.slack.channel_by_name(str(raw["name"]))
        if channel is None:
            people = [
                a.slack_id
                for a in w.accounts.people()
                if not a.disabled and a.slack_id != admin.slack_id
            ]
            try:
                channel = w.slack.create_channel(
                    admin.slack_id,
                    str(raw["name"]),
                    is_default=True,
                    purpose=str(raw.get("purpose") or ""),
                    invite=people,
                )
            except SlackError as exc:
                raise ConfigError(f"slack: the channel {raw['name']!r}: {exc}")
            made.append(f"#{channel.name}")
        w.slack.join(channel.id, app.slack_user_id)

    # GitHub: the repositories, with their files (``from:``, relative to the config) and labels.
    for raw in (config.get("github") or {}).get("repositories") or []:
        raw = {"name": raw} if isinstance(raw, str) else raw
        repo_name = str(raw["name"])
        full_name = f"{owner.login}/{repo_name}"
        repo = w.github.repos.get(full_name)
        if repo is None:
            source = raw.get("from")
            directory = (config_dir / str(source)).resolve() if source else None
            if directory is not None and not directory.is_dir():
                raise ConfigError(
                    f"github: {repo_name}'s from: {directory} is not a directory"
                )
            repo = w.github.create_repo(
                owner.login,
                repo_name,
                admin.github_login,
                description=str(raw.get("description") or ""),
                private=bool(raw.get("private", False)),
                auto_init=directory is None,
                protect_default_branch=bool(raw.get("protected", False)),
            )
            if directory is not None:
                w.github.import_directory(repo, directory, admin.github_login)
            if raw.get("self_hosted"):
                w.github.update_repo(repo, admin.github_login, self_hosted=True)
            _require_checks(w, repo, raw, admin.github_login, f"github: {repo_name}")
            made.append(f"the repository {full_name}")
        for label in raw.get("labels") or []:
            label = {"name": label} if isinstance(label, str) else label
            if label["name"] not in repo.labels:
                w.github.create_label(
                    repo,
                    str(label["name"]),
                    str(label.get("color") or "ededed"),
                    str(label.get("description") or ""),
                )

    # Canvases: each made from a directory of its files (relative to the config), once.
    for raw in config.get("canvases") or []:
        from .canvas.state import INDEX, CanvasError, read_canvas_dir

        title = str(raw.get("title") or "").strip()
        if not title:
            raise ConfigError("canvases: each has a title")
        try:
            files = (
                read_canvas_dir((config_dir / str(raw["from"])).resolve())
                if raw.get("from")
                else None
            )
        except CanvasError as exc:
            raise ConfigError(f"canvases: {title}: {exc.message}")
        # Its files' index names it once it is made (a rename changes both).
        named = {title}
        if files:
            try:
                named.add(str(json.loads(files[INDEX]).get("title") or title))
            except ValueError:
                raise ConfigError(f"canvases: {title}: {INDEX} is not JSON")
        if any(c.title in named for c in w.canvas.canvases.values()):
            continue
        try:
            w.canvas.create(admin.id, title, files=files)
        except CanvasError as exc:
            raise ConfigError(f"canvases: {title}: {exc.message}")
        made.append(f"the canvas {title}")

    # The factory's own repository: what it is made of, for people (and, nightly, the
    # factory itself) to change by pull request. main is protected: changes are merged.
    # Its jobs may use the self-hosted runner: its evaluations run the factory.
    source = (config.get("factory") or {}).get("repository")
    if source:
        source = {"name": source} if isinstance(source, str) else source
        repo_name = str(source.get("name") or "").strip()
        if not repo_name:
            raise ConfigError("factory.repository: it has a name")
        full_name = f"{owner.login}/{repo_name}"
        if full_name not in w.github.repos:
            checkout = (config_dir / str(source.get("from") or ".")).resolve()
            repo = w.github.create_repo(
                owner.login,
                repo_name,
                admin.github_login,
                description=str(source.get("description") or "The factory"),
                private=bool(source.get("private", False)),
                auto_init=False,
                protect_default_branch=True,
            )
            try:
                import_factory(w, repo, checkout, admin.github_login)
            except ConfigError:
                w.github.delete_repo(repo, admin.github_login)
                raise
            w.github.update_repo(repo, admin.github_login, self_hosted=True)
            _require_checks(w, repo, source, admin.github_login, "factory.repository")
            made.append(f"the factory's repository {full_name}")

    # Linear: the teams, with their labels.
    for raw in (config.get("linear") or {}).get("teams") or []:
        team = w.linear.find_team(str(raw.get("key") or ""))
        if team is None:
            try:
                team = w.linear.create_team(
                    str(raw.get("key") or ""),
                    str(raw.get("name") or ""),
                    str(raw.get("description") or ""),
                    creator_id=admin.linear_id,
                )
            except LinearError as exc:
                raise ConfigError(f"linear: {exc}")
            made.append(f"the Linear team {team.name} ({team.key})")
        for label in raw.get("labels") or []:
            label = {"name": label} if isinstance(label, str) else label
            if w.linear.find_label(str(label["name"]), team.id) is None:
                w.linear.create_label(
                    str(label["name"]),
                    str(label.get("color") or "#bec2c8"),
                    label.get("description"),
                    team.id,
                )
    return Applied(app=app, made=made)


def write_env(path: Path, env: Dict[str, str]) -> None:
    """``env`` in the dotenv file at ``path``: its other lines stay, these keys take the new values."""
    path = Path(path)
    kept = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0].removeprefix("export ").strip()
            if key not in env:
                kept.append(line)
    lines = kept + [f"{key}={shlex.quote(value)}" for key, value in env.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return
    import tempfile

    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".factory-env-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class FactoryConfig:
    """A factory's ``workplace.yml``, as ``up`` serves it: read (with the
    environment's credentials), applied, and its factory's credentials
    written; in development, edited from the UI (its configuration page)."""

    def __init__(self, path: Path, credentials: Optional[Credentials]):
        self.path = Path(path).resolve()
        self.root = self.path.parent
        self.credentials = credentials
        #: What the last apply wrote the factory's credentials to.
        self.env_file: Optional[Path] = None

    def load(self) -> Dict[str, Any]:
        return load_config(self.path, self.credentials)

    def raw(self) -> Dict[str, Any]:
        """The file as written: ``${...}`` references unread."""
        loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{self.path}: a workplace config is a mapping")
        return loaded

    def apply(self, workplace: "Workplace") -> Applied:
        config = self.load()
        applied = apply(workplace, config, config_dir=self.root)
        self.write_environment(workplace, config=config)
        return applied

    def write_environment(self, workplace: "Workplace", *, config=None) -> None:
        """Refresh connection credentials without reapplying demo seed configuration."""
        config = self.load() if config is None else config
        app = workplace.apps.by_username(str(config.get("app", {}).get("username", "")))
        if config.get("env_file") and app is not None:
            self.env_file = self.root / str(config["env_file"])
            factory_env = workplace.apps.env(app)
            if (config.get("factory") or {}).get("auth_token"):
                # The token the factory's own server takes (and the workplace calls it with).
                factory_env["FACTORY_AUTH_TOKEN"] = str(config["factory"]["auth_token"])
            if (config.get("factory") or {}).get("admin_auth_token"):
                factory_env["OMNIAGENTS_AGENT_HOST_CONTROL_TOKEN"] = str(
                    config["factory"]["admin_auth_token"]
                )
            write_env(self.env_file, factory_env)

    def save(self, workplace: "Workplace", raw: Dict[str, Any]) -> Applied:
        """Write ``raw`` as the file and apply it; the file is left as it was if it does not apply."""
        if not isinstance(raw, dict):
            raise ConfigError("A workplace config is a mapping")
        before = self.path.read_text(encoding="utf-8")
        # The file's opening comments stay (what it is, how it is used); the rest is the page's to explain.
        header = []
        for line in before.splitlines():
            if line.startswith("#") or (header and not line.strip()):
                header.append(line)
                continue
            break
        body = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100)
        self.path.write_text(
            ("\n".join(header).rstrip() + "\n\n" if header else "") + body,
            encoding="utf-8",
        )
        try:
            # Checked before anything is made: references resolve, the organization is this workplace's.
            config = self.load()
            if (
                workplace.slack.domain
                and str((config.get("organization") or {}).get("slug") or "")
                != workplace.slack.domain
            ):
                raise ConfigError(
                    "organization.slug is this workplace's (another one needs another --data)"
                )
        except ConfigError:
            self.path.write_text(before, encoding="utf-8")
            raise
        return self.apply(workplace)


def _require_checks(
    w: "Workplace", repo: Any, raw: Dict[str, Any], actor: str, where: str
) -> None:
    """``required_checks: [names]``: the checks pull requests into the default branch must pass."""
    names = raw.get("required_checks")
    if not names:
        return
    if not isinstance(names, list):
        raise ConfigError(f"{where}: required_checks is a list of check names")
    if repo.default_branch not in repo.protected:
        raise ConfigError(
            f"{where}: required_checks needs its default branch protected (protected: true)"
        )
    w.github.update_repo(
        repo, actor, required_checks={repo.default_branch: [str(n) for n in names]}
    )


def import_factory(
    workplace: "Workplace", repo: Any, checkout: Path, actor: str
) -> None:
    """The factory checkout's history into its new repository (its HEAD as main), or,
    before its first commit, the files git tracks there: never what .gitignore keeps
    out (keys, .env)."""
    import shutil
    import tempfile

    # A checkout another user owns (a container's mount of the server's): git's ownership check would refuse it.
    safe = ["-c", f"safe.directory={checkout}"]

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *safe, "-C", str(checkout), *args], capture_output=True, text=True
        )

    if not (checkout / ".git").exists():
        raise ConfigError(
            f"factory.repository: {checkout} is not a git repository (git init it)"
        )
    if git("rev-parse", "--verify", "HEAD").returncode == 0:
        # A fetch into the bare repository: its history as it is, no push hooks in the way.
        fetched = subprocess.run(
            [
                "git",
                *safe,
                "-C",
                str(repo.bare.path),
                "fetch",
                "--quiet",
                str(checkout),
                "HEAD:refs/heads/main",
            ],
            capture_output=True,
            text=True,
        )
        if fetched.returncode != 0:
            raise ConfigError(
                f"factory.repository: could not import {checkout}: {fetched.stderr.strip()}"
            )
        repo.bare.set_head("main")
        return
    listed = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if listed.returncode != 0:
        raise ConfigError(f"factory.repository: {listed.stderr.strip()}")
    files = [f for f in listed.stdout.split("\0") if f]
    with tempfile.TemporaryDirectory(prefix="workplace-factory-") as tmp:
        for name in files:
            target = Path(tmp) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(checkout / name, target)
        workplace.github.import_directory(repo, Path(tmp), actor)
