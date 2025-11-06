import asyncio
import contextlib
import sys
import types
from pathlib import Path

import yaml

from omniagents.core.eval import scenarios


def _prepare_agent_yaml(tmp_path: Path) -> Path:
    agent_yaml = tmp_path / "agent.yaml"
    agent_yaml.write_text(
        yaml.dump(
            {
                "evaluation": {
                    "defaults": {"max_turns": 3},
                    "synthetic_data_generation": {
                        "dimensions": [
                            {
                                "name": "Persona",
                                "values": [
                                    {"name": "Expert"},
                                    {"name": "Novice"},
                                ],
                            },
                            {
                                "name": "Region",
                                "values": [
                                    {"name": "US"},
                                    {"name": "EU"},
                                ],
                            },
                        ]
                    },
                }
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return agent_yaml


def _configure_execution(monkeypatch, responses):
    progress_events = []
    trace_calls = []
    setup_calls = []
    history_by_session = {
        data["session_id"]: data.get("history", [])
        for data in responses.values()
        if data.get("session_id")
    }

    class FakeRun:
        def __init__(self, prompt, error):
            self._prompt = prompt
            self._error = error

        async def stream_events(self):
            for event in responses[self._prompt].get("events", []):
                yield event
            if self._error:
                raise self._error

    class FakeAgent:
        def __init__(self):
            self._session_id = None
            self._prompt = None

        def run_streamed(self, msgs):
            prompt = msgs[0]["content"]
            self._prompt = prompt
            data = responses[prompt]
            self._session_id = data.get("session_id")
            return FakeRun(prompt, data.get("error"))

        def get_last_run_end(self):
            return responses[self._prompt].get("last_run")

    async def fake_create_agent(spec):
        return FakeAgent()

    def fake_load_history(session_id, project_slug, agent_slug):
        return history_by_session.get(session_id, [])

    def fake_final_assistant_text(history):
        if not history:
            return ""
        last = history[-1]
        return last.get("content", "")

    def fake_setup_tracing(tracing_config):
        setup_calls.append(dict(tracing_config))

    def fake_load_spec(path):
        return types.SimpleNamespace(
            tracing_config={"provider": "studio"},
            project_slug="proj",
            agent_slug="agent",
            max_turns=2,
            use_safe_agent=True,
            available_tools=[],
        )

    @contextlib.contextmanager
    def fake_trace(workflow_name, group_id=None, metadata=None, **kwargs):
        trace_calls.append(
            {
                "workflow_name": workflow_name,
                "group_id": group_id,
                "metadata": metadata,
            }
        )
        yield

    agentio_mod = types.ModuleType("agentio")
    agentio_mod.trace = fake_trace

    monkeypatch.setattr(scenarios, "create_unified_agent", fake_create_agent)
    monkeypatch.setattr(scenarios, "load_history", fake_load_history)
    monkeypatch.setattr(scenarios, "final_assistant_text", fake_final_assistant_text)
    monkeypatch.setattr("omniagents.core.tracing.setup_tracing", fake_setup_tracing)
    monkeypatch.setattr(scenarios, "load_agent_spec_from_yaml", fake_load_spec)
    monkeypatch.setitem(sys.modules, "agentio", agentio_mod)

    return progress_events, trace_calls, setup_calls


def test_run_scenarios_against_agent_tracks_traces_and_history(tmp_path, monkeypatch):
    agent_yaml = _prepare_agent_yaml(tmp_path)
    responses = {
        "prompt-one": {
            "session_id": "sess-1",
            "events": [{"delta": "chunk"}],
            "history": [{"role": "assistant", "content": "Answer one"}],
            "last_run": {},
        },
        "prompt-two": {
            "session_id": "sess-2",
            "events": [],
            "history": [{"role": "assistant", "content": "Answer two"}],
            "last_run": {},
        },
    }
    progress_events, trace_calls, setup_calls = _configure_execution(
        monkeypatch, responses
    )
    scenario_path = tmp_path / "scenarios.yml"
    scenario_path.write_text("scenarios: []", encoding="utf-8")
    scenario_list = [
        {
            "id": "alpha",
            "name": "Alpha",
            "prompt": "prompt-one",
            "dimensions": {"Persona": "Expert", "Region": "US"},
        },
        {
            "id": "beta",
            "name": "Beta",
            "prompt": "prompt-two",
            "dimensions": {"Persona": "Novice", "Region": "EU"},
        },
    ]
    result = asyncio.run(
        scenarios.run_scenarios_against_agent(
            agent_yaml,
            scenario_list,
            include_history=True,
            workflow_name="wf",
            progress_cb=progress_events.append,
            scenario_source_path=scenario_path,
        )
    )
    assert len(result) == 2
    assert setup_calls == [{"provider": "studio", "workflow_name": "wf"}]
    assert progress_events[0]["event"] == "start"
    assert progress_events[-1]["event"] == "done"
    assert progress_events[-1]["count"] == 2
    assert "failed" not in progress_events[-1]
    assert len(trace_calls) == 2
    first_trace = trace_calls[0]
    assert first_trace["workflow_name"] == "wf"
    meta = first_trace["metadata"]["omniagents"]
    assert meta["scenario"]["id"] == "alpha"
    assert meta["config"]["agent_yaml"] == str(agent_yaml)
    assert result[0]["scenario_id"] == "alpha"
    assert result[0]["history"] == responses["prompt-one"]["history"]
    assert result[0]["history_len"] == 1
    assert result[0]["final_assistant_text"] == "Answer one"
    assert result[0]["scenario_path"] == str(scenario_path)
    assert "error" not in result[0]


def test_run_scenarios_against_agent_records_partial_failures(tmp_path, monkeypatch):
    agent_yaml = _prepare_agent_yaml(tmp_path)
    failure = RuntimeError("network down")
    responses = {
        "prompt-one": {
            "session_id": "sess-ok",
            "events": [],
            "history": [{"role": "assistant", "content": "Answer"}],
            "last_run": {},
        },
        "prompt-two": {
            "session_id": "sess-fail",
            "events": [],
            "history": [],
            "error": failure,
            "last_run": {"error": {"message": "timeout", "type": "AgentsException"}},
        },
    }
    progress_events, trace_calls, setup_calls = _configure_execution(
        monkeypatch, responses
    )
    scenario_list = [
        {
            "id": "alpha",
            "name": "Alpha",
            "prompt": "prompt-one",
            "dimensions": {"Persona": "Expert", "Region": "US"},
        },
        {
            "id": "beta",
            "name": "Beta",
            "prompt": "prompt-two",
            "dimensions": {"Persona": "Novice", "Region": "EU"},
        },
    ]
    result = asyncio.run(
        scenarios.run_scenarios_against_agent(
            agent_yaml,
            scenario_list,
            include_history=True,
            workflow_name="wf",
            progress_cb=progress_events.append,
        )
    )
    assert len(result) == 2
    assert result[1]["scenario_id"] == "beta"
    assert result[1]["error"]["message"] == "network down"
    assert result[1]["error"]["type"] == "RuntimeError"
    assert result[1]["agent_error"] == {
        "error": {"message": "timeout", "type": "AgentsException"}
    }
    assert result[1]["history"] == []
    assert result[1]["history_len"] == 0
    assert result[1]["final_assistant_text"] == ""
    assert progress_events[-1]["failed"] == 1
    progress_updates = [ev for ev in progress_events if ev["event"] == "progress"]
    assert progress_updates[-1]["failed"] == 1
    assert "error" not in result[0]
    assert setup_calls == [{"provider": "studio", "workflow_name": "wf"}]
    assert len(trace_calls) == 2
