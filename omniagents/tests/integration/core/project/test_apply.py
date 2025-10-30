from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omniagents.core.project.apply import apply_agent_instructions, apply_judge_updates


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _write(
        root / "project.yml",
        """name: Apply Fixture
paths:
  agents: agents
  evaluations: evaluations
agents:
  entrypoint: alpha
""",
    )
    _write(
        root / "agents" / "alpha" / "agent.yml",
        """name: alpha
model: gpt-4o
instructions: initial
""",
    )
    return root


def test_apply_agent_instructions_updates_file(tmp_path: Path):
    root = _build_project(tmp_path)
    agent_yaml = root / "agents" / "alpha" / "agent.yml"
    agent_yaml.write_text(
        """name: alpha
model: gpt-4o
instructions_file: instructions.txt
""",
        encoding="utf-8",
    )
    instructions_file = root / "agents" / "alpha" / "instructions.txt"
    instructions_file.write_text("old", encoding="utf-8")
    apply_agent_instructions(str(root), "alpha", "New instructions\nLine two")
    assert instructions_file.read_text(encoding="utf-8") == "New instructions\nLine two"


def test_apply_agent_instructions_updates_inline(tmp_path: Path):
    root = _build_project(tmp_path)
    apply_agent_instructions(str(root), "alpha", "Inline override\nNext line")
    agent_yaml = root / "agents" / "alpha" / "agent.yml"
    data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
    assert data["instructions"] == "Inline override\nNext line"


def test_apply_agent_instructions_noop_when_none(tmp_path: Path):
    root = _build_project(tmp_path)
    agent_yaml = root / "agents" / "alpha" / "agent.yml"
    original = agent_yaml.read_text(encoding="utf-8")
    apply_agent_instructions(str(root), "alpha", None)
    assert agent_yaml.read_text(encoding="utf-8") == original


def test_apply_agent_instructions_uses_cwd_fallback(tmp_path: Path, monkeypatch):
    root = _build_project(tmp_path)
    agent_yaml = root / "agents" / "alpha" / "agent.yml"
    agent_yaml.write_text(
        """name: alpha
model: gpt-4o
instructions_file: fallback.txt
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    apply_agent_instructions("missing/path.yml", "alpha", "Fallback text")
    fallback_file = root / "agents" / "alpha" / "fallback.txt"
    assert fallback_file.read_text(encoding="utf-8") == "Fallback text"


def test_apply_agent_instructions_absolute_path(tmp_path: Path):
    root = _build_project(tmp_path)
    abs_file = tmp_path / "absolute.txt"
    agent_yaml = root / "agents" / "alpha" / "agent.yml"
    agent_yaml.write_text(
        f"""name: alpha
model: gpt-4o
instructions_file: {abs_file}
""",
        encoding="utf-8",
    )
    apply_agent_instructions(str(root), "alpha", "Absolute update")
    assert abs_file.read_text(encoding="utf-8") == "Absolute update"


def test_apply_agent_instructions_missing_manifest_raises(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "nowhere"
    with pytest.raises(FileNotFoundError):
        apply_agent_instructions(str(missing), "alpha", "ignored")


def _build_judges_fixture(tmp_path: Path) -> Path:
    root = _build_project(tmp_path)
    base = root / "evaluations" / "judges"
    _write(
        base / "file_based.yml",
        """judges:
  file_target:
    agent:
      instructions_file: instructions.md
    input:
      template_file: templates/prompt.txt
""",
    )
    _write(base / "instructions.md", "old agent")
    _write(base / "templates" / "prompt.txt", "old template")
    _write(
        base / "ref_source.yml",
        """judges:
  ref_target:
    agent_ref: ref_agent.yml
  ref_inline_target:
    agent_ref: ref_inline.yml
""",
    )
    _write(
        base / "ref_agent.yml",
        """agent:
  instructions_file: agent_text.txt
input:
  template_file: template_text.txt
""",
    )
    _write(base / "agent_text.txt", "ref agent")
    _write(base / "template_text.txt", "ref template")
    _write(
        base / "ref_inline.yml",
        """agent:
  instructions: ref inline
input:
  template: inline template
""",
    )
    _write(
        base / "inline.yml",
        """judges:
  inline_target:
    agent:
      instructions: keep
    input:
      template: old
""",
    )
    _write(
        base / "list.yml",
        """judges:
  - name: list_target
    agent:
      instructions_file: list_instructions.txt
    input:
      template_file: list_template.txt
""",
    )
    _write(base / "list_template.txt", "list template")
    return root


def test_apply_judge_updates_writes_files(tmp_path: Path):
    root = _build_judges_fixture(tmp_path)
    apply_judge_updates(str(root), "file_target", "Agent update", "Template update")
    judge_dir = root / "evaluations" / "judges"
    assert (judge_dir / "instructions.md").read_text(encoding="utf-8") == "Agent update"
    assert (judge_dir / "templates" / "prompt.txt").read_text(
        encoding="utf-8"
    ) == "Template update"


def test_apply_judge_updates_respects_refs(tmp_path: Path):
    root = _build_judges_fixture(tmp_path)
    apply_judge_updates(str(root), "ref_target", "Ref instruction", "Ref template")
    judge_dir = root / "evaluations" / "judges"
    assert (judge_dir / "agent_text.txt").read_text(
        encoding="utf-8"
    ) == "Ref instruction"
    assert (judge_dir / "template_text.txt").read_text(
        encoding="utf-8"
    ) == "Ref template"


def test_apply_judge_updates_inlines_when_no_files(tmp_path: Path):
    root = _build_judges_fixture(tmp_path)
    apply_judge_updates(str(root), "inline_target", "Inline rewrite", "Inline template")
    judge_path = root / "evaluations" / "judges" / "inline.yml"
    doc = yaml.safe_load(judge_path.read_text(encoding="utf-8"))
    target = doc["judges"]["inline_target"]
    assert target["agent"]["instructions"] == "Inline rewrite"
    assert target["input"]["template"] == "Inline template"


def test_apply_judge_updates_template_only(tmp_path: Path):
    root = _build_judges_fixture(tmp_path)
    apply_judge_updates(str(root), "inline_target", None, "Only template")
    judge_path = root / "evaluations" / "judges" / "inline.yml"
    doc = yaml.safe_load(judge_path.read_text(encoding="utf-8"))
    target = doc["judges"]["inline_target"]
    assert target["agent"]["instructions"] == "keep"
    assert target["input"]["template"] == "Only template"


def test_apply_judge_updates_handles_list_judges(tmp_path: Path):
    root = _build_judges_fixture(tmp_path)
    apply_judge_updates(str(root), "list_target", "List instructions", "List template")
    judge_dir = root / "evaluations" / "judges"
    assert (judge_dir / "list_instructions.txt").read_text(
        encoding="utf-8"
    ) == "List instructions"
    assert (judge_dir / "list_template.txt").read_text(
        encoding="utf-8"
    ) == "List template"


def test_apply_judge_updates_missing_judge_noop(tmp_path: Path):
    root = _build_judges_fixture(tmp_path)
    before = (root / "evaluations" / "judges" / "instructions.md").read_text(
        encoding="utf-8"
    )
    apply_judge_updates(str(root), "absent", "Ignore", "Ignore")
    after = (root / "evaluations" / "judges" / "instructions.md").read_text(
        encoding="utf-8"
    )
    assert after == before


def test_apply_judge_updates_ref_inline(tmp_path: Path):
    root = _build_judges_fixture(tmp_path)
    apply_judge_updates(
        str(root), "ref_inline_target", "Inline fresh", "Template fresh"
    )
    ref_inline = root / "evaluations" / "judges" / "ref_inline.yml"
    doc = yaml.safe_load(ref_inline.read_text(encoding="utf-8"))
    assert doc["agent"]["instructions"] == "Inline fresh"
    assert doc["input"]["template"] == "Template fresh"


def test_apply_judge_updates_accepts_manifest_path(tmp_path: Path):
    root = _build_judges_fixture(tmp_path)
    apply_judge_updates(str(root / "project.yml"), "file_target", "Via manifest", None)
    judge_dir = root / "evaluations" / "judges"
    assert (judge_dir / "instructions.md").read_text(encoding="utf-8") == "Via manifest"
