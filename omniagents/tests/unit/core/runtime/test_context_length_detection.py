"""Tests for _is_context_length_exceeded in bridge.py."""

import pytest

from omniagents.core.runtime.bridge import _is_context_length_exceeded


class FakeError(Exception):
    pass


def test_direct_context_length_exceeded_code():
    err = FakeError("context error")
    err.body = {"error": {"code": "context_length_exceeded"}}
    assert _is_context_length_exceeded(err) is True


def test_context_window_exceeded_class_name():
    class ContextWindowExceededError(Exception):
        pass

    err = ContextWindowExceededError("boom")
    assert _is_context_length_exceeded(err) is True


def test_litellm_proxy_error_message_contains_context_length_exceeded():
    """LiteLLM proxy: code='400', real error code buried in the message string."""
    err = FakeError("error code: 400")
    err.body = {
        "error": {
            "message": "litellm.ContextWindowExceededError: context_length_exceeded: This model's maximum context length is 128000 tokens.",
            "type": None,
            "param": None,
            "code": "400",
        }
    }
    assert _is_context_length_exceeded(err) is True


def test_context_window_exceed_in_message():
    err = FakeError("error code: 400")
    err.body = {
        "error": {
            "message": "The context window has been exceeded for this request.",
            "code": "400",
        }
    }
    assert _is_context_length_exceeded(err) is True


def test_code_attribute_context_length_exceeded():
    err = FakeError("some error")
    err.code = "context_length_exceeded"
    assert _is_context_length_exceeded(err) is True


def test_str_contains_context_window_exceeded():
    err = FakeError("context window was exceeded, please reduce input")
    assert _is_context_length_exceeded(err) is True


def test_str_contains_maximum_context_length():
    err = FakeError("maximum context length is 128000 tokens")
    assert _is_context_length_exceeded(err) is True


def test_unrelated_error_returns_false():
    err = FakeError("rate limit exceeded")
    assert _is_context_length_exceeded(err) is False


def test_body_with_non_dict_error_returns_false():
    err = FakeError("bad request")
    err.body = {"error": "string error"}
    assert _is_context_length_exceeded(err) is False


def test_code_400_without_context_keywords_returns_false():
    err = FakeError("error code: 400")
    err.body = {
        "error": {
            "message": "Invalid request: missing required field 'model'",
            "code": "400",
        }
    }
    assert _is_context_length_exceeded(err) is False
