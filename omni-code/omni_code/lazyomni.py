"""lazyomni — a lazygit-style TUI for managing Omni projects, tickets, and agents."""

import argparse
import sys

from omni_code.projects_runtime import get_fleet_store_path
from omni_code.tui_launcher import launch_projects_tui


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="lazyomni",
        description="Terminal UI for managing Omni projects, tickets, and agents",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    launch_projects_tui(projects_config_path=get_fleet_store_path(), debug=args.debug)
