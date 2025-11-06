import textwrap
from types import SimpleNamespace

import pytest

from omniagents.core.eval.gepa_agent_adapter import OmniAgentsAgentAdapter


def _basic_config(tmp_path):
    cfg_dir = tmp_path / "agent"
    cfg_dir.mkdir()
    cfg = cfg_dir / "compiled.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            name: Unit Agent
            label: Unit Label
            evaluation:
              failure_categories:
                unit:
                  - numeric_accuracy
            """
        ),
        encoding="utf-8",
    )
    return cfg


def test_score_fraction_mode_partial_credit(tmp_path):
    """Fraction mode should grant proportional credit based on passing measures."""
    adapter = OmniAgentsAgentAdapter(
        _basic_config(tmp_path), "unit", score_mode="fraction"
    )
    measures = [
        {"name": "numeric_accuracy", "passed": True},
        {"name": "formatting", "passed": False},
        {"name": "tooling", "passed": True},
    ]
    assert adapter._score_from_measures(measures) == pytest.approx(2 / 3, rel=1e-6)


def test_score_all_or_nothing_requires_all_pass(tmp_path):
    """All-or-nothing mode should only award a score when every measure passes."""
    adapter = OmniAgentsAgentAdapter(
        _basic_config(tmp_path), "unit", score_mode="all_or_nothing"
    )
    failing = [
        {"name": "numeric_accuracy", "passed": True},
        {"name": "formatting", "passed": False},
    ]
    assert adapter._score_from_measures(failing) == 0.0
    passing = [
        {"name": "numeric_accuracy", "passed": True},
        {"name": "formatting", "passed": True},
    ]
    assert adapter._score_from_measures(passing) == 1.0


def test_score_handles_empty_measure_list(tmp_path):
    """Scoring should default to zero when no measures are available."""
    adapter = OmniAgentsAgentAdapter(
        _basic_config(tmp_path), "unit", score_mode="fraction"
    )
    assert adapter._score_from_measures([]) == 0.0


def test_tools_schema_markdown_includes_tool_details(tmp_path):
    """Tool schema markdown should include headings, descriptions, and formatted JSON."""
    adapter = OmniAgentsAgentAdapter(_basic_config(tmp_path), "unit")
    tool_a = SimpleNamespace(
        name="alpha",
        description="Primary alpha tool",
        params_json_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
        },
    )
    tool_b = SimpleNamespace(name="beta", description="", params_json_schema={})
    adapter._load_spec = lambda: SimpleNamespace(available_tools=[tool_a, tool_b])

    rendered = adapter._get_tools_schema_md()
    assert "## alpha" in rendered and "Primary alpha tool" in rendered
    assert "```json" in rendered and '"value"' in rendered
    assert "## beta" in rendered

    adapter._load_spec = lambda: SimpleNamespace(available_tools=[])
    assert adapter._get_tools_schema_md() == rendered


def test_extract_fenced_blocks_returns_inner_payload(tmp_path):
    """Fenced extraction should strip language hints and return inner content."""
    adapter = OmniAgentsAgentAdapter(_basic_config(tmp_path), "unit")
    json_block = """```json\n{\"answer\": \"value\"}\n```"""
    yaml_block = """```yaml\nkey: value\n```"""
    plain_text = "No fencing here"

    assert adapter._extract_fenced(json_block) == '{"answer": "value"}'
    assert adapter._extract_fenced(yaml_block) == "key: value"
    assert adapter._extract_fenced(plain_text) == plain_text
