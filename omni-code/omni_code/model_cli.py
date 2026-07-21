"""``omni model ...`` — model management for Omni Code.

A thin product binding over the framework's model/provider CLI
(``omniagents.core.models.cli``): the wizards, listing, and test flows
are generic and operate on whatever store + identity they're given.
This module supplies omni-code's store; prog/label default from the
product identity installed by ``omni_code.identity``.
"""

from __future__ import annotations

from omni_code import identity  # noqa: F401  — installs the product identity
from omniagents.core.models.cli import run_model_command

from omni_code.models import _STORE

__all__ = ["main", "setup_entrypoint"]


def main(argv=None) -> None:
    run_model_command(argv, store=_STORE)


def setup_entrypoint() -> None:
    main(["setup"])


if __name__ == "__main__":
    main()
