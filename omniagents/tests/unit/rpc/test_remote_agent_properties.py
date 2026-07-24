import json
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

from omniagents.rpc.agents.remote import RemoteAgent

identifier = st.text(
    alphabet=st.characters(
        min_codepoint=32, max_codepoint=126, blacklist_characters=["\\"]
    ),
    min_size=1,
    max_size=32,
)

bounded_examples = settings(max_examples=25)


@bounded_examples
@given(identifier)
def test_extract_run_id_from_dict(run_id):
    agent = RemoteAgent("ws://test")
    assert agent._extract_run_id({"run_id": run_id}) == run_id


@bounded_examples
@given(identifier)
def test_extract_run_id_from_json_string(run_id):
    agent = RemoteAgent("ws://test")
    payload = SimpleNamespace(result=json.dumps({"run_id": run_id}))
    assert agent._extract_run_id(payload) == run_id


@bounded_examples
@given(identifier)
def test_extract_run_id_from_literal_string(run_id):
    agent = RemoteAgent("ws://test")
    payload = SimpleNamespace(result=str({"run_id": run_id}))
    assert agent._extract_run_id(payload) == run_id


@bounded_examples
@given(identifier)
def test_extract_run_id_missing_value(value):
    agent = RemoteAgent("ws://test")
    payload = SimpleNamespace(result=json.dumps({"other": value}))
    assert agent._extract_run_id(payload) is None


@bounded_examples
@given(identifier)
def test_extract_session_id_from_dict(session_id):
    agent = RemoteAgent("ws://test")
    assert agent._extract_session_id({"session_id": session_id}) == session_id


@bounded_examples
@given(identifier)
def test_extract_session_id_from_json_string(session_id):
    agent = RemoteAgent("ws://test")
    payload = SimpleNamespace(result=json.dumps({"session_id": session_id}))
    assert agent._extract_session_id(payload) == session_id


@bounded_examples
@given(identifier)
def test_extract_session_id_from_literal_string(session_id):
    agent = RemoteAgent("ws://test")
    payload = SimpleNamespace(result=str({"session_id": session_id}))
    assert agent._extract_session_id(payload) == session_id


@bounded_examples
@given(identifier)
def test_extract_session_id_missing_value(value):
    agent = RemoteAgent("ws://test")
    payload = SimpleNamespace(result=json.dumps({"other": value}))
    assert agent._extract_session_id(payload) is None
