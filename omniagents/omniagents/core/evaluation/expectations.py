"""The ``expect`` checks: declarative assertions a scenario can carry.

A scenario's ``expect`` block is free-form data that measures read
(``ctx.expect["expected_answer"]``). A few keys have a fixed meaning and are
checked by the builtin ``expectations`` measure, so a scenario can assert
simple behaviour without writing a measure:

``tool_called``            str | list - tool(s) that must have been called
``tool_not_called``        str | list - tool(s) that must not have been called
``tool_sequence``          list - tools called in this order (not necessarily adjacent)
``tool_called_times``      dict - exact call count per tool
``response_contains``      str | list - substrings of the final answer (case-insensitive)
``response_not_contains``  str | list - substrings the final answer must not contain
``verify``                 dict - expected state, checked by the scenario's environment

Other keys are ignored here and stay available to measures.
"""

from __future__ import annotations

from typing import Any, Dict, List

EXPECTATION_KEYS = (
    "tool_called",
    "tool_not_called",
    "tool_sequence",
    "tool_called_times",
    "response_contains",
    "response_not_contains",
    "verify",
)


def has_expectations(expect: Any) -> bool:
    """Whether an ``expect`` block uses any of the checked keys."""
    return isinstance(expect, dict) and any(k in expect for k in EXPECTATION_KEYS)


def _as_list(value: Any) -> List[Any]:
    return [value] if isinstance(value, str) else list(value or [])


def _check(name: str, passed: bool, reason: str) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "reason": reason}


def check_expectations(ctx: Any, expect: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Check every synchronous expectation key in ``expect`` against ``ctx``.

    Returns one ``{name, passed, reason}`` dict per check. ``verify`` needs
    the environment and is checked by :func:`check_expectations_async`.
    """
    results: List[Dict[str, Any]] = []

    for tool in _as_list(expect.get("tool_called")):
        called = bool(ctx.tool_calls(tool))
        results.append(
            _check(
                f"tool_called:{tool}",
                called,
                f"{tool} {'was' if called else 'was NOT'} called",
            )
        )

    for tool in _as_list(expect.get("tool_not_called")):
        called = bool(ctx.tool_calls(tool))
        results.append(
            _check(
                f"tool_not_called:{tool}",
                not called,
                f"{tool} {'was' if called else 'was NOT'} called",
            )
        )

    if "tool_sequence" in expect:
        expected_seq = list(expect["tool_sequence"] or [])
        actual = [c.name for c in ctx.tool_calls()]
        seq_idx = 0
        for call_name in actual:
            if seq_idx < len(expected_seq) and call_name == expected_seq[seq_idx]:
                seq_idx += 1
        results.append(
            _check(
                f"tool_sequence:{' → '.join(expected_seq)}",
                seq_idx == len(expected_seq),
                f"actual: {' → '.join(actual) if actual else 'none'}",
            )
        )

    for tool, expected_count in (expect.get("tool_called_times") or {}).items():
        actual_count = len(ctx.tool_calls(tool))
        results.append(
            _check(
                f"tool_called_times:{tool}={expected_count}",
                actual_count == expected_count,
                f"called {actual_count} time(s)",
            )
        )

    final = getattr(ctx, "final_assistant_message", None)
    text = (final.text or "").lower() if final is not None else ""
    for key, want_found in (
        ("response_contains", True),
        ("response_not_contains", False),
    ):
        for needle in _as_list(expect.get(key)):
            found = str(needle).lower() in text
            shown = needle[:30] + "..." if len(needle) > 30 else needle
            results.append(
                _check(
                    f"{key}:{shown}",
                    found if want_found else not found,
                    f"'{needle}' {'found' if found else 'NOT found'} in response",
                )
            )

    return results


async def check_expectations_async(
    ctx: Any, expect: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """:func:`check_expectations` plus ``verify`` against the environment."""
    results = check_expectations(ctx, expect)
    if "verify" not in expect:
        return results
    from omniagents.core.eval.environment import NoOpEnvironment

    # A run without a declared environment has a NoOpEnvironment, whose
    # verify() passes everything; that is not a verification.
    environment = getattr(ctx, "environment", None)
    if environment is None or isinstance(environment, NoOpEnvironment):
        results.append(
            _check(
                "verify",
                False,
                "verify requires an EvalEnvironment but none was provided",
            )
        )
        return results
    try:
        outcome = await ctx.verify(expect["verify"])
    except Exception as exc:
        results.append(_check("verify", False, f"verification error: {exc}"))
        return results
    passed = bool(outcome.get("success", False))
    reason = f"verification {'passed' if passed else 'failed'}"
    details = outcome.get("results") or {}
    if details:
        shown = ", ".join(f"{k}={v}" for k, v in list(details.items())[:3])
        reason = f"{reason}: {shown}"
    results.append(_check("verify", passed, reason))
    return results
