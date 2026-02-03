"""CLI for model management.

Usage:
    omni model setup                 Interactive setup wizard
    omni model list                  List configured models
    omni model add                   Add a new model (and provider profile if needed)
    omni model remove <ref>          Remove a user-defined model
    omni model default <ref>         Set default model
    omni model test [ref]            Test model connection
    omni model show <ref>            Show model details

Model references:
    gpt-5.1              -> openai/gpt-5.1
    azure-prod/gpt-5.2   -> provider profile "azure-prod", model "gpt-5.2"
"""

import argparse
import os
import sys
from getpass import getpass
from typing import Optional


def prompt_choice(prompt: str, options: list, default: Optional[str] = None) -> str:
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
            for opt in options:
                if opt["value"].lower() == raw.lower():
                    return opt["value"]

        print("Invalid choice. Please try again.")


def prompt_text(label: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    while True:
        suffix = f" [{default}]" if default else ""
        raw = input(f"{label}{suffix}: ").strip()

        if not raw:
            if default is not None:
                return default
            if required:
                print(f"{label} is required.")
                continue
            return None
        return raw


def prompt_secret(label: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    while True:
        suffix = " (leave blank to keep existing)" if default else ""
        raw = getpass(f"{label}{suffix}: ").strip()

        if not raw:
            if default is not None:
                return default
            if required:
                print(f"{label} is required.")
                continue
            return None
        return raw


def cmd_setup(args):
    from omni_code.models import add_model, get_models_config_path, load_models_config

    print("Omni Code Model Setup")
    print("=" * 40)
    print()

    existing_config = load_models_config()

    print("Choose provider type:")
    providers = [
        {"value": "openai", "label": "OpenAI (api.openai.com)"},
        {"value": "azure", "label": "Azure OpenAI"},
        {
            "value": "openai-compatible",
            "label": "OpenAI-compatible (Ollama, LM Studio, vLLM, etc.)",
        },
        {"value": "litellm", "label": "Other provider via LiteLLM (Anthropic, Google, etc.)"},
    ]
    provider_type = prompt_choice("Enter choice", providers, "openai")
    print()

    provider_name_default = {
        "openai": "openai",
        "azure": "azure",
        "openai-compatible": "local",
        "litellm": "litellm",
    }.get(provider_type, "openai")
    provider_name = prompt_text("Provider profile name", provider_name_default, required=True) or provider_name_default

    provider_api_key = None
    provider_base_url = None
    provider_api_version = None

    model_key = ""
    model_id = ""

    if provider_type == "openai":
        provider_api_key = prompt_secret("OpenAI API key", required=True)
        model_id = prompt_text("Model identifier", "gpt-5", required=True) or "gpt-5"
        model_key = prompt_text("Model name (for /model)", model_id, required=True) or model_id

    elif provider_type == "azure":
        provider_api_key = prompt_secret("Azure OpenAI API key", required=True)
        provider_base_url = prompt_text("Azure endpoint URL", required=True)
        provider_api_version = prompt_text("API version", "2024-08-01-preview", required=True)
        model_key = prompt_text("Model name (for /model)", "gpt", required=True) or "gpt"
        model_id = prompt_text("Deployment name", required=True) or model_key

    elif provider_type == "openai-compatible":
        provider_base_url = prompt_text("Base URL (e.g., http://localhost:11434/v1)", required=True)
        needs_key = prompt_text("Requires API key? (y/n)", "n")
        if needs_key and needs_key.lower() in ("y", "yes"):
            provider_api_key = prompt_secret("API key", required=True)
        model_id = prompt_text("Model identifier", required=True) or ""
        model_key = prompt_text("Model name (for /model)", model_id, required=True) or model_id

    elif provider_type == "litellm":
        print("\nLiteLLM supports many providers. Examples:")
        print("  anthropic/claude-3-5-sonnet-20241022")
        print("  gemini/gemini-1.5-pro")
        print("  mistral/mistral-large-latest")
        print("  groq/llama-3.1-70b-versatile")
        print()

        model_id = prompt_text("Model identifier (provider/model)", required=True) or ""
        inferred_profile = model_id.split("/", 1)[0] if "/" in model_id else provider_name
        provider_name = prompt_text("Provider profile name", inferred_profile, required=True) or inferred_profile

        if model_id.startswith("anthropic/") or model_id.startswith("claude"):
            key_label = "Anthropic API key"
        elif model_id.startswith("gemini/") or model_id.startswith("google/"):
            key_label = "Google API key"
        elif model_id.startswith("mistral/"):
            key_label = "Mistral API key"
        elif model_id.startswith("groq/"):
            key_label = "Groq API key"
        elif model_id.startswith("deepseek/"):
            key_label = "DeepSeek API key"
        else:
            key_label = "API key"

        provider_api_key = prompt_secret(key_label, required=True)
        model_key_default = model_id.split("/")[-1] if "/" in model_id else model_id
        model_key = prompt_text("Model name (for /model)", model_key_default, required=True) or model_key_default

    label = prompt_text("Display label", model_key) or model_key

    print()
    print("Default reasoning effort (for reasoning models):")
    reasoning_options = [
        {"value": "low", "label": "Low - faster, less thorough"},
        {"value": "medium", "label": "Medium - balanced"},
        {"value": "high", "label": "High - slower, more thorough"},
    ]
    reasoning = prompt_choice("Enter choice", reasoning_options, "low")
    reasoning_value = None if reasoning == "low" else reasoning

    model_settings = None
    if "gpt-5" in model_id.lower() or "o1" in model_id.lower() or "o3" in model_id.lower():
        print()
        print("Detected reasoning model. Adding required model_settings for store=false.")
        model_settings = {
            "store": False,
            "extra_body": {"include": ["reasoning.encrypted_content"]},
        }

    make_default = False
    if not existing_config.get("default"):
        make_default = True
    else:
        should_set = prompt_text(f"Set '{provider_name}/{model_key}' as default? (y/n)", "y")
        if should_set and should_set.lower() in ("y", "yes"):
            make_default = True

    add_model(
        provider_name=provider_name,
        provider_type=provider_type,
        model_name=model_key,
        model=model_id,
        label=label,
        provider_api_key=provider_api_key,
        provider_base_url=provider_base_url,
        provider_api_version=provider_api_version,
        reasoning=reasoning_value,
        model_settings=model_settings,
        set_default=make_default,
    )

    print()
    print(f"Configuration saved to {get_models_config_path()}")
    print("Run 'omni' to start the assistant.")


def cmd_list(args):
    from omni_code.models import get_default_model_name, list_models

    models = list_models()
    default_name = get_default_model_name()

    if not models:
        print("No models available.")
        return

    user_models = [m for m in models if m.get("is_user_defined")]
    builtin_models = [m for m in models if not m.get("is_user_defined")]

    def print_model(m):
        marker = "*" if m["name"] == default_name else " "
        default_label = " (default)" if m.get("is_default") else ""
        reasoning = f" [reasoning: {m['reasoning']}]" if m.get("reasoning") else ""
        tokens = ""
        if m.get("max_input_tokens") or m.get("max_output_tokens"):
            input_t = f"{m['max_input_tokens']//1000}k" if m.get("max_input_tokens") else "?"
            output_t = f"{m['max_output_tokens']//1000}k" if m.get("max_output_tokens") else "?"
            tokens = f" [{input_t}/{output_t}]"
        key_indicator = " [key]" if m.get("has_api_key") else ""
        print(f"  {marker} {m['name']} - {m['label']} [{m['provider']}]{default_label}{reasoning}{tokens}{key_indicator}")

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
    from omni_code.models import add_model, get_model_config, normalize_model_ref

    ref = args.name
    if not ref:
        ref = prompt_text(
            "Model reference (e.g. gpt-5.1 or azure-prod/gpt-5.2)", required=True
        )
    ref = (ref or "").strip()
    if not ref:
        print("Invalid model reference.")
        sys.exit(1)

    if "/" in ref:
        target_ref = normalize_model_ref(ref)
        if not target_ref:
            print("Invalid model reference.")
            sys.exit(1)
        provider_name, model_name = (
            target_ref.split("/", 1) if "/" in target_ref else ("openai", target_ref)
        )
    else:
        provider_name = (args.provider or "openai").strip()
        model_name = ref
        target_ref = model_name if provider_name == "openai" else f"{provider_name}/{model_name}"

    if get_model_config(target_ref) and not args.force:
        print(f"Model '{target_ref}' already exists. Use --force to overwrite.")
        sys.exit(1)

    provider_type = args.type
    if not provider_type:
        existing = get_model_config(target_ref)
        if existing and existing.get("provider"):
            provider_type = existing["provider"]
    if not provider_type:
        providers = [
            {"value": "openai", "label": "OpenAI"},
            {"value": "azure", "label": "Azure OpenAI"},
            {"value": "openai-compatible", "label": "OpenAI-compatible endpoint"},
            {"value": "litellm", "label": "LiteLLM"},
        ]
        provider_type = prompt_choice("Provider type", providers, "openai")

    model_id = args.model
    if not model_id:
        prompt_label = "Deployment name" if provider_type == "azure" else "Model identifier"
        model_id = prompt_text(prompt_label, required=True)

    provider_base_url = args.base_url
    provider_api_version = args.api_version
    if provider_type in ("azure", "openai-compatible") and not provider_base_url:
        provider_base_url = prompt_text("Base URL", required=True)
    if provider_type == "azure" and not provider_api_version:
        provider_api_version = prompt_text("API version", "2024-08-01-preview", required=True)

    provider_api_key = args.api_key
    if not provider_api_key and provider_type != "openai-compatible":
        provider_api_key = prompt_secret("API key (or leave blank to use env var)")

    label = args.label or model_name

    model_settings = None
    if "gpt-5" in (model_id or "").lower() or "o1" in (model_id or "").lower() or "o3" in (model_id or "").lower():
        print("Detected reasoning model. Adding required model_settings.")
        model_settings = {
            "store": False,
            "extra_body": {"include": ["reasoning.encrypted_content"]},
        }

    add_model(
        provider_name=provider_name,
        provider_type=provider_type,
        model_name=model_name,
        model=model_id,
        label=label,
        provider_api_key=provider_api_key if provider_api_key else None,
        provider_base_url=provider_base_url,
        provider_api_version=provider_api_version,
        reasoning=args.reasoning,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        model_settings=model_settings,
        set_default=args.set_default,
    )

    print(f"Saved model '{target_ref}'")


def cmd_remove(args):
    from omni_code.models import remove_model

    if not args.name:
        print("Usage: omni model remove <ref>")
        sys.exit(1)

    if remove_model(args.name):
        print(f"Removed model '{args.name}'")
    else:
        print(f"Model '{args.name}' not found or is a built-in model")
        sys.exit(1)


def cmd_default(args):
    from omni_code.models import set_default_model

    if not args.name:
        print("Usage: omni model default <ref>")
        sys.exit(1)

    if set_default_model(args.name):
        print(f"Set '{args.name}' as default model")
    else:
        print(f"Model '{args.name}' not found")
        sys.exit(1)


def cmd_test(args):
    from omni_code.models import get_default_model_name, resolve_model_for_runtime

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

    if not api_key and provider != "openai-compatible":
        print(f"Warning: No API key configured for '{name}'")

    try:
        if provider == "litellm":
            import litellm

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

        else:
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
    from omni_code.models import get_default_model_name, get_model_config

    name = args.name
    if not name:
        print("Usage: omni model show <ref>")
        sys.exit(1)

    config = get_model_config(name)
    if not config:
        print(f"Model '{name}' not found")
        sys.exit(1)

    normalized_name = config.get("name") or name
    default_name = get_default_model_name()
    is_default = normalized_name == default_name

    print(f"Model: {normalized_name}" + (" (default)" if is_default else ""))
    provider_name = config.get("provider_name")
    provider_type = config.get("provider")
    if provider_name and provider_name != provider_type:
        print(f"  Provider: {provider_type} ({provider_name})")
    else:
        print(f"  Provider: {provider_type}")
    print(f"  Model ID: {config.get('model')}")
    print(f"  Label: {config.get('label')}")
    if config.get("base_url"):
        print(f"  Base URL: {config.get('base_url')}")
    if config.get("api_version"):
        print(f"  API Version: {config.get('api_version')}")
    if config.get("api_key"):
        key = config.get("api_key")
        if isinstance(key, str) and key.startswith("${"):
            print(f"  API Key: {key}")
        elif isinstance(key, str):
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
    parser = argparse.ArgumentParser(
        prog="omni model",
        description="Manage model configurations for Omni Code",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("setup", help="Interactive setup wizard")
    subparsers.add_parser("list", help="List configured models")

    add_parser = subparsers.add_parser("add", help="Add or update a model")
    add_parser.add_argument("--name", "-n", help="Model reference (e.g. gpt-5.1 or azure-prod/gpt-5.2)")
    add_parser.add_argument("--provider", "-p", help="Provider profile name (default: openai)")
    add_parser.add_argument(
        "--type",
        "-t",
        choices=["openai", "azure", "openai-compatible", "litellm"],
        help="Provider type (required when creating a new provider profile)",
    )
    add_parser.add_argument("--model", "-m", help="Model identifier (or Azure deployment name)")
    add_parser.add_argument("--api-key", "-k", help="API key for this provider")
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
    add_parser.add_argument("--default", dest="set_default", action="store_true", help="Set as default")
    add_parser.add_argument("--force", action="store_true", help="Overwrite if model already exists")

    remove_parser = subparsers.add_parser("remove", help="Remove a model")
    remove_parser.add_argument("name", nargs="?", help="Model reference to remove")

    default_parser = subparsers.add_parser("default", help="Set default model")
    default_parser.add_argument("name", nargs="?", help="Model reference to set as default")

    test_parser = subparsers.add_parser("test", help="Test model connection")
    test_parser.add_argument("name", nargs="?", help="Model reference to test (default: current)")

    show_parser = subparsers.add_parser("show", help="Show model details")
    show_parser.add_argument("name", nargs="?", help="Model reference to show")

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


def setup_entrypoint():
    main(["setup"])


if __name__ == "__main__":
    main()
