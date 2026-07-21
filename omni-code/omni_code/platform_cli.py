"""CLI for platform authentication.

Usage:
    omni auth login [url]     Authenticate with the management plane
    omni auth logout          Clear stored credentials
    omni auth status          Show current authentication status
    omni auth codex <cmd>     Manage ChatGPT (Codex) sign-in: login|logout|status

The login/logout/status flow is the shared framework command
(``omniagents.cli.run_platform_auth_command``) bound to omni-code's
program name, policy slug, and credential store.
"""

import sys


def main(args: list[str]) -> None:
    args = list(args or [])

    if args and args[0] == "codex":
        _codex(args[1:])
        return

    if not args or args[0] in {"-h", "--help", "help"}:
        print(
            "usage: omni auth <command>\n\n"
            "Commands:\n"
            "  login [url]    Authenticate with the management plane\n"
            "  logout         Clear stored credentials\n"
            "  status         Show current authentication status\n"
            "  codex <cmd>    ChatGPT (Codex) sign-in: login|logout|status\n"
        )
        return

    from omni_code.config import get_config_dir
    from omniagents.cli import run_platform_auth_command

    status = run_platform_auth_command(
        args, prog="omni", slug="omni_code", config_dir=get_config_dir()
    )
    if status:
        sys.exit(status)


def _codex(args: list[str]) -> None:
    # Importing codex_auth binds the framework's codex token store to
    # omni-code's config dir before the shared CLI flow runs.
    from omni_code import codex_auth  # noqa: F401

    from omniagents.cli import run_codex_auth_command

    status = run_codex_auth_command(args[0] if args else "login", prog="omni")
    if status:
        sys.exit(status)
