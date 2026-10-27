from types import SimpleNamespace as N

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omniagents.core.pricing import load_price_list
from omniagents.core.usage import UsageRecorder
from omniagents.integrations import EventStore, IncomingEvent
from omniagents.integrations.links import PullRequestLinks, opened_pull_requests


def _response(model, input_tokens, cached, written, output, reasoning):
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output,
        "input_tokens_details": {
            "cached_tokens": cached,
            "cache_write_tokens": written,
        },
        "output_tokens_details": {"reasoning_tokens": reasoning},
    }
    return N(model=model, usage=usage)


def _span(span_id, parent, trace, data):
    return N(span_id=span_id, parent_id=parent, trace_id=trace, span_data=data)


def test_every_model_response_is_recorded_with_its_agent_and_session():
    records = []
    recorder = UsageRecorder(records.append)
    recorder.on_trace_start(
        N(
            trace_id="T1",
            group_id="session_S1",
            metadata={},
            name="omniagents_conversation",
        )
    )
    foreman = _span("A1", None, "T1", N(type="agent", name="foreman"))
    tool = _span("F1", "A1", "T1", N(type="function", name="implementation"))
    implementer = _span("A2", "F1", "T1", N(type="agent", name="implementer"))
    for span in (foreman, tool, implementer):
        recorder.on_span_start(span)
    for span, agent_span, response in (
        ("R1", "A1", _response("gpt-6-astra", 60000, 59000, 0, 120, 40)),
        ("R2", "A2", _response("gpt-6-astra", 30000, 0, 28000, 900, 300)),
    ):
        r = _span(
            span, agent_span, "T1", N(type="response", response=response, usage=None)
        )
        recorder.on_span_start(r)
        recorder.on_span_end(r)
    assert [
        (
            r["agent"],
            r["session_id"],
            r["input_tokens"],
            r["cached_tokens"],
            r["cache_write_tokens"],
            r["output_tokens"],
            r["reasoning_tokens"],
        )
        for r in records
    ] == [
        ("foreman", "S1", 60000, 59000, 0, 120, 40),
        ("implementer", "S1", 30000, 0, 28000, 900, 300),
    ]


def test_a_price_list_costs_tokens_with_caching(tmp_path):
    path = tmp_path / "pricing.yml"
    path.write_text(
        "models:\n"
        "  gpt-6-astra: {input: 10, cached_input: 1, cache_write: 12.5, output: 50}\n"
        "  gpt-5.6-luna: {input: 0.2, cached_input: 0.02, output: 1.2}\n"
    )
    prices = load_price_list(path)
    usage = {
        "input_tokens": 1_000_000,
        "cached_tokens": 600_000,
        "cache_write_tokens": 100_000,
        "output_tokens": 10_000,
    }
    # 300k fresh x $10 + 600k cached x $1 + 100k written x $12.50 + 10k out x $50, per million
    assert prices.cost("gpt-6-astra", usage) == pytest.approx(3.0 + 0.6 + 1.25 + 0.5)
    # A dated snapshot, and a catalog ref, are their model's; no cache-write price: as input.
    assert prices.cost("codex/gpt-6-astra-2026-09-01", usage) == pytest.approx(5.35)
    assert prices.cost("gpt-5.6-luna", usage) == pytest.approx(
        0.06 + 0.012 + 0.02 + 0.012
    )
    assert prices.cost("claude-fable-5", usage) is None
    path.write_text("models:\n  x: {input: 1}\n")
    with pytest.raises(ValueError, match="cached_input is required"):
        load_price_list(path)


def _event(**kw):
    return IncomingEvent(
        provider=kw.pop("provider", "slack"),
        event=kw.pop("event", "app_mention"),
        delivery_id=kw.pop("delivery", "E1"),
        payload=kw.pop("payload", {}),
        **kw,
    )


def _usage(store, at, session, agent, model="gpt-6-astra", metadata=None, tokens=1000):
    store.record_usage(
        {
            "at": at,
            "session_id": session,
            "trace_id": "T",
            "workflow": "w",
            "metadata": metadata or {},
            "agent": agent,
            "model": model,
            "input_tokens": tokens,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 10,
            "reasoning_tokens": 0,
        }
    )


def test_a_runs_usage_is_its_sessions_while_it_ran_and_its_judges(tmp_path):
    store = EventStore(tmp_path / "events.db")
    event_id = store.record(_event(thread_key="T:1"))
    first = store.start_dispatch(event_id, "mentions", "foreman", "S1")
    started = store.runs_since(0)[0]["started_at"]
    import time

    for agent in ("foreman", "implementer", "guardian"):
        _usage(store, time.time(), "S1", agent)
    store.finish_dispatch(first, "completed")
    _usage(
        store,
        started + 10_000,
        "S1",
        "Judge:task_compliance",
        metadata={"dispatch": str(first)},
    )
    _usage(
        store, started + 20_000, "S1", "foreman"
    )  # a later run in the same conversation
    _usage(store, time.time() - 0.001, "S2", "foreman")  # another conversation
    rows = store.run_usage(first)
    assert [(r["agent"], r["requests"]) for r in rows] == [
        ("Judge:task_compliance", 1),
        ("foreman", 1),
        ("guardian", 1),
        ("implementer", 1),
    ]


def test_a_runs_pull_requests_are_the_ones_it_opened_and_the_one_it_was_about(tmp_path):
    root = [
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "implementation",
            "arguments": "{}",
        }
    ]
    child = [
        {
            "type": "function_call",
            "call_id": "c2",
            "name": "mcp_github__create_pull_request",
            "arguments": '{"owner": "acme", "repo": "api-service"}',
        },
        {
            "type": "function_call_output",
            "call_id": "c2",
            "output": [
                {
                    "type": "input_text",
                    "text": '{"id":"7","url":"http://w/acme/api-service/pull/4"}',
                }
            ],
        },
    ]
    assert opened_pull_requests(child) == [("acme/api-service", 4)]
    store = EventStore(tmp_path / "events.db")
    links = PullRequestLinks(
        store, lambda a, s: root, lambda a, s: [("implementer", "sub_1", child)]
    )
    event_id = store.record(_event())
    dispatch = store.start_dispatch(event_id, "mentions", "foreman", "S1")
    links.record(dispatch, "foreman", "S1", _event())
    assert store.run_pull_requests(dispatch) == [
        {"repository": "acme/api-service", "number": 4, "relation": "opened"}
    ]
    review = _event(
        provider="github",
        event="pull_request_review_requested",
        delivery="G1",
        thread_key="acme/api-service#4",
        payload={"pull_request": {"number": 4}},
    )
    second = store.start_dispatch(
        store.record(review), "pr-review", "code_reviewer", "S2"
    )
    PullRequestLinks(store, lambda a, s: [], lambda a, s: []).record(
        second, "code_reviewer", "S2", review
    )
    assert store.run_pull_requests(second) == [
        {"repository": "acme/api-service", "number": 4, "relation": "worked"}
    ]


def test_the_runs_route_gives_each_run_its_usage_cost_and_pull_requests(tmp_path):
    from omniagents.integrations.server import _mount_runs_route

    store = EventStore(tmp_path / "events.db")
    dispatch = store.start_dispatch(store.record(_event()), "mentions", "foreman", "S1")
    started = store.runs_since(0)[0]["started_at"]
    import time

    _usage(store, time.time(), "S1", "foreman", tokens=1_000_000)
    _usage(store, time.time(), "S1", "triage", model="mystery-model")
    store.link_pull_request(dispatch, "acme/api-service", 4, "opened")
    store.finish_dispatch(dispatch, "completed")
    (tmp_path / "pricing.yml").write_text(
        "models:\n  gpt-6-astra: {input: 10, cached_input: 1, output: 50}\n"
    )

    class Authenticator:
        async def authenticate_http(self, request):
            from omniagents.rpc.auth import AuthenticationFailed

            if request.headers.get("authorization") != "Bearer t":
                raise AuthenticationFailed("no")

    app = FastAPI()
    app.state.connection_authenticator = Authenticator()
    _mount_runs_route(app, store, load_price_list(tmp_path / "pricing.yml"))
    client = TestClient(app)
    assert client.get("/integrations/runs").status_code == 401
    [run] = client.get(
        "/integrations/runs", headers={"Authorization": "Bearer t"}
    ).json()["runs"]
    by_agent = {u["agent"]: u for u in run["usage"]}
    assert by_agent["foreman"]["cost"] == pytest.approx(10.0 + 10 * 50 / 1_000_000)
    assert by_agent["triage"]["cost"] is None
    assert run["cost"] is None  # not every model it used has a price
    assert run["pull_requests"] == [
        {"repository": "acme/api-service", "number": 4, "relation": "opened"}
    ]


def test_a_trace_that_reports_its_end_early_keeps_its_session():
    """A run's trace can report its end while its spans go on: they are still its session's."""
    records = []
    recorder = UsageRecorder(records.append)
    trace = N(
        trace_id="T1",
        group_id="session_S1",
        metadata={},
        name="omniagents_conversation",
    )
    recorder.on_trace_start(trace)
    agent = _span("A1", None, "T1", N(type="agent", name="foreman"))
    recorder.on_span_start(agent)
    recorder.on_trace_end(trace)
    r = _span(
        "R1",
        "A1",
        "T1",
        N(type="response", response=_response("m", 10, 0, 0, 1, 0), usage=None),
    )
    recorder.on_span_start(r)
    recorder.on_span_end(r)
    assert (records[0]["session_id"], records[0]["agent"]) == ("S1", "foreman")


def test_a_runs_case_gives_what_each_of_its_sessions_was_given(tmp_path):
    from omniagents.integrations.server import _mount_case_route

    store = EventStore(tmp_path / "events.db")
    dispatch = store.start_dispatch(
        store.record(_event()),
        "mentions",
        "foreman",
        "S1",
        prompt="Someone mentioned the factory",
        version="f44aa89",
    )
    store.finish_dispatch(dispatch, "completed")
    children = lambda a, s: [
        ("implementer", "sub_1", [{"role": "user", "content": "Implement ENG-4"}])
    ]

    class Authenticator:
        async def authenticate_http(self, request):
            return object()

    app = FastAPI()
    app.state.connection_authenticator = Authenticator()
    _mount_case_route(app, store, lambda a, s: [], children)
    client = TestClient(app)
    case = client.get(f"/integrations/runs/{dispatch}/case").json()
    assert case["version"] == "f44aa89"
    assert [(s["agent"], s["prompt"]) for s in case["sessions"]] == [
        ("foreman", "Someone mentioned the factory"),
        ("implementer", "Implement ENG-4"),
    ]
    assert client.get("/integrations/runs/999/case").status_code == 404
