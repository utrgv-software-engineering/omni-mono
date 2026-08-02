"""End-to-end ``git_*`` RPC tests over the real /ws surface (tkt_l_l0lUi4A5V2).

Exercises the ticket's acceptance criteria on the wire, against real
temporary repositories:

* hunk-level stage/unstage/discard round trips through JSON-RPC frames,
* destructive actions answer ``-32093 git_confirmation_required`` and only
  run once an impact-bound token is echoed back,
* multi-repository projects address a repository unambiguously by id,
* conflicts and detached HEAD arrive as structure, never as prose,
* an operation that fails partway leaves no half-applied index,
* experimental gating (-32013), cross-user denial (-32014), and the
  progress notification staying out of the durable replay journal.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from omniagents.backends.server import app as server_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.execution import AgentHostResources, register_host_environment
from omniagents.rpc.auth import RPC_ERROR_AUTHORIZATION_DENIED
from tests.fixtures.gui_ws import perform_gui_handshake

from agents import Agent

GIT_EXPERIMENTAL = (
    "git_list_repositories",
    "git_status",
    "git_diff",
    "git_log",
    "git_list_branches",
    "git_list_worktrees",
    "git_conflicts",
    "git_stage",
    "git_unstage",
    "git_discard",
    "git_commit",
    "git_checkout",
    "git_reset",
    "git_fetch",
    "git_pull",
    "git_push",
    "git_operation_progress",
)

NUMBERED = "\n".join(f"line{index}" for index in range(1, 21)) + "\n"


def two_hunk_edit() -> str:
    lines = [f"line{index}" for index in range(1, 21)]
    lines[1] = "CHANGED-TOP"
    lines[17] = "CHANGED-BOTTOM"
    return "\n".join(lines) + "\n"


def git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main", ".")
    git(path, "config", "user.email", "tester@example.com")
    git(path, "config", "user.name", "Tester")
    git(path, "config", "commit.gpgsign", "false")
    return path


async def dummy_build_agent(settings, mcp, app):
    return Agent(name="git", tools=[], instructions="", model="gpt")


def _spec(security_config=None) -> AgentSpec:
    return AgentSpec(
        name="GitAgent",
        welcome_text="hi",
        get_agent_instructions=lambda: "instr",
        build_agent=dummy_build_agent,
        security_config=security_config,
    )


class Rpc:
    """Minimal JSON-RPC helper over a TestClient websocket."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.next_id = 0
        self.notifications: list = []

    def call(self, method, **params):
        self.next_id += 1
        call_id = f"c{self.next_id}"
        self.ws.send_json(
            {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}
        )
        while True:
            message = self.ws.receive_json()
            if message.get("id") == call_id and (
                "result" in message or "error" in message
            ):
                return message
            self.notifications.append(message)

    def result(self, method, **params):
        message = self.call(method, **params)
        assert "error" not in message, f"{method} failed: {message}"
        return message["result"]

    def error(self, method, **params):
        message = self.call(method, **params)
        assert "error" in message, f"{method} unexpectedly succeeded: {message}"
        return message["error"]

    def by_method(self, method):
        return [
            item["params"]
            for item in self.notifications
            if item.get("method") == method
        ]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def repo(workspace: Path) -> Path:
    target = init_repo(workspace / "app")
    (target / "f.txt").write_text(NUMBERED)
    git(target, "add", "-A")
    git(target, "commit", "-qm", "init")
    return target


@pytest.fixture
def client(workspace: Path) -> TestClient:
    resources = AgentHostResources.create("git-test-host")
    register_host_environment(
        resources,
        str(workspace),
        owner_user_id=None,
        workspace_id="git-workspace",
        environment_id="git-env",
    )
    return TestClient(
        server_app.build_app(spec=_spec(), agent_host_resources=resources)
    )


def _ensure_session(rpc: Rpc, session_id: str, workspace_root: Path):
    return rpc.result(
        "server_call",
        function="session.ensure",
        args={"session_id": session_id, "workspace_root": str(workspace_root)},
    )


@pytest.fixture
def session(client: TestClient, workspace: Path):
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=GIT_EXPERIMENTAL)
        rpc = Rpc(ws)
        _ensure_session(rpc, "git-sess", workspace)
        yield rpc


# ---------------------------------------------------------------------------
#  Capability gating
# ---------------------------------------------------------------------------


def test_git_methods_require_an_experimental_grant(client: TestClient, repo: Path):
    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws)
        rpc = Rpc(ws)
        error = rpc.error("git_status", environment_id="git-env", repo="app")
        assert error["code"] == -32013
        assert error["data"]["kind"] == "capability_not_negotiated"


# ---------------------------------------------------------------------------
#  Discovery and multi-repository addressing
# ---------------------------------------------------------------------------


def test_multi_repository_projects_are_addressed_by_id(
    client: TestClient, workspace: Path
):
    for name in ("apps/web", "services/api"):
        target = init_repo(workspace / name)
        (target / "README.md").write_text("hi\n")
        git(target, "add", "-A")
        git(target, "commit", "-qm", "init")
    (workspace / "apps/web/only-web.txt").write_text("x\n")

    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=GIT_EXPERIMENTAL)
        rpc = Rpc(ws)
        _ensure_session(rpc, "git-sess", workspace)

        listing = rpc.result("git_list_repositories", environment_id="git-env")
        assert [item["repo"] for item in listing["repositories"]] == [
            "apps/web",
            "services/api",
        ]

        web = rpc.result("git_status", environment_id="git-env", repo="apps/web")
        api = rpc.result("git_status", environment_id="git-env", repo="services/api")
        assert web["untracked"] == ["only-web.txt"]
        assert api["untracked"] == []

        # An unknown / non-repository id is a typed error, not a guess.
        error = rpc.error("git_status", environment_id="git-env", repo="apps")
        assert error["code"] == -32090
        assert error["data"]["kind"] == "git_repository_not_found"

        # Traversal out of the workspace never reaches git.
        escape = rpc.error("git_status", environment_id="git-env", repo="../elsewhere")
        assert escape["code"] == -32091
        assert escape["data"]["kind"] == "git_path_invalid"


# ---------------------------------------------------------------------------
#  Hunk-level workflow on the wire
# ---------------------------------------------------------------------------


def test_hunk_level_stage_unstage_and_commit_round_trip(session: Rpc, repo: Path):
    (repo / "f.txt").write_text(two_hunk_edit())

    diff = session.result("git_diff", environment_id="git-env", repo="app")
    (diff_file,) = diff["files"]
    assert diff_file["hunk_selectable"] is True
    hunks = diff_file["hunks"]
    assert len(hunks) == 2

    staged = session.result(
        "git_stage",
        environment_id="git-env",
        repo="app",
        hunks=[{"path": "f.txt", "hunk_id": hunks[0]["hunk_id"]}],
    )
    assert staged["staged_hunks"] == [{"path": "f.txt", "hunk_id": hunks[0]["hunk_id"]}]

    index_diff = session.result(
        "git_diff", environment_id="git-env", repo="app", mode="staged"
    )
    index_lines = [
        line["content"]
        for line in index_diff["files"][0]["hunks"][0]["lines"]
        if line["origin"] == "add"
    ]
    assert index_lines == ["CHANGED-TOP"]

    worktree_diff = session.result("git_diff", environment_id="git-env", repo="app")
    remaining = [
        line["content"]
        for line in worktree_diff["files"][0]["hunks"][0]["lines"]
        if line["origin"] == "add"
    ]
    assert remaining == ["CHANGED-BOTTOM"]

    commit = session.result(
        "git_commit", environment_id="git-env", repo="app", message="only the top hunk"
    )
    assert commit["oid"] == git(repo, "rev-parse", "HEAD").strip()
    log = session.result("git_log", environment_id="git-env", repo="app", max_count=1)
    assert log["commits"][0]["subject"] == "only the top hunk"

    # And the leftover hunk can be staged and unstaged again.
    leftover = session.result("git_diff", environment_id="git-env", repo="app")
    hunk_id = leftover["files"][0]["hunks"][0]["hunk_id"]
    session.result(
        "git_stage",
        environment_id="git-env",
        repo="app",
        hunks=[{"path": "f.txt", "hunk_id": hunk_id}],
    )
    staged_now = session.result(
        "git_diff", environment_id="git-env", repo="app", mode="staged"
    )
    session.result(
        "git_unstage",
        environment_id="git-env",
        repo="app",
        hunks=[
            {
                "path": "f.txt",
                "hunk_id": staged_now["files"][0]["hunks"][0]["hunk_id"],
            }
        ],
    )
    assert git(repo, "diff", "--cached").strip() == ""


def test_a_stale_hunk_selection_stages_nothing(session: Rpc, repo: Path):
    (repo / "f.txt").write_text(two_hunk_edit())
    diff = session.result("git_diff", environment_id="git-env", repo="app")
    stale = diff["files"][0]["hunks"][0]["hunk_id"]
    (repo / "f.txt").write_text(NUMBERED.replace("line2", "SOMETHING-ELSE"))

    error = session.error(
        "git_stage",
        environment_id="git-env",
        repo="app",
        hunks=[{"path": "f.txt", "hunk_id": stale}],
    )
    assert error["code"] == -32094
    assert error["data"]["reason"] == "hunk_not_found"
    assert error["data"]["stale"] == [{"path": "f.txt", "hunk_id": stale}]
    assert git(repo, "diff", "--cached").strip() == ""


# ---------------------------------------------------------------------------
#  Destructive-action policy
# ---------------------------------------------------------------------------


def test_destructive_actions_require_an_impact_bound_confirmation(
    session: Rpc, repo: Path
):
    (repo / "f.txt").write_text(two_hunk_edit())

    denied = session.error(
        "git_discard", environment_id="git-env", repo="app", paths=["f.txt"]
    )
    assert denied["code"] == -32093
    assert denied["data"]["kind"] == "git_confirmation_required"
    assert denied["data"]["operation"] == "discard_changes"
    assert denied["data"]["impact"]["paths"] == ["f.txt"]
    assert (repo / "f.txt").read_text() == two_hunk_edit()

    # A token minted for a different operation does not unlock this one.
    reset_denied = session.error(
        "git_reset", environment_id="git-env", repo="app", mode="hard"
    )
    cross = session.error(
        "git_discard",
        environment_id="git-env",
        repo="app",
        paths=["f.txt"],
        confirmation_token=reset_denied["data"]["confirmation_token"],
    )
    assert cross["code"] == -32093
    assert cross["data"]["reason"] == "stale_confirmation"
    assert (repo / "f.txt").read_text() == two_hunk_edit()

    # The right token, for the same impact, applies exactly once.
    token = cross["data"]["confirmation_token"]
    session.result(
        "git_discard",
        environment_id="git-env",
        repo="app",
        paths=["f.txt"],
        confirmation_token=token,
    )
    assert (repo / "f.txt").read_text() == NUMBERED

    replay = session.error(
        "git_discard",
        environment_id="git-env",
        repo="app",
        paths=["f.txt"],
        confirmation_token=token,
    )
    assert replay["code"] in (-32093, -32094)


def test_hard_reset_and_amend_are_gated(session: Rpc, repo: Path):
    (repo / "f.txt").write_text(two_hunk_edit())
    denied = session.error(
        "git_reset", environment_id="git-env", repo="app", mode="hard"
    )
    assert denied["data"]["operation"] == "reset_hard"
    assert denied["data"]["impact"]["dirty_paths"] == ["f.txt"]
    session.result(
        "git_reset",
        environment_id="git-env",
        repo="app",
        mode="hard",
        confirmation_token=denied["data"]["confirmation_token"],
    )
    assert (repo / "f.txt").read_text() == NUMBERED

    amend = session.error(
        "git_commit",
        environment_id="git-env",
        repo="app",
        message="reworded",
        amend=True,
    )
    assert amend["data"]["operation"] == "amend_commit"
    session.result(
        "git_commit",
        environment_id="git-env",
        repo="app",
        message="reworded",
        amend=True,
        confirmation_token=amend["data"]["confirmation_token"],
    )
    assert git(repo, "log", "-1", "--format=%s").strip() == "reworded"


def test_non_destructive_operations_need_no_confirmation(session: Rpc, repo: Path):
    (repo / "f.txt").write_text(two_hunk_edit())
    session.result("git_stage", environment_id="git-env", repo="app", paths=["f.txt"])
    status = session.result(
        "git_reset", environment_id="git-env", repo="app", mode="mixed"
    )
    assert status["entries"][0]["staged"] is False
    assert (repo / "f.txt").read_text() == two_hunk_edit()


# ---------------------------------------------------------------------------
#  Structural state: conflicts, detached HEAD, worktrees
# ---------------------------------------------------------------------------


def test_conflicts_and_detached_head_are_structural(session: Rpc, repo: Path):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "f.txt").write_text(NUMBERED.replace("line2", "FEATURE"))
    git(repo, "commit", "-qam", "feature")
    git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text(NUMBERED.replace("line2", "MAIN"))
    git(repo, "commit", "-qam", "main change")
    git(repo, "merge", "feature", check=False)

    status = session.result("git_status", environment_id="git-env", repo="app")
    assert status["state"] == "merging"
    assert status["conflicted"] == ["f.txt"]
    entry = next(item for item in status["entries"] if item["path"] == "f.txt")
    assert entry["xy"] == "UU"
    assert entry["unmerged"]["ours_oid"] != entry["unmerged"]["theirs_oid"]

    conflicts = session.result("git_conflicts", environment_id="git-env", repo="app")
    (conflict,) = conflicts["conflicts"]
    assert set(conflict["stages"]) == {"base", "ours", "theirs"}
    assert conflict["regions"][0]["ours"] == ["MAIN"]
    assert conflict["regions"][0]["theirs"] == ["FEATURE"]

    git(repo, "merge", "--abort")
    oid = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "--detach", oid)
    detached = session.result("git_status", environment_id="git-env", repo="app")
    assert detached["head"] == {
        "detached": True,
        "unborn": False,
        "branch": None,
        "oid": oid,
    }


def test_worktrees_outside_the_workspace_are_reported_but_not_operable(
    session: Rpc, repo: Path, workspace: Path, tmp_path: Path
):
    scratch = tmp_path / "scratch" / "worktrees" / "app-worker-1"
    git(
        repo, "worktree", "add", "-q", "-b", "worker/app-worker-1", str(scratch), "HEAD"
    )

    listing = session.result("git_list_worktrees", environment_id="git-env", repo="app")
    worker = next(item for item in listing["worktrees"] if item["path"] == str(scratch))
    assert worker["accessible"] is False
    assert worker["inaccessible_reason"] == "outside_workspace"
    assert worker["category"] == "worker"

    branches = session.result("git_list_branches", environment_id="git-env", repo="app")
    worker_branch = next(
        item for item in branches["branches"] if item["name"] == "worker/app-worker-1"
    )
    assert worker_branch["category"] == "worker"

    # The worker's own checkout cannot be addressed through this surface.
    error = session.error("git_status", environment_id="git-env", repo="scratch")
    assert error["code"] in (-32090, -32091)


def test_checkout_creates_and_switches_branches(session: Rpc, repo: Path):
    status = session.result(
        "git_checkout",
        environment_id="git-env",
        repo="app",
        branch="topic",
        create=True,
    )
    assert status["head"]["branch"] == "topic"
    back = session.result(
        "git_checkout", environment_id="git-env", repo="app", branch="main"
    )
    assert back["head"]["branch"] == "main"


# ---------------------------------------------------------------------------
#  Network operations, progress notifications and replay
# ---------------------------------------------------------------------------


def test_push_reports_progress_without_journaling_it(
    session: Rpc, repo: Path, workspace: Path
):
    remote = workspace / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))

    pushed = session.result(
        "git_push",
        environment_id="git-env",
        repo="app",
        remote="origin",
        refspec="main:main",
    )
    assert pushed["ok"] is True
    assert pushed["updates"][0]["status"] == "created"

    progress = session.by_method("git_operation_progress")
    assert [item["phase"] for item in progress] == ["started", "completed"]
    assert {item["operation"] for item in progress} == {"push"}

    # Progress is per-connection state: it never enters the replay journal.
    resumed = session.result("resume_session", session_id="git-sess")
    assert not [
        event
        for event in resumed["events"]
        if str(event.get("method", "")).startswith("git_")
    ]


def test_a_failing_git_command_answers_a_typed_error(session: Rpc, repo: Path):
    error = session.error(
        "git_checkout", environment_id="git-env", repo="app", branch="does-not-exist"
    )
    assert error["code"] == -32092
    assert error["data"]["kind"] == "git_operation_failed"
    assert error["data"]["operation"] == "checkout"
    assert error["data"]["exit_code"] != 0


# ---------------------------------------------------------------------------
#  Cross-user authorization
# ---------------------------------------------------------------------------

ALICE = {"Authorization": "Bearer alice-token"}
BOB = {"Authorization": "Bearer bob-token"}


@pytest.fixture
def authz(tmp_path: Path):
    root = tmp_path / "shared-workspace"
    root.mkdir()
    target = init_repo(root / "app")
    (target / "f.txt").write_text(NUMBERED)
    git(target, "add", "-A")
    git(target, "commit", "-qm", "init")
    (target / "f.txt").write_text(two_hunk_edit())

    spec = _spec(
        security_config={
            "providers": {
                "auth": {
                    "type": "token",
                    "tokens": {"alice-token": "alice", "bob-token": "bob"},
                }
            }
        }
    )
    resources = AgentHostResources.create("git-auth-test-host")
    for user in ("alice", "bob"):
        register_host_environment(
            resources,
            str(root),
            owner_user_id=user,
            workspace_id=f"{user}-workspace",
            environment_id=f"{user}-env",
        )
    app = server_app.build_app(spec=spec, agent_host_resources=resources)
    return SimpleNamespace(client=TestClient(app), workspace=root, repo=target)


def _connect(authz, headers):
    manager = authz.client.websocket_connect("/ws", headers=headers)
    ws = manager.__enter__()
    perform_gui_handshake(ws, experimental=GIT_EXPERIMENTAL)
    return manager, Rpc(ws)


def test_cross_user_git_access_is_denied(authz):
    alice_cm, alice = _connect(authz, ALICE)
    bob_cm, bob = _connect(authz, BOB)
    try:
        alice.result(
            "server_call",
            function="session.ensure",
            args={"session_id": "alice-git", "workspace_root": str(authz.workspace)},
        )
        diff = alice.result("git_diff", environment_id="alice-env", repo="app")
        hunk_id = diff["files"][0]["hunks"][0]["hunk_id"]

        for method, params in (
            ("git_list_repositories", {}),
            ("git_status", {"repo": "app"}),
            ("git_diff", {"repo": "app"}),
            ("git_log", {"repo": "app"}),
            ("git_list_branches", {"repo": "app"}),
            ("git_list_worktrees", {"repo": "app"}),
            ("git_conflicts", {"repo": "app"}),
            ("git_stage", {"repo": "app", "paths": ["f.txt"]}),
            (
                "git_unstage",
                {"repo": "app", "hunks": [{"path": "f.txt", "hunk_id": hunk_id}]},
            ),
            ("git_discard", {"repo": "app", "paths": ["f.txt"]}),
            ("git_commit", {"repo": "app", "message": "hijack"}),
            ("git_checkout", {"repo": "app", "branch": "main"}),
            ("git_reset", {"repo": "app", "mode": "hard"}),
            ("git_fetch", {"repo": "app"}),
            ("git_pull", {"repo": "app"}),
            ("git_push", {"repo": "app"}),
        ):
            error = bob.error(method, environment_id="alice-env", **params)
            assert error["code"] == RPC_ERROR_AUTHORIZATION_DENIED, (method, error)
            assert error["data"]["kind"] == "authorization_denied", (method, error)

        # Alice's repository is untouched by the refused calls.
        assert (authz.repo / "f.txt").read_text() == two_hunk_edit()
        assert git(authz.repo, "diff", "--cached").strip() == ""

        # And alice can still use her own session.
        status = alice.result("git_status", environment_id="alice-env", repo="app")
        assert status["repo"] == "app"
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


def test_a_confirmation_token_cannot_be_replayed_by_another_principal(authz):
    alice_cm, alice = _connect(authz, ALICE)
    bob_cm, bob = _connect(authz, BOB)
    try:
        for owner, rpc in (("alice-git", alice), ("bob-git", bob)):
            rpc.result(
                "server_call",
                function="session.ensure",
                args={"session_id": owner, "workspace_root": str(authz.workspace)},
            )
        denied = alice.error(
            "git_discard", environment_id="alice-env", repo="app", paths=["f.txt"]
        )
        token = denied["data"]["confirmation_token"]

        # Bob owns his own session but the token was minted for alice's.
        replay = bob.error(
            "git_discard",
            environment_id="bob-env",
            repo="app",
            paths=["f.txt"],
            confirmation_token=token,
        )
        assert replay["code"] == -32093
        assert replay["data"]["reason"] == "stale_confirmation"
        assert (authz.repo / "f.txt").read_text() == two_hunk_edit()
    finally:
        bob_cm.__exit__(None, None, None)
        alice_cm.__exit__(None, None, None)


# ---------------------------------------------------------------------------
#  Review round: rollback driven by a real git apply failure
# ---------------------------------------------------------------------------


def test_a_real_apply_failure_rolls_the_index_back(session: Rpc, repo: Path):
    """No mocks: git itself rejects the second step.

    ``paths`` are git pathspecs, so ``src/*.txt`` also covers the file the
    hunk selection targets. Step one stages it whole, which moves the
    index out from under the patch step two was built against, and
    ``git apply --cached`` refuses. The index must come back exactly as it
    was — not with step one's half of the work left behind.
    """
    (repo / "src").mkdir()
    (repo / "src" / "b.txt").write_text(NUMBERED)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add src")
    (repo / "src" / "b.txt").write_text(two_hunk_edit())
    (repo / "f.txt").write_text(two_hunk_edit())

    before = git(repo, "diff", "--cached", "--name-only")
    assert before.strip() == ""

    diff = session.result("git_diff", environment_id="git-env", repo="app")
    target = next(item for item in diff["files"] if item["path"] == "src/b.txt")
    error = session.error(
        "git_stage",
        environment_id="git-env",
        repo="app",
        paths=["src/*.txt"],
        hunks=[{"path": "src/b.txt", "hunk_id": target["hunks"][0]["hunk_id"]}],
    )
    assert error["code"] == -32092
    assert error["data"]["kind"] == "git_operation_failed"
    assert error["data"]["rolled_back"] is True

    # Neither half of the operation survived.
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""
    assert (repo / "src" / "b.txt").read_text() == two_hunk_edit()
    assert (repo / "f.txt").read_text() == two_hunk_edit()

    # And the repository is still perfectly usable afterwards.
    retry = session.result(
        "git_stage",
        environment_id="git-env",
        repo="app",
        hunks=[{"path": "src/b.txt", "hunk_id": target["hunks"][0]["hunk_id"]}],
    )
    assert retry["staged_hunks"]
    assert git(repo, "diff", "--cached", "--name-only").strip() == "src/b.txt"


def test_a_large_patch_stages_over_the_wire(session: Rpc, repo: Path):
    """Regression for the 128 KiB argv limit, exercised through /ws."""
    (repo / "big.txt").write_text(
        "".join(f"original line {index}\n" for index in range(20000))
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "big")
    (repo / "big.txt").write_text(
        "".join(f"rewritten line {index}\n" for index in range(20000))
    )

    diff = session.result(
        "git_diff", environment_id="git-env", repo="app", context_lines=0
    )
    (diff_file,) = diff["files"]
    assert len(diff_file["hunks"]) == 1
    assert len(diff_file["hunks"][0]["lines"]) == 40000
    staged = session.result(
        "git_stage",
        environment_id="git-env",
        repo="app",
        context_lines=0,
        hunks=[{"path": "big.txt", "hunk_id": diff_file["hunks"][0]["hunk_id"]}],
    )
    assert staged["staged_hunks"]
    assert "rewritten line 0" in git(repo, "diff", "--cached")


# ---------------------------------------------------------------------------
#  Review round: destructive intent hidden in the refspec
# ---------------------------------------------------------------------------


def test_destructive_refspecs_are_gated_over_the_wire(
    session: Rpc, repo: Path, workspace: Path
):
    remote = workspace / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    git(repo, "remote", "add", "origin", str(remote))
    session.result(
        "git_push",
        environment_id="git-env",
        repo="app",
        remote="origin",
        refspec="main:refs/heads/release",
    )
    assert git(remote, "rev-parse", "refs/heads/release").strip()

    deleting = session.error(
        "git_push",
        environment_id="git-env",
        repo="app",
        remote="origin",
        refspec=":refs/heads/release",
    )
    assert deleting["code"] == -32093
    assert deleting["data"]["operation"] == "delete_remote_ref"
    assert git(remote, "rev-parse", "refs/heads/release").strip()

    forcing = session.error(
        "git_push",
        environment_id="git-env",
        repo="app",
        remote="origin",
        refspec="+main:refs/heads/release",
    )
    assert forcing["code"] == -32093
    assert forcing["data"]["operation"] == "force_push"


def test_a_remote_url_is_refused_over_the_wire(
    session: Rpc, repo: Path, tmp_path: Path
):
    outside = init_repo(tmp_path / "outside")
    (outside / "secret.txt").write_text("TOP SECRET\n")
    git(outside, "add", "-A")
    git(outside, "commit", "-qm", "outside")

    error = session.error(
        "git_fetch", environment_id="git-env", repo="app", remote=str(outside)
    )
    assert error["code"] == -32094
    assert error["data"]["reason"] == "unknown_remote"
    assert "TOP SECRET" not in git(repo, "log", "--all", "-p")


# ---------------------------------------------------------------------------
#  Review round: the git-dir escape
# ---------------------------------------------------------------------------


def test_a_git_file_escape_is_refused_over_the_wire(
    client: TestClient, workspace: Path, tmp_path: Path
):
    outside = init_repo(tmp_path / "outside")
    (outside / "secret.txt").write_text("TOP SECRET\n")
    git(outside, "add", "-A")
    git(outside, "commit", "-qm", "outside")
    head = git(outside, "rev-parse", "HEAD").strip()

    (workspace / "sneaky").mkdir()
    (workspace / "sneaky" / ".git").write_text(f"gitdir: {outside}/.git\n")

    with client.websocket_connect("/ws") as ws:
        perform_gui_handshake(ws, experimental=GIT_EXPERIMENTAL)
        rpc = Rpc(ws)
        _ensure_session(rpc, "git-sess", workspace)
        for method, params in (
            ("git_status", {"repo": "sneaky"}),
            ("git_log", {"repo": "sneaky"}),
            ("git_diff", {"repo": "sneaky"}),
            ("git_commit", {"repo": "sneaky", "message": "hijack"}),
        ):
            error = rpc.error(method, environment_id="git-env", **params)
            assert error["code"] == -32090, (method, error)
            assert error["data"]["reason"] == "outside_workspace", (method, error)
        listing = rpc.result("git_list_repositories", environment_id="git-env")
        assert listing["repositories"] == []

    assert git(outside, "rev-parse", "HEAD").strip() == head
    assert not (workspace / "sneaky" / "secret.txt").exists()


# ---------------------------------------------------------------------------
#  Review round: serialization and head-scope staging over the wire
# ---------------------------------------------------------------------------


def test_conflict_stage_contents_reach_the_client(session: Rpc, repo: Path):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "f.txt").write_text(NUMBERED.replace("line2", "FEATURE"))
    git(repo, "commit", "-qam", "feature")
    git(repo, "checkout", "-q", "main")
    (repo / "f.txt").write_text(NUMBERED.replace("line2", "MAIN"))
    git(repo, "commit", "-qam", "main change")
    git(repo, "merge", "feature", check=False)

    conflicts = session.result("git_conflicts", environment_id="git-env", repo="app")
    (conflict,) = conflicts["conflicts"]
    # Base is absent from the working file under the default merge style;
    # the stage blob supplies it.
    assert conflict["regions"][0]["base"] is None
    assert "line2" in conflict["stages"]["base"]["content"]
    assert "MAIN" in conflict["stages"]["ours"]["content"]
    assert "FEATURE" in conflict["stages"]["theirs"]["content"]


def test_head_scope_hunks_are_stageable_over_the_wire(session: Rpc, repo: Path):
    (repo / "f.txt").write_text(two_hunk_edit())
    head_diff = session.result(
        "git_diff", environment_id="git-env", repo="app", mode="head"
    )
    hunk_id = head_diff["files"][0]["hunks"][0]["hunk_id"]

    mismatched = session.error(
        "git_stage",
        environment_id="git-env",
        repo="app",
        hunks=[{"path": "f.txt", "hunk_id": hunk_id}],
    )
    assert mismatched["data"]["reason"] == "hunk_not_found"
    assert mismatched["data"]["scope"] == "worktree"

    session.result(
        "git_stage",
        environment_id="git-env",
        repo="app",
        mode="head",
        hunks=[{"path": "f.txt", "hunk_id": hunk_id}],
    )
    staged = git(repo, "diff", "--cached")
    assert "CHANGED-TOP" in staged and "CHANGED-BOTTOM" not in staged
