"""``git_*`` against a real Docker sandbox workspace (tkt_l_l0lUi4A5V2).

Every other test in this suite drives :class:`HostWorkspace`. This one
runs the same manager against a :class:`SandboxWorkspace` backed by an
actual container, because three things in the implementation are
substrate-dependent and cannot be proven on the host:

* the scratch patch file is written with ``Workspace.write_text`` inside
  the repository's git directory, which on a sandbox goes through the
  session's path policy rather than the host filesystem;
* commands run as argv with ``shell=False`` and no ``cwd`` (the sandbox
  workspace refuses ``cwd`` in that mode);
* discovery walks ``list_dir``. Its ``FileEntry`` fields used **not** to
  mean the same thing on both substrates — the sandbox passed the SDK's
  absolute container path through for both ``name`` and ``path``, and a
  mis-typed entry surfaced the container's own ``ls`` failure rather than
  an ``OSError``. Both were real bugs this test found; both are now fixed
  in the workspace layer itself (tkt_z8T1amZ-GUs0), and this module's
  discovery assertions are what proves the layer fix rather than a
  git-side workaround — ``git.py`` reads ``entry.name``/``entry.path``
  directly again.

Skipped unless Docker is reachable and the image is already present, so
it never pulls itself — CI provisions the image in the
``sandbox-substrate`` job and sets ``OMNIAGENTS_REQUIRE_SANDBOX_IMAGE``,
which turns the skip into a hard failure there (tkt_k5Yk_JBkrIw6).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from omniagents.rpc.git import GitConfirmationRequired, GitManager
from omniagents.workspace.sandbox import SandboxWorkspace
from tests.integration._sandbox_image import image_available

IMAGE = "alpine/git:latest"

NUMBERED = "\n".join(f"line{index}" for index in range(1, 21)) + "\n"


def two_hunk_edit() -> str:
    lines = [f"line{index}" for index in range(1, 21)]
    lines[1] = "CHANGED-TOP"
    lines[17] = "CHANGED-BOTTOM"
    return "\n".join(lines) + "\n"


pytestmark = pytest.mark.skipif(
    not image_available(IMAGE),
    reason=f"docker image {IMAGE} is not available locally",
)


@pytest_asyncio.fixture
async def sandbox_workspace():
    from omniagents.core.sandbox import build_sandbox_client

    client, options = build_sandbox_client(
        {"client": {"type": "docker"}, "options": {"image": IMAGE}}
    )
    session = await client.create(options=options)
    try:
        yield SandboxWorkspace(session=session, root="/workspace")
    finally:
        try:
            await client.delete(session)
        except Exception:  # pragma: no cover - teardown is best effort
            pass


@pytest_asyncio.fixture
async def sandbox_repo(sandbox_workspace):
    result = await sandbox_workspace.exec(
        "mkdir -p /workspace/app && cd /workspace/app && git init -q -b main . && "
        "git config user.email tester@example.com && git config user.name Tester && "
        "git config commit.gpgsign false",
        shell=True,
        timeout=60,
    )
    assert result.exit_code == 0, result.stderr
    await sandbox_workspace.write_text("app/f.txt", NUMBERED)
    result = await sandbox_workspace.exec(
        "cd /workspace/app && git add -A && git commit -qm init", shell=True, timeout=60
    )
    assert result.exit_code == 0, result.stderr
    return sandbox_workspace


def _manager(workspace) -> GitManager:
    async def resolver(session_id: str):
        return workspace

    return GitManager(workspace_resolver=resolver)


@pytest.mark.asyncio
async def test_hunk_staging_works_inside_a_container(sandbox_repo):
    """The whole hunk workflow, with the patch file written in-container."""
    workspace = sandbox_repo
    manager = _manager(workspace)
    await workspace.write_text("app/f.txt", two_hunk_edit())

    listing = await manager.list_repositories("s1")
    assert [item["repo"] for item in listing["repositories"]] == ["app"]

    status = await manager.status("s1", "app")
    assert status["head"]["branch"] == "main"
    assert [entry["path"] for entry in status["entries"]] == ["f.txt"]

    diff = await manager.diff("s1", "app")
    hunks = diff["files"][0]["hunks"]
    assert len(hunks) == 2
    assert diff["files"][0]["hunk_selectable"] is True

    await manager.stage(
        "s1", "app", hunks=[{"path": "f.txt", "hunk_id": hunks[1]["hunk_id"]}]
    )
    cached = await workspace.exec(
        "cd /workspace/app && git diff --cached", shell=True, timeout=30
    )
    assert "CHANGED-BOTTOM" in cached.stdout
    assert "CHANGED-TOP" not in cached.stdout

    # The scratch patch is cleaned up and never becomes a workspace change.
    leftovers = await workspace.exec(
        "ls /workspace/app/.git/omniagents-apply-* 2>/dev/null | wc -l",
        shell=True,
        timeout=30,
    )
    assert leftovers.stdout.strip() == "0"
    assert (await manager.status("s1", "app", include_untracked=True))[
        "untracked"
    ] == []


@pytest.mark.asyncio
async def test_a_patch_over_the_argv_limit_applies_inside_a_container(sandbox_repo):
    """The 128 KiB regression, across the sandbox transport."""
    workspace = sandbox_repo
    manager = _manager(workspace)
    await workspace.write_text(
        "app/big.txt", "".join(f"original line {index}\n" for index in range(20000))
    )
    result = await workspace.exec(
        "cd /workspace/app && git add -A && git commit -qm big", shell=True, timeout=120
    )
    assert result.exit_code == 0, result.stderr
    await workspace.write_text(
        "app/big.txt", "".join(f"rewritten line {index}\n" for index in range(20000))
    )

    diff = await manager.diff("s1", "app", paths=["big.txt"], context_lines=0)
    hunks = diff["files"][0]["hunks"]
    assert len(hunks) == 1
    await manager.stage(
        "s1",
        "app",
        context_lines=0,
        hunks=[{"path": "big.txt", "hunk_id": hunks[0]["hunk_id"]}],
    )
    cached = await workspace.exec(
        "cd /workspace/app && git diff --cached -- big.txt", shell=True, timeout=60
    )
    assert "rewritten line 0" in cached.stdout


@pytest.mark.asyncio
async def test_commit_and_confirmed_discard_inside_a_container(sandbox_repo):
    workspace = sandbox_repo
    manager = _manager(workspace)
    await workspace.write_text("app/f.txt", two_hunk_edit())
    await manager.stage("s1", "app", paths=["f.txt"])
    committed = await manager.commit("s1", "app", "from the sandbox")
    head = await workspace.exec(
        "cd /workspace/app && git rev-parse HEAD", shell=True, timeout=30
    )
    assert committed["oid"] == head.stdout.strip()

    await workspace.write_text("app/f.txt", two_hunk_edit() + "TRAILING-EDIT\n")
    diff = await manager.diff("s1", "app", paths=["f.txt"])
    target = next(
        hunk
        for hunk in diff["files"][0]["hunks"]
        if any(
            line["origin"] == "add" and "TRAILING-EDIT" in line["content"]
            for line in hunk["lines"]
        )
    )
    with pytest.raises(GitConfirmationRequired) as excinfo:
        await manager.discard(
            "s1", "app", hunks=[{"path": "f.txt", "hunk_id": target["hunk_id"]}]
        )
    await manager.discard(
        "s1",
        "app",
        hunks=[{"path": "f.txt", "hunk_id": target["hunk_id"]}],
        confirmation_token=excinfo.value.data["confirmation_token"],
    )
    assert "TRAILING-EDIT" not in await workspace.read_text("app/f.txt")
