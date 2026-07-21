"""Tests for the `omni` entrypoint — the framework's product CLI host
(`omniagents.product_cli.product_main`) driven by the packaged
project.yml, with omni-code's behavior supplied by
`omni_code.cli_extensions` (PREFLIGHT + COMMANDS).
"""

import os
import sys
from types import SimpleNamespace

import pytest

import omniagents.cli as fw_cli
import omniagents.core.models.cli as fw_models_cli
import omniagents.core.updater as fw_updater

import omni_code.cli_extensions as cli_extensions
import omni_code.governance as governance
import omni_code.platform_models as platform_models
from omni_code.cli import cli_main
from omniagents.core.models import model_store


@pytest.fixture(autouse=True)
def _clean_cli_env(monkeypatch):
    monkeypatch.setattr(cli_extensions, "_embedded_mode", False)
    yield
    os.environ.pop("OMNI_WEB_AUTO_OPEN", None)
    os.environ.pop("OMNI_CODE_UPDATE_CHECK", None)


@pytest.fixture
def cli_env(monkeypatch):
    """Patch the seams around the host: governance, model bootstrap,
    update notice, and the fallthrough `omniagents run`."""
    calls = []
    monkeypatch.setattr(
        governance, "maybe_enforce_sandbox", lambda: calls.append("governance")
    )
    store = model_store()
    monkeypatch.setattr(store, "load_config", lambda: calls.append("models_load"))
    monkeypatch.setattr(
        store, "resolve_model_for_runtime", lambda model_name=None: {}
    )
    monkeypatch.setattr(store, "has_models_configured", lambda: True)
    monkeypatch.setattr(
        platform_models, "apply_platform_model_governance", lambda: None
    )
    monkeypatch.setattr(fw_updater, "maybe_print_update_notice", lambda config: None)

    run = {}
    monkeypatch.setattr(
        fw_cli,
        "main",
        lambda: (calls.append("run"), run.update({"argv": list(sys.argv)})),
    )
    return SimpleNamespace(calls=calls, run=run, monkeypatch=monkeypatch)


def test_cli_main_force_prompt_mode_with_double_dash(cli_env):
    assert cli_main(["--", "work", "on", "this"]) == 0

    argv = cli_env.run["argv"]
    assert argv[:5] == ["omniagents", "run", "--project", argv[3], "--mode"]
    assert argv[3].endswith("project.yml")
    assert argv[5] == "ink"
    assert argv[-3:] == ["work", "on", "this"]


def test_cli_main_top_level_help_mentions_force_prompt(cli_env, capsys):
    assert cli_main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "omni -- <words...>" in out
    assert "Force prompt mode" in out
    # Product commands from cli_extensions are mounted in the help.
    assert "omni sandbox ..." in out
    assert "omni update ..." in out


def test_cli_main_embedded_web_forces_json_output(cli_env):
    assert cli_main(["--embedded", "--mode", "web", "--ui-minimal"]) == 0

    assert os.environ["OMNI_WEB_AUTO_OPEN"] == "false"
    argv = cli_env.run["argv"]
    assert "--embedded" not in argv
    assert argv[argv.index("--output") + 1] == "json"


def test_cli_main_embedded_web_preserves_explicit_output(cli_env):
    assert cli_main(["--embedded", "--mode", "web", "--output", "text"]) == 0

    argv = cli_env.run["argv"]
    assert argv[argv.index("--output") + 1] == "text"


def test_cli_main_embedded_suppresses_update_notice(cli_env):
    notices = []
    cli_env.monkeypatch.setattr(
        fw_updater, "maybe_print_update_notice", lambda config: notices.append(config)
    )

    assert cli_main(["--embedded"]) == 0
    # The embedded preflight disables the notice via the product env knob
    # the framework's throttle already honors.
    assert os.environ["OMNI_CODE_UPDATE_CHECK"] == "0"


def test_cli_main_embedded_no_models_emits_json_error(cli_env, capsys):
    cli_env.monkeypatch.setattr(model_store(), "has_models_configured", lambda: False)

    with pytest.raises(SystemExit) as exc:
        cli_main(["--embedded"])

    assert exc.value.code == 1
    assert '"error"' in capsys.readouterr().out


def test_governance_preflight_is_first():
    """The security boundary: governance is the first preflight, ahead of
    embedded/models handling and every dispatch."""
    assert cli_extensions.PREFLIGHT[0] is cli_extensions.governance_preflight


def test_governance_runs_before_fallthrough_and_models_bootstrap(cli_env):
    assert cli_main(["fix", "this"]) == 0
    assert cli_env.calls == ["governance", "models_load", "run"]


def test_governance_runs_before_reserved_dispatch(cli_env):
    cli_env.monkeypatch.setattr(
        fw_models_cli,
        "run_model_command",
        lambda argv, **kw: cli_env.calls.append("model_cmd"),
    )

    assert cli_main(["model", "list"]) == 0
    # Governance first; the models-bootstrap preflight self-gates off for
    # subcommand dispatch (no wizard, no config load).
    assert cli_env.calls == ["governance", "model_cmd"]


def test_reserved_commands_skip_models_bootstrap(cli_env):
    captured = {}
    cli_env.monkeypatch.setattr(
        fw_cli,
        "run_platform_auth_command",
        lambda argv, **kw: captured.update({"argv": list(argv)}) or 0,
    )

    assert cli_main(["auth", "status"]) == 0
    assert captured["argv"] == ["status"]
    assert cli_env.calls == ["governance"]


def test_models_wizard_runs_when_unconfigured(cli_env, capsys):
    cli_env.monkeypatch.setattr(model_store(), "has_models_configured", lambda: False)
    cli_env.monkeypatch.setattr(
        fw_models_cli,
        "run_model_command",
        lambda argv, **kw: cli_env.calls.append(("wizard", list(argv))),
    )

    assert cli_main(["hello"]) == 0
    assert ("wizard", ["setup"]) in cli_env.calls
    assert cli_env.calls[-1] == "run"
    assert "Starting setup wizard" in capsys.readouterr().out


def test_prompt_words_pass_through_to_run(cli_env):
    assert cli_main(["make", "it", "faster"]) == 0
    assert cli_env.run["argv"][-3:] == ["make", "it", "faster"]
