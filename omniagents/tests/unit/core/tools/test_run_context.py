from types import SimpleNamespace

from omniagents.core.tools import (
    DEFAULT_TASK_SCOPE,
    ctx_value,
    session_id_from_ctx,
    task_scope_from_ctx,
)


class _Ctx:
    def __init__(self, context):
        self.context = context


def test_ctx_value_dict_and_attr_contexts():
    assert ctx_value(_Ctx({"session_id": "s1"}), "session_id") == "s1"
    assert ctx_value(_Ctx(SimpleNamespace(session_id="s2")), "session_id") == "s2"
    assert ctx_value(_Ctx({}), "session_id") is None
    assert ctx_value(_Ctx(None), "session_id") is None
    assert ctx_value(object(), "session_id") is None


def test_session_id_requires_non_blank_string():
    assert session_id_from_ctx(_Ctx({"session_id": "abc"})) == "abc"
    assert session_id_from_ctx(_Ctx({"session_id": "   "})) is None
    assert session_id_from_ctx(_Ctx({"session_id": 42})) is None
    assert session_id_from_ctx(_Ctx(None)) is None


def test_task_scope_defaults():
    assert task_scope_from_ctx(_Ctx({"task_scope": "worker-7"})) == "worker-7"
    assert task_scope_from_ctx(_Ctx({})) == DEFAULT_TASK_SCOPE
    assert task_scope_from_ctx(_Ctx(None), default="other") == "other"
    assert task_scope_from_ctx(None) == DEFAULT_TASK_SCOPE
