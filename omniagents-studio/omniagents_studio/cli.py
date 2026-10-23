"""``omniagents-studio`` command line: serve, export, show."""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from omniagents.cli import DEFAULT_BIND_HOST


def resolve_project(
    value: Optional[str],
) -> Tuple[Optional[str], Optional[Path]]:
    """Resolve ``--project`` to a trace project name and its manifest.

    ``value`` may be a project name, a ``project.yml`` path, or a directory
    holding one. With no value, a ``project.yml`` in the working directory
    is used. The trace project is the manifest's ``tracing.project``, else
    its product slug (the framework's tracing convention), which a manifest
    without a ``product:`` section takes from its directory name.
    """

    def load_from_manifest(manifest: Path) -> Optional[str]:
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        tracing_project = (data.get("tracing") or {}).get("project")
        if isinstance(tracing_project, str) and tracing_project.strip():
            return tracing_project.strip()
        from omniagents.core.project.runtime import manifest_product

        return manifest_product(data, manifest.parent).slug

    if value:
        looks_like_path = any(
            sep in value for sep in ("/", "\\")
        ) or value.lower().endswith((".yml", ".yaml"))
        if looks_like_path:
            candidate = Path(value).expanduser()
            if candidate.is_dir():
                candidate = candidate / "project.yml"
            if candidate.is_file():
                guess = load_from_manifest(candidate)
                if guess:
                    return guess, candidate
        return value, None

    candidate = Path.cwd() / "project.yml"
    if candidate.is_file():
        guess = load_from_manifest(candidate)
        if guess:
            return guess, candidate
    return None, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omniagents-studio",
        description="OmniAgents Studio: browse traces and run evaluations",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the Studio web UI")
    serve.add_argument(
        "--host", default=None, help="Host interface to bind (default: 127.0.0.1)"
    )
    serve.add_argument(
        "--port", type=int, default=7860, help="Port to bind (default: 7860)"
    )
    serve.add_argument(
        "--db-path",
        dest="db_path",
        default=None,
        help="Path to a specific SQLite trace database (optional)",
    )
    serve.add_argument(
        "--project",
        default=None,
        help=(
            "Trace project under ~/.omniagents/traces (or OMNIAGENTS_HOME), or a "
            "project.yml / project directory (default: ./project.yml)"
        ),
    )
    serve.add_argument(
        "--token", dest="auth_token", default=None, help="Bearer token for API access"
    )
    serve.add_argument(
        "--dev",
        action="store_true",
        help="Run with the Studio web dev server (auto rebuild UI)",
    )
    serve.add_argument(
        "--dev-host",
        default=None,
        help="Host for the Studio web dev server (default: match --host)",
    )
    serve.add_argument(
        "--dev-port",
        type=int,
        default=5173,
        help="Port for the Studio web dev server (default: 5173)",
    )

    export = sub.add_parser("export", help="Export a project's traces")
    export.add_argument("output", help="Output file path")
    export.add_argument(
        "--format",
        choices=["json", "csv", "parquet"],
        default="json",
        help="Export format (default: json)",
    )
    export.add_argument("--project", default=None, help="Project to export")

    show = sub.add_parser("show", help="Show where a project's traces live")
    show.add_argument("--project", default=None, help="Project to show")
    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        sys.exit(1)

    from omniagents_studio import server

    project, manifest_path = resolve_project(args.project)

    if args.command == "serve":
        host = args.host or DEFAULT_BIND_HOST
        try:
            server.serve(
                host=host,
                port=args.port,
                db_path=args.db_path,
                project=project,
                auth_token=args.auth_token,
                dev=args.dev,
                dev_host=args.dev_host or host,
                dev_port=args.dev_port,
                manifest_path=str(manifest_path) if manifest_path else None,
            )
        except Exception as exc:
            print(f"Error starting Studio server: {exc}")
            traceback.print_exc()
            sys.exit(1)
        return

    if not project:
        print(
            "Error: Provide --project <name> or run from a directory containing "
            "project.yml."
        )
        sys.exit(2)

    if args.command == "export":
        try:
            server.export(args.output, format=args.format, project=project)
        except Exception as exc:
            print(f"Error exporting traces: {exc}")
            traceback.print_exc()
            sys.exit(1)
    elif args.command == "show":
        server.show(project)


if __name__ == "__main__":
    main()
