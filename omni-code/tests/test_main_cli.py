"""Tests for the `omni` entrypoint — the framework's generic product
console script (`omniagents.product_cli.main`), which resolves
omni-code's packaged project.yml via entry-point discovery
(`omniagents.products` -> `omni_code`) and dispatches through the
product CLI host.

Embedded mode, the models bootstrap, and the summarizer/sessions
conventions are framework behavior, tested upstream in omniagents.
What omni-code owns — and what these tests cover — is the binding:
discovery resolves to this product with no product-side dispatcher or
extensions module at all.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import omniagents.cli as fw_cli
import omniagents.core.models.cli as fw_models_cli
import omniagents.core.updater as fw_updater
import pytest
from omniagents.core.models import install_model_catalog
from omniagents.core.product import product_info
from omniagents.product_cli import main as cli_main


@pytest.fixture
def cli_env(monkeypatch):
    """Patch the seams around the host: the model store, update notice,
    and the fallthrough run."""
    calls = []
    store = install_model_catalog(product_info().manifest_path)
    monkeypatch.setattr(store, "load_config", lambda: calls.append("models_load"))
    monkeypatch.setattr(store, "resolve_model_for_runtime", lambda model_name=None: {})
    monkeypatch.setattr(store, "has_models_configured", lambda: True)
    monkeypatch.setattr(fw_updater, "maybe_print_update_notice", lambda config: None)

    run = {}

    def fake_run():
        calls.append("run")
        run.update({"argv": list(sys.argv)})

    monkeypatch.setattr(fw_cli, "main", fake_run)
    return SimpleNamespace(calls=calls, run=run, monkeypatch=monkeypatch)


def test_cli_main_discovers_product_and_forces_prompt_with_double_dash(cli_env):
    assert cli_main(["--", "work", "on", "this"]) == 0

    argv = cli_env.run["argv"]
    assert argv[:5] == ["omniagents", "run", "--project", argv[3], "--mode"]
    # Discovery alone resolved the packaged manifest — no product cli.py.
    assert Path(argv[3]).parts[-2:] == ("omni_code", "project.yml")
    assert argv[5] == "ink"
    assert argv[-3:] == ["work", "on", "this"]


def test_cli_main_top_level_help_resolves_prog_omni(cli_env, capsys):
    assert cli_main(["--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: omni ")
    assert "omni -- <words...>" in out
    assert "Force prompt mode" in out
    assert "omni sessions ..." in out
    assert "omni update ..." in out
    # The deprecated Docker-sandbox CLI is gone.
    assert "omni sandbox ..." not in out


def test_fallthrough_runs_models_bootstrap_then_run(cli_env):
    assert cli_main(["fix", "this"]) == 0
    assert cli_env.calls == ["models_load", "run"]


def test_reserved_dispatch_skips_models_bootstrap(cli_env):
    cli_env.monkeypatch.setattr(
        fw_models_cli,
        "run_model_command",
        lambda argv, **kw: cli_env.calls.append("model_cmd"),
    )

    assert cli_main(["model", "list"]) == 0
    # The framework models bootstrap self-gates off for subcommand
    # dispatch (no wizard, no config load).
    assert cli_env.calls == ["model_cmd"]


def test_reserved_commands_skip_models_bootstrap(cli_env):
    captured = {}
    cli_env.monkeypatch.setattr(
        fw_cli,
        "run_codex_auth_command",
        lambda command, **kw: captured.update({"command": command}) or 0,
    )

    assert cli_main(["auth", "codex", "status"]) == 0
    assert captured["command"] == "status"
    assert cli_env.calls == []


def test_prompt_words_pass_through_to_run(cli_env):
    assert cli_main(["make", "it", "faster"]) == 0
    assert cli_env.run["argv"][-3:] == ["make", "it", "faster"]
