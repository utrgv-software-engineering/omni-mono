import json
import sys
import types
from pathlib import Path

import pytest

from omniagents.cli import main as cli_main


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

    module = types.ModuleType("omniagents.core.project.compiler")

    def fake_compile_project(proj_path, select="", exclude="", agent_key=None):
        return str(compiled_path), {"nodes": ["a"]}

    module.compile_project = fake_compile_project
    monkeypatch.setitem(sys.modules, "omniagents.core.project.compiler", module)

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
    assert "nodes" in output


def test_studio_serve_uses_manifest(monkeypatch, tmp_path):
    captured = {}
    tracing_module = types.ModuleType("omniagents.studio.tracing")

    def fake_serve(**kwargs):
        captured.update(kwargs)

    tracing_module.serve = fake_serve
    tracing_module.export = lambda *args, **kwargs: None
    tracing_module.show = lambda *args, **kwargs: None
    studio_pkg = types.ModuleType("omniagents.studio")
    studio_pkg.tracing = tracing_module
    monkeypatch.setitem(sys.modules, "omniagents.studio", studio_pkg)
    monkeypatch.setitem(sys.modules, "omniagents.studio.tracing", tracing_module)

    manifest = tmp_path / "project.yml"
    manifest.write_text("tracing:\n  project: demo\n", encoding="utf-8")

    argv = [
        "omniagents",
        "studio",
        "serve",
        "--project",
        str(manifest),
        "--host",
        "127.0.0.1",
        "--port",
        "9999",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert captured["project"] == "demo"
    assert captured["manifest_path"] == str(manifest)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9999
    assert captured["dev_host"] == "127.0.0.1"


def test_studio_export_requires_project(monkeypatch, tmp_path, capsys):
    tracing_module = types.ModuleType("omniagents.studio.tracing")
    tracing_module.serve = lambda **kwargs: None
    tracing_module.export = lambda *args, **kwargs: None
    tracing_module.show = lambda *args, **kwargs: None
    studio_pkg = types.ModuleType("omniagents.studio")
    studio_pkg.tracing = tracing_module
    monkeypatch.setitem(sys.modules, "omniagents.studio", studio_pkg)
    monkeypatch.setitem(sys.modules, "omniagents.studio.tracing", tracing_module)

    argv = [
        "omniagents",
        "studio",
        "export",
        str(tmp_path / "export.json"),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 2
    captured = capsys.readouterr().out
    assert "Provide --project" in captured


def test_studio_export_invokes_backend(monkeypatch, tmp_path):
    captured = {}
    tracing_module = types.ModuleType("omniagents.studio.tracing")

    def fake_export(output, format="json", project=None):
        captured["output"] = output
        captured["format"] = format
        captured["project"] = project

    tracing_module.serve = lambda **kwargs: None
    tracing_module.export = fake_export
    tracing_module.show = lambda *args, **kwargs: None
    studio_pkg = types.ModuleType("omniagents.studio")
    studio_pkg.tracing = tracing_module
    monkeypatch.setitem(sys.modules, "omniagents.studio", studio_pkg)
    monkeypatch.setitem(sys.modules, "omniagents.studio.tracing", tracing_module)

    target = tmp_path / "export.json"
    argv = [
        "omniagents",
        "studio",
        "export",
        str(target),
        "--project",
        "demo",
        "--format",
        "csv",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert captured["output"] == str(target)
    assert captured["project"] == "demo"
    assert captured["format"] == "csv"


def test_studio_show_invokes_backend(monkeypatch, tmp_path):
    called = {}
    tracing_module = types.ModuleType("omniagents.studio.tracing")

    def fake_show(project=None):
        called["project"] = project

    tracing_module.serve = lambda **kwargs: None
    tracing_module.export = lambda *args, **kwargs: None
    tracing_module.show = fake_show
    studio_pkg = types.ModuleType("omniagents.studio")
    studio_pkg.tracing = tracing_module
    monkeypatch.setitem(sys.modules, "omniagents.studio", studio_pkg)
    monkeypatch.setitem(sys.modules, "omniagents.studio.tracing", tracing_module)

    argv = [
        "omniagents",
        "studio",
        "show",
        "--project",
        "demo",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    assert called["project"] == "demo"


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


def test_eval_suite_run_tap_reporter(monkeypatch, tmp_path, capsys):
    compiled_path = tmp_path / "compiled.yml"
    compiled_path.write_text(
        """
tracing:
  name: demo
scenarios:
  - id: case1
    name: Case One
  - id: case2
    name: Case Two
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    compiler_module = types.ModuleType("omniagents.core.project.compiler")

    def fake_compile_project(proj_path, select="", exclude="", agent_key=None):
        return str(compiled_path), {"graph": []}

    compiler_module.compile_project = fake_compile_project
    monkeypatch.setitem(
        sys.modules, "omniagents.core.project.compiler", compiler_module
    )

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

    pipeline_module = types.ModuleType("omniagents.core.eval.pipeline")

    def fake_run_evaluation(
        path,
        tracing_cfg=None,
        workflow_base_name=None,
        include_unmeasured=False,
        progress_cb=None,
    ):
        if progress_cb:
            progress_cb({"event": "progress", "skipped": False, "failed": 0})
            progress_cb({"event": "progress", "skipped": True, "failed": 1})
        return data

    pipeline_module.run_evaluation = fake_run_evaluation
    monkeypatch.setitem(sys.modules, "omniagents.core.eval.pipeline", pipeline_module)

    output_path = tmp_path / "results.json"

    argv = [
        "omniagents",
        "eval",
        "suite",
        "run",
        "--project",
        "proj.yml",
        "--output",
        str(output_path),
        "--reporter",
        "tap",
        "--color",
        "never",
        "--name",
        "demo",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 1
    captured = capsys.readouterr().out
    assert "TAP version 13" in captured
    assert output_path.is_file()


def test_eval_suite_run_pretty_reporter(monkeypatch, tmp_path, capsys):
    compiled_path = tmp_path / "compiled.yml"
    compiled_path.write_text(
        """
tracing:
  name: demo
evaluation:
  output: alt.json
scenarios:
  - id: case1
    name: Case One
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    compiler_module = types.ModuleType("omniagents.core.project.compiler")

    def fake_compile_project(proj_path, select="", exclude="", agent_key=None):
        return str(compiled_path), {"graph": []}

    compiler_module.compile_project = fake_compile_project
    monkeypatch.setitem(
        sys.modules, "omniagents.core.project.compiler", compiler_module
    )

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

    pipeline_module = types.ModuleType("omniagents.core.eval.pipeline")

    def fake_run_evaluation(
        path,
        tracing_cfg=None,
        workflow_base_name=None,
        include_unmeasured=False,
        progress_cb=None,
    ):
        return data

    pipeline_module.run_evaluation = fake_run_evaluation
    monkeypatch.setitem(sys.modules, "omniagents.core.eval.pipeline", pipeline_module)

    argv = [
        "omniagents",
        "eval",
        "suite",
        "run",
        "--project",
        "proj.yml",
        "--color",
        "never",
        "--reporter",
        "pretty",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    captured = capsys.readouterr().out
    assert "Running" in captured
    assert "Passed:" in captured
    assert (compiled_path.parent / "alt.json").is_file()


def test_eval_suite_run_pretty_verbose(monkeypatch, tmp_path, capsys):
    compiled_path = tmp_path / "compiled.yml"
    compiled_path.write_text(
        """
tracing:
  name: demo
scenarios:
  - id: case1
    name: Case One
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    compiler_module = types.ModuleType("omniagents.core.project.compiler")

    def fake_compile_project(proj_path, select="", exclude="", agent_key=None):
        return str(compiled_path), {"graph": []}

    compiler_module.compile_project = fake_compile_project
    monkeypatch.setitem(
        sys.modules, "omniagents.core.project.compiler", compiler_module
    )

    data = {
        "runs": [
            {
                "scenario": "case1",
                "scenario_config": {"id": "case1", "name": "Case One"},
                "measures": [
                    {"name": "accuracy", "passed": True},
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
            }
        ],
        "stats": {
            "total_scenarios": 1,
            "included_scenarios": 1,
            "skipped_unmeasured": 0,
            "unknown_measures": {"mystery": 2},
            "measures_discovered_count": 12,
            "measures_discovered": [
                "m1",
                "m2",
                "m3",
                "m4",
                "m5",
                "m6",
                "m7",
                "m8",
                "m9",
                "m10",
                "m11",
                "m12",
            ],
        },
    }

    pipeline_module = types.ModuleType("omniagents.core.eval.pipeline")

    def fake_run_evaluation(
        path,
        tracing_cfg=None,
        workflow_base_name=None,
        include_unmeasured=False,
        progress_cb=None,
    ):
        if progress_cb:
            progress_cb({"event": "progress", "skipped": False, "failed": 0})
        return data

    pipeline_module.run_evaluation = fake_run_evaluation
    monkeypatch.setitem(sys.modules, "omniagents.core.eval.pipeline", pipeline_module)

    argv = [
        "omniagents",
        "eval",
        "suite",
        "run",
        "--project",
        "proj.yml",
        "--color",
        "always",
        "--reporter",
        "pretty",
        "-v",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    captured = capsys.readouterr().out
    assert "Discovered measures" in captured
    assert "unknown measures" in captured


def test_eval_scenarios_generate(monkeypatch, tmp_path, capsys):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project_file = project_dir / "project.yml"
    project_file.write_text("name: demo\n", encoding="utf-8")
    compiled_path = tmp_path / "compiled.yml"
    compiled_path.write_text("tracing:\n  provider: other\n", encoding="utf-8")

    compiler_module = types.ModuleType("omniagents.core.project.compiler")

    def fake_compile_project(proj_path, select="", exclude="", agent_key=None):
        return str(compiled_path), {"graph": []}

    compiler_module.compile_project = fake_compile_project
    monkeypatch.setitem(
        sys.modules, "omniagents.core.project.compiler", compiler_module
    )

    async def fake_generate(
        compiled,
        count=None,
        seed=None,
        set_name=None,
        output=None,
        workflow_name=None,
        progress_cb=None,
    ):
        if progress_cb:
            progress_cb({"event": "start", "target": count or 1})
            progress_cb({"event": "progress"})
            progress_cb({"event": "done", "count": count or 1})
        out_path = output or compiled.parent / "generated.yml"
        return ([{"id": "case1"}], out_path, "ts")

    scenarios_module = types.ModuleType("omniagents.core.eval.scenarios")
    scenarios_module.generate_scenarios = fake_generate
    monkeypatch.setitem(sys.modules, "omniagents.core.eval.scenarios", scenarios_module)

    argv = [
        "omniagents",
        "eval",
        "scenarios",
        "generate",
        "--project",
        str(project_file),
        "--name",
        "demo-set",
        "--count",
        "1",
        "--color",
        "never",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    captured = capsys.readouterr().out
    assert "Wrote 1 scenarios" in captured


def test_eval_scenarios_annotate(monkeypatch, tmp_path, capsys):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project_file = project_dir / "project.yml"
    project_file.write_text("name: demo\n", encoding="utf-8")
    compiled_path = tmp_path / "compiled.yml"
    compiled_path.write_text("tracing:\n  project: trace-demo\n", encoding="utf-8")

    scenario_dir = project_dir / "artifacts" / "eval" / "demo-set"
    scenario_dir.mkdir(parents=True)
    scenario_file = scenario_dir / "generated_scenarios.yml"
    scenario_file.write_text(
        "scenarios:\n  - id: case1\n    prompt: hi\n", encoding="utf-8"
    )

    compiler_module = types.ModuleType("omniagents.core.project.compiler")

    def fake_compile_project(proj_path, select="", exclude="", agent_key=None):
        return str(compiled_path), {"graph": []}

    compiler_module.compile_project = fake_compile_project
    monkeypatch.setitem(
        sys.modules, "omniagents.core.project.compiler", compiler_module
    )

    runtime_module = types.ModuleType("omniagents.core.project.runtime")

    def fake_load_project(path):
        return types.SimpleNamespace(
            tracing_defaults={"project": "trace-demo"}, root=project_dir
        )

    def fake_resolve_agent(ctx_obj, agent_key=None):
        agent_path = project_dir / "agents" / "demo" / "agent.yml"
        agent_path.parent.mkdir(parents=True, exist_ok=True)
        agent_path.write_text("name: demo\n", encoding="utf-8")
        return (agent_key or "demo", agent_path)

    runtime_module.load_project = fake_load_project
    runtime_module.resolve_agent = fake_resolve_agent
    monkeypatch.setitem(sys.modules, "omniagents.core.project.runtime", runtime_module)

    annotations = [{"id": "case1", "label": "ok"}]
    annotate_calls = {}

    def fake_annotate(agent_path, scen_path, studio_project, _extra):
        annotate_calls["agent"] = agent_path
        annotate_calls["scenario"] = scen_path
        annotate_calls["project"] = studio_project
        return annotations, [], scen_path

    def fake_write_output(root, source, annotated):
        annotate_calls["root"] = root
        annotate_calls["source"] = source
        annotate_calls["annotated"] = annotated
        out_path = project_dir / "annotated.yml"
        out_path.write_text("done", encoding="utf-8")
        return out_path

    monkeypatch.setattr("omniagents.cli.annotate_scenarios", fake_annotate)
    monkeypatch.setattr("omniagents.cli.write_annotation_output", fake_write_output)

    argv = [
        "omniagents",
        "eval",
        "scenarios",
        "annotate",
        "--project",
        str(project_file),
        "--name",
        "demo-set",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        cli_main()

    assert exc.value.code == 0
    assert annotate_calls["project"] == "trace-demo"
    assert annotate_calls["annotated"] == annotations


def test_eval_scenarios_run(monkeypatch, tmp_path, capsys):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    project_file = project_dir / "project.yml"
    project_file.write_text("name: demo\n", encoding="utf-8")
    compiled_path = tmp_path / "compiled.yml"
    compiled_path.write_text("tracing:\n  provider: other\n", encoding="utf-8")

    scenario_file = project_dir / "scenarios.yml"
    scenario_file.write_text(
        "scenarios:\n  - id: case1\n    prompt: hi\n", encoding="utf-8"
    )

    compiler_module = types.ModuleType("omniagents.core.project.compiler")

    def fake_compile_project(proj_path, select="", exclude="", agent_key=None):
        return str(compiled_path), {"graph": []}

    compiler_module.compile_project = fake_compile_project
    monkeypatch.setitem(
        sys.modules, "omniagents.core.project.compiler", compiler_module
    )

    async def fake_run(
        compiled,
        scenarios,
        max_turns=None,
        include_history=False,
        workflow_name=None,
        progress_cb=None,
        scenario_source_path=None,
    ):
        if progress_cb:
            progress_cb({"event": "start", "total": len(scenarios)})
            for _ in scenarios:
                progress_cb({"event": "progress"})
            progress_cb({"event": "done", "count": len(scenarios)})
        return [{"id": scenarios[0]["id"], "result": "ok"}]

    multi_turn_module = types.ModuleType("omniagents.core.eval.multi_turn")
    multi_turn_module.run_scenarios_against_agent = fake_run
    monkeypatch.setitem(
        sys.modules, "omniagents.core.eval.multi_turn", multi_turn_module
    )

    argv = [
        "omniagents",
        "eval",
        "scenarios",
        "run",
        "--project",
        str(project_file),
        "--input",
        str(scenario_file),
        "--color",
        "never",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_main()

    captured = capsys.readouterr().out
    assert "Ran 1 scenarios" in captured
    runs_path = scenario_file.parent / "generated_runs.json"
    payload = json.loads(runs_path.read_text(encoding="utf-8"))
    assert payload["runs"][0]["result"] == "ok"


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
