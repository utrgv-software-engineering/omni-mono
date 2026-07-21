"""Product identity descriptor — convention over configuration.

A *product* (a project shipping an agent as an installable distribution)
used to hand its identity constants — CLI program name, platform slug,
config-dir name, updater knobs — to each framework subsystem one at a
time. This module makes the project manifest the single source of that
identity: a top-level ``product:`` section in ``project.yml``, parsed
into a frozen :class:`ProductInfo` and installed process-wide so every
subsystem can default from it.

Schema (all keys optional except that at least one of ``slug`` /
``name`` must be present)::

    product:
      name: omni-code            # distribution/package name
                                 #   default: slug with "_" -> "-"
      prog: omni                 # CLI program name for --help rendering
                                 #   default: name
      label: Omni Code           # human display label (model CLI, wizards)
                                 #   default: name
      slug: omni_code            # platform/project slug
                                 #   default: name slugified ("-" -> "_")
      config_dir: omni_code      # per-user config dir name
                                 #   (-> product_config_dir(...)); default: slug
      windows_config_dir: OmniCode   # %APPDATA% dir name on Windows
                                 #   default: config_dir
      env_prefix: OMNI_CODE      # env-var prefix; default: slug upper-cased
                                 #   with non-alphanumerics collapsed to "_"
      update:                    # optional; presence enables self-update defaults
        index_url: https://pypi.fury.io/me/omni-code/   # required (PEP-503 page)
        package: omni-code       # pipx/metadata distribution name; default: name
        command_hint: omni update    # shown in update notices;
                                 #   default: "<prog> update"

A minimal ``product: {slug: x}`` therefore fully works.

Precedence (who wins the process-global identity):

1. :func:`set_product_info` — an explicit set (including via
   :func:`bootstrap_product`) always wins and is never clobbered by
   project loading.
2. ``load_project`` — installs the manifest's ``product:`` section as
   the process default, but only while no explicit set has happened.
   Loading a second project (implicitly) replaces a previous implicit
   install.
3. Otherwise :data:`DEFAULT_PRODUCT_INFO` — the framework's own
   "omniagents" identity.

Product CLIs bootstrap identity *before* (and without) resolving the
whole project with :func:`bootstrap_product`, which uses the light
:func:`load_product_info` parse — a plain YAML read of the ``product:``
key only, no agent discovery, no overlays, no registries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

__all__ = [
    "ProductInfo",
    "ProductUpdateInfo",
    "DEFAULT_PRODUCT_INFO",
    "product_info",
    "set_product_info",
    "reset_product_info",
    "product_info_from_mapping",
    "load_product_info",
    "bootstrap_product",
]


@dataclass(frozen=True)
class ProductUpdateInfo:
    """Self-update knobs from the ``product.update`` block."""

    index_url: str
    """PEP-503 simple project page listing the product's release files."""

    package: str
    """Distribution name as known to pipx and installed metadata."""

    command_hint: str
    """Command shown in the "update available" notice (e.g. ``omni update``)."""


@dataclass(frozen=True)
class ProductInfo:
    """Frozen product identity, resolved with all defaults applied."""

    name: str
    """Distribution/package name (e.g. ``omni-code``)."""

    prog: str
    """CLI program name used in ``--help``/usage rendering (e.g. ``omni``)."""

    slug: str
    """Platform/project slug used for policy scoping (e.g. ``omni_code``)."""

    label: str
    """Human display label (e.g. ``Omni Code``)."""

    config_dir_name: str
    """Per-user config directory name (``~/.config/<config_dir_name>``)."""

    windows_config_dir_name: Optional[str] = None
    """``%APPDATA%`` directory name on Windows; ``config_dir_name`` when None."""

    env_prefix: str = ""
    """Prefix for product environment variables (e.g. ``OMNI_CODE``)."""

    update: Optional[ProductUpdateInfo] = None
    """Self-update configuration, or None when the product has no
    ``update:`` block."""

    def config_dir(self) -> Path:
        """The product's per-user config directory (not created)."""
        from omniagents.core.config.dirs import product_config_dir

        return product_config_dir(
            self.config_dir_name, windows_name=self.windows_config_dir_name
        )


def _derive_slug(name: str) -> str:
    token = re.sub(r"[^a-z0-9\-_]+", "_", name.strip().lower())
    token = re.sub(r"[-_]+", "_", token).strip("_")
    return token


def _derive_env_prefix(slug: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_")
    return token.upper()


DEFAULT_PRODUCT_INFO = ProductInfo(
    name="omniagents",
    prog="omniagents",
    slug="omniagents",
    label="omniagents",
    config_dir_name="omniagents",
    windows_config_dir_name=None,
    env_prefix="OMNIAGENTS",
    update=None,
)
"""The framework's own identity — the process default until a product
installs its own (see the module docstring for precedence)."""


_KNOWN_KEYS = {
    "name",
    "prog",
    "label",
    "slug",
    "config_dir",
    "windows_config_dir",
    "env_prefix",
    "update",
}
_KNOWN_UPDATE_KEYS = {"index_url", "package", "command_hint"}


def _opt_str(data: Mapping[str, Any], key: str, *, where: str) -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} must be a non-empty string, got {value!r}")
    return value.strip()


def product_info_from_mapping(data: Any) -> ProductInfo:
    """Resolve a ``product:`` mapping into a :class:`ProductInfo`.

    Applies every default documented in the module docstring; raises
    ``ValueError`` on unknown keys (typo protection), wrong types, or a
    section naming neither ``slug`` nor ``name``.
    """
    if not isinstance(data, Mapping):
        raise ValueError(
            f"project.yml 'product:' must be a mapping, got {type(data).__name__}"
        )
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        raise ValueError(
            f"Unknown key(s) in project.yml 'product:': {', '.join(unknown)}. "
            f"Known keys: {', '.join(sorted(_KNOWN_KEYS))}"
        )

    name = _opt_str(data, "name", where="product")
    slug = _opt_str(data, "slug", where="product")
    if not name and not slug:
        raise ValueError(
            "project.yml 'product:' requires at least one of 'slug' or 'name'"
        )
    if not slug:
        slug = _derive_slug(name)  # type: ignore[arg-type]
        if not slug:
            raise ValueError(f"product.name {name!r} yields an empty slug")
    if not name:
        name = slug.replace("_", "-")

    prog = _opt_str(data, "prog", where="product") or name
    label = _opt_str(data, "label", where="product") or name
    config_dir = _opt_str(data, "config_dir", where="product") or slug
    windows_config_dir = _opt_str(data, "windows_config_dir", where="product")
    env_prefix = _opt_str(data, "env_prefix", where="product") or _derive_env_prefix(
        slug
    )

    update_data = data.get("update")
    update: Optional[ProductUpdateInfo] = None
    if update_data is not None:
        if not isinstance(update_data, Mapping):
            raise ValueError(
                "project.yml 'product.update:' must be a mapping, got "
                f"{type(update_data).__name__}"
            )
        unknown = sorted(set(update_data) - _KNOWN_UPDATE_KEYS)
        if unknown:
            raise ValueError(
                f"Unknown key(s) in project.yml 'product.update:': {', '.join(unknown)}. "
                f"Known keys: {', '.join(sorted(_KNOWN_UPDATE_KEYS))}"
            )
        index_url = _opt_str(update_data, "index_url", where="product.update")
        if not index_url:
            raise ValueError("project.yml 'product.update:' requires 'index_url'")
        update = ProductUpdateInfo(
            index_url=index_url,
            package=_opt_str(update_data, "package", where="product.update") or name,
            command_hint=_opt_str(update_data, "command_hint", where="product.update")
            or f"{prog} update",
        )

    return ProductInfo(
        name=name,
        prog=prog,
        slug=slug,
        label=label,
        config_dir_name=config_dir,
        windows_config_dir_name=windows_config_dir,
        env_prefix=env_prefix,
        update=update,
    )


# ---------------------------------------------------------------------------
# Process-global identity
# ---------------------------------------------------------------------------

_current: ProductInfo = DEFAULT_PRODUCT_INFO
_explicitly_set: bool = False


def product_info() -> ProductInfo:
    """The current process-wide product identity (never None)."""
    return _current


def set_product_info(info: ProductInfo) -> None:
    """Explicitly install *info* as the process-wide product identity.

    An explicit set always wins: subsequent ``load_project`` calls will
    NOT replace it (see the module docstring for precedence).
    """
    if not isinstance(info, ProductInfo):
        raise TypeError(f"expected ProductInfo, got {type(info).__name__}")
    global _current, _explicitly_set
    _current = info
    _explicitly_set = True


def _set_default_product_info(info: ProductInfo) -> None:
    """Install *info* as the implicit (project-loaded) identity.

    Called by ``load_project`` for manifests carrying a ``product:``
    section. A prior :func:`set_product_info` wins; among implicit
    installs, the most recent project load wins.
    """
    global _current
    if _explicitly_set:
        return
    _current = info


def reset_product_info() -> None:
    """Restore the default identity and clear the explicit-set flag.

    Intended for tests and embedders that spin up multiple products in
    one process.
    """
    global _current, _explicitly_set
    _current = DEFAULT_PRODUCT_INFO
    _explicitly_set = False


# ---------------------------------------------------------------------------
# Light parse + bootstrap
# ---------------------------------------------------------------------------


def load_product_info(project_path: Any) -> ProductInfo:
    """Light-parse the ``product:`` section of a project manifest.

    *project_path* is a project.yml path or a directory containing one.
    This is a plain YAML read of the base manifest's ``product:`` key —
    no agent discovery, no config-dir/env overlays, no registries — so a
    product CLI can bootstrap identity before (or without) loading the
    whole project. Returns :data:`DEFAULT_PRODUCT_INFO` when the
    manifest has no ``product:`` section.

    Does NOT install the result; see :func:`bootstrap_product`.
    """
    p = Path(project_path).expanduser()
    if p.is_dir():
        p = p / "project.yml"
    if not p.is_file():
        raise FileNotFoundError(f"Could not find project manifest: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Project manifest is not a mapping: {p}")
    section = data.get("product")
    if section is None:
        return DEFAULT_PRODUCT_INFO
    try:
        return product_info_from_mapping(section)
    except ValueError as e:
        raise ValueError(f"{e} (file: {p})") from e


def bootstrap_product(project_path: Any) -> ProductInfo:
    """Load a manifest's product identity and install it explicitly.

    The single call a product's ``cli.py`` makes first:
    ``load_product_info(project_path)`` + :func:`set_product_info`.
    Returns the installed :class:`ProductInfo`. Because the install is
    explicit, later ``load_project`` calls never clobber it.
    """
    info = load_product_info(project_path)
    set_product_info(info)
    return info
