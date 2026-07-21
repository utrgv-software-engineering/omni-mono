"""omni-code: a full-stack coding agent built with OmniAgents.

This package is side-effect free on import. omni-code's product
identity — CLI program name, product slug, config-dir name, updater
knobs — is declared exactly once in the ``product:`` section of the
packaged ``project.yml`` and resolved by the framework:

- The distribution declares an ``omniagents.products`` entry point
  (``omni-code = "omni_code"``) pointing at this package, so
  :func:`omniagents.core.product.product_info` discovers the identity
  lazily even on bare module imports with no prior bootstrap.
- The console scripts (``omniagents.product_cli:main`` /
  ``:setup_main``) bootstrap it explicitly (identity + the declared
  model catalog) before dispatching.
"""

from ._version import __version__, __version_info__

__all__ = ["__version__", "__version_info__"]
