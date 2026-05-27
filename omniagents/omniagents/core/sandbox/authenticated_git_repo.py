"""``AuthenticatedGitRepo`` — clone a *private* git remote into the sandbox
workspace, authenticating with a token the launcher injected into the agent
process environment.

Relationship to the SDK's ``GitRepo``:

* ``GitRepo`` clones ``https://<host>/<repo>.git`` with no credentials — fine
  for public repos, useless for private ones.
* ``AuthenticatedGitRepo`` subclasses it and, before delegating to the same
  clone, configures a git **credential helper** inside the container so the
  token covers clone *and* the agent's later ``git fetch`` / ``git push`` — not
  just the initial clone.

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

from agents.sandbox.entries import GitRepo

if TYPE_CHECKING:
    from pathlib import Path

    from agents.sandbox.materialization import MaterializedFile
    from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
    from agents.sandbox.types import ExecResult


logger = logging.getLogger("omniagents.sandbox.authenticated_git_repo")


def _normalize_clone_url(url: str) -> str:
    """The SDK's ``GitRepo`` appends ``.git`` to every clone URL. Azure DevOps
    repo URLs are ``https://dev.azure.com/{org}/{project}/_git/{repo}`` and
    reject the ``.git`` suffix, so strip it for ADO-style URLs (identified by
    the ``/_git/`` path segment). GitHub/GitLab URLs are left untouched.
    """
    if "/_git/" in url and url.endswith(".git"):
        return url[: -len(".git")]
    return url


class AuthenticatedGitRepo(GitRepo):
    """A ``GitRepo`` that authenticates a private remote via a token read from
    the agent process environment.

    Extra fields beyond ``GitRepo``:

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
            await self._configure_credentials(session, token)

        return await super().apply(session, dest, base_dir)

    # The SDK builds clone/fetch URLs as `https://{host}/{repo}.git`. Override
    # both entry points to strip the `.git` for Azure DevOps `_git/` URLs, which
    # 404 with it. (Keyword-only args mirror the base signatures.)
    async def _clone_named_ref(
        self, *, session: "BaseSandboxSession", url: str, tmp_dir: str
    ) -> "ExecResult":
        return await super()._clone_named_ref(session=session, url=_normalize_clone_url(url), tmp_dir=tmp_dir)

    async def _fetch_commit_ref(
        self, *, session: "BaseSandboxSession", url: str, tmp_dir: str
    ) -> "ExecResult":
        return await super()._fetch_commit_ref(session=session, url=_normalize_clone_url(url), tmp_dir=tmp_dir)

    async def _configure_credentials(
        self, session: "BaseSandboxSession", token: str
    ) -> None:
        """Configure the ``store`` credential helper and write
        ``~/.git-credentials`` (mode 0600) in the container's ``$HOME``.
        """
        # git's credential-store format percent-decodes userinfo, so encode it.
        userinfo = f"{quote(self.username, safe='')}:{quote(token, safe='')}"
        cred_line = f"https://{userinfo}@{self.host}"

        set_helper = await session.exec(
            "git", "config", "--global", "credential.helper", "store", shell=False
        )
        if not set_helper.ok():
            logger.warning(
                "AuthenticatedGitRepo: `git config credential.helper store` failed: %s",
                set_helper.stderr.decode("utf-8", errors="replace"),
            )
            return

        # Write into $HOME (resolved by the container shell), not /workspace, so
        # the file is never archived into the workspace snapshot. umask 077 →
        # 0600. The token is in this command's argv only within the trusted
        # in-container shell (see module docstring).
        write = await session.exec(
            f'umask 077 && printf "%s\\n" {shlex.quote(cred_line)} > "$HOME/.git-credentials"'
        )
        if not write.ok():
            logger.warning(
                "AuthenticatedGitRepo: writing ~/.git-credentials failed: %s",
                write.stderr.decode("utf-8", errors="replace"),
            )
