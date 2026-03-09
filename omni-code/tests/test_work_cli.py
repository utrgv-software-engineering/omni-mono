import json
from pathlib import Path

import pytest


def test_work_status_json_when_container_missing(monkeypatch, tmp_path, capsys):
    from omni_code import work_cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(work_cli, "_inspect_container", lambda _name: None)

    work_cli.main(["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["exists"] is False
    assert data["running"] is False


def test_work_ssh_starts_container_and_execs(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()

    start_calls = []
    exec_calls = []

    monkeypatch.setattr(work_cli, "_ensure_started", lambda workspace, rebuild=False: start_calls.append((workspace, rebuild)) or "omni-work-test")
    monkeypatch.setattr(work_cli, "_exec_shell", lambda container_name, shell: exec_calls.append((container_name, shell)) or 0)

    with pytest.raises(SystemExit) as exc:
        work_cli.main(["ssh", "--project", str(workspace), "--shell", "/bin/zsh"])

    assert exc.value.code == 0
    assert start_calls == [(workspace.resolve(), False)]
    assert exec_calls == [("omni-work-test", "/bin/zsh")]


def test_work_start_and_stop(monkeypatch, tmp_path, capsys):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()

    stop_calls = []
    monkeypatch.setattr(work_cli, "_ensure_started", lambda workspace, rebuild=False: "omni-work-test")
    monkeypatch.setattr(work_cli.sandbox_cli, "_stop_container", lambda name: stop_calls.append(name))

    work_cli.main(["start", "--project", str(workspace)])
    assert "Container: omni-work-test" in capsys.readouterr().out

    work_cli.main(["stop", "--project", str(workspace)])
    assert stop_calls == [work_cli._container_name_for_workspace(workspace.resolve())]


def test_work_resolves_registered_project(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()

    class Project:
        workspace_dir = str(workspace.resolve())

    class Runtime:
        def resolve_project(self, value):
            return Project() if value == "demo" else None

    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())

    assert work_cli._resolve_workspace("demo") == workspace.resolve()


def test_work_resolves_ticket_workspace(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()

    class Ticket:
        project_id = "p1"

    class Project:
        workspace_dir = str(workspace.resolve())

    class Runtime:
        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket() if ticket_ref == "t1" else None

        def resolve_project(self, project_ref):
            return Project() if project_ref == "p1" else None

    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())

    assert work_cli._resolve_ticket_workspace("t1") == workspace.resolve()


def test_work_resolves_ticket_worktree_workspace(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()

    class Ticket:
        project_id = "p1"
        worktree_path = str(worktree.resolve())

    class Runtime:
        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket() if ticket_ref == "t1" else None

        def resolve_project(self, project_ref):
            raise AssertionError("project fallback should not be used when worktree exists")

    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())

    assert work_cli._resolve_ticket_workspace("t1") == worktree.resolve()


def test_work_creates_ticket_worktree_when_requested(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()
    created_worktree = tmp_path / "created-wt"
    created_worktree.mkdir()
    updates = []

    class Ticket:
        id = "t1"
        project_id = "p1"
        branch = "main"
        use_worktree = True
        worktree_path = None

    class Project:
        id = "p1"
        workspace_dir = str(workspace.resolve())

    class Runtime:
        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket() if ticket_ref == "t1" else None

        def resolve_project(self, project_ref):
            return Project() if project_ref == "p1" else None

        def update_ticket_fields(self, ticket_ref, patch, project_ref=None):
            updates.append((ticket_ref, patch, project_ref))
            return Ticket()

    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())
    monkeypatch.setattr(work_cli, "_generate_worktree_name", lambda: "work-demo")
    monkeypatch.setattr(work_cli, "_create_worktree", lambda workspace, branch, name: created_worktree.resolve())

    assert work_cli._resolve_ticket_workspace("t1") == created_worktree.resolve()
    assert updates == [("t1", {"worktreePath": str(created_worktree.resolve()), "worktreeName": "work-demo"}, "p1")]


def test_work_ssh_supports_ticket(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()

    start_calls = []
    exec_calls = []

    monkeypatch.setattr(work_cli, "_resolve_ticket_workspace", lambda ticket_ref, project_ref=None: workspace.resolve())
    monkeypatch.setattr(work_cli, "_ensure_started", lambda workspace, rebuild=False: start_calls.append((workspace, rebuild)) or "omni-work-test")
    monkeypatch.setattr(work_cli, "_exec_shell", lambda container_name, shell: exec_calls.append((container_name, shell)) or 0)

    with pytest.raises(SystemExit) as exc:
        work_cli.main(["ssh", "--ticket", "t1", "--shell", "/bin/zsh"])

    assert exc.value.code == 0
    assert start_calls == [(workspace.resolve(), False)]
    assert exec_calls == [("omni-work-test", "/bin/zsh")]


def test_work_status_includes_urls(monkeypatch, tmp_path, capsys):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    details = {
        "State": {"Running": True, "Status": "running"},
        "NetworkSettings": {
            "Ports": {
                "7681/tcp": [{"HostPort": "4001"}],
                "8080/tcp": [{"HostPort": "4002"}],
                "6080/tcp": [{"HostPort": "4003"}],
            }
        },
    }
    monkeypatch.setattr(work_cli, "_inspect_container", lambda _name: details)

    work_cli.main(["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["sandbox_url"] == "http://localhost:4001"
    assert data["code_server_url"] == "http://localhost:4002"
    assert data["desktop_url"].startswith("http://localhost:4003/")


def test_work_open_prints_url(monkeypatch, tmp_path, capsys):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    details = {
        "State": {"Running": True, "Status": "running"},
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "4002"}]}} ,
    }
    monkeypatch.setattr(work_cli, "_inspect_container", lambda _name: details)

    work_cli.main(["open", "code", "--print-url"])
    assert capsys.readouterr().out.strip() == "http://localhost:4002"


def test_work_open_ensures_container(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    details = {
        "State": {"Running": True, "Status": "running"},
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "4002"}]}} ,
    }
    calls = {"inspect": 0, "ensure": 0, "open": []}

    def fake_inspect(_name):
        calls["inspect"] += 1
        return None if calls["inspect"] == 1 else details

    monkeypatch.setattr(work_cli, "_inspect_container", fake_inspect)
    monkeypatch.setattr(work_cli, "_ensure_started", lambda workspace, rebuild=False: calls.__setitem__("ensure", calls["ensure"] + 1) or "omni-work-test")
    monkeypatch.setattr("webbrowser.open", lambda url: calls["open"].append(url) or True)

    work_cli.main(["open", "code", "--ensure"])
    assert calls["ensure"] == 1
    assert calls["open"] == ["http://localhost:4002"]


def test_work_stop_can_cleanup_worktree(monkeypatch, tmp_path, capsys):
    from omni_code import work_cli

    project_workspace = tmp_path / "repo"
    project_workspace.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()

    stop_calls = []
    remove_calls = []
    update_calls = []

    class Ticket:
        id = "t1"
        project_id = "p1"
        worktree_path = str(worktree.resolve())
        worktree_name = "work-demo"

    class Project:
        id = "p1"
        workspace_dir = str(project_workspace.resolve())

    class Runtime:
        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket() if ticket_ref == "t1" else None

        def resolve_project(self, project_ref):
            return Project() if project_ref == "p1" else None

        def update_ticket_fields(self, ticket_ref, patch, project_ref=None):
            update_calls.append((ticket_ref, patch, project_ref))

    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())
    monkeypatch.setattr(work_cli, "_resolve_ticket_workspace", lambda ticket_ref, project_ref=None: worktree.resolve())
    monkeypatch.setattr(work_cli.sandbox_cli, "_stop_container", lambda name: stop_calls.append(name))
    monkeypatch.setattr(work_cli, "_inspect_container", lambda _name: None)
    monkeypatch.setattr(work_cli, "_record_task_state", lambda workspace, details, ticket_ref=None: None)
    monkeypatch.setattr(work_cli, "_remove_worktree", lambda workspace, worktree_path, worktree_name: remove_calls.append((workspace, worktree_path, worktree_name)))

    work_cli.main(["stop", "--ticket", "t1", "--cleanup-worktree"])
    assert stop_calls == [work_cli._container_name_for_workspace(worktree.resolve())]
    assert remove_calls == [(project_workspace.resolve(), worktree.resolve(), "work-demo")]
    assert update_calls == [("t1", {"worktreePath": None, "worktreeName": None}, "p1")]
    assert "Stopped work sandbox" in capsys.readouterr().out


def test_work_artifacts_lists_ticket_files(monkeypatch, tmp_path, capsys):
    from omni_code import work_cli

    config_dir = tmp_path / "cfg"
    artifacts_dir = config_dir / "fleet" / "tickets" / "t1" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "report.txt").write_text("hello", encoding="utf-8")

    class Ticket:
        id = "t1"

    class Runtime:
        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket() if ticket_ref == "t1" else None

    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())
    monkeypatch.setattr(work_cli, "get_config_dir", lambda: config_dir)

    work_cli.main(["artifacts", "--ticket", "t1", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["relative_path"] == "report.txt"


def test_work_artifact_read_outputs_text(monkeypatch, tmp_path, capsys):
    from omni_code import work_cli

    config_dir = tmp_path / "cfg"
    artifacts_dir = config_dir / "fleet" / "tickets" / "t1" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "report.txt").write_text("hello world", encoding="utf-8")

    class Ticket:
        id = "t1"

    class Runtime:
        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket() if ticket_ref == "t1" else None

    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())
    monkeypatch.setattr(work_cli, "get_config_dir", lambda: config_dir)

    work_cli.main(["artifact-read", "report.txt", "--ticket", "t1"])
    assert capsys.readouterr().out.strip() == "hello world"


def test_work_diff_reports_git_status(monkeypatch, tmp_path, capsys):
    from omni_code import work_cli

    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setattr(work_cli, "_resolve_workspace", lambda _project: workspace.resolve())

    monkeypatch.setattr(
        work_cli.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": " M changed.py\n?? new.txt\n"})(),
    )

    work_cli.main(["diff", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data == [
        {"path": "changed.py", "status": "M"},
        {"path": "new.txt", "status": "??"},
    ]


def test_work_history_uses_ticket_session(monkeypatch, capsys):
    from omni_code import work_cli

    class Ticket:
        supervisor_session_id = "s1"

    class Runtime:
        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket() if ticket_ref == "t1" else None

        def resolve_task_for_ticket(self, ticket_ref, project_ref=None):
            return None

    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())
    monkeypatch.setattr(
        work_cli,
        "oa_load_history",
        lambda session_id, project_slug=None, agent_slug=None: [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    )

    work_cli.main(["history", "--ticket", "t1", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["role"] == "user"
    assert data[1]["content"] == "hi"


def test_supervisor_start_writes_ticket_file(monkeypatch, tmp_path):
    from omni_code import work_cli

    writes = []

    class Ticket:
        id = "t1"
        title = "Build board"
        project_id = "p1"
        supervisor_session_id = None

    class Project:
        id = "p1"

    class Runtime:
        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket()

        def resolve_project(self, project_ref):
            return Project()

        def resolve_task_for_ticket(self, ticket_ref, project_ref=None):
            return None

        def write_ticket_file(self, ticket_ref, project_ref=None):
            writes.append((ticket_ref, project_ref))

        def update_ticket_fields(self, ticket_ref, patch, project_ref=None):
            return Ticket()

        def upsert_task(self, **kwargs):
            return None

    monkeypatch.setattr(work_cli, "_ensure_ticket_rpc_context", lambda ticket_ref, project_ref=None: (Runtime(), Ticket(), Project(), None, "ws://example/ws"))
    monkeypatch.setattr(work_cli, "_resolve_ticket_workspace", lambda ticket_ref, project_ref=None: Path("/tmp/work"))

    async def fake_rpc_call(ws_url, method, params):
        if method == "server_call":
            return {"result": {"session_id": "s1"}}
        return {"result": {"session_id": "s1", "run_id": "r1"}}

    monkeypatch.setattr(work_cli, "_rpc_call", fake_rpc_call)

    result = work_cli.supervisor_start("t1", "Begin")
    assert result == {"session_id": "s1", "run_id": "r1"}
    assert writes == [("t1", "p1")]


def test_supervisor_start_runs_before_run_hook(monkeypatch):
    from omni_code import work_cli

    class Ticket:
        id = "t1"
        title = "Build board"
        project_id = "p1"
        supervisor_session_id = None

    class Project:
        id = "p1"

    class Runtime:
        def __init__(self):
            self.hook_calls = []

        def write_ticket_file(self, ticket_ref, project_ref=None):
            return None

        def run_hook(self, project_ref, hook_name, workspace_dir):
            self.hook_calls.append((project_ref, hook_name, str(workspace_dir)))
            return True

        def update_ticket_fields(self, ticket_ref, patch, project_ref=None):
            return Ticket()

        def upsert_task(self, **kwargs):
            return None

    runtime = Runtime()
    monkeypatch.setattr(work_cli, "_ensure_ticket_rpc_context", lambda ticket_ref, project_ref=None: (runtime, Ticket(), Project(), None, "ws://example/ws"))
    monkeypatch.setattr(work_cli, "_resolve_ticket_workspace", lambda ticket_ref, project_ref=None: Path("/tmp/work"))

    async def fake_rpc_call(ws_url, method, params):
        if method == "server_call":
            return {"result": {"session_id": "s1"}}
        return {"result": {"session_id": "s1", "run_id": "r1"}}

    monkeypatch.setattr(work_cli, "_rpc_call", fake_rpc_call)
    work_cli.supervisor_start("t1", "Begin")
    assert runtime.hook_calls[0][1] == "before_run"


def test_work_records_task_state_for_project(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()

    recorded = {}

    class Project:
        id = "p1"
        label = "demo"

    class Runtime:
        def resolve_project(self, value):
            return Project() if value == str(workspace.resolve()) else None

        def resolve_ticket(self, ticket_ref, project_ref=None):
            return None

        def resolve_task_for_ticket(self, ticket_ref, project_ref=None):
            return None

        def upsert_task(self, **kwargs):
            recorded.update(kwargs)

    details = {
        "State": {"Running": True},
        "Name": "/omni-work-test",
        "NetworkSettings": {"Ports": {"7681/tcp": [{"HostPort": "4001"}]}} ,
    }
    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())

    work_cli._record_task_state(workspace.resolve(), details)
    assert recorded["project_id"] == "p1"
    assert recorded["ticket_id"] is None
    assert recorded["status"]["type"] == "running"


def test_work_records_task_state_for_ticket(monkeypatch, tmp_path):
    from omni_code import work_cli

    workspace = tmp_path / "ws"
    workspace.mkdir()

    recorded = {}

    class Project:
        id = "p1"
        label = "demo"

    class Ticket:
        id = "t1"
        title = "Build board"

    class ExistingTask:
        id = "task-1"

    class Runtime:
        def resolve_project(self, value):
            return Project() if value == str(workspace.resolve()) or value == "p1" else None

        def resolve_ticket(self, ticket_ref, project_ref=None):
            return Ticket() if ticket_ref == "t1" else None

        def resolve_task_for_ticket(self, ticket_ref, project_ref=None):
            return ExistingTask() if ticket_ref == "t1" else None

        def upsert_task(self, **kwargs):
            recorded.update(kwargs)

    details = {
        "State": {"Running": True},
        "Name": "/omni-work-test",
        "NetworkSettings": {"Ports": {"8080/tcp": [{"HostPort": "4002"}]}} ,
    }
    monkeypatch.setattr(work_cli, "ProjectsRuntime", lambda: Runtime())

    work_cli._record_task_state(workspace.resolve(), details, ticket_ref="t1")
    assert recorded["project_id"] == "p1"
    assert recorded["ticket_id"] == "t1"
    assert recorded["task_id"] == "task-1"
    assert recorded["task_description"] == "Work sandbox for: Build board"
