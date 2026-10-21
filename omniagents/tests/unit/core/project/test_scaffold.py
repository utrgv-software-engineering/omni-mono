from pathlib import Path

import pytest
import yaml

from omniagents._version import __version__ as OMNIAGENTS_VERSION
from omniagents.core.project.scaffold import (
    _render_template,
    _write_template,
    _sanitize_name,
    scaffold_project,
    scaffold_agent,
    scaffold_tool,
    delete_agent,
    scaffold_guardrail,
)


def test_sanitize_name_variants():
    assert _sanitize_name("Test Agent!") == "test_agent"
    assert _sanitize_name("  123 Hello!@# ") == "a_123_hello"
    assert _sanitize_name("__") == "agent"


def test_render_template_replaces_tokens():
    text = _render_template("project/README.md", sanitized="demo")
    assert "demo" in text


def test_render_template_missing_raises():
    with pytest.raises(FileNotFoundError):
        _render_template("missing/template.txt", sanitized="demo")


def test_write_template_force_behavior(tmp_path):
    target = tmp_path / "file.txt"
    created = []
    _write_template(target, "project/README.md", False, created, sanitized="demo")
    original = target.read_text(encoding="utf-8")
    assert created == [str(target)]
    target.write_text("custom", encoding="utf-8")
    created = []
    _write_template(target, "project/README.md", False, created, sanitized="demo")
    assert target.read_text(encoding="utf-8") == "custom"
    assert created == []
    _write_template(target, "project/README.md", True, created, sanitized="demo")
    assert "demo" in target.read_text(encoding="utf-8")
    assert str(target) in created


def test_scaffold_project_creates_expected_files(tmp_path):
    created, project_dir, sanitized = scaffold_project(
        "Cool Agent",
        parent_dir=str(tmp_path),
        model="gpt-4.1",
        include_samples=False,
    )
    project_path = Path(project_dir)
    assert sanitized == "cool_agent"
    assert project_path.is_dir()
    assert (project_path / "project.yml").is_file()
    assert (project_path / "agents" / sanitized / "agent.yml").is_file()
    assert (project_path / "agents" / sanitized / "instructions.md").is_file()
    assert (project_path / "tools" / "__init__.py").is_file()
    assert not (project_path / "tools" / f"{sanitized}_tools.py").exists()
    assert all(f"{sanitized}_tools.py" not in entry for entry in created)
    pyproject = (project_path / "pyproject.toml").read_text(encoding="utf-8")
    assert OMNIAGENTS_VERSION in pyproject
    config_content = (project_path / sanitized / "config.py").read_text(
        encoding="utf-8"
    )
    assert sanitized.replace("_", "-") in config_content
    camel = "".join(part.capitalize() for part in sanitized.split("_"))
    assert camel in config_content


def test_scaffold_project_creates_battery_agents_and_shared_fragments(tmp_path):
    """The scaffold is the battery carrier: starter recapper/compactor/
    session_summarizer/worker agents plus the agents/_shared fragments are
    product-owned content the framework no longer ships at runtime."""
    _, project_dir, sanitized = scaffold_project(
        "Battery Project", parent_dir=str(tmp_path), include_samples=False
    )
    agents = Path(project_dir) / "agents"

    for key in ("recapper", "compactor", "session_summarizer", "worker"):
        agent_yml = agents / key / "agent.yml"
        instructions = agents / key / "instructions.md"
        assert agent_yml.is_file(), key
        assert instructions.is_file(), key
        doc = yaml.safe_load(agent_yml.read_text(encoding="utf-8"))
        assert doc["name"] == key
        assert doc["instructions_file"] == "instructions.md"

    # The starter prompts carry the battery texts.
    recap = (agents / "recapper" / "instructions.md").read_text(encoding="utf-8")
    assert "catch-up note" in recap
    compact = (agents / "compactor" / "instructions.md").read_text(encoding="utf-8")
    assert "context length limit" in compact
    assert "<analysis>" in compact
    session = (agents / "session_summarizer" / "instructions.md").read_text(
        encoding="utf-8"
    )
    assert session.startswith("Return ONLY valid JSON")
    for key in (
        "summary",
        "decisions",
        "constraints",
        "open_questions",
        "next_steps",
        "notable_errors",
    ):
        assert f'"{key}"' in session
    worker = (agents / "worker" / "instructions.md").read_text(encoding="utf-8")
    assert "Stay focused on the assigned task" in worker
    assert "## Output format" in worker
    # Flattened to plain markdown: no Jinja hooks the scaffolded project
    # does not feed.
    assert "{{" not in worker
    assert "{%" not in worker

    for fragment in (
        "personality.md",
        "proactiveness.md",
        "agents_md_spec.md",
        "task_execution.md",
    ):
        assert (agents / "_shared" / fragment).is_file(), fragment
    task_execution = (agents / "_shared" / "task_execution.md").read_text(
        encoding="utf-8"
    )
    assert "You are an autonomous agent." in task_execution
    assert "{%" not in task_execution

    # The entrypoint agent includes the shared fragments.
    entry = (agents / sanitized / "instructions.md").read_text(encoding="utf-8")
    for fragment in (
        "personality.md",
        "proactiveness.md",
        "agents_md_spec.md",
        "task_execution.md",
    ):
        assert f'{{% include "{fragment}" %}}' in entry


def test_scaffolded_project_loads_and_summarizer_conventions_resolve(tmp_path):
    """End-to-end: a fresh scaffold loads through the project loader, its
    entrypoint instructions render (fragment includes resolve), and the
    recap/compaction/session summarizer conventions find their agents."""
    import asyncio

    from omniagents.core.product import bootstrap_product, reset_product_info
    from omniagents.core.project.runtime import build_spec_from_project, load_project
    from omniagents.core.runtime.compaction import compaction_summarizer
    from omniagents.core.runtime.recap import recap_summarizer
    from omniagents.core.session.summarize import session_summarizer
    from omniagents.core.utils.jinja_instructions import process_instructions

    _, project_dir, sanitized = scaffold_project(
        "Conv Project", parent_dir=str(tmp_path), include_samples=False
    )

    ctx = load_project(str(Path(project_dir) / "project.yml"))
    spec = build_spec_from_project(ctx, agent_key=sanitized)
    renderer = process_instructions(
        spec.get_agent_instructions(),
        search_paths=spec.instructions_search_paths,
    )
    rendered = asyncio.run(renderer(None, None))
    assert "## Personality" in rendered
    assert "## Proactiveness" in rendered
    assert "## AGENTS.md spec" in rendered
    assert "## Task execution" in rendered

    # Battery agents load through the same registry-aware path.
    for key in ("recapper", "compactor", "session_summarizer", "worker"):
        battery_spec = build_spec_from_project(ctx, agent_key=key)
        text = battery_spec.get_agent_instructions()
        assert isinstance(text, str) and text.strip(), key
        assert "helpful assistant" not in text, key

    reset_product_info()
    try:
        bootstrap_product(Path(project_dir) / "project.yml")
        assert recap_summarizer() is not None
        assert compaction_summarizer() is not None
        assert session_summarizer() is not None
    finally:
        reset_product_info()


def test_scaffold_project_existing_dir_requires_force(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "flag.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        scaffold_project("existing", parent_dir=str(tmp_path))
    created, project_dir, sanitized = scaffold_project(
        "existing",
        parent_dir=str(tmp_path),
        force=True,
    )
    assert Path(project_dir).is_dir()
    assert sanitized == "existing"
    assert any(project_dir in entry for entry in created)


def test_scaffold_agent_force_and_duplicate(tmp_path):
    _, project_dir, base = scaffold_project("Base Project", parent_dir=str(tmp_path))
    created, _, sanitized = scaffold_agent(project_dir, "Second Agent")
    root = Path(project_dir)
    agent_dir = root / "agents" / sanitized
    assert agent_dir.is_dir()
    assert (agent_dir / "agent.yml").is_file()
    assert (agent_dir / "instructions.md").is_file()
    assert any("agent.yml" in entry for entry in created)
    with pytest.raises(FileExistsError):
        scaffold_agent(project_dir, "Second Agent")
    created_force, _, _ = scaffold_agent(project_dir, "Second Agent", force=True)
    assert any("agent.yml" in entry for entry in created_force)


def test_scaffold_tool_async_and_force(tmp_path):
    _, project_dir, _ = scaffold_project("Tool Project", parent_dir=str(tmp_path))
    created_async, _ = scaffold_tool(project_dir, "Echo Tool", make_async=True)
    root = Path(project_dir)
    tool_path = root / "tools" / "echo_tool.py"
    assert tool_path.is_file()
    content_async = tool_path.read_text(encoding="utf-8")
    assert "async def echo_tool" in content_async
    assert any("echo_tool.py" in entry for entry in created_async)
    with pytest.raises(FileExistsError):
        scaffold_tool(project_dir, "Echo Tool")
    created_sync, _ = scaffold_tool(project_dir, "Echo Tool", force=True)
    content_sync = tool_path.read_text(encoding="utf-8")
    assert "async def" not in content_sync
    assert "def echo_tool" in content_sync
    assert any("echo_tool.py" in entry for entry in created_sync)


def test_delete_agent_updates_entrypoint(tmp_path):
    _, project_dir, base = scaffold_project("Delete Project", parent_dir=str(tmp_path))
    _, _, other = scaffold_agent(project_dir, "Other Agent")
    root = Path(project_dir)
    project_yaml = root / "project.yml"
    # The scaffolded manifest declares its entrypoint explicitly.
    doc = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
    assert doc["agents"]["entrypoint"] == base
    removed, _, deleted = delete_agent(project_dir, base)
    assert removed == [str(root / "agents" / base)]
    assert deleted == base
    assert not (root / "agents" / base).exists()
    doc = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
    assert doc["agents"]["entrypoint"] == other
    # Deleting the last non-battery agent falls back to a battery agent
    # for the entrypoint (the guard only prevents an agent-less project).
    delete_agent(project_dir, other)
    doc = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
    battery = {"recapper", "compactor", "session_summarizer", "worker"}
    assert doc["agents"]["entrypoint"] in battery
    for key in ("recapper", "compactor", "session_summarizer"):
        delete_agent(project_dir, key)
    with pytest.raises(ValueError):
        delete_agent(project_dir, "worker")


def test_scaffold_guardrail_variants(tmp_path):
    _, project_dir, _ = scaffold_project("Guard Project", parent_dir=str(tmp_path))
    created_input, _ = scaffold_guardrail(
        project_dir, "input", "Check Input", make_async=True
    )
    root = Path(project_dir)
    sanitized_input = _sanitize_name("Check Input")
    input_path = root / "guardrails" / f"{sanitized_input}.py"
    assert input_path.is_file()
    content_input = input_path.read_text(encoding="utf-8")
    assert "input_guardrail" in content_input
    assert "async def" in content_input
    assert any(sanitized_input in entry for entry in created_input)
    created_output, _ = scaffold_guardrail(project_dir, "output", "Result Guard")
    sanitized_output = _sanitize_name("Result Guard")
    output_path = root / "guardrails" / f"{sanitized_output}.py"
    assert output_path.is_file()
    content_output = output_path.read_text(encoding="utf-8")
    assert "output_guardrail" in content_output
    assert "async def" not in content_output
    assert any(sanitized_output in entry for entry in created_output)
    with pytest.raises(FileExistsError):
        scaffold_guardrail(project_dir, "output", "Result Guard")
