"""
Omni Code CLI - Full-stack coding agent built with OmniAgents.

This CLI delegates to the omniagents framework with project-specific configuration.
"""

import os
import sys
from pathlib import Path


def _load_models_config() -> None:
    """Load models config and apply API keys to environment.

    This handles auto-migration from legacy config.env to models.json.
    """
    from omni_code.models import (
        load_models_config,
        apply_api_keys_to_env,
        resolve_model_for_runtime,
    )

    # Load config (auto-migrates from legacy if needed)
    config = load_models_config()

    # Apply API keys to environment
    apply_api_keys_to_env()

    # Set MODEL_NAME from default model config
    if "MODEL_NAME" not in os.environ:
        model_runtime = resolve_model_for_runtime()
        if model_runtime.get("model"):
            os.environ["MODEL_NAME"] = model_runtime["model"]

            # For Azure and openai-compatible, set the base URL
            provider = model_runtime.get("provider")
            if provider in ("azure", "openai-compatible") and model_runtime.get("base_url"):
                if "OPENAI_BASE_URL" not in os.environ:
                    os.environ["OPENAI_BASE_URL"] = model_runtime["base_url"]

            # For Azure, also set the API key header approach
            if provider == "azure" and model_runtime.get("api_key"):
                if "AZURE_OPENAI_API_KEY" not in os.environ:
                    os.environ["AZURE_OPENAI_API_KEY"] = model_runtime["api_key"]


def cli_main(argv=None) -> None:
    """Main entry point for the omni command."""
    args = argv if argv is not None else sys.argv[1:]

    # Handle 'omni model' subcommand
    if args and args[0] == "model":
        from omni_code.model_cli import main as model_main
        return model_main(args[1:])

    # Load models config (with auto-migration from legacy)
    _load_models_config()

    # Check if models are configured, prompt setup if not
    from omni_code.models import has_models_configured
    if not has_models_configured():
        print("No model configured. Starting setup wizard...\n")
        from omni_code.model_cli import main as model_main
        model_main(["setup"])
        print()  # Blank line before starting

    # Run omniagents
    from omniagents.cli import main as omni_main

    project_path = Path(__file__).resolve().parent / "project.yml"
    new_args = ["omniagents", "run", "--project", str(project_path), "--mode", "ink"]
    new_args.extend(args)

    sys.argv = new_args
    omni_main()


if __name__ == "__main__":
    cli_main()
