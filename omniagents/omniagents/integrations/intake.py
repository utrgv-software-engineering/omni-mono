"""Event intake: what a long-running factory server does with each event.

Every source (a Slack webhook, a Socket Mode connection, later GitHub,
Linear, Teams and Azure DevOps) hands its events to :meth:`EventIntake.accept`:

1. **Record** it durably. A provider redelivering the same event (Slack and
   GitHub retry when they get no timely answer) is recognised by its
   delivery id and dropped.
2. **Acknowledge** at once: ``accept`` returns before any agent work, so
   the source can answer the provider within its deadline.
3. **Match** it against the project's automations (``automations/*``).
4. **Route** it: an event in a thread the factory is already working in
   (the same Slack thread, the same issue) continues that conversation;
   otherwise a new conversation starts.
5. **Run** the automation's agent with its instruction and the event, as an
   unattended run on the server's AgentHost. A conversation's runs go one
   at a time; at most ``max_concurrent_runs`` run at once.
6. **Report**: a source that shows a run as it goes (a Linear agent
   session) is given each step the run takes (``on_step``), and the run's
   outcome when it ends (``after_run``).
7. **Keep**: the run's last message, and the pull requests it opened or
   worked on (``links.py``); what its model calls used is recorded as they
   happen (``omniagents.core.usage``).
8. **Grade**: the project's judges on live runs grade a sample of the
   completed ones (``grading.py``).

An event whose ``control`` is ``"stop"`` (someone stopped a Linear agent
session) starts nothing: it cancels its conversation's run in progress,
which reports as ``stopped``.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import logging
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Dict, Optional

from omniagents.core.automations import Automation, list_automations

from .events import IncomingEvent
from .matching import matching_automations
from .store import EventStore

if TYPE_CHECKING:
    from .grading import LiveJudges
    from .links import PullRequestLinks

__all__ = [
    "AfterRun",
    "EventIntake",
    "OnStep",
    "Progress",
    "RunResult",
    "RunStarter",
    "RunStep",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    """How a run ended: ``error`` when it did not complete, and its last message."""

    error: Optional[str] = None
    output: Optional[str] = None
    #: Someone stopped it before it finished.
    stopped: bool = False
    # A shared conversation needs a turn-scoped record for reporting.
    history: Optional[list] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


@dataclass(frozen=True)
class RunStep:
    """A step a run takes, as it takes it: ``kind`` is ``"tool"`` (``name``,
    the tool it calls) or ``"message"`` (``text``, a message it writes)."""

    kind: str
    name: Optional[str] = None
    text: Optional[str] = None


#: Told each step of one run.
Progress = Callable[[RunStep], Awaitable[None]]
#: ``(agent_key, session_id, prompt, progress) -> RunResult``: runs one turn.
RunStarter = Callable[[str, str, str, Optional[Progress]], Awaitable[RunResult]]
#: ``(event, step)``: told each step of the run an event started.
OnStep = Callable[[IncomingEvent, RunStep], Awaitable[None]]
#: ``(event, result)``: told how the run an event started ended.
AfterRun = Callable[[IncomingEvent, RunResult], Awaitable[None]]


class EventIntake:
    def __init__(
        self,
        *,
        project_root: Path,
        entrypoint: str,
        store: EventStore,
        run: RunStarter,
        max_concurrent_runs: int = 4,
        shared_session: Optional[str] = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.entrypoint = entrypoint
        self.shared_session = shared_session
        self.store = store
        self._run = run
        self._slots = asyncio.Semaphore(max_concurrent_runs)
        self._conversation_locks: Dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        #: The runs in progress, by conversation (provider, thread key).
        self._running: Dict[tuple, set] = {}
        #: Per provider: told each step of the runs its events start.
        self.on_step: Dict[str, OnStep] = {}
        #: Per provider: told how each run its events started ended.
        self.after_run: Dict[str, AfterRun] = {}
        #: Grades a sample of completed runs (``grading.LiveJudges``).
        self.judges: Optional["LiveJudges"] = None
        self.extract_memory = None
        #: The factory's version runs are recorded with (its commit, ``FACTORY_VERSION``).
        self.version: Optional[str] = None
        #: Records the pull requests each run opened or worked on (``links.PullRequestLinks``).
        self.links: Optional["PullRequestLinks"] = None

    async def accept(self, event: IncomingEvent) -> bool:
        """Record ``event`` and start processing it; ``False`` for a redelivery."""
        event_id = self.store.record(event)
        if event_id is None:
            logger.info(
                "integrations: %s %s %s already received",
                event.provider,
                event.event,
                event.delivery_id,
            )
            return False
        if event.control == "stop":
            stopped = self.stop(event.provider, event.thread_key)
            self.store.set_status(
                event_id,
                "stopped" if stopped else "ignored",
                f"stopped {stopped} run(s)" if stopped else "nothing was running",
            )
            return True
        task = asyncio.get_running_loop().create_task(self._process(event_id, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    def stop(self, provider: str, thread_key: Optional[str]) -> int:
        """Cancel the conversation's runs in progress; how many there were."""
        running = list(self._running.get((provider, thread_key), ()))
        for task in running:
            task.cancel()
        return len(running)

    async def drain(self) -> None:
        """Wait for every event accepted so far to finish (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _process(self, event_id: int, event: IncomingEvent) -> None:
        try:
            matched = matching_automations(list_automations(self.project_root), event)
        except Exception as exc:  # a malformed automation file
            logger.exception("integrations: reading automations failed")
            self.store.set_status(event_id, "failed", str(exc))
            return
        if not matched:
            self.store.set_status(event_id, "ignored", "no automation matches")
            return
        from .self_improvement import allowed

        matched = [automation for automation in matched if await allowed(automation)]
        if not matched:
            self.store.set_status(
                event_id,
                "ignored",
                "self-improvement is disabled or its factory repository is unavailable",
            )
            return
        self.store.set_status(
            event_id, "dispatched", ", ".join(a.name for a in matched)
        )
        await asyncio.gather(
            *(self._dispatch(event_id, event, automation) for automation in matched)
        )

    async def _dispatch(
        self, event_id: int, event: IncomingEvent, automation: Automation
    ) -> None:
        agent = (
            self.entrypoint
            if self.shared_session
            else (automation.agent or self.entrypoint)
        )
        session_id = self.shared_session
        if event.thread_key and not self.shared_session:
            session_id = self.store.thread_session(
                event.provider, event.thread_key, agent
            )
        if session_id is None:
            session_id = str(uuid.uuid4())
            if event.thread_key:
                self.store.remember_thread(
                    event.provider, event.thread_key, agent, session_id
                )
        prompt = automation.run_input(event.payload, event.context)
        if self.shared_session:
            prompt = (
                f"Automation: {automation.name}\nSource: {event.provider}\n"
                f"Thread: {event.thread_key or '(scheduled event)'}\n\n" + prompt
            )
        dispatch_id = self.store.start_dispatch(
            event_id,
            automation.name,
            agent,
            session_id,
            prompt=prompt,
            version=self.version,
        )
        lock = (
            nullcontext()
            if self.shared_session
            else self._conversation_locks.setdefault(session_id, asyncio.Lock())
        )
        slots = nullcontext() if self.shared_session else self._slots
        on_step = self.on_step.get(event.provider)
        progress: Optional[Progress] = None
        if on_step is not None:

            async def progress(step: RunStep) -> None:
                try:
                    await on_step(event, step)
                except Exception:
                    logger.exception(
                        "integrations: reporting a step of %s failed", agent
                    )

        conversation = (event.provider, event.thread_key)
        run: Optional[asyncio.Future] = None
        try:
            async with lock, slots:
                from .self_improvement import guarded_run

                run = asyncio.ensure_future(
                    guarded_run(
                        self._run, automation, agent, session_id, prompt, progress
                    )
                )
                self._running.setdefault(conversation, set()).add(run)
                try:
                    result = await asyncio.shield(run)
                finally:
                    self._running.get(conversation, set()).discard(run)
        except asyncio.CancelledError:
            if run is None or not run.cancelled():
                if run is not None:
                    run.cancel()
                raise  # the intake itself is shutting down
            result = RunResult(error="stopped", stopped=True)
        except Exception as exc:
            logger.exception("integrations: run of %s failed", agent)
            result = RunResult(error=f"{type(exc).__name__}: {exc}")
        if self.shared_session and result.history is None:
            result = replace(result, history=[])
        if result.history is not None:
            self.store.record_turn(
                dispatch_id, result.history, result.started_at, result.finished_at
            )
        self.store.finish_dispatch(
            dispatch_id,
            "stopped" if result.stopped else "failed" if result.error else "completed",
            result.error,
        )
        if result.output:
            self.store.record_output(dispatch_id, result.output)
        if self.links is not None:
            try:
                await asyncio.to_thread(
                    self.links.record, dispatch_id, agent, session_id, event
                )
            except Exception:
                logger.exception(
                    "integrations: recording the pull requests of %s's run failed",
                    agent,
                )
        report = self.after_run.get(event.provider)
        if report is not None:
            try:
                await report(event, result)
            except Exception:
                logger.exception(
                    "integrations: reporting the run of %s back failed", agent
                )
        if self.judges is not None and not result.error:
            try:
                await self.judges.grade(
                    dispatch_id, automation.name, agent, session_id, event
                )
            except Exception:
                logger.exception("integrations: grading the run of %s failed", agent)
        if self.extract_memory is not None:
            try:
                self.extract_memory(
                    dispatch_id, automation.name, agent, session_id, event, result
                )
            except Exception:
                logger.exception("integrations: scheduling memory extraction failed")
