import sys
import argparse
import json
import os
from pathlib import Path
import yaml
import re
from dotenv import load_dotenv
from typing import Any, Dict, List, Optional, Tuple

# --- Core Framework Imports --- #
# Note: All OpenAI client configuration (API key, base URL, tracing) should be done by the user script or environment.
try:
    from omniagents._version import __version__
except ImportError:
    __version__ = "0.0.0"
# Imports deferred to prevent early initialization of agents library


CODESPACES_ENV = any(
    os.environ.get(key)
    for key in (
        "CODESPACES",
        "CODESPACE_NAME",
        "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
    )
)
DEFAULT_STUDIO_DEV_HOST = "0.0.0.0" if CODESPACES_ENV else "127.0.0.1"


def _merge_prompt_words(words: Optional[List[str]] | None) -> Optional[str]:
    if not words:
        return None
    text = " ".join(words).strip()
    return text or None


def _resolve_studio_project_value(
    value: Optional[str],
) -> Tuple[Optional[str], Optional[Path], bool]:
    manifest_path: Optional[Path] = None
    project_value = value
    derived = False

    def load_from_manifest(manifest: Path) -> Optional[str]:
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        tracing_cfg = data.get("tracing") or {}
        tracing_project = tracing_cfg.get("project")
        if isinstance(tracing_project, str) and tracing_project.strip():
            return tracing_project.strip()
        fallback_name = data.get("name")
        if isinstance(fallback_name, str) and fallback_name.strip():
            return fallback_name.strip()
        return None

    if value:
        lower_val = value.lower()
        looks_like_path = any(
            sep in value for sep in ("/", "\\")
        ) or lower_val.endswith((".yml", ".yaml"))
        if looks_like_path:
            candidate = Path(value).expanduser()
            if candidate.is_dir():
                candidate = candidate / "project.yml"
            if candidate.is_file():
                guess = load_from_manifest(candidate)
                if guess:
                    manifest_path = candidate
                    project_value = guess
                    derived = True
                    return project_value, manifest_path, derived
        return project_value, manifest_path, derived

    candidate = Path.cwd() / "project.yml"
    if candidate.is_file():
        guess = load_from_manifest(candidate)
        if guess:
            manifest_path = candidate
            project_value = guess
            derived = True
    return project_value, manifest_path, derived


def main():
    """Entry point for the omniagents CLI."""
    # Parse args first to get config path if available
    import sys

    project_path = None
    for i, arg in enumerate(sys.argv):
        if arg in ("--project", "-P") and i + 1 < len(sys.argv):
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
            env_file = current / ".env"
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
        # Search for .env starting from CWD  and going upward
        try:
            current = Path.cwd()
            env_file = None
            while True:
                candidate = current / ".env"
                if candidate.exists():
                    env_file = candidate
                    break
                if current == current.parent:
                    break
                current = current.parent
            
            if env_file:
                try:
                    if load_dotenv(env_file, override=False):
                        print(f"Loaded .env file from {env_file}")
                        env_loaded = True
                except Exception as e:
                    print(f"Warning: Could not load .env file {env_file}: {e}")
        except Exception as e:
            print(f"Warning: Error searching for .env file: {e}")

    # --- Command Line Arguments ---
    parser = argparse.ArgumentParser(description="OmniAgents CLI")
    parser.add_argument(
        "--version", "-v", action="version", version=f"omniagents {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # New project scaffold
    new_parser = subparsers.add_parser("new", help="Scaffold a new OmniAgents project")
    new_parser.add_argument("name", help="Project and default agent name (snake_case)")
    new_parser.add_argument(
        "--dir",
        dest="directory",
        default=".",
        help="Parent directory to create the project in (default: .)",
    )
    new_parser.add_argument(
        "--model",
        default="gpt-4.1",
        help="Default model for the agent (default: gpt-4.1)",
    )
    new_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing files when necessary"
    )
    new_parser.add_argument(
        "--no-samples",
        dest="no_samples",
        action="store_true",
        help="Do not create or wire sample tools",
    )

    # Run command
    run_parser = subparsers.add_parser("run", help="Run an agent")
    run_parser.add_argument(
        "--mode",
        "-m",
        choices=["server", "ink", "web"],
        default="web",
        help="Run mode: 'web' (default) for React web UI, 'ink' for React Ink TUI, 'server' for JSON-RPC server",
    )
    run_parser.add_argument(
        "--config", "-c", type=str, help="Path to a single agent YAML config file."
    )
    run_parser.add_argument(
        "--project", "-P", type=str, help="Path to project.yml (dbt-style)."
    )
    run_parser.add_argument(
        "--agent",
        "-a",
        type=str,
        default=None,
        help="Agent key to run from project.yml (defaults to entrypoint).",
    )
    run_parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug mode (overrides YAML setting).",
    )
    run_parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Resume a previous CLI session using this identifier.",
    )
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help="Show TUI session picker (Bubble Tea mode only).",
    )
    # Unified SafeAgent controls (strict)
    run_parser.add_argument(
        "--approvals",
        choices=["require", "auto", "skip"],
        default=None,
        help="Approval policy: require (manual), auto (auto-approve), skip (disable SafeAgent).",
    )
    run_parser.add_argument(
        "--on-reject",
        dest="on_reject",
        choices=["halt", "continue"],
        default=None,
        help="Behavior when a tool call is rejected: halt or continue.",
    )
    # Server-specific arguments
    run_parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind (server mode only, default 0.0.0.0)",
    )
    run_parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=8000,
        help="Port to listen on (server mode only, default 8000)",
    )
    run_parser.add_argument(
        "--auth-token",
        dest="auth_token",
        default=None,
        help="Bearer token to require from clients (server mode only)",
    )
    run_parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload server when code changes (server mode only)",
    )
    # Web dev options
    run_parser.add_argument(
        "--dev",
        action="store_true",
        help="Run web UI in dev mode (Vite) with hot reload (web mode)",
    )
    run_parser.add_argument(
        "--dev-host",
        default=None,
        help="Host for the web dev server (default: match server host)",
    )
    run_parser.add_argument(
        "--dev-port",
        type=int,
        default=5173,
        help="Port for the web dev server (default: 5173)",
    )
    run_parser.add_argument(
        "--ui-minimal",
        action="store_true",
        help="Hide the sidebar when launching the web UI (adds minimal=true param)",
    )
    run_parser.add_argument(
        "prompt",
        nargs="*",
        help="Optional message to send immediately after startup.",
    )

    # Save command
    save_parser = subparsers.add_parser(
        "save", help="Save an agent spec as a standalone Python module"
    )
    save_parser.add_argument(
        "--project",
        "-P",
        type=str,
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    save_parser.add_argument(
        "--agent",
        "-a",
        type=str,
        default=None,
        help="Agent key from project.yml (defaults to entrypoint)",
    )
    save_parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="generated_agent.py",
        help="Output Python file path.",
    )

    compile_parser = subparsers.add_parser(
        "compile", help="Compile a project.yml into target/evaluation_compiled.yml"
    )
    compile_parser.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    compile_parser.add_argument(
        "--agent",
        "-a",
        default="",
        help="Agent key to compile (defaults to entrypoint)",
    )
    compile_parser.add_argument(
        "--select", default="", help="Selector to include scenarios"
    )
    compile_parser.add_argument(
        "--exclude", default="", help="Selector to exclude scenarios"
    )

    eval_parser = subparsers.add_parser("eval", help="Evaluation utilities")
    eval_sub = eval_parser.add_subparsers(dest="eval_cmd")

    suite_parser = eval_sub.add_parser("suite", help="Evaluation suite utilities")
    suite_sub = suite_parser.add_subparsers(dest="suite_cmd")
    suite_run = suite_sub.add_parser("run", help="Run evaluation across scenarios")
    suite_run.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    suite_run.add_argument(
        "--agent", "-a", default="", help="Agent key (defaults to entrypoint)"
    )
    suite_run.add_argument("--output", "-o", default="", help="Write JSON to this path")
    suite_run.add_argument(
        "--reporter", choices=["auto", "pretty", "tap"], default="auto"
    )
    suite_run.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto"
    )
    suite_run.add_argument("--select", default="")
    suite_run.add_argument("--exclude", default="")
    suite_run.add_argument("-q", "--quiet", action="count", default=0)
    suite_run.add_argument("-v", "--verbose", action="count", default=0)
    suite_run.add_argument(
        "--include-unmeasured",
        action="store_true",
        help="Include scenarios without measures/failure_categories (default: skip them)",
    )
    suite_run.add_argument(
        "--name",
        default=None,
        help="Optional scenario set name to include in tracing workflow base name",
    )

    eval_scen = eval_sub.add_parser("scenarios", help="Scenario utilities")
    scen_sub = eval_scen.add_subparsers(dest="scen_cmd")
    scen_gen = scen_sub.add_parser("generate", help="Generate synthetic scenarios")
    scen_gen.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    scen_gen.add_argument(
        "--agent", "-a", default="", help="Agent key (defaults to entrypoint)"
    )
    scen_gen.add_argument("--select", default="")
    scen_gen.add_argument("--exclude", default="")
    scen_gen.add_argument(
        "--name",
        default=None,
        help="Scenario set name written under artifacts/eval/<name>/ (required unless --output is provided)",
    )
    scen_gen.add_argument(
        "--output",
        "-o",
        default="",
        help="Explicit output path for generated_scenarios.yml (overrides --name)",
    )
    scen_gen.add_argument("--count", type=int, default=None)
    scen_gen.add_argument("--seed", type=int, default=None)
    scen_gen.add_argument("-q", "--quiet", action="count", default=0)
    scen_gen.add_argument("-v", "--verbose", action="count", default=0)
    scen_gen.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto"
    )
    scen_run = scen_sub.add_parser(
        "run", help="Run scenarios from a YAML file to record fixtures"
    )
    scen_run.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    scen_run.add_argument(
        "--agent", "-a", default="", help="Agent key (defaults to entrypoint)"
    )
    scen_run.add_argument(
        "--name",
        default=None,
        help="Scenario set name under artifacts/eval/<name>/ (alternative to --input)",
    )
    scen_run.add_argument(
        "--input", "-i", default="", help="Path to scenarios YAML to run"
    )
    scen_run.add_argument(
        "--output",
        "-o",
        default="",
        help="Write generated_runs.json to this path (default alongside scenarios file)",
    )
    scen_run.add_argument("--run-max-turns", type=int, default=None)
    scen_run.add_argument("--select", default="")
    scen_run.add_argument("--exclude", default="")
    ih_group = scen_run.add_mutually_exclusive_group()
    ih_group.add_argument(
        "--include-history",
        dest="include_history",
        action="store_true",
        help="Include full history in runs.json (default)",
    )
    ih_group.add_argument(
        "--no-include-history",
        dest="include_history",
        action="store_false",
        help="Do not include history in runs.json",
    )
    scen_run.set_defaults(include_history=True)
    scen_run.add_argument("-q", "--quiet", action="count", default=0)
    scen_run.add_argument("-v", "--verbose", action="count", default=0)
    scen_run.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto"
    )
    scen_ann = scen_sub.add_parser(
        "annotate", help="Annotate scenarios with Studio categories"
    )
    scen_ann.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    scen_ann.add_argument(
        "--agent", "-a", default="", help="Agent key (defaults to entrypoint)"
    )
    scen_ann.add_argument(
        "--name",
        default=None,
        help="Scenario set name under artifacts/eval/<name>/ (alternative to --input)",
    )
    scen_ann.add_argument("--input", "-i", default="")
    scen_ann.add_argument("--output", "-o", default=None)
    scen_ann.add_argument(
        "--studio-project", "--agentio-project", dest="studio_project", default=None
    )
    scen_ann.add_argument("--select", default="")
    scen_ann.add_argument("--exclude", default="")
    scen_ann.add_argument("-q", "--quiet", action="count", default=0)
    scen_ann.add_argument("-v", "--verbose", action="count", default=0)

    scen_split = scen_sub.add_parser(
        "split", help="Create train/dev/test splits from annotated scenarios"
    )
    scen_split.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    scen_split.add_argument(
        "--name",
        default=None,
        help="Scenario set name under artifacts/eval/<name>/ (alternative to --input)",
    )
    scen_split.add_argument("--input", "-i", required=False, default="")
    scen_split.add_argument("--category", "-c", required=True)
    scen_split.add_argument("--output-dir", "-o", default="")
    scen_split.add_argument("--train", type=float, default=0.15)
    scen_split.add_argument("--dev", type=float, default=0.425)
    scen_split.add_argument("--test", type=float, default=0.425)
    scen_split.add_argument("--seed", type=int, default=42)
    scen_split.add_argument("--negatives-ratio", type=float, default=None)
    scen_split.add_argument("-q", "--quiet", action="count", default=0)
    scen_split.add_argument("-v", "--verbose", action="count", default=0)

    ls_parser = subparsers.add_parser(
        "ls", help="List compiled resources from target/graph.json"
    )
    ls_parser.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    ls_parser.add_argument(
        "--agent", "-a", default="", help="Agent key (defaults to entrypoint)"
    )

    gen_parser = subparsers.add_parser("generate", help="Generators for project assets")
    gen_sub = gen_parser.add_subparsers(dest="gen_cmd")

    gen_tool = gen_sub.add_parser("tool", help="Generate a tool file in tools/")
    gen_tool.add_argument("name", help="Tool function name (snake_case)")
    gen_tool.add_argument(
        "--project",
        "-P",
        required=False,
        default=".",
        help="Path to project.yml or its directory",
    )
    gen_tool.add_argument(
        "--async", dest="is_async", action="store_true", help="Generate an async tool"
    )
    gen_tool.add_argument(
        "--force", action="store_true", help="Overwrite existing files if necessary"
    )

    gen_agent = gen_sub.add_parser(
        "agent", help="Generate a new agent config and instructions"
    )
    gen_agent.add_argument("name", help="Agent name (snake_case)")
    gen_agent.add_argument("--project", "-P", required=False, default=".")
    gen_agent.add_argument("--model", default="gpt-4.1")
    gen_agent.add_argument("--force", action="store_true")

    gen_guard = gen_sub.add_parser(
        "guardrail", help="Generate guardrails in guardrails/"
    )
    gen_guard.add_argument("type", choices=["input", "output"], help="Guardrail type")
    gen_guard.add_argument("name", help="Guardrail name (snake_case)")
    gen_guard.add_argument("--project", "-P", required=False, default=".")
    gen_guard.add_argument("--async", dest="is_async", action="store_true")
    gen_guard.add_argument("--force", action="store_true")

    gen_srv = gen_sub.add_parser(
        "server-function", help="Generate a server function in server_functions/"
    )
    gen_srv.add_argument("name", help="Function name (snake_case)")
    gen_srv.add_argument("--project", "-P", required=False, default=".")
    gen_srv.add_argument("--async", dest="is_async", action="store_true")
    gen_srv.add_argument("--force", action="store_true")

    gen_judge = gen_sub.add_parser("judge", help="Generate an LLM-as-Judge scaffold")
    gen_judge.add_argument(
        "name", help="Judge key (snake_case, e.g., out_of_scope_judge)"
    )
    gen_judge.add_argument("--project", "-P", required=False, default=".")
    gen_judge.add_argument("--model", default="gpt-4.1")
    gen_judge.add_argument(
        "--source",
        choices=["final_assistant_text", "history"],
        default="final_assistant_text",
    )
    gen_judge.add_argument("--failure-category", dest="failure_category", default=None)
    gen_judge.add_argument("--no-measure", dest="no_measure", action="store_true")
    gen_judge.add_argument("--force", action="store_true")

    gen_optimizer = gen_sub.add_parser(
        "optimizer", help="Generate an optimizer agent scaffold"
    )
    gen_optimizer.add_argument(
        "name", help="Optimizer key (snake_case, e.g., rewrite_strict)"
    )
    gen_optimizer.add_argument("--project", "-P", required=False, default=".")
    gen_optimizer.add_argument("--model", default="gpt-4.1")
    gen_optimizer.add_argument(
        "--type", dest="opt_type", choices=["agent", "judge"], default="agent"
    )
    gen_optimizer.add_argument("--force", action="store_true")

    studio_parser = subparsers.add_parser("studio", help="OmniAgents Studio utilities")
    studio_sub = studio_parser.add_subparsers(dest="studio_cmd")

    studio_serve = studio_sub.add_parser(
        "serve", help="Run the OmniAgents Studio web UI"
    )
    studio_serve.add_argument(
        "--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)"
    )
    studio_serve.add_argument(
        "--port", type=int, default=7860, help="Port to bind (default: 7860)"
    )
    studio_serve.add_argument(
        "--db-path",
        dest="db_path",
        default=None,
        help="Path to a specific SQLite database (optional)",
    )
    studio_serve.add_argument(
        "--project",
        default=None,
        help="Project name under ~/.omniagents/traces (or OMNIAGENTS_HOME) to serve (optional)",
    )
    studio_serve.add_argument(
        "--token", dest="auth_token", default=None, help="Bearer token for API access"
    )
    studio_serve.add_argument(
        "--dev",
        action="store_true",
        help="Run with the Studio web dev server (auto rebuild UI)",
    )
    studio_serve.add_argument(
        "--dev-host",
        default=None,
        help="Host for the Studio web dev server (default: match --host)",
    )
    studio_serve.add_argument(
        "--dev-port",
        type=int,
        default=5173,
        help="Port for the Studio web dev server (default: 5173)",
    )

    studio_export = studio_sub.add_parser(
        "export", help="Export traces from OmniAgents Studio"
    )
    studio_export.add_argument("output", help="Output file path")
    studio_export.add_argument(
        "--format",
        choices=["json", "csv", "parquet"],
        default="json",
        help="Export format (default: json)",
    )
    studio_export.add_argument(
        "--project",
        default=None,
        help="Project name to export (defaults to active project)",
    )

    studio_show = studio_sub.add_parser(
        "show", help="Display Studio project information"
    )
    studio_show.add_argument(
        "--project",
        default=None,
        help="Project name to show (defaults to active project)",
    )

    agent_parser = subparsers.add_parser("agent", help="Agent-level operations")
    agent_sub = agent_parser.add_subparsers(dest="agent_cmd")
    agent_run = agent_sub.add_parser("run", help="Run an agent (agent-level)")
    agent_run.add_argument(
        "--mode",
        "-m",
        choices=["server", "ink", "web"],
        default="web",
        help="Run mode: 'web' (default) for React web UI, 'ink' for React Ink TUI, 'server' for JSON-RPC server",
    )
    agent_run.add_argument(
        "--project", "-P", type=str, help="Path to project.yml (dbt-style)."
    )
    agent_run.add_argument(
        "--agent",
        "-a",
        type=str,
        default=None,
        help="Agent key to run from project.yml (defaults to entrypoint).",
    )
    agent_run.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug mode (overrides YAML setting).",
    )
    agent_run.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Resume a previous CLI session using this identifier.",
    )
    agent_run.add_argument(
        "--resume",
        action="store_true",
        help="Show TUI session picker (Bubble Tea mode only).",
    )
    agent_run.add_argument(
        "--approvals",
        choices=["require", "auto", "skip"],
        default=None,
        help="Approval policy: require (manual), auto (auto-approve), skip (disable SafeAgent).",
    )
    agent_run.add_argument(
        "--on-reject",
        dest="on_reject",
        choices=["halt", "continue"],
        default=None,
        help="Behavior when a tool call is rejected: halt or continue.",
    )
    agent_run.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind (server mode only, default 0.0.0.0)",
    )
    agent_run.add_argument(
        "--port",
        "-p",
        type=int,
        default=8000,
        help="Port to listen on (server mode only, default 8000)",
    )
    agent_run.add_argument(
        "--auth-token",
        dest="auth_token",
        default=None,
        help="Bearer token to require from clients (server mode only)",
    )
    agent_run.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload server when code changes (server mode only)",
    )
    agent_run.add_argument(
        "prompt",
        nargs="*",
        help="Optional message to send immediately after startup.",
    )

    agent_save = agent_sub.add_parser(
        "save", help="Save an agent spec as a standalone Python module (agent-level)"
    )
    agent_save.add_argument(
        "--project",
        "-P",
        type=str,
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    agent_save.add_argument(
        "--agent",
        "-a",
        type=str,
        default=None,
        help="Agent key from project.yml (defaults to entrypoint)",
    )
    agent_save.add_argument(
        "--output",
        "-o",
        type=str,
        default="generated_agent.py",
        help="Output Python file path.",
    )

    agent_opt = agent_sub.add_parser(
        "optimize",
        help="Optimize an agent's instructions using GEPA over train/dev/test splits",
    )
    agent_opt.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    agent_opt.add_argument(
        "--agent", "-a", default="", help="Agent key (defaults to entrypoint)"
    )
    agent_opt.add_argument(
        "--category",
        "-c",
        required=False,
        help="Failure category to optimize (required unless --train-file/--dev-file/--test-file provided)",
    )
    agent_opt.add_argument(
        "--train-file",
        dest="train_file",
        default=None,
        help="Explicit path to a train split YAML",
    )
    agent_opt.add_argument(
        "--dev-file",
        dest="dev_file",
        default=None,
        help="Explicit path to a dev split YAML",
    )
    agent_opt.add_argument(
        "--test-file",
        dest="test_file",
        default=None,
        help="Explicit path to a test split YAML",
    )
    agent_opt.add_argument(
        "--positives-only",
        dest="positives_only",
        action="store_true",
        help="Use only positive (label=True) scenarios from splits",
    )
    agent_opt.add_argument(
        "--all-cases",
        dest="positives_only",
        action="store_false",
        help="Use both positives and negatives from splits",
    )
    agent_opt.set_defaults(positives_only=False)
    agent_opt.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Max turns for the agent per example (default from YAML)",
    )
    agent_opt.add_argument(
        "--max-metric-calls", dest="max_metric_calls", type=int, default=100
    )
    agent_opt.add_argument(
        "--reflection-model",
        dest="reflection_model",
        default="gpt-4.1",
        help="Model to use for reflection/mutation (default: gpt-4.1)",
    )
    agent_opt.add_argument(
        "--score-mode", choices=["fraction", "all_or_nothing"], default="fraction"
    )
    agent_opt.add_argument(
        "--optimizer",
        dest="optimizer",
        default=None,
        help="Optimizer key defined under evaluation.optimizers to drive rewrites (overrides --reflection-model)",
    )
    agent_opt.add_argument("--seed", type=int, default=0)
    agent_opt.add_argument(
        "--output", "-o", default="", help="Directory to write optimization artifacts"
    )
    agent_opt.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        help="Resume from existing output directory (default: use a fresh subdirectory)",
    )
    agent_opt.add_argument(
        "--name",
        default=None,
        help="Scenario set name under artifacts/eval/<name>/ for split lookup when files are not provided",
    )
    _agent_apply_group = agent_opt.add_mutually_exclusive_group()
    _agent_apply_group.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Apply optimized instructions back to the agent source with backup (default)",
    )
    _agent_apply_group.add_argument(
        "--no-apply",
        dest="apply",
        action="store_false",
        help="Do not apply optimized changes back to the agent source",
    )
    agent_opt.set_defaults(apply=True)

    judge_parser = subparsers.add_parser("judge", help="Judge utilities")
    judge_sub = judge_parser.add_subparsers(dest="judge_cmd")
    judge_run = judge_sub.add_parser(
        "run", help="Evaluate an LLM-as-Judge over a split"
    )
    judge_run.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    judge_run.add_argument(
        "--agent", "-a", default="", help="Agent key (defaults to entrypoint)"
    )
    judge_run.add_argument(
        "--category",
        "-c",
        required=False,
        help="Category name for locating splits (required unless --split-file provided)",
    )
    judge_run.add_argument(
        "--split",
        "-s",
        choices=["dev", "test", "train"],
        default="dev",
        help="Which split to judge (default: dev)",
    )
    judge_run.add_argument(
        "--split-file",
        dest="split_file",
        required=False,
        default=None,
        help="Explicit path to a split YAML; overrides --category/--split lookup",
    )
    judge_run.add_argument(
        "--name",
        default=None,
        help="Scenario set name under artifacts/eval/<name>/ for split lookup (used with --category)",
    )
    judge_run.add_argument(
        "--judge",
        "-j",
        default=None,
        help="Judge key; if omitted and exactly one judge is configured, it will be used",
    )
    judge_run.add_argument("--max-turns", type=int, default=1)
    judge_run.add_argument("--run-max-turns", type=int, default=None)
    judge_run.add_argument(
        "--replay-mode", choices=["replay", "auto", "record", "live"], default="replay"
    )
    judge_run.add_argument(
        "--runs-file",
        dest="runs_file",
        default=None,
        help="Optional explicit runs fixtures JSON to use",
    )
    judge_run.add_argument("--output", "-o", default="")
    judge_run.add_argument(
        "--fail-on-disagreement",
        dest="fail_on_disagreement",
        action="store_true",
        help="Exit with code 1 if any FP/FN disagreements are found",
    )
    judge_run.add_argument("--show", type=int, default=20)
    judge_run.add_argument(
        "--disagreements",
        dest="disagreements",
        action="store_true",
        help="Print disagreements (FP/FN) with scenario file and line numbers (default)",
    )
    judge_run.add_argument(
        "--no-disagreements",
        dest="disagreements",
        action="store_false",
        help="Do not print disagreements",
    )
    judge_run.set_defaults(disagreements=True)
    judge_run.add_argument(
        "--reporter", choices=["auto", "pretty", "tap"], default="auto"
    )
    judge_run.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto"
    )
    judge_run.add_argument("-q", "--quiet", action="count", default=0)
    judge_run.add_argument("-v", "--verbose", action="count", default=0)

    judge_opt = judge_sub.add_parser(
        "optimize",
        help="Optimize an LLM-as-Judge's prompts using GEPA over train/dev/test splits",
    )
    judge_opt.add_argument(
        "--project",
        "-P",
        required=False,
        help="Path to project.yml (defaults to project.yml in CWD)",
    )
    judge_opt.add_argument(
        "--agent", "-a", default="", help="Agent key (defaults to entrypoint)"
    )
    judge_opt.add_argument(
        "--category",
        "-c",
        required=False,
        help="Failure category name to locate splits under artifacts/eval/*/splits/<category>/ (optional if --train-file/--dev-file/--test-file provided)",
    )
    judge_opt.add_argument(
        "--train-file",
        dest="train_file",
        default=None,
        help="Explicit path to a train split YAML",
    )
    judge_opt.add_argument(
        "--dev-file",
        dest="dev_file",
        default=None,
        help="Explicit path to a dev split YAML",
    )
    judge_opt.add_argument(
        "--test-file",
        dest="test_file",
        default=None,
        help="Explicit path to a test split YAML",
    )
    judge_opt.add_argument(
        "--name",
        default=None,
        help="Scenario set name under artifacts/eval/<name>/ for split lookup when files are not provided",
    )
    judge_opt.add_argument(
        "--judge",
        "-j",
        default=None,
        help="Judge key; if omitted and exactly one judge is configured, it will be used",
    )
    judge_opt.add_argument(
        "--runs-file",
        dest="runs_file",
        default=None,
        help="Path to generated_runs.json; defaults alongside split timestamp dir",
    )
    judge_opt.add_argument(
        "--source",
        choices=["final_assistant_text", "history"],
        default="final_assistant_text",
    )
    judge_opt.add_argument(
        "--max-turns",
        type=int,
        default=1,
        help="Max turns for the judge agent per example (default: 1)",
    )
    judge_opt.add_argument(
        "--max-metric-calls", dest="max_metric_calls", type=int, default=100
    )
    judge_opt.add_argument(
        "--reflection-model",
        dest="reflection_model",
        default="gpt-4.1",
        help="Model to use for reflection/mutation (default: gpt-4.1)",
    )
    judge_opt.add_argument(
        "--optimize",
        default="instructions",
        help="Comma-separated list of components to optimize: instructions,template (default: instructions)",
    )
    judge_opt.add_argument(
        "--optimizer",
        dest="optimizer",
        default=None,
        help="Optimizer key defined under evaluation.optimizers to drive rewrites (overrides --reflection-model)",
    )
    judge_opt.add_argument("--seed", type=int, default=0)
    judge_opt.add_argument(
        "--output", "-o", default="", help="Directory to write optimization artifacts"
    )
    judge_opt.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        help="Resume from existing output directory (default: use a fresh subdirectory)",
    )
    _judge_apply_group2 = judge_opt.add_mutually_exclusive_group()
    _judge_apply_group2.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Apply optimized instructions/template back to source files with backups (default)",
    )
    _judge_apply_group2.add_argument(
        "--no-apply",
        dest="apply",
        action="store_false",
        help="Do not apply optimized changes back to source files",
    )
    judge_opt.set_defaults(apply=True)

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
                include_samples=not bool(getattr(args, "no_samples", False)),
            )
            print(f"Created project '{sanitized}' at {project_path}")
            for p in created:
                print(f"  {p}")
            print(f"\nNext: cd {sanitized}; omniagents run")
            sys.exit(0)
        except Exception as e:
            print(f"Error creating project: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)
    elif args.command == "run":
        from omniagents.core.runtime.runner import Runner

        # Resolve spec/config from either --config or --project
        runner = None
        resolved_config_path = None

        if getattr(args, "config", None):
            # Single-file mode
            resolved_config_path = args.config
            try:
                from omniagents.core.config.loader import load_agent_spec_from_yaml

                spec = load_agent_spec_from_yaml(resolved_config_path)
                runner = Runner(spec=spec)
            except Exception as e:
                print(f"Error loading agent config: {e}")
                sys.exit(1)
        elif getattr(args, "project", None):
            try:
                from omniagents.core.project.runtime import (
                    load_project,
                    build_spec_from_project,
                )
            except Exception:
                load_project = None
                build_spec_from_project = None
            if load_project and build_spec_from_project:
                proj = load_project(str(args.project))
                spec = build_spec_from_project(
                    proj,
                    agent_key=(args.agent or None),
                )
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
                    from omniagents.core.project.runtime import (
                        load_project,
                        build_spec_from_project,
                    )

                    proj = load_project(proj_file)
                    spec = build_spec_from_project(
                        proj,
                        agent_key=(args.agent or None),
                    )
                    runner = Runner(spec=spec)
                except Exception as e:
                    print(f"Error loading project: {e}")
                    sys.exit(2)
            else:
                print(
                    "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                )
                sys.exit(2)

        # Initialize debug system with CLI flag
        from omniagents.core.debug import Debug

        Debug.init(cli_flag=args.debug)

        resume_flag = bool(getattr(args, "resume", False))

        initial_prompt = _merge_prompt_words(getattr(args, "prompt", None))

        # Pass all relevant args to the backend
        kwargs = {
            "config_path": resolved_config_path,
            "args": args,  # Pass all CLI args for backend-specific handling
            "session_id": getattr(args, "session_id", None),
        }

        if resume_flag:
            kwargs["resume"] = True

        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt

        if getattr(args, "ui_minimal", False):
            kwargs["minimal_ui"] = True

        # Add server-specific kwargs if in server mode
        if args.mode == "server":
            kwargs.update(
                {
                    "host": args.host,
                    "port": args.port,
                    "auth_token": args.auth_token,
                    "reload": args.reload,
                }
            )

        try:
            runner.run(mode=args.mode, **kwargs)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            sys.exit(1)
        except ImportError as e:
            print(f"Error: Failed to import required modules: {e}")
            if "PyYAML" in str(e):
                print(
                    "Hint: PyYAML is required for loading config files. Install it using: pip install pyyaml"
                )
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

            proj_path = getattr(args, "project", None)
            if not proj_path:
                p = Path.cwd() / "project.yml"
                if p.is_file():
                    proj_path = str(p)
                else:
                    print(
                        "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                    )
                    sys.exit(2)
            proj = load_project(str(proj_path))
            _key, agent_yaml_path = resolve_agent(proj, agent_key=(args.agent or None))
            # Collect project tool dirs
            tool_dirs = proj.paths.get("tools") if hasattr(proj, "paths") else []
            from omniagents.core.project.export import save_agent_as_py

            save_agent_as_py(
                str(agent_yaml_path), args.output, project_tool_dirs=tool_dirs or []
            )
            print(f"Standalone Python module created at: {args.output}")
        except Exception as e:
            print(f"Error saving agent spec: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)
    elif args.command == "studio":
        if not getattr(args, "studio_cmd", None):
            studio_parser.print_help()
            sys.exit(1)
        try:
            from omniagents.studio import tracing as studio_mod
        except ImportError:
            try:
                from omniagents import agentio as studio_mod
            except ImportError as e:
                print(f"Error importing Studio module: {e}")
                print(
                    "Install optional dependencies with: pip install omniagents[studio]"
                )
                sys.exit(1)

        project_hint, _manifest_path, derived_from_manifest = (
            _resolve_studio_project_value(getattr(args, "project", None))
        )
        inferred_project = getattr(args, "project", None)
        if derived_from_manifest:
            inferred_project = project_hint
        elif inferred_project is None and project_hint:
            inferred_project = project_hint

        if args.studio_cmd == "serve":
            try:
                dev_host_value = getattr(args, "dev_host", None)
                if dev_host_value is None:
                    dev_host_value = args.host or DEFAULT_STUDIO_DEV_HOST
                studio_mod.serve(
                    host=args.host,
                    port=args.port,
                    db_path=getattr(args, "db_path", None),
                    project=inferred_project,
                    auth_token=getattr(args, "auth_token", None),
                    dev=getattr(args, "dev", False),
                    dev_host=dev_host_value,
                    dev_port=getattr(args, "dev_port", 5173),
                    manifest_path=str(_manifest_path) if _manifest_path else None,
                )
            except ImportError as e:
                print(f"Error starting Studio server: {e}")
                print(
                    "Install server dependencies with: pip install omniagents[studio-server]"
                )
                sys.exit(1)
            except Exception as e:
                print(f"Error starting Studio server: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.studio_cmd == "export":
            try:
                if not inferred_project:
                    print(
                        "Error: Provide --project <name> or run from a directory containing project.yml with tracing.project."
                    )
                    sys.exit(2)
                studio_mod.export(
                    args.output,
                    format=getattr(args, "format", "json"),
                    project=inferred_project,
                )
            except Exception as e:
                print(f"Error exporting traces: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.studio_cmd == "show":
            try:
                studio_mod.show(project=inferred_project)
            except Exception as e:
                print(f"Error displaying Studio information: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        else:
            studio_parser.print_help()
            sys.exit(1)
    elif args.command == "agent":
        if args.agent_cmd == "run":
            from omniagents.core.runtime.runner import Runner
            from omniagents.core.debug import Debug

            runner = None
            resolved_config_path = None
            if getattr(args, "project", None):
                try:
                    from omniagents.core.project.runtime import (
                        load_project,
                        build_spec_from_project,
                    )
                except Exception:
                    load_project = None
                    build_spec_from_project = None
                if load_project and build_spec_from_project:
                    proj = load_project(str(args.project))
                    spec = build_spec_from_project(
                        proj,
                        agent_key=(args.agent or None),
                    )
                    runner = Runner(spec=spec)
                else:
                    print("Error: project runtime not available.")
                    sys.exit(1)
            else:
                cwd = Path.cwd()
                proj_file = None
                p = cwd / "project.yml"
                if p.is_file():
                    proj_file = str(p)
                if proj_file:
                    try:
                        from omniagents.core.project.runtime import (
                            load_project,
                            build_spec_from_project,
                        )

                        proj = load_project(proj_file)
                        spec = build_spec_from_project(
                            proj,
                            agent_key=(args.agent or None),
                        )
                        runner = Runner(spec=spec)
                    except Exception as e:
                        print(f"Error loading project: {e}")
                        sys.exit(2)
                else:
                    print(
                        "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                    )
                    sys.exit(2)
            Debug.init(cli_flag=args.debug)
            resume_flag = bool(getattr(args, "resume", False))
            initial_prompt = _merge_prompt_words(getattr(args, "prompt", None))

            kwargs = {
                "config_path": resolved_config_path,
                "args": args,
                "session_id": getattr(args, "session_id", None),
            }
            if resume_flag:
                kwargs["resume"] = True
            if initial_prompt:
                kwargs["initial_prompt"] = initial_prompt
            if args.mode == "server":
                kwargs.update(
                    {
                        "host": args.host,
                        "port": args.port,
                        "auth_token": args.auth_token,
                        "reload": args.reload,
                    }
                )
            try:
                runner.run(mode=args.mode, **kwargs)
            except FileNotFoundError as e:
                print(f"Error: {e}")
                sys.exit(1)
            except ImportError as e:
                print(f"Error: Failed to import required modules: {e}")
                if "PyYAML" in str(e):
                    print(
                        "Hint: PyYAML is required for loading config files. Install it using: pip install pyyaml"
                    )
                sys.exit(1)
            except KeyboardInterrupt:
                print("\nExiting.")
            except Exception as e:
                print(f"An unexpected error occurred: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.agent_cmd == "save":
            try:
                from omniagents.core.project.runtime import load_project, resolve_agent

                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                proj = load_project(str(proj_path))
                _key, agent_yaml_path = resolve_agent(
                    proj, agent_key=(args.agent or None)
                )
                tool_dirs = proj.paths.get("tools") if hasattr(proj, "paths") else []
                from omniagents.core.project.export import save_agent_as_py

                save_agent_as_py(
                    str(agent_yaml_path), args.output, project_tool_dirs=tool_dirs or []
                )
                print(f"Standalone Python module created at: {args.output}")
            except Exception as e:
                print(f"Error saving agent spec: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.agent_cmd == "optimize":
            try:
                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                from omniagents.core.eval.optimize import optimize_agent_from_project

                optimize_agent_from_project(
                    proj_path=str(proj_path),
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                    category=(str(getattr(args, "category") or "") or None),
                    train_file=getattr(args, "train_file", None),
                    dev_file=getattr(args, "dev_file", None),
                    test_file=getattr(args, "test_file", None),
                    name=(str(getattr(args, "name", "") or "") or None),
                    positives_only=bool(getattr(args, "positives_only", False)),
                    max_turns=(
                        int(getattr(args, "max_turns"))
                        if getattr(args, "max_turns", None) is not None
                        else None
                    ),
                    max_metric_calls=int(getattr(args, "max_metric_calls") or 100),
                    reflection_model=str(
                        getattr(args, "reflection_model") or "gpt-4.1"
                    ),
                    score_mode=str(
                        getattr(args, "score_mode", "fraction") or "fraction"
                    ),
                    optimizer_key=(
                        str(getattr(args, "optimizer"))
                        if getattr(args, "optimizer", None)
                        else None
                    ),
                    seed=int(getattr(args, "seed") or 0),
                    output_dir=(str(getattr(args, "output") or "") or None),
                    resume=bool(getattr(args, "resume", False)),
                    apply=bool(getattr(args, "apply", True)),
                )
                return
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error optimizing agent instructions: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        else:
            agent_parser.print_help()
    elif args.command == "compile":
        try:
            from omniagents.core.project.compiler import compile_project

            proj_path = getattr(args, "project", None)
            if not proj_path:
                p = Path.cwd() / "project.yml"
                if p.is_file():
                    proj_path = str(p)
                else:
                    print(
                        "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                    )
                    sys.exit(2)
            compiled_cfg_path, graph = compile_project(
                str(proj_path),
                select=str(getattr(args, "select", "") or ""),
                exclude=str(getattr(args, "exclude", "") or ""),
                agent_key=(str(getattr(args, "agent", "") or "") or None),
            )
            print(str(compiled_cfg_path))
            print(json.dumps(graph, indent=2))
        except Exception as e:
            print(f"Error compiling project: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)
    elif args.command == "eval":
        if args.eval_cmd == "suite" and getattr(args, "suite_cmd", None) == "run":
            try:
                from omniagents.core.project.compiler import compile_project

                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(
                    str(proj_path),
                    select=str(args.select or ""),
                    exclude=str(args.exclude or ""),
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                )
                from pathlib import Path as _P

                cfg = (
                    yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8"))
                    or {}
                )
                tracing = cfg.get("tracing") or {}
                from omniagents.core.eval.pipeline import run_evaluation

                qlvl_pre = int(getattr(args, "quiet", 0) or 0)
                vlvl_pre = int(getattr(args, "verbose", 0) or 0)
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
                    codes = {
                        "red": "\x1b[31m",
                        "green": "\x1b[32m",
                        "yellow": "\x1b[33m",
                        "bold": "\x1b[1m",
                        "reset": "\x1b[0m",
                    }
                    return f"{codes.get(color, '')}{s}{codes['reset']}"

                def _progress_cb(ev: dict):
                    nonlocal did_stream_line, line_count
                    if quiet_pre:
                        return
                    if ev.get("event") == "progress":
                        did_stream_line = True
                        skipped = bool(ev.get("skipped"))
                        failed = int(ev.get("failed") or 0)
                        if skipped:
                            ch = _c_pre("s", "yellow")
                        elif failed == 0:
                            ch = _c_pre(".", "green")
                        else:
                            ch = _c_pre("F", "red")
                        try:
                            print(ch, end="", flush=True)
                        except Exception:
                            pass
                        line_count += 1
                        if line_count >= 80:
                            try:
                                print("")
                            except Exception:
                                pass
                            line_count = 0

                wf_base = None
                try:
                    if (getattr(args, "name", None) is not None) and str(
                        getattr(args, "name")
                    ).strip():
                        base_name = tracing.get("name") or _P(compiled_cfg_path).stem
                        wf_base = (
                            f"{base_name}_scenarios_{str(getattr(args,'name')).strip()}"
                        )
                except Exception:
                    wf_base = None
                data = run_evaluation(
                    _P(compiled_cfg_path),
                    tracing_cfg=tracing,
                    workflow_base_name=wf_base,
                    include_unmeasured=bool(getattr(args, "include_unmeasured", False)),
                    progress_cb=_progress_cb,
                )
                text = json.dumps(data, indent=2)
                yaml_output = (cfg.get("evaluation") or {}).get("output") or ""
                out_arg = str(getattr(args, "output", "") or "").strip()
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
                    name_arg = str(getattr(args, "name", "") or "").strip()
                    if name_arg:
                        final_out = (
                            base_proj
                            / "artifacts"
                            / "eval"
                            / name_arg
                            / "run_results.json"
                        ).resolve()
                    else:
                        final_out = (
                            base_proj / "artifacts" / "eval" / "run_results.json"
                        ).resolve()
                final_out.parent.mkdir(parents=True, exist_ok=True)
                final_out.write_text(text, encoding="utf-8")
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
                    for m in r.get("measures") or []:
                        measure_total += 1
                        pv = m.get("passed")
                        passed = bool(pv) if pv is not None else None
                        if passed is False:
                            measure_failed += 1
                        desc = f"Scenario {scen}: measure {m.get('name')}"
                        reason = m.get("reason")
                        checks.append(
                            {
                                "type": "measure",
                                "desc": desc,
                                "pass": passed is True,
                                "skip": passed is None,
                                "reason": reason if isinstance(reason, str) else "",
                                "prompt": r.get("prompt") or "",
                                "assistant": r.get("final_assistant_text") or "",
                                "scenario_id": sid,
                                "scenario_name": sname,
                            }
                        )
                metrics_failed = 0
                for mt in metrics:
                    name = mt.get("label") or mt.get("name") or "metric"
                    value = mt.get("value")
                    target = mt.get("target")
                    has_target = target is not None
                    meets = bool(mt.get("meets_target")) if has_target else None
                    if has_target and not meets:
                        metrics_failed += 1
                    vstr = (
                        f"{value:.3f}"
                        if isinstance(value, (int, float))
                        else str(value)
                    )
                    if has_target:
                        op = str(target.get("op", ">="))
                        desc = (
                            f"Metric {name}: {vstr} target {op} {target.get('value')}"
                        )
                    else:
                        desc = f"Metric {name}: {vstr}"
                    checks.append(
                        {
                            "type": "metric",
                            "desc": desc,
                            "pass": (meets is True) if has_target else True,
                            "skip": not has_target,
                            "reason": "no target" if not has_target else "",
                        }
                    )
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
                    codes = {
                        "red": "\x1b[31m",
                        "green": "\x1b[32m",
                        "yellow": "\x1b[33m",
                        "bold": "\x1b[1m",
                        "reset": "\x1b[0m",
                    }
                    return f"{codes.get(color, '')}{s}{codes['reset']}"

                # Compute scenario line numbers from compiled YAML for location display
                sc_line_by_id = {}
                sc_line_by_name = {}
                try:
                    comp_lines = (
                        _P(compiled_cfg_path).read_text(encoding="utf-8").splitlines()
                    )
                    import re as _re

                    collecting = False
                    base_indent = 0
                    for i, ln in enumerate(comp_lines, start=1):
                        if not collecting:
                            m = _re.match(r"^(\s*)scenarios\s*:", ln)
                            if m:
                                collecting = True
                                base_indent = len(m.group(1))
                            continue
                        cur_indent = len(ln) - len(ln.lstrip(" "))
                        if ln.strip() == "" or ln.strip().startswith("#"):
                            continue
                        if cur_indent <= base_indent and not ln.lstrip().startswith(
                            "-"
                        ):
                            collecting = False
                            continue
                        m_id = _re.match(r"^\s*-\s*id\s*:\s*(.+?)\s*$", ln)
                        if m_id:
                            scid = m_id.group(1).strip().strip("\"'")
                            if scid and scid not in sc_line_by_id:
                                sc_line_by_id[scid] = i
                            continue
                        m_name = _re.match(r"^\s*-\s*name\s*:\s*(.+?)\s*$", ln)
                        if m_name:
                            nm = m_name.group(1).strip().strip("\"'")
                            if nm and nm not in sc_line_by_name:
                                sc_line_by_name[nm] = i
                            continue
                except Exception:
                    sc_line_by_id = {}
                    sc_line_by_name = {}
                for c in checks:
                    if c.get("type") == "measure":
                        sid = c.get("scenario_id")
                        sname = c.get("scenario_name")
                        ln = (sc_line_by_id.get(sid) if sid else None) or (
                            sc_line_by_name.get(sname) if sname else None
                        )
                        if ln:
                            c["file"] = str(_P(compiled_cfg_path))
                            c["line"] = int(ln)
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
                                fp = c.get("file")
                                fl = c.get("line")
                                if fp and fl:
                                    print(f"# Location: {fp}:{fl}")
                                p = c.get("prompt") or ""
                                if p:
                                    pt = p if len(p) <= 300 else (p[:300] + "...")
                                    print(f"# Prompt: {pt}")
                                a = c.get("assistant") or ""
                                if a:
                                    at = a if len(a) <= 300 else (a[:300] + "...")
                                    print(f"# Assistant: {at}")
                    print(
                        f"# Summary: {len(metrics)} metrics, {metrics_failed} failing targets"
                    )
                    print(
                        f"# Measures: {measure_total} total, {measure_failed} failing"
                    )
                    if scen_total is not None:
                        print(
                            f"# Scenarios: included={scen_included} skipped_unmeasured={scen_skipped} total={scen_total}"
                        )
                    if um:
                        for k in sorted(um.keys()):
                            try:
                                cnt = int(um[k])
                            except Exception:
                                cnt = um[k]
                            print(
                                f"# Warning: unknown measure '{k}' in {cnt} scenario(s)"
                            )
                else:
                    qlvl = int(getattr(args, "quiet", 0) or 0)
                    vlvl = int(getattr(args, "verbose", 0) or 0)
                    vlevel = vlvl - qlvl
                    quiet = vlevel < 0
                    verbose = vlevel > 0
                    passed_count = sum(
                        1 for x in checks if not x.get("skip") and x.get("pass")
                    )
                    failed = [
                        x for x in checks if not x.get("skip") and not x.get("pass")
                    ]
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
                            if f.get("type") == "measure":
                                fp = f.get("file")
                                fl = f.get("line")
                                if fp and fl:
                                    print(f"  Location: {fp}:{fl}")
                                prompt = f.get("prompt") or ""
                                if prompt:
                                    p = (
                                        prompt
                                        if len(prompt) <= 300
                                        else (prompt[:300] + "...")
                                    )
                                    print(f"  Prompt: {p}")
                                assistant = f.get("assistant") or ""
                                if assistant:
                                    a = (
                                        assistant
                                        if len(assistant) <= 300
                                        else (assistant[:300] + "...")
                                    )
                                    print(f"  Assistant: {a}")
                            if rsn:
                                print(f"  Reason: {rsn}")
                    if not quiet:
                        print("")
                        print(
                            f"Passed: {passed_count}  Failed: {len(failed)}  Skipped: {len(skipped)}  Total: {total}"
                        )
                        print(
                            f"Metrics: {len(metrics)}  Failing targets: {metrics_failed}"
                        )
                        print(f"Measures: {measure_total}  Failing: {measure_failed}")
                        if scen_total is not None:
                            print(
                                f"Scenarios: included={scen_included}  Skipped (no measures)={scen_skipped}  Total={scen_total}"
                            )
                        if um:
                            pairs = []
                            for k in sorted(um.keys()):
                                try:
                                    pairs.append(f"{k}({int(um[k])})")
                                except Exception:
                                    pairs.append(f"{k}({um[k]})")
                            print(
                                f"Warning: unknown measures not registered: {', '.join(pairs)}"
                            )
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
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif (
            args.eval_cmd == "scenarios"
            and getattr(args, "scen_cmd", None) == "generate"
        ):
            try:
                from omniagents.core.project.compiler import compile_project

                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(
                    str(proj_path),
                    select=str(args.select or ""),
                    exclude=str(args.exclude or ""),
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                )
                from omniagents.core.eval.scenarios import generate_scenarios
                from pathlib import Path as _P
                import asyncio as _asyncio

                cfg = (
                    yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8"))
                    or {}
                )
                tracing = cfg.get("tracing") or {}
                name_arg = str(getattr(args, "name", "") or "").strip()
                out_arg = str(getattr(args, "output", "") or "").strip()
                if not name_arg and not out_arg:
                    print(
                        "Error: Provide --name <scenario_set> or --output <path> for generated scenarios."
                    )
                    sys.exit(2)
                output_path = None
                if out_arg:
                    outp = _P(out_arg)
                    if not outp.is_absolute():
                        base_proj = _P(proj_path).resolve()
                        if base_proj.is_file():
                            base_proj = base_proj.parent
                        outp = (base_proj / outp).resolve()
                    output_path = outp
                wf_name = None
                try:
                    provider = str(tracing.get("provider", "")).lower()
                    if provider in {"studio", "agentio"}:
                        from datetime import datetime

                        try:
                            from omniagents.studio import tracing as studio_tracing

                            tracing_module = studio_tracing
                        except ImportError:
                            tracing_module = None

                        if tracing_module is None:
                            import agentio as legacy_tracing

                            tracing_module = legacy_tracing

                        base_name = tracing.get("name") or _P(compiled_cfg_path).stem
                        if name_arg:
                            sc_name = name_arg
                        elif output_path is not None:
                            try:
                                sc_name = output_path.parent.name
                            except Exception:
                                sc_name = "scenarios"
                        else:
                            sc_name = "scenarios"
                        ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                        wf_name = f"{base_name}_scenarios_{sc_name}_{ts}_generate"
                        resume = (tracing.get("settings") or {}).get("resume", "never")
                        tracing_module.init(
                            project=str(
                                tracing.get("project") or "scenario_generation"
                            ),
                            name=wf_name,
                            resume=str(resume),
                            replace_default=True,
                        )
                except Exception:
                    wf_name = None
                qlvl_pre = int(getattr(args, "quiet", 0) or 0)
                vlvl_pre = int(getattr(args, "verbose", 0) or 0)
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
                    codes = {
                        "red": "\x1b[31m",
                        "green": "\x1b[32m",
                        "yellow": "\x1b[33m",
                        "bold": "\x1b[1m",
                        "reset": "\x1b[0m",
                    }
                    return f"{codes.get(color, '')}{s}{codes['reset']}"

                def _progress_cb(ev: dict):
                    nonlocal did_stream_line, line_count
                    if quiet_pre:
                        return
                    et = ev.get("event")
                    if et == "start":
                        try:
                            tgt = ev.get("target")
                            if tgt is not None:
                                print(f"Generating {tgt} scenarios...", flush=True)
                        except Exception:
                            pass
                    elif et == "progress":
                        did_stream_line = True
                        try:
                            print(".", end="", flush=True)
                        except Exception:
                            pass
                        line_count += 1
                        if line_count >= 80:
                            try:
                                print("")
                            except Exception:
                                pass
                            line_count = 0
                    elif et == "done":
                        if line_count:
                            try:
                                print("")
                            except Exception:
                                pass
                            line_count = 0
                        try:
                            cnt = ev.get("count")
                            print(f"Generated {cnt} scenarios.")
                        except Exception:
                            pass

                scenarios, out_path, gen_ts = _asyncio.run(
                    generate_scenarios(
                        _P(compiled_cfg_path),
                        count=args.count,
                        seed=args.seed,
                        set_name=(name_arg or None),
                        output=output_path,
                        workflow_name=wf_name,
                        progress_cb=_progress_cb,
                    )
                )
                print(f"Wrote {len(scenarios)} scenarios to {out_path}")
            except Exception as e:
                print(f"Error generating scenarios: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif (
            args.eval_cmd == "scenarios"
            and getattr(args, "scen_cmd", None) == "annotate"
        ):
            try:
                from omniagents.core.project.compiler import compile_project
                from omniagents.core.project.runtime import load_project, resolve_agent

                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                ctx = load_project(str(proj_path))
                agent_key_arg = str(getattr(args, "agent", "") or "").strip() or None
                _, agent_yaml_path = resolve_agent(ctx, agent_key_arg)
                base = Path(proj_path).resolve()
                if base.is_file():
                    base = base.parent
                name_arg = str(getattr(args, "name", "") or "").strip()
                input_arg = str(getattr(args, "input", "") or "").strip()
                scenario_path: Optional[Path] = None
                if input_arg:
                    candidate = Path(input_arg)
                    scenario_path = (
                        candidate
                        if candidate.is_absolute()
                        else (base / candidate).resolve()
                    )
                elif name_arg:
                    candidate_dir = (base / "artifacts" / "eval" / name_arg).resolve()
                    for option in [
                        candidate_dir / "generated_scenarios.yml",
                        candidate_dir / "annotated_scenarios.yml",
                        candidate_dir / "scenarios.yml",
                    ]:
                        if option.is_file():
                            scenario_path = option
                            break
                    if scenario_path is None:
                        scenario_path = candidate_dir / "generated_scenarios.yml"
                else:
                    print(
                        "Error: Provide --name <scenario_set> or --input <path> for scenarios."
                    )
                    sys.exit(2)
                if not scenario_path.exists():
                    print(f"Error: Scenarios file not found: {scenario_path}")
                    sys.exit(2)
                compiled_cfg_path, _graph = compile_project(
                    str(proj_path),
                    select=str(getattr(args, "select", "") or ""),
                    exclude=str(getattr(args, "exclude", "") or ""),
                    agent_key=agent_key_arg,
                )
                cfg = (
                    yaml.safe_load(Path(compiled_cfg_path).read_text(encoding="utf-8"))
                    or {}
                )
                tracing_cfg = cfg.get("tracing") or {}
                studio_project = str(getattr(args, "studio_project") or "").strip()
                if not studio_project:
                    candidate = tracing_cfg.get("project")
                    if isinstance(candidate, str) and candidate.strip():
                        studio_project = candidate.strip()
                if not studio_project:
                    defaults = ctx.tracing_defaults or {}
                    if isinstance(defaults, dict):
                        candidate = defaults.get("project")
                        if isinstance(candidate, str) and candidate.strip():
                            studio_project = candidate.strip()
                if not studio_project:
                    print(
                        "Error: Unable to determine Studio project; set tracing.project in project.yml or provide --studio-project."
                    )
                    sys.exit(2)
                from omniagents.core.eval.annotate import annotate_scenarios

                annotated, _original, source_path = annotate_scenarios(
                    Path(agent_yaml_path), scenario_path, studio_project, None
                )
                output_arg = str(getattr(args, "output", "") or "").strip()
                if output_arg:
                    out_path = Path(output_arg)
                    if not out_path.is_absolute():
                        out_path = (base / out_path).resolve()
                    out_path.parent.mkdir(parents=True, exist_ok=True)

                    class NoAliasDumper(yaml.SafeDumper):
                        def ignore_aliases(self, data):
                            return True

                    out_path.write_text(
                        yaml.dump(
                            {"scenarios": annotated},
                            sort_keys=False,
                            Dumper=NoAliasDumper,
                            allow_unicode=True,
                        ),
                        encoding="utf-8",
                    )
                else:
                    out_path = write_annotation_output(ctx.root, source_path, annotated)
                print(
                    f"Updated annotations for {len(annotated)} scenarios in {out_path}"
                )
                sys.exit(0)
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error annotating scenarios: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "scenarios" and getattr(args, "scen_cmd", None) == "run":
            try:
                from omniagents.core.project.compiler import compile_project

                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(
                    str(proj_path),
                    select=str(getattr(args, "select", "") or ""),
                    exclude=str(getattr(args, "exclude", "") or ""),
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                )
                from omniagents.core.eval.scenarios import run_scenarios_against_agent
                from pathlib import Path as _P

                base = _P(proj_path).resolve()
                base = base.parent if base.is_file() else base
                name_arg = str(getattr(args, "name", "") or "").strip()
                inp = str(getattr(args, "input", "") or "").strip()
                if inp:
                    in_path = _P(inp)
                    if not in_path.is_absolute():
                        in_path = (base / in_path).resolve()
                elif name_arg:
                    in_path = (
                        base
                        / "artifacts"
                        / "eval"
                        / name_arg
                        / "generated_scenarios.yml"
                    ).resolve()
                else:
                    print(
                        "Error: Provide --name <scenario_set> or --input <path> for scenarios."
                    )
                    sys.exit(2)
                if not in_path.exists():
                    print(f"Error: Scenarios file not found: {in_path}")
                    sys.exit(2)
                sc_dir = in_path.parent
                y = yaml.safe_load(in_path.read_text(encoding="utf-8")) or {}
                scenarios = list((y.get("scenarios") or []))
                for sc in scenarios:
                    if not sc.get("id"):
                        mapping = (
                            sc.get("dimensions")
                            if isinstance(sc.get("dimensions"), dict)
                            else None
                        )
                        import hashlib, json as _json

                        blob = _json.dumps(
                            {
                                "prompt": str(sc.get("prompt", "")),
                                "dimensions": mapping or {},
                            },
                            sort_keys=True,
                        )
                        sc["id"] = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
                    sc["_source_path"] = str(in_path)
                cfg = (
                    yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8"))
                    or {}
                )
                tracing = cfg.get("tracing") or {}
                try:
                    provider = str(tracing.get("provider", "")).lower()
                    if provider in {"studio", "agentio"}:
                        from datetime import datetime

                        try:
                            from omniagents.studio import tracing as studio_tracing

                            tracing_module = studio_tracing
                        except ImportError:
                            tracing_module = None

                        if tracing_module is None:
                            import agentio as legacy_tracing

                            tracing_module = legacy_tracing

                        project = tracing.get("project") or "scenario_runs"
                        base_name = tracing.get("name") or Path(compiled_cfg_path).stem
                        ts = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
                        sc_name = name_arg or sc_dir.name
                        wf_name = f"{base_name}_scenarios_{sc_name}_{ts}"
                        resume = (tracing.get("settings") or {}).get("resume", "never")
                        tracing_module.init(
                            project=str(project),
                            name=wf_name,
                            resume=str(resume),
                            replace_default=True,
                        )
                        import asyncio as _asyncio

                        qlvl_pre = int(getattr(args, "quiet", 0) or 0)
                        vlvl_pre = int(getattr(args, "verbose", 0) or 0)
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
                            codes = {
                                "red": "\x1b[31m",
                                "green": "\x1b[32m",
                                "yellow": "\x1b[33m",
                                "bold": "\x1b[1m",
                                "reset": "\x1b[0m",
                            }
                            return f"{codes.get(color, '')}{s}{codes['reset']}"

                        def _progress_cb(ev: dict):
                            nonlocal did_stream_line, line_count
                            if quiet_pre:
                                return
                            et = ev.get("event")
                            if et == "start":
                                try:
                                    tot = ev.get("total")
                                    print(f"Running {tot} scenarios...", flush=True)
                                except Exception:
                                    pass
                            elif et == "progress":
                                did_stream_line = True
                                try:
                                    print(_c_pre(".", "green"), end="", flush=True)
                                except Exception:
                                    pass
                                line_count += 1
                                if line_count >= 80:
                                    try:
                                        print("")
                                    except Exception:
                                        pass
                                    line_count = 0
                            elif et == "done":
                                if line_count:
                                    try:
                                        print("")
                                    except Exception:
                                        pass
                                    line_count = 0
                                try:
                                    cnt = ev.get("count")
                                    print(f"Completed {cnt} scenarios.")
                                except Exception:
                                    pass

                        runs = _asyncio.run(
                            run_scenarios_against_agent(
                                _P(compiled_cfg_path).resolve(),
                                scenarios,
                                max_turns=args.run_max_turns,
                                include_history=bool(
                                    getattr(args, "include_history", False)
                                ),
                                workflow_name=wf_name,
                                progress_cb=_progress_cb,
                                scenario_source_path=in_path,
                            )
                        )
                    else:
                        import asyncio as _asyncio

                        qlvl_pre = int(getattr(args, "quiet", 0) or 0)
                        vlvl_pre = int(getattr(args, "verbose", 0) or 0)
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
                            codes = {
                                "red": "\x1b[31m",
                                "green": "\x1b[32m",
                                "yellow": "\x1b[33m",
                                "bold": "\x1b[1m",
                                "reset": "\x1b[0m",
                            }
                            return f"{codes.get(color, '')}{s}{codes['reset']}"

                        def _progress_cb(ev: dict):
                            nonlocal did_stream_line, line_count
                            if quiet_pre:
                                return
                            et = ev.get("event")
                            if et == "start":
                                try:
                                    tot = ev.get("total")
                                    print(f"Running {tot} scenarios...", flush=True)
                                except Exception:
                                    pass
                            elif et == "progress":
                                did_stream_line = True
                                try:
                                    print(_c_pre(".", "green"), end="", flush=True)
                                except Exception:
                                    pass
                                line_count += 1
                                if line_count >= 80:
                                    try:
                                        print("")
                                    except Exception:
                                        pass
                                    line_count = 0
                            elif et == "done":
                                if line_count:
                                    try:
                                        print("")
                                    except Exception:
                                        pass
                                    line_count = 0
                                try:
                                    cnt = ev.get("count")
                                    print(f"Completed {cnt} scenarios.")
                                except Exception:
                                    pass

                        runs = _asyncio.run(
                            run_scenarios_against_agent(
                                _P(compiled_cfg_path).resolve(),
                                scenarios,
                                max_turns=args.run_max_turns,
                                include_history=bool(
                                    getattr(args, "include_history", False)
                                ),
                                progress_cb=_progress_cb,
                                scenario_source_path=in_path,
                            )
                        )
                except Exception:
                    import asyncio as _asyncio

                    qlvl_pre = int(getattr(args, "quiet", 0) or 0)
                    vlvl_pre = int(getattr(args, "verbose", 0) or 0)
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
                        codes = {
                            "red": "\x1b[31m",
                            "green": "\x1b[32m",
                            "yellow": "\x1b[33m",
                            "bold": "\x1b[1m",
                            "reset": "\x1b[0m",
                        }
                        return f"{codes.get(color, '')}{s}{codes['reset']}"

                    def _progress_cb(ev: dict):
                        nonlocal did_stream_line, line_count
                        if quiet_pre:
                            return
                        et = ev.get("event")
                        if et == "start":
                            try:
                                tot = ev.get("total")
                                print(f"Running {tot} scenarios...", flush=True)
                            except Exception:
                                pass
                        elif et == "progress":
                            did_stream_line = True
                            try:
                                print(_c_pre(".", "green"), end="", flush=True)
                            except Exception:
                                pass
                            line_count += 1
                            if line_count >= 80:
                                try:
                                    print("")
                                except Exception:
                                    pass
                                line_count = 0
                        elif et == "done":
                            if line_count:
                                try:
                                    print("")
                                except Exception:
                                    pass
                                line_count = 0
                            try:
                                cnt = ev.get("count")
                                print(f"Completed {cnt} scenarios.")
                            except Exception:
                                pass

                    runs = _asyncio.run(
                        run_scenarios_against_agent(
                            _P(compiled_cfg_path).resolve(),
                            scenarios,
                            max_turns=args.run_max_turns,
                            include_history=bool(
                                getattr(args, "include_history", False)
                            ),
                            progress_cb=_progress_cb,
                            scenario_source_path=in_path,
                        )
                    )
                out_arg = str(getattr(args, "output", "") or "").strip()
                if out_arg:
                    run_out = _P(out_arg)
                    if not run_out.is_absolute():
                        run_out = (base / run_out).resolve()
                else:
                    run_out = (sc_dir / "generated_runs.json").resolve()
                run_out.parent.mkdir(parents=True, exist_ok=True)
                Path(run_out).write_text(
                    json.dumps({"runs": runs}, indent=2), encoding="utf-8"
                )
                print(f"Ran {len(runs)} scenarios; wrote results to {run_out}")
                try:
                    mf = sc_dir / "manifest.json"
                    if "wf_name" in locals() and wf_name and mf.exists():
                        try:
                            md = json.loads(mf.read_text(encoding="utf-8"))
                        except Exception:
                            md = {}
                        md["workflow_run"] = str(wf_name)
                        try:
                            mf.write_text(json.dumps(md, indent=2), encoding="utf-8")
                        except Exception:
                            pass
                except Exception:
                    pass
            except Exception as e:
                print(f"Error running scenarios: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "scenarios":
            print("Error: Provide a scenarios subcommand: generate | run")
            sys.exit(2)
        elif args.eval_cmd == "judge" and getattr(args, "judge_cmd", None) == "run":
            try:
                from omniagents.core.project.compiler import compile_project

                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(
                    str(proj_path),
                    select=None,
                    exclude=None,
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                )
                from omniagents.core.eval.judges import evaluate_split
                import asyncio as _asyncio
                from pathlib import Path as _P

                base = _P(proj_path).resolve()
                if base.is_file():
                    base = base.parent
                qlvl_pre = int(getattr(args, "quiet", 0) or 0)
                vlvl_pre = int(getattr(args, "verbose", 0) or 0)
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
                    codes = {
                        "red": "\x1b[31m",
                        "green": "\x1b[32m",
                        "yellow": "\x1b[33m",
                        "bold": "\x1b[1m",
                        "reset": "\x1b[0m",
                    }
                    return f"{codes.get(color, '')}{s}{codes['reset']}"

                def _progress_cb(ev: dict):
                    nonlocal did_stream_line, line_count
                    if quiet_pre:
                        return
                    if ev.get("event") == "progress":
                        did_stream_line = True
                        pred = ev.get("pred")
                        label = ev.get("label")
                        agree = ev.get("agree")
                        if pred is None:
                            ch = _c_pre("s", "yellow")
                        elif bool(agree):
                            ch = _c_pre(".", "green")
                        else:
                            ch = _c_pre("F", "red")
                        try:
                            print(ch, end="", flush=True)
                        except Exception:
                            pass
                        line_count += 1
                        if line_count >= 80:
                            try:
                                print("")
                            except Exception:
                                pass
                            line_count = 0

                split_file_arg = getattr(args, "split_file", None)
                if split_file_arg:
                    split_path = _P(split_file_arg)
                    if not split_path.is_absolute():
                        split_path = (base / split_path).resolve()
                    split_name = split_path.stem
                else:
                    category = str(getattr(args, "category", "") or "").strip()
                    if not category:
                        print(
                            "Error: Provide either --split-file <path> or --category <name> with --split <dev|test|train>."
                        )
                        sys.exit(2)
                    name_arg = str(getattr(args, "name", "") or "").strip()
                    if not name_arg:
                        print(
                            "Error: Provide --name <scenario_set> to locate splits (or use --split-file)."
                        )
                        sys.exit(2)
                    which = str(getattr(args, "split", "dev") or "dev")
                    split_path = (
                        base
                        / "artifacts"
                        / "eval"
                        / name_arg
                        / "splits"
                        / category
                        / f"{which}.yml"
                    ).resolve()
                    split_name = which
                    if not split_path.exists():
                        print(f"Error: Split file not found: {split_path}")
                        sys.exit(2)
                # Auto-detect judge if not provided and only one exists
                jkey = getattr(args, "judge", None)
                if not jkey:
                    cfg = (
                        yaml.safe_load(
                            _P(compiled_cfg_path).read_text(encoding="utf-8")
                        )
                        or {}
                    )
                    js = (cfg.get("evaluation") or {}).get("judges") or {}
                    if isinstance(js, dict) and len(js) == 1:
                        jkey = next(iter(js.keys()))
                    else:
                        print(
                            "Error: Provide --judge <key> (multiple or no judges configured)."
                        )
                        sys.exit(2)
                out = _asyncio.run(
                    evaluate_split(
                        _P(compiled_cfg_path),
                        split_path,
                        str(jkey),
                        judge_max_turns=int(args.max_turns or 1),
                        run_max_turns=(
                            int(args.run_max_turns)
                            if args.run_max_turns is not None
                            else None
                        ),
                        show=int(args.show),
                        replay_mode=str(getattr(args, "replay_mode", "auto") or "auto"),
                        runs_file=(
                            str(getattr(args, "runs_file"))
                            if getattr(args, "runs_file", None)
                            else None
                        ),
                        progress_cb=_progress_cb,
                    )
                )
                cfg = (
                    yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8"))
                    or {}
                )
                yml_out = (
                    (cfg.get("evaluation") or {}).get("judge_output")
                    or (cfg.get("evaluation") or {}).get("output")
                    or ""
                )
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
                tp = out.get("counts", {}).get("TP")
                tn = out.get("counts", {}).get("TN")
                fp = out.get("counts", {}).get("FP")
                fn = out.get("counts", {}).get("FN")
                tpr = out.get("tpr")
                tnr = out.get("tnr")
                try:
                    results = out.get("results") or []
                    checks = []
                    try:
                        thr = 30
                        pass_cnt = sum(1 for r in results if r.get("label") is False)
                        fail_cnt = sum(1 for r in results if r.get("label") is True)
                        cat_for_warn = None
                        try:
                            cat_for_warn = split_path.parent.name
                        except Exception:
                            cat_for_warn = None
                        prefix = f"category '{cat_for_warn}' " if cat_for_warn else ""
                        if pass_cnt < thr:
                            print(
                                f"Warning: {prefix}split '{split_name}' has only {pass_cnt} Pass labels (<{thr})."
                            )
                        if fail_cnt < thr:
                            print(
                                f"Warning: {prefix}split '{split_name}' has only {fail_cnt} Fail labels (<{thr})."
                            )
                    except Exception:
                        pass
                    for r in results:
                        pred = r.get("pred")
                        agree = r.get("agree")
                        label = r.get("label")
                        name = r.get("name") or r.get("id")
                        sid = r.get("id")
                        exp = (
                            (r.get("judge_output") or {}).get("explanation")
                            or (r.get("judge_output") or {}).get("reasoning")
                            or ""
                        )
                        kind = (
                            "FP"
                            if (pred is True and label is False)
                            else (
                                "FN"
                                if (pred is False and label is True)
                                else ("UNK" if pred is None else "OK")
                            )
                        )
                        reason = f"label={label} pred={pred} {kind}"
                        if exp:
                            reason = f"{reason} — {exp}"
                        checks.append(
                            {
                                "type": "scenario",
                                "desc": f"Scenario {name} ({sid})",
                                "pass": bool(agree) if agree is not None else False,
                                "skip": pred is None,
                                "reason": reason,
                                "prompt": r.get("prompt") or "",
                                "assistant": r.get("final_assistant_text") or "",
                                "split_file": r.get("split_file") or out.get("split"),
                                "split_line": int(r.get("split_line") or 0),
                                "label": label,
                                "pred": pred,
                            }
                        )
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
                        codes = {
                            "red": "\x1b[31m",
                            "green": "\x1b[32m",
                            "yellow": "\x1b[33m",
                            "bold": "\x1b[1m",
                            "reset": "\x1b[0m",
                        }
                        return f"{codes.get(color, '')}{s}{codes['reset']}"

                    reporter = args.reporter
                    if reporter == "auto":
                        reporter = "pretty" if _isatty() else "tap"
                    qlvl = int(getattr(args, "quiet", 0) or 0)
                    vlvl = int(getattr(args, "verbose", 0) or 0)
                    vlevel = vlvl - qlvl
                    quiet = vlevel < 0
                    verbose = vlevel > 0
                    passed_count = sum(
                        1 for x in checks if not x.get("skip") and x.get("pass")
                    )
                    failed = [
                        x for x in checks if not x.get("skip") and not x.get("pass")
                    ]
                    skipped = [x for x in checks if x.get("skip")]
                    if reporter == "tap":
                        print("TAP version 13")
                        print(f"1..{total}")
                        for i, c in enumerate(checks, start=1):
                            if c.get("skip"):
                                print(
                                    f"ok {i} - {c.get('desc')} # SKIP {c.get('reason')}"
                                )
                            elif c.get("pass"):
                                print(f"ok {i} - {c.get('desc')}")
                            else:
                                print(f"not ok {i} - {c.get('desc')}")
                                if c.get("reason"):
                                    print(f"# {c.get('reason')}")
                                p = c.get("prompt") or ""
                                if p:
                                    pt = p if len(p) <= 300 else (p[:300] + "...")
                                    print(f"# Prompt: {pt}")
                                a = c.get("assistant") or ""
                                if a:
                                    at = a if len(a) <= 300 else (a[:300] + "...")
                                    print(f"# Assistant: {at}")
                        print(f"# Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                        print(
                            f"# TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}"
                        )
                    else:
                        if not quiet and not verbose and not did_stream_line:
                            print(f"Judged {total} scenarios...")
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
                        if (
                            not quiet
                            and failed
                            and bool(getattr(args, "disagreements", False))
                        ):
                            print("")
                            print(_c("Failures:", "bold"))
                            for idx, f in enumerate(failed, start=1):
                                label = f.get("label")
                                pred = f.get("pred")
                                sf = f.get("split_file")
                                ln = int(f.get("split_line") or 0)
                                kind = (
                                    "FP"
                                    if (pred is True and label is False)
                                    else (
                                        "FN"
                                        if (pred is False and label is True)
                                        else "DISAGREE"
                                    )
                                )
                                loc = f"{sf}:{ln}" if sf else ""
                                title = f"{idx}) {f.get('desc')} label={label} pred={pred} {kind} {loc}"
                                print(_c("FAIL ", "red") + title)
                                prompt = f.get("prompt") or ""
                                if prompt:
                                    p = (
                                        prompt
                                        if len(prompt) <= 300
                                        else (prompt[:300] + "...")
                                    )
                                    print(f"  Prompt: {p}")
                                assistant = f.get("assistant") or ""
                                if assistant:
                                    a = (
                                        assistant
                                        if len(assistant) <= 300
                                        else (assistant[:300] + "...")
                                    )
                                    print(f"  Assistant: {a}")
                                rsn = f.get("reason")
                                if rsn and verbose:
                                    print(f"  Reason: {rsn}")
                        if not quiet:
                            print("")
                            print(
                                f"Passed: {passed_count}  Failed: {len(failed)}  Skipped: {len(skipped)}  Total: {total}"
                            )
                            print(f"Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                            print(
                                f"TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}"
                            )
                except Exception as _:
                    # Fallback to minimal counts output if reporter fails
                    print(f"Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                    print(
                        f"TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}"
                    )
                # Write results after displaying metrics/failures
                out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
                print(f"Wrote results: {out_path}")
                try:
                    results_for_exit = out.get("results") or []
                    any_disagree = any(
                        (
                            (r.get("pred") is True and r.get("label") is False)
                            or (r.get("pred") is False and r.get("label") is True)
                        )
                        for r in results_for_exit
                    )
                    if (
                        bool(getattr(args, "fail_on_disagreement", False))
                        and any_disagree
                    ):
                        sys.exit(1)
                except Exception:
                    pass
            except Exception as e:
                print(f"Error judge-evaluating split: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif (
            args.eval_cmd == "judge" and getattr(args, "judge_cmd", None) == "optimize"
        ):
            try:
                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                optimize_judge_from_project(
                    proj_path=str(proj_path),
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                    category=(str(getattr(args, "category") or "") or None),
                    train_file=getattr(args, "train_file", None),
                    dev_file=getattr(args, "dev_file", None),
                    test_file=getattr(args, "test_file", None),
                    name=(str(getattr(args, "name", "") or "") or None),
                    judge_key=(str(getattr(args, "judge", "") or "") or None),
                    runs_file=getattr(args, "runs_file", None),
                    source=str(
                        getattr(args, "source", "final_assistant_text")
                        or "final_assistant_text"
                    ),
                    max_turns=int(getattr(args, "max_turns") or 1),
                    max_metric_calls=int(getattr(args, "max_metric_calls") or 100),
                    reflection_model=(
                        str(getattr(args, "reflection_model"))
                        if getattr(args, "reflection_model", None)
                        else None
                    ),
                    optimize_components=str(
                        getattr(args, "optimize", "instructions") or "instructions"
                    ),
                    optimizer_key=(
                        str(getattr(args, "optimizer"))
                        if getattr(args, "optimizer", None)
                        else None
                    ),
                    seed=int(getattr(args, "seed") or 0),
                    output_dir=(str(getattr(args, "output") or "") or None),
                    resume=bool(getattr(args, "resume", False)),
                    apply=bool(getattr(args, "apply", True)),
                )
                return
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error optimizing judge: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif (
            args.eval_cmd == "agent" and getattr(args, "agent_cmd", None) == "optimize"
        ):
            try:
                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                from omniagents.core.eval.optimize import optimize_agent_from_project

                optimize_agent_from_project(
                    proj_path=str(proj_path),
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                    category=(str(getattr(args, "category") or "") or None),
                    train_file=getattr(args, "train_file", None),
                    dev_file=getattr(args, "dev_file", None),
                    test_file=getattr(args, "test_file", None),
                    name=(str(getattr(args, "name", "") or "") or None),
                    positives_only=bool(getattr(args, "positives_only", False)),
                    max_turns=(
                        int(getattr(args, "max_turns"))
                        if getattr(args, "max_turns", None) is not None
                        else None
                    ),
                    max_metric_calls=int(getattr(args, "max_metric_calls") or 100),
                    reflection_model=str(
                        getattr(args, "reflection_model") or "gpt-4.1"
                    ),
                    score_mode=str(
                        getattr(args, "score_mode", "fraction") or "fraction"
                    ),
                    optimizer_key=(
                        str(getattr(args, "optimizer"))
                        if getattr(args, "optimizer", None)
                        else None
                    ),
                    seed=int(getattr(args, "seed") or 0),
                    output_dir=(str(getattr(args, "output") or "") or None),
                    resume=bool(getattr(args, "resume", False)),
                    apply=bool(getattr(args, "apply", True)),
                )
                return
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error optimizing agent instructions: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.eval_cmd == "judge":
            print("Error: Provide a judge subcommand: run | optimize")
            sys.exit(2)
        else:
            eval_parser.print_help()
    elif args.command == "judge":
        if getattr(args, "judge_cmd", None) == "run":
            try:
                from omniagents.core.project.compiler import compile_project

                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                compiled_cfg_path, _graph = compile_project(
                    str(proj_path),
                    select=None,
                    exclude=None,
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                )
                from omniagents.core.eval.judges import evaluate_split
                import asyncio as _asyncio
                from pathlib import Path as _P

                base = _P(proj_path).resolve()
                if base.is_file():
                    base = base.parent
                qlvl_pre = int(getattr(args, "quiet", 0) or 0)
                vlvl_pre = int(getattr(args, "verbose", 0) or 0)
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
                    codes = {
                        "red": "\x1b[31m",
                        "green": "\x1b[32m",
                        "yellow": "\x1b[33m",
                        "bold": "\x1b[1m",
                        "reset": "\x1b[0m",
                    }
                    return f"{codes.get(color, '')}{s}{codes['reset']}"

                def _progress_cb(ev: dict):
                    nonlocal did_stream_line, line_count
                    if quiet_pre:
                        return
                    if ev.get("event") == "progress":
                        did_stream_line = True
                        pred = ev.get("pred")
                        label = ev.get("label")
                        agree = ev.get("agree")
                        if pred is None:
                            ch = _c_pre("s", "yellow")
                        elif bool(agree):
                            ch = _c_pre(".", "green")
                        else:
                            ch = _c_pre("F", "red")
                        try:
                            print(ch, end="", flush=True)
                        except Exception:
                            pass
                        line_count += 1
                        if line_count >= 80:
                            try:
                                print("")
                            except Exception:
                                pass
                            line_count = 0

                split_file_arg = getattr(args, "split_file", None)
                if split_file_arg:
                    split_path = _P(split_file_arg)
                    if not split_path.is_absolute():
                        split_path = (base / split_path).resolve()
                    split_name = split_path.stem
                else:
                    category = str(getattr(args, "category", "") or "").strip()
                    if not category:
                        print(
                            "Error: Provide either --split-file <path> or --category <name> with --split <dev|test|train>."
                        )
                        sys.exit(2)
                    name_arg = str(getattr(args, "name", "") or "").strip()
                    if not name_arg:
                        print(
                            "Error: Provide --name <scenario_set> to locate splits (or use --split-file)."
                        )
                        sys.exit(2)
                    which = str(getattr(args, "split", "dev") or "dev")
                    split_path = (
                        base
                        / "artifacts"
                        / "eval"
                        / name_arg
                        / "splits"
                        / category
                        / f"{which}.yml"
                    ).resolve()
                    split_name = which
                    if not split_path.exists():
                        print(f"Error: Split file not found: {split_path}")
                        sys.exit(2)
                jkey = getattr(args, "judge", None)
                if not jkey:
                    cfg = (
                        yaml.safe_load(
                            _P(compiled_cfg_path).read_text(encoding="utf-8")
                        )
                        or {}
                    )
                    js = (cfg.get("evaluation") or {}).get("judges") or {}
                    if isinstance(js, dict) and len(js) == 1:
                        jkey = next(iter(js.keys()))
                    else:
                        print(
                            "Error: Provide --judge <key> (multiple or no judges configured)."
                        )
                        sys.exit(2)
                out = _asyncio.run(
                    evaluate_split(
                        _P(compiled_cfg_path),
                        split_path,
                        str(jkey),
                        judge_max_turns=int(args.max_turns or 1),
                        run_max_turns=(
                            int(args.run_max_turns)
                            if args.run_max_turns is not None
                            else None
                        ),
                        show=int(args.show),
                        replay_mode=str(getattr(args, "replay_mode", "auto") or "auto"),
                        runs_file=(
                            str(getattr(args, "runs_file"))
                            if getattr(args, "runs_file", None)
                            else None
                        ),
                        progress_cb=_progress_cb,
                    )
                )
                cfg = (
                    yaml.safe_load(_P(compiled_cfg_path).read_text(encoding="utf-8"))
                    or {}
                )
                yml_out = (
                    (cfg.get("evaluation") or {}).get("judge_output")
                    or (cfg.get("evaluation") or {}).get("output")
                    or ""
                )
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
                tp = out.get("counts", {}).get("TP")
                tn = out.get("counts", {}).get("TN")
                fp = out.get("counts", {}).get("FP")
                fn = out.get("counts", {}).get("FN")
                tpr = out.get("tpr")
                tnr = out.get("tnr")
                try:
                    results = out.get("results") or []
                    checks = []
                    try:
                        thr = 30
                        pass_cnt = sum(1 for r in results if r.get("label") is False)
                        fail_cnt = sum(1 for r in results if r.get("label") is True)
                        cat_for_warn = None
                        try:
                            cat_for_warn = split_path.parent.name
                        except Exception:
                            cat_for_warn = None
                        prefix = f"category '{cat_for_warn}' " if cat_for_warn else ""
                        if pass_cnt < thr:
                            print(
                                f"Warning: {prefix}split '{split_name}' has only {pass_cnt} Pass labels (<{thr})."
                            )
                        if fail_cnt < thr:
                            print(
                                f"Warning: {prefix}split '{split_name}' has only {fail_cnt} Fail labels (<{thr})."
                            )
                    except Exception:
                        pass
                    for r in results:
                        pred = r.get("pred")
                        agree = r.get("agree")
                        label = r.get("label")
                        name = r.get("name") or r.get("id")
                        sid = r.get("id")
                        exp = (
                            (r.get("judge_output") or {}).get("explanation")
                            or (r.get("judge_output") or {}).get("reasoning")
                            or ""
                        )
                        kind = (
                            "FP"
                            if (pred is True and label is False)
                            else (
                                "FN"
                                if (pred is False and label is True)
                                else ("UNK" if pred is None else "OK")
                            )
                        )
                        reason = f"label={label} pred={pred} {kind}"
                        if exp:
                            reason = f"{reason} — {exp}"
                        checks.append(
                            {
                                "type": "scenario",
                                "desc": f"Scenario {name} ({sid})",
                                "pass": bool(agree) if agree is not None else False,
                                "skip": pred is None,
                                "reason": reason,
                                "prompt": r.get("prompt") or "",
                                "assistant": r.get("final_assistant_text") or "",
                                "split_file": r.get("split_file") or out.get("split"),
                                "split_line": int(r.get("split_line") or 0),
                                "label": label,
                                "pred": pred,
                            }
                        )
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
                        codes = {
                            "red": "\x1b[31m",
                            "green": "\x1b[32m",
                            "yellow": "\x1b[33m",
                            "bold": "\x1b[1m",
                            "reset": "\x1b[0m",
                        }
                        return f"{codes.get(color, '')}{s}{codes['reset']}"

                    reporter = args.reporter
                    if reporter == "auto":
                        reporter = "pretty" if _isatty() else "tap"
                    qlvl = int(getattr(args, "quiet", 0) or 0)
                    vlvl = int(getattr(args, "verbose", 0) or 0)
                    vlevel = vlvl - qlvl
                    quiet = vlevel < 0
                    verbose = vlevel > 0
                    passed_count = sum(
                        1 for x in checks if not x.get("skip") and x.get("pass")
                    )
                    failed = [
                        x for x in checks if not x.get("skip") and not x.get("pass")
                    ]
                    skipped = [x for x in checks if x.get("skip")]
                    if reporter == "tap":
                        print("TAP version 13")
                        print(f"1..{total}")
                        for i, c in enumerate(checks, start=1):
                            if c.get("skip"):
                                print(
                                    f"ok {i} - {c.get('desc')} # SKIP {c.get('reason')}"
                                )
                            elif c.get("pass"):
                                print(f"ok {i} - {c.get('desc')}")
                            else:
                                print(f"not ok {i} - {c.get('desc')}")
                                if c.get("reason"):
                                    print(f"#{' '}{c.get('reason')}")
                                p = c.get("prompt") or ""
                                if p:
                                    pt = p if len(p) <= 300 else (p[:300] + "...")
                                    print(f"# Prompt: {pt}")
                                a = c.get("assistant") or ""
                                if a:
                                    at = a if len(a) <= 300 else (a[:300] + "...")
                                    print(f"# Assistant: {at}")
                        print(f"# Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                        print(
                            f"# TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}"
                        )
                    else:
                        if not quiet and not verbose and not did_stream_line:
                            print(f"Judged {total} scenarios...")
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
                        if (
                            not quiet
                            and failed
                            and bool(getattr(args, "disagreements", False))
                        ):
                            print("")
                            print(_c("Failures:", "bold"))
                            for idx, f in enumerate(failed, start=1):
                                label = f.get("label")
                                pred = f.get("pred")
                                sf = f.get("split_file")
                                ln = int(f.get("split_line") or 0)
                                kind = (
                                    "FP"
                                    if (pred is True and label is False)
                                    else (
                                        "FN"
                                        if (pred is False and label is True)
                                        else "DISAGREE"
                                    )
                                )
                                loc = f"{sf}:{ln}" if sf else ""
                                title = f"{idx}) {f.get('desc')} label={label} pred={pred} {kind} {loc}"
                                print(_c("FAIL ", "red") + title)
                                prompt = f.get("prompt") or ""
                                if prompt:
                                    p = (
                                        prompt
                                        if len(prompt) <= 300
                                        else (prompt[:300] + "...")
                                    )
                                    print(f"  Prompt: {p}")
                                assistant = f.get("assistant") or ""
                                if assistant:
                                    a = (
                                        assistant
                                        if len(assistant) <= 300
                                        else (assistant[:300] + "...")
                                    )
                                    print(f"  Assistant: {a}")
                                rsn = f.get("reason")
                                if rsn and verbose:
                                    print(f"  Reason: {rsn}")
                        if not quiet:
                            print("")
                            print(
                                f"Passed: {passed_count}  Failed: {len(failed)}  Skipped: {len(skipped)}  Total: {total}"
                            )
                            print(f"Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                            print(
                                f"TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}"
                            )
                except Exception as _:
                    print(f"Counts: TP={tp} TN={tn} FP={fp} FN={fn}")
                    print(
                        f"TPR: {tpr if tpr is not None else 'NA'}  TNR: {tnr if tnr is not None else 'NA'}"
                    )
                out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
                print(f"Wrote results: {out_path}")
                try:
                    results_for_exit = out.get("results") or []
                    any_disagree = any(
                        (
                            (r.get("pred") is True and r.get("label") is False)
                            or (r.get("pred") is False and r.get("label") is True)
                        )
                        for r in results_for_exit
                    )
                    if (
                        bool(getattr(args, "fail_on_disagreement", False))
                        and any_disagree
                    ):
                        sys.exit(1)
                except Exception:
                    pass
            except Exception as e:
                print(f"Error judge-evaluating split: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif getattr(args, "judge_cmd", None) == "optimize":
            try:
                proj_path = getattr(args, "project", None)
                if not proj_path:
                    p = Path.cwd() / "project.yml"
                    if p.is_file():
                        proj_path = str(p)
                    else:
                        print(
                            "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                        )
                        sys.exit(2)
                optimize_judge_from_project(
                    proj_path=str(proj_path),
                    agent_key=(str(getattr(args, "agent", "") or "") or None),
                    category=(str(getattr(args, "category") or "") or None),
                    train_file=getattr(args, "train_file", None),
                    dev_file=getattr(args, "dev_file", None),
                    test_file=getattr(args, "test_file", None),
                    name=(str(getattr(args, "name", "") or "") or None),
                    judge_key=(str(getattr(args, "judge", "") or "") or None),
                    runs_file=getattr(args, "runs_file", None),
                    source=str(
                        getattr(args, "source", "final_assistant_text")
                        or "final_assistant_text"
                    ),
                    max_turns=int(getattr(args, "max_turns") or 1),
                    max_metric_calls=int(getattr(args, "max_metric_calls") or 100),
                    reflection_model=(
                        str(getattr(args, "reflection_model"))
                        if getattr(args, "reflection_model", None)
                        else None
                    ),
                    optimize_components=str(
                        getattr(args, "optimize", "instructions") or "instructions"
                    ),
                    optimizer_key=(
                        str(getattr(args, "optimizer"))
                        if getattr(args, "optimizer", None)
                        else None
                    ),
                    seed=int(getattr(args, "seed") or 0),
                    output_dir=(str(getattr(args, "output") or "") or None),
                    resume=bool(getattr(args, "resume", False)),
                    apply=bool(getattr(args, "apply", True)),
                )
                return
            except SystemExit:
                raise
            except Exception as e:
                print(f"Error optimizing judge: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        else:
            judge_parser.print_help()
    elif args.command == "generate":
        try:
            from omniagents.core.project.scaffold import (
                scaffold_tool,
                scaffold_agent,
                scaffold_guardrail,
                scaffold_server_function,
                scaffold_judge,
            )
        except Exception as e:
            print(f"Error: generators not available: {e}")
            sys.exit(1)
        if args.gen_cmd == "tool":
            try:
                created, root = scaffold_tool(
                    project_ref=str(getattr(args, "project", ".") or "."),
                    tool_name=str(args.name),
                    make_async=bool(getattr(args, "is_async", False)),
                    force=bool(getattr(args, "force", False)),
                )
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print(
                    "Next: add the tool name to your agent's 'tools' list and run: omniagents run -P project.yml"
                )
            except Exception as e:
                print(f"Error generating tool: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "agent":
            try:
                created, root, sanitized = scaffold_agent(
                    project_ref=str(getattr(args, "project", ".") or "."),
                    agent_name=str(args.name),
                    model=str(getattr(args, "model", "gpt-4.1")),
                    force=bool(getattr(args, "force", False)),
                )
                print(f"Project: {root}")
                print(f"Created agent '{sanitized}'")
                for p in created:
                    print(f"  {p}")
                print("Next: run with --agent or set agents.entrypoint in project.yml")
            except Exception as e:
                print(f"Error generating agent: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "guardrail":
            try:
                created, root = scaffold_guardrail(
                    project_ref=str(getattr(args, "project", ".") or "."),
                    kind=str(args.type),
                    name=str(args.name),
                    make_async=bool(getattr(args, "is_async", False)),
                    force=bool(getattr(args, "force", False)),
                )
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print(
                    "Next: reference this guardrail name in your agent YAML under input_guardrails or output_guardrails."
                )
            except Exception as e:
                print(f"Error generating guardrail: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "server-function":
            try:
                created, root = scaffold_server_function(
                    project_ref=str(getattr(args, "project", ".") or "."),
                    name=str(args.name),
                    make_async=bool(getattr(args, "is_async", False)),
                    force=bool(getattr(args, "force", False)),
                )
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print(
                    "Next: list this function in your agent's server_functions or set to '*'."
                )
            except Exception as e:
                print(f"Error generating server function: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "judge":
            try:
                created, root = scaffold_judge(
                    project_ref=str(getattr(args, "project", ".") or "."),
                    key=str(args.name),
                    model=str(getattr(args, "model", "gpt-4.1")),
                    source=str(getattr(args, "source", "final_assistant_text")),
                    add_measure=not bool(getattr(args, "no_measure", False)),
                    failure_category=(
                        str(getattr(args, "failure_category"))
                        if getattr(args, "failure_category", None)
                        else None
                    ),
                    force=bool(getattr(args, "force", False)),
                )
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print("Next: omniagents eval run -P project.yml --reporter pretty")
            except Exception as e:
                print(f"Error generating judge: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        elif args.gen_cmd == "optimizer":
            try:
                from omniagents.core.project.scaffold import scaffold_optimizer

                created, root = scaffold_optimizer(
                    project_ref=str(getattr(args, "project", ".") or "."),
                    key=str(args.name),
                    model=str(getattr(args, "model", "gpt-4.1")),
                    kind=str(getattr(args, "opt_type", "agent") or "agent"),
                    force=bool(getattr(args, "force", False)),
                )
                print(f"Project: {root}")
                for p in created:
                    print(f"  {p}")
                print("Next: use with --optimizer <key> on eval agent|judge optimize")
            except Exception as e:
                print(f"Error generating optimizer: {e}")
                import traceback

                traceback.print_exc()
                sys.exit(1)
        else:
            gen_parser.print_help()
    elif args.command == "ls":
        try:
            from omniagents.core.project.compiler import compile_project

            proj_path = getattr(args, "project", None)
            if not proj_path:
                p = Path.cwd() / "project.yml"
                if p.is_file():
                    proj_path = str(p)
                else:
                    print(
                        "Error: Provide --project <project.yml> (or run from a directory containing a project.yml)."
                    )
                    sys.exit(2)
            compiled_cfg_path, graph = compile_project(
                str(proj_path),
                select=None,
                exclude=None,
                agent_key=(str(getattr(args, "agent", "") or "") or None),
            )
            print(json.dumps(graph, indent=2))
        except Exception as e:
            print(f"Error listing resources: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
