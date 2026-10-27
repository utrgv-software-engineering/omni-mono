"""Schedules as an event source: automations that run on a cron.

An automation's trigger names its schedule::

    triggers:
      - provider: schedule
        event: cron_fired
        schedule:
          name: weekly-dependency-audit
          cron: "0 9 * * 1"          # minute hour day-of-month month day-of-week
          timezone: America/Chicago  # optional; the server's local time otherwise

In server mode, :class:`ScheduleSource` fires each schedule at its times:
one ``cron_fired`` event per firing, which starts that automation only (the
trigger matches its own schedule's name). Automation files are re-read as
the server runs, so a schedule added, changed or removed takes effect
without a restart. A firing the server was down for is not made up later. ``omniagents
integrations run-schedule <name>`` fires one now.
Each firing is its own conversation.

A schedule with ``review_runs`` (``24h``, ``7d``, ``90m``) gives its run the
factory's own record as context: every automation run in that window, how it
ended and what the judges on live runs made of it, with what started the
runs that failed, or that a ``self_improve`` judge graded Fail, and their
last messages. A nightly
review reads it to propose changes to the factory::

    schedule:
      name: nightly-review
      cron: "0 2 * * *"
      review_runs: 24h
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from omniagents.core.automations import list_automations, load_automation
from omniagents.core.autonomy.cron import _matches, parse_cron

from .events import IncomingEvent

__all__ = [
    "RUN_SCHEDULE_PATH",
    "SCHEDULES_PATH",
    "Schedule",
    "ScheduleSource",
    "next_fire",
    "project_schedules",
    "review_context",
]

logger = logging.getLogger(__name__)

#: Fires a schedule on demand, with the server's token (``omniagents integrations run-schedule``).
RUN_SCHEDULE_PATH = "/integrations/schedules/{name}/run"
SCHEDULES_PATH = "/integrations/schedules"


@dataclass(frozen=True)
class Schedule:
    automation: str
    name: str
    cron: str
    timezone: Optional[str] = None
    #: ``review_runs``, in seconds: the window of runs its firing reviews.
    review: Optional[float] = None

    @property
    def key(self) -> Tuple[str, str, str, str, str]:
        return (
            self.automation,
            self.name,
            self.cron,
            self.timezone or "",
            str(self.review or ""),
        )


_DURATION = re.compile(r"^\s*(\d+)\s*([mhd])\s*$")


def parse_duration(value: Any) -> float:
    """``90m``, ``24h`` or ``7d`` in seconds."""
    match = _DURATION.match(str(value))
    if not match:
        raise ValueError(f"{value!r} is not a duration such as 24h, 7d or 90m")
    return int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400}[match.group(2)]


def next_fire(cron: str, after: float, tz: Optional[str] = None) -> float:
    """The first time after ``after`` (epoch seconds) that ``cron`` names, in ``tz``."""
    schedule = parse_cron(cron)
    zone = ZoneInfo(tz) if tz else datetime.now().astimezone().tzinfo
    current = datetime.fromtimestamp(after, zone).replace(
        second=0, microsecond=0
    ) + timedelta(minutes=1)
    deadline = current + timedelta(days=366)
    while current <= deadline:
        if _matches(schedule, current):
            return current.timestamp()
        current += timedelta(minutes=1)
    raise ValueError(f"cron {cron!r} names no time within a year")


def project_schedules(project_root: Path) -> List[Schedule]:
    """Every schedule the project's automations declare (malformed ones are logged and left out)."""
    schedules: List[Schedule] = []
    for automation in list_automations(project_root):
        for trigger in automation.triggers:
            if trigger.get("provider") != "schedule":
                continue
            block = trigger.get("schedule") or {}
            name = str(block.get("name") or automation.name)
            cron = str(block.get("cron") or "")
            tz = block.get("timezone") or None
            try:
                parse_cron(cron)
                if tz:
                    ZoneInfo(str(tz))
                review = (
                    parse_duration(block["review_runs"])
                    if block.get("review_runs")
                    else None
                )
            except Exception as exc:
                logger.warning(
                    "integrations: schedule %r of %s is off: %s",
                    name,
                    automation.name,
                    exc,
                )
                continue
            schedules.append(
                Schedule(automation.name, name, cron, str(tz) if tz else None, review)
            )
    return schedules


class ScheduleSource:
    """Fires the project's schedules into ``accept``."""

    def __init__(
        self,
        project_root: Path,
        accept: Callable[[IncomingEvent], Awaitable[bool]],
        *,
        clock: Callable[[], float] = time.time,
        reread_seconds: float = 30.0,
        runs: Optional[Callable[[float], List[Dict[str, Any]]]] = None,
        improve: Optional[set] = None,
        unmatched: Optional[Callable[[float], List[Dict[str, Any]]]] = None,
    ) -> None:
        """``runs(since)``: the factory's runs since then (``EventStore.runs_since``),
        for schedules with ``review_runs``; ``improve``: the judges (``self_improve``)
        whose Fails the review is shown in full; ``unmatched(since)``: the kinds of
        event that started nothing (``EventStore.unmatched_since``)."""
        self.project_root = Path(project_root)
        self._accept = accept
        self._runs = runs
        self._improve = improve or set()
        self._unmatched = unmatched
        self._clock = clock
        self._reread = reread_seconds
        self._next: Dict[Tuple[str, ...], Tuple[Schedule, float]] = {}
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def refresh(self) -> None:
        """Re-read the automations: new schedules start from now, removed ones stop."""
        now = self._clock()
        current = {s.key: s for s in project_schedules(self.project_root)}
        for key in list(self._next):
            if key not in current:
                del self._next[key]
        for key, schedule in current.items():
            if key not in self._next:
                self._next[key] = (
                    schedule,
                    next_fire(schedule.cron, now, schedule.timezone),
                )

    async def tick(self) -> None:
        """Fire every schedule whose time has come."""
        now = self._clock()
        for key, (schedule, at) in list(self._next.items()):
            if at > now:
                continue
            self._next[key] = (
                schedule,
                next_fire(schedule.cron, max(now, at), schedule.timezone),
            )
            from .self_improvement import allowed

            if not await allowed(
                load_automation(self.project_root, schedule.automation)
            ):
                continue
            context = None
            if schedule.review and self._runs is not None:
                context = review_context(
                    self._runs(at - schedule.review),
                    at - schedule.review,
                    self._improve,
                    self._unmatched(at - schedule.review) if self._unmatched else [],
                )
            await self._accept(fired(schedule, at, context))

    def listing(self) -> List[Dict[str, Any]]:
        """The project's schedules as they are now, with when each fires next."""
        self.refresh()
        return [
            {
                "name": schedule.name,
                "automation": schedule.automation,
                "cron": schedule.cron,
                "timezone": schedule.timezone,
                "review_runs": schedule.review,
                "next": _stamp(at),
            }
            for schedule, at in sorted(self._next.values(), key=lambda pair: pair[1])
        ]

    async def fire_now(self, name: str) -> bool:
        """Fire the schedule named ``name`` now, as its time would (``run-schedule``);
        ``False`` if the project has none by that name."""
        schedule = next(
            (s for s in project_schedules(self.project_root) if s.name == name), None
        )
        if schedule is None:
            return False
        from .self_improvement import allowed

        if not await allowed(load_automation(self.project_root, schedule.automation)):
            raise PermissionError(
                "Self-improvement is disabled or its factory repository is unavailable"
            )
        at = self._clock()
        context = None
        if schedule.review and self._runs is not None:
            context = review_context(
                self._runs(at - schedule.review),
                at - schedule.review,
                self._improve,
                self._unmatched(at - schedule.review) if self._unmatched else [],
            )
        event = fired(schedule, at, context)
        # Its own delivery: a firing on demand never collides with the schedule's own.
        event = dataclasses.replace(
            event, delivery_id=f"{event.delivery_id}:now:{uuid.uuid4().hex[:8]}"
        )
        return await self._accept(event)

    def seconds_to_next(self) -> float:
        now = self._clock()
        soonest = min((at for _, at in self._next.values()), default=now + self._reread)
        return max(0.0, min(soonest - now, self._reread))

    async def _run(self) -> None:
        last_read = 0.0
        while True:
            try:
                if self._clock() - last_read >= self._reread:
                    self.refresh()
                    last_read = self._clock()
                await self.tick()
            except Exception:
                logger.exception("integrations: the schedule source failed a tick")
            await asyncio.sleep(max(1.0, self.seconds_to_next()))


def _stamp(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " [...]"


def review_context(
    runs: List[Dict[str, Any]],
    since: float,
    improve: set,
    unmatched: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """The factory's runs since ``since``, for its review: one line each, what started
    and ended the ones that failed or that a judge in ``improve`` graded Fail, and the
    kinds of event that started nothing (work that may have gone unhandled)."""
    tail = []
    if unmatched:
        tail = ["", "Events that started nothing (no automation matches them):", ""]
        tail += [f"- {u['provider']} {u['event']}: {u['n']}" for u in unmatched]
    if not runs:
        return "\n".join([f"The factory has no runs since {_stamp(since)}."] + tail)
    graded = [r for r in runs if r["grades"]]
    failing = [g for r in runs for g in r["grades"] if g["passed"] is False]
    lines = [
        f"The factory's runs since {_stamp(since)}: {len(runs)}, "
        f"{sum(1 for r in runs if r['status'] != 'completed')} not completed; "
        f"{len(graded)} graded, {len(failing)} grade(s) Fail.",
        "",
    ]
    for run in runs:
        grades = ", ".join(
            f"{g['judge']} {'Pass' if g['passed'] else 'Fail' if g['passed'] is False else 'no verdict'}"
            + (f" ({g['reason']})" if g["reason"] and g["passed"] is not True else "")
            + (f" on {g['agent']}'s session" if g["agent"] != run["agent"] else "")
            for g in run["grades"]
        )
        lines.append(
            f"- Run {run['id']}, {_stamp(run['started_at'])}: {run['automation']} ({run['agent']}), "
            f"from {run['provider']} {run['event']}, {run['status']}"
            + (f"; graded {grades}" if grades else "")
        )
    for run in runs:
        failed = run["status"] not in ("completed", "running")
        fails = [
            g for g in run["grades"] if g["passed"] is False and g["judge"] in improve
        ]
        if not failed and not fails:
            continue
        lines += [
            "",
            f"### Run {run['id']}: {run['automation']} ({run['agent']}), {run['status']}",
            "",
        ]
        if run["error"]:
            lines.append(f"Error: {_clip(run['error'], 1000)}")
        for grade in fails:
            lines.append(
                f"{grade['judge']} ({grade['agent']}, session {grade['session_id']}): "
                f"Fail ({grade['reason'] or 'no reason'}). "
                f"{_clip(grade['reasoning'] or '', 2000)}"
            )
        lines += [
            "",
            "What started it:",
            "",
            "```json",
            _clip(
                json.dumps(run["payload"], indent=2, ensure_ascii=False, default=str),
                3000,
            ),
            "```",
        ]
        if run["output"]:
            lines += ["", "Its last message:", "", _clip(run["output"], 3000)]
    return "\n".join(lines + tail)


def fired(
    schedule: Schedule, at: float, context: Optional[str] = None
) -> IncomingEvent:
    stamp = _stamp(at)
    return IncomingEvent(
        provider="schedule",
        event="cron_fired",
        # One firing is one delivery: a restart in the same minute does not repeat it.
        delivery_id=f"{schedule.automation}:{schedule.name}:{stamp}",
        payload={
            "schedule": schedule.name,
            "cron": schedule.cron,
            "timezone": schedule.timezone,
            "scheduled_for": stamp,
        },
        filters={"schedules": [schedule.name]},
        context=context,
    )
