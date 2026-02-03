"""CLI for provider management.

Usage:
    omni provider list               List configured providers
    omni provider add                Add a new provider (interactive)
    omni provider show <name>        Show provider details and its models
    omni provider update <name>      Update provider settings
    omni provider remove <name>      Remove a user-defined provider
"""

import argparse
import sys
from typing import Optional

from omni_code.model_cli import prompt_choice, prompt_secret, prompt_text


PROVIDER_TYPE_OPTIONS = [
    {"value": "openai", "label": "OpenAI (api.openai.com)"},
    {"value": "azure", "label": "Azure OpenAI"},
    {"value": "openai-compatible", "label": "OpenAI-compatible (Ollama, LM Studio, vLLM, etc.)"},
    {"value": "litellm", "label": "Other provider via LiteLLM (Anthropic, Google, etc.)"},
]


def _prompt_provider_fields(provider_type: str) -> dict:
    """Prompt for provider-level fields based on type. Returns kwargs for add_provider."""
    fields: dict = {}

    if provider_type == "openai":
        fields["api_key"] = prompt_secret("OpenAI API key", required=True)

    elif provider_type == "azure":
        fields["api_key"] = prompt_secret("Azure OpenAI API key", required=True)
        fields["base_url"] = prompt_text("Azure endpoint URL", required=True)
        fields["api_version"] = prompt_text("API version", "2024-08-01-preview", required=True)

    elif provider_type == "openai-compatible":
        fields["base_url"] = prompt_text("Base URL (e.g., http://localhost:11434/v1)", required=True)
        needs_key = prompt_text("Requires API key? (y/n)", "n")
        if needs_key and needs_key.lower() in ("y", "yes"):
            fields["api_key"] = prompt_secret("API key", required=True)

    elif provider_type == "litellm":
        print("\nLiteLLM supports many providers. Examples:")
        print("  anthropic  - Anthropic (Claude)")
        print("  gemini     - Google (Gemini)")
        print("  mistral    - Mistral")
        print("  groq       - Groq")
        print("  deepseek   - DeepSeek")
        print()

        key_label = "API key"
        fields["api_key"] = prompt_secret(key_label, required=True)
        base_url = prompt_text("Custom base URL (leave blank for default)")
        if base_url:
            fields["base_url"] = base_url

    return fields


def cmd_list(args):
    from omni_code.models import list_providers

    providers = list_providers()
    if not providers:
        print("No providers configured.")
        return

    # Column headers
    print(f"  {'Name':<20} {'Type':<22} {'Base URL':<35} {'Key':<5} {'Models'}")
    print(f"  {'-'*20} {'-'*22} {'-'*35} {'-'*5} {'-'*6}")

    for p in providers:
        name = p["name"]
        ptype = p["type"]
        base_url = p.get("base_url") or ""
        if len(base_url) > 33:
            base_url = base_url[:30] + "..."
        key = "yes" if p["has_api_key"] else "-"
        models = str(p["model_count"])
        user = " (user)" if p["is_user_defined"] else ""
        print(f"  {name:<20} {ptype:<22} {base_url:<35} {key:<5} {models}{user}")


def cmd_add(args):
    from omni_code.models import add_provider, get_provider

    print("Add Provider")
    print("=" * 40)
    print()

    print("Choose provider type:")
    provider_type = prompt_choice("Enter choice", PROVIDER_TYPE_OPTIONS, "openai")
    print()

    default_name = {
        "openai": "openai",
        "azure": "azure",
        "openai-compatible": "local",
        "litellm": "litellm",
    }.get(provider_type, "openai")

    name = args.name if args.name else None
    if not name:
        name = prompt_text("Provider name", default_name, required=True) or default_name

    existing = get_provider(name)
    if existing and not args.force:
        print(f"Provider '{name}' already exists. Use --force to overwrite.")
        sys.exit(1)

    fields = _prompt_provider_fields(provider_type)

    add_provider(name, provider_type, **fields)

    print()
    print(f"Provider '{name}' saved.")
    print(f"Add models with: omni model add --provider {name}")


def cmd_show(args):
    from omni_code.models import get_provider, is_user_provider

    name = args.name
    if not name:
        print("Usage: omni provider show <name>")
        sys.exit(1)

    provider = get_provider(name)
    if not provider:
        print(f"Provider '{name}' not found.")
        sys.exit(1)

    user_defined = is_user_provider(name)
    provider_type = provider.get("type") or provider.get("provider") or "unknown"

    print(f"Provider: {name}" + (" (user-defined)" if user_defined else " (built-in)"))
    print(f"  Type: {provider_type}")

    if provider.get("base_url"):
        print(f"  Base URL: {provider['base_url']}")
    if provider.get("api_version"):
        print(f"  API Version: {provider['api_version']}")
    if provider.get("api_key"):
        key = provider["api_key"]
        if isinstance(key, str) and key.startswith("${"):
            print(f"  API Key: {key}")
        elif isinstance(key, str):
            print(f"  API Key: {key[:8]}...{key[-4:]}" if len(key) > 12 else "  API Key: ****")
    if provider.get("realtime_url"):
        print(f"  Realtime URL: {provider['realtime_url']}")
    if provider.get("realtime_base_url"):
        print(f"  Realtime Base URL: {provider['realtime_base_url']}")

    models = provider.get("models")
    if isinstance(models, dict) and models:
        print()
        print(f"  Models ({len(models)}):")
        for model_name, model_config in models.items():
            if not isinstance(model_config, dict):
                continue
            label = model_config.get("label", model_name)
            model_id = model_config.get("model", model_name)
            extra = ""
            if model_config.get("realtime"):
                extra += " [realtime]"
            if model_config.get("reasoning"):
                extra += f" [reasoning: {model_config['reasoning']}]"
            print(f"    {model_name} - {label} (model: {model_id}){extra}")
    else:
        print()
        print("  No models configured. Add one with:")
        print(f"    omni model add --provider {name}")


def cmd_update(args):
    from omni_code.models import get_provider, is_user_provider, update_provider

    name = args.name
    if not name:
        print("Usage: omni provider update <name>")
        sys.exit(1)

    provider = get_provider(name)
    if not provider:
        print(f"Provider '{name}' not found.")
        sys.exit(1)

    if not is_user_provider(name):
        print(f"Provider '{name}' is a built-in provider.")
        print("Updating will create a user override in your config.")
        confirm = prompt_text("Continue? (y/n)", "y")
        if not confirm or confirm.lower() not in ("y", "yes"):
            return

    provider_type = provider.get("type") or provider.get("provider") or "unknown"

    kwargs: dict = {}

    # Accept flags or prompt interactively
    if args.api_key:
        kwargs["api_key"] = args.api_key
    elif args.interactive:
        new_key = prompt_secret("API key", required=False)
        if new_key:
            kwargs["api_key"] = new_key

    if args.base_url:
        kwargs["base_url"] = args.base_url
    elif args.interactive and provider_type in ("azure", "openai-compatible"):
        new_url = prompt_text("Base URL", provider.get("base_url"), required=False)
        if new_url:
            kwargs["base_url"] = new_url

    if args.api_version:
        kwargs["api_version"] = args.api_version
    elif args.interactive and provider_type == "azure":
        new_ver = prompt_text("API version", provider.get("api_version"), required=False)
        if new_ver:
            kwargs["api_version"] = new_ver

    if not kwargs:
        print("Nothing to update. Use flags (--api-key, --base-url) or --interactive.")
        return

    if update_provider(name, **kwargs):
        print(f"Provider '{name}' updated.")
    else:
        print(f"Failed to update provider '{name}'.")
        sys.exit(1)


def cmd_remove(args):
    from omni_code.models import is_user_provider, remove_provider

    name = args.name
    if not name:
        print("Usage: omni provider remove <name>")
        sys.exit(1)

    if not is_user_provider(name):
        print(f"Provider '{name}' is not a user-defined provider (or does not exist).")
        sys.exit(1)

    confirm = prompt_text(f"Remove provider '{name}' and all its models? (y/n)", "n")
    if not confirm or confirm.lower() not in ("y", "yes"):
        print("Cancelled.")
        return

    if remove_provider(name):
        print(f"Provider '{name}' removed.")
    else:
        print(f"Failed to remove provider '{name}'.")
        sys.exit(1)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="omni provider",
        description="Manage provider configurations for Omni Code",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("list", help="List configured providers")

    add_parser = subparsers.add_parser("add", help="Add a new provider")
    add_parser.add_argument("--name", "-n", help="Provider name")
    add_parser.add_argument("--force", action="store_true", help="Overwrite if provider already exists")

    show_parser = subparsers.add_parser("show", help="Show provider details")
    show_parser.add_argument("name", nargs="?", help="Provider name")

    update_parser = subparsers.add_parser("update", help="Update provider settings")
    update_parser.add_argument("name", nargs="?", help="Provider name")
    update_parser.add_argument("--api-key", "-k", help="New API key")
    update_parser.add_argument("--base-url", "-u", help="New base URL")
    update_parser.add_argument("--api-version", help="New API version")
    update_parser.add_argument("--interactive", "-i", action="store_true", help="Prompt for fields interactively")

    remove_parser = subparsers.add_parser("remove", help="Remove a user-defined provider")
    remove_parser.add_argument("name", nargs="?", help="Provider name")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "list": cmd_list,
        "add": cmd_add,
        "show": cmd_show,
        "update": cmd_update,
        "remove": cmd_remove,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
