import os
import sys
from types import ModuleType, SimpleNamespace


def test_cli_main_force_prompt_mode_with_double_dash(monkeypatch):
    from omni_code import cli as main_mod

    monkeypatch.setattr(main_mod, "_load_models_config", lambda: None)

    fake_models = ModuleType("omni_code.models")
    fake_models.has_models_configured = lambda: True
    monkeypatch.setitem(sys.modules, "omni_code.models", fake_models)

    monkeypatch.setattr(main_mod, "_print_update_notice", lambda: None)

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


def test_cli_main_embedded_web_forces_json_output(monkeypatch):
    from omni_code import cli as main_mod

    monkeypatch.setattr(main_mod, "_load_models_config", lambda: None)

    fake_models = ModuleType("omni_code.models")
    fake_models.has_models_configured = lambda: True
    monkeypatch.setitem(sys.modules, "omni_code.models", fake_models)

    fake_proxy = ModuleType("omni_code.proxy")
    fake_proxy.maybe_start_proxy = lambda _project_path: None
    monkeypatch.setitem(sys.modules, "omni_code.proxy", fake_proxy)

    called = {}
    fake_omniagents_cli = ModuleType("omniagents.cli")
    fake_omniagents_cli.main = lambda: called.update({"argv": list(sys.argv)})
    monkeypatch.setitem(sys.modules, "omniagents.cli", fake_omniagents_cli)

    main_mod.cli_main(["--embedded", "--mode", "web", "--ui-minimal"])

    assert os.environ["OMNI_WEB_AUTO_OPEN"] == "false"
    assert "--embedded" not in called["argv"]
    assert "--output" in called["argv"]
    output_index = called["argv"].index("--output")
    assert called["argv"][output_index + 1] == "json"


def test_cli_main_embedded_web_preserves_explicit_output(monkeypatch):
    from omni_code import cli as main_mod

    monkeypatch.setattr(main_mod, "_load_models_config", lambda: None)

    fake_models = ModuleType("omni_code.models")
    fake_models.has_models_configured = lambda: True
    monkeypatch.setitem(sys.modules, "omni_code.models", fake_models)

    fake_proxy = ModuleType("omni_code.proxy")
    fake_proxy.maybe_start_proxy = lambda _project_path: None
    monkeypatch.setitem(sys.modules, "omni_code.proxy", fake_proxy)

    called = {}
    fake_omniagents_cli = ModuleType("omniagents.cli")
    fake_omniagents_cli.main = lambda: called.update({"argv": list(sys.argv)})
    monkeypatch.setitem(sys.modules, "omniagents.cli", fake_omniagents_cli)

    main_mod.cli_main(["--embedded", "--mode", "web", "--output", "text"])

    output_index = called["argv"].index("--output")
    assert called["argv"][output_index + 1] == "text"
