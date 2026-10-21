"""Tests for the product identity descriptor (omniagents.core.product).

Covers: schema parse + defaults/derivations, precedence (explicit set
wins over load_project), the per-subsystem "None -> product_info()"
defaults, and that the light parse does not resolve the full project.
"""

from pathlib import Path
from textwrap import dedent

import pytest

from omniagents.core import product as product_mod
from omniagents.core.product import (
    DEFAULT_PRODUCT_INFO,
    ProductInfo,
    ProductUpdateInfo,
    bootstrap_product,
    load_product_info,
    product_info,
    product_info_from_mapping,
    reset_product_info,
    set_product_info,
)


@pytest.fixture(autouse=True)
def _fresh_identity():
    reset_product_info()
    yield
    reset_product_info()


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content))
    return path


ACME_INFO = ProductInfo(
    name="acme-code",
    prog="acme",
    slug="acme_code",
    label="Acme Code",
    config_dir_name="acme_code",
    env_prefix="ACME_CODE",
)


def _product_project(tmp_path: Path, product_yaml: str) -> Path:
    _write(
        tmp_path / "agents" / "main" / "agent.yml",
        """
        name: main
        instructions: You are a test agent.
        tools: []
        """,
    )
    return _write(tmp_path / "project.yml", dedent(product_yaml))


# ---------------------------------------------------------------------------
# Schema parse: defaults and derivations
# ---------------------------------------------------------------------------


def test_minimal_slug_only_derives_everything():
    info = product_info_from_mapping({"slug": "omni_code"})
    assert info.slug == "omni_code"
    assert info.name == "omni-code"
    assert info.prog == "omni-code"
    assert info.label == "Omni Code"
    assert info.config_dir_name == "omni_code"
    assert info.windows_config_dir_name == "OmniCode"
    assert info.env_prefix == "OMNI_CODE"
    assert info.update is None


def test_name_only_derives_everything():
    """The one-name convention: `product: {name: x}` seeds every facet."""
    info = product_info_from_mapping({"name": "omni-code"})
    assert info.slug == "omni_code"
    assert info.name == "omni-code"
    assert info.prog == "omni-code"
    assert info.label == "Omni Code"
    assert info.config_dir_name == "omni_code"
    assert info.windows_config_dir_name == "OmniCode"
    assert info.env_prefix == "OMNI_CODE"


def test_label_derivation_splits_and_title_cases():
    assert product_info_from_mapping({"name": "acme-agent"}).label == "Acme Agent"
    assert product_info_from_mapping({"name": "acme_agent"}).label == "Acme Agent"
    assert product_info_from_mapping({"name": "acme"}).label == "Acme"


def test_windows_config_dir_derives_from_label():
    # Derived label -> spaces removed.
    assert (
        product_info_from_mapping({"name": "acme-agent"}).windows_config_dir_name
        == "AcmeAgent"
    )
    # Explicit label feeds the derivation too.
    assert (
        product_info_from_mapping(
            {"name": "acme-agent", "label": "Acme Dev Tool"}
        ).windows_config_dir_name
        == "AcmeDevTool"
    )


def test_update_command_hint_derives_from_prog():
    info = product_info_from_mapping(
        {"name": "acme-agent", "prog": "acme", "update": {"index_url": "https://x/"}}
    )
    assert info.update is not None
    assert info.update.command_hint == "acme update"


def test_full_schema_explicit_keys_win():
    info = product_info_from_mapping(
        {
            "name": "omni-code",
            "prog": "omni",
            "label": "Omni Code",
            "slug": "omni_code",
            "config_dir": "omni_cfg",
            "windows_config_dir": "OmniCode",
            "env_prefix": "OMNIX",
            "update": {
                "index_url": "https://pypi.fury.io/me/omni-code/",
                "package": "omni-code-dist",
                "command_hint": "omni self-update",
            },
        }
    )
    assert info.prog == "omni"
    assert info.label == "Omni Code"
    assert info.config_dir_name == "omni_cfg"
    assert info.windows_config_dir_name == "OmniCode"
    assert info.env_prefix == "OMNIX"
    assert info.update == ProductUpdateInfo(
        index_url="https://pypi.fury.io/me/omni-code/",
        package="omni-code-dist",
        command_hint="omni self-update",
    )


def test_update_block_defaults():
    info = product_info_from_mapping(
        {"slug": "omni_code", "prog": "omni", "update": {"index_url": "https://x/"}}
    )
    assert info.update is not None
    assert info.update.package == "omni-code"  # defaults to name
    assert info.update.command_hint == "omni update"  # defaults to "<prog> update"


@pytest.mark.parametrize(
    "bad",
    [
        {},  # neither slug nor name
        {"slug": ""},
        {"slug": "x", "bogus": 1},  # unknown key
        {"slug": "x", "update": {"package": "p"}},  # update without index_url
        {"slug": "x", "update": {"index_url": "u", "nope": 1}},
        "not-a-mapping",
        {"slug": "x", "prog": 7},
    ],
)
def test_invalid_product_sections_raise(bad):
    with pytest.raises(ValueError):
        product_info_from_mapping(bad)


def test_default_identity_is_omniagents():
    info = product_info()
    assert info is DEFAULT_PRODUCT_INFO
    assert (info.name, info.prog, info.slug) == ("omniagents",) * 3
    assert info.config_dir_name == "omniagents"
    assert info.env_prefix == "OMNIAGENTS"
    assert info.update is None


# ---------------------------------------------------------------------------
# Light parse
# ---------------------------------------------------------------------------


def test_load_product_info_light_parse_skips_project_resolution(tmp_path):
    # An agents path that is a *file* makes full load_project raise;
    # the light parse must not care.
    _write(tmp_path / "agents", "")  # file, not directory
    _write(
        tmp_path / "project.yml",
        """
        name: broken_project
        product:
          slug: acme_code
          prog: acme
        """,
    )
    from omniagents.core.project.runtime import load_project

    info = load_product_info(tmp_path / "project.yml")
    assert (info.slug, info.prog) == ("acme_code", "acme")
    # Accepts a directory too, and does not install anything.
    assert load_product_info(tmp_path).slug == "acme_code"
    assert product_info() is DEFAULT_PRODUCT_INFO

    # ...while full resolution of the same manifest fails on the broken
    # agents layout the light parse never touched.
    with pytest.raises(FileNotFoundError):
        load_project(str(tmp_path / "project.yml"))


def test_load_product_info_without_section_returns_default(tmp_path):
    _write(tmp_path / "project.yml", "name: plain\n")
    assert load_product_info(tmp_path) is DEFAULT_PRODUCT_INFO


def test_load_product_info_stamps_manifest_path(tmp_path):
    proj = _write(tmp_path / "project.yml", "product: {slug: acme_code}\n")
    assert load_product_info(tmp_path).manifest_path == proj
    assert load_product_info(proj).manifest_path == proj
    # Hand-built infos carry no manifest.
    assert product_info_from_mapping({"slug": "x"}).manifest_path is None
    # bootstrap installs the stamped identity process-wide.
    bootstrap_product(proj)
    assert product_info().manifest_path == proj


def test_load_project_stamps_manifest_path(tmp_path):
    from omniagents.core.project.runtime import load_project

    proj_file = _product_project(tmp_path, "product: {slug: acme_code}\n")
    load_project(str(proj_file))
    assert product_info().manifest_path == proj_file.resolve()


def test_load_product_info_missing_manifest_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_product_info(tmp_path / "nope")


def test_load_product_info_error_names_file(tmp_path):
    _write(tmp_path / "project.yml", "product:\n  bogus: 1\n")
    with pytest.raises(ValueError, match="project.yml"):
        load_product_info(tmp_path)


# ---------------------------------------------------------------------------
# Precedence: explicit set wins over load_project
# ---------------------------------------------------------------------------


def test_load_project_installs_product_identity(tmp_path):
    from omniagents.core.project.runtime import load_project

    proj_file = _product_project(
        tmp_path,
        """
        product:
          slug: acme_code
          prog: acme
        """,
    )
    load_project(str(proj_file))
    assert product_info().slug == "acme_code"
    assert product_info().prog == "acme"


def test_explicit_set_wins_over_load_project(tmp_path):
    from omniagents.core.project.runtime import load_project

    set_product_info(ACME_INFO)
    proj_file = _product_project(tmp_path, "product: {slug: other_product}\n")
    load_project(str(proj_file))
    assert product_info() is ACME_INFO


def test_bootstrap_product_installs_explicitly(tmp_path):
    from omniagents.core.project.runtime import load_project

    _product_project(
        tmp_path,
        """
        product:
          slug: acme_code
        """,
    )
    info = bootstrap_product(tmp_path)
    assert product_info() is info
    assert info.slug == "acme_code"

    other = tmp_path / "other"
    proj_file = _product_project(other, "product: {slug: other_product}\n")
    load_project(str(proj_file))
    assert product_info() is info  # bootstrap is explicit; not clobbered


def test_load_project_without_product_leaves_identity_alone(tmp_path):
    from omniagents.core.project.runtime import load_project

    proj_file = _product_project(tmp_path, "")
    load_project(str(proj_file))
    assert product_info() is DEFAULT_PRODUCT_INFO


def test_set_product_info_rejects_non_product_info():
    with pytest.raises(TypeError):
        set_product_info({"slug": "x"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Subsystem defaults
# ---------------------------------------------------------------------------


def test_product_config_dir_argless_defaults_from_identity(tmp_path, monkeypatch):
    from omniagents.core.config.dirs import product_config_dir

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # Windows config root
    assert product_config_dir() == tmp_path / "omniagents"
    set_product_info(ACME_INFO)
    assert product_config_dir() == tmp_path / "acme_code"
    assert ACME_INFO.config_dir() == tmp_path / "acme_code"
    # Explicit argument still wins.
    assert product_config_dir("elsewhere") == tmp_path / "elsewhere"


def test_updater_config_from_product():
    from omniagents.core.updater import UpdaterConfig

    info = product_info_from_mapping(
        {
            "slug": "acme_code",
            "prog": "acme",
            "update": {"index_url": "https://pypi.fury.io/me/acme-code/"},
        }
    )
    cfg = UpdaterConfig.from_product(info)
    assert cfg.simple_index_url == "https://pypi.fury.io/me/acme-code/"
    assert cfg.package_name == "acme-code"
    assert cfg.env_prefix == "ACME_CODE"
    assert cfg.resolved_update_command_hint == "acme update"
    assert cfg.resolve_cache_dir() == info.config_dir()


def test_updater_config_from_product_requires_update_block():
    from omniagents.core.updater import UpdaterConfig, run_update_command

    with pytest.raises(ValueError, match="no 'update:' block"):
        UpdaterConfig.from_product(ACME_INFO)
    set_product_info(ACME_INFO)
    with pytest.raises(ValueError, match="no 'update:' block"):
        UpdaterConfig.from_product()
    with pytest.raises(ValueError, match="no 'update:' block"):
        run_update_command([])


def test_platform_client_defaults_from_identity(tmp_path, monkeypatch):
    from omniagents.core.providers.platform import client

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # Windows config root
    prior_dir = client._credential_store_dir
    prior_slug = client._product_slug
    try:
        client.set_credential_store_dir(None)
        client.set_product_slug(None)
        assert client.get_product_slug() == "omniagents"
        assert client.credential_path() == tmp_path / "omniagents" / "platform.json"

        set_product_info(ACME_INFO)
        assert client.get_product_slug() == "acme_code"
        assert client.credential_path() == tmp_path / "acme_code" / "platform.json"

        # Explicit bindings still win over product identity.
        client.set_product_slug("explicit_slug")
        client.set_credential_store_dir(tmp_path / "explicit")
        assert client.get_product_slug() == "explicit_slug"
        assert client.credential_path() == tmp_path / "explicit" / "platform.json"
    finally:
        client.set_credential_store_dir(prior_dir)
        client.set_product_slug(prior_slug)


def test_codex_token_store_defaults_from_identity(tmp_path, monkeypatch):
    from omniagents.core.providers import codex

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # Windows config root
    monkeypatch.delenv("OMNIAGENTS_CODEX_HOME", raising=False)
    prior = codex._token_store_dir
    try:
        codex._token_store_dir = None
        assert codex._config_dir() == tmp_path / "omniagents"
        set_product_info(ACME_INFO)
        assert codex._config_dir() == tmp_path / "acme_code"
        # Env override still wins over product identity.
        monkeypatch.setenv("OMNIAGENTS_CODEX_HOME", str(tmp_path / "env_home"))
        assert codex._config_dir() == tmp_path / "env_home"
        # Explicit binding wins over everything.
        codex.set_token_store_dir(tmp_path / "explicit")
        assert codex._config_dir() == tmp_path / "explicit"
    finally:
        codex._token_store_dir = prior


def test_codex_auth_command_prog_defaults_from_identity(tmp_path, monkeypatch, capsys):
    from omniagents.cli import run_codex_auth_command

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # Windows config root
    monkeypatch.delenv("OMNIAGENTS_CODEX_HOME", raising=False)
    set_product_info(ACME_INFO)
    assert run_codex_auth_command("status") == 0
    out = capsys.readouterr().out
    assert "Run: acme auth codex login" in out
    # Explicit prog still wins.
    assert run_codex_auth_command("status", prog="other") == 0
    assert "Run: other auth codex login" in capsys.readouterr().out


def test_model_and_provider_cli_prog_defaults_from_identity(capsys):
    from omniagents.core.models.cli import run_model_command, run_provider_command

    set_product_info(ACME_INFO)

    class _StoreStub:
        pass

    with pytest.raises(SystemExit):
        run_model_command([], store=_StoreStub())
    assert "usage: acme model" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run_provider_command([], store=_StoreStub())
    assert "usage: acme provider" in capsys.readouterr().out
    # Explicit prog still wins.
    with pytest.raises(SystemExit):
        run_model_command([], store=_StoreStub(), prog="explicitprog")
    assert "usage: explicitprog model" in capsys.readouterr().out


def test_platform_auth_help_prog_defaults_from_identity(capsys):
    from omniagents.cli import run_platform_auth_command

    set_product_info(ACME_INFO)
    assert run_platform_auth_command(["--help"]) == 0
    assert "usage: acme auth" in capsys.readouterr().out


def test_resolve_profile_config_dir_defaults_from_identity(tmp_path, monkeypatch):
    from omniagents.core.sandbox.profiles import resolve_profile

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # Windows config root
    set_product_info(ACME_INFO)
    user_layer = _write(
        tmp_path / "acme_code" / "sandbox" / "default.yml",
        """
        client:
          type: docker
        """,
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    resolved = resolve_profile(workspace_dir=workspace)
    assert user_layer in resolved.sources
    assert resolved.raw["client"]["type"] == "docker"


def test_reset_product_info_clears_explicit_flag(tmp_path):
    from omniagents.core.project.runtime import load_project

    set_product_info(ACME_INFO)
    reset_product_info()
    assert product_info() is DEFAULT_PRODUCT_INFO
    proj_file = _product_project(tmp_path, "product: {slug: fresh_product}\n")
    load_project(str(proj_file))
    assert product_info().slug == "fresh_product"


def test_module_underscore_state_not_leaked():
    # _set_default_product_info is project-loader plumbing, not API.
    assert "_set_default_product_info" not in product_mod.__all__


# ---------------------------------------------------------------------------
# Entry-point discovery
# ---------------------------------------------------------------------------


def _install_product_package(
    tmp_path, monkeypatch, package="acmepkg", slug="acme_code"
):
    """A fake installed product package shipping a project.yml, plus its
    entry point declared in the ``omniagents.products`` group."""
    from types import SimpleNamespace

    pkg = tmp_path / package
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    manifest = pkg / "project.yml"
    manifest.write_text(dedent(f"""
            name: {slug}
            product:
              name: {slug.replace("_", "-")}
              prog: acme
              slug: {slug}
            """))
    monkeypatch.syspath_prepend(str(tmp_path))
    import sys as _sys

    monkeypatch.delitem(_sys.modules, package, raising=False)
    return SimpleNamespace(name=slug.replace("_", "-"), value=package), manifest


@pytest.fixture
def _discovery_enabled(monkeypatch):
    monkeypatch.delenv("OMNIAGENTS_PRODUCT_DISCOVERY", raising=False)


def test_bare_import_identity_resolves_via_discovery(
    tmp_path, monkeypatch, _discovery_enabled
):
    ep, manifest = _install_product_package(tmp_path, monkeypatch)
    monkeypatch.setattr(product_mod, "_load_product_entry_points", lambda: [ep])

    info = product_info()
    assert info.name == "acme-code"
    assert info.prog == "acme"
    assert info.manifest_path == manifest
    assert product_mod.discover_product_manifest() == manifest


def test_discovery_is_cached_after_first_lookup(
    tmp_path, monkeypatch, _discovery_enabled
):
    ep, _ = _install_product_package(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        product_mod, "_load_product_entry_points", lambda: calls.append(1) or [ep]
    )

    assert product_info().name == "acme-code"
    assert product_info().name == "acme-code"
    assert len(calls) == 1
    reset_product_info()
    assert product_info().name == "acme-code"
    assert len(calls) == 2


def test_explicit_set_wins_over_discovery(tmp_path, monkeypatch, _discovery_enabled):
    ep, _ = _install_product_package(tmp_path, monkeypatch)
    monkeypatch.setattr(product_mod, "_load_product_entry_points", lambda: [ep])

    set_product_info(ACME_INFO)
    assert product_info() is ACME_INFO


def test_load_project_implicit_install_wins_over_discovery(
    tmp_path, monkeypatch, _discovery_enabled
):
    from omniagents.core.project.runtime import load_project

    ep, _ = _install_product_package(tmp_path, monkeypatch)
    monkeypatch.setattr(product_mod, "_load_product_entry_points", lambda: [ep])

    proj_file = _product_project(tmp_path / "proj", "product: {slug: loaded_product}\n")
    load_project(str(proj_file))
    assert product_info().slug == "loaded_product"


def test_no_entry_points_falls_back_to_default(monkeypatch, _discovery_enabled):
    monkeypatch.setattr(product_mod, "_load_product_entry_points", lambda: [])
    assert product_info() is DEFAULT_PRODUCT_INFO
    assert product_mod.discover_product_manifest() is None


def test_multiple_products_pick_deterministically_and_warn(
    tmp_path, monkeypatch, _discovery_enabled, capsys
):
    ep_a, manifest_a = _install_product_package(
        tmp_path, monkeypatch, package="aaapkg", slug="aaa_code"
    )
    ep_b, _ = _install_product_package(
        tmp_path, monkeypatch, package="zzzpkg", slug="zzz_code"
    )
    # Declaration order must not matter: lexicographically smallest wins.
    monkeypatch.setattr(product_mod, "_load_product_entry_points", lambda: [ep_b, ep_a])

    info = product_info()
    assert info.slug == "aaa_code"
    assert product_mod.discover_product_manifest() == manifest_a
    err = capsys.readouterr().err
    assert "multiple distributions declare" in err
    assert "omniagents.products" in err
    assert "'aaa-code'" in err


def test_broken_entry_point_warns_and_falls_back(
    monkeypatch, _discovery_enabled, capsys
):
    from types import SimpleNamespace

    ep = SimpleNamespace(name="ghost", value="package_that_does_not_exist_xyz")
    monkeypatch.setattr(product_mod, "_load_product_entry_points", lambda: [ep])

    assert product_info() is DEFAULT_PRODUCT_INFO
    assert "could not resolve product entry point" in capsys.readouterr().err


def test_discovery_kill_switch_env(tmp_path, monkeypatch):
    ep, _ = _install_product_package(tmp_path, monkeypatch)
    monkeypatch.setattr(product_mod, "_load_product_entry_points", lambda: [ep])
    monkeypatch.setenv("OMNIAGENTS_PRODUCT_DISCOVERY", "0")

    assert product_info() is DEFAULT_PRODUCT_INFO
    assert product_mod.discover_product_manifest() is None
