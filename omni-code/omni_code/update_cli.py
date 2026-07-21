"""``omni update`` — the framework's shared self-update flow bound to omni-code."""

import sys

from omniagents.core.updater import run_update_command

from omni_code.updater import UPDATER_CONFIG


def main(argv=None) -> None:
    sys.exit(run_update_command(argv, config=UPDATER_CONFIG, prog="omni update"))
