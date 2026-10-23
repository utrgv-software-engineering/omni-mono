import json
import sys
import types
from pathlib import Path

import pytest
import yaml

from omniagents.cli import main as cli_main
from omniagents.core.eval.loader import EvalConfig


def _make_fake_loader_module(compiled_path):
    """Create a fake loader module that returns a minimal EvalConfig."""
    p = Path(compiled_path).resolve()
    # Read the file if it exists to capture tracing config etc.
    tracing_config = {}
    eval_config_dict = {}
    agent_config = {}
    try:
        import yaml

        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        eval_config_dict = raw.get("evaluation") or {}
        tracing_config = raw.get("tracing") or {}
        agent_config = raw
    except Exception:
        pass

    fake_eval_config = EvalConfig(
        config=eval_config_dict,
        agent_config=agent_config,
        agent_path=p,
        eval_dir=p.parent,
        project_root=p.parent,
        tracing_config=tracing_config,
        project_config={},
    )

    def fake_load_eval_config(*args, **kwargs):
        return fake_eval_config

    def fake_introspect_resources(cfg):
        return {
            "project_name": "test",
            "agent_file": str(cfg.agent_path),
            "resources": {"scenarios": [], "judges": [], "metrics": []},
        }

    def fake_filter_scenarios(items, select, exclude):
        return items

    module = types.ModuleType("omniagents.core.eval.loader")
    module.load_eval_config = fake_load_eval_config
    module.introspect_resources = fake_introspect_resources
    module.filter_scenarios = fake_filter_scenarios
    module.EvalConfig = EvalConfig
    module.__package__ = "omniagents.core.eval"
    return module


class DummyRunner:
    def __init__(self, spec=None, config_path=None):
        self.spec = spec
        self.config_path = config_path
        self.calls = []

    def run(self, mode, **kwargs):
        self.calls.append((mode, kwargs))


@pytest.fixture
def stub_runtime(monkeypatch):
    module = types.ModuleType("omniagents.core.project.runtime")
    module.calls = []

    def _load_project(path):
        return types.SimpleNamespace(path=path, paths={"tools": []})

    def _build_spec_from_project(project, agent_key=None, prefer_voice=False):
        module.calls.append(
            {
                "project": project,
                "agent_key": agent_key,
                "prefer_voice": prefer_voice,
            }
        )
        return {
            "project": project,
            "agent_key": agent_key,
            "prefer_voice": prefer_voice,
        }

    def _resolve_agent(project, agent_key=None):
        key = agent_key or "entry"
        return (key, Path("agents") / key / "agent.yml")

    module.load_project = _load_project
    module.build_spec_from_project = _build_spec_from_project
    module.resolve_agent = _resolve_agent
    monkeypatch.setitem(sys.modules, "omniagents.core.project.runtime", module)


def test_run_command_forwards_initial_prompt(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "web",
        "hello",
        "world",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert runner.calls
    mode, kwargs = runner.calls[0]
    assert mode == "web"
    assert kwargs["initial_prompt"] == "hello world"
    assert kwargs["args"].prompt == ["hello", "world"]


def test_run_command_without_prompt_skips_initial_prompt(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "web",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert runner.calls
    mode, kwargs = runner.calls[0]
    assert mode == "web"
    assert "initial_prompt" not in kwargs


def test_run_command_web_mode_defaults_voice_off(monkeypatch, stub_runtime):
    runner = DummyRunner()

    def fake_runner(spec=None, config_path=None):
        runner.spec = spec
        return runner

    monkeypatch.setattr("omniagents.cli.Runner", fake_runner)
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "web",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    module = sys.modules["omniagents.core.project.runtime"]
    assert module.calls
    assert module.calls[-1]["prefer_voice"] is False


def test_run_command_server_mode_includes_network_args(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "server",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--auth-token",
        "token",
        "--reload",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert runner.calls
    mode, kwargs = runner.calls[0]
    assert mode == "server"
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8765
    assert kwargs["auth_token"] == "token"
    assert kwargs["reload"] is True
    module = sys.modules["omniagents.core.project.runtime"]
    assert module.calls
    assert module.calls[-1]["prefer_voice"] is False


def test_run_command_web_mode_with_agent_disables_voice(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "web",
        "--agent",
        "custom",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    module = sys.modules["omniagents.core.project.runtime"]
    assert module.calls
    assert module.calls[-1]["prefer_voice"] is False


def test_run_command_server_mode_with_agent_disables_voice(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "server",
        "--agent",
        "custom",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    module = sys.modules["omniagents.core.project.runtime"]
    assert module.calls
    assert module.calls[-1]["prefer_voice"] is False


def test_run_command_resume_invalid_mode_exits(monkeypatch, stub_runtime, capsys):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "web",
        "--resume",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    # Resume should work with web mode now
    cli_main()

    assert runner.calls
    mode, kwargs = runner.calls[0]
    assert mode == "web"
    assert kwargs.get("resume") is True


def test_sessions_db_path_command(monkeypatch, capsys):
    argv = ["omniagents", "sessions", "db-path"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as excinfo:
        cli_main()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip().endswith("history.db")


def test_save_command_invokes_save_agent(monkeypatch, stub_runtime, tmp_path, capsys):
    saved = {}

    def fake_save_agent(src, dest, project_tool_dirs=None):
        saved["src"] = src
        saved["dest"] = dest
        saved["dirs"] = project_tool_dirs

    monkeypatch.setattr("omniagents.cli.save_agent_as_py", fake_save_agent)

    argv = [
        "omniagents",
        "save",
        "--project",
        "proj.yml",
        "--agent",
        "custom",
        "--output",
        str(tmp_path / "agent.py"),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    capture = capsys.readouterr()
    assert "Standalone Python module created" in capture.out
    assert saved["dest"] == str(tmp_path / "agent.py")
    assert saved["src"].endswith(str(Path("agents") / "custom" / "agent.yml"))
    assert saved["dirs"] == []


def test_compile_command_prints_output(monkeypatch, tmp_path, capsys):
    compiled_path = tmp_path / "compiled.yml"
    compiled_path.write_text("{}", encoding="utf-8")

    loader_module = _make_fake_loader_module(compiled_path)
    monkeypatch.setitem(sys.modules, "omniagents.core.eval.loader", loader_module)

    argv = [
        "omniagents",
        "compile",
        "--project",
        "proj.yml",
        "--select",
        "alpha",
        "--exclude",
        "beta",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    output = capsys.readouterr().out
    assert str(compiled_path) in output
    assert "resources" in output


def test_agent_run_server_mode_forwards_args(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "agent",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "server",
        "--host",
        "0.0.0.0",
        "--port",
        "9001",
        "--auth-token",
        "token",
        "--reload",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert runner.calls
    mode, kwargs = runner.calls[0]
    assert mode == "server"
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 9001
    assert kwargs["auth_token"] == "token"
    assert kwargs["reload"] is True
    module = sys.modules["omniagents.core.project.runtime"]
    assert module.calls
    assert module.calls[-1]["prefer_voice"] is False


def test_agent_run_web_mode_defaults_voice_off(monkeypatch, stub_runtime):
    runner = DummyRunner()

    def fake_runner(spec=None, config_path=None):
        runner.spec = spec
        return runner

    monkeypatch.setattr("omniagents.cli.Runner", fake_runner)
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "agent",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "web",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    module = sys.modules["omniagents.core.project.runtime"]
    assert module.calls
    assert module.calls[-1]["prefer_voice"] is False


def test_agent_run_server_mode_defaults_voice_off(monkeypatch, stub_runtime):
    runner = DummyRunner()

    def fake_runner(spec=None, config_path=None):
        runner.spec = spec
        return runner

    monkeypatch.setattr("omniagents.cli.Runner", fake_runner)
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "agent",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "server",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    module = sys.modules["omniagents.core.project.runtime"]
    assert module.calls
    assert module.calls[-1]["prefer_voice"] is False


def test_agent_run_server_mode_with_agent_disables_voice(monkeypatch, stub_runtime):
    runner = DummyRunner()
    monkeypatch.setattr(
        "omniagents.cli.Runner", lambda spec=None, config_path=None: runner
    )
    monkeypatch.setattr("omniagents.cli.Debug.init", lambda cli_flag: None)

    argv = [
        "omniagents",
        "agent",
        "run",
        "--project",
        "proj.yml",
        "--mode",
        "server",
        "--agent",
        "custom",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    module = sys.modules["omniagents.core.project.runtime"]
    assert module.calls
    assert module.calls[-1]["prefer_voice"] is False


def test_agent_save_invokes_save_agent(monkeypatch, stub_runtime, tmp_path, capsys):
    saved = {}

    def fake_save_agent(src, dest, project_tool_dirs=None):
        saved["src"] = src
        saved["dest"] = dest
        saved["dirs"] = project_tool_dirs

    monkeypatch.setattr("omniagents.cli.save_agent_as_py", fake_save_agent)

    argv = [
        "omniagents",
        "agent",
        "save",
        "--project",
        "proj.yml",
        "--agent",
        "custom",
        "--output",
        str(tmp_path / "agent.py"),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    capture = capsys.readouterr()
    assert "Standalone Python module created" in capture.out
    assert saved["dest"] == str(tmp_path / "agent.py")
    assert saved["src"].endswith(str(Path("agents") / "custom" / "agent.yml"))
    assert saved["dirs"] == []


def test_agent_optimize_invokes_backend(monkeypatch, stub_runtime):
    recorded = {}

    def fake_optimize(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("omniagents.cli.optimize_agent_from_project", fake_optimize)

    argv = [
        "omniagents",
        "agent",
        "optimize",
        "--project",
        "proj.yml",
        "--agent",
        "demo",
        "--category",
        "quality",
        "--train-file",
        "train.yml",
        "--dev-file",
        "dev.yml",
        "--test-file",
        "test.yml",
        "--optimizer",
        "rewrite",
        "--score-mode",
        "all_or_nothing",
        "--reflection-model",
        "gpt-4",
        "--output",
        "out",
        "--resume",
        "--all-cases",
        "--max-turns",
        "5",
        "--max-metric-calls",
        "50",
        "--positives-only",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert recorded["proj_path"] == "proj.yml"
    assert recorded["agent_key"] == "demo"
    assert recorded["category"] == "quality"
    assert recorded["train_file"] == "train.yml"
    assert recorded["dev_file"] == "dev.yml"
    assert recorded["test_file"] == "test.yml"
    assert recorded["optimizer_key"] == "rewrite"
    assert recorded["score_mode"] == "all_or_nothing"
    assert recorded["reflection_model"] == "gpt-4"


def _fake_workspace(monkeypatch, tmp_path, data, *, output=None, progress=()):
    """Stand in for EvalWorkspace: ``run`` returns ``data`` as the saved run
    and records how it was called."""
    seen = {}

    class FakeWorkspace:
        def __init__(self, project, agent=None):
            self.root = tmp_path
            seen.update(project=project, agent=agent)

        def run(self, **kwargs):
            seen.update(kwargs)
            for event in progress:
                kwargs["progress_cb"](event)
            saved = tmp_path / "results" / "results.json"
            saved.parent.mkdir(exist_ok=True)
            saved.write_text(json.dumps(data))
            return {**data, "path": str(saved)}

        def config(self):
            return types.SimpleNamespace(config={"output": output} if output else {})

        def resolve_set(self, value):
            return f"resolved:{value}"

    monkeypatch.setattr("omniagents.core.eval.workspace.EvalWorkspace", FakeWorkspace)
    return seen


def _suite_argv(*extra):
    return ["omniagents", "eval", "suite", "run", "--project", "proj.yml", *extra]


def test_eval_suite_run_tap_reporter(monkeypatch, tmp_path, capsys):
    data = {
        "runs": [
            {
                "scenario": "case1",
                "scenario_config": {"id": "case1", "name": "Case One"},
                "measures": [
                    {"name": "accuracy", "passed": True},
                    {"name": "latency", "passed": False, "reason": "slow"},
                    {"name": "skipped", "passed": None},
                ],
                "prompt": "hello",
                "final_assistant_text": "world",
            }
        ],
        "metrics": [
            {
                "label": "score",
                "value": 0.9,
                "target": {"op": ">=", "value": 0.8},
                "meets_target": True,
            },
            {
                "label": "errors",
                "value": 0.2,
                "target": {"op": "<=", "value": 0.1},
                "meets_target": False,
            },
            {"label": "notes", "value": "n/a"},
        ],
        "stats": {
            "total_scenarios": 2,
            "included_scenarios": 1,
            "skipped_unmeasured": 1,
            "unknown_measures": {"mystery": 1},
            "measures_discovered_count": 1,
            "measures_discovered": ["mystery"],
        },
    }
    seen = _fake_workspace(
        monkeypatch,
        tmp_path,
        data,
        progress=[
            {"event": "progress", "skipped": False, "failed": 0},
            {"event": "progress", "skipped": True, "failed": 1},
        ],
    )
    output_path = tmp_path / "copy.json"
    argv = _suite_argv(
        "--output",
        str(output_path),
        "--reporter",
        "tap",
        "--color",
        "never",
        "--name",
        "demo",
    )
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 1
    captured = capsys.readouterr().out
    assert "TAP version 13" in captured
    assert json.loads(output_path.read_text())["runs"] == data["runs"]
    assert seen["name"] == "demo"
    assert seen["include_unmeasured"] is False


def test_eval_suite_run_pretty_reporter(monkeypatch, tmp_path, capsys):
    data = {
        "runs": [
            {
                "scenario": "case1",
                "scenario_config": {"id": "case1", "name": "Case One"},
                "measures": [
                    {"name": "accuracy", "passed": True},
                    {"name": "skipped", "passed": None},
                ],
                "prompt": "hello",
                "final_assistant_text": "world",
            }
        ],
        "metrics": [
            {
                "label": "score",
                "value": 0.95,
                "target": {"op": ">=", "value": 0.9},
                "meets_target": True,
            },
            {"label": "notes", "value": "n/a"},
        ],
        "stats": {
            "total_scenarios": 1,
            "included_scenarios": 1,
            "skipped_unmeasured": 0,
            "unknown_measures": {},
            "measures_discovered_count": 0,
            "measures_discovered": [],
        },
    }
    _fake_workspace(monkeypatch, tmp_path, data, output="alt.json")
    monkeypatch.setattr(
        sys, "argv", _suite_argv("--color", "never", "--reporter", "pretty")
    )

    cli_main()

    captured = capsys.readouterr().out
    assert "Running" in captured
    assert "Passed:" in captured
    # evaluation.output writes a copy beside the project.
    assert (tmp_path / "alt.json").is_file()


def test_eval_suite_run_pretty_verbose(monkeypatch, tmp_path, capsys):
    data = {
        "runs": [
            {
                "scenario": "case1",
                "scenario_config": {"id": "case1", "name": "Case One"},
                "measures": [{"name": "accuracy", "passed": True}],
                "prompt": "hello",
                "final_assistant_text": "world",
            }
        ],
        "metrics": [
            {
                "label": "score",
                "value": 0.95,
                "target": {"op": ">=", "value": 0.9},
                "meets_target": True,
            }
        ],
        "stats": {
            "total_scenarios": 1,
            "included_scenarios": 1,
            "skipped_unmeasured": 0,
            "unknown_measures": {"mystery": 2},
            "measures_discovered_count": 12,
            "measures_discovered": [f"m{i}" for i in range(1, 13)],
        },
    }
    _fake_workspace(
        monkeypatch,
        tmp_path,
        data,
        progress=[{"event": "progress", "skipped": False, "failed": 0}],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        _suite_argv("--color", "always", "--reporter", "pretty", "-v"),
    )

    cli_main()

    captured = capsys.readouterr().out
    assert "Discovered measures" in captured
    assert "unknown measures" in captured


def test_eval_scenarios_generate(monkeypatch, tmp_path, capsys):
    calls = {}

    class FakeWorkspace:
        def __init__(self, project, agent=None):
            calls["project"] = project

        async def generate_scenarios(self, name, *, count, seed, progress_cb):
            calls.update(name=name, count=count, seed=seed)
            progress_cb({"event": "start", "target": count})
            progress_cb({"event": "progress"})
            progress_cb({"event": "done", "count": count})
            return {
                "scenarios": [{"id": "case1"}],
                "path": f"evaluations/generated/{name}/scenarios.yml",
            }

    monkeypatch.setattr("omniagents.core.eval.workspace.EvalWorkspace", FakeWorkspace)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omniagents",
            "eval",
            "scenarios",
            "generate",
            "--project",
            str(tmp_path / "project.yml"),
            "--name",
            "demo-set",
            "--count",
            "1",
            "--color",
            "never",
        ],
    )

    cli_main()

    assert calls == {
        "project": str(tmp_path / "project.yml"),
        "name": "demo-set",
        "count": 1,
        "seed": None,
    }
    captured = capsys.readouterr().out
    assert (
        "Wrote 1 scenarios to evaluations/generated/demo-set/scenarios.yml" in captured
    )


def test_generate_tool(monkeypatch, tmp_path, capsys):
    def fake_scaffold(project_ref, tool_name, make_async=False, force=False):
        return ([str(tmp_path / "tools" / f"{tool_name}.py")], str(tmp_path))

    monkeypatch.setattr("omniagents.core.project.scaffold.scaffold_tool", fake_scaffold)

    argv = [
        "omniagents",
        "generate",
        "tool",
        "demo_tool",
        "--project",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    captured = capsys.readouterr().out
    assert "Project: " in captured
    assert "demo_tool.py" in captured


def test_generate_agent(monkeypatch, tmp_path, capsys):
    def fake_scaffold(project_ref, agent_name, model="gpt-4.1", force=False):
        files = [str(tmp_path / "agents" / agent_name / "agent.yml")]
        return (files, str(tmp_path), agent_name)

    monkeypatch.setattr(
        "omniagents.core.project.scaffold.scaffold_agent", fake_scaffold
    )

    argv = [
        "omniagents",
        "generate",
        "agent",
        "demo_agent",
        "--project",
        str(tmp_path),
        "--model",
        "gpt-4.1-mini",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    output = capsys.readouterr().out
    assert "demo_agent" in output


def test_generate_guardrail(monkeypatch, tmp_path, capsys):
    def fake_scaffold(project_ref, kind, name, make_async=False, force=False):
        files = [str(tmp_path / "guardrails" / f"{name}.py")]
        return (files, str(tmp_path))

    monkeypatch.setattr(
        "omniagents.core.project.scaffold.scaffold_guardrail", fake_scaffold
    )

    argv = [
        "omniagents",
        "generate",
        "guardrail",
        "input",
        "demo_guardrail",
        "--project",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    captured = capsys.readouterr().out
    assert "demo_guardrail" in captured


def test_generate_server_function(monkeypatch, tmp_path, capsys):
    def fake_scaffold(project_ref, name, make_async=False, force=False):
        files = [str(tmp_path / "server_functions" / f"{name}.py")]
        return (files, str(tmp_path))

    monkeypatch.setattr(
        "omniagents.core.project.scaffold.scaffold_server_function", fake_scaffold
    )

    argv = [
        "omniagents",
        "generate",
        "server-function",
        "demo_server",
        "--project",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    result = capsys.readouterr().out
    assert "demo_server" in result


def test_generate_judge(monkeypatch, tmp_path, capsys):
    def fake_scaffold(
        project_ref,
        key,
        model="gpt-4.1",
        source="final_assistant_text",
        add_measure=True,
        failure_category=None,
        force=False,
    ):
        files = [str(tmp_path / "judges" / key / "judge.yml")]
        return (files, str(tmp_path))

    monkeypatch.setattr(
        "omniagents.core.project.scaffold.scaffold_judge", fake_scaffold
    )

    argv = [
        "omniagents",
        "generate",
        "judge",
        "demo_judge",
        "--project",
        str(tmp_path),
        "--model",
        "gpt-4.1-mini",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    output = capsys.readouterr().out
    assert "demo_judge" in output


def test_generate_optimizer(monkeypatch, tmp_path, capsys):
    def fake_scaffold(project_ref, key, model="gpt-4.1", kind="agent", force=False):
        files = [str(tmp_path / "optimizers" / f"{key}.yml")]
        return (files, str(tmp_path))

    monkeypatch.setattr(
        "omniagents.core.project.scaffold.scaffold_optimizer", fake_scaffold
    )

    argv = [
        "omniagents",
        "generate",
        "optimizer",
        "demo_optimizer",
        "--project",
        str(tmp_path),
        "--type",
        "judge",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    captured = capsys.readouterr().out
    assert "demo_optimizer" in captured


# ---------------------------------------------------------------------------
# run_codex_auth_command — shared product-facing codex auth flow
# ---------------------------------------------------------------------------


def test_run_codex_auth_status_not_signed_in_uses_prog(monkeypatch, capsys):
    from omniagents.cli import run_codex_auth_command

    monkeypatch.setattr("omniagents.core.providers.codex.load_tokens", lambda: None)

    status = run_codex_auth_command("status", prog="omni")

    assert status == 0
    assert "Run: omni auth codex login" in capsys.readouterr().out


def test_run_codex_auth_login_success(monkeypatch, capsys):
    from omniagents.cli import run_codex_auth_command

    monkeypatch.setattr(
        "omniagents.core.providers.codex.device_login",
        lambda: {"account_id": "acct-1"},
    )

    status = run_codex_auth_command("login")

    assert status == 0
    out = capsys.readouterr().out
    assert "Signed in to ChatGPT (Codex)." in out
    assert "acct-1" in out


def test_run_codex_auth_login_failure_returns_1(monkeypatch, capsys):
    from omniagents.cli import run_codex_auth_command

    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr("omniagents.core.providers.codex.device_login", boom)

    assert run_codex_auth_command("login") == 1
    assert "Codex login failed" in capsys.readouterr().err


def test_run_codex_auth_unknown_command_returns_1(capsys):
    from omniagents.cli import run_codex_auth_command

    assert run_codex_auth_command("frobnicate") == 1
    assert "Unknown codex command" in capsys.readouterr().err


def _project_with_scenarios(root, scenarios):
    (root / "project.yml").write_text("name: demo\nagents:\n  entrypoint: main\n")
    agent = root / "agents" / "main"
    agent.mkdir(parents=True)
    (agent / "agent.yml").write_text("name: main\ninstructions: Help.\n")
    suite = root / "evaluations" / "scenarios" / "main.yml"
    suite.parent.mkdir(parents=True)
    suite.write_text(json.dumps({"scenarios": scenarios}))


def test_eval_scenarios_holdout_writes_split_files(monkeypatch, tmp_path, capsys):
    import yaml as _yaml

    _project_with_scenarios(
        tmp_path,
        [
            {"name": f"case-{i}", "prompt": "hi", "expect": {"tone": str(i % 2)}}
            for i in range(8)
        ],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omniagents",
            "eval",
            "scenarios",
            "holdout",
            "--project",
            str(tmp_path),
            "--strata",
            "expect.tone",
            "--seed",
            "3",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Holdout: train=4 dev=2 test=2" in out
    split_dir = tmp_path / "evaluations" / "holdout"
    assert (split_dir / "test.yml").is_file() and (split_dir / "manifest.yml").is_file()
    manifest = _yaml.safe_load((split_dir / "manifest.yml").read_text())
    assert manifest["strata"] == ["expect.tone"] and manifest["seed"] == 3


def test_eval_runs_pass_their_selection_to_the_workspace(monkeypatch, tmp_path):
    seen = _fake_workspace(
        monkeypatch, tmp_path, {"runs": [], "metrics": [], "stats": {}}
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omniagents",
            "eval",
            "scenarios",
            "run",
            "--project",
            str(tmp_path),
            "--agent",
            "alpha",
            "--split",
            "test",
            "--select",
            "tag:smoke",
            "--set",
            "b",
            "--reporter",
            "tap",
            "--color",
            "never",
        ],
    )

    try:
        cli_main()
    except SystemExit as exc:  # a passing suite returns; a failing one exits non-zero
        assert exc.code in (0, None)

    assert seen["agent"] == "alpha"
    assert seen["split"] == "test"
    assert seen["select"] == "tag:smoke"
    assert seen["scenario_set"] == "resolved:b"
    # `eval scenarios run` includes unmeasured scenarios by default.
    assert seen["include_unmeasured"] is True


def _reviewed_project(root, monkeypatch, verdicts):
    """A project with one saved run whose answers carry ``verdicts``
    (``(verdict, categories)`` per answer)."""
    from omniagents.core.eval import pipeline
    from omniagents.core.eval.workspace import EvalWorkspace

    _project_with_scenarios(root, [{"name": "suite", "prompt": "p"}])
    evaluation = root / "evaluations" / "evaluation.yml"
    evaluation.write_text("evaluation:\n  failure_categories:\n    - name: tone\n")
    records = [
        {
            "scenario": f"s{i}",
            "scenario_config": {"id": f"s{i}", "name": f"s{i}", "prompt": "p"},
            "measures": [],
            "final_assistant_text": f"reply {i}",
            "trace_group_id": f"g{i}",
        }
        for i in range(len(verdicts))
    ]
    monkeypatch.setattr(
        pipeline,
        "run_evaluation",
        lambda agent_path, **kw: {
            "runs": records,
            "manifest": {},
            "workflow_name": "wf",
        },
    )
    workspace = EvalWorkspace(root)
    run = workspace.run(include_unmeasured=True)
    for i, (verdict, categories) in enumerate(verdicts):
        workspace.save_review(
            f"g{i}", reviewer="ana", verdict=verdict, failure_categories=categories
        )
    return run


def test_judge_dataset_is_built_from_reviews(monkeypatch, tmp_path, capsys):
    _reviewed_project(
        tmp_path,
        monkeypatch,
        [("unacceptable", ["tone"]) if i % 2 else ("acceptable", []) for i in range(8)],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omniagents",
            "judge",
            "dataset",
            "--project",
            str(tmp_path),
            "--category",
            "tone",
            "--train",
            "0.5",
            "--dev",
            "0.25",
            "--test",
            "0.25",
        ],
    )

    cli_main()

    out = capsys.readouterr().out
    assert "train: " in out and "answers" in out
    split_dir = tmp_path / "evaluations" / "judge-datasets" / "tone"
    labels = [
        item["label"]
        for split in ("train", "dev", "test")
        for item in yaml.safe_load((split_dir / f"{split}.yml").read_text())[
            "scenarios"
        ]
    ]
    assert sorted(labels) == [False] * 4 + [True] * 4


def test_eval_baseline_keeps_a_saved_run(monkeypatch, tmp_path, capsys):
    run = _reviewed_project(tmp_path, monkeypatch, [])
    run_id = run["metadata"]["id"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omniagents",
            "eval",
            "baseline",
            "--project",
            str(tmp_path),
            run_id,
            "--name",
            "v1",
        ],
    )

    cli_main()

    assert f"Kept {run_id} as baseline v1" in capsys.readouterr().out
    assert (tmp_path / "evaluations" / "baselines" / "v1" / "results.json").is_file()
