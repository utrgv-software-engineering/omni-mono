"""Judges on live runs: the project's judges grade a sample of real runs.

A judge in ``evaluations/judges/<key>/judge.yml`` that has ``live`` grades
that share of its agents' completed automation runs (every replayed one,
``replay.py``), as it grades evaluation runs::

    agents: [implementer]     # whose runs it grades
    live: { sample: 25 }      # the percentage of them

A run is its agent's session, and also the session of every agent it called
as a tool (``agent_tools``). Each of those is a child thread of the run's own,
recorded with its whole transcript (``orchestration/subagents.py``). A
foreman's run that called the implementer is graded by the implementer's
judges, reading the implementer's own session: what it was given, every tool
call and its output, and its report. The judge's ``input`` template sees
that history, ``scenario.name`` (the automation), ``scenario.agent`` (whose
session it is), ``scenario.parent_agent`` (whose run called it, if one did)
and ``event`` (what started the run).
Its verdict goes in the event store's ``grades``: Pass or Fail, its reason
and its reasoning. The nightly review (``review_runs`` on a schedule, see
``schedule.py``) reads them back: in full, the Fails of judges with
``self_improve: true``, which are what the factory improves itself from.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .events import IncomingEvent
from .store import EventStore

__all__ = ["LiveJudges", "live_judges", "verdict"]

logger = logging.getLogger(__name__)

#: ``(agent_key, session_id) -> history``: a run's conversation.
History = Callable[[str, str], List[Dict[str, Any]]]
#: ``(agent_key, session_id) -> [(agent_key, thread_id, history)]``: the sessions of the
#: agents a run called as tools (its child threads), each with its transcript.
Children = Callable[[str, str], List[Tuple[str, str, List[Dict[str, Any]]]]]


def thread_history(items: List[Any]) -> List[Dict[str, Any]]:
    """A recorded thread's items (``conversation.model.Item``) as the history judges read:
    messages, and each tool call with its output."""
    out: List[Dict[str, Any]] = []
    for item in items:
        kind = getattr(item.kind, "value", item.kind)
        content = item.content or {}
        if kind == "user_message":
            out.append({"role": "user", "content": content.get("text") or ""})
        elif kind == "agent_message":
            out.append({"role": "assistant", "content": content.get("text") or ""})
        elif kind == "tool_call":
            call_id = item.item_id
            out.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": content.get("tool"),
                    "arguments": content.get("input") or "",
                }
            )
            if content.get("output") is not None:
                out.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": content.get("output"),
                    }
                )
    return out


def live_judges(
    project_root: Path, project_config: Dict[str, Any]
) -> Dict[str, Dict[str, Any]]:
    """The project's judges that grade live runs, by key."""
    from omniagents.core.eval.layout import EvalLayout, evaluations_dir
    from omniagents.core.eval.loader import _collect_judges

    eval_dir = evaluations_dir(project_root, project_config)
    if not eval_dir.is_dir():
        return {}
    judges, _ = _collect_judges(EvalLayout(eval_dir, project_root))
    return {key: cfg for key, cfg in judges.items() if _sample(cfg) > 0}


def _sample(cfg: Dict[str, Any]) -> float:
    live = cfg.get("live")
    if not isinstance(live, dict):
        return 0.0
    try:
        return max(0.0, min(100.0, float(live.get("sample") or 0)))
    except (TypeError, ValueError):
        return 0.0


def verdict(output: Any) -> Tuple[Optional[bool], Optional[str], Optional[str]]:
    """``(passed, reason, reasoning)`` from a judge's output; ``passed`` is
    ``None`` when it gave no Pass or Fail."""
    if not isinstance(output, dict):
        return None, None, None
    answer = str(output.get("answer") or "").strip().lower()
    passed = True if answer == "pass" else False if answer == "fail" else None
    reason = output.get("reason") if passed is not None else output.get("error")
    reasoning = output.get("reasoning")
    return (
        passed,
        None if reason is None else str(reason),
        None if reasoning is None else str(reasoning),
    )


class LiveJudges:
    def __init__(
        self,
        judges: Dict[str, Dict[str, Any]],
        eval_dir: Path,
        store: EventStore,
        history: History,
        *,
        children: Children = lambda agent, session_id: [],
        draw: Callable[[], float] = random.random,
    ) -> None:
        self.judges = judges
        self.eval_dir = Path(eval_dir)
        self.store = store
        self.history = history
        self.children = children
        self._draw = draw

    def sampled(
        self, agent: str, *, every: bool = False
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """The judges that grade a session of ``agent``: each draws against its
        sample, or all of them (``every``: a replayed run, which staging grades)."""
        chosen = []
        for key, cfg in self.judges.items():
            agents = cfg.get("agents") or []
            if agents and agent not in agents:
                continue
            if every or self._draw() * 100 < _sample(cfg):
                chosen.append((key, cfg))
        return chosen

    async def grade(
        self,
        dispatch_id: int,
        automation: str,
        agent: str,
        session_id: str,
        event: IncomingEvent,
    ) -> int:
        """Grade a completed run's sessions (its own, and those of the agents it called)
        with the judges each is sampled for; how many grades it got."""
        from .replay import is_replay

        every = is_replay(event)
        scoped = self.store.run_history(dispatch_id)
        sessions: List[
            Tuple[str, str, Optional[str], Callable[[], List[Dict[str, Any]]]]
        ] = [
            (
                agent,
                session_id,
                None,
                lambda: (
                    scoped if scoped is not None else self.history(agent, session_id)
                ),
            )
        ]
        try:
            sessions += [
                (child, thread, agent, (lambda h=history: h))
                for child, thread, history in (
                    self.children(agent, session_id) if scoped is None else []
                )
            ]
        except Exception:
            logger.exception(
                "integrations: reading the sessions run %s called failed", dispatch_id
            )
        graded = 0
        for who, thread, parent, load in sessions:
            chosen = self.sampled(who, every=every)
            if not chosen:
                continue
            history = load()
            if not history:
                logger.warning(
                    "integrations: session %s of %s has no history to grade",
                    thread,
                    who,
                )
                continue
            scenario = {
                "name": automation,
                "agent": who,
                "parent_agent": parent,
                "event": event.payload,
            }
            for key, cfg in chosen:
                graded += await self._judge(
                    dispatch_id, session_id, thread, key, cfg, scenario, history
                )
        return graded

    async def _judge(
        self,
        dispatch_id: int,
        run_session: str,
        thread: str,
        key: str,
        cfg: Dict[str, Any],
        scenario: Dict[str, Any],
        history: List[Dict[str, Any]],
    ) -> int:
        from agents import trace

        from omniagents.core.eval.pipeline import run_judge

        try:
            # Its trace is grouped with the run it grades, naming the session graded.
            with trace(
                f"Judge {key}",
                group_id=f"session_{run_session}",
                metadata={
                    "judge": key,
                    "session": thread,
                    "agent": scenario["agent"],
                    "dispatch": str(dispatch_id),
                },
            ):
                output = await run_judge(
                    key, cfg, scenario=scenario, history=history, base_dir=self.eval_dir
                )
        except Exception as exc:  # a judge that cannot run grades nothing
            logger.exception(
                "integrations: judge %s failed on run %s", key, dispatch_id
            )
            output = {"error": f"{type(exc).__name__}: {exc}"}
        passed, reason, reasoning = verdict(output)
        self.store.record_grade(
            dispatch_id,
            key,
            passed,
            reason,
            reasoning,
            session_id=thread,
            agent=scenario["agent"],
        )
        return 1
