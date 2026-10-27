"""Drive scenario events through production intake, retaining each phase's evidence.

The workplace replaces external services, not automation matching, deduplication,
thread routing, role definitions, approvals, or agent execution. Provider HTTP
signature verification is covered separately by integration tests.
"""

from __future__ import annotations

import asyncio
import copy

from omniagents.core.eval.targets import AgentRun
from omniagents.core.evaluation import final_assistant_text
from omniagents.integrations.intake import EventIntake, RunResult
from omniagents.integrations.store import EventStore


class FactoryEventRun(AgentRun):
    def __init__(self, target, scenario, defaults, environment, context):
        self.target, self.scenario, self.defaults = target, scenario, defaults
        self.environment, self.context = environment, context
        self.runs = {}
        self._history = []
        self._error = None
        self.available_tools = []
        self.description = "Factory event workflow"
        self._config = None
        self._usage = {}
        self.store = EventStore(environment.event_store_path(context))
        project = target._project_context()
        self.intake = EventIntake(
            project_root=project.root,
            entrypoint=project.entrypoint,
            store=self.store,
            run=self._dispatch,
            max_concurrent_runs=1,
        )

    @property
    def session_id(self):
        first = next(iter(self.runs.values()), None)
        return getattr(first, "session_id", None) or self.context.session_id

    async def _dispatch(self, agent_key, session_id, prompt, progress=None):
        if session_id not in self.runs:
            scenario = {
                k: v
                for k, v in self.scenario.items()
                if k not in ("events", "automation", "agent")
            }
            self.runs[session_id] = await self.target.for_agent(agent_key).start(
                scenario,
                defaults=self.defaults,
                environment=self.environment,
                context=self.context,
            )
        run = self.runs[session_id]
        before = len(run.history())
        try:
            telemetry = await run.run_turn(prompt, self._config)
        finally:
            self._history.extend(run.history()[before:])
            self.available_tools = list(run.available_tools)
        for key, value in (telemetry.get("usage") or {}).items():
            if isinstance(value, (int, float)):
                self._usage[key] = self._usage.get(key, 0) + value
        error = run.run_error()
        if error:
            self._error = error
        return RunResult(
            error=str(error) if error else None,
            output=final_assistant_text(run.history()),
        )

    async def run_turn(self, prompt, run_config=None):
        self._config = run_config
        phases = self.context.data.setdefault("event_phases", [])
        await self.environment.collect(self.context)
        self.context.data["event_baseline"] = self._snapshot()
        for index, step in enumerate(self.scenario["events"]):
            event = await self.environment.prepare_event(self.context, step, index)
            accepted = await self.intake.accept(event) if event is not None else False
            async with asyncio.timeout(
                int(self.scenario.get("event_timeout_seconds", 2400))
            ):
                await self.intake.drain()
            runs = self.store.runs_since(0)
            failures = [r for r in runs if r["status"] != "completed"]
            if failures:
                self._error = {
                    "error": {
                        "message": failures[-1].get("error") or "event dispatch failed"
                    }
                }
            await self.environment.collect(self.context)
            snapshot = self._snapshot()
            snapshot.update(
                name=step.get("name", str(index)),
                accepted=accepted,
                dispatches=copy.deepcopy(runs),
                history_end=len(self._history),
            )
            if step.get("validate"):
                try:
                    snapshot["validation"] = await self.environment.validate_event(
                        self.context, step
                    )
                except (ValueError, asyncio.TimeoutError) as exc:
                    snapshot["validation"] = {"exit_code": 1, "error": str(exc)}
            phases.append(snapshot)
        self.context.data["event_dispatches"] = self.store.runs_since(0)
        return {"usage": self._usage}

    def _snapshot(self):
        return copy.deepcopy({k: self.context.data.get(k) for k in ("github", "slack")})

    def history(self):
        return self._history

    def run_error(self):
        return self._error

    def annotate(self, metadata):
        for run in self.runs.values():
            run.annotate(metadata)

    async def cleanup(self):
        for task in list(self.intake._tasks):
            task.cancel()
        await self.intake.drain()
        try:
            for run in self.runs.values():
                await run.cleanup()
        finally:
            self.store.close()
