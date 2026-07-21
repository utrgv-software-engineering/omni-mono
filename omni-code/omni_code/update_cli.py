"""``omni update`` — the framework's shared self-update flow.

The config (Gemfury index, package name, cache dir, env prefix, prog)
defaults via ``UpdaterConfig.from_product`` from the product identity
declared in project.yml (installed by ``omni_code.identity``).
"""

import sys

from omni_code import identity  # noqa: F401  — installs the product identity
from omniagents.core.updater import run_update_command


def main(argv=None) -> None:
    sys.exit(run_update_command(argv))
