"""Tests for the framework-owned product CLI host (omniagents.product_cli).

Covers: dispatch to each reserved command, update-block gating,
extensions PREFLIGHT ordering + argv mutation, COMMANDS override +
delegate-to-default, fallthrough arg passthrough + ``--`` escape, and
prog rendering from the product identity.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from textwrap import dedent

import pytest

import omniagents.cli as fw_cli
import omniagents.core.models.cli as models_cli
import omniagents.core.session.cli as sessions_cli
import omniagents.core.updater as fw_updater
from omniagents.core.product import reset_product_info
from omniagents.product_cli import (
    dispatches_to_command,
    load_product_cli_config,
    product_main,
    run_product_sessions_command,
)


@pytest.fixture(autouse=True)
def _fresh_identity():
    reset_product_info()
    yield
    reset_product_info()


@pytest.fixture(autouse=True)
def _fresh_model_store():
    import omniagents.core.models as models_pkg

    before = models_pkg._ACTIVE_STORE
    yield
    models_pkg._ACTIVE_STORE = before


ACME_PRODUCT = """
product:
  name: acme-code
  prog: acme
  label: Acme Code
  slug: acme_code
"""

ACME_UPDATE = """\
  update:
    index_url: https://pypi.example.test/acme/acme-code/
    command_hint: acme update
"""


def _write_manifest(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "project.yml"
    p.write_text("name: acme_code\n" + dedent(body))
    return p


def _capture_run(monkeypatch):
    """Capture the argv the fallthrough hands to `omniagents run`."""
    called = {}
    monkeypatch.setattr(fw_cli, "main", lambda: called.update({"argv": list(sys.argv)}))
    return called


def _make_extensions(monkeypatch, name="fake_product_cli_ext", **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return name


# ---------------------------------------------------------------------------
# Reserved-command dispatch
# ---------------------------------------------------------------------------


def test_reserved_model_dispatch_uses_installed_store(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)

    from omniagents.core.models import ModelConfigStore, set_model_store

    store = ModelConfigStore(config_dir=lambda: tmp_path / "cfg")
    set_model_store(store)

    called = {}
    monkeypatch.setattr(
        models_cli,
        "run_model_command",
        lambda argv, *, store, **kw: called.update({"argv": argv, "store": store}),
    )

    assert product_main(manifest, ["model", "list", "--json"]) == 0
    assert called["argv"] == ["list", "--json"]
    assert called["store"] is store


def test_reserved_model_dispatch_falls_back_to_generic_store(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)

    called = {}
    monkeypatch.setattr(
        models_cli,
        "run_model_command",
        lambda argv, *, store, **kw: called.update({"store": store}),
    )

    assert product_main(manifest, ["model", "list"]) == 0
    # The generic store is rooted at the product config dir and installed
    # process-wide.
    from omniagents.core.models import model_store

    assert called["store"] is model_store()
    assert called["store"].config_path().name == "models.json"


def test_reserved_provider_dispatch(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)

    called = {}
    monkeypatch.setattr(
        models_cli,
        "run_provider_command",
        lambda argv, *, store, **kw: called.update({"argv": argv}),
    )

    assert product_main(manifest, ["provider", "list"]) == 0
    assert called["argv"] == ["list"]


def test_reserved_auth_platform_dispatch(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)

    called = {}

    def fake_platform_auth(argv, **kw):
        called["argv"] = list(argv)
        return 3

    monkeypatch.setattr(fw_cli, "run_platform_auth_command", fake_platform_auth)

    assert product_main(manifest, ["auth", "status"]) == 3
    assert called["argv"] == ["status"]


def test_reserved_auth_codex_passthrough(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)

    called = {}

    def fake_codex(command, **kw):
        called["command"] = command
        return 0

    monkeypatch.setattr(fw_cli, "run_codex_auth_command", fake_codex)

    assert product_main(manifest, ["auth", "codex", "status"]) == 0
    assert called["command"] == "status"
    # Bare `auth codex` defaults to login.
    assert product_main(manifest, ["auth", "codex"]) == 0
    assert called["command"] == "login"


def test_reserved_auth_help_renders_prog(tmp_path, capsys):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)

    assert product_main(manifest, ["auth"]) == 0
    out = capsys.readouterr().out
    assert "usage: acme auth <command>" in out
    assert "codex <cmd>" in out


def test_reserved_sessions_pins_project_and_entrypoint_agent(tmp_path, monkeypatch):
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + """
agents:
  entrypoint: main_agent
""",
    )

    captured = {}

    def fake_handle(args):
        captured["project"] = args.project
        captured["agent"] = args.agent
        return 0

    monkeypatch.setattr(sessions_cli, "handle_sessions_command", fake_handle)

    assert product_main(manifest, ["sessions", "list"]) == 0
    assert captured["project"] == "acme_code"
    assert captured["agent"] == "main_agent"


def test_sessions_explicit_agent_flag_wins(tmp_path, monkeypatch):
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + """
agents:
  entrypoint: main_agent
""",
    )

    captured = {}
    monkeypatch.setattr(
        sessions_cli,
        "handle_sessions_command",
        lambda args: captured.update({"agent": args.agent}) or 0,
    )

    assert product_main(manifest, ["sessions", "--agent", "other", "list"]) == 0
    assert captured["agent"] == "other"


def test_run_product_sessions_command_pins_envelope_and_wraps_runtime_error(
    tmp_path, monkeypatch, capsys
):
    from omniagents.core.product import bootstrap_product

    manifest = _write_manifest(tmp_path, ACME_PRODUCT)
    bootstrap_product(manifest)

    captured = {}

    def fake_handle(args):
        captured["envelope"] = args.summary_envelope
        raise RuntimeError("No model configured. Run: acme model setup")

    monkeypatch.setattr(sessions_cli, "handle_sessions_command", fake_handle)

    rc = run_product_sessions_command(
        ["summarize", "s1"], summary_envelope={"summary": "", "files": []}
    )
    assert rc == 1
    assert captured["envelope"] == {"summary": "", "files": []}
    assert "No model configured" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Update gating
# ---------------------------------------------------------------------------


def test_update_command_mounted_when_declared(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT + ACME_UPDATE)

    called = {}

    def fake_update(argv, **kw):
        called["argv"] = list(argv)
        return 1

    monkeypatch.setattr(fw_updater, "run_update_command", fake_update)

    assert product_main(manifest, ["update", "--check"]) == 1
    assert called["argv"] == ["--check"]


def test_update_command_falls_through_without_update_block(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)
    called = _capture_run(monkeypatch)

    assert product_main(manifest, ["update"]) == 0
    # Not mounted: "update" is an ordinary prompt word.
    assert called["argv"] == [
        "omniagents", "run", "--project", str(manifest), "--mode", "ink", "update",
    ]


def test_update_notice_on_fallthrough_only(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT + ACME_UPDATE)
    _capture_run(monkeypatch)

    notices = []
    monkeypatch.setattr(
        fw_updater, "maybe_print_update_notice", lambda config: notices.append(config)
    )
    monkeypatch.setattr(models_cli, "run_model_command", lambda argv, **kw: None)

    assert product_main(manifest, ["model", "list"]) == 0
    assert notices == []

    assert product_main(manifest, ["hello", "world"]) == 0
    assert len(notices) == 1
    assert notices[0].package_name == "acme-code"


def test_no_update_notice_without_update_block(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)
    _capture_run(monkeypatch)

    notices = []
    monkeypatch.setattr(
        fw_updater, "maybe_print_update_notice", lambda config: notices.append(config)
    )

    assert product_main(manifest, ["hello"]) == 0
    assert notices == []


# ---------------------------------------------------------------------------
# Extensions: PREFLIGHT
# ---------------------------------------------------------------------------


def test_preflight_ordering_and_argv_mutation(tmp_path, monkeypatch):
    calls = []

    def first(args):
        calls.append(("first", list(args)))
        return [*args, "--injected"]

    def second(args):
        calls.append(("second", list(args)))
        return None  # keep argv unchanged

    ext = _make_extensions(monkeypatch, PREFLIGHT=[first, second])
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + f"""
cli:
  extensions: {ext}
""",
    )
    called = _capture_run(monkeypatch)

    assert product_main(manifest, ["do", "thing"]) == 0
    assert calls == [
        ("first", ["do", "thing"]),
        ("second", ["do", "thing", "--injected"]),
    ]
    assert called["argv"][-3:] == ["do", "thing", "--injected"]


def test_preflight_runs_before_command_dispatch(tmp_path, monkeypatch):
    """The security-boundary ordering: preflights fire ahead of every
    dispatch target, including product COMMANDS."""
    calls = []

    def preflight(args):
        calls.append("preflight")
        return None

    def serve(args):
        calls.append("serve")
        return 0

    ext = _make_extensions(monkeypatch, PREFLIGHT=[preflight], COMMANDS={"serve": serve})
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + f"""
cli:
  extensions: {ext}
""",
    )

    assert product_main(manifest, ["serve", "--port", "1"]) == 0
    assert calls == ["preflight", "serve"]


def test_preflight_can_veto_with_system_exit(tmp_path, monkeypatch):
    def veto(args):
        raise SystemExit(7)

    ext = _make_extensions(monkeypatch, PREFLIGHT=[veto])
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + f"""
cli:
  extensions: {ext}
""",
    )

    with pytest.raises(SystemExit) as exc:
        product_main(manifest, ["anything"])
    assert exc.value.code == 7


# ---------------------------------------------------------------------------
# Extensions: COMMANDS
# ---------------------------------------------------------------------------


def test_product_command_dispatch_and_exit_status(tmp_path, monkeypatch):
    called = {}

    def serve(args):
        called["argv"] = list(args)
        return 5

    ext = _make_extensions(monkeypatch, COMMANDS={"serve": serve})
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + f"""
cli:
  extensions: {ext}
""",
    )

    assert product_main(manifest, ["serve", "--json"]) == 5
    assert called["argv"] == ["--json"]


def test_command_override_of_reserved_delegates_to_default(tmp_path, monkeypatch):
    """A product override of `sessions` can wire its envelope and then
    delegate to the exported framework default."""
    calls = []

    def sessions_override(args):
        calls.append("override")
        return run_product_sessions_command(
            args, agent="pinned_agent", summary_envelope={"summary": ""}
        )

    ext = _make_extensions(monkeypatch, COMMANDS={"sessions": sessions_override})
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + f"""
agents:
  entrypoint: main_agent
cli:
  extensions: {ext}
""",
    )

    captured = {}

    def fake_handle(args):
        captured["project"] = args.project
        captured["agent"] = args.agent
        captured["envelope"] = args.summary_envelope
        return 0

    monkeypatch.setattr(sessions_cli, "handle_sessions_command", fake_handle)

    assert product_main(manifest, ["sessions", "list"]) == 0
    assert calls == ["override"]
    assert captured["project"] == "acme_code"
    assert captured["agent"] == "pinned_agent"
    assert captured["envelope"] == {"summary": ""}


# ---------------------------------------------------------------------------
# Fallthrough + `--` escape
# ---------------------------------------------------------------------------


def test_fallthrough_builds_run_argv(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)
    called = _capture_run(monkeypatch)

    assert product_main(manifest, ["fix", "the", "bug"]) == 0
    assert called["argv"] == [
        "omniagents", "run", "--project", str(manifest), "--mode", "ink",
        "fix", "the", "bug",
    ]


def test_fallthrough_default_mode_from_manifest(tmp_path, monkeypatch):
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + """
cli:
  default_mode: web
""",
    )
    called = _capture_run(monkeypatch)

    assert product_main(manifest, []) == 0
    assert called["argv"] == [
        "omniagents", "run", "--project", str(manifest), "--mode", "web",
    ]


def test_double_dash_forces_prompt_mode_for_reserved_words(tmp_path, monkeypatch):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT + ACME_UPDATE)
    called = _capture_run(monkeypatch)
    monkeypatch.setattr(fw_updater, "maybe_print_update_notice", lambda config: None)

    assert product_main(manifest, ["--", "model", "the", "data"]) == 0
    assert called["argv"][-3:] == ["model", "the", "data"]
    assert "--" not in called["argv"]


def test_dispatches_to_command_gate(tmp_path):
    from omniagents.core.product import bootstrap_product

    manifest = _write_manifest(tmp_path, ACME_PRODUCT)
    bootstrap_product(manifest)

    assert dispatches_to_command(["model", "list"])
    assert dispatches_to_command(["--help"])
    assert dispatches_to_command(["serve"], extra_commands=("serve",))
    assert not dispatches_to_command(["--", "model"])
    assert not dispatches_to_command(["fix", "bug"])
    assert not dispatches_to_command([])
    # `update` gates on the declared update block.
    assert not dispatches_to_command(["update"])
    bootstrap_product(_write_manifest(tmp_path, ACME_PRODUCT + ACME_UPDATE))
    assert dispatches_to_command(["update"])


# ---------------------------------------------------------------------------
# Help / prog rendering
# ---------------------------------------------------------------------------


def test_top_level_help_renders_product_prog_and_commands(tmp_path, monkeypatch, capsys):
    def serve(args):
        """Run the acme host server."""
        return 0

    ext = _make_extensions(monkeypatch, COMMANDS={"serve": serve})
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + ACME_UPDATE
        + f"""
cli:
  extensions: {ext}
""",
    )

    assert product_main(manifest, ["--help"]) == 0
    out = capsys.readouterr().out
    assert "usage: acme [" in out
    assert "acme model ..." in out
    assert "acme update ..." in out
    assert "acme serve ..." in out
    assert "Run the acme host server." in out
    assert "acme -- <words...>" in out
    assert "Force prompt mode" in out


def test_top_level_help_hides_update_when_not_declared(tmp_path, capsys):
    manifest = _write_manifest(tmp_path, ACME_PRODUCT)

    assert product_main(manifest, ["help"]) == 0
    out = capsys.readouterr().out
    assert "acme update" not in out


# ---------------------------------------------------------------------------
# Manifest cli: schema
# ---------------------------------------------------------------------------


def test_cli_section_defaults_and_parse(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + """
agents:
  entrypoint: main_agent
cli:
  default_mode: web
  extensions: some.module
""",
    )
    cfg = load_product_cli_config(manifest)
    assert cfg.default_mode == "web"
    assert cfg.extensions == "some.module"
    assert cfg.entrypoint_agent == "main_agent"

    bare = load_product_cli_config(_write_manifest(tmp_path, ACME_PRODUCT))
    assert bare.default_mode == "ink"
    assert bare.extensions is None


def test_cli_section_rejects_unknown_keys(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + """
cli:
  default_mod: web
""",
    )
    with pytest.raises(ValueError, match="Unknown key"):
        load_product_cli_config(manifest)


def test_missing_extensions_module_raises_clear_error(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        ACME_PRODUCT
        + """
cli:
  extensions: not_a_real_module_xyz
""",
    )
    with pytest.raises(ImportError, match="cli.extensions"):
        product_main(manifest, ["anything"])
