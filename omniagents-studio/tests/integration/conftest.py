from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from omniagents.core.project.runtime import build_spec_from_project, load_project
from omniagents_studio.api import create_api_app
from omniagents.core.tracing.storage import SQLiteStorage


@pytest.fixture
def studio_api_env(tmp_path: Path):
    fixtures_root = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "projects"
        / "studio_playground"
    )
    project_dir = tmp_path / "project"
    shutil.copytree(fixtures_root, project_dir)
    storage_path = tmp_path / "storage" / "demo.db"
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage = SQLiteStorage(str(storage_path))
    manifest_path = project_dir / "project.yml"
    ctx = load_project(str(manifest_path))
    app_resources = {
        "manifest_path": str(manifest_path),
        "project_name": ctx.product.slug,
        "agent_specs": {ctx.entrypoint: build_spec_from_project(ctx)},
        "entry_agent_key": ctx.entrypoint,
    }
    app = create_api_app(
        storage=storage,
        default_project="demo",
        project_resources=app_resources,
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
