"""
Omni Code CLI - Full-stack coding agent built with OmniAgents.

This CLI delegates to the omniagents framework with project-specific configuration.
"""

import os
import sys
from pathlib import Path


def _load_models_config() -> None:
    """Load models config and set MODEL_NAME for the framework.

    Each model's api_key / base_url are resolved at runtime by the service
    layer when creating the per-request model_provider.  We intentionally
    do NOT write OPENAI_API_KEY or OPENAI_BASE_URL into the environment
    here because that would poison the process: after a /model switch the
    stale env values would still be read by the OpenAI SDK.
    """
    from omni_code.models import (
        load_models_config,
        resolve_model_for_runtime,
    )

    # Load config
    load_models_config()

    # Set MODEL_NAME so the framework knows the default model identifier.
    if "MODEL_NAME" not in os.environ:
        model_runtime = resolve_model_for_runtime()
        if model_runtime.get("model"):
            os.environ["MODEL_NAME"] = model_runtime["model"]


def cli_main(argv=None) -> None:
    """Main entry point for the omni command."""
    args = argv if argv is not None else sys.argv[1:]

    # Handle 'omni model' subcommand
    if args and args[0] == "model":
        from omni_code.model_cli import main as model_main
        return model_main(args[1:])

    # Handle 'omni provider' subcommand
    if args and args[0] == "provider":
        from omni_code.provider_cli import main as provider_main
        return provider_main(args[1:])

    if args and args[0] == "update":
        from omni_code.update_cli import main as update_main
        return update_main(args[1:])

    # Load models config
    _load_models_config()

    # Check if a usable model is configured, prompt setup if not
    from omni_code.models import has_models_configured
    if not has_models_configured():
        print("No model credentials configured. Starting setup wizard...\n")
        from omni_code.model_cli import main as model_main
        model_main(["setup"])
        print()  # Blank line before starting

    from omni_code.updater import maybe_print_update_notice

    maybe_print_update_notice()

    # Run omniagents
    from omniagents.cli import main as omni_main

    project_path = Path(__file__).resolve().parent / "project.yml"
    new_args = ["omniagents", "run", "--project", str(project_path), "--mode", "ink"]
    new_args.extend(args)

    sys.argv = new_args
    omni_main()


if __name__ == "__main__":
    cli_main()
