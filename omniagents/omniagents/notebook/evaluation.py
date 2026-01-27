"""Notebook-friendly evaluation API for omniagents.

This module provides a simplified, progressive API for evaluating agents
in Jupyter notebooks. It wraps the core evaluation primitives with a
DataFrame-native, visually rich interface.
"""

from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from agents import Agent, Runner as AgentRunner

# Disable OpenAI tracing by default for notebook evaluation
# Users can opt-in to tracing by setting it up explicitly before importing
try:
    from agents import set_trace_processors
    set_trace_processors([])
except ImportError:
    pass

from omniagents.core.evaluation.context import (
    EvalContext,
    ToolCall,
    build_eval_context,
    pass_reason,
    fail_reason,
)
from omniagents.core.evaluation.registry import (
    evaluation_measure,
    run_measures as _run_measures,
    get_measure,
    _MEASURES,
)
from omniagents.core.eval.environment import (
    EvalEnvironment,
    EnvironmentContext,
    NoOpEnvironment,
)


# -----------------------------------------------------------------------------
# Result Data Classes
# -----------------------------------------------------------------------------


@dataclass
class MeasureResult:
    """Result of running a single measure."""

    name: str
    passed: bool
    reason: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MeasureResult":
        """Create from measure result dict."""
        return cls(
            name=data.get("name", "unknown"),
            passed=data.get("passed", False),
            reason=data.get("reason"),
            extra={k: v for k, v in data.items() if k not in ("name", "passed", "reason")},
        )

    def _repr_html_(self) -> str:
        icon = "✓" if self.passed else "✗"
        color = "#22c55e" if self.passed else "#ef4444"
        reason_html = f" - {html.escape(self.reason)}" if self.reason else ""
        return f'<span style="color: {color}">{icon} {html.escape(self.name)}{reason_html}</span>'


@dataclass
class ExpectationResult:
    """Result of checking a single expectation."""

    name: str
    passed: bool
    reason: Optional[str] = None

    def _repr_html_(self) -> str:
        icon = "✓" if self.passed else "✗"
        color = "#22c55e" if self.passed else "#ef4444"
        reason_html = f" - {html.escape(self.reason)}" if self.reason else ""
        return f'<span style="color: {color}">{icon} {html.escape(self.name)}{reason_html}</span>'


@dataclass
class JudgeResult:
    """Result from an LLM judge evaluation."""

    name: str
    passed: bool
    reasoning: str
    score: Optional[float] = None  # Optional 0-1 score
    raw_output: Optional[Any] = None  # Full judge output

    def _repr_html_(self) -> str:
        icon = "✓" if self.passed else "✗"
        color = "#22c55e" if self.passed else "#ef4444"
        score_html = f" ({self.score:.0%})" if self.score is not None else ""
        return f'''
        <div style="border-left: 3px solid {color}; padding-left: 8px; margin: 4px 0;">
            <span style="color: {color}; font-weight: bold;">{icon} {html.escape(self.name)}{score_html}</span>
            <div style="color: #6b7280; font-size: 0.9em; margin-top: 2px;">{html.escape(self.reasoning)}</div>
        </div>
        '''


@dataclass
class Turn:
    """A single turn in a multi-turn conversation."""

    role: str  # "user" or "assistant"
    content: str
    tools_used: List[str] = field(default_factory=list)

    def _repr_html_(self) -> str:
        role_color = "#3b82f6" if self.role == "user" else "#10b981"
        role_label = "User" if self.role == "user" else "Assistant"
        tools_html = ""
        if self.tools_used:
            tools_html = f' <span style="color: #6b7280; font-size: 0.8em;">({", ".join(self.tools_used)})</span>'
        content_display = self.content[:200] + "..." if len(self.content) > 200 else self.content
        return f'''
        <div style="margin: 4px 0; padding: 8px; background: #f9fafb; border-radius: 4px;">
            <span style="color: {role_color}; font-weight: bold;">{role_label}:</span>{tools_html}
            <div style="margin-top: 4px;">{html.escape(content_display)}</div>
        </div>
        '''


@dataclass
class EvalResult:
    """Result of a single agent evaluation."""

    input: str
    output: str
    passed: bool
    measures: List[MeasureResult]
    expectations: Optional[List[ExpectationResult]] = None
    judges: Optional[List[JudgeResult]] = None  # LLM judge results
    ctx: Optional[EvalContext] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    name: Optional[str] = None  # For named test cases
    turns: Optional[List[Turn]] = None  # For multi-turn conversations
    completion_reason: Optional[str] = None  # For synthetic: "done", "give_up", "max_turns"

    @property
    def tool_calls(self) -> List[ToolCall]:
        """All tool calls made during this run."""
        if self.ctx is None:
            return []
        return self.ctx.tool_calls()

    @property
    def tools_used(self) -> List[str]:
        """Names of tools that were called."""
        return [c.name for c in self.tool_calls if c.name]

    def called(self, tool_name: str) -> bool:
        """Check if a specific tool was called."""
        if self.ctx is None:
            return False
        return bool(self.ctx.tool_calls(tool_name))

    def call_count(self, tool_name: str) -> int:
        """Count how many times a tool was called."""
        if self.ctx is None:
            return 0
        return len(self.ctx.tool_calls(tool_name))

    def tool_args(self, tool_name: str) -> List[Any]:
        """Get arguments for all calls to a tool."""
        if self.ctx is None:
            return []
        return [c.args for c in self.ctx.tool_calls(tool_name)]

    @property
    def is_multi_turn(self) -> bool:
        """Whether this was a multi-turn conversation."""
        return self.turns is not None and len(self.turns) > 2

    @property
    def num_turns(self) -> int:
        """Number of turns in the conversation."""
        return len(self.turns) if self.turns else 1

    def _repr_html_(self) -> str:
        """Rich HTML display for Jupyter notebooks."""
        status = "PASSED" if self.passed else "FAILED"
        status_color = "#22c55e" if self.passed else "#ef4444"
        status_bg = "#dcfce7" if self.passed else "#fef2f2"

        # Build tools used string
        tools_str = " → ".join(self.tools_used) if self.tools_used else "<em>none</em>"

        # Build measures list
        measures_html = ""
        for m in self.measures:
            icon = "✓" if m.passed else "✗"
            color = "#22c55e" if m.passed else "#ef4444"
            reason = f" - {html.escape(m.reason)}" if m.reason else ""
            measures_html += f'<div style="color: {color}; margin-left: 12px;">{icon} {html.escape(m.name)}{reason}</div>'

        # Build expectations list
        expectations_html = ""
        if self.expectations:
            expectations_html = '<tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 12px; vertical-align: top;"><strong>Expectations</strong></td><td style="padding: 8px 12px;">'
            for e in self.expectations:
                icon = "✓" if e.passed else "✗"
                color = "#22c55e" if e.passed else "#ef4444"
                reason = f" - {html.escape(e.reason)}" if e.reason else ""
                expectations_html += f'<div style="color: {color}; margin-left: 12px;">{icon} {html.escape(e.name)}{reason}</div>'
            expectations_html += "</td></tr>"

        # Build judges list
        judges_html = ""
        if self.judges:
            judges_html = '<tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 12px; vertical-align: top;"><strong>Judges</strong></td><td style="padding: 8px 12px;">'
            for j in self.judges:
                icon = "✓" if j.passed else "✗"
                color = "#22c55e" if j.passed else "#ef4444"
                score_str = f" ({j.score:.0%})" if j.score is not None else ""
                judges_html += f'''
                <div style="border-left: 3px solid {color}; padding-left: 8px; margin: 4px 0;">
                    <span style="color: {color}; font-weight: bold;">{icon} {html.escape(j.name)}{score_str}</span>
                    <div style="color: #6b7280; font-size: 0.9em;">{html.escape(j.reasoning[:150] + "..." if len(j.reasoning) > 150 else j.reasoning)}</div>
                </div>
                '''
            judges_html += "</td></tr>"

        # Error section
        error_html = ""
        if self.error:
            error_html = f'<tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 12px;"><strong>Error</strong></td><td style="padding: 8px 12px; color: #ef4444;">{html.escape(self.error)}</td></tr>'

        # Name header
        name_html = f" - {html.escape(self.name)}" if self.name else ""

        # Multi-turn conversation display
        if self.turns and len(self.turns) > 0:
            # Show conversation view for multi-turn
            turns_html = '<tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 12px; vertical-align: top;"><strong>Conversation</strong></td><td style="padding: 8px 12px;">'
            for turn in self.turns:
                role_label = "User" if turn.role == "user" else "Assistant"
                role_color = "#6b7280" if turn.role == "user" else "#059669"  # Gray for user, green for assistant
                tools_badge = ""
                if turn.tools_used:
                    tools_badge = f' <span style="color: #9ca3af; font-size: 0.8em;">({", ".join(turn.tools_used)})</span>'
                content_display = turn.content[:150] + "..." if len(turn.content) > 150 else turn.content
                turns_html += f'''
                <div style="margin: 4px 0; padding: 6px 8px; background: white; border-radius: 4px; border: 1px solid #e5e7eb; border-left: 3px solid {role_color};">
                    <span style="color: {role_color}; font-weight: 600; font-size: 0.85em;">{role_label}</span>{tools_badge}
                    <div style="margin-top: 2px; font-size: 0.9em; color: #374151;">{html.escape(content_display)}</div>
                </div>
                '''
            turns_html += "</td></tr>"

            # Completion reason for synthetic
            completion_html = ""
            if self.completion_reason:
                reason_color = "#22c55e" if self.completion_reason == "done" else "#f59e0b" if self.completion_reason == "max_turns" else "#ef4444"
                reason_label = {"done": "Goal achieved", "give_up": "User gave up", "max_turns": "Max turns reached"}.get(self.completion_reason, self.completion_reason)
                completion_html = f'<tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 12px;"><strong>Completion</strong></td><td style="padding: 8px 12px; color: {reason_color};">{reason_label}</td></tr>'

            return f'''
            <div style="border: 1px solid #e5e7eb; border-radius: 8px; margin: 8px 0; font-family: system-ui, sans-serif;">
                <div style="background: {status_bg}; color: {status_color}; padding: 8px 12px; font-weight: bold; border-radius: 8px 8px 0 0;">
                    {status}{name_html} <span style="font-weight: normal; font-size: 0.9em;">({len(self.turns)} turns)</span>
                </div>
                <table style="width: 100%; border-collapse: collapse;">
                    {turns_html}
                    {completion_html}
                    <tr style="border-bottom: 1px solid #f3f4f6;">
                        <td style="padding: 8px 12px; width: 120px; vertical-align: top;"><strong>Tools Used</strong></td>
                        <td style="padding: 8px 12px;">{tools_str}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f3f4f6;">
                        <td style="padding: 8px 12px; vertical-align: top;"><strong>Measures</strong></td>
                        <td style="padding: 8px 12px;">{measures_html if measures_html else "<em>none</em>"}</td>
                    </tr>
                    {expectations_html}
                    {judges_html}
                    {error_html}
                </table>
            </div>
            '''

        # Single-turn display (original)
        input_display = self.input[:100] + "..." if len(self.input) > 100 else self.input
        output_display = self.output[:200] + "..." if len(self.output) > 200 else self.output

        return f'''
        <div style="border: 1px solid #e5e7eb; border-radius: 8px; margin: 8px 0; font-family: system-ui, sans-serif;">
            <div style="background: {status_bg}; color: {status_color}; padding: 8px 12px; font-weight: bold; border-radius: 8px 8px 0 0;">
                {status}{name_html}
            </div>
            <table style="width: 100%; border-collapse: collapse;">
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px 12px; width: 100px; vertical-align: top;"><strong>Input</strong></td>
                    <td style="padding: 8px 12px;">{html.escape(input_display)}</td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px 12px; vertical-align: top;"><strong>Output</strong></td>
                    <td style="padding: 8px 12px;">{html.escape(output_display)}</td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px 12px; vertical-align: top;"><strong>Tools Used</strong></td>
                    <td style="padding: 8px 12px;">{tools_str}</td>
                </tr>
                <tr style="border-bottom: 1px solid #f3f4f6;">
                    <td style="padding: 8px 12px; vertical-align: top;"><strong>Measures</strong></td>
                    <td style="padding: 8px 12px;">{measures_html if measures_html else "<em>none</em>"}</td>
                </tr>
                {expectations_html}
                {judges_html}
                {error_html}
            </table>
        </div>
        '''

    def __repr__(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        return f"EvalResult({status}, input={self.input[:30]!r}..., tools={self.tools_used})"


# -----------------------------------------------------------------------------
# Expectations DSL
# -----------------------------------------------------------------------------


async def check_expectations_async(
    ctx: EvalContext, expect: Dict[str, Any]
) -> List[ExpectationResult]:
    """Check expectations against evaluation context (async version for verify support).

    Expectation keys:
        tool_called: str | list[str] - Tool(s) that should have been called
        tool_not_called: str | list[str] - Tool(s) that should NOT have been called
        tool_sequence: list[str] - Tools called in this order
        tool_called_times: dict[str, int] - Tool call counts
        response_contains: str | list[str] - Strings that should be in output
        response_not_contains: str | list[str] - Strings that should NOT be in output
        verify: dict - Expected state to verify against environment (requires EvalEnvironment)
        passes: bool - Whether all measures should pass
        measures: dict[str, bool] - Expected pass/fail for specific measures
    """
    # Run sync expectations first
    results = check_expectations(ctx, expect)

    # verify - async environment verification
    if "verify" in expect:
        verify_spec = expect["verify"]
        if ctx.environment is None:
            results.append(ExpectationResult(
                name="verify",
                passed=False,
                reason="verify requires an EvalEnvironment but none was provided",
            ))
        else:
            try:
                verify_result = await ctx.verify(verify_spec)
                passed = verify_result.get("success", False)
                details = verify_result.get("results", {})
                reason = f"verification {'passed' if passed else 'failed'}"
                if details:
                    detail_str = ", ".join(f"{k}={v}" for k, v in list(details.items())[:3])
                    reason = f"{reason}: {detail_str}"
                results.append(ExpectationResult(
                    name="verify",
                    passed=passed,
                    reason=reason,
                ))
            except Exception as e:
                results.append(ExpectationResult(
                    name="verify",
                    passed=False,
                    reason=f"verification error: {str(e)}",
                ))

    return results


def check_expectations(ctx: EvalContext, expect: Dict[str, Any]) -> List[ExpectationResult]:
    """Check expectations against evaluation context.

    Expectation keys:
        tool_called: str | list[str] - Tool(s) that should have been called
        tool_not_called: str | list[str] - Tool(s) that should NOT have been called
        tool_sequence: list[str] - Tools called in this order
        tool_called_times: dict[str, int] - Tool call counts
        response_contains: str | list[str] - Strings that should be in output
        response_not_contains: str | list[str] - Strings that should NOT be in output
        passes: bool - Whether all measures should pass
        measures: dict[str, bool] - Expected pass/fail for specific measures

    Note: For 'verify' expectations (environment verification), use check_expectations_async.
    """
    results: List[ExpectationResult] = []

    # tool_called
    if "tool_called" in expect:
        tools = expect["tool_called"]
        if isinstance(tools, str):
            tools = [tools]
        for tool in tools:
            called = bool(ctx.tool_calls(tool))
            results.append(ExpectationResult(
                name=f"tool_called:{tool}",
                passed=called,
                reason=f"{tool} {'was' if called else 'was NOT'} called",
            ))

    # tool_not_called
    if "tool_not_called" in expect:
        tools = expect["tool_not_called"]
        if isinstance(tools, str):
            tools = [tools]
        for tool in tools:
            called = bool(ctx.tool_calls(tool))
            results.append(ExpectationResult(
                name=f"tool_not_called:{tool}",
                passed=not called,
                reason=f"{tool} {'was' if called else 'was NOT'} called",
            ))

    # tool_sequence
    if "tool_sequence" in expect:
        expected_seq = expect["tool_sequence"]
        actual_calls = [c.name for c in ctx.tool_calls()]

        # Check if expected sequence appears in order (not necessarily contiguous)
        seq_idx = 0
        for call_name in actual_calls:
            if seq_idx < len(expected_seq) and call_name == expected_seq[seq_idx]:
                seq_idx += 1

        passed = seq_idx == len(expected_seq)
        results.append(ExpectationResult(
            name=f"tool_sequence:{' → '.join(expected_seq)}",
            passed=passed,
            reason=f"actual: {' → '.join(actual_calls) if actual_calls else 'none'}",
        ))

    # tool_called_times
    if "tool_called_times" in expect:
        for tool, expected_count in expect["tool_called_times"].items():
            actual_count = len(ctx.tool_calls(tool))
            passed = actual_count == expected_count
            results.append(ExpectationResult(
                name=f"tool_called_times:{tool}={expected_count}",
                passed=passed,
                reason=f"called {actual_count} time(s)",
            ))

    # response_contains
    if "response_contains" in expect:
        needles = expect["response_contains"]
        if isinstance(needles, str):
            needles = [needles]
        text = ctx.final_assistant_message.text.lower() if ctx.final_assistant_message else ""
        for needle in needles:
            found = needle.lower() in text
            display_needle = needle[:30] + "..." if len(needle) > 30 else needle
            results.append(ExpectationResult(
                name=f"response_contains:{display_needle}",
                passed=found,
                reason=f"'{needle}' {'found' if found else 'NOT found'} in response",
            ))

    # response_not_contains
    if "response_not_contains" in expect:
        needles = expect["response_not_contains"]
        if isinstance(needles, str):
            needles = [needles]
        text = ctx.final_assistant_message.text.lower() if ctx.final_assistant_message else ""
        for needle in needles:
            found = needle.lower() in text
            display_needle = needle[:30] + "..." if len(needle) > 30 else needle
            results.append(ExpectationResult(
                name=f"response_not_contains:{display_needle}",
                passed=not found,
                reason=f"'{needle}' {'found' if found else 'NOT found'} in response",
            ))

    return results


# -----------------------------------------------------------------------------
# Test Suite
# -----------------------------------------------------------------------------


@dataclass
class TestCase:
    """A single test case in an evaluation suite."""

    name: str
    input: Optional[str] = None  # Single-turn input (mutually exclusive with prompts/goal)
    prompts: Optional[List[str]] = None  # Multi-turn scripted prompts
    goal: Optional[str] = None  # Synthetic user goal
    persona: Optional[Dict[str, Any]] = None  # Synthetic user persona
    expect: Optional[Dict[str, Any]] = None
    measures: Optional[List[str]] = None  # Per-case measures
    judges: Optional[List[Union[str, "Judge"]]] = None  # Per-case judges
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    max_exchanges: Optional[int] = None  # For multi-turn

    @property
    def is_multi_turn(self) -> bool:
        """Whether this is a multi-turn test case."""
        return self.prompts is not None or self.goal is not None


@dataclass
class EvalSuiteResults:
    """Results from running an evaluation suite."""

    suite: "EvalSuite"
    results: List[EvalResult]

    @property
    def df(self) -> "pd.DataFrame":
        """Results as a pandas DataFrame."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is required for DataFrame output. Install with: pip install pandas")

        rows = []
        for r in self.results:
            row = {
                "name": r.name or "",
                "input": r.input[:50] + "..." if len(r.input) > 50 else r.input,
                "passed": r.passed,
                "tools_used": ", ".join(r.tools_used),
                "output": r.output[:80] + "..." if len(r.output) > 80 else r.output,
            }
            for m in r.measures:
                row[m.name] = m.passed
            rows.append(row)

        return pd.DataFrame(rows)

    @property
    def pass_rate(self) -> float:
        """Fraction of tests that passed."""
        if not self.results:
            return 0.0
        return sum(r.passed for r in self.results) / len(self.results)

    @property
    def summary(self) -> Dict[str, Any]:
        """Summary statistics."""
        passed = sum(r.passed for r in self.results)
        failed = len(self.results) - passed
        return {
            "suite_name": self.suite.name,
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "pass_rate": self.pass_rate,
        }

    def failures(self) -> List[EvalResult]:
        """Return only failed results."""
        return [r for r in self.results if not r.passed]

    def by_tag(self, tag: str) -> "EvalSuiteResults":
        """Filter results by tag."""
        filtered_cases = [c for c in self.suite.cases if c.tags and tag in c.tags]
        case_names = {c.name for c in filtered_cases}
        filtered_results = [r for r in self.results if r.name in case_names]
        return EvalSuiteResults(suite=self.suite, results=filtered_results)

    def breakdown(self, column: str) -> "pd.DataFrame":
        """Get pass rate breakdown by a metadata column.

        Extracts the specified column from measure extras and groups results
        by that column, showing total count, passed count, and accuracy.

        Args:
            column: The metadata column name to group by (e.g., 'problem_type',
                   'problem_level'). This should be a key in the measure's
                   extra dict.

        Returns:
            DataFrame with columns: Total, Passed, Accuracy

        Example:
            results = await suite.run(agent)
            results.breakdown('problem_type')
            #                  Total  Passed  Accuracy
            # problem_type
            # Algebra             45      38    84.4%
            # Geometry            25      18    72.0%
            # Number Theory       30      22    73.3%
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas required for breakdown(). Install with: pip install pandas")

        # Extract metadata from measure extras
        rows = []
        for r in self.results:
            row_data = {column: None, "passed": r.passed}
            # Look through measures for the column value
            for m in r.measures:
                if m.extra and column in m.extra:
                    row_data[column] = m.extra[column]
                    break
            rows.append(row_data)

        df = pd.DataFrame(rows)

        # Filter out rows where the column is None
        df = df[df[column].notna()]

        if df.empty:
            return pd.DataFrame(columns=["Total", "Passed", "Accuracy"])

        # Group and aggregate
        stats = df.groupby(column).agg(
            Total=("passed", "count"),
            Passed=("passed", "sum"),
        )
        stats["Accuracy"] = (stats["Passed"] / stats["Total"]).apply(lambda x: f"{x:.1%}")
        stats["Passed"] = stats["Passed"].astype(int)

        return stats

    def _repr_html_(self) -> str:
        """Rich HTML display."""
        s = self.summary
        status_color = "#22c55e" if s["pass_rate"] >= 0.8 else "#f59e0b" if s["pass_rate"] >= 0.5 else "#ef4444"

        # Build results table
        table_rows = ""
        for r in self.results:
            icon = "✓" if r.passed else "✗"
            color = "#22c55e" if r.passed else "#ef4444"
            name_cell = f"<td style='padding: 6px 12px;'>{html.escape(r.name or '')}</td>" if any(x.name for x in self.results) else ""
            input_display = r.input[:40] + "..." if len(r.input) > 40 else r.input
            tools = ", ".join(r.tools_used) if r.tools_used else "-"
            table_rows += f'''
            <tr style="border-bottom: 1px solid #f3f4f6;">
                {name_cell}
                <td style="padding: 6px 12px;">{html.escape(input_display)}</td>
                <td style="padding: 6px 12px; color: {color}; text-align: center;">{icon}</td>
                <td style="padding: 6px 12px;">{html.escape(tools)}</td>
            </tr>
            '''

        name_header = "<th style='padding: 6px 12px; text-align: left;'>Name</th>" if any(x.name for x in self.results) else ""

        # Show failures detail if any
        failures_html = ""
        failed = [r for r in self.results if not r.passed]
        if failed:
            failures_html = '<div style="margin-top: 12px; font-weight: bold; color: #ef4444;">Failed tests:</div>'
            for r in failed:
                name = f"<strong>{html.escape(r.name)}</strong>: " if r.name else ""
                reasons = []
                for m in r.measures:
                    if not m.passed:
                        reasons.append(f"{m.name}: {m.reason or 'failed'}")
                if r.expectations:
                    for e in r.expectations:
                        if not e.passed:
                            reasons.append(f"{e.name}: {e.reason or 'failed'}")
                reason_str = "; ".join(reasons) if reasons else "unknown reason"
                failures_html += f'<div style="margin-left: 12px; color: #6b7280;">• {name}{html.escape(reason_str)}</div>'

        return f'''
        <div style="font-family: system-ui, sans-serif; margin: 8px 0;">
            <div style="font-size: 18px; font-weight: bold; margin-bottom: 4px;">
                {html.escape(self.suite.name)}
            </div>
            <div style="font-size: 14px; color: {status_color}; margin-bottom: 12px;">
                {s["passed"]}/{s["total"]} tests passed ({s["pass_rate"]:.0%})
            </div>
            <table style="width: 100%; border-collapse: collapse; border: 1px solid #e5e7eb; border-radius: 8px;">
                <thead style="background: #f9fafb;">
                    <tr>
                        {name_header}
                        <th style="padding: 6px 12px; text-align: left;">Input</th>
                        <th style="padding: 6px 12px; text-align: center;">Pass</th>
                        <th style="padding: 6px 12px; text-align: left;">Tools</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
            {failures_html}
        </div>
        '''

    def __repr__(self) -> str:
        s = self.summary
        return f"EvalSuiteResults({self.suite.name!r}, {s['passed']}/{s['total']} passed)"

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self):
        return iter(self.results)

    def __getitem__(self, index):
        return self.results[index]


class EvalSuite:
    """A named collection of test cases."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.cases: List[TestCase] = []

    @classmethod
    def from_records(
        cls,
        records: List[Dict[str, Any]],
        *,
        input_fn: Callable[[Dict[str, Any]], str],
        name: str = "Dataset Evaluation",
        description: str = "",
        expect_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        tags_fn: Optional[Callable[[Dict[str, Any]], List[str]]] = None,
        name_fn: Optional[Callable[[int, Dict[str, Any]], str]] = None,
        measures: Optional[List[str]] = None,
        judges: Optional[List[Union[str, "Judge"]]] = None,
    ) -> "EvalSuite":
        """Create an EvalSuite from a list of records (dicts).

        This is a convenience method for creating test cases from datasets
        without writing repetitive loop code.

        Args:
            records: List of dicts, each representing one test case
            input_fn: Function that takes a record and returns the input prompt
            name: Name for the suite
            description: Description for the suite
            expect_fn: Optional function that takes a record and returns expect dict
            tags_fn: Optional function that takes a record and returns list of tags
            name_fn: Optional function that takes (index, record) and returns case name.
                    Defaults to "case_{index}"
            measures: Measures to run on all cases
            judges: Judges to run on all cases

        Returns:
            EvalSuite with one test case per record

        Example:
            # Create suite from MATH dataset
            suite = EvalSuite.from_records(
                math_data[:100],
                name="MATH Evaluation",
                input_fn=lambda p: f"Question:\\n{p['problem']}\\n\\nAnswer:",
                expect_fn=lambda p: {
                    'expected_answer': p['answer'],
                    'problem_type': p['type'],
                    'problem_level': p['level'],
                },
                tags_fn=lambda p: [p['type'], p['level']],
                measures=["correct_math_answer"],
            )
        """
        suite = cls(name=name, description=description)

        for i, record in enumerate(records):
            case_name = name_fn(i, record) if name_fn else f"case_{i}"
            input_text = input_fn(record)
            expect = expect_fn(record) if expect_fn else None
            tags = tags_fn(record) if tags_fn else None

            suite.add(
                name=case_name,
                input=input_text,
                expect=expect,
                measures=measures,
                judges=judges,
                tags=tags,
            )

        return suite

    def add(
        self,
        name: str,
        input: Optional[str] = None,
        *,
        prompts: Optional[List[str]] = None,
        goal: Optional[str] = None,
        persona: Optional[Dict[str, Any]] = None,
        max_exchanges: Optional[int] = None,
        expect: Optional[Dict[str, Any]] = None,
        measures: Optional[List[str]] = None,
        judges: Optional[List[Union[str, "Judge"]]] = None,
        tags: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> "EvalSuite":
        """Add a test case to the suite.

        Args:
            name: Unique name for this test case
            input: Single-turn prompt (use this OR prompts OR goal)
            prompts: Multi-turn scripted prompts (list of user messages)
            goal: Synthetic user goal (for LLM-driven multi-turn)
            persona: Synthetic user persona info (dict)
            max_exchanges: Max exchanges for multi-turn (default: 10)
            expect: Expectations dict (see check_expectations)
            measures: Custom measures to run for this case (in addition to suite-level)
            judges: LLM judges to run for this case (in addition to suite-level)
            tags: Optional tags for filtering
            description: Optional description

        Returns:
            self, for method chaining

        Examples:
            # Single-turn
            suite.add("basic", "What's the weather?")

            # Scripted multi-turn
            suite.add("booking_flow", prompts=["Book a room", "Tomorrow 2pm", "Confirm"])

            # Synthetic multi-turn
            suite.add(
                "synthetic_booking",
                goal="Book a meeting room for tomorrow at 2pm",
                persona={"name": "Alice", "department": "Engineering"},
            )
        """
        self.cases.append(TestCase(
            name=name,
            input=input,
            prompts=prompts,
            goal=goal,
            persona=persona,
            max_exchanges=max_exchanges,
            expect=expect,
            measures=measures,
            judges=judges,
            tags=tags,
            description=description,
        ))
        return self

    async def run(
        self,
        agent: Agent,
        *,
        measures: Optional[List[str]] = None,
        judges: Optional[List[Union[str, "Judge"]]] = None,
        environment: Optional["EvalEnvironment"] = None,
        include_builtin_measures: bool = True,
        tags: Optional[List[str]] = None,
        max_turns: int = 10,
        show_progress: bool = True,
    ) -> EvalSuiteResults:
        """Run all test cases against the agent.

        Args:
            agent: The agent to evaluate
            measures: Additional measures to run
            judges: LLM judges to run (applied to all cases)
            environment: Optional EvalEnvironment for fixture management
            include_builtin_measures: Include tool_hallucination etc.
            tags: Only run cases with these tags (None = all)
            max_turns: Maximum agent turns per case
            show_progress: Show progress indicator

        Returns:
            EvalSuiteResults with all test results
        """
        # Filter cases by tags if specified
        cases = self.cases
        if tags:
            cases = [c for c in cases if c.tags and any(t in c.tags for t in tags)]

        results: List[EvalResult] = []
        total = len(cases)

        for i, case in enumerate(cases):
            if show_progress:
                print(f"\rRunning {i + 1}/{total}: {case.name}...", end="", flush=True)

            # Merge suite-level measures with per-case measures
            case_measures = list(measures) if measures else []
            if case.measures:
                for m in case.measures:
                    if m not in case_measures:
                        case_measures.append(m)

            # Merge suite-level judges with per-case judges
            case_judges: List[Union[str, "Judge"]] = list(judges) if judges else []
            if case.judges:
                for j in case.judges:
                    # Avoid duplicates by name
                    j_name = j if isinstance(j, str) else j.name
                    existing_names = [
                        (jj if isinstance(jj, str) else jj.name) for jj in case_judges
                    ]
                    if j_name not in existing_names:
                        case_judges.append(j)

            # Run evaluation using the unified evaluate() function
            if case.is_multi_turn:
                if case.goal:
                    # Synthetic multi-turn
                    result = await evaluate(
                        agent,
                        goal=case.goal,
                        persona=case.persona,
                        max_exchanges=case.max_exchanges or 10,
                        measures=case_measures if case_measures else None,
                        judges=case_judges if case_judges else None,
                        environment=environment,
                        expect=case.expect,
                        include_builtin_measures=include_builtin_measures,
                        name=case.name,
                    )
                else:
                    # Scripted multi-turn
                    result = await evaluate(
                        agent,
                        prompts=case.prompts,
                        max_exchanges=case.max_exchanges or 10,
                        measures=case_measures if case_measures else None,
                        judges=case_judges if case_judges else None,
                        environment=environment,
                        expect=case.expect,
                        include_builtin_measures=include_builtin_measures,
                        name=case.name,
                    )
            else:
                # Single-turn evaluation
                result = await evaluate(
                    agent,
                    case.input,
                    measures=case_measures if case_measures else None,
                    judges=case_judges if case_judges else None,
                    environment=environment,
                    expect=case.expect,
                    include_builtin_measures=include_builtin_measures,
                    max_turns=max_turns,
                    name=case.name,
                )

            results.append(result)

        if show_progress:
            passed = sum(r.passed for r in results)
            print(f"\rCompleted: {passed}/{total} passed" + " " * 20)

        return EvalSuiteResults(suite=self, results=results)

    def __len__(self) -> int:
        return len(self.cases)

    def __repr__(self) -> str:
        return f"EvalSuite({self.name!r}, {len(self.cases)} cases)"


# -----------------------------------------------------------------------------
# Measure Decorator (alias for convenience)
# -----------------------------------------------------------------------------


def measure(
    func: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    overwrite: bool = True,
):
    """Decorator to register a custom measure function.

    This is an alias for @evaluation_measure that feels more natural in notebooks.
    By default, overwrite=True to allow re-running notebook cells without errors.

    Example:
        @measure
        def my_check(ctx):
            if ctx.tool_calls("dangerous_tool"):
                return {"passed": False, "reason": "Called dangerous tool"}
            return {"passed": True}

        # Explicitly prevent overwriting (stricter, like production code):
        @measure(overwrite=False)
        def strict_measure(ctx):
            return {"passed": True}
    """
    return evaluation_measure(func, name=name, overwrite=overwrite)


# -----------------------------------------------------------------------------
# LLM-as-Judge
# -----------------------------------------------------------------------------

# Global registry of judges
_JUDGES: Dict[str, "Judge"] = {}


class Judge:
    """An LLM-based judge for evaluating agent outputs.

    Judges use an LLM to evaluate whether an agent's response meets
    specified criteria. This is useful for subjective quality checks
    that can't be expressed as simple rules.

    Example:
        # Simple criteria-based judge
        quality = Judge(
            "quality",
            criteria="The response is helpful, accurate, and well-formatted",
        )

        # Use in evaluation
        result = await evaluate(agent, "Hello", judges=[quality])

        # Or with custom instructions
        expert = Judge(
            "domain_expert",
            instructions='''You are an expert meteorologist.
            Evaluate if the weather information is accurate and complete.
            Consider: temperature units, humidity, wind speed, conditions.''',
        )
    """

    def __init__(
        self,
        name: str,
        *,
        criteria: Optional[str] = None,
        instructions: Optional[str] = None,
        model: str = "gpt-4.1",
        include_tools: bool = True,
        include_history: bool = False,
    ):
        """Create a new judge.

        Args:
            name: Unique name for this judge
            criteria: Simple criteria description (generates instructions automatically)
            instructions: Full custom instructions for the judge (overrides criteria)
            model: Model to use for judging (default: gpt-4.1-mini for speed/cost)
            include_tools: Include tool calls in judge input
            include_history: Include full conversation history (default: just final output)
        """
        self.name = name
        self.criteria = criteria
        self.instructions = instructions
        self.model = model
        self.include_tools = include_tools
        self.include_history = include_history

        # Register globally
        _JUDGES[name] = self

    def _build_instructions(self) -> str:
        """Build the judge agent instructions."""
        if self.instructions:
            return self.instructions

        if self.criteria:
            return f'''You are an evaluation judge. Your task is to determine if an AI agent's response meets the following criteria:

**Criteria:** {self.criteria}

Evaluate the agent's response and return a JSON object with:
- "passed": true if the criteria is met, false otherwise
- "reasoning": A brief explanation of your judgment (1-2 sentences)
- "score": Optional score from 0.0 to 1.0 indicating how well the criteria was met

Be strict but fair. Focus on whether the core criteria is satisfied.'''

        return '''You are an evaluation judge. Evaluate the AI agent's response for quality and correctness.

Return a JSON object with:
- "passed": true if the response is good, false otherwise
- "reasoning": A brief explanation of your judgment
- "score": Optional score from 0.0 to 1.0'''

    def _build_prompt(
        self,
        user_input: str,
        agent_output: str,
        tools_used: List[str],
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build the prompt to send to the judge."""
        parts = [f"**User Input:** {user_input}", f"**Agent Response:** {agent_output}"]

        if self.include_tools and tools_used:
            parts.append(f"**Tools Used:** {', '.join(tools_used)}")

        if self.include_history and history:
            # Simplified history representation
            history_str = "\n".join(
                f"- {item.get('role', 'unknown')}: {str(item.get('content', ''))[:200]}"
                for item in history[:10]  # Limit to first 10 items
            )
            parts.append(f"**Conversation History:**\n{history_str}")

        return "\n\n".join(parts)

    async def evaluate(
        self,
        user_input: str,
        agent_output: str,
        tools_used: List[str],
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> JudgeResult:
        """Run the judge on an agent's output.

        Args:
            user_input: The original user input
            agent_output: The agent's final output
            tools_used: List of tool names that were called
            history: Optional conversation history

        Returns:
            JudgeResult with pass/fail and reasoning
        """
        from typing_extensions import TypedDict

        class JudgeOutput(TypedDict):
            passed: bool
            reasoning: str
            score: float

        judge_agent = Agent(
            name=f"Judge: {self.name}",
            instructions=self._build_instructions(),
            model=self.model,
            output_type=JudgeOutput,
        )

        prompt = self._build_prompt(user_input, agent_output, tools_used, history)

        try:
            result = await AgentRunner.run(judge_agent, prompt, max_turns=1)
            output = result.final_output

            if isinstance(output, dict):
                passed = bool(output.get("passed", False))
                reasoning = str(output.get("reasoning", "No reasoning provided"))
                score = output.get("score")
                if score is not None:
                    try:
                        score = float(score)
                        score = max(0.0, min(1.0, score))  # Clamp to 0-1
                    except (TypeError, ValueError):
                        score = None
            else:
                # Try to parse as pass/fail from string
                output_str = str(output).lower()
                passed = "pass" in output_str and "fail" not in output_str
                reasoning = str(output)
                score = None

            return JudgeResult(
                name=self.name,
                passed=passed,
                reasoning=reasoning,
                score=score,
                raw_output=output,
            )

        except Exception as e:
            return JudgeResult(
                name=self.name,
                passed=False,
                reasoning=f"Judge error: {str(e)}",
                score=None,
                raw_output=None,
            )

    def __repr__(self) -> str:
        criteria_preview = self.criteria[:50] + "..." if self.criteria and len(self.criteria) > 50 else self.criteria
        return f"Judge({self.name!r}, criteria={criteria_preview!r})"


def get_judge(name: str) -> Judge:
    """Get a registered judge by name."""
    if name not in _JUDGES:
        raise KeyError(f"Judge '{name}' not found. Available: {list(_JUDGES.keys())}")
    return _JUDGES[name]


async def run_judges(
    judges: List[Union[str, Judge]],
    user_input: str,
    agent_output: str,
    tools_used: List[str],
    history: Optional[List[Dict[str, Any]]] = None,
) -> List[JudgeResult]:
    """Run multiple judges and collect results.

    Args:
        judges: List of judge names or Judge instances
        user_input: The original user input
        agent_output: The agent's final output
        tools_used: Tools that were called
        history: Optional conversation history

    Returns:
        List of JudgeResult objects
    """
    results: List[JudgeResult] = []

    for judge in judges:
        if isinstance(judge, str):
            judge = get_judge(judge)
        result = await judge.evaluate(user_input, agent_output, tools_used, history)
        results.append(result)

    return results


# -----------------------------------------------------------------------------
# Multi-Turn Evaluation
# -----------------------------------------------------------------------------


class SyntheticUser:
    """A simulated user that role-plays to achieve a goal.

    Uses an LLM to generate realistic user responses based on a goal and
    optional persona information. The conversation continues until the
    goal is achieved, the user gives up, or max exchanges is reached.

    Note: You typically don't create SyntheticUser directly. Instead, use
    the `goal` and `persona` parameters of `evaluate()`:

        result = await evaluate(agent,
            goal="Book a meeting room for tomorrow at 2pm",
            persona={"name": "Alice", "email": "alice@example.com"})
    """

    def __init__(
        self,
        goal: str,
        *,
        persona: Optional[Dict[str, Any]] = None,
        initial_message: Optional[str] = None,
        instructions: Optional[str] = None,
        model: str = "gpt-4.1",
    ):
        """Create a synthetic user.

        Args:
            goal: What the user is trying to accomplish
            persona: Optional dict of user info (name, email, preferences, etc.)
            initial_message: Optional specific first message (otherwise generated)
            instructions: Optional custom system prompt (overrides default)
            model: Model to use for generating responses
        """
        self.goal = goal
        self.persona = persona or {}
        self.initial_message = initial_message
        self.instructions = instructions
        self.model = model
        self._agent: Optional[Agent] = None
        self._history: List[Dict[str, str]] = []
        self._completed = False
        self._completion_reason: Optional[str] = None

    def _build_system_prompt(self, agent_name: str) -> str:
        """Build the system prompt for the simulated user."""
        if self.instructions:
            return self.instructions

        # Build persona info block
        info_lines = []
        for key, value in self.persona.items():
            label = key.replace("_", " ").title()
            info_lines.append(f"- {label}: {value}")
        info_block = "\n".join(info_lines) if info_lines else "- No specific information provided"

        return f"""You are a simulated user interacting with an AI assistant called "{agent_name}".

## Your Goal
{self.goal}

## Your Information
{info_block}

## Instructions
1. Respond naturally to the assistant's questions and messages
2. Provide your information when asked (based on what you know above)
3. Stay in character throughout the conversation
4. If the assistant asks for confirmation of correct details, confirm them
5. If the assistant provides incorrect information, politely correct them

## Completion
- When your goal has been achieved (e.g., task confirmed), end your message with [DONE]
- If you've been trying for several turns and the assistant cannot help, end with [GIVE_UP]
- Do NOT say [DONE] until you have received confirmation that your goal was accomplished

## Important
- You are the USER, not the assistant
- Keep responses concise and natural
- Don't explain what you're doing, just do it
"""

    def _get_agent(self, agent_name: str) -> Agent:
        """Get or create the underlying LLM agent."""
        if self._agent is None:
            self._agent = Agent(
                name="SyntheticUser",
                instructions=self._build_system_prompt(agent_name),
                model=self.model,
            )
        return self._agent

    @property
    def completed(self) -> bool:
        """Whether the user has signaled completion."""
        return self._completed

    @property
    def completion_reason(self) -> Optional[str]:
        """Why the user completed: 'done', 'give_up', or None."""
        return self._completion_reason

    async def get_initial_message(self, agent_name: str = "Assistant") -> str:
        """Get the user's first message to start the conversation."""
        if self.initial_message:
            self._history.append({"role": "assistant", "content": self.initial_message})
            return self.initial_message

        # Generate an initial message based on the goal
        prompt = f"""Based on your goal, write a natural opening message to start the conversation.
Your goal: {self.goal}

Write only the message, nothing else. Be natural and conversational."""

        result = await AgentRunner.run(
            self._get_agent(agent_name), prompt, max_turns=1
        )
        response = str(result.final_output or "")
        self._history.append({"role": "assistant", "content": response})
        return response

    async def respond(self, assistant_message: str, agent_name: str = "Assistant") -> str:
        """Generate the user's response to the assistant's message."""
        if self._completed:
            return "[DONE] (already completed)"

        # Add the real agent's message to history
        self._history.append({"role": "user", "content": assistant_message})

        # Generate response with full conversation context
        result = await AgentRunner.run(
            self._get_agent(agent_name),
            self._history.copy(),
            max_turns=1,
        )
        response = str(result.final_output or "")
        self._history.append({"role": "assistant", "content": response})

        # Check for completion signals
        response_upper = response.upper()
        if "[DONE]" in response_upper or "[GOAL_ACHIEVED]" in response_upper:
            self._completed = True
            self._completion_reason = "done"
        elif "[GIVE_UP]" in response_upper:
            self._completed = True
            self._completion_reason = "give_up"

        return response

    def reset(self) -> None:
        """Reset the user for a new conversation."""
        self._history = []
        self._completed = False
        self._completion_reason = None
        self._agent = None

    def __repr__(self) -> str:
        goal_preview = self.goal[:40] + "..." if len(self.goal) > 40 else self.goal
        return f"SyntheticUser(goal={goal_preview!r})"


async def _evaluate_multi_turn(
    agent: Agent,
    user: Union[SyntheticUser, List[str]],
    *,
    max_exchanges: int,
    measure_names: List[str],
    judges: Optional[List[Union[str, Judge]]],
    environment: Optional[EvalEnvironment],
    expect: Optional[Dict[str, Any]],
    include_builtin_measures: bool,
    name: Optional[str],
) -> EvalResult:
    """Run a multi-turn evaluation (internal implementation)."""
    turns: List[Turn] = []
    all_history: List[Dict[str, Any]] = []
    all_tools_used: List[str] = []
    error: Optional[str] = None
    completion_reason: Optional[str] = None
    final_output = ""
    ctx: Optional[EvalContext] = None
    env_context: Optional[EnvironmentContext] = None
    measure_results: List[MeasureResult] = []
    expectation_results: Optional[List[ExpectationResult]] = None

    agent_name = agent.name if hasattr(agent, "name") and agent.name else "Assistant"

    # Build scenario dict for environment
    goal = user.goal if isinstance(user, SyntheticUser) else None
    scenario = {"name": name, "expect": expect, "goal": goal}

    try:
        # Setup environment if provided
        if environment is not None:
            env_context = await environment.setup(scenario)

        if isinstance(user, list):
            # Scripted multi-turn: list of user messages
            prompts = user
            conversation_input: List[Dict[str, Any]] = []

            for i, user_msg in enumerate(prompts):
                if i >= max_exchanges:
                    completion_reason = "max_turns"
                    break

                # Add user message
                turns.append(Turn(role="user", content=user_msg))
                conversation_input.append({"role": "user", "content": user_msg})

                # Run agent
                run_result = await AgentRunner.run(
                    agent,
                    conversation_input.copy(),
                    max_turns=10,
                )

                # Extract assistant response
                assistant_output = str(run_result.final_output or "")
                final_output = assistant_output

                # Get tools used in this turn
                turn_tools: List[str] = []
                if hasattr(run_result, "new_items"):
                    for item in run_result.new_items:
                        if hasattr(item, "type") and item.type == "function_call_output":
                            if hasattr(item, "name"):
                                turn_tools.append(item.name)
                        elif hasattr(item, "call_id"):
                            # Tool call item - get name from the call
                            if hasattr(item, "name"):
                                turn_tools.append(item.name)

                # Try to get tool names from the run result
                if hasattr(run_result, "to_input_list"):
                    new_items = run_result.to_input_list()
                    for item in new_items:
                        if isinstance(item, dict) and item.get("type") == "function_call":
                            tool_name = item.get("name", "")
                            if tool_name and tool_name not in turn_tools:
                                turn_tools.append(tool_name)

                all_tools_used.extend(turn_tools)
                turns.append(Turn(role="assistant", content=assistant_output, tools_used=turn_tools))

                # Update conversation for next turn
                if hasattr(run_result, "to_input_list"):
                    conversation_input = run_result.to_input_list()
                else:
                    conversation_input.append({"role": "assistant", "content": assistant_output})

                all_history = conversation_input.copy()

            completion_reason = completion_reason or "done"

        else:
            # Synthetic multi-turn: SyntheticUser
            synthetic_user = user
            synthetic_user.reset()

            # Get initial message from synthetic user
            user_msg = await synthetic_user.get_initial_message(agent_name)
            turns.append(Turn(role="user", content=user_msg))
            conversation_input = [{"role": "user", "content": user_msg}]

            for exchange in range(max_exchanges):
                # Run agent
                run_result = await AgentRunner.run(
                    agent,
                    conversation_input.copy(),
                    max_turns=10,
                )

                # Extract assistant response
                assistant_output = str(run_result.final_output or "")
                final_output = assistant_output

                # Get tools used in this turn
                turn_tools: List[str] = []
                if hasattr(run_result, "to_input_list"):
                    new_items = run_result.to_input_list()
                    for item in new_items:
                        if isinstance(item, dict) and item.get("type") == "function_call":
                            tool_name = item.get("name", "")
                            if tool_name and tool_name not in turn_tools:
                                turn_tools.append(tool_name)

                all_tools_used.extend(turn_tools)
                turns.append(Turn(role="assistant", content=assistant_output, tools_used=turn_tools))

                # Update conversation
                if hasattr(run_result, "to_input_list"):
                    conversation_input = run_result.to_input_list()
                else:
                    conversation_input.append({"role": "assistant", "content": assistant_output})

                # Check if synthetic user is done
                if synthetic_user.completed:
                    completion_reason = synthetic_user.completion_reason
                    break

                # Get next user response
                if exchange < max_exchanges - 1:
                    user_msg = await synthetic_user.respond(assistant_output, agent_name)
                    turns.append(Turn(role="user", content=user_msg))
                    conversation_input.append({"role": "user", "content": user_msg})

                    if synthetic_user.completed:
                        completion_reason = synthetic_user.completion_reason
                        break

            if not completion_reason:
                completion_reason = "max_turns"

            all_history = conversation_input.copy()

        # Build evaluation context from full history
        ctx = build_eval_context(
            history=all_history,
            tools=agent.tools if hasattr(agent, "tools") else [],
            agent=agent,
            metadata={"scenario": scenario, "environment_context": env_context.data if env_context else {}},
        )

        # Wire environment into context for verify() and get_env_data()
        if environment is not None and env_context is not None:
            ctx._environment = environment
            ctx._environment_context = env_context

        # Run measures BEFORE teardown (so they can access environment state)
        if measure_names and ctx:
            raw_results = _run_measures(ctx, measure_names)
            measure_results = [MeasureResult.from_dict(r) for r in raw_results]

        # Check expectations BEFORE teardown (verify needs database state)
        if expect and ctx:
            if "verify" in expect:
                expectation_results = await check_expectations_async(ctx, expect)
            else:
                expectation_results = check_expectations(ctx, expect)

    except Exception as e:
        error = str(e)

    finally:
        # Always teardown environment
        if environment is not None and env_context is not None:
            try:
                await environment.teardown(env_context)
            except Exception as teardown_err:
                teardown_msg = f"teardown failed: {teardown_err}"
                if error:
                    error = f"{error}; {teardown_msg}"
                else:
                    error = teardown_msg

    # Run judges
    judge_results: Optional[List[JudgeResult]] = None
    if judges:
        # For multi-turn, use the full conversation as context
        first_user_msg = turns[0].content if turns else ""
        judge_results = await run_judges(
            judges=judges,
            user_input=first_user_msg,
            agent_output=final_output,
            tools_used=list(set(all_tools_used)),
            history=all_history,
        )

    # Determine pass/fail
    passed = True
    if error:
        passed = False
    elif measure_results and not all(m.passed for m in measure_results):
        passed = False
    elif expectation_results and not all(e.passed for e in expectation_results):
        passed = False
    elif judge_results and not all(j.passed for j in judge_results):
        passed = False
    # For synthetic, goal not achieved = fail
    elif isinstance(user, SyntheticUser) and completion_reason != "done":
        passed = False

    # Build input string for display
    if isinstance(user, list):
        input_display = " → ".join(user[:3])
        if len(user) > 3:
            input_display += f" → ... ({len(user)} messages)"
    else:
        input_display = f"[Synthetic] {user.goal}"

    return EvalResult(
        input=input_display,
        output=final_output,
        passed=passed,
        measures=measure_results,
        expectations=expectation_results,
        judges=judge_results,
        ctx=ctx,
        history=all_history,
        error=error,
        name=name,
        turns=turns,
        completion_reason=completion_reason,
    )


# -----------------------------------------------------------------------------
# Main evaluate() Function
# -----------------------------------------------------------------------------


async def evaluate(
    agent: Agent,
    input: Optional[str] = None,
    *,
    # Multi-turn: scripted
    prompts: Optional[List[str]] = None,
    # Multi-turn: synthetic
    goal: Optional[str] = None,
    persona: Optional[Dict[str, Any]] = None,
    # Shared options
    expect: Optional[Dict[str, Any]] = None,
    measures: Optional[List[str]] = None,
    judges: Optional[List[Union[str, Judge]]] = None,
    environment: Optional[EvalEnvironment] = None,
    include_builtin_measures: bool = True,
    name: Optional[str] = None,
    # Turn limits
    max_turns: int = 10,
    max_exchanges: int = 10,
) -> EvalResult:
    """Evaluate an agent with a single test case.

    This function runs ONE test case and returns ONE result. For running
    multiple test cases, use EvalSuite.

    There are three modes:

    **Single-turn**: Provide `input` - one prompt, one response.

    **Multi-turn (scripted)**: Provide `prompts` - a sequence of user messages
    sent in order, with conversation history maintained between them.

    **Multi-turn (synthetic)**: Provide `goal` (and optionally `persona`) -
    an LLM role-plays as a user trying to achieve the goal.

    Args:
        agent: The agent to evaluate

        # Input modes (mutually exclusive)
        input: Single-turn prompt
        prompts: Multi-turn scripted conversation (list of user messages)
        goal: Multi-turn synthetic user goal
        persona: Synthetic user persona info (used with goal)

        # Shared options
        expect: Expectations dict (tool_called, response_contains, verify, etc.)
        measures: Custom measures to run (by name)
        judges: LLM judges to run (Judge instances or names)
        environment: EvalEnvironment for fixture management
        include_builtin_measures: Include tool_hallucination measure
        name: Name for this test case

        # Turn limits
        max_turns: Maximum agent turns (tool calls) per response
        max_exchanges: Maximum user-agent exchanges for multi-turn

    Returns:
        EvalResult with pass/fail status, measures, expectations, etc.

    Examples:
        # Single-turn
        result = await evaluate(agent, "What's the weather?")

        # Single-turn with expectations
        result = await evaluate(agent, "Weather in Paris?",
                                expect={"tool_called": "get_weather"})

        # Multi-turn scripted
        result = await evaluate(agent,
            prompts=["Book a room", "Tomorrow 2pm", "Confirm"],
            expect={"tool_called": "create_booking"})

        # Multi-turn synthetic
        result = await evaluate(agent,
            goal="Book a meeting room for tomorrow at 2pm",
            persona={"name": "Alice", "email": "alice@example.com"})

        # With judges
        quality = Judge("quality", criteria="Response is helpful")
        result = await evaluate(agent, "Hello", judges=[quality])

        # With environment
        result = await evaluate(agent,
            goal="Book a room",
            environment=BookingEnv(),
            expect={"verify": {"booking_created": True}})

        # For multiple test cases, use EvalSuite:
        suite = EvalSuite("My Tests")
        suite.add("weather", "Weather?", expect={"tool_called": "get_weather"})
        suite.add("booking", prompts=["Book a room", ...])
        results = await suite.run(agent)
    """
    # Validate input modes are mutually exclusive
    modes_provided = sum([
        input is not None,
        prompts is not None,
        goal is not None,
    ])

    if modes_provided == 0:
        raise ValueError("Must provide one of: input, prompts, or goal")
    if modes_provided > 1:
        raise ValueError("Only one of input, prompts, or goal can be provided")

    # Build measure list
    measure_names: List[str] = []
    if include_builtin_measures and "tool_hallucination" in _MEASURES:
        measure_names.append("tool_hallucination")
    if measures:
        measure_names.extend(measures)

    # Route to appropriate handler
    if input is not None:
        # Single-turn
        return await _evaluate_single_turn(
            agent=agent,
            input_text=input,
            measure_names=measure_names,
            judges=judges,
            environment=environment,
            expect=expect,
            name=name,
            max_turns=max_turns,
        )
    elif prompts is not None:
        # Multi-turn scripted
        return await _evaluate_multi_turn(
            agent=agent,
            user=prompts,
            max_exchanges=max_exchanges,
            measure_names=measure_names,
            judges=judges,
            environment=environment,
            expect=expect,
            include_builtin_measures=include_builtin_measures,
            name=name,
        )
    else:
        # Multi-turn synthetic
        synthetic_user = SyntheticUser(goal=goal, persona=persona)
        return await _evaluate_multi_turn(
            agent=agent,
            user=synthetic_user,
            max_exchanges=max_exchanges,
            measure_names=measure_names,
            judges=judges,
            environment=environment,
            expect=expect,
            include_builtin_measures=include_builtin_measures,
            name=name,
        )


async def _evaluate_single_turn(
    agent: Agent,
    input_text: str,
    measure_names: List[str],
    judges: Optional[List[Union[str, Judge]]],
    environment: Optional[EvalEnvironment],
    expect: Optional[Dict[str, Any]],
    name: Optional[str],
    max_turns: int,
) -> EvalResult:
    """Run a single-turn evaluation with optional environment support."""
    error: Optional[str] = None
    output = ""
    history: List[Dict[str, Any]] = []
    ctx: Optional[EvalContext] = None
    measure_results: List[MeasureResult] = []
    expectation_results: Optional[List[ExpectationResult]] = None
    judge_results: Optional[List[JudgeResult]] = None
    tools_used: List[str] = []
    env_context: Optional[EnvironmentContext] = None

    # Build scenario dict for environment setup
    scenario = {"input": input_text, "name": name, "expect": expect}

    try:
        # Setup environment if provided
        if environment is not None:
            env_context = await environment.setup(scenario)

        # Run the agent
        run_result = await AgentRunner.run(agent, input=input_text, max_turns=max_turns)

        # Extract output and history
        output = str(run_result.final_output or "")

        # Get history from result
        if hasattr(run_result, "to_input_list"):
            history = run_result.to_input_list()
        elif hasattr(run_result, "new_items"):
            # Convert items to input list format
            history = [item.to_input_item() for item in run_result.new_items if hasattr(item, "to_input_item")]

        # Build evaluation context
        ctx = build_eval_context(
            history=history,
            tools=agent.tools if hasattr(agent, "tools") else [],
            agent=agent,
            metadata={"scenario": scenario, "environment_context": env_context.data if env_context else {}},
        )

        # Wire environment into context for verify() and get_env_data()
        if environment is not None and env_context is not None:
            ctx._environment = environment
            ctx._environment_context = env_context

        # Get tools used for judges
        tools_used = [c.name for c in ctx.tool_calls() if c.name]

        # Run measures
        if measure_names:
            raw_results = _run_measures(ctx, measure_names)
            measure_results = [MeasureResult.from_dict(r) for r in raw_results]

        # Check expectations (use async version if verify is present)
        if expect:
            if "verify" in expect:
                expectation_results = await check_expectations_async(ctx, expect)
            else:
                expectation_results = check_expectations(ctx, expect)

        # Run judges
        if judges:
            judge_results = await run_judges(
                judges=judges,
                user_input=input_text,
                agent_output=output,
                tools_used=tools_used,
                history=history,
            )

    except Exception as e:
        error = str(e)

    finally:
        # Always teardown environment
        if environment is not None and env_context is not None:
            try:
                await environment.teardown(env_context)
            except Exception as teardown_err:
                # Append teardown error to existing error or set it
                teardown_msg = f"teardown failed: {teardown_err}"
                if error:
                    error = f"{error}; {teardown_msg}"
                else:
                    error = teardown_msg

    # Determine overall pass/fail
    passed = True
    if error:
        passed = False
    elif measure_results and not all(m.passed for m in measure_results):
        passed = False
    elif expectation_results and not all(e.passed for e in expectation_results):
        passed = False
    elif judge_results and not all(j.passed for j in judge_results):
        passed = False

    return EvalResult(
        input=input_text,
        output=output,
        passed=passed,
        measures=measure_results,
        expectations=expectation_results,
        judges=judge_results,
        ctx=ctx,
        history=history,
        error=error,
        name=name,
    )


# -----------------------------------------------------------------------------
# Convenience re-exports
# -----------------------------------------------------------------------------

# Re-export for users who want to write custom measures
__all__ = [
    # Main function
    "evaluate",
    # Result types
    "EvalResult",
    "Turn",
    # Test suites
    "EvalSuite",
    "EvalSuiteResults",
    "TestCase",
    # Measures
    "MeasureResult",
    "ExpectationResult",
    "measure",
    "check_expectations",
    "check_expectations_async",
    # Judges
    "JudgeResult",
    "Judge",
    "get_judge",
    "run_judges",
    # Multi-turn
    "SyntheticUser",
    # From core (for advanced users)
    "EvalContext",
    "ToolCall",
    "build_eval_context",
    "pass_reason",
    "fail_reason",
    # Environment support
    "EvalEnvironment",
    "EnvironmentContext",
    "NoOpEnvironment",
]
