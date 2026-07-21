"""Tests for {% include %} support in instruction templates.

Covers the include search-path semantics (same dir, _shared conventions,
nested includes), the missing-include error, and the byte-identical
regression guarantee for templates that use no includes.
"""

import asyncio
import textwrap

import pytest

from omniagents.core.utils.jinja_instructions import (
    InstructionIncludeNotFoundError,
    create_jinja2_instructions,
    instruction_search_paths,
    process_instructions,
    render_template,
)


def _render(instructions_fn, context=None):
    """Render a create_jinja2_instructions/process_instructions callable."""

    class _Wrapper:
        def __init__(self, ctx):
            self.context = ctx

    wrapper = _Wrapper(context) if context is not None else None
    return asyncio.run(instructions_fn(wrapper, None))


# ---------------------------------------------------------------------------
# instruction_search_paths semantics
# ---------------------------------------------------------------------------


def test_instruction_search_paths_order_and_dedupe(tmp_path):
    agent_dir = tmp_path / "agents" / "voice"
    extra = tmp_path / "fragments"
    paths = instruction_search_paths(agent_dir, [extra, agent_dir])
    assert paths == [
        agent_dir,
        agent_dir / "_shared",
        tmp_path / "agents" / "_shared",
        extra,
    ]


# ---------------------------------------------------------------------------
# include resolution
# ---------------------------------------------------------------------------


def test_include_from_same_dir(tmp_path):
    (tmp_path / "tone.md").write_text("Be kind.", encoding="utf-8")
    fn = create_jinja2_instructions(
        "Intro.\n{% include 'tone.md' %}\nOutro.",
        search_paths=instruction_search_paths(tmp_path),
    )
    assert _render(fn) == "Intro.\nBe kind.\nOutro."


def test_include_from_shared_sibling_dir(tmp_path):
    # Strict layout: agents/<key>/instructions.md + agents/_shared/core.md
    agent_dir = tmp_path / "agents" / "voice"
    shared_dir = tmp_path / "agents" / "_shared"
    agent_dir.mkdir(parents=True)
    shared_dir.mkdir(parents=True)
    (shared_dir / "core.md").write_text("Shared core rules.", encoding="utf-8")
    fn = create_jinja2_instructions(
        "{% include 'core.md' %}",
        search_paths=instruction_search_paths(agent_dir),
    )
    assert _render(fn) == "Shared core rules."


def test_include_own_dir_wins_over_shared(tmp_path):
    agent_dir = tmp_path / "agents" / "voice"
    shared_dir = tmp_path / "agents" / "_shared"
    agent_dir.mkdir(parents=True)
    shared_dir.mkdir(parents=True)
    (agent_dir / "frag.md").write_text("local", encoding="utf-8")
    (shared_dir / "frag.md").write_text("shared", encoding="utf-8")
    fn = create_jinja2_instructions(
        "{% include 'frag.md' %}",
        search_paths=instruction_search_paths(agent_dir),
    )
    assert _render(fn) == "local"


def test_nested_include(tmp_path):
    agent_dir = tmp_path / "agents" / "voice"
    shared_dir = tmp_path / "agents" / "_shared"
    agent_dir.mkdir(parents=True)
    shared_dir.mkdir(parents=True)
    (agent_dir / "outer.md").write_text(
        "outer>{% include 'inner.md' %}", encoding="utf-8"
    )
    (shared_dir / "inner.md").write_text("inner:{{ name }}", encoding="utf-8")
    fn = create_jinja2_instructions(
        "start {% include 'outer.md' %} end",
        search_paths=instruction_search_paths(agent_dir),
    )
    assert _render(fn, {"name": "Ada"}) == "start outer>inner:Ada end"


def test_include_sees_template_context(tmp_path):
    (tmp_path / "frag.md").write_text("Hello {{ user }}", encoding="utf-8")
    fn = process_instructions(
        "{% include 'frag.md' %}",
        search_paths=instruction_search_paths(tmp_path),
    )
    assert _render(fn, {"user": "Eve"}) == "Hello Eve"


def test_render_template_supports_includes(tmp_path):
    (tmp_path / "frag.md").write_text("frag", encoding="utf-8")
    out = render_template(
        "[{% include 'frag.md' %}]",
        silent_undefined=True,
        search_paths=instruction_search_paths(tmp_path),
    )
    assert out == "[frag]"


# ---------------------------------------------------------------------------
# missing include errors
# ---------------------------------------------------------------------------


def test_missing_include_error_names_searched_paths(tmp_path):
    agent_dir = tmp_path / "agents" / "voice"
    agent_dir.mkdir(parents=True)
    paths = instruction_search_paths(agent_dir)
    fn = create_jinja2_instructions(
        "{% include 'nope.md' %}", search_paths=paths
    )
    with pytest.raises(InstructionIncludeNotFoundError) as excinfo:
        _render(fn)
    msg = str(excinfo.value)
    assert "'nope.md'" in msg
    for p in paths:
        assert str(p) in msg


def test_missing_include_error_via_render_template(tmp_path):
    with pytest.raises(InstructionIncludeNotFoundError) as excinfo:
        render_template(
            "{% include 'gone.md' %}",
            search_paths=[tmp_path],
        )
    assert "'gone.md'" in str(excinfo.value)
    assert str(tmp_path) in str(excinfo.value)


def test_include_without_search_paths_raises_clear_error():
    # Instructions built in Python (no file) have no loader; using an
    # include there should produce our error, not a bare jinja2 TypeError.
    fn = create_jinja2_instructions("{% include 'x.md' %}")
    with pytest.raises(InstructionIncludeNotFoundError) as excinfo:
        _render(fn)
    assert "No include search paths are configured" in str(excinfo.value)


def test_render_template_without_search_paths_keeps_fallback():
    # Historical behavior: render_template swallows errors and returns the
    # raw template when no search paths were supplied.
    raw = "{% include 'x.md' %}"
    assert render_template(raw, silent_undefined=True) == raw


# ---------------------------------------------------------------------------
# backward-compatibility regression: no includes => byte-identical output
# ---------------------------------------------------------------------------

_REGRESSION_TEMPLATE = textwrap.dedent(
    """\
    # Agent

    Hello {{ name }}!
    {%- if role %}
    Role: {{ role }}
    {%- endif %}
    {% for item in items %}- {{ item }}
    {% endfor %}
    Missing renders empty: [{{ not_defined }}]
    JSON: {{ payload | tojson }}
    """
)

# Exact output of the pre-include implementation (Environment() with
# SilentUndefined and the tojson filter, no loader) for the context below.
# Captured by rendering this template with the pre-change
# create_jinja2_instructions (git main) — do not regenerate from current code.
_REGRESSION_EXPECTED = (
    "# Agent\n"
    "\n"
    "Hello Ada!\n"
    "Role: pilot\n"
    "- a\n"
    "- b\n"
    "\n"
    "Missing renders empty: []\n"
    'JSON: {"k": 1}'
)

_REGRESSION_CONTEXT = {
    "name": "Ada",
    "role": "pilot",
    "items": ["a", "b"],
    "payload": {"k": 1},
}


def test_no_include_regression_byte_identical_without_search_paths():
    fn = create_jinja2_instructions(_REGRESSION_TEMPLATE)
    assert _render(fn, dict(_REGRESSION_CONTEXT)) == _REGRESSION_EXPECTED


def test_no_include_regression_byte_identical_with_search_paths(tmp_path):
    # Providing search paths must not change rendering of templates that
    # use no includes.
    fn = create_jinja2_instructions(
        _REGRESSION_TEMPLATE,
        search_paths=instruction_search_paths(tmp_path),
    )
    assert _render(fn, dict(_REGRESSION_CONTEXT)) == _REGRESSION_EXPECTED
