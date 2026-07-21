"""Tests for the `omni` entrypoint — the framework's generic product
console script (`omniagents.product_cli.main`), which resolves
omni-code's packaged project.yml via entry-point discovery
(`omniagents.products` -> `omni_code`) and dispatches through the
product CLI host.

Embedded mode and the models bootstrap are framework fallthrough
conventions now, tested upstream in omniagents. What omni-code owns —
and what these tests cover — is the binding: discovery resolves to this
product with no product-side dispatcher, and the governance preflight
(the whole of `cli_extensions.PREFLIGHT`) runs first on every
invocation.
"""

import sys
from types import SimpleNamespace

import pytest

import omniagents.cli as fw_cli
import omniagents.core.models.cli as fw_models_cli
import omniagents.core.models.governed as fw_governed
import omniagents.core.updater as fw_updater
from omniagents.core.models import install_model_catalog
from omniagents.core.product import product_info
from omniagents.product_cli import main as cli_main

import omni_code.cli_extensions as cli_extensions
import omni_code.governance as governance


@pytest.fixture
def cli_env(monkeypatch):
    """Patch the seams around the host: governance, the model store,
    platform governance, update notice, and the fallthrough run."""
    calls = []
    monkeypatch.setattr(
        governance, "maybe_enforce_sandbox", lambda: calls.append("governance")
    )
    store = install_model_catalog(product_info().manifest_path)
    monkeypatch.setattr(store, "load_config", lambda: calls.append("models_load"))
    monkeypatch.setattr(
        store, "resolve_model_for_runtime", lambda model_name=None: {}
    )
    monkeypatch.setattr(store, "has_models_configured", lambda: True)
    monkeypatch.setattr(
        fw_governed, "apply_platform_model_governance", lambda config_dir=None: None
    )
    monkeypatch.setattr(fw_updater, "maybe_print_update_notice", lambda config: None)

    run = {}
    monkeypatch.setattr(
        fw_cli,
        "main",
        lambda: (calls.append("run"), run.update({"argv": list(sys.argv)})),
    )
    return SimpleNamespace(calls=calls, run=run, monkeypatch=monkeypatch)


def test_cli_main_discovers_product_and_forces_prompt_with_double_dash(cli_env):
    assert cli_main(["--", "work", "on", "this"]) == 0

    argv = cli_env.run["argv"]
    assert argv[:5] == ["omniagents", "run", "--project", argv[3], "--mode"]
    # Discovery alone resolved the packaged manifest — no product cli.py.
    assert argv[3].endswith("omni_code/project.yml")
    assert argv[5] == "ink"
    assert argv[-3:] == ["work", "on", "this"]


def test_cli_main_top_level_help_resolves_prog_omni(cli_env, capsys):
    assert cli_main(["--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: omni ")
    assert "omni -- <words...>" in out
    assert "Force prompt mode" in out
    # Product commands from cli_extensions are mounted in the help.
    assert "omni sessions ..." in out
    assert "omni update ..." in out
    # The deprecated Docker-sandbox CLI is gone.
    assert "omni sandbox ..." not in out


def test_governance_is_the_only_preflight():
    """After the framework lifted embedded + models handling, omni-code's
    PREFLIGHT is exactly the governance sandbox re-exec — the security
    boundary, ahead of every dispatch."""
    assert cli_extensions.PREFLIGHT == [cli_extensions.governance_preflight]


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
    # Governance first; the framework models bootstrap self-gates off for
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


def test_prompt_words_pass_through_to_run(cli_env):
    assert cli_main(["make", "it", "faster"]) == 0
    assert cli_env.run["argv"][-3:] == ["make", "it", "faster"]
