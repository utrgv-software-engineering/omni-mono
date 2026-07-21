"""Install omni-code's product identity (import for side effect).

Identity — CLI program name, platform slug, config-dir name, updater
knobs — is declared exactly once, in the ``product:`` section of the
packaged ``project.yml``. Importing this module bootstraps it
process-wide via :func:`omniagents.core.product.bootstrap_product`, so
every framework subsystem default (model/provider CLI prog, the
per-user config dir, the platform policy slug + credential store, the
codex token store, ``UpdaterConfig.from_product``, sandbox-profile user
layers) resolves to omni-code without per-subsystem plumbing.

Import-order rule: any module that reads identity — directly or through
a framework default — imports this module first (``omni_code.config``
does, so anything touching the config dir is covered transitively; the
standalone CLI entrypoints also import it explicitly). Python caches
module imports, so the bootstrap is idempotent and costs a single YAML
read of the packaged manifest for the whole process. Because
``bootstrap_product`` installs the identity *explicitly*, a later
``load_project`` of the same manifest never clobbers it.
"""

from __future__ import annotations

from pathlib import Path

from omniagents.core.product import ProductInfo, bootstrap_product

#: The packaged project manifest. agents/, tools/, capabilities/,
#: server_functions/ are subpackages beside it, so the same path works
#: identically for editable and wheel installs.
PROJECT_MANIFEST: Path = Path(__file__).resolve().parent / "project.yml"

#: omni-code's resolved product identity, installed process-wide on import.
PRODUCT: ProductInfo = bootstrap_product(PROJECT_MANIFEST)
