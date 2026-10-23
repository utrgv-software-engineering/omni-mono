"""Evaluation conversations are saved where the project's runtime keeps that
agent's conversations, so Studio's Logs lists them for review."""

from pathlib import Path

from omniagents.core.eval.targets import _store_identity
from omniagents.core.project.runtime import load_project


def _project(root: Path, manifest: str) -> Path:
    (root / "project.yml").write_text(manifest)
    agent = root / "agents" / "main_helper" / "agent.yml"
    agent.parent.mkdir(parents=True)
    agent.write_text("name: main_helper\ninstructions: Help.\n")
    return agent


def test_the_store_is_the_runtimes_for_a_declared_product(tmp_path):
    agent = _project(tmp_path, "product:\n  name: Demo App\nagents:\n  entrypoint: main_helper\n")
    project, key = _store_identity(agent, tmp_path)
    assert project == load_project(str(tmp_path)).product.slug
    assert key == "main_helper"


def test_the_store_is_the_runtimes_without_a_product_section(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    agent = _project(root, "agents:\n  entrypoint: main_helper\n")
    project, _ = _store_identity(agent, root)
    assert project == load_project(str(root)).product.slug != "default"
