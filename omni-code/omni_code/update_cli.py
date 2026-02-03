import argparse
import sys

from omni_code.updater import (
    GEMFURY_SIMPLE_INDEX_URL,
    check_for_update,
    run_pipx_upgrade,
)


def _prompt_confirm(prompt: str) -> bool:
    raw = input(prompt).strip().lower()
    return raw in ("y", "yes")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="omni update",
        description="Update omni-code (pipx install recommended)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check for updates (exit 1 if an update is available)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not prompt; proceed with upgrade",
    )
    parser.add_argument(
        "--simple-index-url",
        default=GEMFURY_SIMPLE_INDEX_URL,
        help="Override Gemfury simple index URL",
    )

    args = parser.parse_args(argv)

    result = check_for_update(simple_index_url=args.simple_index_url)

    if not result.latest_version:
        print("Unable to determine latest version.")
        sys.exit(1)

    if not result.update_available:
        print(f"omni-code is up to date ({result.current_version}).")
        sys.exit(0)

    print(f"Update available: {result.current_version} -> {result.latest_version}")

    if args.check:
        sys.exit(1)

    if not args.yes:
        if not _prompt_confirm("Run pipx upgrade now? [y/N]: "):
            sys.exit(0)

    run_pipx_upgrade()
    print("Upgrade complete. Re-run `omni` to use the new version.")
