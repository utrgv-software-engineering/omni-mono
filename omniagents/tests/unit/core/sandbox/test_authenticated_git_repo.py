"""Tests for ``AuthenticatedGitRepo`` and the shared boot-time credential setup.

Two concerns live here:

* ``AuthenticatedGitRepo.apply`` — clone-time *git* auth only (credential helper
  so a private clone succeeds). It must NOT log CLIs in; that's the boot pass.
* ``configure_host_credentials`` — the boot pass omni serve runs for every linked
  host the project uses: git helper + ``gh`` / ``az devops`` login.

Session calls are mocked; the actual clone is delegated to the SDK ``GitRepo``
and stubbed.
"""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "UnixLocalSandbox is POSIX-only; Windows hosts use docker/wasm sandboxes or host mode",
        allow_module_level=True,
    )

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.sandbox.entries import GitRepo
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxSession
from omniagents.core.sandbox import AuthenticatedGitRepo, configure_host_credentials
from omniagents.core.sandbox.authenticated_git_repo import _azure_devops_org_url
from omniagents.core.sandbox.partial_clone_git_repo import _normalize_clone_url


@pytest.fixture(autouse=True)
def stub_base_clone(monkeypatch):
    """Isolate the credential-config behaviour: the actual clone is the SDK
    ``GitRepo.apply``'s job (and its own tests'), so stub it to a no-op."""
    monkeypatch.setattr(GitRepo, "apply", AsyncMock(return_value=[]))


def _ok() -> MagicMock:
    r = MagicMock()
    r.ok.return_value = True
    r.stderr = b""
    r.stdout = b""
    return r


def _session(spec: type | None = None) -> MagicMock:
    session = MagicMock(spec=spec) if spec else MagicMock()
    session.mkdir = AsyncMock(return_value=None)
    session.write = AsyncMock(return_value=None)
    session.exec = AsyncMock(return_value=_ok())
    return session


def _exec_calls(session: MagicMock) -> list[tuple]:
    return [tuple(str(a) for a in c.args) for c in session.exec.call_args_list]


def _exec_router(gh_present: bool = True, az_present: bool = True):
    """``session.exec`` side-effect: every command succeeds, except the
    ``command -v <cli>`` probes report presence per ``gh_present`` /
    ``az_present`` (so the CLI-not-installed branches are testable)."""

    async def _exec(*command, **_kwargs):
        cmd = str(command[0]) if command else ""
        r = MagicMock()
        r.stderr = b""
        r.stdout = b""
        if "command -v gh" in cmd:
            r.ok.return_value = gh_present
        elif "command -v az" in cmd:
            r.ok.return_value = az_present
        else:
            r.ok.return_value = True
        return r

    return _exec


def _git_cred_writes(session: MagicMock) -> list[str]:
    return [c[0] for c in _exec_calls(session) if c and ".git-credentials" in c[0]]


def _gh_login_calls(session: MagicMock) -> list[str]:
    return [c[0] for c in _exec_calls(session) if c and "gh auth login" in c[0]]


def _az_login_calls(session: MagicMock) -> list[str]:
    return [c[0] for c in _exec_calls(session) if c and "az devops login" in c[0]]


def _configured_git_helper(session: MagicMock) -> bool:
    # The git helper + credentials write are one flock-guarded shell command now.
    return any(
        "git config --global credential.helper store" in c
        for c in _git_cred_writes(session)
    )


def _entry() -> AuthenticatedGitRepo:
    return AuthenticatedGitRepo(
        host="github.com",
        repo="acme/private",
        ref="main",
        username="x-access-token",
        token_env="OMNI_GIT_TOKEN_TEST",
    )


# ── AuthenticatedGitRepo.apply — clone-time git auth only ────────────────────


@pytest.mark.asyncio
async def test_apply_configures_credential_helper_when_token_present(monkeypatch):
    monkeypatch.setenv("OMNI_GIT_TOKEN_TEST", "ghp_secret/with+special")
    session = _session()

    await _entry().apply(session, Path("/workspace/svc"), Path("/tmp"))

    # The store helper + ~/.git-credentials write are one shell command.
    cred_writes = _git_cred_writes(session)
    assert len(cred_writes) == 1
    cmd = cred_writes[0]
    assert _configured_git_helper(session)
    assert '"$HOME/.git-credentials"' in cmd
    assert "/workspace" not in cmd
    assert "ghp_secret%2Fwith%2Bspecial" in cmd  # percent-encoded token
    assert "ghp_secret/with+special" not in cmd  # raw token never written verbatim
    # apply() must NOT log the CLI in — that's the boot pass's job.
    assert _gh_login_calls(session) == []


@pytest.mark.asyncio
async def test_apply_skips_credentials_when_token_missing(monkeypatch):
    monkeypatch.delenv("OMNI_GIT_TOKEN_TEST", raising=False)
    session = _session()

    await _entry().apply(session, Path("/workspace/svc"), Path("/tmp"))

    assert not _configured_git_helper(session)
    assert _git_cred_writes(session) == []


@pytest.mark.asyncio
async def test_apply_host_mode_does_not_touch_credentials(monkeypatch):
    monkeypatch.setenv("OMNI_GIT_TOKEN_TEST", "ghp_secret")
    session = _session(spec=UnixLocalSandboxSession)

    await _entry().apply(session, Path("/workspace/svc"), Path("/tmp"))

    # Host mode must not write ~/.git-credentials (would clobber the host user's).
    assert _git_cred_writes(session) == []


# ── configure_host_credentials — boot pass (git helper + CLI login) ──────────


@pytest.mark.asyncio
async def test_boot_github_configures_git_and_gh():
    session = _session()
    session.exec = AsyncMock(side_effect=_exec_router(gh_present=True))

    await configure_host_credentials(
        session,
        host="github.com",
        repo="acme/private",
        username="x-access-token",
        token="ghp_secret",
    )

    assert _configured_git_helper(session)
    assert len(_git_cred_writes(session)) == 1
    gh = _gh_login_calls(session)
    assert len(gh) == 1
    assert "--with-token" in gh[0]
    assert "--hostname github.com" in gh[0]
    assert "printf '%s' ghp_secret |" in gh[0]  # token piped via stdin
    assert _az_login_calls(session) == []


@pytest.mark.asyncio
async def test_boot_github_skips_gh_when_not_installed():
    session = _session()
    session.exec = AsyncMock(side_effect=_exec_router(gh_present=False))

    await configure_host_credentials(
        session,
        host="github.com",
        repo="acme/private",
        username="x-access-token",
        token="ghp_secret",
    )

    # gh missing → no login, but git credentials are still configured.
    assert _gh_login_calls(session) == []
    assert _configured_git_helper(session)


@pytest.mark.asyncio
async def test_boot_azure_devops_logs_in_per_org():
    session = _session()
    session.exec = AsyncMock(side_effect=_exec_router(az_present=True))

    await configure_host_credentials(
        session,
        host="dev.azure.com",
        repo="myorg/proj/_git/repo",
        username="pat",
        token="azpat",
    )

    az = _az_login_calls(session)
    assert len(az) == 1
    assert "az devops login --organization https://dev.azure.com/myorg" in az[0]
    assert "printf '%s' azpat |" in az[0]  # PAT piped via stdin
    # ADO host uses az, never gh; git helper still written.
    assert _gh_login_calls(session) == []
    assert len(_git_cred_writes(session)) == 1


@pytest.mark.asyncio
async def test_boot_azure_devops_visualstudio_host():
    session = _session()
    session.exec = AsyncMock(side_effect=_exec_router(az_present=True))

    await configure_host_credentials(
        session,
        host="myorg.visualstudio.com",
        repo="proj/_git/repo",
        username="pat",
        token="azpat",
    )

    az = _az_login_calls(session)
    assert len(az) == 1
    assert "--organization https://myorg.visualstudio.com" in az[0]


@pytest.mark.asyncio
async def test_boot_azure_devops_skips_when_az_not_installed():
    session = _session()
    session.exec = AsyncMock(side_effect=_exec_router(az_present=False))

    await configure_host_credentials(
        session,
        host="dev.azure.com",
        repo="myorg/proj/_git/repo",
        username="pat",
        token="azpat",
    )

    assert _az_login_calls(session) == []
    assert _configured_git_helper(session)


@pytest.mark.asyncio
async def test_git_credentials_write_is_race_safe():
    # Concurrent materialization of multiple git-remote sources writes the same
    # ~/.gitconfig and ~/.git-credentials: serialize with flock and use a unique
    # temp file (never a shared .git-credentials.tmp that another mv could stat-race).
    session = _session()

    await configure_host_credentials(
        session,
        host="github.com",
        repo="acme/private",
        username="x-access-token",
        token="t",
    )

    cmd = _git_cred_writes(session)[0]
    assert "flock" in cmd
    assert "mktemp" in cmd
    assert 'mv "$t" "$f"' in cmd
    assert ".git-credentials.tmp" not in cmd  # the old shared-temp bug


@pytest.mark.asyncio
async def test_boot_runs_as_given_user():
    # Creds must be written as the agent's run_as identity (e.g. uid 1000 on a
    # devbox); written as root they'd be invisible to gh/git at runtime.
    session = _session()
    session.exec = AsyncMock(side_effect=_exec_router(gh_present=True))

    await configure_host_credentials(
        session,
        host="github.com",
        repo="acme/private",
        username="x-access-token",
        token="ghp_secret",
        user="1000:1000",
    )

    assert session.exec.call_args_list
    assert all(c.kwargs.get("user") == "1000:1000" for c in session.exec.call_args_list)


@pytest.mark.asyncio
async def test_boot_host_mode_does_nothing():
    session = _session(spec=UnixLocalSandboxSession)
    session.exec = AsyncMock(side_effect=_exec_router())

    await configure_host_credentials(
        session,
        host="github.com",
        repo="acme/private",
        username="x-access-token",
        token="ghp_secret",
    )

    # Host mode: leave the real user's git/CLI auth untouched.
    assert _exec_calls(session) == []


# ── pure helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "host,repo,expected",
    [
        ("dev.azure.com", "myorg/proj/_git/repo", "https://dev.azure.com/myorg"),
        ("myorg.visualstudio.com", "proj/_git/repo", "https://myorg.visualstudio.com"),
        ("dev.azure.com", "", None),
    ],
)
def test_azure_devops_org_url(host, repo, expected):
    assert _azure_devops_org_url(host, repo) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://dev.azure.com/org/proj/_git/repo.git",
            "https://dev.azure.com/org/proj/_git/repo",
        ),
        (
            "https://org.visualstudio.com/proj/_git/repo.git",
            "https://org.visualstudio.com/proj/_git/repo",
        ),
        ("https://github.com/owner/name.git", "https://github.com/owner/name.git"),
        ("https://gitlab.com/group/repo.git", "https://gitlab.com/group/repo.git"),
    ],
)
def test_normalize_clone_url(url, expected):
    assert _normalize_clone_url(url) == expected


def _https_args(session):
    return [
        a
        for c in session.exec.call_args_list
        for a in c.args
        if isinstance(a, str) and a.startswith("https://")
    ]


@pytest.mark.asyncio
async def test_clone_strips_git_suffix_for_azure():
    session = _session()
    entry = AuthenticatedGitRepo(
        host="dev.azure.com",
        repo="org/proj/_git/repo",
        ref="main",
        username="pat",
        token_env="X",
    )
    await entry._clone_named_ref(
        session=session,
        url="https://dev.azure.com/org/proj/_git/repo.git",
        tmp_dir="/tmp/x",
    )
    urls = _https_args(session)
    assert "https://dev.azure.com/org/proj/_git/repo" in urls
    assert all(not u.endswith(".git") for u in urls)


@pytest.mark.asyncio
async def test_clone_keeps_git_suffix_for_github():
    session = _session()
    entry = AuthenticatedGitRepo(
        host="github.com",
        repo="owner/name",
        ref="main",
        username="x-access-token",
        token_env="X",
    )
    await entry._clone_named_ref(
        session=session, url="https://github.com/owner/name.git", tmp_dir="/tmp/x"
    )
    assert "https://github.com/owner/name.git" in _https_args(session)


@pytest.mark.parametrize("blank_ref", ["HEAD", ""])
@pytest.mark.asyncio
async def test_clone_omits_branch_for_blank_ref(blank_ref):
    # A source added with a blank branch in the UI arrives here as the "HEAD"
    # sentinel (serve_cli's ``src.get("ref") or "HEAD"``). "HEAD" is not a real
    # branch, so ``git clone --branch HEAD`` fails — we must do a plain clone
    # that checks out the remote's default branch instead.
    session = _session()
    entry = AuthenticatedGitRepo(
        host="github.com",
        repo="owner/name",
        ref=blank_ref,
        username="x-access-token",
        token_env="X",
    )
    await entry._clone_named_ref(
        session=session, url="https://github.com/owner/name.git", tmp_dir="/tmp/x"
    )
    calls = _exec_calls(session)
    assert len(calls) == 1
    args = calls[0]
    assert "clone" in args
    assert "--branch" not in args  # the whole point: no --branch HEAD
    assert "HEAD" not in args
    assert "https://github.com/owner/name.git" in args


@pytest.mark.asyncio
async def test_clone_uses_branch_for_explicit_ref():
    # A real branch name clones with --branch, but NOT single-branch/shallow.
    session = _session()
    entry = AuthenticatedGitRepo(
        host="github.com",
        repo="owner/name",
        ref="dev",
        username="x-access-token",
        token_env="X",
    )
    await entry._clone_named_ref(
        session=session, url="https://github.com/owner/name.git", tmp_dir="/tmp/x"
    )
    args = _exec_calls(session)[0]
    assert "--branch" in args
    assert "dev" in args


@pytest.mark.parametrize("ref", ["HEAD", "main"])
@pytest.mark.asyncio
async def test_clone_is_blobless_never_shallow(ref):
    # Shallow (--depth) implies --single-branch, which narrows the fetch
    # refspec so `git fetch` in the workspace never sees other remote branches,
    # and --no-tags persists as tagOpt. The seed must be a blobless partial
    # clone instead: full history, all refs, normal refspec.
    session = _session()
    entry = AuthenticatedGitRepo(
        host="github.com",
        repo="owner/name",
        ref=ref,
        username="x-access-token",
        token_env="X",
    )
    await entry._clone_named_ref(
        session=session, url="https://github.com/owner/name.git", tmp_dir="/tmp/x"
    )
    args = _exec_calls(session)[0]
    assert "--filter=blob:none" in args
    assert "--depth" not in args
    assert "--single-branch" not in args
    assert "--no-tags" not in args


@pytest.mark.asyncio
async def test_fetch_commit_ref_is_blobless_never_shallow():
    session = _session()
    entry = AuthenticatedGitRepo(
        host="github.com",
        repo="owner/name",
        ref="a" * 40,
        username="x-access-token",
        token_env="X",
    )
    await entry._fetch_commit_ref(
        session=session, url="https://github.com/owner/name.git", tmp_dir="/tmp/x"
    )
    fetch_calls = [c for c in _exec_calls(session) if "fetch" in c]
    assert len(fetch_calls) == 1
    assert "--filter=blob:none" in fetch_calls[0]
    assert "--depth" not in fetch_calls[0]
    assert "--no-tags" not in fetch_calls[0]
