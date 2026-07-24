"""Product prompt-template files: declaration, resolution, precedence.

Covers ``omniagents.core.project.prompts`` plus the seams that consume
it — the goal prompt builders (``goal_initial.md`` / ``goal_tick.md``)
and the conventional default-loop-prompt chain (``loop.md``).
"""

from dataclasses import replace

import pytest

from omniagents.core.autonomy import goal as goal_mod
from omniagents.core.autonomy import loop as loop_mod
from omniagents.core.product import (
    product_info_from_mapping,
    reset_product_info,
    set_product_info,
)
from omniagents.core.project.prompts import (
    product_prompt_template,
    product_prompts_dirs,
    render_prompt_template,
)


@pytest.fixture
def fake_product(tmp_path):
    """Install a product identity whose manifest lives in tmp_path, with
    a conventional prompts/ dir. Yields the project root."""
    manifest = tmp_path / "project.yml"
    manifest.write_text("name: acme\n", encoding="utf-8")
    (tmp_path / "prompts").mkdir()
    info = replace(
        product_info_from_mapping(
            {"name": "acme", "prog": "acme", "config_dir": "acme_cfg"}
        ),
        manifest_path=manifest,
    )
    set_product_info(info)
    yield tmp_path
    reset_product_info()


@pytest.fixture(autouse=True)
def _reset_builders():
    yield
    goal_mod.set_goal_prompt_builders(initial=None, tick=None)
    loop_mod.set_loop_default_prompt_resolver(None)


# ---------------------------------------------------------------------------
# Declaration + file resolution
# ---------------------------------------------------------------------------


def test_prompts_dir_defaults_to_conventional_sibling(fake_product):
    dirs = product_prompts_dirs()
    assert dirs == [(fake_product / "prompts").resolve()]


def test_prompts_dir_declared_key_overrides_default(fake_product):
    (fake_product / "project.yml").write_text(
        "name: acme\npaths:\n  prompts: promptdata\n", encoding="utf-8"
    )
    (fake_product / "promptdata").mkdir()
    (fake_product / "promptdata" / "loop.md").write_text(
        "declared dir\n", encoding="utf-8"
    )
    assert product_prompts_dirs() == [(fake_product / "promptdata").resolve()]
    assert product_prompt_template("loop.md") == "declared dir"


def test_missing_template_and_missing_manifest_yield_none(fake_product):
    assert product_prompt_template("goal_initial.md") is None
    reset_product_info()  # no product => no manifest (discovery disabled)
    assert product_prompts_dirs() == []
    assert product_prompt_template("goal_initial.md") is None


def test_render_prompt_template_uses_instructions_env_and_strips():
    out = render_prompt_template(
        "Goal: {{ goal }} in {{ max_turns }} turns.\n", goal="ship", max_turns=7
    )
    assert out == "Goal: ship in 7 turns."
    # Silent-undefined, like agent instructions.
    assert render_prompt_template("A{{ missing }}B") == "AB"


# ---------------------------------------------------------------------------
# Goal builders: explicit install > product template > framework default
# ---------------------------------------------------------------------------


def test_goal_builders_render_product_templates(fake_product):
    (fake_product / "prompts" / "goal_initial.md").write_text(
        "INITIAL {{ goal }} budget={{ max_turns }}\n", encoding="utf-8"
    )
    (fake_product / "prompts" / "goal_tick.md").write_text(
        "TICK {{ goal }}/{{ max_turns }}\n", encoding="utf-8"
    )
    assert goal_mod.build_initial_prompt("ship it", 9) == "INITIAL ship it budget=9"
    assert goal_mod.build_tick_prompt(goal="ship it", max_turns=9) == "TICK ship it/9"


def test_goal_templates_resolve_independently(fake_product):
    # Only the tick template exists: initial falls back to the framework
    # default while tick renders from the file.
    (fake_product / "prompts" / "goal_tick.md").write_text(
        "TICK-ONLY {{ goal }}\n", encoding="utf-8"
    )
    initial = goal_mod.build_initial_prompt("ship it", 9)
    assert initial == goal_mod.default_goal_initial_prompt("ship it", 9)
    assert (
        goal_mod.build_tick_prompt(goal="ship it", max_turns=9) == "TICK-ONLY ship it"
    )


def test_explicit_goal_builders_beat_product_templates(fake_product):
    (fake_product / "prompts" / "goal_initial.md").write_text(
        "TEMPLATE {{ goal }}\n", encoding="utf-8"
    )
    (fake_product / "prompts" / "goal_tick.md").write_text(
        "TEMPLATE-TICK {{ goal }}\n", encoding="utf-8"
    )
    goal_mod.set_goal_prompt_builders(
        initial=lambda goal, max_turns: f"CODE {goal} {max_turns}",
        tick=lambda *, goal, max_turns: f"CODE-TICK {goal} {max_turns}",
    )
    assert goal_mod.build_initial_prompt("g", 3) == "CODE g 3"
    assert goal_mod.build_tick_prompt(goal="g", max_turns=3) == "CODE-TICK g 3"
    # Restoring the defaults (None) re-enables the template convention.
    goal_mod.set_goal_prompt_builders(initial=None, tick=None)
    assert goal_mod.build_initial_prompt("g", 3) == "TEMPLATE g"


def test_goal_defaults_without_product_templates(fake_product):
    assert goal_mod.build_initial_prompt(
        "g", 3
    ) == goal_mod.default_goal_initial_prompt("g", 3)
    assert goal_mod.build_tick_prompt(
        goal="g", max_turns=3
    ) == goal_mod.default_goal_tick_prompt(goal="g", max_turns=3)


# ---------------------------------------------------------------------------
# Loop default-prompt chain:
# workspace .<prog>/loop.md > <config>/loop.md > packaged loop.md > None
# ---------------------------------------------------------------------------


@pytest.fixture
def loop_layers(fake_product, tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".acme").mkdir(parents=True)
    xdg = tmp_path / "xdg"
    (xdg / "acme_cfg").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return {"root": fake_product, "ws": ws, "cfg": xdg / "acme_cfg"}


def test_loop_chain_full_precedence(loop_layers):
    (loop_layers["root"] / "prompts" / "loop.md").write_text(
        "packaged body\n", encoding="utf-8"
    )
    (loop_layers["cfg"] / "loop.md").write_text("config body\n", encoding="utf-8")
    (loop_layers["ws"] / ".acme" / "loop.md").write_text(
        "workspace body\n", encoding="utf-8"
    )

    ws = str(loop_layers["ws"])
    assert loop_mod.resolve_default_loop_prompt(ws) == "workspace body"
    (loop_layers["ws"] / ".acme" / "loop.md").unlink()
    assert loop_mod.resolve_default_loop_prompt(ws) == "config body"
    (loop_layers["cfg"] / "loop.md").unlink()
    assert loop_mod.resolve_default_loop_prompt(ws) == "packaged body"
    (loop_layers["root"] / "prompts" / "loop.md").unlink()
    assert loop_mod.resolve_default_loop_prompt(ws) is None


def test_loop_explicit_resolver_beats_the_chain(loop_layers):
    (loop_layers["root"] / "prompts" / "loop.md").write_text(
        "packaged body\n", encoding="utf-8"
    )
    loop_mod.set_loop_default_prompt_resolver(lambda _ws: "resolver body")
    assert loop_mod.resolve_default_loop_prompt(str(loop_layers["ws"])) == (
        "resolver body"
    )
    loop_mod.set_loop_default_prompt_resolver(None)
    assert loop_mod.resolve_default_loop_prompt(str(loop_layers["ws"])) == (
        "packaged body"
    )


def test_loop_chain_without_workspace_root(loop_layers):
    (loop_layers["root"] / "prompts" / "loop.md").write_text(
        "packaged body\n", encoding="utf-8"
    )
    assert loop_mod.resolve_default_loop_prompt(None) == "packaged body"
