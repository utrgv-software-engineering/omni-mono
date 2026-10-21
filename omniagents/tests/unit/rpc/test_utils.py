from types import SimpleNamespace
from omniagents.rpc.agents.remote import RemoteAgent


def test_extract_run_id_dict():
    agent = RemoteAgent("ws://test")
    assert agent._extract_run_id({"run_id": "abc"}) == "abc"


def test_extract_run_id_attr_dict():
    agent = RemoteAgent("ws://test")
    resp = SimpleNamespace(result={"run_id": "xyz"})
    assert agent._extract_run_id(resp) == "xyz"


def test_extract_run_id_str():
    agent = RemoteAgent("ws://test")
    resp = SimpleNamespace(result='{"run_id": "aaa"}')
    assert agent._extract_run_id(resp) == "aaa"


def test_extract_session_id_dict():
    agent = RemoteAgent("ws://test")
    assert agent._extract_session_id({"session_id": "s1"}) == "s1"


def test_extract_session_id_attr_dict():
    agent = RemoteAgent("ws://test")
    resp = SimpleNamespace(result={"session_id": "s2"})
    assert agent._extract_session_id(resp) == "s2"


def test_extract_session_id_str():
    agent = RemoteAgent("ws://test")
    resp = SimpleNamespace(result='{"session_id": "ss"}')
    assert agent._extract_session_id(resp) == "ss"
