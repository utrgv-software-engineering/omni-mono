"""Tests for the declarative model catalog (omniagents.core.models.catalog).

Covers: catalog parse/validation, manifest path resolution, the
conventional-store hooks (config dir from product identity, env
expansion at resolve time, codex OAuth wiring only for openai-oauth
catalogs, auth-gated governed config), and install timing/precedence
(bootstrap_product installs the default store; load_project installs
implicitly; an explicit set_model_store always wins).
"""

from pathlib import Path
from textwrap import dedent

import pytest

import omniagents.core.models as models_pkg
from omniagents.core.models import (
    ModelConfigStore,
    build_catalog_store,
    install_model_catalog,
    load_model_catalog,
    model_catalog_path,
    model_setup_hint,
    model_store,
    reset_model_store,
    set_model_store,
)
from omniagents.core.models.catalog import _conventional_governed_config
from omniagents.core.product import bootstrap_product, reset_product_info


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch, tmp_path):
    reset_product_info()
    reset_model_store()
    # Keep conventional config dirs inside the sandbox.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    yield
    reset_model_store()
    reset_product_info()


CATALOG_YAML = """
default_model: gpt-test
providers:
  openai:
    type: openai
    api_key: ${ACME_TEST_KEY}
    models:
      gpt-test:
        model: gpt-test
        label: GPT Test
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content))
    return path


def _product_dir(tmp_path: Path, *, catalog: str = CATALOG_YAML) -> Path:
    root = tmp_path / "proj"
    _write(
        root / "project.yml",
        """
        name: acme_code
        product:
          name: acme-code
          prog: acme
        paths:
          model_catalog: models.yml
        """,
    )
    _write(root / "models.yml", catalog)
    return root


# ---------------------------------------------------------------------------
# Parse + validation
# ---------------------------------------------------------------------------


def test_load_model_catalog_parses_fields(tmp_path):
    p = _write(tmp_path / "models.yml", CATALOG_YAML)
    catalog = load_model_catalog(p)
    assert catalog.default_model == "gpt-test"
    assert catalog.default_provider == "openai"
    assert catalog.providers["openai"]["models"]["gpt-test"]["label"] == "GPT Test"
    assert catalog.path == p.resolve()
    assert not catalog.has_oauth_provider()


def test_load_model_catalog_default_provider_override(tmp_path):
    p = _write(
        tmp_path / "models.yml",
        """
        default_provider: azure
        providers:
          azure: {type: azure, models: {}}
        """,
    )
    assert load_model_catalog(p).default_provider == "azure"


def test_load_model_catalog_unknown_key_rejected(tmp_path):
    p = _write(tmp_path / "models.yml", "modelz: {}\n")
    with pytest.raises(ValueError, match="unknown model-catalog key"):
        load_model_catalog(p)


def test_load_model_catalog_bad_provider_rejected(tmp_path):
    p = _write(tmp_path / "models.yml", "providers:\n  openai: 3\n")
    with pytest.raises(ValueError, match="provider 'openai' must be a mapping"):
        load_model_catalog(p)


def test_load_model_catalog_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_model_catalog(tmp_path / "nope.yml")


def test_oauth_provider_detection(tmp_path):
    p = _write(
        tmp_path / "models.yml",
        """
        providers:
          codex: {type: openai-oauth, models: {}}
        """,
    )
    assert load_model_catalog(p).has_oauth_provider()


# ---------------------------------------------------------------------------
# Manifest resolution
# ---------------------------------------------------------------------------


def test_model_catalog_path_resolves_relative(tmp_path):
    root = _product_dir(tmp_path)
    assert model_catalog_path(root / "project.yml") == root / "models.yml"
    assert model_catalog_path(root) == root / "models.yml"


def test_model_catalog_path_absent(tmp_path):
    root = tmp_path / "p"
    _write(root / "project.yml", "product:\n  name: x\n")
    assert model_catalog_path(root) is None


def test_model_catalog_path_sibling_convention(tmp_path):
    """An undeclared catalog resolves to a manifest-sibling models.yml
    when — and only when — that file exists."""
    root = tmp_path / "p"
    _write(root / "project.yml", "product:\n  name: x\n")
    assert model_catalog_path(root) is None
    _write(root / "models.yml", "providers: {}\n")
    assert model_catalog_path(root) == (root / "models.yml").resolve()
    # An explicit declaration still wins over the sibling convention.
    resolved = model_catalog_path(
        root / "project.yml", data={"paths": {"model_catalog": "other.yml"}}
    )
    assert resolved == root / "other.yml"


def test_model_catalog_path_prefers_preparsed_data(tmp_path):
    root = _product_dir(tmp_path)
    # An overlay-merged dict wins over the on-disk manifest content.
    resolved = model_catalog_path(
        root / "project.yml", data={"paths": {"model_catalog": "other.yml"}}
    )
    assert resolved == root / "other.yml"


# ---------------------------------------------------------------------------
# Conventional hooks
# ---------------------------------------------------------------------------


def test_catalog_store_uses_product_config_dir(tmp_path):
    root = _product_dir(tmp_path)
    bootstrap_product(root)
    store = model_store()
    assert store is not None
    assert store.config_path() == tmp_path / "xdg" / "acme_code" / "models.json"


def test_catalog_store_env_expansion_at_resolve(tmp_path, monkeypatch):
    monkeypatch.setenv("ACME_TEST_KEY", "sk-from-env")
    root = _product_dir(tmp_path)
    bootstrap_product(root)
    runtime = model_store().resolve_model_for_runtime("gpt-test")
    assert runtime["model"] == "gpt-test"
    assert runtime["api_key"] == "sk-from-env"


def test_catalog_store_defaults_and_fallback(tmp_path):
    root = _product_dir(tmp_path)
    bootstrap_product(root)
    store = model_store()
    assert store.get_default_model_name() == "gpt-test"
    merged = store.merged_providers()
    assert merged["openai"]["models"]["gpt-test"]["label"] == "GPT Test"
    # The hooks serve copies — mutating merged output must not corrupt
    # the catalog for later reads.
    merged["openai"]["models"].clear()
    assert store.merged_providers()["openai"]["models"]


def test_oauth_hooks_only_for_oauth_catalogs(tmp_path):
    plain = build_catalog_store(
        load_model_catalog(_write(tmp_path / "a.yml", CATALOG_YAML))
    )
    assert plain._oauth_models is None
    assert plain._oauth_authenticated is None

    oauth = build_catalog_store(
        load_model_catalog(
            _write(
                tmp_path / "b.yml",
                """
                providers:
                  codex: {type: openai-oauth, models: {}}
                """,
            )
        )
    )
    assert oauth._oauth_models is not None
    assert oauth._oauth_authenticated is not None


def test_governed_hook_gated_on_platform_auth(tmp_path, monkeypatch):
    from omniagents.core.models.governed import write_governed_config
    from omniagents.core.providers.platform import client

    root = _product_dir(tmp_path)
    bootstrap_product(root)
    config_dir = tmp_path / "xdg" / "acme_code"
    write_governed_config(config_dir, {"gov": {"type": "openai", "models": {}}})

    # Signed out: a governed file on disk must NOT apply.
    monkeypatch.setattr(client, "is_authenticated", lambda: False)
    assert _conventional_governed_config() is None

    # Signed in: the standard governed file applies.
    monkeypatch.setattr(client, "is_authenticated", lambda: True)
    governed = _conventional_governed_config()
    assert governed and "gov" in governed["providers"]


def test_governed_hook_reapplies_once_when_cache_missing(tmp_path, monkeypatch):
    from omniagents.core.models import governed as governed_mod
    from omniagents.core.providers.platform import client

    root = _product_dir(tmp_path)
    bootstrap_product(root)
    monkeypatch.setattr(client, "is_authenticated", lambda: True)

    calls = []
    monkeypatch.setattr(
        governed_mod,
        "apply_platform_model_governance",
        lambda config_dir=None: calls.append(config_dir),
    )
    assert _conventional_governed_config() is None
    assert calls == [tmp_path / "xdg" / "acme_code"]


# ---------------------------------------------------------------------------
# Install timing + precedence
# ---------------------------------------------------------------------------


def test_bootstrap_installs_catalog_store_before_any_project_load(tmp_path):
    root = _product_dir(tmp_path)
    assert model_store() is None
    bootstrap_product(root)
    store = model_store()
    assert isinstance(store, ModelConfigStore)
    assert store.get_default_model_name() == "gpt-test"
    assert "Run `acme model setup`" in model_setup_hint()


def test_bootstrap_without_catalog_installs_nothing(tmp_path):
    root = tmp_path / "p"
    _write(root / "project.yml", "name: x\nproduct: {slug: x}\n")
    bootstrap_product(root)
    assert model_store() is None


def test_explicit_set_model_store_wins_over_catalog_install(tmp_path):
    explicit = ModelConfigStore(config_dir=lambda: tmp_path)
    set_model_store(explicit, setup_hint="explicit hint")
    root = _product_dir(tmp_path)
    bootstrap_product(root)
    assert model_store() is explicit
    assert model_setup_hint() == "explicit hint"


def test_explicit_set_model_store_after_catalog_install_wins(tmp_path):
    root = _product_dir(tmp_path)
    bootstrap_product(root)
    explicit = ModelConfigStore(config_dir=lambda: tmp_path)
    set_model_store(explicit)
    assert model_store() is explicit


def test_repeat_install_of_same_catalog_reuses_the_store(tmp_path):
    # bootstrap_product at package import, load_project later: everything
    # bound to the early store must stay bound to the live one.
    root = _product_dir(tmp_path)
    bootstrap_product(root)
    first = model_store()
    assert install_model_catalog(root / "project.yml") is first
    assert model_store() is first


def test_invalid_declared_catalog_fails_loud_at_bootstrap(tmp_path):
    root = _product_dir(tmp_path, catalog="providers: 3\n")
    with pytest.raises(ValueError, match="'providers' must be a mapping"):
        bootstrap_product(root)


def test_load_project_installs_catalog_implicitly(tmp_path):
    from omniagents.core.project.runtime import load_project

    root = tmp_path / "proj"
    _write(
        root / "project.yml",
        """
        name: acme_code
        paths:
          model_catalog: models.yml
        """,
    )
    _write(root / "models.yml", CATALOG_YAML)
    _write(
        root / "agents" / "helper" / "agent.yml",
        """
        name: helper
        instructions: You are a helper.
        """,
    )
    assert model_store() is None
    load_project(str(root))
    store = model_store()
    assert store is not None
    assert store.get_default_model_name() == "gpt-test"
