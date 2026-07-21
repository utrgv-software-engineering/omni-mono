"""``omni provider ...`` — provider management for Omni Code.

A thin product binding over the framework's model/provider CLI
(``omniagents.core.models.cli``); see ``model_cli.py``.
"""

from __future__ import annotations

from omni_code import identity  # noqa: F401  — installs the product identity
from omniagents.core.models.cli import run_provider_command

from omni_code.models import _STORE

__all__ = ["main"]


def main(argv=None) -> None:
    run_provider_command(argv, store=_STORE)


if __name__ == "__main__":
    main()
