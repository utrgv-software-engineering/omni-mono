"""Tests for ``AuthenticatedGitRepo``.

Focus on the credential-configuration behaviour this class adds on top of the
SDK's ``GitRepo`` (the actual clone is delegated to ``super().apply`` and
exercised by the SDK's own tests). Session calls are mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.sandbox.entries import GitRepo
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxSession
from omniagents.core.sandbox import AuthenticatedGitRepo
from omniagents.core.sandbox.authenticated_git_repo import _normalize_clone_url


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


def _entry() -> AuthenticatedGitRepo:
    return AuthenticatedGitRepo(
        host="github.com",
        repo="acme/private",
        ref="main",
        username="x-access-token",
        token_env="OMNI_GIT_TOKEN_TEST",
    )


@pytest.mark.asyncio
async def test_configures_credential_helper_when_token_present(monkeypatch):
    monkeypatch.setenv("OMNI_GIT_TOKEN_TEST", "ghp_secret/with+special")
    session = _session()

    await _entry().apply(session, Path("/workspace/svc"), Path("/tmp"))

    calls = _exec_calls(session)
    # The store helper is configured.
    assert ("git", "config", "--global", "credential.helper", "store") in calls
    # ~/.git-credentials is written in $HOME (not /workspace) with the token,
    # URL-encoded (so the slash/plus don't break the credential URL).
    cred_writes = [c for c in calls if c and ".git-credentials" in c[0]]
    assert len(cred_writes) == 1
    cmd = cred_writes[0][0]
    assert '"$HOME/.git-credentials"' in cmd
    assert "/workspace" not in cmd
    assert "ghp_secret%2Fwith%2Bspecial" in cmd  # percent-encoded token
    assert "ghp_secret/with+special" not in cmd  # raw token never written verbatim


@pytest.mark.asyncio
async def test_skips_credentials_when_token_missing(monkeypatch):
    monkeypatch.delenv("OMNI_GIT_TOKEN_TEST", raising=False)
    session = _session()

    await _entry().apply(session, Path("/workspace/svc"), Path("/tmp"))

    calls = _exec_calls(session)
    assert ("git", "config", "--global", "credential.helper", "store") not in calls
    assert not [c for c in calls if c and ".git-credentials" in c[0]]


@pytest.mark.asyncio
async def test_host_mode_does_not_touch_credentials(monkeypatch):
    monkeypatch.setenv("OMNI_GIT_TOKEN_TEST", "ghp_secret")
    session = _session(spec=UnixLocalSandboxSession)

    await _entry().apply(session, Path("/workspace/svc"), Path("/tmp"))

    calls = _exec_calls(session)
    # Host mode must not write ~/.git-credentials (would clobber the host user's).
    assert ("git", "config", "--global", "credential.helper", "store") not in calls
    assert not [c for c in calls if c and ".git-credentials" in c[0]]


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://dev.azure.com/org/proj/_git/repo.git", "https://dev.azure.com/org/proj/_git/repo"),
        ("https://org.visualstudio.com/proj/_git/repo.git", "https://org.visualstudio.com/proj/_git/repo"),
        ("https://github.com/owner/name.git", "https://github.com/owner/name.git"),
        ("https://gitlab.com/group/repo.git", "https://gitlab.com/group/repo.git"),
    ],
)
def test_normalize_clone_url(url, expected):
    assert _normalize_clone_url(url) == expected


def _https_args(session):
    return [a for c in session.exec.call_args_list for a in c.args if isinstance(a, str) and a.startswith("https://")]


@pytest.mark.asyncio
async def test_clone_strips_git_suffix_for_azure():
    session = _session()
    entry = AuthenticatedGitRepo(
        host="dev.azure.com", repo="org/proj/_git/repo", ref="main", username="pat", token_env="X"
    )
    await entry._clone_named_ref(session=session, url="https://dev.azure.com/org/proj/_git/repo.git", tmp_dir="/tmp/x")
    urls = _https_args(session)
    assert "https://dev.azure.com/org/proj/_git/repo" in urls
    assert all(not u.endswith(".git") for u in urls)


@pytest.mark.asyncio
async def test_clone_keeps_git_suffix_for_github():
    session = _session()
    entry = AuthenticatedGitRepo(
        host="github.com", repo="owner/name", ref="main", username="x-access-token", token_env="X"
    )
    await entry._clone_named_ref(session=session, url="https://github.com/owner/name.git", tmp_dir="/tmp/x")
    assert "https://github.com/owner/name.git" in _https_args(session)
