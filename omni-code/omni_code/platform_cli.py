"""CLI for platform authentication.

Usage:
    omni auth login [url]     Authenticate with the management plane
    omni auth logout          Clear stored credentials
    omni auth status          Show current authentication status
    omni auth codex <cmd>     Manage ChatGPT (Codex) sign-in: login|logout|status

The login/logout/status flow is the shared framework command
(``omniagents.cli.run_platform_auth_command``); program name, policy
slug, and credential store default from the product identity declared
in project.yml (installed by ``omni_code.identity``).
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

    from omni_code import identity  # noqa: F401  — installs the product identity
    from omniagents.cli import run_platform_auth_command

    status = run_platform_auth_command(args)
    if status:
        sys.exit(status)


def _codex(args: list[str]) -> None:
    # The codex token store and prog default from the product identity.
    from omni_code import identity  # noqa: F401  — installs the product identity
    from omniagents.cli import run_codex_auth_command

    status = run_codex_auth_command(args[0] if args else "login")
    if status:
        sys.exit(status)
