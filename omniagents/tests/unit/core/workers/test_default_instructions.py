import asyncio
import os
from types import SimpleNamespace

import pytest

from omniagents.core.workers import (
    DEFAULT_WORKER_INSTRUCTIONS,
    apply_default_worker_instructions,
    default_worker_instructions_path,
)


class _Spec:
    def __init__(self, get_agent_instructions=None):
        if get_agent_instructions is not None:
            self.get_agent_instructions = get_agent_instructions


def test_template_ships_generic_worker_protocol():
    text = DEFAULT_WORKER_INSTRUCTIONS
    assert "{{ worker_name }}" in text
    assert "Stay focused on the assigned task" in text
    assert "multiple tools in parallel" in text
    assert "absolute paths" in text
    assert "## Task list" in text
    assert "## When stuck" in text
    assert "## Output format" in text
    # Product hooks for layering a delta on the base.
    assert "additional_instructions" in text
    # Coding-specific lines stay on the product side.
    assert "commit" not in text.lower()


def test_template_file_is_the_source():
    path = default_worker_instructions_path()
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        assert f.read() == DEFAULT_WORKER_INSTRUCTIONS


def test_spec_without_instructions_gets_default():
    spec = _Spec()
    assert apply_default_worker_instructions(spec) is True
    assert spec.get_agent_instructions() == DEFAULT_WORKER_INSTRUCTIONS


def test_blank_instructions_get_default():
    spec = _Spec(lambda: "   ")
    assert apply_default_worker_instructions(spec) is True
    assert spec.get_agent_instructions() == DEFAULT_WORKER_INSTRUCTIONS


def test_yaml_loader_fallback_counts_as_missing():
    spec = _Spec(lambda: "You are a helpful assistant.")
    assert apply_default_worker_instructions(spec) is True
    assert spec.get_agent_instructions() == DEFAULT_WORKER_INSTRUCTIONS


def test_product_instructions_untouched():
    spec = _Spec(lambda: "You are omni-code's worker.")
    assert apply_default_worker_instructions(spec) is False
    assert spec.get_agent_instructions() == "You are omni-code's worker."


def test_raising_getter_never_blocks_spawn():
    def boom():
        raise RuntimeError("no instructions today")

    spec = _Spec(boom)
    assert apply_default_worker_instructions(spec) is False
    assert spec.get_agent_instructions is boom


@pytest.mark.asyncio
async def test_worker_manager_spawn_applies_default():
    # The seam: WorkerManager._create_worker defaults instructions right
    # after _default_worker_safety, so any spawned worker whose spec
    # carries no instructions runs on the framework prompt.
    from omniagents.rpc.agents.workers import WorkerManager

    class FakeAgent:
        def __init__(self, spec):
            self.spec = spec
            self._event_queue = asyncio.Queue()
            self._client = SimpleNamespace(
                other=SimpleNamespace(
                    start_run=self._start_run,
                )
            )

        async def _start_run(self, prompt, session_id=None, context=None, **kw):
            return SimpleNamespace(result={"run_id": "r1", "session_id": "s1"})

        async def initialize(self):
            return None

        async def _ensure_client(self):
            return None

        async def cleanup(self):
            return None

    def spec_loader(model):
        return SimpleNamespace(variables={}), None

    manager = WorkerManager(spec_loader=spec_loader, agent_factory=FakeAgent)
    handle = await manager.spawn(worker_id="w1", task_description="task")
    assert (
        handle.agent.spec.get_agent_instructions() == DEFAULT_WORKER_INSTRUCTIONS
    )
    handle.monitor_task.cancel()
