"""Product-mountable model/provider management CLI.

Each omniagents product is a distinct install with its own model
registry (its ``ModelConfigStore`` → its own models.json under its own
config dir). Products mount these commands under their own program name:

    <prog> model setup | list | add | remove | default | test | show
    <prog> provider list | add | show | update | remove

by calling :func:`run_model_command` / :func:`run_provider_command` with
their store and prog — help text, hints, and prompts all render the
product's command name. Lifted from omni-code.

Model references:
    gpt-5.1              -> <default-provider>/gpt-5.1
    azure-prod/gpt-5.2   -> provider profile "azure-prod", model "gpt-5.2"
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from getpass import getpass
from typing import Optional

from .store import ModelConfigStore, litellm_api_key_env

__all__ = [
    "run_model_command",
    "run_provider_command",
    "prompt_choice",
    "prompt_text",
    "prompt_secret",
    "PROVIDER_TYPE_OPTIONS",
]


@dataclass(frozen=True)
class _CLIContext:
    store: ModelConfigStore
    prog: str
    product_label: str


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


def _setup_new_provider(ctx) -> tuple[str, str]:
    """Interactive provider creation. Returns (provider_name, provider_type)."""

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
    ctx.store.add_provider(provider_name, provider_type, **fields)

    print(f"Provider '{provider_name}' saved.")
    return provider_name, provider_type


def _prompt_model_fields(ctx, provider_name: str, provider_type: str) -> dict:
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


def cmd_check(ctx, args):

    sys.exit(0 if ctx.store.has_models_configured() else 1)


def cmd_setup(ctx, args):

    print(f"{ctx.product_label} Model Setup")
    print("=" * 40)

    existing_config = ctx.store.load_config()
    providers = ctx.store.list_providers()
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
            provider_name, provider_type = _setup_new_provider(ctx)
        else:
            provider_name = choice
            provider_config = ctx.store.get_provider(provider_name)
            provider_type = (provider_config or {}).get("type") or "openai"
    else:
        print()
        provider_name, provider_type = _setup_new_provider(ctx)

    # --- Phase 2: Model ---
    print()
    print("Step 2: Model")
    print("-" * 40)
    print()

    fields = _prompt_model_fields(ctx, provider_name, provider_type)

    model_key = fields["model_name"]
    make_default = False
    if not existing_config.get("default"):
        make_default = True
    else:
        model_ref = ctx.store.format_model_ref(provider_name, model_key)
        should_set = prompt_text(f"Set '{model_ref}' as default? (y/n)", "y")
        if should_set and should_set.lower() in ("y", "yes"):
            make_default = True

    ctx.store.add_model(
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
    print(f"Configuration saved to {ctx.store.config_path()}")
    print(f"Run '{ctx.prog}' to start the assistant.")


def cmd_list(ctx, args):

    models = ctx.store.list_models()
    default_name = ctx.store.get_default_model_name()
    voice_default = ctx.store.get_voice_default_model_name()

    # Machine-readable list (used by the launcher to populate model pickers with
    # the live set, including OAuth-discovered models). Always valid JSON.
    if getattr(args, "json", False):
        import json

        print(json.dumps({
            "models": models,
            "default": default_name,
            "voice_default": voice_default,
        }))
        return

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


def cmd_add(ctx, args):

    # --- Determine provider ---
    ref = args.name
    provider_name = None
    provider_type = None
    model_name = None

    if ref and "/" in ref:
        target_ref = ctx.store.normalize_model_ref(ref)
        if not target_ref:
            print("Invalid model reference.")
            sys.exit(1)
        provider_name, model_name = (
            target_ref.split("/", 1) if "/" in target_ref else (ctx.store.default_provider_name, target_ref)
        )
    elif args.provider:
        provider_name = args.provider.strip()
        model_name = (ref or "").strip() if ref else None
    else:
        # Interactive: pick from existing providers
        providers = ctx.store.list_providers()
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
                provider_name, provider_type = _setup_new_provider(ctx)
            else:
                provider_name = choice
            print()

        if not provider_name:
            print(f"No providers configured. Create one first with: {ctx.prog} provider add")
            sys.exit(1)

        model_name = (ref or "").strip() if ref else None

    if not provider_name:
        provider_name = ctx.store.default_provider_name

    # Look up provider to get its type
    existing_provider = ctx.store.get_provider(provider_name)
    if not existing_provider:
        print(f"Provider '{provider_name}' not found. Create it first with: {ctx.prog} provider add")
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

    target_ref = ctx.store.format_model_ref(provider_name, model_name)

    if ctx.store.get_model_config(target_ref) and not args.force:
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

    ctx.store.add_model(
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


def cmd_remove(ctx, args):

    if not args.name:
        print(f"Usage: {ctx.prog} model remove <ref>")
        sys.exit(1)

    if ctx.store.remove_model(args.name):
        print(f"Removed model '{args.name}'")
    else:
        print(f"Model '{args.name}' not found or is a built-in model")
        sys.exit(1)


def cmd_default(ctx, args):

    if not args.name:
        print(f"Usage: {ctx.prog} model default <ref>")
        sys.exit(1)

    if ctx.store.set_default_model(args.name):
        print(f"Set '{args.name}' as default model")
    else:
        print(f"Model '{args.name}' not found")
        sys.exit(1)


def cmd_voice_default(ctx, args):

    if not args.name:
        print(f"Usage: {ctx.prog} model voice-default <ref>")
        sys.exit(1)

    if ctx.store.set_voice_default_model(args.name):
        print(f"Set '{args.name}' as voice default model")
    else:
        print(f"Voice model '{args.name}' not found or not realtime-enabled")
        sys.exit(1)


def cmd_test(ctx, args):

    name = args.name or ctx.store.get_default_model_name()
    if not name:
        print(f"No model configured. Run '{ctx.prog} model setup' first.")
        sys.exit(1)

    config = ctx.store.resolve_model_for_runtime(name)
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
            
                os.environ[litellm_api_key_env(model)] = api_key

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


def cmd_show(ctx, args):

    name = args.name
    if not name:
        print(f"Usage: {ctx.prog} model show <ref>")
        sys.exit(1)

    config = ctx.store.get_model_config(name)
    if not config:
        print(f"Model '{name}' not found")
        sys.exit(1)

    normalized_name = config.get("name") or name
    default_name = ctx.store.get_default_model_name()
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


def cmd_provider_list(ctx, args):

    providers = ctx.store.list_providers()
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


def cmd_provider_add(ctx, args):

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

    existing = ctx.store.get_provider(name)
    if existing and not args.force:
        print(f"Provider '{name}' already exists. Use --force to overwrite.")
        sys.exit(1)

    fields = _prompt_provider_fields(provider_type)

    ctx.store.add_provider(name, provider_type, **fields)

    print()
    print(f"Provider '{name}' saved.")
    print(f"Add models with: {ctx.prog} model add --provider {name}")


def cmd_provider_show(ctx, args):

    name = args.name
    if not name:
        print(f"Usage: {ctx.prog} provider show <name>")
        sys.exit(1)

    provider = ctx.store.get_provider(name)
    if not provider:
        print(f"Provider '{name}' not found.")
        sys.exit(1)

    user_defined = ctx.store.is_user_provider(name)
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
        print(f"    {ctx.prog} model add --provider {name}")


def cmd_provider_update(ctx, args):

    name = args.name
    if not name:
        print(f"Usage: {ctx.prog} provider update <name>")
        sys.exit(1)

    provider = ctx.store.get_provider(name)
    if not provider:
        print(f"Provider '{name}' not found.")
        sys.exit(1)

    if not ctx.store.is_user_provider(name):
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

    if ctx.store.update_provider(name, **kwargs):
        print(f"Provider '{name}' updated.")
    else:
        print(f"Failed to update provider '{name}'.")
        sys.exit(1)


def cmd_provider_remove(ctx, args):

    name = args.name
    if not name:
        print(f"Usage: {ctx.prog} provider remove <name>")
        sys.exit(1)

    if not ctx.store.is_user_provider(name):
        print(f"Provider '{name}' is not a user-defined provider (or does not exist).")
        sys.exit(1)

    confirm = prompt_text(f"Remove provider '{name}' and all its models? (y/n)", "n")
    if not confirm or confirm.lower() not in ("y", "yes"):
        print("Cancelled.")
        return

    if ctx.store.remove_provider(name):
        print(f"Provider '{name}' removed.")
    else:
        print(f"Failed to remove provider '{name}'.")
        sys.exit(1)



def run_model_command(argv=None, *, store: ModelConfigStore, prog: str, product_label: Optional[str] = None):
    ctx = _CLIContext(store=store, prog=prog, product_label=product_label or prog)
    parser = argparse.ArgumentParser(
        prog=f"{prog} model",
        description=f"Manage model configurations for {ctx.product_label}",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("setup", help="Interactive setup wizard")
    list_parser = subparsers.add_parser("list", help="List configured models")
    list_parser.add_argument(
        "--json", action="store_true", help="Emit the model list as JSON"
    )
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
        handler(ctx, args)
    else:
        parser.print_help()
        sys.exit(1)




def run_provider_command(argv=None, *, store: ModelConfigStore, prog: str, product_label: Optional[str] = None):
    ctx = _CLIContext(store=store, prog=prog, product_label=product_label or prog)
    parser = argparse.ArgumentParser(
        prog=f"{prog} provider",
        description=f"Manage provider configurations for {ctx.product_label}",
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
        "list": cmd_provider_list,
        "add": cmd_provider_add,
        "show": cmd_provider_show,
        "update": cmd_provider_update,
        "remove": cmd_provider_remove,
    }

    handler = commands.get(args.command)
    if handler:
        handler(ctx, args)
    else:
        parser.print_help()
        sys.exit(1)

