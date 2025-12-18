"""
Omni Code CLI - Full-stack coding agent built with OmniAgents.

This CLI delegates to the omniagents framework with project-specific configuration.
"""

import os
import sys
from pathlib import Path

from omni_code.config import load_config


def cli_main(argv=None) -> None:
    """Main entry point for the omni command."""
    from omniagents.cli import main as omni_main

    # Load config from ~/.config/omni-code/config.env
    for key, value in load_config().items():
        if key not in os.environ:
            os.environ[key] = value

    # Map OMNI_CODE_MODEL to MODEL_NAME for omniagents settings
    omni_model = os.environ.get("OMNI_CODE_MODEL")
    if omni_model and "MODEL_NAME" not in os.environ:
        os.environ["MODEL_NAME"] = omni_model

    # Build args for omniagents run
    project_path = Path(__file__).resolve().parent / "project.yml"
    new_args = ["omniagents", "run", "--project", str(project_path), "--mode", "ink"]

    # Pass through any additional args from user (can override --mode)
    if argv is not None:
        new_args.extend(argv)
    elif len(sys.argv) > 1:
        new_args.extend(sys.argv[1:])

    sys.argv = new_args
    omni_main()


if __name__ == "__main__":
    cli_main()
