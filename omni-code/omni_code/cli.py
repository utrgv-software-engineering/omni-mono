"""Console-script entrypoints — dispatch lives in the framework.

The framework's product CLI host (:func:`omniagents.product_cli.product_main`)
drives everything from the packaged project.yml: the ``product:`` section
declares identity, the ``cli:`` section declares the default run mode and
the extensions module (:mod:`omni_code.cli_extensions`) carrying
omni-code's preflights and extra commands.
"""

from __future__ import annotations


def cli_main(argv=None) -> int:
    from omni_code import PROJECT_MANIFEST
    from omniagents.product_cli import product_main

    return product_main(PROJECT_MANIFEST, argv)


def setup_main() -> None:
    """``omni-setup`` — the model setup wizard, standalone."""
    import omni_code  # noqa: F401 — bootstrap installs the catalog store

    from omniagents.core.models import model_store
    from omniagents.core.models.cli import run_model_command

    run_model_command(["setup"], store=model_store())
