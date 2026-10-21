"""Declarative model catalog — a product's shipped model data as YAML.

A product's provider/model catalog is pure data: the built-in provider
profiles, their models, and the fallback default model. Products used to
ship it as a Python module and hand-build their
:class:`~omniagents.core.models.ModelConfigStore` around it. This module
makes the manifest the single declaration: a catalog file referenced
from ``project.yml`` and a store built + installed from convention.

Manifest declaration (``project.yml``)::

    paths:
      model_catalog: models.yml     # path relative to project.yml

The catalog is a *file reference* under ``paths:`` (not an inline block)
because ``paths.*`` entries are already manifest-relative references to
shipped assets, catalogs are long enough to bloat the manifest, and a
sibling file ships in the wheel exactly like ``project.yml`` itself.

Catalog schema (``models.yml``) — the exact shape
:class:`ModelConfigStore` consumes::

    default_model: gpt-5.1     # fallback default ref when the user
                               #   set none (optional)
    default_provider: openai   # provider implied by unprefixed model refs
                               #   (optional; default "openai")
    providers:                 # -> the store's default_providers layer
      openai:
        type: openai
        api_key: ${OPENAI_API_KEY}     # ${VAR}/${VAR:-default} expanded by
                                       #   the store at resolve time
        models:
          gpt-5.1:
            model: gpt-5.1
            label: GPT-5.1
            max_input_tokens: 272000
            max_output_tokens: 128000
            model_settings: {...}      # passed through verbatim
      codex:
        type: openai-oauth             # enables the codex OAuth hooks below
        models: {...}                  # offline fallback; live discovery
                                       #   replaces it when signed in

Provider/model entries are passed to the store verbatim — every key the
store understands in ``models.json`` providers (``base_url``,
``api_version``, ``realtime``, ``reasoning``, ...)
is valid here.

The installed store uses the *conventional hooks*:

- ``config_dir`` — the installed product identity's config dir
  (late-bound through :func:`omniagents.core.product.product_info`).
- ``default_providers`` / ``default_model_fallback`` — the catalog data.
- ``oauth_models`` / ``oauth_authenticated`` — the framework codex
  provider (live model discovery + sign-in state), wired only when the
  catalog contains an ``openai-oauth`` provider.

Install timing (documented contract):

1. An explicit :func:`omniagents.core.models.set_model_store` always
   wins and is never clobbered by catalog installs.
2. :func:`omniagents.core.product.bootstrap_product` — the first call of
   every product CLI (and of a product package's ``__init__``) —
   installs the declared catalog's store as the process default. This is
   what makes the store available to CLI paths that never load a
   project: the model wizard (``<prog> model setup``), ``omni-setup``
   style console scripts, ``<prog> model``/``provider``.
3. ``load_project`` installs it implicitly as well (covering
   ``omniagents run --project ...`` without a product bootstrap); among
   conventional installs the most recent wins.

The catalog file is parsed eagerly at install (one small YAML read, with
validation errors surfacing at bootstrap rather than first store use)
and the parsed data is served to the store via copying hooks.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

from .store import ModelConfigStore, validate_provider_connections

__all__ = [
    "MODEL_CATALOG_PATH_KEY",
    "ModelCatalog",
    "load_model_catalog",
    "model_catalog_path",
    "build_catalog_store",
    "install_model_catalog",
]

#: The ``paths:`` key naming the catalog file, relative to the manifest.
MODEL_CATALOG_PATH_KEY = "model_catalog"

_KNOWN_CATALOG_KEYS = {
    "providers",
    "default_model",
    "default_provider",
    "single_provider",
}


@dataclass(frozen=True)
class ModelCatalog:
    """A parsed, validated model catalog."""

    providers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    """Provider profiles — the store's ``default_providers`` layer."""

    default_model: Optional[str] = None
    """Fallback default model ref (``default_model_fallback``)."""

    default_provider: str = "openai"
    """Provider profile implied by unprefixed model refs."""

    single_provider: bool = False

    path: Optional[Path] = None
    """The catalog file this was parsed from, when file-loaded."""

    def has_oauth_provider(self) -> bool:
        """Whether any provider is ``type: openai-oauth`` (codex-backed)."""
        return any(
            (p.get("type") or "").strip().lower() == "openai-oauth"
            for p in self.providers.values()
            if isinstance(p, Mapping)
        )


def _catalog_from_mapping(data: Any, *, where: str) -> ModelCatalog:
    if not isinstance(data, Mapping):
        raise ValueError(
            f"{where}: model catalog must be a mapping, got {type(data).__name__}"
        )
    unknown = sorted(set(data) - _KNOWN_CATALOG_KEYS)
    if unknown:
        raise ValueError(
            f"{where}: unknown model-catalog key(s): {', '.join(unknown)}. "
            f"Known keys: {', '.join(sorted(_KNOWN_CATALOG_KEYS))}"
        )

    providers_raw = data.get("providers") or {}
    if not isinstance(providers_raw, Mapping):
        raise ValueError(
            f"{where}: 'providers' must be a mapping, got "
            f"{type(providers_raw).__name__}"
        )
    providers: Dict[str, Dict[str, Any]] = {}
    for name, provider in providers_raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}: provider names must be non-empty strings")
        if not isinstance(provider, Mapping):
            raise ValueError(
                f"{where}: provider {name!r} must be a mapping, got "
                f"{type(provider).__name__}"
            )
        models = provider.get("models")
        if models is not None and not isinstance(models, Mapping):
            raise ValueError(
                f"{where}: provider {name!r} 'models' must be a mapping, got "
                f"{type(models).__name__}"
            )
        providers[name.strip()] = {k: v for k, v in provider.items()}

    default_model = data.get("default_model")
    if default_model is not None and (
        not isinstance(default_model, str) or not default_model.strip()
    ):
        raise ValueError(
            f"{where}: 'default_model' must be a non-empty string, got "
            f"{default_model!r}"
        )

    default_provider = data.get("default_provider")
    if default_provider is None:
        default_provider = "openai"
    elif not isinstance(default_provider, str) or not default_provider.strip():
        raise ValueError(
            f"{where}: 'default_provider' must be a non-empty string, got "
            f"{default_provider!r}"
        )

    validate_provider_connections(dict(providers_raw))

    return ModelCatalog(
        providers=providers,
        default_model=default_model.strip() if default_model else None,
        default_provider=default_provider.strip(),
        single_provider=bool(data.get("single_provider", False)),
    )


def load_model_catalog(path: Any) -> ModelCatalog:
    """Parse and validate a catalog file. Raises on missing/invalid files."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"Model catalog file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    catalog = _catalog_from_mapping(data, where=str(p))
    return ModelCatalog(
        providers=catalog.providers,
        default_model=catalog.default_model,
        default_provider=catalog.default_provider,
        single_provider=catalog.single_provider,
        path=p.resolve(),
    )


def model_catalog_path(
    manifest_path: Any, *, data: Optional[Mapping[str, Any]] = None
) -> Optional[Path]:
    """The manifest's catalog file, or None.

    Declared via ``paths.model_catalog``; by convention a
    manifest-sibling ``models.yml`` (used only when the file exists —
    mirroring the sandbox-profile sibling convention). An explicit
    declaration always resolves (missing files then fail loudly at
    load time).

    *manifest_path* is a project.yml path or a directory containing one.
    *data* is an optional pre-parsed manifest mapping (e.g. the
    overlay-merged project dict inside ``load_project``) — when given,
    the manifest is not re-read; the path is still resolved relative to
    the manifest's directory.
    """
    p = Path(manifest_path).expanduser()
    if p.is_dir():
        p = p / "project.yml"
    if data is None:
        if not p.is_file():
            return None
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
    if not isinstance(data, Mapping):
        return None
    paths_cfg = data.get("paths")
    rel = (
        paths_cfg.get(MODEL_CATALOG_PATH_KEY)
        if isinstance(paths_cfg, Mapping)
        else None
    )
    if not isinstance(rel, str) or not rel.strip():
        # Convention: a manifest-sibling models.yml, when it exists.
        conventional = (p.parent / "models.yml").resolve()
        return conventional if conventional.is_file() else None
    candidate = Path(rel.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = (p.parent / candidate).resolve()
    return candidate


# ---------------------------------------------------------------------------
# Conventional hooks
# ---------------------------------------------------------------------------


def _conventional_config_dir() -> Path:
    """The installed product identity's config dir, late-bound."""
    from omniagents.core.product import product_info

    return product_info().config_dir()


def _codex_models() -> Optional[Dict[str, Dict[str, Any]]]:
    from omniagents.core.providers.codex import list_codex_models

    return list_codex_models()


def _codex_authenticated() -> bool:
    from omniagents.core.providers.codex import is_authenticated

    return is_authenticated()


def build_catalog_store(catalog: ModelCatalog) -> ModelConfigStore:
    """A :class:`ModelConfigStore` over *catalog* with the conventional
    hooks (see the module docstring)."""
    has_oauth = catalog.has_oauth_provider()
    return ModelConfigStore(
        config_dir=_conventional_config_dir,
        default_providers=lambda: copy.deepcopy(catalog.providers),
        default_model_fallback=lambda: catalog.default_model,
        oauth_models=_codex_models if has_oauth else None,
        oauth_authenticated=_codex_authenticated if has_oauth else None,
        default_provider_name=catalog.default_provider,
        single_provider=catalog.single_provider,
    )


# Stores already built per catalog file: repeated conventional installs
# of the same catalog (bootstrap_product at package import, load_project
# later) must hand back the same store instance, so everything bound to
# the process-wide store early stays bound to the live one.
_built_stores: Dict[Path, ModelConfigStore] = {}


def _reset_catalog_store_cache() -> None:
    """Drop built-store memoization (tests; called by reset_model_store)."""
    _built_stores.clear()


def install_model_catalog(
    manifest_path: Any, *, data: Optional[Mapping[str, Any]] = None
) -> Optional[ModelConfigStore]:
    """Install the manifest-declared catalog's store as the process default.

    Returns the store when the manifest declares ``paths.model_catalog``
    (installed via the default seam — an explicit
    :func:`omniagents.core.models.set_model_store` still wins), or None
    when no catalog is declared. Idempotent per catalog file: installing
    the same catalog again re-installs the same store instance. Raises on
    a declared-but-invalid catalog file, so packaging bugs surface at
    bootstrap.
    """
    catalog_path = model_catalog_path(manifest_path, data=data)
    if catalog_path is None:
        return None
    key = catalog_path.resolve()
    store = _built_stores.get(key)
    if store is None:
        store = build_catalog_store(load_model_catalog(catalog_path))
        _built_stores[key] = store

    from omniagents.core.product import product_info

    from . import _set_default_model_store

    prog = product_info().prog
    _set_default_model_store(
        store,
        setup_hint=f"No model configured. Run `{prog} model setup` to add one.",
    )
    return store
