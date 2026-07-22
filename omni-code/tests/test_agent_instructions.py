"""Instruction-template composition tests.

omni and voice_agent share verbatim base sections via ``{% include %}``
fragments in ``omni_code/agents/_shared/`` (search order: anchor dir ->
anchor/_shared -> agents/_shared). Every fragment is product-owned — the
framework ships no instruction prose. The worker agent's system prompt
is its own ``instructions.md``: the generic worker protocol merged with
omni-code's coding delta as one file, with the
``additional_instructions`` context hook kept live for caller-supplied
extras (e.g. per-scenario eval variables).
"""

from pathlib import Path

import yaml

from omniagents.core.utils.jinja_instructions import (
    instruction_search_paths,
    render_template,
)

AGENTS_DIR = Path(__file__).resolve().parents[1] / "omni_code" / "agents"


def _render(agent_key: str, **extra) -> str:
    anchor = AGENTS_DIR / agent_key
    template = (anchor / "instructions.md").read_text(encoding="utf-8")
    return render_template(
        template,
        silent_undefined=True,
        search_paths=instruction_search_paths(anchor),
        current_date="2026-01-01",
        **extra,
    )


def test_omni_and_voice_share_fragment_sections_verbatim():
    omni = _render("omni")
    voice = _render("voice_agent")

    shared_probes = [
        "You are Omni Code, a highly-skilled full-stack software-engineering assistant.",
        "## AGENTS.md spec",
        "## Proactiveness",
        "## Personality",
        "## Following conventions",
        "## Code Style",
        "## Debugging Libraries, Frameworks, Dependencies",
        "## Task execution",
        "- Fix the problem at the root cause rather than applying surface-level patches, when possible.",
    ]
    for probe in shared_probes:
        assert probe in omni, probe
        assert probe in voice, probe

    # The full AGENTS.md spec section (a product-owned _shared fragment)
    # must be byte-identical in both renders.
    spec_fragment = (
        (AGENTS_DIR / "_shared" / "agents_md_spec.md")
        .read_text(encoding="utf-8")
        .rstrip("\n")
    )
    assert spec_fragment in omni
    assert spec_fragment in voice


def test_terminal_only_bullets_render_for_omni_not_voice():
    omni = _render("omni")
    voice = _render("voice_agent")

    terminal_only = [
        "- Showing user code and tool call details is allowed.",
        "- NEVER output inline citations like",
    ]
    for bullet in terminal_only:
        assert bullet in omni, bullet
        assert bullet not in voice, bullet

    # Per-agent deltas stay per-agent.
    assert "displayed on a command line interface" in omni
    assert "easy to speak aloud" in voice
    assert "## Voice Output" in voice
    assert "## Voice Output" not in omni


def test_instructions_use_no_framework_fragments():
    # The framework ships no instruction prose: every include in every
    # agent template resolves inside the project tree.
    for instructions in AGENTS_DIR.glob("*/instructions.md"):
        text = instructions.read_text(encoding="utf-8")
        assert 'include "builtin/' not in text, instructions
        assert "include 'builtin/" not in text, instructions


def test_worker_owns_its_prompt_with_coding_delta_inline():
    # The worker spec points at its own instructions.md — the framework
    # no longer installs any default worker prompt at spawn time.
    config = yaml.safe_load(
        (AGENTS_DIR / "worker" / "agent.yml").read_text(encoding="utf-8")
    )
    assert config["instructions_file"] == "instructions.md"

    from omni_code.agents.worker.context import build_worker_context

    context = build_worker_context({})
    context["worker_name"] = "worker-test-1"
    anchor = AGENTS_DIR / "worker"
    rendered = render_template(
        (anchor / "instructions.md").read_text(encoding="utf-8"),
        silent_undefined=True,
        search_paths=instruction_search_paths(anchor),
        **context,
    )

    # Generic worker protocol present.
    assert (
        "You are **worker-test-1**, an autonomous worker agent executing a "
        "focused subtask." in rendered
    )
    assert "## Task list" in rendered
    assert "## When stuck" in rendered
    # Coding delta present exactly once, under the (now inline) heading.
    assert rendered.count("# Additional Instructions") == 1
    assert rendered.count("Do not commit changes to git.") == 1
    assert (
        rendered.count("list every file you created or changed by absolute path")
        == 1
    )
    # Nothing duplicated from the generic protocol.
    assert rendered.count("Stay focused on the assigned task.") == 1


def test_worker_context_passes_caller_additional_instructions_through():
    # The delta lives in instructions.md now; the context factory forwards
    # caller extras untouched and the template hook renders them after it.
    from omni_code.agents.worker.context import build_worker_context

    context = build_worker_context({"additional_instructions": "Scenario extra."})
    assert context["additional_instructions"] == "Scenario extra."

    context["worker_name"] = "worker-test-2"
    anchor = AGENTS_DIR / "worker"
    rendered = render_template(
        (anchor / "instructions.md").read_text(encoding="utf-8"),
        silent_undefined=True,
        search_paths=instruction_search_paths(anchor),
        **context,
    )
    assert rendered.count("Scenario extra.") == 1
    delta_pos = rendered.index("Do not commit changes to git.")
    assert rendered.index("Scenario extra.") > delta_pos
