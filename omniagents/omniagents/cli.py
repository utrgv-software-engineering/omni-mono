import sys
import argparse
import json
from pathlib import Path
import yaml
import re
from dotenv import load_dotenv
from typing import Any, Dict, List

# --- Core Framework Imports --- #
# Note: All OpenAI client configuration (API key, base URL, tracing) should be done by the user script or environment.
from omniagents import __version__
from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.runtime.runner import Runner
from omniagents.core.debug import Debug

def main():
    """Entry point for the omniagents CLI."""
    # Parse args first to get config path if available
    import sys
    project_path = None
    for i, arg in enumerate(sys.argv):
        if arg in ('--project', '-P') and i + 1 < len(sys.argv):
            project_path = sys.argv[i + 1]
            # don't break; prefer explicit --config if both are present
    
    # Load .env for CLI runs: when a YAML config is provided, ALWAYS search
    # upward from the YAML's directory so the correct .env is used regardless
    # of the shell's current working directory.
    env_loaded = False
    if project_path:
        base_ref = project_path
        config_dir = Path(base_ref).resolve().parent
        current = config_dir
        while True:
            env_file = current / '.env'
            if env_file.exists():
                try:
                    # Do not override explicit env variables that may be set by the user
                    if load_dotenv(env_file, override=False):
                        print(f"Loaded .env file from {env_file}")
                        env_loaded = True
                except Exception as e:
                    print(f"Warning: Could not load .env file {env_file}: {e}")
                break
            if current == current.parent:
                break
            current = current.parent
    else:
        # Fallback: standard behavior only when no config path is known
        try:
            if load_dotenv():
                print("Loaded .env file")
                env_loaded = True
        except Exception as e:
            print(f"Warning: Could not load .env file: {e}")

    # --- Command Line Arguments ---
    parser = argparse.ArgumentParser(description="OmniAgents CLI")
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"omniagents {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # New project scaffold
    new_parser = subparsers.add_parser("new", help="Scaffold a new OmniAgents project")
    new_parser.add_argument("name", help="Project and default agent name (snake_case)")
    new_parser.add_argument("--dir", dest="directory", default=".", help="Parent directory to create the project in (default: .)")
    new_parser.add_argument("--model", default="gpt-4.1", help="Default model for the agent (default: gpt-4.1)")
    new_parser.add_argument("--force", action="store_true", help="Overwrite existing files when necessary")
    new_parser.add_argument("--no-samples", dest="no_samples", action="store_true", help="Do not create or wire sample tools")
    
    # Run command
    run_parser = subparsers.add_parser("run", help="Run an agent")
    run_parser.add_argument(
        "--mode", "-m",
        choices=["gui", "cli", "server", "bubbletea", "tui"],
        default="gui",
        help="Run mode: 'gui' (default) for graphical UI, 'cli' for command-line interface, 'server' for JSON-RPC server, 'bubbletea'/'tui' for Bubble Tea TUI"
    )
    run_parser.add_argument(
        "--project",
        "-P",
        type=str,
        help="Path to project.yml (dbt-style)."
    )
    run_parser.add_argument(
        "--agent",
        "-a",
        type=str,
        default=None,
        help="Agent key to run from project.yml (defaults to entrypoint)."
    )
    run_parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug mode (overrides YAML setting)."
    )
    run_parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Resume a previous CLI session using this identifier."
    )
    # CLI-specific arguments for safe agent control
    run_parser.add_argument(
        "--no-approval",
        action="store_true",
        help="Disable SafeAgent tool approval (CLI mode only)."
    )
    run_parser.add_argument(
        "--skip-approvals",
        action="store_true",
        help="Auto-approve all tool calls (CLI mode only)."
    )
    run_parser.add_argument(
        "--no-halt",
        action="store_true",
        help="Don't halt on rejection (CLI mode only)."
    )
    run_parser.add_argument(
        "--pre-approve",
        type=str,
        help="Comma-separated list of tool names to pre-approve (CLI mode only)."
    )
    run_parser.add_argument(
        "--safe-patterns",
        type=str,
        help="Comma-separated list of safe tool patterns (CLI mode only)."
    )
    # Server-specific arguments
    run_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind (server mode only, default 0.0.0.0)"
    )
    run_parser.add_argument(
        "--port", "-p",
        type=int,
        default=8000,
        help="Port to listen on (server mode only, default 8000)"
    )
    run_parser.add_argument(
        "--auth-token",
        dest="auth_token",
        default=None,
        help="Bearer token to require from clients (server mode only)"
    )
    run_parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload server when code changes (server mode only)"
    )
    
    # Save command
    save_parser = subparsers.add_parser("save", help="Save an agent spec as a standalone Python module")
    save_parser.add_argument(
        "--project",
        "-P",
        type=str,
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)"
    )
    save_parser.add_argument(
        "--agent",
        "-a",
        type=str,
        default=None,
        help="Agent key from project.yml (defaults to entrypoint)"
    )
    save_parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="generated_agent.py",
        help="Output Python file path."
    )
    
    compile_parser = subparsers.add_parser("compile", help="Compile a project.yml into target/evaluation_compiled.yml")
    compile_parser.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    compile_parser.add_argument("--agent", "-a", default="", help="Agent key to compile (defaults to entrypoint)")
    compile_parser.add_argument("--select", default="", help="Selector to include scenarios")
    compile_parser.add_argument("--exclude", default="", help="Selector to exclude scenarios")

    eval_parser = subparsers.add_parser("eval", help="Evaluation utilities")
    eval_sub = eval_parser.add_subparsers(dest="eval_cmd")

    eval_run = eval_sub.add_parser("run", help="Run evaluation across scenarios")
    eval_run.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    eval_run.add_argument("--agent", "-a", default="", help="Agent key (defaults to entrypoint)")
    eval_run.add_argument("--output", "-o", default="", help="Write JSON to this path")
    eval_run.add_argument("--reporter", choices=["auto", "pretty", "tap"], default="auto")
    eval_run.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    eval_run.add_argument("--select", default="")
    eval_run.add_argument("--exclude", default="")
    eval_run.add_argument("-q", "--quiet", action="count", default=0)
    eval_run.add_argument("-v", "--verbose", action="count", default=0)
    eval_run.add_argument("--include-unmeasured", action="store_true", help="Include scenarios without measures/failure_categories (default: skip them)")

    eval_scen = eval_sub.add_parser("scenarios", help="Scenario utilities")
    scen_sub = eval_scen.add_subparsers(dest="scen_cmd")
    scen_gen = scen_sub.add_parser("generate", help="Generate synthetic scenarios")
    scen_gen.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    scen_gen.add_argument("--agent", "-a", default="", help="Agent key (defaults to entrypoint)")
    scen_gen.add_argument("--select", default="")
    scen_gen.add_argument("--exclude", default="")
    scen_gen.add_argument("--output", "-o", default="artifacts/eval/generated_scenarios.yml")
    scen_gen.add_argument("--count", type=int, default=None)
    scen_gen.add_argument("--seed", type=int, default=None)
    scen_run = scen_sub.add_parser("run", help="Run scenarios from a YAML file to record fixtures")
    scen_run.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    scen_run.add_argument("--agent", "-a", default="", help="Agent key (defaults to entrypoint)")
    scen_run.add_argument("--input", "-i", default="artifacts/eval/latest/generated_scenarios.yml", help="Path to scenarios YAML to run")
    scen_run.add_argument("--output", "-o", default="", help="Write generated_runs.json to this path (default next to scenarios file)")
    scen_run.add_argument("--run-max-turns", type=int, default=None)
    ih_group = scen_run.add_mutually_exclusive_group()
    ih_group.add_argument("--include-history", dest="include_history", action="store_true", help="Include full history in runs.json (default)")
    ih_group.add_argument("--no-include-history", dest="include_history", action="store_false", help="Do not include history in runs.json")
    scen_run.set_defaults(include_history=True)

    eval_ann = eval_sub.add_parser("annotate", help="Annotate scenarios with AgentIO categories")
    eval_ann.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    eval_ann.add_argument("--agent", "-a", default="", help="Agent key (defaults to entrypoint)")
    eval_ann.add_argument("--input", "-i", default="artifacts/eval/latest/generated_scenarios.yml")
    eval_ann.add_argument("--output", "-o", default="artifacts/eval/annotated_scenarios.yml")
    eval_ann.add_argument("--agentio-project", default=None)
    eval_ann.add_argument("--workflow-name", default=None)

    eval_split = eval_sub.add_parser("split", help="Create train/dev/test splits from annotated scenarios")
    eval_split.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    eval_split.add_argument("--input", "-i", required=False, default="artifacts/eval/latest/annotated_scenarios.yml")
    eval_split.add_argument("--category", "-c", required=True)
    eval_split.add_argument("--output-dir", "-o", default="")
    eval_split.add_argument("--train", type=float, default=0.15)
    eval_split.add_argument("--dev", type=float, default=0.425)
    eval_split.add_argument("--test", type=float, default=0.425)
    eval_split.add_argument("--seed", type=int, default=42)
    eval_split.add_argument("--negatives-ratio", type=float, default=None)

    eval_judge = eval_sub.add_parser("judge", help="Judge utilities")
    judge_sub = eval_judge.add_subparsers(dest="judge_cmd")

    judge_run = judge_sub.add_parser("run", help="Evaluate an LLM-as-Judge over a split")
    judge_run.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    judge_run.add_argument("--agent", "-a", default="", help="Agent key (defaults to entrypoint)")
    judge_run.add_argument("--category", "-c", required=False, help="Category name for locating splits (required unless --split-file provided)")
    judge_run.add_argument("--split", "-s", choices=["dev", "test", "train"], default="dev", help="Which split to judge (default: dev)")
    judge_run.add_argument("--split-file", dest="split_file", required=False, default=None, help="Explicit path to a split YAML; overrides --category/--split lookup")
    judge_run.add_argument("--judge", "-j", default=None, help="Judge key; if omitted and exactly one judge is configured, it will be used")
    judge_run.add_argument("--max-turns", type=int, default=1)
    judge_run.add_argument("--run-max-turns", type=int, default=None)
    judge_run.add_argument("--replay-mode", choices=["replay", "auto", "record", "live"], default="replay")
    judge_run.add_argument("--runs-file", dest="runs_file", default=None, help="Optional explicit runs fixtures JSON to use")
    judge_run.add_argument("--output", "-o", default="")
    judge_run.add_argument("--fail-on-disagreement", dest="fail_on_disagreement", action="store_true", help="Exit with code 1 if any FP/FN disagreements are found")
    judge_run.add_argument("--show", type=int, default=20)
    judge_run.add_argument("--disagreements", dest="disagreements", action="store_true", help="Print disagreements (FP/FN) with scenario file and line numbers (default)")
    judge_run.add_argument("--no-disagreements", dest="disagreements", action="store_false", help="Do not print disagreements")
    judge_run.set_defaults(disagreements=True)
    judge_run.add_argument("--reporter", choices=["auto", "pretty", "tap"], default="auto")
    judge_run.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    judge_run.add_argument("-q", "--quiet", action="count", default=0)
    judge_run.add_argument("-v", "--verbose", action="count", default=0)

    judge_opt = judge_sub.add_parser("optimize", help="Optimize an LLM-as-Judge's prompts using GEPA over train/dev/test splits")
    judge_opt.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    judge_opt.add_argument("--agent", "-a", default="", help="Agent key (defaults to entrypoint)")
    judge_opt.add_argument("--category", "-c", required=False, help="Failure category name to locate splits under artifacts/eval/*/splits/<category>/ (optional if --train-file/--dev-file/--test-file provided)")
    judge_opt.add_argument("--train-file", dest="train_file", default=None, help="Explicit path to a train split YAML")
    judge_opt.add_argument("--dev-file", dest="dev_file", default=None, help="Explicit path to a dev split YAML")
    judge_opt.add_argument("--test-file", dest="test_file", default=None, help="Explicit path to a test split YAML")
    judge_opt.add_argument("--judge", "-j", default=None, help="Judge key; if omitted and exactly one judge is configured, it will be used")
    judge_opt.add_argument("--runs-file", dest="runs_file", default=None, help="Path to generated_runs.json; defaults alongside split timestamp dir")
    judge_opt.add_argument("--source", choices=["final_assistant_text", "history"], default="final_assistant_text")
    judge_opt.add_argument("--max-turns", type=int, default=1, help="Max turns for the judge agent per example (default: 1)")
    judge_opt.add_argument("--max-metric-calls", dest="max_metric_calls", type=int, default=100)
    judge_opt.add_argument("--reflection-model", dest="reflection_model", default="gpt-4.1", help="Model to use for reflection/mutation (default: gpt-4.1)")
    judge_opt.add_argument("--optimize", default="instructions,template", help="Comma-separated list of components to optimize: instructions,template")
    judge_opt.add_argument("--seed", type=int, default=0)
    judge_opt.add_argument("--output", "-o", default="", help="Directory to write optimization artifacts")
    judge_opt.add_argument("--apply", dest="apply", action="store_true", help="Apply optimized instructions/template back to source files with backups")

    eval_agent = eval_sub.add_parser("agent", help="Agent optimization utilities")
    agent_sub = eval_agent.add_subparsers(dest="agent_cmd")
    agent_opt = agent_sub.add_parser("optimize", help="Optimize an agent's instructions using GEPA over train/dev/test splits")
    agent_opt.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    agent_opt.add_argument("--agent", "-a", default="", help="Agent key (defaults to entrypoint)")
    agent_opt.add_argument("--category", "-c", required=False, help="Failure category to optimize (required unless --train-file/--dev-file/--test-file provided)")
    agent_opt.add_argument("--train-file", dest="train_file", default=None, help="Explicit path to a train split YAML")
    agent_opt.add_argument("--dev-file", dest="dev_file", default=None, help="Explicit path to a dev split YAML")
    agent_opt.add_argument("--test-file", dest="test_file", default=None, help="Explicit path to a test split YAML")
    agent_opt.add_argument("--positives-only", dest="positives_only", action="store_true", help="Use only positive (label=True) scenarios from splits")
    agent_opt.add_argument("--all-cases", dest="positives_only", action="store_false", help="Use both positives and negatives from splits")
    agent_opt.set_defaults(positives_only=True)
    agent_opt.add_argument("--max-turns", type=int, default=None, help="Max turns for the agent per example (default from YAML)")
    agent_opt.add_argument("--max-metric-calls", dest="max_metric_calls", type=int, default=100)
    agent_opt.add_argument("--reflection-model", dest="reflection_model", default="gpt-4.1", help="Model to use for reflection/mutation (default: gpt-4.1)")
    agent_opt.add_argument("--score-mode", choices=["fraction", "all_or_nothing"], default="fraction")
    agent_opt.add_argument("--seed", type=int, default=0)
    agent_opt.add_argument("--output", "-o", default="", help="Directory to write optimization artifacts")
    agent_opt.add_argument("--apply", dest="apply", action="store_true", help="Apply optimized instructions back to the agent source with backup")

    ls_parser = subparsers.add_parser("ls", help="List compiled resources from target/graph.json")
    ls_parser.add_argument("--project", "-P", required=False, help="Path to project.yml (defaults to project.yml in CWD)")
    ls_parser.add_argument("--agent", "-a", default="", help="Agent key (defaults to entrypoint)")

    gen_parser = subparsers.add_parser("generate", help="Generators for project assets")
    gen_sub = gen_parser.add_subparsers(dest="gen_cmd")

    gen_tool = gen_sub.add_parser("tool", help="Generate a tool file in tools/")
    gen_tool.add_argument("name", help="Tool function name (snake_case)")
    gen_tool.add_argument("--project", "-P", required=False, default=".", help="Path to project.yml or its directory")
    gen_tool.add_argument("--async", dest="is_async", action="store_true", help="Generate an async tool")
    gen_tool.add_argument("--force", action="store_true", help="Overwrite existing files if necessary")

    gen_agent = gen_sub.add_parser("agent", help="Generate a new agent config and instructions")
    gen_agent.add_argument("name", help="Agent name (snake_case)")
    gen_agent.add_argument("--project", "-P", required=False, default=".")
    gen_agent.add_argument("--model", default="gpt-4.1")
    gen_agent.add_argument("--force", action="store_true")

    gen_eval = gen_sub.add_parser("eval", help="Generate evaluation scaffolds")
    gen_eval_sub = gen_eval.add_subparsers(dest="eval_cmd")
    gen_eval_qs = gen_eval_sub.add_parser("quickstart", help="Add evaluations quickstart scaffold")
    gen_eval_qs.add_argument("--project", "-P", required=False, default=".")
    gen_eval_qs.add_argument("--force", action="store_true")

    gen_guard = gen_sub.add_parser("guardrail", help="Generate guardrails in guardrails/")
    gen_guard.add_argument("type", choices=["input", "output"], help="Guardrail type")
    gen_guard.add_argument("name", help="Guardrail name (snake_case)")
    gen_guard.add_argument("--project", "-P", required=False, default=".")
    gen_guard.add_argument("--async", dest="is_async", action="store_true")
    gen_guard.add_argument("--force", action="store_true")

    gen_srv = gen_sub.add_parser("server-function", help="Generate a server function in server_functions/")
    gen_srv.add_argument("name", help="Function name (snake_case)")
    gen_srv.add_argument("--project", "-P", required=False, default=".")
    gen_srv.add_argument("--async", dest="is_async", action="store_true")
    gen_srv.add_argument("--force", action="store_true")

    gen_judge = gen_sub.add_parser("judge", help="Generate an LLM-as-Judge scaffold")
    gen_judge.add_argument("name", help="Judge key (snake_case, e.g., out_of_scope_judge)")
    gen_judge.add_argument("--project", "-P", required=False, default=".")
    gen_judge.add_argument("--model", default="gpt-4.1")
    gen_judge.add_argument("--source", choices=["final_assistant_text", "history"], default="final_assistant_text")
    gen_judge.add_argument("--failure-category", dest="failure_category", default=None)
    gen_judge.add_argument("--no-measure", dest="no_measure", action="store_true")
    gen_judge.add_argument("--force", action="store_true")
    
    
    args = parser.parse_args()
    
    if args.command == "new":
        try:
            from omniagents.core.project.scaffold import scaffold_project
        except Exception as e:
            print(f"Error: scaffolding not available: {e}")
            sys.exit(1)
        try:
            created, project_path, sanitized = scaffold_project(
                project_name=str(args.name),
                parent_dir=str(args.directory),
                model=str(args.model),
                force=bool(args.force),
                include_samples=not bool(getattr(args, 'no_samples', False)),
            )
            print(f"Created project '{sanitized}' at {project_path}")
            for p in created:
                print(f"  {p}")
            print(f"\nNext: cd {sanitized}; omniagents run")
            sys.exit(0)
        except Exception as e:
            print(f"Error creating project: {e}")
            import traceback; traceback.print_exc()
            sys.exit(1)
    elif args.command == "run":
        # Resolve spec/config from either --config or --project
        runner = None
        resolved_config_path = None
        if getattr(args, 'project', None):
            try:
                from omniagents.core.project.runtime import load_project, build_spec_from_project
            except Exception:
                load_project = None
                build_spec_from_project = None
            if load_project and build_spec_from_project:
                proj = load_project(str(args.project))
                spec = build_spec_from_project(proj, agent_key=(args.agent or None))
                runner = Runner(spec=spec)
            else:
                print("Error: project runtime not available.")
                sys.exit(1)
        else:
            # Auto-detect project.yml in CWD
            cwd = Path.cwd()
            proj_file = None
            p = cwd / "project.yml"
            if p.is_file():
                proj_file = str(p)
            if proj_file:
                try:
                    from omniagents.core.project.runtime import load_project, build_spec_from_project
                    proj = load_project(proj_file)
                    spec = build_spec_from_project(proj, agent_key=(args.agent or None))
                    runner = Runner(spec=spec)
                except Exception as e:
                    print(f"Error loading project: {e}")
                    sys.exit(2)
            else:
                print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                sys.exit(2)
        
        # Initialize debug system with CLI flag
        Debug.init(cli_flag=args.debug)
        
        # Pass all relevant args to the backend
        kwargs = {
            'config_path': resolved_config_path,
            'args': args,  # Pass all CLI args for backend-specific handling
            'session_id': getattr(args, 'session_id', None),
        }
        
        # Add server-specific kwargs if in server mode
        if args.mode == 'server':
            kwargs.update({
                'host': args.host,
                'port': args.port,
                'auth_token': args.auth_token,
                'reload': args.reload
            })
        
        try:
            runner.run(mode=args.mode, **kwargs)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            sys.exit(1)
        except ImportError as e:
            print(f"Error: Failed to import required modules: {e}")
            if "PyYAML" in str(e):
                print("Hint: PyYAML is required for loading config files. Install it using: pip install pyyaml")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\nExiting.")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    elif args.command == "save":
        try:
            from omniagents.core.project.runtime import load_project, resolve_agent
            proj_path = getattr(args, 'project', None)
            if not proj_path:
                p = Path.cwd() / "project.yml"
                if p.is_file():
                    proj_path = str(p)
                else:
                    print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                    sys.exit(2)
            proj = load_project(str(proj_path))
            _key, agent_yaml_path = resolve_agent(proj, agent_key=(args.agent or None))
            # Collect project tool dirs
            tool_dirs = proj.paths.get('tools') if hasattr(proj, 'paths') else []
            save_agent_as_py(str(agent_yaml_path), args.output, project_tool_dirs=tool_dirs or [])
            print(f"Standalone Python module created at: {args.output}")
        except Exception as e:
            print(f"Error saving agent spec: {e}")
            import traceback; traceback.print_exc()
            sys.exit(1)
    elif args.command == "compile":
        try:
            from omniagents.core.project.compiler import compile_project
            proj_path = getattr(args, 'project', None)
            if not proj_path:
                p = Path.cwd() / "project.yml"
                if p.is_file():
                    proj_path = str(p)
                else:
                    print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                    sys.exit(2)
            compiled_cfg_path, graph = compile_project(str(proj_path), select=str(getattr(args, 'select', '') or ''), exclude=str(getattr(args, 'exclude', '') or ''), agent_key=(str(getattr(args, 'agent', '') or '') or None))
            print(str(compiled_cfg_path))
            print(json.dumps(graph, indent=2))
        except Exception as e:
            print(f"Error compiling project: {e}")
            import traceback; traceback.print_exc()
            sys.exit(1)
    elif args.command == "eval":
        if args.eval_cmd == "run":
            try:
                from omniagents.core.project.compiler import compile_project
                proj_path = getattr(args, 'project', None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(str(proj_path), select=str(args.select or ""), exclude=str(args.exclude or ""), agent_key=(str(getattr(args, 'agent', '') or '') or None))
                from pathlib import Path as _P
                cfg = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding='utf-8')) or {}
                tracing = (cfg.get("tracing") or {})
                from omniagents.core.eval.pipeline import run_evaluation
                qlvl_pre = int(getattr(args, 'quiet', 0) or 0)
                vlvl_pre = int(getattr(args, 'verbose', 0) or 0)
                vlevel_pre = vlvl_pre - qlvl_pre
                quiet_pre = vlevel_pre < 0
                did_stream_line = False
                line_count = 0
                def _isatty_pre():
                    try:
                        return sys.stdout.isatty()
                    except Exception:
                        return False
                def _use_color_pre():
                    if args.color == "always":
                        return True
                    if args.color == "never":
                        return False
                    return _isatty_pre()
                def _c_pre(s: str, color: str) -> str:
                    if not _use_color_pre():
                        return s
                    codes = {"red": "\x1b[31m", "green": "\x1b[32m", "yellow": "\x1b[33m", "bold": "\x1b[1m", "reset": "\x1b[0m"}
                    return f"{codes.get(color, '')}{s}{codes['reset']}"
                def _progress_cb(ev: dict):
                    nonlocal did_stream_line, line_count
                    if quiet_pre:
                        return
                    if ev.get('event') == 'progress':
                        did_stream_line = True
                        skipped = bool(ev.get('skipped'))
                        failed = int(ev.get('failed') or 0)
                        if skipped:
                            ch = _c_pre('s', 'yellow')
                        elif failed == 0:
                            ch = _c_pre('.', 'green')
                        else:
                            ch = _c_pre('F', 'red')
                        try:
                            print(ch, end='', flush=True)
                        except Exception:
                            pass
                        line_count += 1
                        if line_count >= 80:
                            try:
                                print('')
                            except Exception:
                                pass
                            line_count = 0
                data = run_evaluation(_P(compiled_cfg_path), tracing_cfg=tracing, include_unmeasured=bool(getattr(args, 'include_unmeasured', False)), progress_cb=_progress_cb)
                text = json.dumps(data, indent=2)
                yaml_output = ((cfg.get("evaluation") or {}).get("output") or "")
                out_arg = str(getattr(args, 'output', '') or '').strip()
                final_out_path = out_arg or yaml_output
                if final_out_path:
                    final_out = _P(final_out_path)
                    if not final_out.is_absolute():
                        base = _P(compiled_cfg_path).resolve().parent
                        final_out = (base / final_out).resolve()
                else:
                    base_proj = _P(proj_path).resolve()
                    if base_proj.is_file():
                        base_proj = base_proj.parent
                    final_out = (base_proj / "artifacts" / "eval" / "latest" / "run_results.json").resolve()
                final_out.parent.mkdir(parents=True, exist_ok=True)
                final_out.write_text(text, encoding='utf-8')
                print(f"Wrote results: {final_out}")
                checks = []
                runs = data.get("runs", []) or []
                metrics = data.get("metrics", []) or []
                measure_total = 0
                measure_failed = 0
                for r in runs or []:
                    scen = r.get("scenario")
                    sc_cfg = r.get("scenario_config") or {}
                    sid = sc_cfg.get("id")
                    sname = sc_cfg.get("name") or scen
                    for m in (r.get("measures") or []):
                        measure_total += 1
                        pv = m.get("passed")
                        passed = bool(pv) if pv is not None else None
                        if passed is False:
                            measure_failed += 1
                        desc = f"Scenario {scen}: measure {m.get('name')}"
                        reason = m.get("reason")
                        checks.append({
                            "type": "measure",
                            "desc": desc,
                            "pass": passed is True,
                            "skip": passed is None,
                            "reason": reason if isinstance(reason, str) else "",
                            "prompt": r.get("prompt") or "",
                            "assistant": r.get("final_assistant_text") or "",
                            "scenario_id": sid,
                            "scenario_name": sname,
                        })
                metrics_failed = 0
                for mt in metrics:
                    name = mt.get("label") or mt.get("name") or "metric"
                    value = mt.get("value")
                    target = mt.get("target")
                    has_target = target is not None
                    meets = bool(mt.get("meets_target")) if has_target else None
                    if has_target and not meets:
                        metrics_failed += 1
                    vstr = f"{value:.3f}" if isinstance(value, (int, float)) else str(value)
                    if has_target:
                        op = str(target.get("op", ">="))
                        desc = f"Metric {name}: {vstr} target {op} {target.get('value')}"
                    else:
                        desc = f"Metric {name}: {vstr}"
                    checks.append({"type": "metric", "desc": desc, "pass": (meets is True) if has_target else True, "skip": not has_target, "reason": "no target" if not has_target else ""})
                total = len(checks)
                stats = data.get("stats") or {}
                scen_total = stats.get("total_scenarios")
                scen_included = stats.get("included_scenarios")
                scen_skipped = stats.get("skipped_unmeasured")
                um = stats.get("unknown_measures") or {}
                disc_count = stats.get("measures_discovered_count")
                disc_list = stats.get("measures_discovered") or []
                def _isatty():
                    try:
                        return sys.stdout.isatty()
                    except Exception:
                        return False
                def _use_color():
                    if args.color == "always":
                        return True
                    if args.color == "never":
                        return False
                    return _isatty()
                def _c(s: str, color: str) -> str:
                    if not _use_color():
                        return s
                    codes = {"red": "\x1b[31m", "green": "\x1b[32m", "yellow": "\x1b[33m", "bold": "\x1b[1m", "reset": "\x1b[0m"}
                    return f"{codes.get(color, '')}{s}{codes['reset']}"
                # Compute scenario line numbers from compiled YAML for location display
                sc_line_by_id = {}
                sc_line_by_name = {}
                try:
                    comp_lines = _P(compiled_cfg_path).read_text(encoding='utf-8').splitlines()
                    import re as _re
                    collecting = False
                    base_indent = 0
                    for i, ln in enumerate(comp_lines, start=1):
                        if not collecting:
                            m = _re.match(r'^(\s*)scenarios\s*:', ln)
                            if m:
                                collecting = True
                                base_indent = len(m.group(1))
                            continue
                        cur_indent = len(ln) - len(ln.lstrip(' '))
                        if ln.strip() == '' or ln.strip().startswith('#'):
                            continue
                        if cur_indent <= base_indent and not ln.lstrip().startswith('-'):
                            collecting = False
                            continue
                        m_id = _re.match(r'^\s*-\s*id\s*:\s*(.+?)\s*$', ln)
                        if m_id:
                            scid = m_id.group(1).strip().strip('\"\'')
                            if scid and scid not in sc_line_by_id:
                                sc_line_by_id[scid] = i
                            continue
                        m_name = _re.match(r'^\s*-\s*name\s*:\s*(.+?)\s*$', ln)
                        if m_name:
                            nm = m_name.group(1).strip().strip('\"\'')
                            if nm and nm not in sc_line_by_name:
                                sc_line_by_name[nm] = i
                            continue
                except Exception:
                    sc_line_by_id = {}
                    sc_line_by_name = {}
                for c in checks:
                    if c.get('type') == 'measure':
                        sid = c.get('scenario_id')
                        sname = c.get('scenario_name')
                        ln = (sc_line_by_id.get(sid) if sid else None) or (sc_line_by_name.get(sname) if sname else None)
                        if ln:
                            c['file'] = str(_P(compiled_cfg_path))
                            c['line'] = int(ln)
                reporter = args.reporter
                if reporter == "auto":
                    reporter = "pretty" if _isatty() else "tap"
                if reporter == "tap":
                    print("TAP version 13")
                    print(f"1..{total}")
                    for i, c in enumerate(checks, start=1):
                        if c.get("skip"):
                            print(f"ok {i} - {c.get('desc')} # SKIP {c.get('reason')}")
                        elif c.get("pass"):
                            print(f"ok {i} - {c.get('desc')}")
                        else:
                            print(f"not ok {i} - {c.get('desc')}")
                            if c.get("reason"):
                                print(f"# {c.get('reason')}")
                            if c.get("type") == "measure":
                                fp = c.get('file'); fl = c.get('line')
                                if fp and fl:
                                    print(f"# Location: {fp}:{fl}")
                                p = c.get('prompt') or ''
                                if p:
                                    pt = p if len(p) <= 300 else (p[:300] + '...')
                                    print(f"# Prompt: {pt}")
                                a = c.get('assistant') or ''
                                if a:
                                    at = a if len(a) <= 300 else (a[:300] + '...')
                                    print(f"# Assistant: {at}")
                    print(f"# Summary: {len(metrics)} metrics, {metrics_failed} failing targets")
                    print(f"# Measures: {measure_total} total, {measure_failed} failing")
                    if scen_total is not None:
                        print(f"# Scenarios: included={scen_included} skipped_unmeasured={scen_skipped} total={scen_total}")
                    if um:
                        for k in sorted(um.keys()):
                            try:
                                cnt = int(um[k])
                            except Exception:
                                cnt = um[k]
                            print(f"# Warning: unknown measure '{k}' in {cnt} scenario(s)")
                else:
                    qlvl = int(getattr(args, 'quiet', 0) or 0)
                    vlvl = int(getattr(args, 'verbose', 0) or 0)
                    vlevel = vlvl - qlvl
                    quiet = vlevel < 0
                    verbose = vlevel > 0
                    passed_count = sum(1 for x in checks if not x.get("skip") and x.get("pass"))
                    failed = [x for x in checks if not x.get("skip") and not x.get("pass")]
                    skipped = [x for x in checks if x.get("skip")]
                    if not quiet and not verbose and not did_stream_line:
                        print(f"Running {total} checks...")
                        line = []
                        for x in checks:
                            if x.get("skip"):
                                line.append(_c("s", "yellow"))
                            elif x.get("pass"):
                                line.append(_c(".", "green"))
                            else:
                                line.append(_c("F", "red"))
                            if len(line) >= 80:
                                print("".join(line))
                                line = []
                        if line:
                            print("".join(line))
                    if not quiet and failed:
                        print("")
                        print(_c("Failures:", "bold"))
                        for idx, f in enumerate(failed, start=1):
                            title = f"{idx}) {f.get('desc')}"
                            print(_c("FAIL ", "red") + title)
                            rsn = f.get("reason")
                            if f.get('type') == 'measure':
                                fp = f.get('file'); fl = f.get('line')
                                if fp and fl:
                                    print(f"  Location: {fp}:{fl}")
                                prompt = f.get('prompt') or ''
                                if prompt:
                                    p = prompt if len(prompt) <= 300 else (prompt[:300] + '...')
                                    print(f"  Prompt: {p}")
                                assistant = f.get('assistant') or ''
                                if assistant:
                                    a = assistant if len(assistant) <= 300 else (assistant[:300] + '...')
                                    print(f"  Assistant: {a}")
                            if rsn and verbose:
                                print(f"  Reason: {rsn}")
                    if not quiet:
                        print("")
                        print(f"Passed: {passed_count}  Failed: {len(failed)}  Skipped: {len(skipped)}  Total: {total}")
                        print(f"Metrics: {len(metrics)}  Failing targets: {metrics_failed}")
                        print(f"Measures: {measure_total}  Failing: {measure_failed}")
                        if scen_total is not None:
                            print(f"Scenarios: included={scen_included}  Skipped (no measures)={scen_skipped}  Total={scen_total}")
                        if um:
                            pairs = []
                            for k in sorted(um.keys()):
                                try:
                                    pairs.append(f"{k}({int(um[k])})")
                                except Exception:
                                    pairs.append(f"{k}({um[k]})")
                            print(f"Warning: unknown measures not registered: {', '.join(pairs)}")
                        if verbose and disc_count is not None:
                            names = ", ".join(disc_list[:10])
                            extra = "" if len(disc_list) <= 10 else ", ..."
                            print(f"Discovered measures: {disc_count} [{names}{extra}]")
                overall_failed = (metrics_failed > 0) or (measure_failed > 0)
                if overall_failed:
                    sys.exit(1)
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error running evaluation: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "scenarios" and getattr(args, 'scen_cmd', None) == "generate":
            try:
                from omniagents.core.project.compiler import compile_project
                proj_path = getattr(args, 'project', None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(str(proj_path), select=str(args.select or ""), exclude=str(args.exclude or ""), agent_key=(str(getattr(args, 'agent', '') or '') or None))
                from omniagents.core.eval.scenarios import generate_scenarios
                from pathlib import Path as _P
                import asyncio as _asyncio
                scenarios, out_path, gen_ts = _asyncio.run(generate_scenarios(_P(compiled_cfg_path), count=args.count, seed=args.seed))
                print(f"Wrote {len(scenarios)} scenarios to {out_path}")
            except Exception as e:
                print(f"Error generating scenarios: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "scenarios" and getattr(args, 'scen_cmd', None) == "run":
            try:
                from omniagents.core.project.compiler import compile_project
                proj_path = getattr(args, 'project', None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(str(proj_path), select=str(getattr(args,'select', '') or ''), exclude=str(getattr(args,'exclude','') or ''), agent_key=(str(getattr(args, 'agent', '') or '') or None))
                from omniagents.core.eval.scenarios import run_scenarios_against_agent
                from pathlib import Path as _P
                base = _P(proj_path).resolve(); base = base.parent if base.is_file() else base
                inp = str(getattr(args, 'input', '') or '').strip()
                in_path = _P(inp)
                if not in_path.is_absolute():
                    in_path = (base / in_path).resolve()
                if not in_path.exists():
                    eval_dir = (base / "artifacts" / "eval")
                    preferred = [eval_dir / "latest" / "generated_scenarios.yml"]
                    picked = None
                    for c in preferred:
                        if c.exists():
                            picked = c.resolve(); break
                    if not picked:
                        try:
                            ts_dirs = []
                            if eval_dir.exists():
                                for d in eval_dir.iterdir():
                                    if d.is_dir() and re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", d.name):
                                        ts_dirs.append(d)
                            ts_dirs.sort(key=lambda p: p.name, reverse=True)
                            for d in ts_dirs:
                                c = d / "generated_scenarios.yml"
                                if c.exists():
                                    picked = c.resolve(); break
                        except Exception:
                            picked = None
                    if picked:
                        in_path = picked
                if not in_path.exists():
                    print(f"Error: Could not find scenarios file. Searched: {args.input} and latest timestamped folders under {base / 'artifacts' / 'eval'}")
                    sys.exit(2)
                ts_dir = in_path.parent
                y = yaml.safe_load(in_path.read_text(encoding='utf-8')) or {}
                scenarios = list((y.get('scenarios') or []))
                for sc in scenarios:
                    if not sc.get('id'):
                        mapping = sc.get('dimensions') if isinstance(sc.get('dimensions'), dict) else None
                        import hashlib, json as _json
                        blob = _json.dumps({"prompt": str(sc.get("prompt", "")), "dimensions": mapping or {}}, sort_keys=True)
                        sc['id'] = hashlib.sha1(blob.encode('utf-8')).hexdigest()[:16]
                cfg = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding='utf-8')) or {}
                tracing = (cfg.get('tracing') or {})
                try:
                    if str(tracing.get('provider','')).lower() == 'agentio':
                        import agentio
                        from datetime import datetime
                        project = tracing.get('project') or 'scenario_runs'
                        base_name = tracing.get('name') or Path(compiled_cfg_path).stem
                        ts = ts_dir.name if re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", ts_dir.name) else datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                        name = f"{base_name}_scenarios_{ts}"
                        resume = (tracing.get('settings') or {}).get('resume', 'never')
                        agentio.init(project=str(project), name=name, resume=str(resume), replace_default=True)
                        import asyncio as _asyncio
                        runs = _asyncio.run(run_scenarios_against_agent(_P(compiled_cfg_path).resolve(), scenarios, max_turns=args.run_max_turns, include_history=bool(getattr(args,'include_history', False)), workflow_name=name))
                    else:
                        import asyncio as _asyncio
                        runs = _asyncio.run(run_scenarios_against_agent(_P(compiled_cfg_path).resolve(), scenarios, max_turns=args.run_max_turns, include_history=bool(getattr(args,'include_history', False))))
                except Exception:
                    import asyncio as _asyncio
                    runs = _asyncio.run(run_scenarios_against_agent(_P(compiled_cfg_path).resolve(), scenarios, max_turns=args.run_max_turns, include_history=bool(getattr(args,'include_history', False))))
                out_arg = str(getattr(args, 'output', '') or '').strip()
                if out_arg:
                    run_out = _P(out_arg)
                    if not run_out.is_absolute():
                        run_out = (base / run_out).resolve()
                else:
                    run_out = ts_dir / 'generated_runs.json'
                run_out.parent.mkdir(parents=True, exist_ok=True)
                Path(run_out).write_text(json.dumps({"runs": runs}, indent=2), encoding='utf-8')
                print(f"Ran {len(runs)} scenarios; wrote results to {run_out}")
            except Exception as e:
                print(f"Error running scenarios: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "scenarios":
            print("Error: Provide a scenarios subcommand: generate | run")
            sys.exit(2)
        elif args.eval_cmd == "annotate":
            try:
                from omniagents.core.project.compiler import compile_project
                proj_path = getattr(args, 'project', None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(str(proj_path), select=None, exclude=None, agent_key=(str(getattr(args, 'agent', '') or '') or None))
                from pathlib import Path as _P
                agent_yaml = _P(compiled_cfg_path).resolve()
                from omniagents.core.eval.annotate import annotate_scenarios, write_annotation_output
                cfg = yaml.safe_load(agent_yaml.read_text(encoding='utf-8')) or {}
                tracing = cfg.get("tracing") or {}
                agentio_project = args.agentio_project or tracing.get("project") or "default"
                base = _P(proj_path).resolve()
                if base.is_file():
                    base = base.parent
                inp = str(getattr(args, 'input', '') or '').strip()
                in_path = _P(inp)
                if not in_path.is_absolute():
                    in_path = (base / in_path).resolve()
                if not in_path.exists():
                    eval_dir = (base / "artifacts" / "eval")
                    preferred = [
                        eval_dir / "latest" / "generated_scenarios.yml",
                    ]
                    picked = None
                    for c in preferred:
                        if c.exists():
                            picked = c.resolve()
                            break
                    if not picked:
                        try:
                            ts_dirs = []
                            if eval_dir.exists():
                                for d in eval_dir.iterdir():
                                    if d.is_dir() and re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", d.name):
                                        ts_dirs.append(d)
                            ts_dirs.sort(key=lambda p: p.name, reverse=True)
                            for d in ts_dirs:
                                c = d / "generated_scenarios.yml"
                                if c.exists():
                                    picked = c.resolve()
                                    break
                        except Exception:
                            picked = None
                    if picked:
                        in_path = picked
                # Compute workflow name override based on timestamped parent directory
                override_wf = getattr(args, 'workflow_name', None)
                if not override_wf:
                    ts = None
                    try:
                        par = in_path.parent
                        if re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", par.name):
                            ts = par.name
                    except Exception:
                        ts = None
                    if ts:
                        base_name = (tracing.get("name") or agent_yaml.stem)
                        override_wf = f"{base_name}_scenarios_{ts}"
                if not in_path.exists():
                    print(f"Error: Could not find scenarios file. Searched: {args.input} and latest timestamped folders under {base / 'artifacts' / 'eval'}")
                    sys.exit(2)
                annotated, original, _ = annotate_scenarios(agent_yaml, in_path, agentio_project, override_wf)
                out_path = write_annotation_output(base, in_path, annotated)
                labeled = sum(1 for sc in annotated if sc.get("failure_categories"))
                print(f"Annotated {len(original)} scenarios; labeled={labeled}; wrote {out_path}")
                try:
                    cfg_all = yaml.safe_load(agent_yaml.read_text(encoding='utf-8')) or {}
                    eval_block = (cfg_all.get('evaluation') or {})
                    fc_cfg = (eval_block.get('failure_categories') or [])
                    judges_cfg = (eval_block.get('judges') or {})
                    def _slugify_local(s: str) -> str:
                        t = str(s).lower()
                        try:
                            t = t.encode('ascii','ignore').decode('ascii')
                        except Exception:
                            pass
                        import re as _re
                        t = _re.sub(r"[^a-z0-9]+", "_", t)
                        t = t.strip('_')
                        t = _re.sub(r"_+", "_", t)
                        return t or 'category'
                    counts = {}
                    for sc in annotated:
                        for c in (sc.get('failure_categories') or []):
                            counts[c] = counts.get(c, 0) + 1
                    threshold = 30
                    for cat in fc_cfg:
                        name = str(cat.get('name') or '')
                        slug = _slugify_local(name)
                        measures = cat.get('measures') or []
                        uses_judge = any((isinstance(m, str) and m.endswith('_agent')) for m in measures)
                        if not uses_judge:
                            continue
                        cnt = int(counts.get(slug, 0))
                        if cnt < threshold:
                            print(f"Warning: LLM-as-Judge category '{name}' has only {cnt} labeled scenarios (<{threshold}).")
                except Exception:
                    pass
            except Exception as e:
                print(f"Error annotating scenarios: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "split":
            try:
                from omniagents.core.eval.split import create_splits
                from pathlib import Path as _P
                proj_path = getattr(args, 'project', None)
                base = None
                if proj_path:
                    base = _P(proj_path).resolve()
                    if base.is_file():
                        base = base.parent
                else:
                    cwd = _P.cwd()
                    if (cwd / "project.yml").is_file():
                        base = cwd
                inp = str(getattr(args, 'input', '') or '').strip()
                in_path = _P(inp)
                if not in_path.is_absolute():
                    if base is not None:
                        in_path = (base / in_path).resolve()
                    else:
                        in_path = in_path.resolve()
                if not in_path.exists():
                    search_root = (base if base is not None else _P.cwd())
                    eval_dir = (search_root / "artifacts" / "eval")
                    preferred = [eval_dir / "latest" / "annotated_scenarios.yml"]
                    picked = None
                    for c in preferred:
                        if c.exists():
                            picked = c.resolve()
                            break
                    if not picked:
                        try:
                            ts_dirs = []
                            if eval_dir.exists():
                                for d in eval_dir.iterdir():
                                    if d.is_dir() and re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", d.name):
                                        ts_dirs.append(d)
                            ts_dirs.sort(key=lambda p: p.name, reverse=True)
                            for d in ts_dirs:
                                c = d / "annotated_scenarios.yml"
                                if c.exists():
                                    picked = c.resolve()
                                    break
                        except Exception:
                            picked = None
                    if picked:
                        in_path = picked
                if not in_path.exists():
                    print(f"Error: Could not find annotated scenarios file. Searched: {args.input} and latest timestamped folders under {(base if base is not None else _P.cwd()) / 'artifacts' / 'eval'}")
                    sys.exit(2)
                out_dir = None
                if getattr(args, 'output_dir', ''):
                    od = _P(getattr(args, 'output_dir'))
                    if not od.is_absolute():
                        if base is not None:
                            od = (base / od).resolve()
                        else:
                            od = od.resolve()
                    out_dir = od
                out = create_splits(in_path, category=str(args.category), output_dir=out_dir, f_train=float(args.train), f_dev=float(args.dev), f_test=float(args.test), seed=int(args.seed), negatives_ratio=(float(args.negatives_ratio) if args.negatives_ratio is not None else None))
                print(f"Wrote: {out['train']}")
                print(f"Wrote: {out['dev']}")
                print(f"Wrote: {out['test']}")
            except Exception as e:
                print(f"Error splitting scenarios: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "judge" and getattr(args, 'judge_cmd', None) == "run":
            try:
                from omniagents.core.project.compiler import compile_project
                proj_path = getattr(args, 'project', None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(str(proj_path), select=None, exclude=None, agent_key=(str(getattr(args, 'agent', '') or '') or None))
                from omniagents.core.eval.judges import evaluate_split
                import asyncio as _asyncio
                from pathlib import Path as _P
                base = _P(proj_path).resolve()
                if base.is_file():
                    base = base.parent
                qlvl_pre = int(getattr(args, 'quiet', 0) or 0)
                vlvl_pre = int(getattr(args, 'verbose', 0) or 0)
                vlevel_pre = vlvl_pre - qlvl_pre
                quiet_pre = vlevel_pre < 0
                did_stream_line = False
                line_count = 0
                def _isatty_pre():
                    try:
                        return sys.stdout.isatty()
                    except Exception:
                        return False
                def _use_color_pre():
                    if args.color == "always":
                        return True
                    if args.color == "never":
                        return False
                    return _isatty_pre()
                def _c_pre(s: str, color: str) -> str:
                    if not _use_color_pre():
                        return s
                    codes = {"red": "\x1b[31m", "green": "\x1b[32m", "yellow": "\x1b[33m", "bold": "\x1b[1m", "reset": "\x1b[0m"}
                    return f"{codes.get(color, '')}{s}{codes['reset']}"
                def _progress_cb(ev: dict):
                    nonlocal did_stream_line, line_count
                    if quiet_pre:
                        return
                    if ev.get('event') == 'progress':
                        did_stream_line = True
                        pred = ev.get('pred')
                        label = ev.get('label')
                        agree = ev.get('agree')
                        if pred is None:
                            ch = _c_pre('s', 'yellow')
                        elif bool(agree):
                            ch = _c_pre('.', 'green')
                        else:
                            ch = _c_pre('F', 'red')
                        try:
                            print(ch, end='', flush=True)
                        except Exception:
                            pass
                        line_count += 1
                        if line_count >= 80:
                            try:
                                print('')
                            except Exception:
                                pass
                            line_count = 0
                split_file_arg = getattr(args, 'split_file', None)
                if split_file_arg:
                    split_path = _P(split_file_arg)
                    if not split_path.is_absolute():
                        split_path = (base / split_path).resolve()
                    split_name = split_path.stem
                else:
                    category = str(getattr(args, 'category', '') or '').strip()
                    if not category:
                        print("Error: Provide either --split-file <path> or --category <name> with --split <dev|test|train>.")
                        sys.exit(2)
                    which = str(getattr(args, 'split', 'dev') or 'dev')
                    eval_dir = (base / "artifacts" / "eval")
                    candidate = (eval_dir / "latest" / "splits" / category / f"{which}.yml")
                    picked = None
                    if candidate.exists():
                        picked = candidate.resolve()
                    else:
                        try:
                            ts_dirs = []
                            if eval_dir.exists():
                                for d in eval_dir.iterdir():
                                    if d.is_dir() and re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", d.name):
                                        ts_dirs.append(d)
                            ts_dirs.sort(key=lambda p: p.name, reverse=True)
                            for d in ts_dirs:
                                c = d / "splits" / category / f"{which}.yml"
                                if c.exists():
                                    picked = c.resolve()
                                    break
                        except Exception:
                            picked = None
                    if not picked:
                        print(f"Error: Could not find split for category '{category}' ({which}). Looked under {eval_dir}/latest and most recent timestamped folders.")
                        sys.exit(2)
                    split_path = picked
                    split_name = which
                # Auto-detect judge if not provided and only one exists
                jkey = getattr(args, 'judge', None)
                if not jkey:
                    cfg = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding='utf-8')) or {}
                    js = ((cfg.get('evaluation') or {}).get('judges') or {})
                    if isinstance(js, dict) and len(js) == 1:
                        jkey = next(iter(js.keys()))
                    else:
                        print("Error: Provide --judge <key> (multiple or no judges configured).")
                        sys.exit(2)
                out = _asyncio.run(evaluate_split(
                    _P(compiled_cfg_path),
                    split_path,
                    str(jkey),
                    judge_max_turns=int(args.max_turns or 1),
                    run_max_turns=(int(args.run_max_turns) if args.run_max_turns is not None else None),
                    show=int(args.show),
                    replay_mode=str(getattr(args, 'replay_mode', 'auto') or 'auto'),
                    runs_file=(str(getattr(args, 'runs_file')) if getattr(args, 'runs_file', None) else None),
                    progress_cb=_progress_cb,
                ))
                cfg = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding='utf-8')) or {}
                yml_out = ((cfg.get("evaluation") or {}).get("judge_output") or (cfg.get("evaluation") or {}).get("output") or "")
                out_path = _P(args.output) if args.output else None
                if not out_path and yml_out:
                    candidate = _P(yml_out)
                    if not candidate.is_absolute():
                        base = _P(compiled_cfg_path).resolve().parent
                        candidate = (base / candidate).resolve()
                    out_path = candidate
                if not out_path:
                    ts_dir = split_path.parents[2]
                    out_dir = ts_dir / "judge"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / (split_path.stem + f"_{jkey}.json")
                tp = out.get("counts", {}).get("TP"); tn = out.get("counts", {}).get("TN"); fp = out.get("counts", {}).get("FP"); fn = out.get("counts", {}).get("FN")
                tpr = out.get("tpr"); tnr = out.get("tnr")
                try:
                    results = out.get('results') or []
                    checks = []
                    try:
                        thr = 30
                        pass_cnt = sum(1 for r in results if r.get('label') is False)
                        fail_cnt = sum(1 for r in results if r.get('label') is True)
                        cat_for_warn = None
                        try:
                            cat_for_warn = split_path.parent.name
                        except Exception:
                            cat_for_warn = None
                        prefix = (f"category '{cat_for_warn}' " if cat_for_warn else "")
                        if pass_cnt < thr:
                            print(f"Warning: {prefix}split '{split_name}' has only {pass_cnt} Pass labels (<{thr}).")
                        if fail_cnt < thr:
                            print(f"Warning: {prefix}split '{split_name}' has only {fail_cnt} Fail labels (<{thr}).")
                    except Exception:
                        pass
                    for r in results:
                        pred = r.get('pred')
                        agree = r.get('agree')
                        label = r.get('label')
                        name = r.get('name') or r.get('id')
                        sid = r.get('id')
                        exp = (r.get('judge_output') or {}).get('explanation') or (r.get('judge_output') or {}).get('reasoning') or ''
                        kind = 'FP' if (pred is True and label is False) else ('FN' if (pred is False and label is True) else ('UNK' if pred is None else 'OK'))
                        reason = f"label={label} pred={pred} {kind}"
                        if exp:
                            reason = f"{reason} — {exp}"
                        checks.append({
                            "type": "scenario",
                            "desc": f"Scenario {name} ({sid})",
                            "pass": bool(agree) if agree is not None else False,
                            "skip": pred is None,
                            "reason": reason,
                            "prompt": r.get('prompt') or '',
                            "assistant": r.get('final_assistant_text') or '',
                            "split_file": r.get('split_file') or out.get('split'),
                            "split_line": int(r.get('split_line') or 0),
                            "label": label,
                            "pred": pred,
                        })
                    total = len(checks)
                    def _isatty():
                        try:
                            return sys.stdout.isatty()
                        except Exception:
                            return False
                    def _use_color():
                        if args.color == "always":
                            return True
                        if args.color == "never":
                            return False
                        return _isatty()
                    def _c(s: str, color: str) -> str:
                        if not _use_color():
                            return s
                        codes = {"red": "\x1b[31m", "green": "\x1b[32m", "yellow": "\x1b[33m", "bold": "\x1b[1m", "reset": "\x1b[0m"}
                        return f"{codes.get(color, '')}{s}{codes['reset']}"
                    reporter = args.reporter
                    if reporter == "auto":
                        reporter = "pretty" if _isatty() else "tap"
                    qlvl = int(getattr(args, 'quiet', 0) or 0)
                    vlvl = int(getattr(args, 'verbose', 0) or 0)
                    vlevel = vlvl - qlvl
                    quiet = vlevel < 0
                    verbose = vlevel > 0
                    passed_count = sum(1 for x in checks if not x.get("skip") and x.get("pass"))
                    failed = [x for x in checks if not x.get("skip") and not x.get("pass")]
                    skipped = [x for x in checks if x.get("skip")]
                    if reporter == "tap":
                        print("TAP version 13")
                        print(f"1..{total}")
                        for i, c in enumerate(checks, start=1):
                            if c.get("skip"):
                                print(f"ok {i} - {c.get('desc')} # SKIP {c.get('reason')}")
                            elif c.get("pass"):
                                print(f"ok {i} - {c.get('desc')}")
                            else:
                                print(f"not ok {i} - {c.get('desc')}")
                                if c.get("reason"):
                                    print(f"# {c.get('reason')}")
                                p = c.get('prompt') or ''
                                if p:
                                    pt = p if len(p) <= 300 else (p[:300] + '...')
                                    print(f"# Prompt: {pt}")
                                a = c.get('assistant') or ''
                                if a:
                                    at = a if len(a) <= 300 else (a[:300] + '...')
                                    print(f"# Assistant: {at}")
                        print(f"# Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                        print(f"# TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}")
                    else:
                        if not quiet and not verbose and not did_stream_line:
                            print(f"Judged {total} scenarios...")
                            line = []
                            for x in checks:
                                if x.get('skip'):
                                    line.append(_c('s', 'yellow'))
                                elif x.get('pass'):
                                    line.append(_c('.', 'green'))
                                else:
                                    line.append(_c('F', 'red'))
                                if len(line) >= 80:
                                    print(''.join(line)); line = []
                            if line:
                                print(''.join(line))
                        if not quiet and failed and bool(getattr(args, 'disagreements', False)):
                            print("")
                            print(_c("Failures:", "bold"))
                            for idx, f in enumerate(failed, start=1):
                                label = f.get('label'); pred = f.get('pred')
                                sf = f.get('split_file'); ln = int(f.get('split_line') or 0)
                                kind = 'FP' if (pred is True and label is False) else ('FN' if (pred is False and label is True) else 'DISAGREE')
                                loc = f"{sf}:{ln}" if sf else ""
                                title = f"{idx}) {f.get('desc')} label={label} pred={pred} {kind} {loc}"
                                print(_c("FAIL ", "red") + title)
                                prompt = f.get('prompt') or ''
                                if prompt:
                                    p = prompt if len(prompt) <= 300 else (prompt[:300] + '...')
                                    print(f"  Prompt: {p}")
                                assistant = f.get('assistant') or ''
                                if assistant:
                                    a = assistant if len(assistant) <= 300 else (assistant[:300] + '...')
                                    print(f"  Assistant: {a}")
                                rsn = f.get('reason')
                                if rsn and verbose:
                                    print(f"  Reason: {rsn}")
                        if not quiet:
                            print("")
                            print(f"Passed: {passed_count}  Failed: {len(failed)}  Skipped: {len(skipped)}  Total: {total}")
                            print(f"Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                            print(f"TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}")
                except Exception as _:
                    # Fallback to minimal counts output if reporter fails
                    print(f"Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                    print(f"TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}")
                # Write results after displaying metrics/failures
                out_path.write_text(json.dumps(out, indent=2), encoding='utf-8')
                print(f"Wrote results: {out_path}")
                try:
                    results_for_exit = out.get('results') or []
                    any_disagree = any(((r.get('pred') is True and r.get('label') is False) or (r.get('pred') is False and r.get('label') is True)) for r in results_for_exit)
                    if bool(getattr(args, 'fail_on_disagreement', False)) and any_disagree:
                        sys.exit(1)
                except Exception:
                    pass
            except Exception as e:
                print(f"Error judge-evaluating split: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "judge" and getattr(args, 'judge_cmd', None) == "optimize":
            try:
                try:
                    import gepa  # type: ignore
                except Exception:
                    print("Error: This command requires the 'gepa' package. Install with: pip install gepa[full]")
                    sys.exit(1)
                from omniagents.core.eval.gepa_judge_adapter import OmniAgentsJudgeAdapter
                from omniagents.core.project.compiler import compile_project
                proj_path = getattr(args, 'project', None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(str(proj_path), select=None, exclude=None, agent_key=(str(getattr(args, 'agent', '') or '') or None))
                from pathlib import Path as _P
                base = _P(proj_path).resolve()
                if base.is_file():
                    base = base.parent
                category = str(getattr(args, 'category') or '').strip()
                def _resolve_split(which, explicit):
                    if explicit:
                        p = _P(explicit)
                        return p if p.is_absolute() else (base / p).resolve()
                    eval_dir = (base / "artifacts" / "eval")
                    candidate = (eval_dir / "latest" / "splits" / category / f"{which}.yml")
                    if candidate.exists():
                        return candidate.resolve()
                    picked = None
                    try:
                        ts_dirs = []
                        if eval_dir.exists():
                            for d in eval_dir.iterdir():
                                if d.is_dir() and re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", d.name):
                                    ts_dirs.append(d)
                        ts_dirs.sort(key=lambda p: p.name, reverse=True)
                        for d in ts_dirs:
                            c = d / "splits" / category / f"{which}.yml"
                            if c.exists():
                                picked = c.resolve(); break
                    except Exception:
                        picked = None
                    if picked:
                        return picked
                    print(f"Error: Could not find split for category '{category}' ({which}). Looked under {eval_dir}/latest and most recent timestamped folders.")
                    sys.exit(2)
                tr_file = getattr(args, 'train_file', None)
                dv_file = getattr(args, 'dev_file', None)
                ts_file = getattr(args, 'test_file', None)
                if not category and not (tr_file and dv_file and ts_file):
                    print("Error: Provide --category or all of --train-file/--dev-file/--test-file.")
                    sys.exit(2)
                tr_path = _resolve_split("train", tr_file) if category or not tr_file else (_P(tr_file) if _P(tr_file).is_absolute() else (base / tr_file).resolve())
                dv_path = _resolve_split("dev", dv_file) if category or not dv_file else (_P(dv_file) if _P(dv_file).is_absolute() else (base / dv_file).resolve())
                ts_path = _resolve_split("test", ts_file) if category or not ts_file else (_P(ts_file) if _P(ts_file).is_absolute() else (base / ts_file).resolve())
                def _read_items(p: _P):
                    y = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
                    items = [x for x in (y.get('scenarios') or []) if isinstance(x, dict)]
                    return [{"id": str(x.get('id')), "label": bool(x.get('label')), "name": x.get('name')} for x in items]
                trainset = _read_items(tr_path)
                devset = _read_items(dv_path)
                testset = _read_items(ts_path)
                try:
                    thr = 30
                    def _warn_split(_name, _ds):
                        p_cnt = sum(1 for x in (_ds or []) if bool(x.get('label')) is False)
                        f_cnt = sum(1 for x in (_ds or []) if bool(x.get('label')) is True)
                        cat_for_warn = category or dv_path.parent.name
                        prefix = f"category '{cat_for_warn}' " if cat_for_warn else ""
                        if p_cnt < thr:
                            print(f"Warning: {prefix}split '{_name}' has only {p_cnt} Pass labels (<{thr}).")
                        if f_cnt < thr:
                            print(f"Warning: {prefix}split '{_name}' has only {f_cnt} Fail labels (<{thr}).")
                    _warn_split('train', trainset)
                    _warn_split('dev', devset)
                    _warn_split('test', testset)
                except Exception:
                    pass
                def _default_runs_path(sp: _P) -> _P:
                    return sp.parents[2] / "generated_runs.json"
                runs_file = getattr(args, 'runs_file', None)
                if runs_file:
                    rf = _P(runs_file)
                    runs_path = rf if rf.is_absolute() else (base / rf).resolve()
                else:
                    runs_path = _default_runs_path(dv_path)
                if not runs_path.exists():
                    print(f"Error: Missing generated runs fixtures: {runs_path}. Generate scenarios, then run with 'omniagents eval scenarios run'.")
                    sys.exit(2)
                jkey = getattr(args, 'judge', None)
                if not jkey:
                    cfg_detect = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding='utf-8')) or {}
                    js = ((cfg_detect.get('evaluation') or {}).get('judges') or {})
                    if isinstance(js, dict) and len(js) == 1:
                        jkey = next(iter(js.keys()))
                    else:
                        print("Error: Provide --judge <key> (multiple or no judges configured).")
                        sys.exit(2)
                # Seed candidate from compiled config
                cfg = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding='utf-8')) or {}
                jcfg = ((cfg.get('evaluation') or {}).get('judges') or {}).get(jkey) or {}
                def _read_text_file(base_dir: _P, rel: str) -> str:
                    p = _P(rel)
                    if not p.is_absolute():
                        p = (base_dir / rel).resolve()
                    try:
                        return p.read_text(encoding='utf-8')
                    except Exception:
                        return ''
                agent_cfg = jcfg.get('agent') or {}
                input_cfg = jcfg.get('input') or {}
                seed_instructions = agent_cfg.get('instructions') or ''
                if (not seed_instructions) and agent_cfg.get('instructions_file'):
                    seed_instructions = _read_text_file(_P(compiled_cfg_path).resolve().parent, str(agent_cfg.get('instructions_file')))
                seed_template = input_cfg.get('template') or ''
                if (not seed_template) and input_cfg.get('template_file'):
                    seed_template = _read_text_file(_P(compiled_cfg_path).resolve().parent, str(input_cfg.get('template_file')))
                if not seed_template:
                    seed_template = "{{ text }}"
                if not seed_instructions:
                    seed_instructions = "You are an evaluation agent. Return ONLY JSON."
                seed_candidate = {}
                comps = [s.strip() for s in str(getattr(args, 'optimize', 'instructions,template')).split(',') if s.strip()]
                if 'instructions' in comps:
                    seed_candidate['instructions'] = seed_instructions
                if 'template' in comps:
                    seed_candidate['template'] = seed_template
                adapter = OmniAgentsJudgeAdapter(_P(compiled_cfg_path), jkey, runs_path, source=str(getattr(args,'source','final_assistant_text') or 'final_assistant_text'), judge_max_turns=int(getattr(args,'max_turns') or 1))
                out_dir = getattr(args, 'output', '')
                if out_dir:
                    outp = _P(out_dir)
                    run_dir = outp if outp.is_absolute() else (base / outp).resolve()
                else:
                    run_dir = dv_path.parents[2] / "judge_optimize" / jkey
                run_dir.mkdir(parents=True, exist_ok=True)
                result = gepa.optimize(
                    seed_candidate=seed_candidate,
                    trainset=trainset,
                    valset=devset,
                    adapter=adapter,
                    max_metric_calls=int(getattr(args,'max_metric_calls') or 100),
                    reflection_lm=str(getattr(args,'reflection_model')),
                    run_dir=str(run_dir),
                    seed=int(getattr(args,'seed') or 0),
                    display_progress_bar=True,
                )
                best = result.best_candidate
                # Quick test evaluation on test set
                def _acc(ds, cand):
                    ev = adapter.evaluate(ds, cand, capture_traces=False)
                    return (sum(ev.scores)/len(ev.scores)) if ev.scores else 0.0
                dev_acc = _acc(devset, best)
                test_acc = _acc(testset, best)
                # Write artifacts
                out_cfg = {
                    "judge": jkey,
                    "optimized": {
                        "instructions": best.get('instructions'),
                        "template": best.get('template'),
                    },
                    "metrics": {
                        "dev_accuracy": dev_acc,
                        "test_accuracy": test_acc,
                    },
                }
                out_json = run_dir / "result.json"
                out_json.write_text(json.dumps(out_cfg, indent=2), encoding='utf-8')
                print(f"Optimized judge '{jkey}'. Dev acc={dev_acc:.3f} Test acc={test_acc:.3f}")
                print(f"Wrote: {out_json}")
                # Also emit a judge override YAML for convenience
                class _LiteralStr(str):
                    pass
                class _LiteralDumper(yaml.SafeDumper):
                    pass
                def _lit_representer(dumper, data):
                    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
                _LiteralDumper.add_representer(_LiteralStr, _lit_representer)
                instr_val = best.get('instructions') if isinstance(best.get('instructions'), str) else ''
                tmpl_val = best.get('template') if isinstance(best.get('template'), str) else ''
                instr_node = _LiteralStr(instr_val) if ('\n' in instr_val) else instr_val
                tmpl_node = _LiteralStr(tmpl_val) if ('\n' in tmpl_val) else tmpl_val
                override = {
                    "judges": {
                        jkey: {
                            "agent": {"instructions": instr_node},
                            "input": {"template": tmpl_node},
                        }
                    }
                }
                out_yml = run_dir / f"{jkey}_override.yml"
                out_yml.write_text(yaml.dump(override, Dumper=_LiteralDumper, sort_keys=False, allow_unicode=True), encoding='utf-8')
                print(f"Judge override written: {out_yml}")

                if bool(getattr(args, 'apply', False)):
                    from omniagents.core.project.runtime import load_project
                    from datetime import datetime
                    def _backup(fp: _P):
                        try:
                            ts = datetime.now().strftime("%Y%m%d%H%M%S")
                            bak = fp.with_suffix(fp.suffix + f".bak.{ts}")
                            bak.write_text(fp.read_text(encoding='utf-8'), encoding='utf-8')
                            return bak
                        except Exception:
                            return None
                    def _find_judge_source(proj_path: _P, key: str) -> tuple[_P, dict, str | None]:
                        pj = load_project(str(proj_path))
                        paths = pj.paths.get('evaluations') or []
                        cands: list[_P] = []
                        for ed in paths:
                            p = _P(ed)
                            if p.is_file():
                                cands.append(p)
                            elif p.is_dir():
                                cands.extend([p / 'judges.yml'])
                                try:
                                    for y in (p / 'judges').rglob('*.yml') if (p / 'judges').exists() else []:
                                        cands.append(y)
                                except Exception:
                                    pass
                                for y in p.rglob('*.yml'):
                                    cands.append(y)
                        seen = set()
                        uniq: list[_P] = []
                        for c in cands:
                            s = str(c.resolve())
                            if s in seen:
                                continue
                            seen.add(s)
                            if c.exists():
                                uniq.append(c)
                        for y in uniq:
                            try:
                                doc = yaml.safe_load(y.read_text(encoding='utf-8')) or {}
                            except Exception:
                                continue
                            js = doc.get('judges')
                            if isinstance(js, dict) and key in js:
                                return y, js.get(key) or {}, None
                            if isinstance(js, list):
                                for it in js:
                                    nm = (it or {}).get('key') or (it or {}).get('name')
                                    if nm == key:
                                        return y, it or {}, None
                        return _P(''), {}, None
                    def _resolve_ref(base_file: _P, jcfg: dict) -> tuple[_P, dict]:
                        ref = jcfg.get('agent_ref') or jcfg.get('ref')
                        if isinstance(ref, str) and ref.strip():
                            rp = _P(ref)
                            if not rp.is_absolute():
                                rp = (base_file.parent / rp).resolve()
                            try:
                                sub = yaml.safe_load(rp.read_text(encoding='utf-8')) or {}
                            except Exception:
                                sub = {}
                            return rp, sub
                        return base_file, None  # type: ignore
                    def _apply_to_yaml(doc: dict, new_instr: str | None, new_tmpl: str | None) -> tuple[dict, bool]:
                        changed = False
                        jblock = doc.get('judges')
                        targets: list[dict] = []
                        if isinstance(jblock, dict) and jkey in jblock:
                            targets = [jblock[jkey]]
                        elif isinstance(jblock, list):
                            for it in jblock:
                                nm = (it or {}).get('key') or (it or {}).get('name')
                                if nm == jkey:
                                    targets = [it]
                                    break
                        for tgt in targets:
                            ag = tgt.get('agent') or {}
                            inp = tgt.get('input') or {}
                            if new_instr is not None:
                                if isinstance(ag.get('instructions_file'), str) and ag.get('instructions_file').strip():
                                    pass
                                else:
                                    ag['instructions'] = new_instr
                                    tgt['agent'] = ag
                                    changed = True
                            if new_tmpl is not None:
                                if isinstance((inp.get('template_file')), str) and inp.get('template_file').strip():
                                    pass
                                else:
                                    inp['template'] = new_tmpl
                                    tgt['input'] = inp
                                    changed = True
                        return doc, changed
                    def _write_yaml(fp: _P, data: dict):
                        class _LS(str):
                            pass
                        class _D(yaml.SafeDumper):
                            pass
                        def _rep(dumper, value):
                            return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|')
                        _D.add_representer(_LS, _rep)
                        def _lit(v):
                            if isinstance(v, str) and '\n' in v:
                                return _LS(v)
                            return v
                        blob = json.loads(json.dumps(data))
                        try:
                            j = blob.get('judges')
                            if isinstance(j, dict):
                                t = j.get(jkey) or {}
                                if isinstance(t, dict):
                                    if 'agent' in t and isinstance(t['agent'], dict) and 'instructions' in t['agent']:
                                        t['agent']['instructions'] = _lit(t['agent']['instructions'])
                                    if 'input' in t and isinstance(t['input'], dict) and 'template' in t['input']:
                                        t['input']['template'] = _lit(t['input']['template'])
                            elif isinstance(j, list):
                                for it in j:
                                    nm = (it or {}).get('key') or (it or {}).get('name')
                                    if nm == jkey:
                                        if 'agent' in it and isinstance(it['agent'], dict) and 'instructions' in it['agent']:
                                            it['agent']['instructions'] = _lit(it['agent']['instructions'])
                                        if 'input' in it and isinstance(it['input'], dict) and 'template' in it['input']:
                                            it['input']['template'] = _lit(it['input']['template'])
                        except Exception:
                            pass
                        fp.write_text(yaml.dump(blob, Dumper=_D, sort_keys=False, allow_unicode=True), encoding='utf-8')
                    new_instr = best.get('instructions') if isinstance(best.get('instructions'), str) else None
                    new_tmpl = best.get('template') if isinstance(best.get('template'), str) else None
                    src_file, jcfg_src, _ = _find_judge_source(_P(proj_path), jkey)
                    if not str(src_file):
                        pass
                    else:
                        ref_file, subdoc = _resolve_ref(src_file, jcfg_src)
                        if subdoc is not None and isinstance(subdoc, dict):
                            ag = subdoc.get('agent') or {}
                            inp = subdoc.get('input') or {}
                            if new_instr is not None and isinstance(ag.get('instructions_file'), str) and ag.get('instructions_file').strip():
                                fp = _P(ag.get('instructions_file'))
                                if not fp.is_absolute():
                                    fp = (ref_file.parent / fp).resolve()
                                if fp.exists():
                                    _backup(fp)
                                fp.write_text(new_instr, encoding='utf-8')
                                print(f"Applied judge instructions: {fp}")
                            elif new_instr is not None:
                                if ref_file.exists():
                                    _backup(ref_file)
                                ag['instructions'] = new_instr
                                subdoc['agent'] = ag
                                _write_yaml(ref_file, subdoc)
                                print(f"Applied judge instructions inline: {ref_file}")
                            if new_tmpl is not None and isinstance(inp.get('template_file'), str) and inp.get('template_file').strip():
                                fp = _P(inp.get('template_file'))
                                if not fp.is_absolute():
                                    fp = (ref_file.parent / fp).resolve()
                                if fp.exists():
                                    _backup(fp)
                                fp.write_text(new_tmpl, encoding='utf-8')
                                print(f"Applied judge template: {fp}")
                            elif new_tmpl is not None:
                                if ref_file.exists():
                                    _backup(ref_file)
                                inp['template'] = new_tmpl
                                subdoc['input'] = inp
                                _write_yaml(ref_file, subdoc)
                                print(f"Applied judge template inline: {ref_file}")
                        else:
                            try:
                                doc = yaml.safe_load(src_file.read_text(encoding='utf-8')) or {}
                            except Exception:
                                doc = {}
                            to_write = False
                            if new_instr is not None or new_tmpl is not None:
                                jblock = doc.get('judges')
                                tgt = None
                                if isinstance(jblock, dict) and jkey in jblock:
                                    tgt = jblock[jkey]
                                elif isinstance(jblock, list):
                                    for it in jblock:
                                        nm = (it or {}).get('key') or (it or {}).get('name')
                                        if nm == jkey:
                                            tgt = it
                                            break
                                if isinstance(tgt, dict):
                                    ag = tgt.get('agent') or {}
                                    inp = tgt.get('input') or {}
                                    if new_instr is not None:
                                        if isinstance(ag.get('instructions_file'), str) and ag.get('instructions_file').strip():
                                            fp = _P(ag.get('instructions_file'))
                                            if not fp.is_absolute():
                                                fp = (src_file.parent / fp).resolve()
                                            if fp.exists():
                                                _backup(fp)
                                            fp.write_text(new_instr, encoding='utf-8')
                                            print(f"Applied judge instructions: {fp}")
                                        else:
                                            tgt.setdefault('agent', {})
                                            tgt['agent']['instructions'] = new_instr
                                            to_write = True
                                    if new_tmpl is not None:
                                        if isinstance(inp.get('template_file'), str) and inp.get('template_file').strip():
                                            fp = _P(inp.get('template_file'))
                                            if not fp.is_absolute():
                                                fp = (src_file.parent / fp).resolve()
                                            if fp.exists():
                                                _backup(fp)
                                            fp.write_text(new_tmpl, encoding='utf-8')
                                            print(f"Applied judge template: {fp}")
                                        else:
                                            tgt.setdefault('input', {})
                                            tgt['input']['template'] = new_tmpl
                                            to_write = True
                                else:
                                    mod, changed = _apply_to_yaml(doc, new_instr, new_tmpl)
                                    if changed:
                                        to_write = True
                                        doc = mod
                            if to_write:
                                _backup(src_file)
                                _write_yaml(src_file, doc)
                                print(f"Applied judge changes: {src_file}")
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error optimizing judge: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "agent" and getattr(args, 'agent_cmd', None) == "optimize":
            try:
                try:
                    import gepa  # type: ignore
                except Exception:
                    print("Error: This command requires the 'gepa' package. Install with: pip install gepa[full]")
                    sys.exit(1)
                from omniagents.core.eval.gepa_agent_adapter import OmniAgentsAgentAdapter
                from omniagents.core.project.compiler import compile_project
                proj_path = getattr(args, 'project', None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(str(proj_path), select=None, exclude=None, agent_key=(str(getattr(args, 'agent', '') or '') or None))
                from pathlib import Path as _P
                base = _P(proj_path).resolve()
                if base.is_file():
                    base = base.parent
                category = str(getattr(args, 'category') or '').strip()
                def _resolve_split(which, explicit):
                    if explicit:
                        p = _P(explicit)
                        return p if p.is_absolute() else (base / p).resolve()
                    if not category:
                        return None
                    eval_dir = (base / "artifacts" / "eval")
                    candidate = (eval_dir / "latest" / "splits" / category / f"{which}.yml")
                    if candidate.exists():
                        return candidate.resolve()
                    picked = None
                    try:
                        ts_dirs = []
                        if eval_dir.exists():
                            for d in eval_dir.iterdir():
                                if d.is_dir() and re.fullmatch(r"\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}", d.name):
                                    ts_dirs.append(d)
                        ts_dirs.sort(key=lambda p: p.name, reverse=True)
                        for d in ts_dirs:
                            c = d / "splits" / category / f"{which}.yml"
                            if c.exists():
                                picked = c.resolve(); break
                    except Exception:
                        picked = None
                    return picked
                tr_file = getattr(args, 'train_file', None)
                dv_file = getattr(args, 'dev_file', None)
                ts_file = getattr(args, 'test_file', None)
                if not category and not (tr_file and dv_file and ts_file):
                    print("Error: Provide --category or all of --train-file/--dev-file/--test-file.")
                    sys.exit(2)
                tr_path = _resolve_split("train", tr_file) if (category or not tr_file) else (_P(tr_file) if _P(tr_file).is_absolute() else (base / tr_file).resolve())
                dv_path = _resolve_split("dev", dv_file) if (category or not dv_file) else (_P(dv_file) if _P(dv_file).is_absolute() else (base / dv_file).resolve())
                ts_path = _resolve_split("test", ts_file) if (category or not ts_file) else (_P(ts_file) if _P(ts_file).is_absolute() else (base / ts_file).resolve())
                def _read_scenarios(p: _P) -> List[Dict[str, Any]]:
                    y = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
                    items = [x for x in (y.get('scenarios') or []) if isinstance(x, dict)]
                    return items
                train_raw = _read_scenarios(tr_path) if tr_path else []
                dev_raw = _read_scenarios(dv_path) if dv_path else []
                test_raw = _read_scenarios(ts_path) if ts_path else []
                # Determine category if not provided from split metadata
                if not category:
                    try:
                        for it in (dev_raw or train_raw or test_raw):
                            c = it.get('target_category')
                            if c:
                                category = str(c)
                                break
                    except Exception:
                        pass
                if not category:
                    print("Error: Could not determine failure category from splits; please provide --category.")
                    sys.exit(2)
                # Filter positives-only if requested
                def _maybe_filter(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                    if bool(getattr(args, 'positives_only', True)):
                        return [x for x in items if bool(x.get('label')) is True]
                    return items
                trainset = _maybe_filter(train_raw)
                devset = _maybe_filter(dev_raw)
                testset = _maybe_filter(test_raw)
                try:
                    thr = 30
                    def _warn_split(_name, _ds):
                        p_cnt = sum(1 for x in (_ds or []) if bool(x.get('label')) is True)
                        if p_cnt < thr:
                            print(f"Warning: split '{_name}' has only {p_cnt} positive cases (<{thr}).")
                    _warn_split('train', trainset)
                    _warn_split('dev', devset)
                    _warn_split('test', testset)
                except Exception:
                    pass
                # Seed instructions from compiled config
                cfg = yaml.safe_load(_P(compiled_cfg_path).read_text(encoding='utf-8')) or {}
                def _read_text_file(base_dir: _P, rel: str) -> str:
                    p = _P(rel)
                    if not p.is_absolute():
                        p = (base_dir / rel).resolve()
                    try:
                        return p.read_text(encoding='utf-8')
                    except Exception:
                        return ''
                seed_instructions = cfg.get('instructions') or ''
                if (not seed_instructions) and cfg.get('instructions_file'):
                    seed_instructions = _read_text_file(_P(compiled_cfg_path).resolve().parent, str(cfg.get('instructions_file')))
                if not seed_instructions:
                    seed_instructions = "You are a helpful assistant."
                seed_candidate = {"instructions": seed_instructions}
                # Build adapter and run GEPA
                adapter = OmniAgentsAgentAdapter(_P(compiled_cfg_path), category, agent_max_turns=(int(getattr(args,'max_turns')) if getattr(args,'max_turns', None) is not None else None), score_mode=str(getattr(args,'score_mode','fraction') or 'fraction'))
                out_dir = getattr(args, 'output', '')
                if out_dir:
                    outp = _P(out_dir)
                    run_dir = outp if outp.is_absolute() else (base / outp).resolve()
                else:
                    # Co-locate under the dev split timestamp dir
                    run_dir = dv_path.parents[2] / "agent_optimize" / (category or "category")
                run_dir.mkdir(parents=True, exist_ok=True)
                result = gepa.optimize(
                    seed_candidate=seed_candidate,
                    trainset=trainset,
                    valset=devset,
                    adapter=adapter,
                    max_metric_calls=int(getattr(args,'max_metric_calls') or 100),
                    reflection_lm=str(getattr(args,'reflection_model') or 'gpt-4.1'),
                    run_dir=str(run_dir),
                    seed=int(getattr(args,'seed') or 0),
                    display_progress_bar=True,
                )
                best = result.best_candidate
                def _acc(ds, cand):
                    ev = adapter.evaluate(ds, cand, capture_traces=False)
                    return (sum(ev.scores)/len(ev.scores)) if ev.scores else 0.0
                dev_acc = _acc(devset, best)
                test_acc = _acc(testset, best)
                out_cfg = {
                    "category": category,
                    "optimized": {
                        "instructions": best.get('instructions'),
                    },
                    "metrics": {
                        "dev_score": dev_acc,
                        "test_score": test_acc,
                        "score_mode": str(getattr(args, 'score_mode', 'fraction') or 'fraction'),
                        "positives_only": bool(getattr(args, 'positives_only', True)),
                    },
                }
                out_json = run_dir / "result.json"
                out_json.write_text(json.dumps(out_cfg, indent=2), encoding='utf-8')
                print(f"Optimized agent instructions for category '{category}'. Dev={dev_acc:.3f} Test={test_acc:.3f}")
                print(f"Wrote: {out_json}")
                class _LiteralStr(str):
                    pass
                class _LiteralDumper(yaml.SafeDumper):
                    pass
                def _lit_representer(dumper, data):
                    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')
                _LiteralDumper.add_representer(_LiteralStr, _lit_representer)
                instr_val = best.get('instructions') if isinstance(best.get('instructions'), str) else ''
                instr_node = _LiteralStr(instr_val) if ('\n' in instr_val) else instr_val
                override = {"agent": {"instructions": instr_node}}
                out_yml = run_dir / "agent_override.yml"
                out_yml.write_text(yaml.dump(override, Dumper=_LiteralDumper, sort_keys=False, allow_unicode=True), encoding='utf-8')
                print(f"Agent override written: {out_yml}")

                if bool(getattr(args, 'apply', False)):
                    from omniagents.core.project.runtime import load_project, resolve_agent
                    from pathlib import Path as _P
                    from datetime import datetime
                    proj_path = getattr(args, 'project', None)
                    if not proj_path:
                        p = _P.cwd() / 'project.yml'
                        if p.is_file():
                            proj_path = str(p)
                    try:
                        prj = load_project(str(proj_path))
                        _akey = str(getattr(args, 'agent', '') or '') or None
                        key, agent_yaml_path = resolve_agent(prj, agent_key=_akey)
                        ay = _P(agent_yaml_path)
                        doc = yaml.safe_load(ay.read_text(encoding='utf-8')) or {}
                        new_instr = best.get('instructions') if isinstance(best.get('instructions'), str) else None
                        if new_instr is not None:
                            instr_file = doc.get('instructions_file') if isinstance(doc.get('instructions_file'), str) else None
                            if instr_file and str(instr_file).strip():
                                fp = _P(instr_file)
                                if not fp.is_absolute():
                                    fp = (ay.parent / fp).resolve()
                                ts = datetime.now().strftime('%Y%m%d%H%M%S')
                                if fp.exists():
                                    bak = fp.with_suffix(fp.suffix + f'.bak.{ts}')
                                    bak.write_text(fp.read_text(encoding='utf-8'), encoding='utf-8')
                                fp.write_text(new_instr, encoding='utf-8')
                                print(f"Applied agent instructions: {fp}")
                            else:
                                ts = datetime.now().strftime('%Y%m%d%H%M%S')
                                bak = ay.with_suffix(ay.suffix + f'.bak.{ts}')
                                bak.write_text(ay.read_text(encoding='utf-8'), encoding='utf-8')
                                class _LS(str):
                                    pass
                                class _D(yaml.SafeDumper):
                                    pass
                                def _rep(dumper, value):
                                    return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|')
                                _D.add_representer(_LS, _rep)
                                def _lit(v):
                                    return _LS(v) if isinstance(v, str) and '\n' in v else v
                                doc['instructions'] = new_instr
                                blob = json.loads(json.dumps(doc))
                                if 'instructions' in blob:
                                    blob['instructions'] = _lit(blob['instructions'])
                                ay.write_text(yaml.dump(blob, Dumper=_D, sort_keys=False, allow_unicode=True), encoding='utf-8')
                                print(f"Applied agent instructions inline: {ay}")
                    except Exception as _e:
                        pass
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error optimizing agent instructions: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "judge":
            print("Error: Provide a judge subcommand: run | optimize")
            sys.exit(2)
        else:
            eval_parser.print_help()
    elif args.command == "generate":
        try:
            from omniagents.core.project.scaffold import (
                scaffold_tool,
                scaffold_agent,
                scaffold_eval_quickstart,
                scaffold_guardrail,
                scaffold_server_function,
                scaffold_judge,
            )
        except Exception as e:
            print(f"Error: generators not available: {e}")
            sys.exit(1)
        if args.gen_cmd == "tool":
            try:
                created, root = scaffold_tool(project_ref=str(getattr(args, 'project', '.') or '.'), tool_name=str(args.name), make_async=bool(getattr(args, 'is_async', False)), force=bool(getattr(args, 'force', False)))
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print("Next: add the tool name to your agent's 'tools' list and run: omniagents run -P project.yml")
            except Exception as e:
                print(f"Error generating tool: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "agent":
            try:
                created, root, sanitized = scaffold_agent(project_ref=str(getattr(args, 'project', '.') or '.'), agent_name=str(args.name), model=str(getattr(args, 'model', 'gpt-4.1')), force=bool(getattr(args, 'force', False)))
                print(f"Project: {root}")
                print(f"Created agent '{sanitized}'")
                for p in created:
                    print(f"  {p}")
                print("Next: run with --agent or set agents.entrypoint in project.yml")
            except Exception as e:
                print(f"Error generating agent: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "eval" and args.eval_cmd == "quickstart":
            try:
                created, root = scaffold_eval_quickstart(project_ref=str(getattr(args, 'project', '.') or '.'), force=bool(getattr(args, 'force', False)))
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print("Next: omniagents eval run -P project.yml --reporter pretty")
            except Exception as e:
                print(f"Error generating evaluations: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "guardrail":
            try:
                created, root = scaffold_guardrail(
                    project_ref=str(getattr(args, 'project', '.') or '.'),
                    kind=str(args.type),
                    name=str(args.name),
                    make_async=bool(getattr(args, 'is_async', False)),
                    force=bool(getattr(args, 'force', False)),
                )
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print("Next: reference this guardrail name in your agent YAML under input_guardrails or output_guardrails.")
            except Exception as e:
                print(f"Error generating guardrail: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "server-function":
            try:
                created, root = scaffold_server_function(
                    project_ref=str(getattr(args, 'project', '.') or '.'),
                    name=str(args.name),
                    make_async=bool(getattr(args, 'is_async', False)),
                    force=bool(getattr(args, 'force', False)),
                )
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print("Next: list this function in your agent's server_functions or set to '*'.")
            except Exception as e:
                print(f"Error generating server function: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "judge":
            try:
                created, root = scaffold_judge(
                    project_ref=str(getattr(args, 'project', '.') or '.'),
                    key=str(args.name),
                    model=str(getattr(args, 'model', 'gpt-4.1')),
                    source=str(getattr(args, 'source', 'final_assistant_text')),
                    add_measure=not bool(getattr(args, 'no_measure', False)),
                    failure_category=(str(getattr(args, 'failure_category')) if getattr(args, 'failure_category', None) else None),
                    force=bool(getattr(args, 'force', False)),
                )
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print("Next: omniagents eval run -P project.yml --reporter pretty")
            except Exception as e:
                print(f"Error generating judge: {e}")
                import traceback; traceback.print_exc()
                sys.exit(1)
        else:
            gen_parser.print_help()
    elif args.command == "ls":
        try:
            from omniagents.core.project.compiler import compile_project
            proj_path = getattr(args, 'project', None)
            if not proj_path:
                p = Path.cwd() / "project.yml"
                if p.is_file():
                    proj_path = str(p)
                else:
                    print("Error: Provide --project <project.yml> (or run from a directory containing a project.yml).")
                    sys.exit(2)
            compiled_cfg_path, graph = compile_project(str(proj_path), select=None, exclude=None, agent_key=(str(getattr(args, 'agent', '') or '') or None))
            print(json.dumps(graph, indent=2))
        except Exception as e:
            print(f"Error listing resources: {e}")
            import traceback; traceback.print_exc()
            sys.exit(1)
    else:
        parser.print_help()

def save_agent_as_py(config_path: str, output_path: str, project_tool_dirs: list[Path] | None = None):
    """Generate a standalone Python script by inlining all tools and instructions."""
    import yaml
    from pathlib import Path

    # Load the AgentSpec so we can inline spec values
    spec = load_agent_spec_from_yaml(config_path)
    # Capture the list of tool functions for embedding and spec construction
    tool_funcs = spec.available_tools

    cfg_path = Path(config_path)
    raw_cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))

    # Resolve instructions text
    instructions = ''
    if raw_cfg.get('instructions'):
        instructions = raw_cfg['instructions']
    elif raw_cfg.get('instructions_file'):
        instr_file = cfg_path.parent / raw_cfg['instructions_file']
        if not instr_file.is_file():
            alt = Path(raw_cfg['instructions_file'])
            if alt.is_file():
                instr_file = alt
        if instr_file.is_file():
            instructions = instr_file.read_text(encoding='utf-8')
    instructions = instructions.replace("'''", "\\'\\'\\'")

    # Gather tool directories from config
    tool_dirs = []
    base = cfg_path.parent
    default_tools = base / 'tools'
    if default_tools.is_dir():
        tool_dirs.append(default_tools)
    # Include project-level tool dirs when provided
    for td in (project_tool_dirs or []):
        try:
            p = Path(td)
        except Exception:
            p = None
        if p and p.is_dir():
            tool_dirs.append(p)
    for td in raw_cfg.get('additional_tools_dirs', []):
        p = (base / td).resolve()
        if p.is_dir():
            tool_dirs.append(p)

    # Collect all .py files from those directories
    tool_files = []
    for d in tool_dirs:
        for py in d.rglob('*.py'):
            if py.name != '__init__.py':
                tool_files.append(py)

    # Begin assembling the generated script
    lines: list[str] = []
    lines.append('# Autogenerated standalone agent script\n')

    # --- Embed tool modules ---
    for tf in tool_files:
        rel = tf.relative_to(Path.cwd())
        lines.append(f"# --- From {rel} ---")
        lines.append(tf.read_text(encoding='utf-8'))
        lines.append("\n")

    # --- Embed agent instructions ---
    lines.append("def get_agent_instructions():")
    lines.append("    return '''")
    lines.append(instructions)
    lines.append("'''\n")

    # Imports for building AgentSpec
    lines.append("from omniagents.core.runtime.runner import Runner")
    lines.append("from omniagents.core.agents.specs import AgentSpec, SafeAgentOptions, SettingField\n")

    # Construct the AgentSpec inline
    lines.append("spec = AgentSpec(")
    lines.append(f"    name={spec.name!r},")
    try:
        _lbl = getattr(spec, 'label', None)
    except Exception:
        _lbl = None
    try:
        _desc = getattr(spec, 'description', None)
    except Exception:
        _desc = None
    lines.append(f"    label={_lbl!r},")
    lines.append(f"    description={_desc!r},")
    lines.append(f"    header_title={spec.header_title!r},")
    lines.append(f"    page_title={spec.page_title!r},")
    lines.append(f"    welcome_text={spec.welcome_text!r},")
    lines.append(f"    client_storage_prefix={spec.client_storage_prefix!r},")
    lines.append("    get_agent_instructions=get_agent_instructions,")
    lines.append("    build_agent=None,  # use default internal builder\n")

    # List available tools
    tool_names = [t.name for t in tool_funcs]
    if not tool_names:
        try:
            raw_tools = raw_cfg.get('tools') or []
            if isinstance(raw_tools, list):
                tool_names = [str(x) for x in raw_tools]
        except Exception:
            pass
    lines.append("    available_tools=[%s]," % ", ".join(tool_names))

    lines.append(f"    model_name={spec.model_name!r},")
    lines.append(f"    use_safe_agent={spec.use_safe_agent},")
    so = spec.safe_agent_options
    lines.append(f"    safe_agent_options=SafeAgentOptions(skip_approvals={so.skip_approvals}, halt_on_rejection={so.halt_on_rejection}, safe_tool_names={so.safe_tool_names!r}, safe_tool_patterns={so.safe_tool_patterns!r}),")
    lines.append(f"    uses_mcp={spec.uses_mcp},")
    lines.append(f"    mcp_url_setting_key={spec.mcp_url_setting_key!r},")
    lines.append(f"    mcp_token_setting_key={spec.mcp_token_setting_key!r},")
    lines.append(f"    show_mcp_status_indicator={spec.show_mcp_status_indicator},")

    # Embed icon_map
    lines.append("    icon_map={")
    for k, v in raw_cfg.get('icon_map', {}).items():
        lines.append(f"        {k!r}: {v},")
    lines.append("    },")

    # Embed settings_fields
    lines.append("    settings_fields=[")
    for field in spec.settings_fields:
        lines.append(f"        SettingField(key={field.key!r}, label={field.label!r}, group={field.group!r}, default={field.default!r}, secret={field.secret}, hint={field.hint!r}),")
    lines.append("    ],")

    lines.append(f"    page_title_suffix={spec.page_title_suffix!r},")

    # Theme color
    if 'theme_color' in raw_cfg:
        lines.append(f"    theme_color={raw_cfg['theme_color']},")
    else:
        lines.append(f"    theme_color={spec.theme_color!r},")

    lines.append(")\n")

    # Run the agent
    lines.append("if __name__ == '__main__':")
    lines.append("    runner = Runner(spec=spec)")
    lines.append("    runner.run(mode='gui')\n")

    # Write out the file
    Path(output_path).write_text("\n".join(lines), encoding='utf-8')

if __name__ == "__main__":
    main() 
