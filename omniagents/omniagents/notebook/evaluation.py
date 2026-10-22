"""Notebook-friendly evaluation API for omniagents.

A DataFrame-native, visually rich layer over the evaluation engine
(:mod:`omniagents.core.eval.engine`) - the same engine ``omniagents eval
suite run`` uses. Cases built here become ordinary scenario dicts
(:meth:`EvalSuite.to_config` shows them), run through
:func:`~omniagents.core.eval.engine.run_suite`, and come back as
:class:`EvalResult` objects wrapping the engine's run records. Nothing about
running, judging or scoring is implemented here, so a suite gives the same
outcomes, metrics, intervals and manifest here as from the CLI.

- ``EvalSuite.from_project(path)`` loads a project's ``evaluations/``
  folder exactly as the CLI does; ``await suite.run()`` runs the project
  agent, ``await suite.run(agent)`` an ``agents.Agent`` built in code.
- ``EvalSuite("name").add(...)`` builds cases by hand.
"""

from __future__ import annotations

import copy
import dataclasses
import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple, Any, Callable, Dict, List, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


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
from omniagents.core.evaluation.expectations import (
    check_expectations as _check_expectations,
    check_expectations_async as _check_expectations_async,
    has_expectations,
)
from omniagents.core.evaluation.registry import evaluation_measure
from omniagents.core.eval.environment import (
    EvalEnvironment,
    EnvironmentContext,
    NoOpEnvironment,
)
from omniagents.core.eval.measure_tiers import DEFAULT_TIER, parse_measures
from omniagents.core.eval.metrics import group_runs, run_passed, summarize_runs
from omniagents.core.eval.simulated_user import SimulatedUser, UserPersona
from omniagents.core.eval.targets import (
    AgentTarget,
    ProjectAgentTarget,
    SdkAgentTarget,
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
            extra={
                k: v for k, v in data.items() if k not in ("name", "passed", "reason")
            },
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
        return f"""
        <div style="border-left: 3px solid {color}; padding-left: 8px; margin: 4px 0;">
            <span style="color: {color}; font-weight: bold;">{icon} {html.escape(self.name)}{score_html}</span>
            <div style="color: #6b7280; font-size: 0.9em; margin-top: 2px;">{html.escape(self.reasoning)}</div>
        </div>
        """


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
        content_display = (
            self.content[:200] + "..." if len(self.content) > 200 else self.content
        )
        return f"""
        <div style="margin: 4px 0; padding: 8px; background: #f9fafb; border-radius: 4px;">
            <span style="color: {role_color}; font-weight: bold;">{role_label}:</span>{tools_html}
            <div style="margin-top: 4px;">{html.escape(content_display)}</div>
        </div>
        """


@dataclass
class EvalResult:
    """One run of one case: a view over the engine's run record (``record``)."""

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
    completion_reason: Optional[str] = (
        None  # For synthetic: "done", "give_up", "max_turns"
    )
    trial: int = 0  # Which repeat of the case this is (EvalSuite.run(repeats=N))
    metadata: Optional[Dict[str, Any]] = None  # The case's metadata, for slicing
    record: Optional[Dict[str, Any]] = None  # The engine's run record, verbatim

    @property
    def answered(self) -> bool:
        """Whether the agent produced a final answer at all."""
        if self.record is not None and "answered" in self.record:
            return bool(self.record["answered"])
        return bool((self.output or "").strip())

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
                judges_html += f"""
                <div style="border-left: 3px solid {color}; padding-left: 8px; margin: 4px 0;">
                    <span style="color: {color}; font-weight: bold;">{icon} {html.escape(j.name)}{score_str}</span>
                    <div style="color: #6b7280; font-size: 0.9em;">{html.escape(j.reasoning[:150] + "..." if len(j.reasoning) > 150 else j.reasoning)}</div>
                </div>
                """
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
                role_color = (
                    "#6b7280" if turn.role == "user" else "#059669"
                )  # Gray for user, green for assistant
                tools_badge = ""
                if turn.tools_used:
                    tools_badge = f' <span style="color: #9ca3af; font-size: 0.8em;">({", ".join(turn.tools_used)})</span>'
                content_display = (
                    turn.content[:150] + "..."
                    if len(turn.content) > 150
                    else turn.content
                )
                turns_html += f"""
                <div style="margin: 4px 0; padding: 6px 8px; background: white; border-radius: 4px; border: 1px solid #e5e7eb; border-left: 3px solid {role_color};">
                    <span style="color: {role_color}; font-weight: 600; font-size: 0.85em;">{role_label}</span>{tools_badge}
                    <div style="margin-top: 2px; font-size: 0.9em; color: #374151;">{html.escape(content_display)}</div>
                </div>
                """
            turns_html += "</td></tr>"

            # Completion reason for synthetic
            completion_html = ""
            if self.completion_reason:
                reason_color = (
                    "#22c55e"
                    if self.completion_reason == "done"
                    else (
                        "#f59e0b"
                        if self.completion_reason == "max_turns"
                        else "#ef4444"
                    )
                )
                reason_label = {
                    "done": "Goal achieved",
                    "give_up": "User gave up",
                    "max_turns": "Max turns reached",
                }.get(self.completion_reason, self.completion_reason)
                completion_html = f'<tr style="border-bottom: 1px solid #f3f4f6;"><td style="padding: 8px 12px;"><strong>Completion</strong></td><td style="padding: 8px 12px; color: {reason_color};">{reason_label}</td></tr>'

            return f"""
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
            """

        # Single-turn display (original)
        input_display = (
            self.input[:100] + "..." if len(self.input) > 100 else self.input
        )
        output_display = (
            self.output[:200] + "..." if len(self.output) > 200 else self.output
        )

        return f"""
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
        """

    def __repr__(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        return f"EvalResult({status}, input={self.input[:30]!r}..., tools={self.tools_used})"


def _input_display(record: Dict[str, Any]) -> str:
    if record.get("type") == "synthetic":
        return f"[Synthetic] {record.get('goal') or ''}"
    prompts = record.get("prompts")
    if isinstance(prompts, list):
        shown = " → ".join(str(p) for p in prompts[:3])
        if len(prompts) > 3:
            shown += f" → ... ({len(prompts)} messages)"
        return shown
    return str(record.get("prompt") or "")


def _turns_from_history(history: List[Dict[str, Any]]) -> List[Turn]:
    """User and assistant turns, each assistant turn with the tools it called."""
    from omniagents.core.evaluation.context import _extract_message_text

    turns: List[Turn] = []
    pending_tools: List[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call" and item.get("name"):
            pending_tools.append(str(item["name"]))
            continue
        role = item.get("role")
        if role == "user":
            turns.append(Turn(role="user", content=_extract_message_text(item)))
            pending_tools = []
        elif role == "assistant":
            text = _extract_message_text(item)
            if turns and turns[-1].role == "assistant":
                # Several assistant messages in one answer read as one turn.
                turns[-1].content = (turns[-1].content + "\n" + text).strip()
                turns[-1].tools_used.extend(pending_tools)
            else:
                turns.append(
                    Turn(role="assistant", content=text, tools_used=pending_tools)
                )
            pending_tools = []
    return turns


def _judge_result(name: str, output: Any) -> JudgeResult:
    if not isinstance(output, dict) or "error" in output:
        reason = (output or {}).get("error") if isinstance(output, dict) else output
        return JudgeResult(
            name=name,
            passed=False,
            reasoning=f"Judge error: {reason}",
            raw_output=output,
        )
    answer = str(output.get("answer") or "")
    score = output.get("score")
    try:
        score = max(0.0, min(1.0, float(score))) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return JudgeResult(
        name=name,
        passed=answer.strip().lower() == "pass",
        reasoning=str(output.get("reasoning") or "No reasoning provided"),
        score=score,
        raw_output=output,
    )


def _result_from_record(
    record: Dict[str, Any], judge_keys: List[str], tools: Optional[List[Any]]
) -> EvalResult:
    scenario = record.get("scenario_config") or {}
    history = list(record.get("history") or [])
    run_metadata = record.get("metadata") or {}
    ctx = build_eval_context(
        history=history,
        tools=tools,
        metadata={"scenario": scenario, **run_metadata},
    )
    expectations: Optional[List[ExpectationResult]] = None
    for m in record.get("measures") or []:
        if m.get("name") == "expectations":
            expectations = [
                ExpectationResult(c["name"], bool(c["passed"]), c.get("reason"))
                for c in m.get("checks") or []
            ]
    judges = [
        _judge_result(k, run_metadata[k]) for k in judge_keys if k in run_metadata
    ]
    error: Optional[str] = None
    if record.get("error"):
        err = record["error"]
        error = err.get("message") if isinstance(err, dict) else str(err)
    else:
        agent_error = next(
            (
                m
                for m in record.get("measures") or []
                if m.get("name") == "_agent_error"
            ),
            None,
        )
        if agent_error is not None:
            error = agent_error.get("reason")
    synthetic = record.get("type") == "synthetic"
    multi = synthetic or isinstance(record.get("prompts"), list)
    return EvalResult(
        input=_input_display(record),
        output=record.get("final_assistant_text") or "",
        passed=run_passed(record),
        measures=[MeasureResult.from_dict(m) for m in record.get("measures") or []],
        expectations=expectations,
        judges=judges or None,
        ctx=ctx,
        history=history,
        error=error,
        name=scenario.get("name") or record.get("scenario"),
        turns=_turns_from_history(history) if multi else None,
        completion_reason=(
            (record.get("user_completion_reason") or "max_turns") if synthetic else None
        ),
        trial=int(record.get("trial") or 0),
        metadata=scenario.get("metadata"),
        record=record,
    )


def _record_of(result: EvalResult) -> Dict[str, Any]:
    """The run record behind ``result`` (synthesized for a hand-made result)."""
    if result.record is not None:
        return result.record
    return {
        "measures": [{"name": "passed", "passed": result.passed}],
        "answered": result.answered,
        "scenario_config": {
            "name": result.name,
            "metadata": result.metadata or {},
        },
    }


# -----------------------------------------------------------------------------
# Expectations DSL
# -----------------------------------------------------------------------------


def check_expectations(
    ctx: EvalContext, expect: Dict[str, Any]
) -> List[ExpectationResult]:
    """Check ``expect``'s keys against ``ctx``.

    The keys are those of :mod:`omniagents.core.evaluation.expectations`
    (``tool_called``, ``tool_not_called``, ``tool_sequence``,
    ``tool_called_times``, ``response_contains``, ``response_not_contains``);
    ``verify`` needs :func:`check_expectations_async`. A case's ``expect``
    is checked during a run by the builtin ``expectations`` measure, which
    :meth:`EvalSuite.add` attaches when these keys are used.
    """
    return [
        ExpectationResult(c["name"], c["passed"], c["reason"])
        for c in _check_expectations(ctx, expect)
    ]


async def check_expectations_async(
    ctx: EvalContext, expect: Dict[str, Any]
) -> List[ExpectationResult]:
    """:func:`check_expectations` plus ``verify`` against the context's environment."""
    return [
        ExpectationResult(c["name"], c["passed"], c["reason"])
        for c in await _check_expectations_async(ctx, expect)
    ]


# -----------------------------------------------------------------------------
# Test Suite
# -----------------------------------------------------------------------------

# Scenario keys a TestCase models as fields; everything else rides in ``extra``.
_CASE_KEYS = (
    "name",
    "prompt",
    "prompts",
    "goal",
    "persona",
    "expect",
    "measures",
    "judges",
    "tags",
    "description",
    "max_exchanges",
    "metadata",
)


@dataclass
class TestCase:
    """A single test case in an evaluation suite: one scenario.

    :meth:`to_scenario` is the scenario dict the engine runs - the same
    shape as an entry in ``evaluations/scenarios/*.yml``.
    """

    __test__ = False  # not a pytest test class

    name: str
    input: Optional[str] = (
        None  # Single-turn input (mutually exclusive with prompts/goal)
    )
    prompts: Optional[List[str]] = None  # Multi-turn scripted prompts
    goal: Optional[str] = None  # Synthetic user goal
    persona: Optional[Dict[str, Any]] = None  # Synthetic user persona
    expect: Optional[Dict[str, Any]] = None
    # Measure names, or a tier-keyed dict ({"outcome": [...], "process": [...]})
    measures: Optional[Union[List[str], Dict[str, List[str]]]] = None
    judges: Optional[List[Union[str, "Judge"]]] = None  # Per-case judges
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    max_exchanges: Optional[int] = None  # Synthetic: max user-agent exchanges
    metadata: Optional[Dict[str, Any]] = None  # Fields to slice results by
    extra: Dict[str, Any] = field(default_factory=dict)  # Other scenario keys

    @property
    def is_multi_turn(self) -> bool:
        """Whether this is a multi-turn test case."""
        return self.prompts is not None or self.goal is not None

    @classmethod
    def from_scenario(cls, scenario: Dict[str, Any]) -> "TestCase":
        """A case for a scenario dict from an evaluation config, kept verbatim."""
        sc = copy.deepcopy(scenario)
        judges = sc.get("judges")
        extra = {k: v for k, v in sc.items() if k not in _CASE_KEYS}
        if not isinstance(judges, list) and "judges" in sc:
            extra["judges"] = judges
        if extra.get("type") == "synthetic":
            extra.pop("type")
        return cls(
            name=str(sc.get("name") or sc.get("id") or "scenario"),
            input=sc.get("prompt"),
            prompts=sc.get("prompts"),
            goal=sc.get("goal"),
            persona=sc.get("persona"),
            expect=sc.get("expect"),
            measures=sc.get("measures"),
            judges=judges if isinstance(judges, list) else None,
            tags=sc.get("tags"),
            description=sc.get("description"),
            max_exchanges=sc.get("max_exchanges"),
            metadata=sc.get("metadata"),
            extra=extra,
        )

    def to_scenario(
        self,
        *,
        measures: Optional[List[str]] = None,
        judges: Optional[List[Union[str, "Judge"]]] = None,
        config_judges: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """The scenario dict for this case.

        ``measures`` and ``judges`` are run-level additions
        (:meth:`EvalSuite.run`), placed before the case's own. A notebook
        :class:`Judge` contributes its ``<name>_agent`` measure (which also
        selects the judge, by the engine's ``_agent`` convention); any other
        judge named by string - one of ``config_judges``, the evaluation
        config's - is listed under the scenario's ``judges``.
        """
        sc: Dict[str, Any] = copy.deepcopy(self.extra)
        sc["name"] = self.name
        if self.input is not None:
            sc["prompt"] = self.input
        if self.prompts is not None:
            sc["prompts"] = list(self.prompts)
        if self.goal is not None:
            sc["type"] = "synthetic"
            sc["goal"] = self.goal
        for key in ("persona", "expect", "tags", "description", "metadata"):
            value = getattr(self, key)
            if value is not None:
                sc[key] = copy.deepcopy(value)
        if self.max_exchanges is not None:
            sc["max_exchanges"] = self.max_exchanges

        added: List[str] = list(measures or [])
        config_judges: List[str] = []
        for j in list(judges or []) + list(self.judges or []):
            judge = _notebook_judge(j, config_judges or [])
            if judge is None:
                if j not in config_judges:
                    config_judges.append(str(j))
            else:
                added.append(judge.measure_name)
        if config_judges:
            prior = sc.get("judges")
            prior_list = prior if isinstance(prior, list) else []
            sc["judges"] = prior_list + [
                j for j in config_judges if j not in prior_list
            ]

        own_flat, _ = parse_measures(self.measures)
        extra_measures = [
            m for i, m in enumerate(added) if m not in added[:i] and m not in own_flat
        ]
        if isinstance(self.measures, dict):
            tiers = copy.deepcopy(self.measures)
            if extra_measures:
                tiers[DEFAULT_TIER] = extra_measures + list(
                    tiers.get(DEFAULT_TIER) or []
                )
            sc["measures"] = tiers
        elif extra_measures or self.measures is not None:
            sc["measures"] = extra_measures + list(self.measures or [])
        return sc


def _rate(block: Dict[str, Any]) -> Dict[str, Any]:
    interval = block.get("interval") or [0.0, 0.0]
    return {
        "runs": block["runs"],
        "passed": block["passed_runs"],
        "pass_rate": block["value"] or 0.0,
        "interval": (interval[0], interval[1]),
        "answered": block["answered"],
    }


@dataclass
class EvalSuiteResults:
    """Results from running an evaluation suite.

    ``results`` are the runs; ``metrics``, ``stats`` and ``manifest`` are the
    engine's aggregates, identical to what ``omniagents eval suite run``
    records for the same scenarios. Rates here (``pass_rate``,
    ``by_case()``, ``by_field()``) count a run as passed when every measure
    on it passed - the rule results storage uses.
    """

    suite: "EvalSuite"
    results: List[EvalResult]
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def runs(self) -> List[Dict[str, Any]]:
        """The engine's run records."""
        return [_record_of(r) for r in self.results]

    @property
    def metrics(self) -> List[Dict[str, Any]]:
        """Configured metrics (``evaluation.metrics``), with intervals and slices."""
        return list(self.data.get("metrics") or [])

    @property
    def stats(self) -> Dict[str, Any]:
        return dict(self.data.get("stats") or {})

    @property
    def manifest(self) -> Optional[Dict[str, Any]]:
        """What was run: agent, model, tools, hashes, selection, repeats."""
        return self.data.get("manifest")

    @property
    def df(self) -> "pd.DataFrame":
        """Results as a pandas DataFrame."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for DataFrame output. Install with: pip install pandas"
            )

        rows = []
        for r in self.results:
            row = {
                "name": r.name or "",
                "trial": r.trial,
                "input": r.input[:50] + "..." if len(r.input) > 50 else r.input,
                "passed": r.passed,
                "tools_used": ", ".join(r.tools_used),
                "output": r.output[:80] + "..." if len(r.output) > 80 else r.output,
            }
            for m in r.measures:
                row[m.name] = m.passed
            rows.append(row)

        return pd.DataFrame(rows)

    def _summary_block(self) -> Dict[str, Any]:
        return _rate(summarize_runs(self.runs))

    @property
    def pass_rate(self) -> float:
        """Fraction of runs that passed."""
        return self._summary_block()["pass_rate"]

    @property
    def pass_rate_interval(self) -> Tuple[float, float]:
        """95% Wilson interval around :attr:`pass_rate`."""
        return self._summary_block()["interval"]

    @property
    def answered_rate(self) -> float:
        """Fraction of runs that produced a final answer at all."""
        block = self._summary_block()
        return block["answered"] / block["runs"] if block["runs"] else 0.0

    @property
    def summary(self) -> Dict[str, Any]:
        """Summary statistics."""
        block = self._summary_block()
        return {
            "suite_name": self.suite.name,
            "total": block["runs"],
            "passed": block["passed"],
            "failed": block["runs"] - block["passed"],
            "pass_rate": block["pass_rate"],
            "interval": block["interval"],
            "answered": block["answered"],
        }

    def by_case(self) -> Dict[str, Dict[str, Any]]:
        """Per case: runs, passes, pass rate and interval across repeats.

        With ``repeats=1`` every case has one run and the rate is 0 or 1;
        with more, this is where a flaky case (some passes) separates from a
        broken one (none).
        """
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in self.results:
            groups.setdefault(r.name or "", []).append(_record_of(r))
        return {name: _rate(summarize_runs(rs)) for name, rs in groups.items()}

    def by_field(self, path: str) -> Dict[str, "EvalSuiteResults"]:
        """Split results by a case field: a metadata key, or a scenario path.

        ``path`` is looked up in the case's ``metadata`` first
        (``"skin_tone"``, ``"patient.age_band"``), then in the scenario itself
        (``"expect.skin_tone"``, ``"tags"`` - one group per tag) - the same
        lookup as a metric's ``slice_by``. Each group is an
        :class:`EvalSuiteResults`, so
        ``results.by_field("skin_tone")["56"].pass_rate_interval`` is the
        subgroup's rate with its interval.
        """
        records = [_record_of(r) for r in self.results]
        by_id = {id(rec): r for rec, r in zip(records, self.results)}
        return {
            key: EvalSuiteResults(suite=self.suite, results=[by_id[id(x)] for x in rs])
            for key, rs in group_runs(records, path).items()
        }

    def failures(self) -> List[EvalResult]:
        """Return only failed results."""
        return [r for r in self.results if not r.passed]

    def by_tag(self, tag: str) -> "EvalSuiteResults":
        """Filter results by tag."""
        return self.by_field("tags").get(
            tag, EvalSuiteResults(suite=self.suite, results=[])
        )

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
            raise ImportError(
                "pandas required for breakdown(). Install with: pip install pandas"
            )

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
        stats["Accuracy"] = (stats["Passed"] / stats["Total"]).apply(
            lambda x: f"{x:.1%}"
        )
        stats["Passed"] = stats["Passed"].astype(int)

        return stats

    def _repr_html_(self) -> str:
        """Rich HTML display."""
        s = self.summary
        status_color = (
            "#22c55e"
            if s["pass_rate"] >= 0.8
            else "#f59e0b" if s["pass_rate"] >= 0.5 else "#ef4444"
        )

        # Build results table
        table_rows = ""
        for r in self.results:
            icon = "✓" if r.passed else "✗"
            color = "#22c55e" if r.passed else "#ef4444"
            name_cell = (
                f"<td style='padding: 6px 12px;'>{html.escape(r.name or '')}</td>"
                if any(x.name for x in self.results)
                else ""
            )
            input_display = r.input[:40] + "..." if len(r.input) > 40 else r.input
            tools = ", ".join(r.tools_used) if r.tools_used else "-"
            table_rows += f"""
            <tr style="border-bottom: 1px solid #f3f4f6;">
                {name_cell}
                <td style="padding: 6px 12px;">{html.escape(input_display)}</td>
                <td style="padding: 6px 12px; color: {color}; text-align: center;">{icon}</td>
                <td style="padding: 6px 12px;">{html.escape(tools)}</td>
            </tr>
            """

        name_header = (
            "<th style='padding: 6px 12px; text-align: left;'>Name</th>"
            if any(x.name for x in self.results)
            else ""
        )

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

        return f"""
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
        """

    def __repr__(self) -> str:
        s = self.summary
        return (
            f"EvalSuiteResults({self.suite.name!r}, {s['passed']}/{s['total']} passed)"
        )

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self):
        return iter(self.results)

    def __getitem__(self, index):
        return self.results[index]


def _target_for(
    agent: Any, *, max_turns: Optional[int], context: Any
) -> Optional[AgentTarget]:
    if agent is None:
        return None
    if isinstance(agent, AgentTarget):
        return agent
    if isinstance(agent, (str, Path)):
        return ProjectAgentTarget(agent)
    return SdkAgentTarget(
        agent, max_turns=max_turns if max_turns is not None else 10, context=context
    )


class EvalSuite:
    """A named collection of test cases."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.cases: List[TestCase] = []
        # Set by from_project: the loaded project config, and its evaluation
        # dict without scenarios (judges, metrics, defaults, environment).
        self.project: Any = None
        self.config: Dict[str, Any] = {}

    @classmethod
    def from_project(
        cls,
        project: Union[str, Path],
        *,
        select: Optional[str] = None,
        exclude: Optional[str] = None,
        split: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> "EvalSuite":
        """Load a project's ``evaluations/`` folder, exactly as the CLI does.

        ``project`` is a ``project.yml`` or its directory; ``select``,
        ``exclude``, ``split`` and ``agent`` are ``eval suite run``'s
        ``--select``, ``--exclude``, ``--split`` and ``--agent``. The cases
        are the scenarios verbatim; judges, metrics, defaults, the declared
        environment and the project's ``measures.py`` all apply when the
        suite runs. ``await suite.run()`` runs the project agent - the same
        run as ``omniagents eval suite run`` - and ``suite.run(agent)`` an
        agent built in code against the same scenarios.
        """
        from omniagents.core.eval.loader import load_eval_config

        cfg = load_eval_config(
            str(project), select=select, exclude=exclude, agent_key=agent, split=split
        )
        suite = cls(
            name=str(cfg.agent_config.get("name") or cfg.project_root.name),
            description=f"Evaluations of {cfg.agent_path}",
        )
        suite.project = cfg
        suite.config = {k: v for k, v in cfg.config.items() if k != "scenarios"}
        suite.cases = [
            TestCase.from_scenario(sc) for sc in cfg.config.get("scenarios") or []
        ]
        return suite

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
        metadata_fn: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> "EvalSuite":
        """Create an EvalSuite from a list of records (dicts).

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
            metadata_fn: Optional function returning the fields to slice by

        Example:
            suite = EvalSuite.from_records(
                math_data[:100],
                name="MATH Evaluation",
                input_fn=lambda p: f"Question:\\n{p['problem']}\\n\\nAnswer:",
                expect_fn=lambda p: {'expected_answer': p['answer']},
                tags_fn=lambda p: [p['type'], p['level']],
                measures=["correct_math_answer"],
            )
        """
        suite = cls(name=name, description=description)
        for i, record in enumerate(records):
            suite.add(
                name=name_fn(i, record) if name_fn else f"case_{i}",
                input=input_fn(record),
                expect=expect_fn(record) if expect_fn else None,
                measures=measures,
                judges=judges,
                tags=tags_fn(record) if tags_fn else None,
                metadata=metadata_fn(record) if metadata_fn else None,
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
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "EvalSuite":
        """Add a test case to the suite.

        Args:
            name: Unique name for this test case
            input: Single-turn prompt (use this OR prompts OR goal)
            prompts: Multi-turn scripted prompts (list of user messages)
            goal: Synthetic user goal (for LLM-driven multi-turn)
            persona: Synthetic user persona info (dict)
            max_exchanges: Max user-agent exchanges for a synthetic case (default: 10)
            expect: Expectations dict (see check_expectations); other keys are
                data for measures (``ctx.expect``)
            measures: Measures to run for this case (in addition to run-level ones)
            judges: LLM judges to run for this case (in addition to run-level ones)
            tags: Optional tags for filtering
            description: Optional description
            metadata: Fields to slice results by (``results.by_field("skin_tone")``)

        The case's measures gain ``expectations`` when ``expect`` uses the
        checked keys, and ``user_goal_achieved`` for a synthetic case (the
        simulated user must say its goal was reached).

        Returns:
            self, for method chaining

        Examples:
            suite.add("basic", "What's the weather?")
            suite.add("booking_flow", prompts=["Book a room", "Tomorrow 2pm", "Confirm"])
            suite.add(
                "synthetic_booking",
                goal="Book a meeting room for tomorrow at 2pm",
                persona={"name": "Alice", "department": "Engineering"},
            )
        """
        if sum(x is not None for x in (input, prompts, goal)) != 1:
            raise ValueError("Provide exactly one of: input, prompts, or goal")
        case_measures = list(measures or [])
        if has_expectations(expect) and "expectations" not in case_measures:
            case_measures.append("expectations")
        if goal is not None and "user_goal_achieved" not in case_measures:
            case_measures.append("user_goal_achieved")
        self.cases.append(
            TestCase(
                name=name,
                input=input,
                prompts=prompts,
                goal=goal,
                persona=persona,
                max_exchanges=max_exchanges,
                expect=expect,
                measures=case_measures or None,
                judges=judges,
                tags=tags,
                description=description,
                metadata=metadata,
            )
        )
        return self

    def to_config(
        self,
        *,
        measures: Optional[List[str]] = None,
        judges: Optional[List[Union[str, "Judge"]]] = None,
        include_builtin_measures: Optional[bool] = None,
        max_turns: Optional[int] = None,
        repeats: Optional[int] = None,
    ) -> Dict[str, Any]:
        """The evaluation config :meth:`run` hands the engine.

        The same shape as a project's merged ``evaluation`` block, so it can
        be written to ``evaluations/evaluation.yml`` and run with the CLI.
        ``include_builtin_measures`` (default: on for a hand-built suite,
        off for :meth:`from_project`) adds ``tool_hallucination``;
        ``max_turns`` and ``repeats`` set ``defaults.max_turns`` and
        ``defaults.runs_per_prompt`` (a scenario's own ``runs_per_prompt``
        still wins).
        """
        if include_builtin_measures is None:
            include_builtin_measures = self.project is None
        added = (["tool_hallucination"] if include_builtin_measures else []) + list(
            measures or []
        )
        cfg = copy.deepcopy(self.config)
        judge_cfgs = dict(cfg.get("judges") or {})
        cfg["scenarios"] = [
            case.to_scenario(
                measures=added, judges=judges, config_judges=list(judge_cfgs)
            )
            for case in self.cases
        ]
        config_keys = list(judge_cfgs)
        for case in self.cases:
            for j in list(judges or []) + list(case.judges or []):
                judge = _notebook_judge(j, config_keys)
                if judge is not None:
                    judge_cfgs[judge.name] = judge.to_config()
        if judge_cfgs:
            cfg["judges"] = judge_cfgs
        defaults = dict(cfg.get("defaults") or {})
        if max_turns is not None:
            defaults["max_turns"] = int(max_turns)
        if repeats is not None:
            defaults["runs_per_prompt"] = max(1, int(repeats))
        if defaults:
            cfg["defaults"] = defaults
        return cfg

    async def run(
        self,
        agent: Any = None,
        *,
        measures: Optional[List[str]] = None,
        judges: Optional[List[Union[str, "Judge"]]] = None,
        environment: Optional["EvalEnvironment"] = None,
        include_builtin_measures: Optional[bool] = None,
        tags: Optional[List[str]] = None,
        max_turns: Optional[int] = None,
        show_progress: bool = True,
        repeats: Optional[int] = None,
        concurrency: int = 5,
        include_unmeasured: Optional[bool] = None,
        context: Any = None,
    ) -> EvalSuiteResults:
        """Run all test cases through the evaluation engine.

        Args:
            agent: An ``agents.Agent`` (run with the SDK ``Runner``), a path
                to an ``agent.yml``, or an
                :class:`~omniagents.core.eval.targets.AgentTarget`. Optional
                for a :meth:`from_project` suite, which then runs the
                project agent.
            measures: Additional measures to run on every case
            judges: LLM judges to run on every case
            environment: EvalEnvironment for fixture management (overrides
                a project's declared environment)
            include_builtin_measures: Add ``tool_hallucination`` (default:
                on for a hand-built suite, off for a project suite)
            tags: Only run cases with any of these tags (``--select tag:...``)
            max_turns: Maximum agent turns per answer (``defaults.max_turns``;
                an ``agents.Agent`` defaults to 10)
            show_progress: Show progress indicator
            repeats: Run every case this many times (``defaults.runs_per_prompt``;
                default: the config's, else 1). Each result's ``trial`` says
                which repeat it was; ``by_case()`` gives the rate per case.
            concurrency: Runs in flight at once (the CLI's default is 5)
            include_unmeasured: Run cases with no measures (default: yes for a
                hand-built suite; no for a project suite, as ``eval suite run``
                without ``--include-unmeasured``)
            context: SDK run context for an ``agents.Agent``

        Returns:
            EvalSuiteResults with all test results
        """
        from omniagents.core.eval.engine import run_suite
        from omniagents.core.eval.loader import filter_scenarios
        from omniagents.core.eval.pipeline import run_evaluation_async

        cfg = self.to_config(
            measures=measures,
            judges=judges,
            include_builtin_measures=include_builtin_measures,
            max_turns=max_turns,
            repeats=repeats,
        )
        tag_select = ",".join(f"tag:{t}" for t in tags) if tags else None
        if tag_select:
            cfg["scenarios"] = filter_scenarios(cfg["scenarios"], tag_select, None)
        if include_unmeasured is None:
            include_unmeasured = self.project is None
        target = _target_for(agent, max_turns=max_turns, context=context)
        if target is None and self.project is None:
            raise ValueError("EvalSuite.run needs an agent (or EvalSuite.from_project)")

        state = {"done": 0, "total": 0}

        def _progress(ev: Dict[str, Any]) -> None:
            if not show_progress:
                return
            if ev.get("event") == "start":
                state["total"] = int(ev.get("total") or 0)
            elif ev.get("event") == "progress":
                state["done"] += 1
                print(
                    f"\rRunning {state['done']}/{state['total']}: {ev.get('scenario')}...",
                    end="",
                    flush=True,
                )

        if self.project is not None:
            selects = [s for s in (self.project.select, tag_select) if s]
            project = dataclasses.replace(
                self.project, select=",".join(selects) if selects else None
            )
            data = await run_evaluation_async(
                eval_config=project,
                target=target,
                eval_cfg=cfg,
                environment=environment,
                include_unmeasured=include_unmeasured,
                progress_cb=_progress,
                concurrency=concurrency,
                include_history=True,
            )
        else:
            assert target is not None
            data = await run_suite(
                target,
                cfg,
                environment=environment,
                include_unmeasured=include_unmeasured,
                progress_cb=_progress,
                concurrency=concurrency,
                include_history=True,
                selection={"select": tag_select},
            )

        tools = (
            list(getattr(target.agent, "tools", None) or [])
            if isinstance(target, SdkAgentTarget)
            else None
        )
        judge_keys = list((cfg.get("judges") or {}).keys())
        results = [_result_from_record(rec, judge_keys, tools) for rec in data["runs"]]
        if show_progress:
            passed = sum(r.passed for r in results)
            print(f"\rCompleted: {passed}/{len(results)} passed" + " " * 20)
        return EvalSuiteResults(suite=self, results=results, data=data)

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
    """
    return evaluation_measure(func, name=name, overwrite=overwrite)


# -----------------------------------------------------------------------------
# LLM-as-Judge
# -----------------------------------------------------------------------------

# Global registry of judges
_JUDGES: Dict[str, "Judge"] = {}

_JUDGE_OUTPUT_SCHEMA = {
    "properties": {
        "reasoning": {"type": "string"},
        "answer": {"type": "string", "enum": ["Pass", "Fail"]},
        "score": {"type": "number"},
    },
    "required": ["reasoning", "answer", "score"],
}

_JUDGE_RETURN_FORMAT = """Return a JSON object with:
- "answer": "Pass" if the criteria is met, "Fail" otherwise
- "reasoning": A brief explanation of your judgment (1-2 sentences)
- "score": A score from 0.0 to 1.0 indicating how well the criteria was met"""


class Judge:
    """An LLM-based judge for evaluating agent outputs.

    A judge is an ``evaluation.judges`` entry built in code: :meth:`to_config`
    is the config the engine runs, and creating one registers the measure
    ``<name>_agent`` that passes when the judge answers ``Pass`` (the same
    convention a project's judges follow).

    Example:
        quality = Judge(
            "quality",
            criteria="The response is helpful, accurate, and well-formatted",
        )
        result = await evaluate(agent, "Hello", judges=[quality])

        expert = Judge(
            "domain_expert",
            instructions='''You are an expert meteorologist.
            Evaluate if the weather information is accurate and complete.''',
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
            instructions: Full custom instructions for the judge (overrides criteria);
                they should ask for the JSON ``{"answer": "Pass"|"Fail", "reasoning", "score"}``
            model: Model to use for judging
            include_tools: Include tool calls in judge input
            include_history: Include the conversation history (default: just final output)
        """
        self.name = name
        self.criteria = criteria
        self.instructions = instructions
        self.model = model
        self.include_tools = include_tools
        self.include_history = include_history

        # Register globally, with the measure that scores it.
        _JUDGES[name] = self
        key = name

        def _judge_measure(ctx: EvalContext) -> Dict[str, Any]:
            judge = ctx.judge_result(key)
            if judge is None:
                output = ctx.metadata.get(key)
                return fail_reason(f"{key} judge did not run", output=output)
            return judge.to_result()

        evaluation_measure(_judge_measure, name=self.measure_name, overwrite=True)

    @property
    def measure_name(self) -> str:
        """The measure that passes when this judge answers ``Pass``."""
        return f"{self.name}_agent"

    def _build_instructions(self) -> str:
        """Build the judge agent instructions."""
        if self.instructions:
            return self.instructions

        if self.criteria:
            return f"""You are an evaluation judge. Your task is to determine if an AI agent's response meets the following criteria:

**Criteria:** {self.criteria}

{_JUDGE_RETURN_FORMAT}

Be strict but fair. Focus on whether the core criteria is satisfied."""

        return f"""You are an evaluation judge. Evaluate the AI agent's response for quality and correctness.

{_JUDGE_RETURN_FORMAT}"""

    def _template(self) -> str:
        """The judge's input template (rendered by the engine's judge runner)."""
        parts = ["**User Input:** {{ user_input }}", "**Agent Response:** {{ text }}"]
        if self.include_tools:
            parts.append(
                "{% if tools_used %}**Tools Used:** {{ tools_used | join(', ') }}{% endif %}"
            )
        if self.include_history:
            parts.append(
                "{% if history %}**Conversation History:**\n"
                "{% for item in history[:10] %}- {{ item.get('role', 'unknown') }}: "
                "{{ (item.get('content', '') | string)[:200] }}\n{% endfor %}{% endif %}"
            )
        return "\n\n".join(parts)

    def to_config(self) -> Dict[str, Any]:
        """This judge as an ``evaluation.judges`` entry."""
        return {
            "agent": {
                "name": f"Judge: {self.name}",
                "model": self.model,
                "instructions": self._build_instructions(),
                "output_type": "dict",
                "output_type_strict": True,
                "output_schema": copy.deepcopy(_JUDGE_OUTPUT_SCHEMA),
            },
            "input": {"source": "final_assistant_text", "template": self._template()},
        }

    async def evaluate(
        self,
        user_input: str,
        agent_output: str,
        tools_used: List[str],
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> JudgeResult:
        """Run the judge on one exchange outside a suite.

        Args:
            user_input: The original user input
            agent_output: The agent's final output
            tools_used: List of tool names that were called
            history: Optional conversation history (built from the other
                arguments when omitted)

        Returns:
            JudgeResult with pass/fail and reasoning
        """
        from omniagents.core.eval.pipeline import run_judge

        if history is None:
            history = [{"role": "user", "content": user_input}]
            history += [
                {
                    "type": "function_call",
                    "name": t,
                    "call_id": f"call_{i}",
                    "arguments": "{}",
                }
                for i, t in enumerate(tools_used)
            ]
            history.append({"role": "assistant", "content": agent_output})
        output = await run_judge(
            self.name,
            self.to_config(),
            scenario={"name": self.name},
            history=history,
            base_dir=Path.cwd(),
        )
        return _judge_result(self.name, output)

    def __repr__(self) -> str:
        criteria_preview = (
            self.criteria[:50] + "..."
            if self.criteria and len(self.criteria) > 50
            else self.criteria
        )
        return f"Judge({self.name!r}, criteria={criteria_preview!r})"


def _notebook_judge(
    judge: Union[str, "Judge"], config_judges: List[str]
) -> Optional["Judge"]:
    """The notebook :class:`Judge` a reference means (``None``: a config judge)."""
    if not isinstance(judge, str):
        return judge
    if judge in config_judges:
        return None
    return _JUDGES.get(judge)


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
    """Run several judges on one exchange (see :meth:`Judge.evaluate`)."""
    results: List[JudgeResult] = []
    for judge in judges:
        if isinstance(judge, str):
            judge = get_judge(judge)
        results.append(
            await judge.evaluate(user_input, agent_output, tools_used, history)
        )
    return results


# -----------------------------------------------------------------------------
# Multi-Turn Evaluation
# -----------------------------------------------------------------------------


class SyntheticUser(SimulatedUser):
    """A simulated user that role-plays to achieve a goal.

    The engine's :class:`~omniagents.core.eval.simulated_user.SimulatedUser`
    with a goal-first constructor. You typically don't create one directly;
    ``evaluate(agent, goal=..., persona=...)`` and ``suite.add(goal=...)``
    run a synthetic scenario, which builds its simulated user itself.
    """

    def __init__(
        self,
        goal: str,
        *,
        persona: Optional[Dict[str, Any]] = None,
        initial_message: Optional[str] = None,
        instructions: Optional[str] = None,
        model: str = "gpt-4.1",
        agent_description: str = "an AI assistant",
    ):
        super().__init__(
            UserPersona(
                goal=goal,
                info=dict(persona or {}),
                initial_message=initial_message,
                instructions=instructions,
            ),
            agent_description=agent_description,
            model=model,
        )

    @property
    def goal(self) -> str:
        return self.persona.goal

    def __repr__(self) -> str:
        goal_preview = self.goal[:40] + "..." if len(self.goal) > 40 else self.goal
        return f"SyntheticUser(goal={goal_preview!r})"


# -----------------------------------------------------------------------------
# Main evaluate() Function
# -----------------------------------------------------------------------------


async def evaluate(
    agent: Any,
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

    A one-case :class:`EvalSuite` run once. There are three modes:

    **Single-turn**: Provide `input` - one prompt, one response.

    **Multi-turn (scripted)**: Provide `prompts` - a sequence of user messages
    sent in order, with conversation history maintained between them.

    **Multi-turn (synthetic)**: Provide `goal` (and optionally `persona`) -
    an LLM role-plays as a user trying to achieve the goal.

    Args:
        agent: The agent to evaluate (see :meth:`EvalSuite.run`)
        input: Single-turn prompt
        prompts: Multi-turn scripted conversation (list of user messages)
        goal: Multi-turn synthetic user goal
        persona: Synthetic user persona info (used with goal)
        expect: Expectations dict (tool_called, response_contains, verify, etc.)
        measures: Custom measures to run (by name)
        judges: LLM judges to run (Judge instances or names)
        environment: EvalEnvironment for fixture management
        include_builtin_measures: Include the tool_hallucination measure
        name: Name for this test case
        max_turns: Maximum agent turns (tool calls) per response
        max_exchanges: Maximum user-agent exchanges for a synthetic conversation

    Returns:
        EvalResult with pass/fail status, measures, expectations, etc.

    Examples:
        result = await evaluate(agent, "What's the weather?")
        result = await evaluate(agent, "Weather in Paris?",
                                expect={"tool_called": "get_weather"})
        result = await evaluate(agent,
            prompts=["Book a room", "Tomorrow 2pm", "Confirm"],
            expect={"tool_called": "create_booking"})
        result = await evaluate(agent,
            goal="Book a meeting room for tomorrow at 2pm",
            persona={"name": "Alice", "email": "alice@example.com"})
    """
    suite = EvalSuite(name or "evaluate")
    suite.add(
        name or "case",
        input,
        prompts=prompts,
        goal=goal,
        persona=persona,
        max_exchanges=max_exchanges if goal is not None else None,
        expect=expect,
        measures=measures,
        judges=judges,
    )
    results = await suite.run(
        agent,
        environment=environment,
        include_builtin_measures=include_builtin_measures,
        max_turns=max_turns,
        show_progress=False,
        concurrency=1,
    )
    result = results.results[0]
    if name is None:
        result.name = None
    return result


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
