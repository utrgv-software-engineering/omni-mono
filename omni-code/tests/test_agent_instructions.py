"""Instruction-template composition tests.

omni and voice_agent share verbatim base sections via ``{% include %}``
fragments in ``omni_code/agents/_shared/`` (framework include mechanism:
anchor dir -> anchor/_shared -> agents/_shared). The worker agent declares
no instructions at all: WorkerManager installs the framework's
DEFAULT_WORKER_INSTRUCTIONS and omni-code layers its coding delta through
the ``additional_instructions`` context hook.
"""

from pathlib import Path

import yaml

from omniagents.core.utils.jinja_instructions import (
    instruction_search_paths,
    render_template,
)

AGENTS_DIR = Path(__file__).resolve().parents[1] / "omni_code" / "agents"


def _render(agent_key: str) -> str:
    anchor = AGENTS_DIR / agent_key
    template = (anchor / "instructions.md").read_text(encoding="utf-8")
    return render_template(
        template,
        silent_undefined=True,
        search_paths=instruction_search_paths(anchor),
        current_date="2026-01-01",
    )


def test_omni_and_voice_share_fragment_sections_verbatim():
    omni = _render("omni")
    voice = _render("voice_agent")

    shared_probes = [
        "You are Omni Code, a highly-skilled full-stack software-engineering assistant.",
        "## AGENTS.md spec",
        "## Proactiveness",
        "## Following conventions",
        "## Code Style",
        "## Debugging Libraries, Frameworks, Dependencies",
        "## Task execution",
        "- Fix the problem at the root cause rather than applying surface-level patches, when possible.",
    ]
    for probe in shared_probes:
        assert probe in omni, probe
        assert probe in voice, probe

    # The full AGENTS.md spec section must be byte-identical in both renders.
    spec_fragment = (AGENTS_DIR / "_shared" / "agents_md_spec.md").read_text(
        encoding="utf-8"
    ).rstrip("\n")
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


def test_worker_spec_uses_framework_default_plus_coding_delta(monkeypatch, tmp_path):
    from omniagents.core.workers.instructions import (
        DEFAULT_WORKER_INSTRUCTIONS,
        apply_default_worker_instructions,
    )

    from omni_code.agents.worker.context import build_worker_context

    # The worker agent.yml deliberately declares no instructions, so the
    # loader fallback applies and WorkerManager swaps in the framework
    # default at spawn time.
    config = yaml.safe_load(
        (AGENTS_DIR / "worker" / "agent.yml").read_text(encoding="utf-8")
    )
    assert "instructions" not in config
    assert "instructions_file" not in config

    class SpecStub:
        def get_agent_instructions(self):
            return "You are a helpful assistant."

    spec = SpecStub()
    assert apply_default_worker_instructions(spec) is True
    assert spec.get_agent_instructions() == DEFAULT_WORKER_INSTRUCTIONS

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    context = build_worker_context({})
    context["worker_name"] = "worker-test-1"

    rendered = render_template(
        DEFAULT_WORKER_INSTRUCTIONS, silent_undefined=True, **context
    )

    # Framework base present.
    assert (
        "You are **worker-test-1**, an autonomous worker agent executing a "
        "focused subtask." in rendered
    )
    assert "## Task list" in rendered
    assert "## When stuck" in rendered
    # Coding delta present exactly once, under the framework's hook heading.
    assert rendered.count("# Additional Instructions") == 1
    assert rendered.count("Do not commit changes to git.") == 1
    assert (
        rendered.count("list every file you created or changed by absolute path")
        == 1
    )
    # Nothing duplicated from the framework base.
    assert rendered.count("Stay focused on the assigned task.") == 1


def test_worker_context_preserves_caller_additional_instructions():
    from omni_code.agents.worker.context import (
        WORKER_CODING_DELTA,
        build_worker_context,
    )

    context = build_worker_context({"additional_instructions": "Scenario extra."})
    assert context["additional_instructions"].startswith(WORKER_CODING_DELTA)
    assert context["additional_instructions"].endswith("Scenario extra.")
