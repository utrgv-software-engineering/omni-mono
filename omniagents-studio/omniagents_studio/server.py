"""Run the OmniAgents Studio server and export recorded traces."""

from __future__ import annotations

import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError

from omniagents.core.paths import get_traces_dir
from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.core.tracing.storage import SQLiteStorage

from .api import create_api_app

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def serve(
    host: str = "0.0.0.0",
    port: int = 7860,
    db_path: Optional[str] = None,
    project: Optional[str] = None,
    auth_token: Optional[str] = None,
    dev: bool = False,
    dev_host: Optional[str] = None,
    dev_port: int = 5173,
    manifest_path: Optional[str] = None,
):
    """Run the OmniAgents Studio tracing server."""
    import uvicorn

    traces_dir = get_traces_dir()
    storage = None
    active_project = None
    created_db = False
    dev_server_url: Optional[str] = None
    vite_process: Optional[subprocess.Popen] = None

    if db_path:
        resolved = Path(db_path).expanduser().resolve()
        existed = resolved.exists()
        storage = SQLiteStorage(str(resolved))
        created_db = not existed
        active_project = project or resolved.stem
    else:
        if project:
            resolved = traces_dir / f"{project}.db"
            existed = resolved.exists()
            storage = SQLiteStorage(str(resolved))
            created_db = not existed
            active_project = project
        else:
            db_files = sorted(traces_dir.glob("*.db"))
            if len(db_files) == 1:
                resolved = db_files[0]
                storage = SQLiteStorage(str(resolved))
                active_project = resolved.stem
                print(f"📂 Found one database, using: {active_project}")
            elif len(db_files) > 1:
                print("📂 Found multiple project databases:\n")
                for db in db_files:
                    try:
                        temp_storage = SQLiteStorage(str(db))
                        temp_storage.get_traces(limit=1)
                        temp_storage.close()
                        size_kb = db.stat().st_size / 1024
                        print(f"  • {db.stem:<20} ({size_kb:.1f} KB)")
                    except Exception:
                        print(f"  • {db.stem:<20}")

                print(
                    "\n✨ Serving all projects. Use the project selector in the UI to switch between them."
                )
                print(
                    "📌 To serve only a specific project, use: omniagents-studio serve --project <name>"
                )
            else:
                print(f"📭 No trace databases found in {traces_dir}")
                print(
                    "✨ Studio will create databases as traces are recorded or when --project is provided."
                )

    project_resources = None
    if manifest_path:
        try:
            project_context = load_project(manifest_path)
            agent_specs: Dict[str, Any] = {}
            for agent_key in sorted(project_context.agents_map.keys()):
                try:
                    agent_specs[agent_key] = build_spec_from_project(
                        project_context, agent_key
                    )
                except Exception as agent_exc:
                    logger.warning(
                        "Unable to load agent '%s': %s", agent_key, agent_exc
                    )
            project_resources = {
                "project_name": project or project_context.product.slug,
                "manifest_path": manifest_path,
                "agent_specs": agent_specs,
                "entry_agent_key": project_context.entrypoint,
            }
        except Exception as exc:
            logger.warning("Unable to load project manifest: %s", exc)
            project_resources = None

    if dev:
        fallback_host = host or "127.0.0.1"
        resolved_dev_host = (dev_host or fallback_host).strip() or "127.0.0.1"
        dev_server_url = f"http://{resolved_dev_host}:{dev_port}"
        web_dir = WEB_DIR
        package_manifest = web_dir / "package.json"
        if not package_manifest.exists():
            raise RuntimeError(f"Studio web project not found at {web_dir}")

        env = os.environ.copy()
        env.setdefault("BROWSER", "none")
        vite_cmd = [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            resolved_dev_host,
            "--port",
            str(dev_port),
        ]
        try:
            print(f"🛠️  Starting Studio web dev server at {dev_server_url} ...")
            vite_process = subprocess.Popen(vite_cmd, cwd=str(web_dir), env=env)
        except FileNotFoundError as exc:
            raise RuntimeError(
                "npm is required to run the Studio dev server. Ensure Node.js is installed."
            ) from exc

        try:
            start_time = time.time()
            while True:
                if vite_process.poll() is not None:
                    raise RuntimeError(
                        "Studio web dev server exited before becoming ready"
                    )
                try:
                    with urllib.request.urlopen(dev_server_url, timeout=1):
                        break
                except (URLError, HTTPError):
                    if time.time() - start_time > 30:
                        raise RuntimeError(
                            f"Timed out waiting for Studio web dev server at {dev_server_url}"
                        )
                    time.sleep(0.5)
        except Exception:
            if vite_process.poll() is None:
                vite_process.terminate()
                try:
                    vite_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    vite_process.kill()
            raise

        print(f"🛠️  Studio web dev server running at {dev_server_url}")

    app, _ = create_api_app(
        storage,
        default_project=active_project,
        dev_server_url=dev_server_url,
        project_resources=project_resources,
        auth_token=auth_token,
    )

    if storage:
        print(f"🚀 OmniAgents Studio tracing server running at http://{host}:{port}")
        print(f"💾 Database: {storage.db_path}")
        if created_db:
            print("🆕 Initialized new database")
    else:
        print(f"🚀 OmniAgents Studio tracing server running at http://{host}:{port}")
        print(f"📊 Multi-project mode - all databases in {traces_dir} are accessible")
    if dev and dev_server_url:
        print(f"🎨 UI (dev): {dev_server_url} (redirected from http://{host}:{port})")
    else:
        print(f"🎨 UI: http://{host}:{port}")
    if auth_token:
        print("🔐 Authentication: Enabled")

    try:
        uvicorn.run(
            app, host=host, port=port, proxy_headers=True, forwarded_allow_ips="*"
        )
    finally:
        if vite_process:
            vite_process.terminate()
            try:
                vite_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                vite_process.kill()


def export(output_path: str, format: str = "json", *, project: str) -> None:
    """Export a project's traces to a file."""
    target_project = project

    db_path = get_traces_dir() / f"{target_project}.db"
    if not db_path.exists():
        raise ValueError(f"Project database not found: {target_project}")
    storage = SQLiteStorage(str(db_path))

    traces = storage.get_traces(limit=10000)

    if format == "json":
        import json

        export_data = []
        for trace in traces:
            trace_export = trace.copy()
            trace_export["spans"] = storage.get_spans_for_trace(trace["trace_id"])
            export_data.append(trace_export)

        with open(output_path, "w") as handle:
            json.dump(export_data, handle, indent=2, default=str)

    elif format == "csv":
        import csv

        with open(output_path, "w", newline="") as handle:
            if traces:
                writer = csv.DictWriter(handle, fieldnames=traces[0].keys())
                writer.writeheader()
                writer.writerows(traces)

    elif format == "parquet":
        try:
            import pandas as pd

            df = pd.DataFrame(traces)
            df.to_parquet(output_path)
        except ImportError:
            raise ImportError(
                "pandas and pyarrow are required for parquet export. Install with: pip install pandas pyarrow"
            )

    else:
        raise ValueError(f"Unknown format: {format}")

    print(f"Exported {len(traces)} traces to {output_path}")
    storage.close()


def show(project: str) -> None:
    """Print where a project's traces live and how to view them."""
    db_path = get_traces_dir() / f"{project}.db"
    print(f"📊 OmniAgents Studio project: {project}")
    print(f"💾 Database: {db_path}")
    if not db_path.exists():
        print("📭 No traces recorded yet")
    print(f"\n💡 To view traces, run: omniagents-studio serve --project {project}")
