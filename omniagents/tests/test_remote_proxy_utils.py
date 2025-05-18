from types import SimpleNamespace
from omniagents.remote_proxy import _extract_run_id, _extract_session_id


def test_extract_run_id_dict():
    assert _extract_run_id({'run_id': 'abc'}) == 'abc'


def test_extract_run_id_attr_dict():
    resp = SimpleNamespace(result={'run_id': 'xyz'})
    assert _extract_run_id(resp) == 'xyz'


def test_extract_run_id_str():
    resp = SimpleNamespace(result='{"run_id": "aaa"}')
    assert _extract_run_id(resp) == 'aaa'


def test_extract_session_id_dict():
    assert _extract_session_id({'session_id': 's1'}) == 's1'


def test_extract_session_id_attr_dict():
    resp = SimpleNamespace(result={'session_id': 's2'})
    assert _extract_session_id(resp) == 's2'


def test_extract_session_id_str():
    resp = SimpleNamespace(result='{"session_id": "ss"}')
    assert _extract_session_id(resp) == 'ss'
