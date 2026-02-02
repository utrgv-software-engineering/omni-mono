"""CLI for model management.

Usage:
    omni model setup          Interactive setup wizard
    omni model list           List configured models
    omni model add            Add a new model
    omni model remove <name>  Remove a model
    omni model default <name> Set default model
    omni model test [name]    Test model connection
    omni model show <name>    Show model details
"""

import argparse
import os
import sys
from getpass import getpass
from typing import Optional


def prompt_choice(prompt: str, options: list, default: Optional[str] = None) -> str:
    """Prompt user to select from numbered options."""
    while True:
        for i, opt in enumerate(options, 1):
            marker = "*" if opt.get("value") == default else " "
            print(f"  {marker}[{i}] {opt['label']}")

        suffix = f" [{default}]" if default else ""
        raw = input(f"{prompt}{suffix}: ").strip()

        if not raw and default:
            return default

        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]["value"]
        except ValueError:
            # Try matching by value
            for opt in options:
                if opt["value"].lower() == raw.lower():
                    return opt["value"]

        print("Invalid choice. Please try again.")


def prompt_text(
    label: str, default: Optional[str] = None, required: bool = False
) -> Optional[str]:
    """Prompt for text input."""
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{label}{suffix}: ").strip()

        if not raw:
            if default:
                return default
            if required:
                print(f"{label} is required.")
                continue
            return None
        return raw


def prompt_secret(
    label: str, default: Optional[str] = None, required: bool = False
) -> Optional[str]:
    """Prompt for secret input (hidden)."""
    while True:
        suffix = " (leave blank to keep existing)" if default else ""
        raw = getpass(f"{label}{suffix}: ").strip()

        if not raw:
            if default:
                return default
            if required:
                print(f"{label} is required.")
                continue
            return None
        return raw


def cmd_setup(args):
    """Interactive setup wizard."""
    from omni_code.models import (
        load_models_config,
        save_models_config,
        get_models_config_path,
    )

    print("Omni Code Model Setup")
    print("=" * 40)
    print()

    config = load_models_config()

    # Provider selection
    print("Choose provider type:")
    providers = [
        {"value": "openai", "label": "OpenAI (api.openai.com)"},
        {"value": "azure", "label": "Azure OpenAI"},
        {"value": "openai-compatible", "label": "OpenAI-compatible (Ollama, LM Studio, vLLM, etc.)"},
        {"value": "litellm", "label": "Other provider via LiteLLM (Anthropic, Google, etc.)"},
    ]
    provider = prompt_choice("Enter choice", providers, "openai")
    print()

    model_name = "default"
    model_config = {}

    if provider == "openai":
        # OpenAI setup
        api_key = prompt_secret("OpenAI API key", required=True)
        model = prompt_text("Model name", "gpt-5", required=True)
        model_name = prompt_text("Config name (for /model command)", model or "gpt-5")

        model_config = {
            "provider": "openai",
            "model": model,
            "label": model,
            "api_key": api_key,
        }

    elif provider == "azure":
        # Azure setup
        api_key = prompt_secret("Azure OpenAI API key", required=True)
        endpoint = prompt_text("Azure endpoint URL", required=True)
        deployment = prompt_text("Deployment name", required=True)
        api_version = prompt_text("API version", "2024-08-01-preview", required=True)

        model_name = prompt_text("Config name", deployment or "azure")

        model_config = {
            "provider": "azure",
            "model": deployment,
            "label": f"Azure ({deployment})",
            "base_url": endpoint,
            "api_version": api_version,
            "api_key": api_key,
        }

    elif provider == "openai-compatible":
        # Custom endpoint setup
        base_url = prompt_text(
            "Base URL (e.g., http://localhost:11434/v1)", required=True
        )
        model = prompt_text("Model name", required=True)

        needs_key = prompt_text("Requires API key? (y/n)", "n")
        api_key = None
        if needs_key.lower() in ("y", "yes"):
            api_key = prompt_secret("API key", required=True)

        model_name = prompt_text("Config name", model)
        label = prompt_text("Display label", model)

        model_config = {
            "provider": "openai-compatible",
            "model": model,
            "label": label,
            "base_url": base_url,
        }
        if api_key:
            model_config["api_key"] = api_key

    elif provider == "litellm":
        # LiteLLM setup
        print("\nLiteLLM supports many providers. Examples:")
        print("  anthropic/claude-3-5-sonnet-20241022")
        print("  gemini/gemini-1.5-pro")
        print("  mistral/mistral-large-latest")
        print("  groq/llama-3.1-70b-versatile")
        print()

        model = prompt_text("Model identifier (provider/model)", required=True)

        # Detect provider and prompt for API key
        if model.startswith("anthropic/") or model.startswith("claude"):
            key_label = "Anthropic API key"
        elif model.startswith("gemini/") or model.startswith("google/"):
            key_label = "Google API key"
        elif model.startswith("mistral/"):
            key_label = "Mistral API key"
        elif model.startswith("groq/"):
            key_label = "Groq API key"
        elif model.startswith("deepseek/"):
            key_label = "DeepSeek API key"
        else:
            key_label = "API key"

        api_key = prompt_secret(key_label, required=True)

        # Extract simple name for config
        simple_name = model.split("/")[-1] if "/" in model else model
        model_name = prompt_text("Config name", simple_name)
        label = prompt_text("Display label", simple_name)

        model_config = {
            "provider": "litellm",
            "model": model,
            "label": label,
            "api_key": api_key,
        }

    # Reasoning effort
    print()
    print("Default reasoning effort (for reasoning models):")
    reasoning_options = [
        {"value": "low", "label": "Low - faster, less thorough"},
        {"value": "medium", "label": "Medium - balanced"},
        {"value": "high", "label": "High - slower, more thorough"},
    ]
    reasoning = prompt_choice("Enter choice", reasoning_options, "low")
    if reasoning != "low":
        model_config["reasoning"] = reasoning

    # Check if this is a reasoning model (GPT-5.x) and add model_settings
    model_id = model_config.get("model", "")
    if "gpt-5" in model_id.lower() or "o1" in model_id.lower() or "o3" in model_id.lower():
        print()
        print("Detected reasoning model. Adding required model_settings for store=false.")
        model_config["model_settings"] = {
            "store": False,
            "extra_body": {"include": ["reasoning.encrypted_content"]}
        }

    # Save
    if "models" not in config:
        config["models"] = {}
    config["models"][model_name] = model_config

    # Set as default if first model or user wants
    if not config.get("default") or len(config["models"]) == 1:
        config["default"] = model_name
    else:
        set_default = prompt_text(f"Set '{model_name}' as default? (y/n)", "y")
        if set_default.lower() in ("y", "yes"):
            config["default"] = model_name

    save_models_config(config)
    print()
    print(f"Configuration saved to {get_models_config_path()}")
    print("Run 'omni' to start the assistant.")


def cmd_list(args):
    """List available models."""
    from omni_code.models import list_models, get_default_model_name

    models = list_models()
    default_name = get_default_model_name()

    if not models:
        print("No models available.")
        return

    # Separate user-defined and built-in models
    user_models = [m for m in models if m.get("is_user_defined")]
    builtin_models = [m for m in models if not m.get("is_user_defined")]

    def print_model(m):
        marker = "*" if m["name"] == default_name else " "
        default_label = " (default)" if m["is_default"] else ""
        reasoning = f" [reasoning: {m['reasoning']}]" if m.get("reasoning") else ""
        tokens = ""
        if m.get("max_input_tokens") or m.get("max_output_tokens"):
            input_t = f"{m['max_input_tokens']//1000}k" if m.get("max_input_tokens") else "?"
            output_t = f"{m['max_output_tokens']//1000}k" if m.get("max_output_tokens") else "?"
            tokens = f" [{input_t}/{output_t}]"
        key_indicator = " [key]" if m.get("has_api_key") else ""
        print(
            f"  {marker} {m['name']} - {m['label']} [{m['provider']}]{default_label}{reasoning}{tokens}{key_indicator}"
        )

    if builtin_models:
        print("Built-in models:")
        for m in builtin_models:
            print_model(m)

    if user_models:
        if builtin_models:
            print()
        print("User-defined models:")
        for m in user_models:
            print_model(m)


def cmd_add(args):
    """Add a new model (interactive or with flags)."""
    from omni_code.models import add_model, get_model_config

    name = args.name
    if not name:
        name = prompt_text("Model config name", required=True)

    if get_model_config(name):
        print(f"Model '{name}' already exists. Use 'omni model remove {name}' first.")
        sys.exit(1)

    provider = args.provider
    if not provider:
        providers = [
            {"value": "openai", "label": "OpenAI"},
            {"value": "azure", "label": "Azure OpenAI"},
            {"value": "openai-compatible", "label": "OpenAI-compatible endpoint"},
            {"value": "litellm", "label": "LiteLLM provider"},
        ]
        provider = prompt_choice("Provider", providers)

    model = args.model
    if not model:
        model = prompt_text("Model identifier", required=True)

    base_url = args.base_url
    if provider in ("azure", "openai-compatible") and not base_url:
        base_url = prompt_text("Base URL", required=True)

    api_key = args.api_key
    if not api_key:
        api_key = prompt_secret("API key (or leave blank to use env var)")

    label = args.label or name

    # Auto-detect reasoning model and add model_settings
    model_settings = None
    if "gpt-5" in model.lower() or "o1" in model.lower() or "o3" in model.lower():
        print("Detected reasoning model. Adding required model_settings.")
        model_settings = {
            "store": False,
            "extra_body": {"include": ["reasoning.encrypted_content"]}
        }

    add_model(
        name=name,
        provider=provider,
        model=model,
        api_key=api_key if api_key else None,
        label=label,
        base_url=base_url,
        api_version=args.api_version,
        reasoning=args.reasoning,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        model_settings=model_settings,
        set_default=args.set_default,
    )

    print(f"Added model '{name}'")


def cmd_remove(args):
    """Remove a model."""
    from omni_code.models import remove_model

    if not args.name:
        print("Usage: omni model remove <name>")
        sys.exit(1)

    if remove_model(args.name):
        print(f"Removed model '{args.name}'")
    else:
        print(f"Model '{args.name}' not found or is a built-in model")
        sys.exit(1)


def cmd_default(args):
    """Set default model."""
    from omni_code.models import set_default_model

    if not args.name:
        print("Usage: omni model default <name>")
        sys.exit(1)

    if set_default_model(args.name):
        print(f"Set '{args.name}' as default model")
    else:
        print(f"Model '{args.name}' not found")
        sys.exit(1)


def cmd_test(args):
    """Test model connection."""
    from omni_code.models import resolve_model_for_runtime, get_default_model_name

    name = args.name or get_default_model_name()
    if not name:
        print("No model configured. Run 'omni model setup' first.")
        sys.exit(1)

    config = resolve_model_for_runtime(name)
    if not config:
        print(f"Model '{name}' not found")
        sys.exit(1)

    print(f"Testing {config['label']} ({config['provider']})...")

    provider = config.get("provider")
    model = config.get("model")
    api_key = config.get("api_key")
    base_url = config.get("base_url")

    if not api_key:
        print(f"Warning: No API key configured for '{name}'")
        print("Set one with: omni model add --name {name} --api-key <key>")

    try:
        if provider == "litellm":
            import litellm

            # Set api key in env for litellm
            if api_key:
                model_str = model or ""
                if model_str.startswith("anthropic/") or model_str.startswith("claude"):
                    os.environ["ANTHROPIC_API_KEY"] = api_key
                elif model_str.startswith("gemini/") or model_str.startswith("google/"):
                    os.environ["GOOGLE_API_KEY"] = api_key
                else:
                    os.environ["OPENAI_API_KEY"] = api_key

            response = litellm.completion(
                model=model,
                messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
                max_tokens=10,
            )
            reply = response.choices[0].message.content
            print(f"Response: {reply}")
            print("Connection successful!")

        elif provider == "azure":
            from openai import AzureOpenAI

            client = AzureOpenAI(
                api_key=api_key,
                api_version=config.get("api_version", "2024-08-01-preview"),
                azure_endpoint=base_url,
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
                max_tokens=10,
            )
            reply = response.choices[0].message.content
            print(f"Response: {reply}")
            print("Connection successful!")

        else:  # openai or openai-compatible
            from openai import OpenAI

            client_kwargs = {}
            if api_key:
                client_kwargs["api_key"] = api_key
            if base_url:
                client_kwargs["base_url"] = base_url

            client = OpenAI(**client_kwargs)
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Say 'hello' and nothing else."}],
                max_tokens=10,
            )
            reply = response.choices[0].message.content
            print(f"Response: {reply}")
            print("Connection successful!")

    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)


def cmd_show(args):
    """Show model details."""
    from omni_code.models import get_model_config, get_default_model_name

    name = args.name
    if not name:
        print("Usage: omni model show <name>")
        sys.exit(1)

    config = get_model_config(name)
    if not config:
        print(f"Model '{name}' not found")
        sys.exit(1)

    default_name = get_default_model_name()
    is_default = name == default_name

    print(f"Model: {name}" + (" (default)" if is_default else ""))
    print(f"  Provider: {config.get('provider')}")
    print(f"  Model ID: {config.get('model')}")
    print(f"  Label: {config.get('label')}")
    if config.get("base_url"):
        print(f"  Base URL: {config.get('base_url')}")
    if config.get("api_version"):
        print(f"  API Version: {config.get('api_version')}")
    if config.get("api_key"):
        # Mask the API key
        key = config.get("api_key")
        if key.startswith("${"):
            print(f"  API Key: {key}")
        else:
            print(f"  API Key: {key[:8]}...{key[-4:]}" if len(key) > 12 else "  API Key: ****")
    if config.get("reasoning"):
        print(f"  Reasoning: {config.get('reasoning')}")
    if config.get("max_input_tokens"):
        print(f"  Max Input Tokens: {config.get('max_input_tokens'):,}")
    if config.get("max_output_tokens"):
        print(f"  Max Output Tokens: {config.get('max_output_tokens'):,}")
    if config.get("model_settings"):
        print(f"  Model Settings: {config.get('model_settings')}")


def main(argv=None):
    """Main entry point for model CLI."""
    parser = argparse.ArgumentParser(
        prog="omni model",
        description="Manage model configurations for Omni Code",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # setup
    subparsers.add_parser("setup", help="Interactive setup wizard")

    # list
    subparsers.add_parser("list", help="List configured models")

    # add
    add_parser = subparsers.add_parser("add", help="Add a new model")
    add_parser.add_argument("--name", "-n", help="Config name for this model")
    add_parser.add_argument(
        "--provider",
        "-p",
        choices=["openai", "azure", "openai-compatible", "litellm"],
        help="Provider type",
    )
    add_parser.add_argument("--model", "-m", help="Model identifier")
    add_parser.add_argument("--api-key", "-k", help="API key for this model")
    add_parser.add_argument("--label", "-l", help="Display label")
    add_parser.add_argument("--base-url", "-u", help="Base URL (for azure/compatible)")
    add_parser.add_argument("--api-version", help="API version (for azure)")
    add_parser.add_argument(
        "--reasoning",
        choices=["low", "medium", "high"],
        help="Default reasoning effort",
    )
    add_parser.add_argument(
        "--max-input-tokens",
        type=int,
        help="Maximum input context tokens",
    )
    add_parser.add_argument(
        "--max-output-tokens",
        type=int,
        help="Maximum output tokens",
    )
    add_parser.add_argument(
        "--default", dest="set_default", action="store_true", help="Set as default"
    )

    # remove
    remove_parser = subparsers.add_parser("remove", help="Remove a model")
    remove_parser.add_argument("name", nargs="?", help="Model name to remove")

    # default
    default_parser = subparsers.add_parser("default", help="Set default model")
    default_parser.add_argument("name", nargs="?", help="Model name to set as default")

    # test
    test_parser = subparsers.add_parser("test", help="Test model connection")
    test_parser.add_argument("name", nargs="?", help="Model name to test (default: current)")

    # show
    show_parser = subparsers.add_parser("show", help="Show model details")
    show_parser.add_argument("name", nargs="?", help="Model name to show")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "setup": cmd_setup,
        "list": cmd_list,
        "add": cmd_add,
        "remove": cmd_remove,
        "default": cmd_default,
        "test": cmd_test,
        "show": cmd_show,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


# Entry point for backward compatibility with omni-setup
def setup_entrypoint():
    """Entry point that mimics old omni-setup behavior."""
    main(["setup"])


if __name__ == "__main__":
    main()
