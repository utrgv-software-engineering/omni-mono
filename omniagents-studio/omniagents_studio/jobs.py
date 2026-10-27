"""Long-running work Studio starts for a person: an evaluation run, scenario
generation, annotating a set, optimizing a judge or an agent.

Each is a :class:`Job` with a progress count, a tail of its log, and a
result or an error. The page that started one follows it over a socket
(``/api/evaluation/jobs/<id>/ws``) or by asking for it; jobs live as long
as the Studio process.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: Log lines kept per job; older ones are dropped.
LOG_LINES = 400


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str
    params: dict[str, Any]
    state: str = "queued"  # queued | running | completed | failed
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    completed: int = 0
    total: int | None = None
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    result: dict[str, Any] | None = None
    error: str | None = None
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "params": self.params,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "completed": self.completed,
            "total": self.total,
            "log": list(self.log),
            "result": self.result,
            "error": self.error,
        }


class JobReporter:
    """What a job's work uses to report progress and log lines. Safe to
    call from the worker thread a job's work runs on."""

    def __init__(self, jobs: Jobs, job: Job, loop: asyncio.AbstractEventLoop) -> None:
        self._jobs = jobs
        self._job = job
        self._loop = loop

    def _apply(self, update: Callable[[Job], None]) -> None:
        def apply() -> None:
            update(self._job)
            self._jobs.changed(self._job)

        self._loop.call_soon_threadsafe(apply)

    def progress(self, completed: int, total: int | None = None) -> None:
        def update(job: Job) -> None:
            job.completed = max(job.completed, int(completed))
            if total is not None:
                job.total = int(total)

        self._apply(update)

    def log(self, line: str) -> None:
        self._apply(lambda job: job.log.append(str(line)))


Work = Callable[[JobReporter], Awaitable[dict[str, Any]]]


class Jobs:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._changed: dict[str, asyncio.Event] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self, kind: str, params: dict[str, Any], work: Work) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, params=params)
        self._jobs[job.id] = job
        self._changed[job.id] = asyncio.Event()
        task = asyncio.get_running_loop().create_task(self._run(job, work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: Job, work: Work) -> None:
        job.state, job.started_at = "running", _now()
        self.changed(job)
        reporter = JobReporter(self, job, asyncio.get_running_loop())
        try:
            job.result = await work(reporter)
            job.state = "completed"
        except (Exception, SystemExit) as exc:  # the job's failure is its result
            logger.info("Studio job %s (%s) failed", job.id, job.kind, exc_info=True)
            job.state = "failed"
            job.error = str(exc) or type(exc).__name__
            job.log.extend(traceback.format_exception_only(type(exc), exc))
        job.finished_at = _now()
        self.changed(job)

    def changed(self, job: Job) -> None:
        job.revision += 1
        event = self._changed.get(job.id)
        if event is not None:
            event.set()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    async def follow(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        """The job as it changes, until it finishes."""
        job = self._jobs[job_id]
        event = self._changed[job_id]
        seen = -1
        while True:
            if job.revision != seen:
                seen = job.revision
                yield job.to_dict()
            if job.state in ("completed", "failed"):
                return
            event.clear()
            if job.revision == seen:
                await event.wait()
