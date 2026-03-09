import json

import pytest


def test_projects_add_list_use_show_remove(monkeypatch, tmp_path, capsys):
    from omni_code import projects_cli
    from omni_code import projects_runtime as runtime_mod

    config_path = tmp_path / "cfg" / "projects.json"
    monkeypatch.setattr(projects_cli, "ProjectsRuntime", lambda: runtime_mod.ProjectsRuntime(config_path))

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    projects_cli.main(["add", str(workspace), "--label", "demo", "--id", "p1"])
    out = capsys.readouterr().out
    assert "Added project demo" in out

    projects_cli.main(["list", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["label"] == "demo"
    assert data[0]["workspace_dir"] == str(workspace.resolve())
    assert data[0]["is_current"] is True

    projects_cli.main(["show", "demo", "--json"])
    show_data = json.loads(capsys.readouterr().out)
    assert show_data["id"] == "p1"

    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    projects_cli.main(["add", str(other_workspace), "--label", "other", "--id", "p2"])
    capsys.readouterr()

    projects_cli.main(["use", "other"])
    assert "Current project: other" in capsys.readouterr().out

    projects_cli.main(["remove", "demo"])
    assert "Removed project demo" in capsys.readouterr().out


def test_projects_default_invocation_prints_tui_message(capsys):
    from omni_code import projects_cli

    called = {}

    projects_cli.launch_projects_tui = lambda *, projects_config_path, debug=False: called.update(
        {"path": str(projects_config_path), "debug": debug}
    )

    projects_cli.main([])
    assert called["path"].endswith("config.json")
    assert called["debug"] is False


def test_projects_add_missing_workspace_exits_1(tmp_path):
    from omni_code import projects_cli

    with pytest.raises(SystemExit) as exc:
        projects_cli.main(["add", str(tmp_path / "missing")])

    assert exc.value.code == 1


def test_projects_tui_debug_flag(monkeypatch):
    from omni_code import projects_cli

    called = {}
    monkeypatch.setattr(
        projects_cli,
        "launch_projects_tui",
        lambda *, projects_config_path, debug=False: called.update({"path": str(projects_config_path), "debug": debug}),
    )

    projects_cli.main(["tui", "--debug"])
    assert called["path"].endswith("config.json")
    assert called["debug"] is True


def test_projects_runtime_supports_tickets(tmp_path):
    from omni_code.projects_runtime import ProjectsRuntime

    runtime = ProjectsRuntime(tmp_path / "projects.json")
    workspace = tmp_path / "repo"
    workspace.mkdir()

    project = runtime.add_project(label="demo", workspace_dir=workspace, project_id="p1")
    ticket = runtime.add_ticket(project_ref=project.id, title="Implement TUI", description="Build the first board")
    tickets = runtime.list_tickets(project.id)

    assert len(tickets) == 1
    assert tickets[0].id == ticket.id
    assert tickets[0].column_id == runtime.get_pipeline(project.id).columns[0].id


def test_projects_runtime_snapshot_contains_pipeline_and_tickets(tmp_path):
    from omni_code.projects_runtime import ProjectsRuntime

    runtime = ProjectsRuntime(tmp_path / "projects.json")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "FLEET.md").write_text("- [Todo](backlog)\n- [Doing](implementation)\n- [Done](completed)\n", encoding="utf-8")

    project = runtime.add_project(label="demo", workspace_dir=workspace, project_id="p1")
    runtime.add_ticket(project_ref=project.id, title="Build board")

    snapshot = runtime.export_snapshot()
    assert snapshot["current_project"] == "p1"
    assert len(snapshot["projects"]) == 1
    assert len(snapshot["tickets"]) == 1
    assert snapshot["pipelines"]["p1"]["columns"][1]["id"] == "implementation"


def test_projects_runtime_writes_ticket_file_on_create_and_move(tmp_path):
    from omni_code.projects_runtime import ProjectsRuntime

    runtime = ProjectsRuntime(tmp_path / "config.json")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    project = runtime.add_project(label="demo", workspace_dir=workspace, project_id="p1")

    ticket = runtime.add_ticket(project_ref=project.id, title="Build board")
    ticket_file = runtime._ticket_file_path(ticket.id)
    assert ticket_file.exists()
    assert 'column: "Backlog"' in ticket_file.read_text(encoding="utf-8")

    runtime.move_ticket(ticket.id, "implementation", project.id)
    assert 'column: "Implementation"' in ticket_file.read_text(encoding="utf-8")


def test_projects_runtime_parses_workflow_pipeline_and_hooks(tmp_path):
    from omni_code.projects_runtime import ProjectsRuntime

    runtime = ProjectsRuntime(tmp_path / "config.json")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "FLEET.md").write_text(
        """---
hooks:
  before_run: echo build
pipeline:
  columns:
    - id: todo
      label: To Do
    - id: review
      label: Review
      gate: true
---

Custom prompt.
""",
        encoding="utf-8",
    )
    project = runtime.add_project(label="demo", workspace_dir=workspace, project_id="p1")

    workflow = runtime.get_workflow(project.id)
    pipeline = runtime.get_pipeline(project.id)
    assert workflow.config.hooks.before_run == "echo build"
    assert workflow.prompt_template == "Custom prompt."
    assert [column.id for column in pipeline.columns] == ["todo", "review"]
    assert pipeline.columns[1].gate is True


def test_projects_runtime_sync_ticket_file_updates_column(tmp_path):
    from omni_code.projects_runtime import ProjectsRuntime

    runtime = ProjectsRuntime(tmp_path / "config.json")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "FLEET.md").write_text(
        """---
pipeline:
  columns:
    - id: backlog
      label: Backlog
    - id: implementation
      label: Implementation
---
""",
        encoding="utf-8",
    )
    project = runtime.add_project(label="demo", workspace_dir=workspace, project_id="p1")
    ticket = runtime.add_ticket(project_ref=project.id, title="Build board")
    ticket_file = runtime._ticket_file_path(ticket.id)
    ticket_file.write_text('column: "Implementation"\nescalation: "Need human review"\n', encoding="utf-8")

    result = runtime.sync_ticket_file(ticket.id, project.id)
    updated = runtime.resolve_ticket(ticket.id, project.id)
    assert result == {"column_id": "implementation", "escalation": "Need human review"}
    assert updated is not None and updated.column_id == "implementation"


def test_projects_ticket_sync_command(tmp_path, monkeypatch, capsys):
    from omni_code import projects_cli
    from omni_code import projects_runtime as runtime_mod

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(projects_cli, "ProjectsRuntime", lambda: runtime_mod.ProjectsRuntime(config_path))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "FLEET.md").write_text(
        """---
pipeline:
  columns:
    - id: backlog
      label: Backlog
    - id: implementation
      label: Implementation
---
""",
        encoding="utf-8",
    )
    projects_cli.main(["add", str(workspace), "--label", "demo", "--id", "p1"])
    capsys.readouterr()
    projects_cli.main(["ticket", "add", "--project", "demo", "Build board"])
    tickets = json.loads(capsys.readouterr().out or "[]") if False else None
    runtime = runtime_mod.ProjectsRuntime(config_path)
    ticket = runtime.list_tickets("demo")[0]
    runtime._ticket_file_path(ticket.id).write_text('column: "Implementation"\n', encoding="utf-8")

    projects_cli.main(["ticket", "sync", "--ticket", ticket.id, "--project", "demo"])
    assert "Synced ticket" in capsys.readouterr().out


def test_projects_ticket_watch_once_command(tmp_path, monkeypatch, capsys):
    from omni_code import projects_cli
    from omni_code import projects_runtime as runtime_mod

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(projects_cli, "ProjectsRuntime", lambda: runtime_mod.ProjectsRuntime(config_path))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "FLEET.md").write_text(
        """---
pipeline:
  columns:
    - id: backlog
      label: Backlog
    - id: implementation
      label: Implementation
---
""",
        encoding="utf-8",
    )
    projects_cli.main(["add", str(workspace), "--label", "demo", "--id", "p1"])
    capsys.readouterr()
    projects_cli.main(["ticket", "add", "--project", "demo", "Build board"])
    capsys.readouterr()
    runtime = runtime_mod.ProjectsRuntime(config_path)
    ticket = runtime.list_tickets("demo")[0]
    runtime._ticket_file_path(ticket.id).write_text('column: "Implementation"\n', encoding="utf-8")

    projects_cli.main(["ticket", "watch", "--ticket", ticket.id, "--project", "demo", "--once", "--force"])
    assert "Synced ticket" in capsys.readouterr().out


def test_projects_ticket_cli_commands(monkeypatch, tmp_path, capsys):
    from omni_code import projects_cli
    from omni_code import projects_runtime as runtime_mod

    config_path = tmp_path / "cfg" / "projects.json"
    monkeypatch.setattr(projects_cli, "ProjectsRuntime", lambda: runtime_mod.ProjectsRuntime(config_path))

    workspace = tmp_path / "repo"
    workspace.mkdir()

    projects_cli.main(["add", str(workspace), "--label", "demo", "--id", "p1"])
    capsys.readouterr()

    projects_cli.main(["ticket", "add", "--project", "demo", "Implement board", "--description", "First board screen"])
    assert "Added ticket Implement board" in capsys.readouterr().out

    projects_cli.main(["ticket", "list", "--project", "demo", "--json"])
    tickets = json.loads(capsys.readouterr().out)
    assert len(tickets) == 1
    ticket_id = tickets[0]["id"]
    assert tickets[0]["column_id"] == "backlog"

    projects_cli.main(["ticket", "move", ticket_id, "implementation", "--project", "demo"])
    assert "Moved ticket Implement board to implementation" in capsys.readouterr().out


def test_projects_ticket_history_delegates_to_work(monkeypatch):
    from omni_code import projects_cli

    called = {}
    monkeypatch.setattr(projects_cli, "work_cmd_history", lambda args: called.update({"ticket": args.ticket, "json": args.json}))

    projects_cli.main(["ticket", "history", "--ticket", "t1", "--json"])
    assert called == {"ticket": "t1", "json": True}


def test_projects_ticket_artifact_read_delegates_to_work(monkeypatch):
    from omni_code import projects_cli

    called = {}
    monkeypatch.setattr(projects_cli, "work_cmd_artifact_read", lambda args: called.update({"ticket": args.ticket, "path": args.path}))

    projects_cli.main(["ticket", "artifact-read", "report.txt", "--ticket", "t1"])
    assert called == {"ticket": "t1", "path": "report.txt"}


def test_projects_ticket_supervisor_start_delegates(monkeypatch, capsys):
    from omni_code import projects_cli

    monkeypatch.setattr(
        projects_cli,
        "work_supervisor_start",
        lambda ticket, prompt, project=None: {"session_id": "s1", "run_id": "r1"},
    )

    projects_cli.main(["ticket", "supervisor-start", "--ticket", "t1", "Begin", "work"])
    out = capsys.readouterr().out
    assert "Started supervisor for t1" in out
    assert "Session: s1" in out


def test_projects_ticket_supervisor_send_delegates(monkeypatch, capsys):
    from omni_code import projects_cli

    called = {}
    monkeypatch.setattr(
        projects_cli,
        "work_supervisor_send",
        lambda ticket, message, project=None: called.update({"ticket": ticket, "message": message}),
    )

    projects_cli.main(["ticket", "supervisor-send", "--ticket", "t1", "hello"])
    assert called == {"ticket": "t1", "message": "hello"}
    assert "Sent message to supervisor" in capsys.readouterr().out


def test_projects_ticket_supervisor_stop_delegates(monkeypatch, capsys):
    from omni_code import projects_cli

    called = {}
    monkeypatch.setattr(
        projects_cli,
        "work_supervisor_stop",
        lambda ticket, project=None: called.update({"ticket": ticket}),
    )

    projects_cli.main(["ticket", "supervisor-stop", "--ticket", "t1"])
    assert called == {"ticket": "t1"}
    assert "Stopped supervisor for t1" in capsys.readouterr().out
