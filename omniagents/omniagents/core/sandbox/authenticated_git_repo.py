"""``AuthenticatedGitRepo`` — clone a *private* git remote into the sandbox
workspace, authenticating with a token the launcher injected into the agent
process environment.

Relationship to :class:`PartialCloneGitRepo` (and the SDK's ``GitRepo``):

* ``PartialCloneGitRepo`` clones ``https://<host>/<repo>`` blobless with no
  credentials — fine for public repos, useless for private ones. (It in turn
  replaces the SDK ``GitRepo``'s shallow single-branch clone; see its module
  docstring.)
* ``AuthenticatedGitRepo`` subclasses it and, before delegating to the same
  clone, configures a git **credential helper** inside the container so the
  token covers clone *and* the agent's later ``git fetch`` / ``git push`` — not
  just the initial clone.
CLI login (``gh`` / ``az devops``) is **not** done by this entry — it only owns
the git auth its clone needs. Configuring the host's CLI is the job of the
module-level :func:`configure_host_credentials`, which omni serve runs once at
boot for *every* linked host the project uses (so local-git checkouts and other
non-cloned sources get ``gh`` / ``az`` too, not just cloned remotes). That
function reuses :func:`write_git_credential_helper` here, so git + CLI auth share
one implementation. The CLIs' on-disk auth (``~/.config/gh/hosts.yml``,
``$HOME/.azure``) lives in ``$HOME`` too, so it shares the snapshot-exclusion
property below.

Why a credential helper (``store``) rather than embedding the token in the
clone URL:

* The token never lands in the cloned repo's ``.git/config`` remote URL (URL
  embedding would persist it there, and it could be pushed or leaked).
* ``~/.git-credentials`` lives in ``$HOME``, **outside** ``/workspace`` — so it
  is not captured by the workspace snapshot tar (which only archives
  ``/workspace``). The token survives for the live session (push works) but
  isn't carried into snapshots, where it could outlive its scope.

Token flow: the launcher resolves the host-matched credential, injects the
token into the ``omni serve`` process env under ``token_env``, and passes the
env var *name* (never the value) in the ``--source`` descriptor. This entry
reads ``os.environ[token_env]`` host-side and writes it into the container.

The token does appear in the argv of the in-container shell that writes the
credentials file (``ps`` inside the sandbox). That is acceptable: the agent
running in the sandbox is the intended holder of the token (it needs it to
push), so this is not a new disclosure. The threats this design defends against
are persistence in the snapshot and in ``.git/config`` — both avoided above.

Host mode (``unix_local``) is special-cased: the agent runs directly on the
host, which already carries the user's ambient git credentials. Writing
``~/.git-credentials`` there would clobber the real user's file, so credential
configuration is skipped and the clone uses ambient auth.
"""

from __future__ import annotations

import logging
import os
import shlex
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

from .partial_clone_git_repo import PartialCloneGitRepo

if TYPE_CHECKING:
    from pathlib import Path

    from agents.sandbox.materialization import MaterializedFile
    from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
    from agents.sandbox.types import ExecResult


logger = logging.getLogger("omniagents.sandbox.authenticated_git_repo")


def _is_github_host(host: str) -> bool:
    """True for github.com and GitHub Enterprise Server hosts. Mirrors the
    launcher's host heuristic (``defaultUsernameForHost``) so the two agree on
    which credentials are GitHub credentials worth logging ``gh`` into."""
    h = host.lower()
    return h == "github.com" or h.endswith(".github.com") or "github" in h


def _is_azure_devops_host(host: str) -> bool:
    """True for Azure DevOps hosts: ``dev.azure.com`` and the legacy
    ``{org}.visualstudio.com`` form."""
    h = host.lower()
    return h == "dev.azure.com" or h.endswith(".visualstudio.com")


def _azure_devops_org_url(host: str, repo: str) -> str | None:
    """Derive the organization URL ``az devops login`` keys on, from the parsed
    ``(host, repo)``. The org lives in different places per host form:

    * ``dev.azure.com``        → ``repo`` is ``{org}/{project}/_git/{name}``, so
      the org is the first path segment → ``https://dev.azure.com/{org}``.
    * ``{org}.visualstudio.com`` → the org is the hostname label, so the org URL
      is just ``https://{host}``.

    Returns ``None`` when the org can't be determined (login is then skipped)."""
    h = host.lower()
    if h.endswith(".visualstudio.com"):
        return f"https://{host}"
    org = repo.split("/", 1)[0].strip()
    return f"https://{host}/{org}" if org else None


class AuthenticatedGitRepo(PartialCloneGitRepo):
    """A :class:`PartialCloneGitRepo` that authenticates a private remote via a
    token read from the agent process environment.

    Extra fields beyond ``PartialCloneGitRepo``:

    * ``username`` — HTTPS basic-auth username paired with the token
      (``x-access-token`` for GitHub PATs, ``oauth2`` for GitLab, …).
    * ``token_env`` — name of the environment variable holding the token. The
      value is read host-side at :meth:`apply` time; only the name is part of
      the serialized manifest.
    """

    type: Literal["authenticated_git_repo"] = "authenticated_git_repo"  # type: ignore[assignment]
    username: str = "x-access-token"
    token_env: str

    async def apply(
        self,
        session: "BaseSandboxSession",
        dest: "Path",
        base_dir: "Path",
    ) -> "list[MaterializedFile]":
        # Local import: only needed to special-case host mode, and keeps this
        # module importable without eagerly pulling the unix_local session.
        from agents.sandbox.sandboxes.unix_local import (  # noqa: PLC0415
            UnixLocalSandboxSession,
        )

        token = os.environ.get(self.token_env) if self.token_env else None
        if not token:
            logger.warning(
                "AuthenticatedGitRepo: env %r is empty; cloning %s/%s without auth",
                self.token_env,
                self.host,
                self.repo,
            )
        elif isinstance(session, UnixLocalSandboxSession):
            # Host mode: don't touch the host user's ~/.git-credentials; rely on
            # their ambient git auth for the clone.
            logger.info(
                "AuthenticatedGitRepo: host mode — using ambient git credentials for %s",
                self.host,
            )
        else:
            # Clone-time auth only: configure the git credential helper so the
            # private clone (and the agent's later fetch/push) authenticate. CLI
            # login (`gh` / `az devops`) is NOT done here — that is owned by the
            # boot-time :func:`configure_host_credentials` pass, which runs for
            # every linked host the project uses, not just cloned remotes.
            await self._configure_credentials(session, token)

        return await super().apply(session, dest, base_dir)

    async def _configure_credentials(
        self, session: "BaseSandboxSession", token: str
    ) -> None:
        """Clone-time git auth: configure the ``store`` credential helper for
        ``self.host`` so the private clone succeeds. Delegates to the shared
        :func:`write_git_credential_helper`."""
        await write_git_credential_helper(session, self.host, self.username, token)


# ---------------------------------------------------------------------------
# Shared credential configuration — reused by the clone path above and by the
# boot-time pass that omni serve runs for every linked host the project uses.
# ---------------------------------------------------------------------------


# Resolve the *invoking* user's real ``$HOME`` before touching ~/.gitconfig,
# ~/.git-credentials, ~/.config/gh, or ~/.azure. ``sudo -u`` (how we drop to the
# profile's run_as identity) does not reliably reset ``$HOME``, so derive it from
# the passwd db for the effective uid. No-op for a plain root exec.
_HOME_PREFIX = 'H="$(getent passwd "$(id -u)" 2>/dev/null | cut -d: -f6)"; [ -n "$H" ] && export HOME="$H"; '


async def _exec_as(
    session: "BaseSandboxSession", script: str, *, user: str | None
) -> "ExecResult":
    """Run ``script`` in a shell, resolving the caller's real ``$HOME`` first, as
    ``user`` (``sudo -u`` via the SDK) or the session default when ``user`` is
    ``None``."""
    if user:
        return await session.exec(_HOME_PREFIX + script, user=user)
    return await session.exec(_HOME_PREFIX + script)


async def write_git_credential_helper(
    session: "BaseSandboxSession",
    host: str,
    username: str,
    token: str,
    *,
    user: str | None = None,
) -> None:
    """Configure the ``store`` credential helper and write ``~/.git-credentials``
    (mode 0600) in the container's ``$HOME`` for ``host``, as ``user``.

    ``$HOME`` (not ``/workspace``) so the file is never archived into the
    workspace snapshot. The token is in this command's argv only within the
    trusted in-container shell (see module docstring).

    Concurrency-safe: the SDK materializes multiple git-remote sources in
    parallel, so several of these run at once against the *same* ~/.gitconfig and
    ~/.git-credentials. The whole op runs under an ``flock`` (best-effort — the
    devbox ships util-linux; if ``flock`` is absent the unique ``mktemp`` temp +
    idempotent content still avoid the worst races), and the credentials file is
    updated via a unique temp + atomic ``mv`` (no shared ``.tmp`` to stat-race
    on). Idempotent: drops any prior line for this host before appending."""
    # git's credential-store format percent-decodes userinfo, so encode it.
    userinfo = f"{quote(username, safe='')}:{quote(token, safe='')}"
    cred_line = f"https://{userinfo}@{host}"
    body = (
        "umask 077; "
        "git config --global credential.helper store || exit 11; "
        'f="$HOME/.git-credentials"; touch "$f"; '
        't="$(mktemp "$f.XXXXXX")" || exit 12; '
        f"{{ grep -vF {shlex.quote(f'@{host}')} \"$f\" 2>/dev/null || true; "
        f"printf '%s\\n' {shlex.quote(cred_line)}; }} > \"$t\" && mv \"$t\" \"$f\" || exit 13"
    )
    # Lock under $HOME, not a shared /tmp path: clone-time runs as root and the
    # boot pass as the run_as user (uid 1000), so a single /tmp lock created by
    # root is mode-0600/root-owned and unopenable by uid 1000 ("cannot create
    # ... Permission denied"). A per-$HOME lock is always writable by the running
    # uid, and still serializes the concurrent same-uid clone-time writers
    # (which is where the git-config race actually happens). flock stays
    # best-effort: if it can't be taken, the unique mktemp temp still prevents
    # the credentials-file race.
    script = (
        'L="$HOME/.omni-git-cred.lock"; '
        f'if command -v flock >/dev/null 2>&1; then exec 9>"$L" 2>/dev/null && flock 9 2>/dev/null; fi; {body}'
    )
    res = await _exec_as(session, script, user=user)
    if not res.ok():
        logger.warning(
            "credentials: configuring git auth for %s failed: %s",
            host,
            res.stderr.decode("utf-8", errors="replace"),
        )


async def _gh_login(
    session: "BaseSandboxSession", host: str, token: str, *, user: str | None = None
) -> None:
    """Log the GitHub CLI (``gh``) into ``host`` as ``user``. ``gh auth login
    --with-token`` reads the token from stdin and writes ``~/.config/gh/hosts.yml``
    in ``$HOME`` — same snapshot-excluded posture as ``~/.git-credentials``.

    Best-effort: no-op (info log) when ``gh`` isn't installed (the ``-min`` /
    fast-ACI image omits it); warn-and-continue on login failure."""
    check = await _exec_as(session, "command -v gh >/dev/null 2>&1", user=user)
    if not check.ok():
        logger.info(
            "credentials: `gh` not installed in this sandbox; skipping gh login "
            "for %s (git auth is unaffected)",
            host,
        )
        return

    login = await _exec_as(
        session,
        f"printf '%s' {shlex.quote(token)} | gh auth login --hostname {shlex.quote(host)} --with-token",
        user=user,
    )
    if not login.ok():
        logger.warning(
            "credentials: `gh auth login` failed for %s: %s",
            host,
            login.stderr.decode("utf-8", errors="replace"),
        )
    else:
        logger.info("credentials: gh CLI logged in for %s", host)


async def _az_devops_login(
    session: "BaseSandboxSession", host: str, repo: str, token: str, *, user: str | None = None
) -> None:
    """Log the Azure CLI's azure-devops extension into the org derived from
    ``(host, repo)`` as ``user``. ``az devops login --organization <url>`` reads
    the PAT from stdin and stores it under ``$HOME/.azure`` — snapshot-excluded.

    Best-effort: no-op when ``az`` isn't installed or the org can't be derived;
    warn-and-continue on login failure."""
    org_url = _azure_devops_org_url(host, repo)
    if not org_url:
        logger.info(
            "credentials: could not derive Azure DevOps org from %s/%s; "
            "skipping az devops login (git auth is unaffected)",
            host,
            repo,
        )
        return

    check = await _exec_as(session, "command -v az >/dev/null 2>&1", user=user)
    if not check.ok():
        logger.info(
            "credentials: `az` not installed in this sandbox; skipping az devops "
            "login for %s (git auth is unaffected)",
            org_url,
        )
        return

    login = await _exec_as(
        session,
        f"printf '%s' {shlex.quote(token)} | az devops login --organization {shlex.quote(org_url)}",
        user=user,
    )
    if not login.ok():
        logger.warning(
            "credentials: `az devops login` failed for %s: %s",
            org_url,
            login.stderr.decode("utf-8", errors="replace"),
        )
    else:
        logger.info("credentials: az devops logged in for %s", org_url)


async def configure_host_credentials(
    session: "BaseSandboxSession",
    *,
    host: str,
    repo: str,
    username: str,
    token: str,
    user: str | None = None,
) -> None:
    """Boot-time credential setup for one linked host the project uses: configure
    the git credential helper **and** log the host's CLI in, so the agent's
    ``git`` / ``gh`` / ``az devops`` commands all authenticate — for *any* source
    kind (git-remote, local-git, …), not just cloned remotes.

    ``user`` is the identity to write the credentials as — the profile's
    ``run_as`` (e.g. ``"1000:1000"``), passed through to ``session.exec(user=...)``
    (``docker exec --user`` for the docker backend). This MUST match the identity
    the agent and terminal run as: a devbox runs tools as uid 1000, so credentials
    written as root (``/root``) would be invisible to ``gh``/``git`` at runtime.
    ``None`` writes as the session default.

    Host mode (``unix_local``) is skipped: the agent runs on the host with the
    real user's ambient git/CLI auth, which we must not clobber. Each step is
    best-effort and independent — a CLI that isn't installed or rejects the token
    never blocks the others.

    ``repo`` is the parsed repo path (``owner/name`` for GitHub,
    ``{org}/{project}/_git/{name}`` for Azure DevOps); it's only needed to derive
    the Azure DevOps organization and may be empty for non-ADO hosts."""
    from agents.sandbox.sandboxes.unix_local import (  # noqa: PLC0415
        UnixLocalSandboxSession,
    )

    if isinstance(session, UnixLocalSandboxSession):
        logger.info("credentials: host mode — leaving ambient git/CLI auth for %s", host)
        return

    await write_git_credential_helper(session, host, username, token, user=user)
    if _is_github_host(host):
        await _gh_login(session, host, token, user=user)
    elif _is_azure_devops_host(host):
        await _az_devops_login(session, host, repo, token, user=user)
