"""CLI for model management.

Usage:
    omni model setup                 Interactive setup wizard (provider + model)
    omni model list                  List configured models
    omni model add                   Add a model to an existing provider
    omni model remove <ref>          Remove a user-defined model
    omni model default <ref>         Set default model
    omni model test [ref]            Test model connection
    omni model show <ref>            Show model details

Provider management is separate:
    omni provider list               List configured providers
    omni provider add                Add a new provider
    omni provider show <name>        Show provider details
    omni provider update <name>      Update provider settings
    omni provider remove <name>      Remove a user-defined provider

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


def _setup_new_provider() -> tuple[str, str]:
    """Interactive provider creation. Returns (provider_name, provider_type)."""
    from omni_code.provider_cli import PROVIDER_TYPE_OPTIONS, _prompt_provider_fields
    from omni_code.models import add_provider

    print("Choose provider type:")
    provider_type = prompt_choice("Enter choice", PROVIDER_TYPE_OPTIONS, "openai")
    print()

    default_name = {
        "openai": "openai",
        "azure": "azure",
        "openai-compatible": "local",
        "litellm": "litellm",
    }.get(provider_type, "openai")

    provider_name = prompt_text("Provider name", default_name, required=True) or default_name

    fields = _prompt_provider_fields(provider_type)
    add_provider(provider_name, provider_type, **fields)

    print(f"Provider '{provider_name}' saved.")
    return provider_name, provider_type


def _prompt_model_fields(provider_name: str, provider_type: str) -> dict:
    """Prompt for model-specific fields. Returns kwargs for add_model."""
    if provider_type == "litellm":
        print("\nLiteLLM model identifier examples:")
        print("  anthropic/claude-3-5-sonnet-20241022")
        print("  gemini/gemini-1.5-pro")
        print("  mistral/mistral-large-latest")
        print()

    prompt_label = "Deployment name" if provider_type == "azure" else "Model identifier"
    model_id = prompt_text(prompt_label, required=True) or ""

    default_key = model_id.split("/")[-1] if "/" in model_id else model_id
    model_key = prompt_text("Model name (for /model)", default_key, required=True) or default_key

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

    return {
        "model_name": model_key,
        "model": model_id,
        "label": label,
        "reasoning": reasoning_value,
        "model_settings": model_settings,
    }


def cmd_check(args):
    from omni_code.models import has_models_configured

    sys.exit(0 if has_models_configured() else 1)


def cmd_setup(args):
    from omni_code.models import (
        add_model, get_models_config_path, get_provider, list_providers,
        load_models_config,
    )

    print("Omni Code Model Setup")
    print("=" * 40)

    existing_config = load_models_config()
    providers = list_providers()
    # Only show user-defined providers that already have credentials configured
    usable_providers = [p for p in providers if p["has_api_key"] or p["type"] == "openai-compatible"]

    # --- Phase 1: Provider ---
    print()
    print("Step 1: Provider")
    print("-" * 40)

    if usable_providers:
        print()
        print("Existing providers:")
        options = []
        for p in usable_providers:
            ptype = p["type"]
            models_n = p["model_count"]
            suffix = f" ({models_n} model{'s' if models_n != 1 else ''})" if models_n else ""
            options.append({"value": p["name"], "label": f"{p['name']} [{ptype}]{suffix}"})
        options.append({"value": "__new__", "label": "Create a new provider"})

        choice = prompt_choice("Select a provider", options)
        print()

        if choice == "__new__":
            provider_name, provider_type = _setup_new_provider()
        else:
            provider_name = choice
            provider_config = get_provider(provider_name)
            provider_type = (provider_config or {}).get("type") or "openai"
    else:
        print()
        provider_name, provider_type = _setup_new_provider()

    # --- Phase 2: Model ---
    print()
    print("Step 2: Model")
    print("-" * 40)
    print()

    fields = _prompt_model_fields(provider_name, provider_type)

    model_key = fields["model_name"]
    make_default = False
    if not existing_config.get("default"):
        make_default = True
    else:
        model_ref = model_key if provider_name == "openai" else f"{provider_name}/{model_key}"
        should_set = prompt_text(f"Set '{model_ref}' as default? (y/n)", "y")
        if should_set and should_set.lower() in ("y", "yes"):
            make_default = True

    add_model(
        provider_name=provider_name,
        provider_type=provider_type,
        model_name=fields["model_name"],
        model=fields["model"],
        label=fields["label"],
        reasoning=fields["reasoning"],
        model_settings=fields["model_settings"],
        set_default=make_default,
    )

    print()
    print(f"Configuration saved to {get_models_config_path()}")
    print("Run 'omni' to start the assistant.")


def cmd_list(args):
    from omni_code.models import get_default_model_name, get_voice_default_model_name, list_models

    models = list_models()
    default_name = get_default_model_name()
    voice_default = get_voice_default_model_name()

    if not models:
        print("No models available.")
        return

    text_models = [m for m in models if not m.get("realtime")]
    voice_models = [m for m in models if m.get("realtime")]

    user_models = [m for m in text_models if m.get("is_user_defined")]
    builtin_models = [m for m in text_models if not m.get("is_user_defined")]

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

    if voice_models:
        print()
        print("Voice models:")
        for m in voice_models:
            marker = "*" if m["name"] == voice_default else " "
            default_label = " (voice default)" if m.get("is_voice_default") else ""
            key_indicator = " [key]" if m.get("has_api_key") else ""
            print(f"  {marker} {m['name']} - {m['label']} [{m['provider']}]{default_label}{key_indicator}")


def cmd_add(args):
    from omni_code.models import (
        add_model, get_model_config, get_provider, list_providers, normalize_model_ref,
    )

    # --- Determine provider ---
    ref = args.name
    provider_name = None
    provider_type = None
    model_name = None

    if ref and "/" in ref:
        target_ref = normalize_model_ref(ref)
        if not target_ref:
            print("Invalid model reference.")
            sys.exit(1)
        provider_name, model_name = (
            target_ref.split("/", 1) if "/" in target_ref else ("openai", target_ref)
        )
    elif args.provider:
        provider_name = args.provider.strip()
        model_name = (ref or "").strip() if ref else None
    else:
        # Interactive: pick from existing providers
        providers = list_providers()
        usable = [p for p in providers if p["has_api_key"] or p["type"] == "openai-compatible"]
        if usable:
            print("Select a provider for this model:")
            options = [
                {"value": p["name"], "label": f"{p['name']} [{p['type']}]"}
                for p in usable
            ]
            options.append({"value": "__new__", "label": "Create a new provider first"})
            choice = prompt_choice("Provider", options)
            if choice == "__new__":
                print()
                provider_name, provider_type = _setup_new_provider()
            else:
                provider_name = choice
            print()

        if not provider_name:
            print("No providers configured. Create one first with: omni provider add")
            sys.exit(1)

        model_name = (ref or "").strip() if ref else None

    if not provider_name:
        provider_name = "openai"

    # Look up provider to get its type
    existing_provider = get_provider(provider_name)
    if not existing_provider:
        print(f"Provider '{provider_name}' not found. Create it first with: omni provider add")
        sys.exit(1)

    if not provider_type:
        provider_type = existing_provider.get("type") or existing_provider.get("provider") or "openai"

    # --- Model name ---
    if not model_name:
        model_name = prompt_text("Model name (for /model)", required=True)
    model_name = (model_name or "").strip()
    if not model_name:
        print("Invalid model name.")
        sys.exit(1)

    target_ref = model_name if provider_name == "openai" else f"{provider_name}/{model_name}"

    if get_model_config(target_ref) and not args.force:
        print(f"Model '{target_ref}' already exists. Use --force to overwrite.")
        sys.exit(1)

    # --- Model identifier ---
    model_id = args.model
    if not model_id:
        prompt_label = "Deployment name" if provider_type == "azure" else "Model identifier"
        model_id = prompt_text(prompt_label, model_name, required=True)

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
        realtime=args.realtime,
        reasoning=args.reasoning,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        model_settings=model_settings,
        set_default=args.set_default,
        set_voice_default=args.set_voice_default,
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


def cmd_voice_default(args):
    from omni_code.models import set_voice_default_model

    if not args.name:
        print("Usage: omni model voice-default <ref>")
        sys.exit(1)

    if set_voice_default_model(args.name):
        print(f"Set '{args.name}' as voice default model")
    else:
        print(f"Voice model '{args.name}' not found or not realtime-enabled")
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
    subparsers.add_parser("check", help="Check if models are configured (exit 0=yes, 1=no)")

    add_parser = subparsers.add_parser("add", help="Add a model to an existing provider")
    add_parser.add_argument("--name", "-n", help="Model reference (e.g. gpt-5.1 or azure-prod/gpt-5.2)")
    add_parser.add_argument("--provider", "-p", help="Provider name to add the model to")
    add_parser.add_argument("--model", "-m", help="Model identifier (or Azure deployment name)")
    add_parser.add_argument("--label", "-l", help="Display label")
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
        "--realtime",
        action="store_true",
        help="Mark this model as voice/realtime-capable",
    )
    add_parser.add_argument("--default", dest="set_default", action="store_true", help="Set as default")
    add_parser.add_argument(
        "--voice-default",
        dest="set_voice_default",
        action="store_true",
        help="Set as voice default model",
    )
    add_parser.add_argument("--force", action="store_true", help="Overwrite if model already exists")

    remove_parser = subparsers.add_parser("remove", help="Remove a model")
    remove_parser.add_argument("name", nargs="?", help="Model reference to remove")

    default_parser = subparsers.add_parser("default", help="Set default model")
    default_parser.add_argument("name", nargs="?", help="Model reference to set as default")

    voice_default_parser = subparsers.add_parser(
        "voice-default", help="Set voice default model"
    )
    voice_default_parser.add_argument(
        "name", nargs="?", help="Realtime model reference to set as voice default"
    )

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
        "check": cmd_check,
        "add": cmd_add,
        "remove": cmd_remove,
        "default": cmd_default,
        "voice-default": cmd_voice_default,
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
