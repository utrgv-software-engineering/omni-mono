"""``PartialCloneGitRepo`` — clone a git remote into the sandbox workspace as
a *blobless partial clone* instead of the SDK's shallow single-branch clone.

Why this overrides the SDK's clone: ``GitRepo`` seeds the workspace with
``git clone --depth 1 --no-tags [--branch <ref>]``. ``--depth`` implies
``--single-branch``, which writes a narrowed fetch refspec
(``+refs/heads/<ref>:refs/remotes/origin/<ref>``) into the cloned repo's
config — and that config is copied into ``/workspace/<mount>`` along with the
checkout. The workspace is not a disposable snapshot: the user gets a terminal
in it and the agent does real git work in it, and the shallow clone breaks
both —

* ``git fetch`` only ever fetches the seeded branch, so no other remote
  branch ever appears in ``git branch -r`` no matter how often the user
  fetches;
* history is one commit deep — ``git log``, ``git blame``, ``git describe``,
  ``git merge-base`` (and thus rebases/diffs against the base branch) are
  useless;
* ``--no-tags`` persists as ``remote.origin.tagOpt``, so tags never arrive.

``--filter=blob:none`` keeps the seed nearly as cheap (commits and trees are
small; blobs — the bulk — are fetched lazily on demand) while the repo behaves
normally: full history, all refs, the wildcard fetch refspec, working
``log``/``blame``/``merge-base``. The container keeps authenticated network
access to the remote for its whole life, so lazy blob fetch is safe here.
Servers that don't support partial clone ignore the filter and serve a full
clone (git warns and proceeds), so the degraded case is just a regular clone.

URL normalization: the SDK builds clone/fetch URLs as
``https://{host}/{repo}.git``, but Azure DevOps ``…/_git/<repo>`` URLs reject
the ``.git`` suffix, so it's stripped for ADO-style URLs (identified by the
``/_git/`` path segment). GitHub/GitLab URLs are left untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents.sandbox.entries import GitRepo

if TYPE_CHECKING:
    from agents.sandbox.session.base_sandbox_session import BaseSandboxSession
    from agents.sandbox.types import ExecResult


__all__ = ["PartialCloneGitRepo"]


def _normalize_clone_url(url: str) -> str:
    """The SDK's ``GitRepo`` appends ``.git`` to every clone URL. Azure DevOps
    repo URLs are ``https://dev.azure.com/{org}/{project}/_git/{repo}`` and
    reject the ``.git`` suffix, so strip it for ADO-style URLs (identified by
    the ``/_git/`` path segment). GitHub/GitLab URLs are left untouched.
    """
    if "/_git/" in url and url.endswith(".git"):
        return url[: -len(".git")]
    return url


class PartialCloneGitRepo(GitRepo):
    """A ``GitRepo`` that clones blobless (``--filter=blob:none``) so the
    seeded workspace has full history, all remote refs, the wildcard fetch
    refspec, and fetchable tags. See module docstring for the rationale.
    """

    type: Literal["partial_clone_git_repo"] = "partial_clone_git_repo"  # type: ignore[assignment]

    async def _clone_named_ref(
        self, *, session: "BaseSandboxSession", url: str, tmp_dir: str
    ) -> "ExecResult":
        # A source added without a branch in the UI means "use the repo's
        # default branch". That empty ref arrives here as the "HEAD" sentinel
        # (``serve_cli`` does ``src.get("ref") or "HEAD"``). "HEAD" is not a
        # real branch/tag name, so ``git clone --branch HEAD`` fails with
        # "Remote branch HEAD not found" — a plain clone checks out whatever
        # the remote's default branch is, which is what the empty field means.
        branch = [] if self.ref in ("HEAD", "", None) else ["--branch", self.ref]
        return await session.exec(
            "git",
            "clone",
            "--filter=blob:none",
            *branch,
            _normalize_clone_url(url),
            tmp_dir,
            shell=False,
        )

    async def _fetch_commit_ref(
        self, *, session: "BaseSandboxSession", url: str, tmp_dir: str
    ) -> "ExecResult":
        # Pinned-SHA seed: same init/remote-add/fetch/checkout sequence as the
        # SDK's, but the fetch is blobless instead of ``--depth 1 --no-tags``.
        # ``git remote add`` already writes the wildcard refspec, so later
        # fetches see all branches either way; the filter keeps history whole.
        init = await session.exec("git", "init", tmp_dir, shell=False)
        if not init.ok():
            return init

        remote_add = await session.exec(
            "git", "-C", tmp_dir, "remote", "add", "origin", _normalize_clone_url(url), shell=False
        )
        if not remote_add.ok():
            return remote_add

        fetch = await session.exec(
            "git", "-C", tmp_dir, "fetch", "--filter=blob:none", "origin", self.ref, shell=False
        )
        if not fetch.ok():
            return fetch

        return await session.exec(
            "git", "-C", tmp_dir, "checkout", "--detach", "FETCH_HEAD", shell=False
        )
