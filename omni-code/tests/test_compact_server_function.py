"""/compact wiring tests.

The server function itself is the framework builtin
(``omniagents.builtin.server_functions.compact``, activated by the builtin
``compact`` capability pack). The summarizer is conventional: with no
explicit install, the framework seam runs omni-code's packaged
``compactor`` agent (named by convention) under
DEFAULT_CHECKPOINT_SUMMARY_INSTRUCTIONS on the utility-run model
resolution. These tests cover that convention and the omni agent's
activation of the ``compact`` capability.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

# Importing omni_code bootstraps the product identity (manifest_path),
# which is what the conventional summarizer resolves the compactor
# agent through.
import omni_code  # noqa: F401
from omniagents.core.runtime.compaction import (
    DEFAULT_CHECKPOINT_SUMMARY_INSTRUCTIONS,
    compaction_summarizer,
)


class FakeService:
    def __init__(self, model_name: str | None = None):
        self.spec = SimpleNamespace(model_name=model_name)


class FakeSession:
    def __init__(self, session_id: str, *, active_model: str | None = None):
        self.id = session_id
        self.active_model = active_model
        self.model_config = None
        self.history: list[dict] = [{"role": "user", "content": "hello"}]

    def append_message(self, item: dict) -> None:
        self.history.append(item)


def test_conventional_compaction_summarizer_is_active():
    """No explicit install anywhere in omni-code: the seam resolves the
    conventionally-named packaged compactor agent."""
    assert compaction_summarizer() is not None


def test_summarizer_runs_compactor_with_framework_instructions(monkeypatch):
    """The conventional summarizer resolves the session's model through
    omni-code's store and runs the compactor utility agent under the
    framework's checkpoint-summary instructions."""
    captured: dict = {}

    def fake_resolve_model_for_runtime(model_name=None):
        captured["resolve_arg"] = model_name
        return {
            "provider": "openai-compatible",
            "model": "gpt-4.1",
            "base_url": "http://localhost:11434/v1",
            "api_key": None,
        }

    async def fake_run_project_agent_once(agent_name, prompt, **kwargs):
        captured["agent_name"] = agent_name
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "summary"

    # The summarizer resolves through the framework's
    # resolve_service_model_runtime, which reads the store instance.
    from omniagents.core.models import model_store

    models = model_store()

    monkeypatch.setattr(
        models, "resolve_model_for_runtime", fake_resolve_model_for_runtime
    )
    monkeypatch.setattr(
        "omniagents.core.runtime.ephemeral.run_project_agent_once",
        fake_run_project_agent_once,
    )

    service = FakeService(model_name="local/gpt-4.1")
    session = FakeSession("sess-1", active_model="local/gpt-4.1")

    summarizer = compaction_summarizer()
    result = asyncio.run(summarizer("transcript", service=service, session=session))

    assert result == "summary"
    assert captured["agent_name"] == "compactor"
    assert captured["prompt"] == "transcript"
    assert captured["resolve_arg"] == "local/gpt-4.1"
    # Non-litellm provider prefix stripped before the provider sees it.
    assert captured["model"] == "gpt-4.1"
    assert captured["runtime"]["provider"] == "openai-compatible"
    assert captured["session"] is session
    assert captured["instructions"] == DEFAULT_CHECKPOINT_SUMMARY_INSTRUCTIONS


def test_builtin_compact_resolves_through_omni_agent_capabilities():
    """Loading the project resolves the omni agent's ``compact`` capability
    to the framework builtin server function (omni-code no longer ships its
    own compact server function)."""
    from omniagents.core.project.runtime import build_spec_from_project, load_project

    project_yml = Path(__file__).parent.parent / "omni_code" / "project.yml"
    project = load_project(str(project_yml))

    # The builtin owns the name: no project server function shadows it.
    assert "compact" in project.builtin_server_function_names

    spec = build_spec_from_project(project, "omni")
    caps = {
        getattr(cap, "capability_name", None): cap
        for cap in getattr(spec, "resolved_capabilities", [])
    }
    assert "compact" in caps

    sf_entries = [
        entry
        for entry in (getattr(caps["compact"], "server_function_specs", None) or [])
        if entry.get("name") == "compact"
    ]
    assert len(sf_entries) == 1
    func = sf_entries[0]["func"]
    assert func.__module__ == "omniagents.builtin.server_functions.compact"

    # The explicit server_functions list no longer carries compact — the
    # capability-gated path does.
    explicit = [
        entry.get("name") if isinstance(entry, dict) else entry
        for entry in (getattr(spec, "server_functions", None) or [])
    ]
    assert "compact" not in explicit


def test_filesystem_and_shell_resolve_to_builtin_packs():
    """omni-code deleted its local shell/filesystem capability overrides:
    the lifted guidance now ships in the builtins, exactly once."""
    from omniagents.core.project.runtime import build_spec_from_project, load_project

    project_yml = Path(__file__).parent.parent / "omni_code" / "project.yml"
    project = load_project(str(project_yml))
    spec = build_spec_from_project(project, "omni")

    caps = {
        getattr(cap, "capability_name", None): cap
        for cap in getattr(spec, "resolved_capabilities", [])
    }

    shell_text = caps["shell"].instructions_text or ""
    assert shell_text.count("Prefer `rg <pattern>` for text search") == 1

    fs_text = caps["filesystem"].instructions_text or ""
    assert fs_text.count("`applypatch` or `apply-patch`") == 1
    assert fs_text.count("do not re-read the file") == 1
