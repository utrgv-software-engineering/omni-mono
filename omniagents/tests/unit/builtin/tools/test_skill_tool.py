import json
import types

import pytest

from omniagents.builtin.tools import skill_tool
from omniagents.core.skills import set_skill_roots_provider


@pytest.fixture(autouse=True)
def _clear_provider():
    set_skill_roots_provider(None)
    yield
    set_skill_roots_provider(None)


def _ctx(workspace_root):
    return types.SimpleNamespace(context={"workspace_root": str(workspace_root)})


def _write_skill(root, name, body="Body\n"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(f"---\nname: {name}\ndescription: test\n---\n\n{body}")
    return p


@pytest.mark.asyncio
async def test_load_skill_default_root(tmp_path):
    default_root = tmp_path / ".omniagents" / "skills"
    _write_skill(default_root, "my-skill", body="Do the thing.\n")

    out = await skill_tool.load_skill._original_func(_ctx(tmp_path), "my-skill")
    assert "Do the thing." in str(out)
    assert out.ui_metadata["metadata"]["success"] is True


@pytest.mark.asyncio
async def test_load_skill_not_found_lists_searched_paths(tmp_path):
    out = await skill_tool.load_skill._original_func(_ctx(tmp_path), "missing")
    text = str(out)
    assert "not found" in text
    assert ".omniagents" in text
    assert out.ui_metadata["metadata"]["success"] is False


@pytest.mark.asyncio
async def test_load_skill_uses_installed_provider(tmp_path):
    custom_root = tmp_path / "custom" / "skills"
    _write_skill(custom_root, "provided-skill", body="Provider wins.\n")
    set_skill_roots_provider(lambda workspace_root: [custom_root])

    out = await skill_tool.load_skill._original_func(_ctx(tmp_path), "provided-skill")
    assert "Provider wins." in str(out)


@pytest.mark.asyncio
async def test_provider_order_earlier_root_wins(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_skill(first, "dup", body="from first\n")
    _write_skill(second, "dup", body="from second\n")
    set_skill_roots_provider(lambda workspace_root: [first, second])

    out = await skill_tool.load_skill._original_func(_ctx(tmp_path), "dup")
    assert "from first" in str(out)


def test_failing_provider_degrades_to_default(tmp_path):
    from omniagents.core.skills import skill_roots

    def boom(workspace_root):
        raise RuntimeError("boom")

    set_skill_roots_provider(boom)
    roots = skill_roots(str(tmp_path))
    assert roots == [tmp_path / ".omniagents" / "skills"]
