import sys
from types import ModuleType, SimpleNamespace


def test_cli_main_dispatches_work(monkeypatch):
    from omni_code import cli as main_mod

    called = {}
    fake_module = ModuleType("omni_code.work_cli")
    fake_module.main = lambda argv=None: called.update({"argv": argv})
    monkeypatch.setitem(sys.modules, "omni_code.work_cli", fake_module)

    main_mod.cli_main(["work", "status"])
    assert called["argv"] == ["status"]


def test_cli_main_dispatches_projects(monkeypatch):
    from omni_code import cli as main_mod

    called = {}
    fake_module = ModuleType("omni_code.projects_cli")
    fake_module.main = lambda argv=None: called.update({"argv": argv})
    monkeypatch.setitem(sys.modules, "omni_code.projects_cli", fake_module)

    main_mod.cli_main(["projects", "list"])
    assert called["argv"] == ["list"]


def test_cli_main_force_prompt_mode_with_double_dash(monkeypatch):
    from omni_code import cli as main_mod

    monkeypatch.setattr(main_mod, "_load_models_config", lambda: None)

    fake_models = ModuleType("omni_code.models")
    fake_models.has_models_configured = lambda: True
    monkeypatch.setitem(sys.modules, "omni_code.models", fake_models)

    fake_updater = ModuleType("omni_code.updater")
    fake_updater.maybe_print_update_notice = lambda: None
    monkeypatch.setitem(sys.modules, "omni_code.updater", fake_updater)

    fake_proxy = ModuleType("omni_code.proxy")
    fake_proxy.maybe_start_proxy = lambda _project_path: None
    monkeypatch.setitem(sys.modules, "omni_code.proxy", fake_proxy)

    called = {}
    fake_omniagents_cli = ModuleType("omniagents.cli")
    fake_omniagents_cli.main = lambda: called.update({"argv": list(sys.argv)})
    monkeypatch.setitem(sys.modules, "omniagents.cli", fake_omniagents_cli)

    main_mod.cli_main(["--", "work", "on", "this"])

    assert called["argv"][:5] == ["omniagents", "run", "--project", called["argv"][3], "--mode"]
    assert called["argv"][-3:] == ["work", "on", "this"]


def test_cli_main_top_level_help_mentions_force_prompt(capsys):
    from omni_code import cli as main_mod

    main_mod.cli_main(["--help"])
    out = capsys.readouterr().out
    assert "omni -- <words...>" in out
    assert "Force prompt mode" in out
