import os
import sys
from pathlib import Path


def _get_arg_value(args: list[str], *flags: str) -> str | None:
    for i, arg in enumerate(args):
        if arg in flags and i + 1 < len(args):
            return args[i + 1]
    return None


def _print_top_level_help() -> None:
    print(
        "usage: omni [auth|model|provider|update|sandbox|sessions] [args]\n\n"
        "Commands:\n"
        "  omni                 Launch the Omni Code chat TUI\n"
        "  omni auth ...        Authenticate with the management plane\n"
        "  omni model ...       Manage model configuration\n"
        "  omni provider ...    Manage provider configuration\n"
        "  omni update ...      Manage updates\n"
        "  omni sandbox ...     Run Omni Code inside a Docker sandbox\n"
        "  omni sessions ...    Inspect OmniAgents sessions\n\n"
        "Prompt mode:\n"
        "  omni <words...>      Send words as the initial chat prompt\n"
        "  omni -- <words...>   Force prompt mode even for reserved words\n\n"
        "Anything not matched above falls through to the standard Omni Code chat runtime."
    )


def _load_models_config() -> None:
    from omni_code.models import load_models_config, resolve_model_for_runtime

    load_models_config()

    # Apply platform model governance if authenticated
    try:
        from omni_code.platform_models import apply_platform_model_governance
        apply_platform_model_governance()
    except Exception:
        pass

    if "MODEL_NAME" not in os.environ:
        model_runtime = resolve_model_for_runtime()
        if model_runtime.get("model"):
            os.environ["MODEL_NAME"] = model_runtime["model"]


def cli_main(argv=None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    force_prompt_mode = bool(args and args[0] == "--")

    if force_prompt_mode:
        args = args[1:]

    # Governance: if platform policy requires sandbox, re-exec inside omni-sandbox.
    # Must run before anything else — if it re-execs, this process is replaced.
    # Skips for non-agent subcommands (auth, model, etc.) automatically.
    # Fail-closed: if governance can't be enforced, this exits rather than
    # silently running ungoverned.
    from omni_code.governance import maybe_enforce_sandbox
    maybe_enforce_sandbox()

    if not force_prompt_mode and args and args[0] in {"-h", "--help", "help"}:
        _print_top_level_help()
        return

    if not force_prompt_mode and args and args[0] == "auth":
        from omni_code.platform_cli import main as auth_main

        return auth_main(args[1:])

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

    if not force_prompt_mode and args and args[0] == "serve":
        from omni_code.serve_cli import main as serve_main

        return serve_main(args[1:])

    if not force_prompt_mode and args and args[0] == "sandbox-host":
        from omni_code.sandbox_host_cli import main as sandbox_host_main

        return sandbox_host_main(args[1:])

    if not force_prompt_mode and args and args[0] == "sessions":
        from omni_code.sessions_cli import main as sessions_main

        return sessions_main(args[1:])

    embedded = "--embedded" in args
    if embedded:
        args = [a for a in args if a != "--embedded"]
        os.environ["OMNI_WEB_AUTO_OPEN"] = "false"

    selected_mode = _get_arg_value(args, "--mode", "-m")
    if embedded and selected_mode == "web" and _get_arg_value(args, "--output") is None:
        args.extend(["--output", "json"])

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

    # Resolve project.yml so that relative paths (omni_agents/, tools/, etc.)
    # resolve to sibling packages.  Editable installs have project.yml at the
    # repo root; wheel installs bundle it inside omni_code/ but sibling packages
    # live at the site-packages root.  We promote the file to the common root so
    # relative paths resolve correctly regardless of install mode.
    pkg_dir = Path(__file__).resolve().parent          # .../omni_code/
    common_root = pkg_dir.parent                       # .../site-packages/ or repo root
    repo_root_project = common_root / "project.yml"    # works for editable installs
    pkg_project = pkg_dir / "project.yml"              # bundled in wheel
    if repo_root_project.is_file():
        project_path = repo_root_project
    elif pkg_project.is_file():
        # Wheel install: promote project.yml to site-packages root.
        # If site-packages is read-only (e.g. Docker sandbox running as
        # non-root), fall back to a temp directory with a symlink tree so
        # relative paths still resolve.
        try:
            repo_root_project.write_text(pkg_project.read_text())
            project_path = repo_root_project
        except OSError:
            import tempfile

            tmp_dir = Path(tempfile.mkdtemp(prefix="omni_project_"))
            tmp_project = tmp_dir / "project.yml"
            tmp_project.write_text(pkg_project.read_text())
            # Symlink sibling packages so relative paths in project.yml resolve
            for name in ("omni_agents", "tools", "server_functions", "evaluations", "target"):
                src = common_root / name
                if src.exists():
                    (tmp_dir / name).symlink_to(src)
            project_path = tmp_project
    else:
        project_path = pkg_project
    from omniagents.cli import main as omni_main

    new_args = ["omniagents", "run", "--project", str(project_path), "--mode", "ink"]
    new_args.extend(args)

    sys.argv = new_args
    omni_main()
