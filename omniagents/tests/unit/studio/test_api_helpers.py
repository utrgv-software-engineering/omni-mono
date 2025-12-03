"""Unit tests for Studio API helper functions."""

from __future__ import annotations

import os

import pytest


def test_expand_env_template_no_template():
    """Test that strings without templates are returned unchanged."""
    from omniagents.studio.server.api import _expand_env_template

    assert _expand_env_template("hello world") == "hello world"
    assert _expand_env_template("http://localhost:3000") == "http://localhost:3000"
    assert _expand_env_template("") == ""


def test_expand_env_template_with_default():
    """Test ${VAR:-default} pattern uses default when var is not set."""
    from omniagents.studio.server.api import _expand_env_template

    # Ensure the variable is not set
    os.environ.pop("TEST_UNSET_VAR", None)

    result = _expand_env_template("${TEST_UNSET_VAR:-http://default.com}")
    assert result == "http://default.com"


def test_expand_env_template_with_env_var_set():
    """Test ${VAR:-default} pattern uses env var when set."""
    from omniagents.studio.server.api import _expand_env_template

    os.environ["TEST_SET_VAR"] = "http://custom.com"
    try:
        result = _expand_env_template("${TEST_SET_VAR:-http://default.com}")
        assert result == "http://custom.com"
    finally:
        os.environ.pop("TEST_SET_VAR", None)


def test_expand_env_template_without_default():
    """Test ${VAR} pattern returns empty string when var is not set."""
    from omniagents.studio.server.api import _expand_env_template

    os.environ.pop("TEST_EMPTY_VAR", None)

    result = _expand_env_template("prefix-${TEST_EMPTY_VAR}-suffix")
    assert result == "prefix--suffix"


def test_expand_env_template_multiple_variables():
    """Test multiple variables in single string."""
    from omniagents.studio.server.api import _expand_env_template

    os.environ["TEST_HOST"] = "myhost"
    os.environ.pop("TEST_PORT", None)
    try:
        result = _expand_env_template("http://${TEST_HOST}:${TEST_PORT:-8080}/api")
        assert result == "http://myhost:8080/api"
    finally:
        os.environ.pop("TEST_HOST", None)


def test_expand_env_template_non_string_input():
    """Test that non-string inputs are returned as-is."""
    from omniagents.studio.server.api import _expand_env_template

    assert _expand_env_template(123) == 123
    assert _expand_env_template(None) is None
    assert _expand_env_template(["list"]) == ["list"]
