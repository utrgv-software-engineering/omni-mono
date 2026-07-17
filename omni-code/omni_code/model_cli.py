"""``omni model ...`` — model management for Omni Code.

A thin product binding over the framework's model/provider CLI
(``omniagents.core.models.cli``): the wizards, listing, and test flows
are generic and operate on whatever store + program name they're given.
This module supplies omni-code's store and identity.
"""

from __future__ import annotations

from omniagents.core.models.cli import run_model_command

from omni_code.models import _STORE

__all__ = ["main", "setup_entrypoint"]


def main(argv=None) -> None:
    run_model_command(argv, store=_STORE, prog="omni", product_label="Omni Code")


def setup_entrypoint() -> None:
    main(["setup"])


if __name__ == "__main__":
    main()
