"""Slash declarations and the typed-argument rules."""

import pytest

from omniagents import server_function
from omniagents.core.server_functions.slash import (
    SlashArgsError,
    none,
    parse_slash_args,
    slash_command,
    slash_declaration_of,
    text,
    words,
)
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


def test_declaration_rides_on_the_function_and_defaults_its_name():
    @slash_command(description="Say hi", usage="<name>", args=text("who"))
    @server_function(
        name_override="hi",
        resource_scope=ResourceScope.THREAD,
        execution_requirements=ExecutionRequirement.NONE,
    )
    async def hi(who: str = "") -> dict:
        return {"who": who}

    decl = slash_declaration_of(hi)
    assert decl is not None
    assert decl.name == "hi"
    assert decl.to_dict("hi")["args"] == {"kind": "text", "field": "who"}
    assert decl.to_dict("hi")["during_run"] is True


def test_a_plain_server_function_is_not_a_slash_command():
    @server_function(
        name_override="internal.thing",
        resource_scope=ResourceScope.THREAD,
        execution_requirements=ExecutionRequirement.NONE,
    )
    async def internal() -> dict:
        return {}

    assert slash_declaration_of(internal) is None


def test_explicit_name_and_aliases_are_normalized():
    @slash_command("/Jobs", description="x", aliases=["/J", "jobs.ls"])
    def f():
        return None

    decl = slash_declaration_of(f)
    assert decl.name == "jobs"
    assert decl.aliases == ("j", "jobs.ls")


@pytest.mark.parametrize(
    "rule,rest,expected",
    [
        (none(), "", {}),
        (text("goal"), "fix the tests", {"goal": "fix the tests"}),
        (text("goal"), "   ", {}),
        (
            words("interval", "prompt"),
            "5m fix the tests",
            {"interval": "5m", "prompt": "fix the tests"},
        ),
        (words("interval", "prompt"), "5m", {"interval": "5m"}),
        (words("title", "content"), "", {}),
    ],
)
def test_parse_slash_args(rule, rest, expected):
    assert parse_slash_args(rule, rest) == expected
    # The wire form (what clients receive) parses identically.
    assert parse_slash_args(rule.to_dict(), rest) == expected


def test_no_argument_command_refuses_trailing_text():
    with pytest.raises(SlashArgsError):
        parse_slash_args(none(), "extra")
