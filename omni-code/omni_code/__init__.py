"""omni-code: a full-stack coding agent built with OmniAgents.

Importing this package installs omni-code's product identity — CLI
program name, platform slug, config-dir name, updater knobs — declared
exactly once in the ``product:`` section of the packaged ``project.yml``
and bootstrapped process-wide via
:func:`omniagents.core.product.bootstrap_product`. Every framework
subsystem default (CLI prog rendering, the per-user config dir, the
platform policy slug + credential store, the codex token store,
``UpdaterConfig.from_product``, sandbox-profile user layers) then
resolves to omni-code without per-subsystem plumbing.

The bootstrap is idempotent (Python caches package import) and costs one
YAML read of the packaged manifest. Because it installs the identity
*explicitly*, a later ``load_project`` of the same manifest never
clobbers it.
"""

from pathlib import Path

from omniagents.core.product import ProductInfo, bootstrap_product

from ._version import __version__, __version_info__

#: The packaged project manifest. agents/, tools/, capabilities/,
#: server_functions/ are subpackages beside it, so the same path works
#: identically for editable and wheel installs.
PROJECT_MANIFEST: Path = Path(__file__).resolve().parent / "project.yml"

#: omni-code's resolved product identity, installed process-wide on import.
PRODUCT: ProductInfo = bootstrap_product(PROJECT_MANIFEST)

__all__ = ["PRODUCT", "PROJECT_MANIFEST", "__version__", "__version_info__"]
