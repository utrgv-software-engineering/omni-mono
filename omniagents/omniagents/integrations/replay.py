"""Replaying one factory's events into another, and comparing how each did.

Staging tries a candidate factory on real work: its workplace is a copy of
production's (last night's backup), and production's recent events (the
Slack mentions, delegated Linear issues and GitHub events that started runs)
are sent to it again. Each replayed event is a new delivery, in a
conversation of its own (``replay:`` before its delivery id and thread), so
the candidate starts afresh. The judges on live runs grade every replayed run,
not a sample.

``omniagents integrations replay`` sends them to a factory's
``POST /integrations/replay``, which takes the factory's own token.
``omniagents integrations compare`` then sets the candidate's runs beside the
originals: how each ended and what the judges said.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Dict, List

from .events import IncomingEvent
from .store import EventStore

__all__ = [
    "PREFIX",
    "REPLAY_PATH",
    "compare",
    "pass_rates",
    "regressions",
    "event_from_json",
    "event_json",
    "format_comparison",
    "is_replay",
    "replayed",
]

PREFIX = "replay:"
REPLAY_PATH = "/integrations/replay"


def is_replay(event: IncomingEvent) -> bool:
    return event.delivery_id.startswith(PREFIX)


def replayed(event: IncomingEvent) -> IncomingEvent:
    """``event`` as a new delivery in a conversation of its own."""
    return dataclasses.replace(
        event,
        delivery_id=PREFIX + event.delivery_id,
        thread_key=PREFIX + event.thread_key if event.thread_key else None,
        received_at=time.time(),
    )


def event_json(event: IncomingEvent) -> Dict[str, Any]:
    return dataclasses.asdict(event)


def event_from_json(data: Dict[str, Any]) -> IncomingEvent:
    fields = {f.name for f in dataclasses.fields(IncomingEvent)}
    unknown = set(data) - fields
    if unknown:
        raise ValueError(f"unknown event fields: {sorted(unknown)}")
    return IncomingEvent(**data)


def _grades(run: Dict[str, Any]) -> str:
    out = []
    for grade in run["grades"]:
        word = (
            "Pass"
            if grade["passed"]
            else "Fail" if grade["passed"] is False else "no verdict"
        )
        out.append(f"{grade['judge']} {word}")
    return ", ".join(out) or "not graded"


def compare(
    baseline: EventStore, candidate: EventStore, since: float
) -> List[Dict[str, Any]]:
    """Each replayed run in ``candidate`` since ``since``, beside the run of the
    same automation that its event started in ``baseline`` (``None`` if none)."""
    originals: Dict[tuple, Dict[str, Any]] = {}
    for run in baseline.runs_since(0):
        originals[(run["delivery_id"], run["automation"])] = run
    rows = []
    for run in candidate.runs_since(since):
        if not run["delivery_id"].startswith(PREFIX):
            continue
        original = originals.get((run["delivery_id"][len(PREFIX) :], run["automation"]))
        rows.append({"candidate": run, "baseline": original})
    return rows


def pass_rates(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per judge, each side's ``(passed, graded)`` over the replayed runs."""
    tally: Dict[str, Dict[str, List[int]]] = {}
    for row in rows:
        for side, source in (
            ("baseline", row["baseline"]),
            ("candidate", row["candidate"]),
        ):
            for grade in (source or {}).get("grades") or []:
                if grade["passed"] is None:
                    continue
                counts = tally.setdefault(grade["judge"], {}).setdefault(side, [0, 0])
                counts[0] += int(grade["passed"])
                counts[1] += 1
    return {
        judge: {
            side: tuple(sides.get(side, [0, 0])) for side in ("baseline", "candidate")
        }
        for judge, sides in tally.items()
    }


def regressions(rows: List[Dict[str, Any]]) -> List[str]:
    """What the candidate did worse: a judge's lower pass rate (where both sides were
    graded), or a run that failed where the original completed."""
    out = []
    for judge, sides in sorted(pass_rates(rows).items()):
        (bp, bg), (cp, cg) = sides["baseline"], sides["candidate"]
        if bg and cg and cp / cg < bp / bg:
            out.append(f"{judge}: {cp}/{cg} Pass, down from {bp}/{bg}")
    for row in rows:
        run, original = row["candidate"], row["baseline"]
        if (
            original
            and original["status"] == "completed"
            and run["status"] != "completed"
        ):
            out.append(
                f"run {run['id']} ({run['automation']}): {run['status']}, where the original completed"
            )
    return out


def format_comparison(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No replayed runs."
    lines = []
    for row in rows:
        run, original = row["candidate"], row["baseline"]
        lines.append(
            f"{run['automation']} ({run['agent']}), from {run['provider']} {run['event']}:\n"
            f"  baseline:  {original['status'] + ', ' + _grades(original) if original else 'not found'}\n"
            f"  candidate: {run['status']}, {_grades(run)}"
        )
    lines.append("")
    for judge, sides in sorted(pass_rates(rows).items()):
        parts = []
        for side in ("baseline", "candidate"):
            passed, graded = sides[side]
            parts.append(
                f"{side} {passed}/{graded} Pass" if graded else f"{side} not graded"
            )
        lines.append(f"{judge}: " + "; ".join(parts))
    return "\n".join(lines)
