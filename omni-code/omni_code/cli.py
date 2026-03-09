import os
import sys
from pathlib import Path


def _print_top_level_help() -> None:
    print(
        "usage: omni [work|projects|lazyomni|model|provider|update|sandbox|sessions] [args]\n\n"
        "Commands:\n"
        "  omni                 Launch the Omni Code chat TUI\n"
        "  omni work ...        Manage work sandboxes\n"
        "  omni projects        Launch the projects TUI\n"
        "  omni projects ...    Manage registered projects\n"
        "  omni lazyomni        Launch the lazyomni full-screen TUI\n"
        "  omni model ...       Manage model configuration\n"
        "  omni provider ...    Manage provider configuration\n"
        "  omni update ...      Manage updates\n"
        "  omni sandbox ...     Run Omni Code inside a Docker sandbox\n"
        "  omni sessions ...    Inspect OmniAgents sessions\n\n"
        "Prompt mode:\n"
        "  omni <words...>      Send words as the initial chat prompt\n"
        "  omni -- <words...>   Force prompt mode even for reserved words like 'work' or 'projects'\n\n"
        "Anything not matched above falls through to the standard Omni Code chat runtime."
    )


def _load_models_config() -> None:
    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()

    if "MODEL_NAME" not in os.environ:
        model_runtime = resolve_model_for_runtime()
        if model_runtime.get("model"):
            os.environ["MODEL_NAME"] = model_runtime["model"]


def cli_main(argv=None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    force_prompt_mode = bool(args and args[0] == "--")

    if force_prompt_mode:
        args = args[1:]

    if not force_prompt_mode and args and args[0] in {"-h", "--help", "help"}:
        _print_top_level_help()
        return

    if not force_prompt_mode and args and args[0] == "model":
        from omni_code.model_cli import main as model_main

        return model_main(args[1:])

    if not force_prompt_mode and args and args[0] == "provider":
        from omni_code.provider_cli import main as provider_main

        return provider_main(args[1:])

    if not force_prompt_mode and args and args[0] == "update":
        from omni_code.update_cli import main as update_main

        return update_main(args[1:])

    if not force_prompt_mode and args and args[0] == "sandbox":
        from omni_code.sandbox_cli import main as sandbox_main

        return sandbox_main(args[1:])

    if not force_prompt_mode and args and args[0] == "sessions":
        from omni_code.sessions_cli import main as sessions_main

        return sessions_main(args[1:])

    if not force_prompt_mode and args and args[0] == "work":
        from omni_code.work_cli import main as work_main

        return work_main(args[1:])

    if not force_prompt_mode and args and args[0] == "projects":
        from omni_code.projects_cli import main as projects_main

        return projects_main(args[1:])

    if not force_prompt_mode and args and args[0] == "lazyomni":
        from omni_code.lazyomni import main as lazyomni_main

        return lazyomni_main(args[1:])

    embedded = "--embedded" in args
    if embedded:
        args = [a for a in args if a != "--embedded"]
        os.environ["OMNI_WEB_AUTO_OPEN"] = "false"

    _load_models_config()

    from omni_code.models import has_models_configured

    if not has_models_configured():
        if embedded:
            import json

            print(json.dumps({"error": "No model credentials configured"}))
            sys.exit(1)
        print("No model credentials configured. Starting setup wizard...\n")
        from omni_code.model_cli import main as model_main

        model_main(["setup"])
        print()

    if not embedded:
        from omni_code.updater import maybe_print_update_notice

        maybe_print_update_notice()

    project_path = Path(__file__).resolve().parent / "project.yml"
    try:
        from omni_code.proxy import maybe_start_proxy

        maybe_start_proxy(str(project_path))
    except Exception as e:
        print(f"Warning: Network proxy failed to start: {e}", file=sys.stderr)

    from omniagents.cli import main as omni_main

    new_args = ["omniagents", "run", "--project", str(project_path), "--mode", "ink"]
    new_args.extend(args)

    sys.argv = new_args
    omni_main()
