"""Tests for the @is_enabled_predicate decorator and resolver."""

import textwrap

import pytest

from omniagents.core.predicates.discovery import (
    is_enabled_predicate,
    discover_predicates_in_dir,
    resolve_predicate,
)


def test_decorator_marks_function():
    @is_enabled_predicate
    def pred(ctx, agent):
        return True

    assert getattr(pred, "_is_omniagents_is_enabled_predicate") is True
    assert getattr(pred, "_predicate_name") == "pred"


def test_decorator_with_explicit_name():
    @is_enabled_predicate(name="custom")
    def pred(ctx, agent):
        return False

    assert getattr(pred, "_predicate_name") == "custom"


def test_resolve_bool_passthrough():
    assert resolve_predicate(True) is True
    assert resolve_predicate(False) is False


def test_resolve_callable_passthrough():
    def fn(ctx, agent):
        return True

    assert resolve_predicate(fn) is fn


def test_resolve_none():
    assert resolve_predicate(None) is None


def test_resolve_via_registry():
    def fn(ctx, agent):
        return True

    out = resolve_predicate("my_pred", {"my_pred": fn})
    assert out is fn


def test_resolve_via_dotted_path():
    out = resolve_predicate("omniagents.core.predicates.discovery:resolve_predicate")
    assert callable(out)
    # Same callable
    assert out is resolve_predicate


def test_resolve_unknown_returns_none():
    assert resolve_predicate("nonexistent.module:func") is None


def test_discover_predicates_in_dir(tmp_path):
    # Build a fake agent directory with predicates/ subfolder.
    pdir = tmp_path / "predicates"
    pdir.mkdir()
    (pdir / "preds.py").write_text(textwrap.dedent("""
            from omniagents.core.predicates.discovery import is_enabled_predicate

            @is_enabled_predicate
            def is_admin(ctx, agent):
                return True

            @is_enabled_predicate(name="renamed")
            def something(ctx, agent):
                return False

            def not_a_predicate():
                pass
            """))

    discovered = discover_predicates_in_dir(tmp_path)
    assert "is_admin" in discovered
    assert "renamed" in discovered
    assert "not_a_predicate" not in discovered
    # Returns the actual callables
    assert callable(discovered["is_admin"])
