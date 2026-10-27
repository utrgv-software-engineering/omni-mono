"""``omniagents-workplace serve``: run the workplace on this machine (``up``: as a factory's config makes it)."""

from __future__ import annotations

import argparse
import logging
import os
import ipaddress
import socket
import subprocess
from pathlib import Path
from typing import List, Optional

from .server import Workplace, create_app


def docker_bridge_gateway() -> Optional[str]:
    """The address this machine has on Docker's bridge network, if it has one.

    Containers reach it as ``host.docker.internal`` on Linux. With Docker
    Desktop the bridge is inside Docker's VM, the address is not this
    machine's, and binding it fails (Docker Desktop forwards to localhost).
    """
    try:
        out = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                "bridge",
                "--format",
                "{{range .IPAM.Config}}{{.Gateway}} {{end}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    for value in out:
        try:
            if ipaddress.ip_address(value).version == 4:
                return value
        except ValueError:
            continue
    return None


def _sockets(host: str, port: int, also: Optional[str]) -> List[socket.socket]:
    sockets = []
    for address in [host] + ([also] if also and also != host else []):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((address, port))
        except OSError:
            sock.close()
            if address == host:
                raise
            continue
        sock.listen(128)
        sock.set_inheritable(True)
        sockets.append(sock)
    return sockets


def default_data_dir() -> Path:
    """``$XDG_DATA_HOME/omniagents-workplace`` (``~/.local/share/...``)."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "omniagents-workplace"


def print_env(data: Path, username: Optional[str]) -> None:
    """``export`` lines for the app: its tokens, and the workplace's URLs as it was last served."""
    import shlex
    import sys

    if not (data / "workplace.db").is_file():
        sys.exit(f"No workplace in {data}")
    served = {}
    from .store import Store

    store = Store(data / "workplace.db")
    served = store.meta("workplace.served_at") or {}
    store.close()
    if not served:
        sys.exit(
            "The workplace has not been served yet: start it with `omniagents-workplace serve`"
        )
    workplace = Workplace(
        data,
        base_url=served["base_url"],
        sandbox_url=served.get("sandbox_url"),
        resume=False,
    )
    try:
        apps = workplace.apps.active()
        if username:
            apps = [a for a in apps if a.username == username]
        if len(apps) != 1:
            names = ", ".join(a.username for a in workplace.apps.active()) or "none"
            sys.exit(f"Name the app with --app (apps: {names})")
        for key, value in workplace.apps.env(apps[0]).items():
            print(f"export {key}={shlex.quote(value)}")
    finally:
        workplace.close()


def _environment(parser: argparse.ArgumentParser) -> None:
    from .credentials import ENVIRONMENTS

    parser.add_argument(
        "--environment",
        choices=ENVIRONMENTS,
        default=os.environ.get("WORKPLACE_ENV") or "development",
        help="The environment it runs as, as Rails has them: its credentials, and what it allows "
        "(default: WORKPLACE_ENV, else %(default)s)",
    )


def _serve_options(parser: argparse.ArgumentParser) -> None:
    _environment(parser)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7300)
    parser.add_argument(
        "--data",
        type=Path,
        default=default_data_dir(),
        help="Where the workplace keeps its state (default: %(default)s)",
    )
    parser.add_argument(
        "--sandbox-url",
        help="The URL a factory's sandboxes reach the workplace at (default: host.docker.internal on --port, "
        "Docker's name for this machine)",
    )
    parser.add_argument(
        "--public-url",
        help="The URL people and links reach the workplace at (default: http://<host>:<port>)",
    )
    parser.add_argument(
        "--internal-url",
        help="The URL a factory beside the workplace reaches it at, in its apps' environment "
        "(default: the public URL)",
    )
    parser.add_argument(
        "--sandbox-port",
        type=int,
        help="Also listen on this port for sandboxes: it serves git and uploads only (a deployment's "
        "sandboxes reach the workplace there; give --sandbox-url to match)",
    )
    parser.add_argument(
        "--trust-proxy",
        action="store_true",
        help="Behind a reverse proxy (Caddy, nginx): take the scheme and client address from its "
        "X-Forwarded-* headers. Only when nothing but the proxy reaches the port",
    )
    parser.add_argument(
        "--proxy-ips",
        default="127.0.0.1,::1",
        help="Trusted reverse-proxy IPs/CIDRs (default: loopback); never use * in production",
    )
    parser.add_argument(
        "--workflows",
        choices=("host", "docker", "off"),
        default="host",
        help="Where repositories' workflows run: on this machine as this user (host, for your own "
        "computer), in a container per job (docker, for a workplace others push to), or not at all "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--workflow-image",
        help="The image a job's container starts from (with --workflows docker)",
    )
    parser.add_argument(
        "--workflow-network",
        default="bridge",
        help="The Docker network jobs' containers are on (default: %(default)s)",
    )
    parser.add_argument(
        "--workflow-memory",
        default="2g",
        help="A job container's memory (default: %(default)s)",
    )
    parser.add_argument(
        "--workflow-cpus",
        default="2",
        help="A job container's CPUs (default: %(default)s)",
    )
    parser.add_argument(
        "--self-hosted-image",
        help="The self-hosted runner (with --workflows docker): `runs-on: self-hosted` jobs, in the "
        "repositories an admin allows, run in containers of this image with Docker",
    )
    parser.add_argument(
        "--self-hosted-network",
        help="The Docker network the self-hosted runner's jobs, and the containers they start, are on",
    )
    parser.add_argument(
        "--self-hosted-volume",
        action="append",
        default=[],
        metavar="SRC:DST[:ro]",
        help="A volume the self-hosted runner's jobs get (repeatable)",
    )
    parser.add_argument(
        "--self-hosted-env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="An environment variable the self-hosted runner's jobs get (repeatable)",
    )


def invite_link(data: Path, username: str) -> str:
    """A new invite link for ``username`` (any earlier one stops working), at the URL the workplace was last served."""
    from .store import Store

    if not (data / "workplace.db").is_file():
        raise SystemExit(f"No workplace in {data}")
    store = Store(data / "workplace.db")
    served = store.meta("workplace.served_at") or {}
    store.close()
    if not served:
        raise SystemExit("The workplace has not been served yet")
    workplace = Workplace(
        data,
        base_url=served["base_url"],
        sandbox_url=served.get("sandbox_url"),
        resume=False,
    )
    try:
        account = workplace.accounts.by_username(username)
        if account is None or account.disabled:
            raise SystemExit(f"No one is {username} here")
        return f"{served['base_url'].rstrip('/')}/invite/{workplace.accounts.invite(account)}"
    finally:
        workplace.close()


def repoint_webhooks(data: Path, to: str) -> List[str]:
    """Point every app's webhooks at ``to``'s scheme, host and port, keeping each one's
    path: a copy of a workplace (staging, a check's replay) sends its events to its own
    factory, never to the one it was copied from. What changed, one line each."""
    from urllib.parse import urlsplit, urlunsplit

    from .store import Store

    if not (data / "workplace.db").is_file():
        raise SystemExit(f"No workplace in {data}")
    target = urlsplit(to)
    if not target.scheme or not target.netloc:
        raise SystemExit(f"--to is a URL such as http://127.0.0.1:8765, not {to!r}")

    def moved(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        parts = urlsplit(url)
        return urlunsplit((target.scheme, target.netloc, parts.path, parts.query, ""))

    store = Store(data / "workplace.db")
    served = store.meta("workplace.served_at") or {}
    store.close()
    workplace = Workplace(
        data,
        base_url=served.get("base_url") or "http://127.0.0.1:7300",
        sandbox_url=served.get("sandbox_url"),
        resume=False,
    )
    changed = []
    try:
        for app in workplace.apps.active():
            settings = workplace.apps.settings(app)
            github = moved(settings["github"]["webhook_url"])
            linear = moved(settings["linear"]["webhook_url"])
            slack = moved(settings["slack"].get("request_url"))
            workplace.apps.configure(
                app,
                github_webhook_url=github,
                linear_webhook_url=linear,
                slack_request_url=slack,
            )
            changed += [
                f"{app.username}: {kind} webhooks go to {url}"
                for kind, url in (
                    ("GitHub", github),
                    ("Linear", linear),
                    ("Slack", slack),
                )
                if url
            ]
    finally:
        workplace.close()
    return changed


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="omniagents-workplace")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Run the workplace and its UI")
    _serve_options(serve)
    up = sub.add_parser(
        "up", help="Make what a factory's workplace.yml needs, then run the workplace"
    )
    up.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=Path("workplace.yml"),
        help="The factory's workplace config (default: ./workplace.yml)",
    )
    _serve_options(up)
    backup = sub.add_parser(
        "backup",
        help="Copy a workplace's state (its database and repositories), "
        "while it runs, into a dated directory",
    )
    backup.add_argument(
        "--data",
        type=Path,
        default=default_data_dir(),
        help="The workplace's data directory",
    )
    backup.add_argument("--to", type=Path, required=True, help="Where backups go")
    backup.add_argument(
        "--keep",
        type=int,
        default=14,
        help="How many backups to keep (default: %(default)s)",
    )
    restore = sub.add_parser(
        "restore",
        help="Replace a workplace's state with a backup's (stop the workplace first)",
    )
    restore.add_argument(
        "--data",
        type=Path,
        default=default_data_dir(),
        help="The workplace's data directory",
    )
    restore.add_argument(
        "--from",
        dest="source",
        type=Path,
        required=True,
        help="A backup, or a directory of them (the latest is restored)",
    )
    creds = sub.add_parser(
        "credentials",
        help="Edit or show an environment's encrypted credentials "
        "(config/credentials/<environment>.yml.enc beside workplace.yml)",
    )
    creds.add_argument(
        "action",
        choices=("create", "edit", "show"),
        help="create: a new environment's credentials and key (no editor); edit: in $EDITOR; show",
    )
    _environment(creds)
    creds.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="The factory's directory, where workplace.yml is (default: here)",
    )
    inv = sub.add_parser(
        "invite",
        help="Print an invite link for someone to choose a new password with, as "
        "Jumpstart's admins use the Rails console (stop the workplace first)",
    )
    inv.add_argument("username")
    inv.add_argument(
        "--data",
        type=Path,
        default=default_data_dir(),
        help="The workplace's data directory",
    )
    hooks = sub.add_parser(
        "webhooks",
        help="Point every app's webhooks at another host (a copy of a "
        "workplace, for its own factory; stop the workplace first)",
    )
    hooks.add_argument(
        "--data",
        type=Path,
        default=default_data_dir(),
        help="The workplace's data directory",
    )
    hooks.add_argument(
        "--to",
        required=True,
        help="Where they go now: scheme, host and port (http://127.0.0.1:8765)",
    )
    env = sub.add_parser(
        "env", help="Print the environment a factory connects as an app with"
    )
    env.add_argument(
        "--data",
        type=Path,
        default=default_data_dir(),
        help="The workplace's data directory",
    )
    env.add_argument("--app", help="The app's username (default: the only one)")
    args = parser.parse_args(argv)

    if args.command == "env":
        print_env(args.data, args.app)
        return
    if args.command == "invite":
        print(invite_link(args.data, args.username))
        return
    if args.command == "webhooks":
        for line in repoint_webhooks(args.data, args.to) or ["No app has webhooks"]:
            print(line)
        return
    if args.command == "credentials":
        from .credentials import Credentials, CredentialsError, edit

        credentials = Credentials(args.root, args.environment)
        try:
            if args.action == "show":
                print(credentials.read(), end="")
            elif args.action == "create":
                if credentials.exists():
                    print(f"{credentials.path} exists")
                else:
                    credentials.create()
                    print(
                        f"Made {credentials.path} and its key, {credentials.key_path} (do not commit the key)"
                    )
                admin = credentials.get("admin") or {}
                if admin.get("username"):
                    print(
                        f"The first admin signs in as {admin['username']}, with the password in "
                        f"`omniagents-workplace credentials show --environment {args.environment}`"
                    )
                else:
                    print(
                        "No admin yet: the workplace's first visit asks who it is (or name one under admin: with "
                        f"`omniagents-workplace credentials edit --environment {args.environment}`)"
                    )
            else:
                edit(credentials)
        except CredentialsError as exc:
            raise SystemExit(str(exc))
        return
    if args.command == "restore":
        from .backup import restore as restore_backup

        print(f"Restored {restore_backup(args.source, args.data)} into {args.data}")
        return
    if args.command == "backup":
        from .backup import backup as make_backup

        print(make_backup(args.data, args.to, keep=args.keep))
        return

    import sys

    import uvicorn

    # The links it prints are how people get in: they go out as printed,
    # to a terminal or a log.
    sys.stdout.reconfigure(line_buffering=True)

    logging.basicConfig(level=logging.WARNING)
    base_url = args.public_url or f"http://{args.host}:{args.port}"
    if args.environment in ("staging", "production"):
        # What a server needs, said rather than assumed.
        refused = []
        if args.workflows == "host":
            refused.append(
                "--workflows host runs other people's workflows as this user: use docker (or off)"
            )
        if "*" in args.proxy_ips:
            refused.append("--proxy-ips must name specific trusted proxies, not *")
        if not args.trust_proxy:
            refused.append("a server is behind its reverse proxy: give --trust-proxy")
        if not base_url.startswith("https://"):
            refused.append("--public-url is the proxy's https:// address")
        if refused:
            sys.exit(
                f"The {args.environment} environment refuses to start:\n  "
                + "\n  ".join(refused)
            )
    config = None
    credentials = None
    if args.command == "up":
        from .config import ConfigError, load_config
        from .credentials import Credentials

        credentials = Credentials(args.config.resolve().parent, args.environment)
        try:
            config = load_config(args.config, credentials)
        except ConfigError as exc:
            sys.exit(str(exc))
    container = None
    if args.workflows == "docker":
        from .github.spaces import ContainerSettings

        if not args.workflow_image:
            sys.exit(
                "--workflows docker runs jobs in containers of an image: give it with --workflow-image"
            )
        container = ContainerSettings(
            image=args.workflow_image,
            network=args.workflow_network,
            memory=args.workflow_memory,
            cpus=args.workflow_cpus,
        )
    self_hosted = None
    if args.self_hosted_image:
        from .github.spaces import ContainerSettings

        if args.workflows != "docker":
            sys.exit(
                "--self-hosted-image is for --workflows docker (on this machine, self-hosted jobs run here)"
            )
        if not args.self_hosted_network:
            sys.exit(
                "--self-hosted-image: give the network its jobs are on with --self-hosted-network"
            )
        env = dict(item.split("=", 1) for item in args.self_hosted_env if "=" in item)
        if len(env) != len(args.self_hosted_env):
            sys.exit("--self-hosted-env is NAME=VALUE")
        self_hosted = ContainerSettings(
            image=args.self_hosted_image,
            network=args.self_hosted_network,
            memory=args.workflow_memory,
            cpus=args.workflow_cpus,
            docker_socket=True,
            volumes=list(args.self_hosted_volume),
            env=env,
            label="the self-hosted runner",
        )
    # A workplace starts empty: seeds are evaluations' (evaluation.py); `up` makes what its config names.
    from .security import lock_data_directory

    server_lock = lock_data_directory(args.data)
    workplace = Workplace(
        args.data,
        base_url=base_url,
        sandbox_url=args.sandbox_url,
        run_workflows=args.workflows != "off",
        workflow_container=container,
        self_hosted_runner=self_hosted,
        internal_url=args.internal_url,
        environment=args.environment,
    )
    print(f"Environment: {args.environment}")
    print(
        f"Workflows: {dict(host='on this machine', docker=f'in containers of {args.workflow_image}', off='off')[args.workflows]}"
    )
    if self_hosted is not None:
        print(
            f"Self-hosted runner: containers of {self_hosted.image}, on {self_hosted.network}"
        )
    print(f"Workplace: {base_url} (state in {args.data})")
    if config is not None:
        from .config import ConfigError, FactoryConfig

        workplace.factory_config = FactoryConfig(args.config, credentials)
        try:
            applied = workplace.factory_config.apply(workplace)
        except ConfigError as exc:
            workplace.close()
            sys.exit(f"{args.config}: {exc}")
        for made in applied.made:
            print(f"  made {made}")
        if applied.waiting:
            print("  the rest waits for its first admin (the setup page below)")
        if workplace.factory_config.env_file is not None:
            print(
                f"{applied.app.name}'s credentials are in {workplace.factory_config.env_file}"
            )
        # On a laptop; on a server (behind its proxy) the factory is started beside it.
        if config.get("start") and not args.trust_proxy:
            print(
                f"Start the factory, in {args.config.resolve().parent}: {config['start']}"
            )
    # A factory's sandboxes clone from the workplace: on Linux they reach
    # this machine on Docker's bridge, so listen there too (a deployment
    # gives them a port of their own instead).
    bridge = None if args.sandbox_port else docker_bridge_gateway()
    sockets = _sockets(args.host, args.port, bridge)
    if args.sandbox_port:
        sockets += _sockets(args.host, args.sandbox_port, None)
    if workplace.accounts.needs_setup():
        print(
            f"\nSet it up: open {base_url} on this machine, or from elsewhere {base_url}/setup/{workplace.setup_token}"
            " (the link works while this server runs)"
        )
    unclaimed = workplace.unclaimed()
    if unclaimed is not None:
        link = f"{base_url}/invite/{workplace.accounts.invite(unclaimed)}"
        if args.trust_proxy:
            # Behind a proxy no one is "on this machine": the link is the way in.
            print(
                f"\n{unclaimed.name} ({unclaimed.username}), its admin, chooses a password at: {link}"
            )
        else:
            print(
                f"\nYou are {unclaimed.name} ({unclaimed.username}), its admin: open {base_url} on this machine "
                f"and choose a password (from elsewhere: {link})"
            )
    for account in workplace.accounts.people():
        if (
            account.admin
            and account.password is None
            and not account.disabled
            and account is not unclaimed
        ):
            print(
                f"\n{account.name} ({account.username}), an admin, joins with: "
                f"{base_url}/invite/{workplace.accounts.invite(account)}"
            )
    workplace.store.set_meta(
        "workplace.served_at",
        {"base_url": base_url, "sandbox_url": workplace.sandbox_url},
    )
    if workplace.apps.active() and config is None:
        print(
            f"\nA factory connects as an app: `omniagents-workplace env --data {args.data}` prints one's."
        )
    print()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(
                workplace,
                sandbox_addresses=[bridge] if bridge and bridge != args.host else [],
                sandbox_ports=[args.sandbox_port] if args.sandbox_port else [],
            ),
            log_level="warning",
            proxy_headers=args.trust_proxy,
            forwarded_allow_ips=args.proxy_ips if args.trust_proxy else None,
            ws_max_size=1024 * 1024,
            workers=1,
        )
    )
    server.run(sockets=sockets)


if __name__ == "__main__":
    main()
