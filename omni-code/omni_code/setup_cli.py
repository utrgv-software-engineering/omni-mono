import argparse
import os
import sys
from getpass import getpass
from typing import Dict, Optional

from omni_code.config import get_config_path, load_config, write_config


def prompt_choice(prompt: str, options: Dict[str, str], default: str) -> str:
    while True:
        raw = input(f"{prompt} ").strip()
        if not raw:
            raw = default
        if raw in options:
            return options[raw]
        print("Please choose a valid option.")


def prompt_text(label: str, existing: Optional[str], required: bool) -> Optional[str]:
    while True:
        suffix = " (leave blank to keep current)" if existing else ""
        raw = input(f"{label}{suffix}: ").strip()
        if not raw:
            if existing:
                return existing
            if required:
                print(f"{label} is required.")
                continue
            return None
        return raw


def prompt_secret(label: str, existing: Optional[str], required: bool) -> Optional[str]:
    while True:
        suffix = " (leave blank to keep current)" if existing else ""
        raw = getpass(f"{label}{suffix}: ").strip()
        if not raw:
            if existing:
                return existing
            if required:
                print(f"{label} is required.")
                continue
            return None
        return raw


def configure_provider(existing: Dict[str, str]) -> Dict[str, Optional[str]]:
    choices = {"1": "openai", "2": "azure", "3": "custom"}
    reverse = {v: k for k, v in choices.items()}
    default_choice = reverse.get((existing.get("OPENAI_PROVIDER") or "openai").lower(), "1")
    print("Choose the API provider:")
    print("  [1] OpenAI (api.openai.com)")
    print("  [2] Azure OpenAI")
    print("  [3] Custom OpenAI-compatible endpoint")
    provider = prompt_choice("Enter 1, 2, or 3:", choices, default_choice)
    result: Dict[str, Optional[str]] = {"OPENAI_PROVIDER": provider}
    if provider in {"openai", "custom"}:
        key = prompt_secret("OpenAI API key", existing.get("OPENAI_API_KEY"), True)
        result["OPENAI_API_KEY"] = key
        if provider == "custom":
            base_url = prompt_text("OpenAI base URL", existing.get("OPENAI_BASE_URL"), True)
            result["OPENAI_BASE_URL"] = base_url
        else:
            result["OPENAI_BASE_URL"] = None
        org = prompt_text("OpenAI organization (optional)", existing.get("OPENAI_ORG"), False)
        project = prompt_text("OpenAI project (optional)", existing.get("OPENAI_PROJECT"), False)
        result["OPENAI_ORG"] = org
        result["OPENAI_PROJECT"] = project
        for key_name in [
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_API_VERSION",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_DEPLOYMENT",
        ]:
            result[key_name] = None
    if provider == "azure":
        result["OPENAI_API_KEY"] = None
        result["OPENAI_BASE_URL"] = None
        result["OPENAI_ORG"] = prompt_text("OpenAI organization (optional)", existing.get("OPENAI_ORG"), False)
        result["OPENAI_PROJECT"] = prompt_text("OpenAI project (optional)", existing.get("OPENAI_PROJECT"), False)
        result["AZURE_OPENAI_API_KEY"] = prompt_secret(
            "Azure OpenAI API key",
            existing.get("AZURE_OPENAI_API_KEY"),
            True,
        )
        result["AZURE_OPENAI_API_VERSION"] = prompt_text(
            "Azure OpenAI API version",
            existing.get("AZURE_OPENAI_API_VERSION") or "2024-08-01-preview",
            True,
        )
        result["AZURE_OPENAI_ENDPOINT"] = prompt_text(
            "Azure OpenAI endpoint",
            existing.get("AZURE_OPENAI_ENDPOINT"),
            True,
        )
        result["AZURE_OPENAI_DEPLOYMENT"] = prompt_text(
            "Azure OpenAI deployment name",
            existing.get("AZURE_OPENAI_DEPLOYMENT"),
            True,
        )
    serp = prompt_secret("SerpAPI key (optional)", existing.get("SERPAPI_API_KEY"), False)
    result["SERPAPI_API_KEY"] = serp
    return result


def write_configuration(values: Dict[str, Optional[str]]) -> None:
    compact = {k: v for k, v in values.items() if v}
    write_config(compact)
    for key, value in compact.items():
        os.environ[key] = value


def main() -> None:
    parser = argparse.ArgumentParser(prog="omni-setup")
    parser.parse_args()
    existing = load_config()
    values = configure_provider(existing)
    write_configuration(values)
    path = get_config_path()
    print(f"Configuration saved to {path}")
    print("Run 'omni' to start the assistant.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
        sys.exit(1)
