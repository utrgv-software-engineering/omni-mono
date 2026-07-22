from __future__ import annotations

import shutil
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents.studio.server.api import create_api_app
from omniagents.studio.tracing.storage import SQLiteStorage


@pytest.fixture
def studio_api_env(tmp_path: Path):
    fixtures_root = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "projects"
        / "studio_playground"
    )
    project_dir = tmp_path / "project"
    shutil.copytree(fixtures_root, project_dir)
    storage_path = tmp_path / "storage" / "demo.db"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorage(str(storage_path))
    app_resources: dict[str, object] = {}
    manifest_path = project_dir / "project.yml"
    ctx = load_project(str(manifest_path))
    base_spec = build_spec_from_project(ctx)
    app_resources["manifest_path"] = str(manifest_path)
    app_resources["project_name"] = ctx.product.slug
    app_resources["base_spec"] = base_spec
    app_resources["agent_specs"] = {ctx.entrypoint: base_spec}
    app_resources["entry_agent_key"] = ctx.entrypoint
    app_resources["tool_registry"] = ctx.tool_registry
    app_resources["guardrails_registry"] = ctx.guardrails_registry
    app_resources["default_model_settings"] = base_spec.model_settings or {}
    app_resources["default_safe_agent"] = bool(
        getattr(base_spec, "use_safe_agent", False)
    )
    app_resources["default_safe_agent_options"] = (
        asdict(base_spec.safe_agent_options) if base_spec.safe_agent_options else {}
    )
    app_resources["default_guardrails"] = {"input": [], "output": []}
    app, _ = create_api_app(
        storage=storage,
        default_project="demo",
        playground_resources=app_resources,
        auth_token="secret",
    )
    client_ctx = TestClient(app)
    client = client_ctx.__enter__()
    try:
        yield SimpleNamespace(
            app=app,
            client=client,
            portal=client.portal,
            project_dir=project_dir,
            manifest_path=manifest_path,
        )
    finally:
        client_ctx.__exit__(None, None, None)
        storage.close()
