"""Studio's evaluation API: the project's :class:`EvalWorkspace` over HTTP.

Every endpoint is a call into the workspace, so Studio reads and writes a
project's evaluation exactly as ``omniagents eval`` does. Long work
(running scenarios, generating them, annotating a set, optimizing a judge
or an agent) starts a job (:mod:`.jobs`) the page follows.

Reviews (a person's judgment of one conversation, with the failure
categories it showed) are Studio's own data, kept with its traces; they
count toward a category's use here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from omniagents.core.eval.workspace import EvalWorkspace, EvalWorkspaceError, NotFound

from .jobs import JobReporter, Jobs

router = APIRouter(prefix="/api/evaluation")


def _manifest(request: Request) -> Path:
    resources = getattr(request.app.state, "project_resources", {}) or {}
    hint = resources.get("manifest_path")
    if hint and Path(hint).expanduser().is_file():
        return Path(hint).expanduser().resolve()
    raise HTTPException(
        status_code=404, detail="Launch Studio from a project to evaluate it"
    )


def _project_name(request: Request) -> str | None:
    resources = getattr(request.app.state, "project_resources", {}) or {}
    return resources.get("project_name") or getattr(
        request.app.state, "default_project", None
    )


def _workspace(request: Request, agent: str | None = None) -> EvalWorkspace:
    return EvalWorkspace(_manifest(request), agent=agent or None)


def _call(action: Callable[[], Any]) -> Any:
    """Run a workspace call, answering its refusals as HTTP errors."""
    try:
        return action()
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (EvalWorkspaceError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _review_usage(request: Request) -> dict[str, int]:
    project = _project_name(request)
    if not project:
        return {}
    storage = request.app.state.storage_manager.get_storage(project)
    return storage.get_analysis_category_usage(project)


# -- dimensions ---------------------------------------------------------------


class Dimensions(BaseModel):
    dimensions: list[dict[str, Any]]


@router.get("/dimensions")
def get_dimensions(request: Request) -> dict[str, Any]:
    return {"dimensions": _call(lambda: _workspace(request).dimensions())}


@router.put("/dimensions")
def put_dimensions(request: Request, body: Dimensions) -> dict[str, Any]:
    return {
        "dimensions": _call(lambda: _workspace(request).set_dimensions(body.dimensions))
    }


# -- failure categories -------------------------------------------------------


class NewCategory(BaseModel):
    name: str
    description: str | None = None
    measures: list[str] = Field(default_factory=list)


class CategoryChange(BaseModel):
    description: str | None = None
    measures: list[str] | None = None


def _with_review_usage(request: Request, categories: list[dict]) -> list[dict]:
    reviewed = _review_usage(request)
    return [{**c, "review_count": reviewed.get(c["id"], 0)} for c in categories]


@router.get("/categories")
def get_categories(request: Request) -> dict[str, Any]:
    categories = _call(lambda: _workspace(request).failure_categories())
    return {"categories": _with_review_usage(request, categories)}


@router.post("/categories")
def post_category(request: Request, body: NewCategory) -> dict[str, Any]:
    category = _call(
        lambda: _workspace(request).add_failure_category(
            body.name, description=body.description, measures=body.measures
        )
    )
    return _with_review_usage(request, [category])[0]


@router.patch("/categories/{category_id}")
def patch_category(
    request: Request, category_id: str, body: CategoryChange
) -> dict[str, Any]:
    category = _call(
        lambda: _workspace(request).update_failure_category(
            category_id, description=body.description, measures=body.measures
        )
    )
    return _with_review_usage(request, [category])[0]


@router.delete("/categories/{category_id}")
def delete_category(request: Request, category_id: str) -> dict[str, Any]:
    scenarios = _call(lambda: _workspace(request).remove_failure_category(category_id))
    project = _project_name(request)
    if project:
        storage = request.app.state.storage_manager.get_storage(project)
        storage.remove_analysis_category_slug(project, category_id)
    return {"removed_from_scenarios": scenarios}


# -- measures -----------------------------------------------------------------


class NewMeasure(BaseModel):
    name: str
    label: str | None = None
    description: str | None = None
    stub: bool = True


class MeasureChange(BaseModel):
    label: str | None = None
    description: str | None = None


@router.get("/measures")
def get_measures(request: Request) -> dict[str, Any]:
    return {"measures": _call(lambda: _workspace(request).measures())}


@router.post("/measures")
def post_measure(request: Request, body: NewMeasure) -> dict[str, Any]:
    return _call(
        lambda: _workspace(request).add_measure(
            body.name, label=body.label, description=body.description, stub=body.stub
        )
    )


@router.patch("/measures/{name}")
def patch_measure(request: Request, name: str, body: MeasureChange) -> dict[str, Any]:
    return _call(
        lambda: _workspace(request).update_measure(
            name, label=body.label, description=body.description
        )
    )


@router.delete("/measures/{name}")
def delete_measure(request: Request, name: str) -> dict[str, Any]:
    _call(lambda: _workspace(request).remove_measure(name))
    return {"removed": name}


# -- scenario sets ------------------------------------------------------------


class Match(BaseModel):
    """Which scenario of a set: by id, else name, else position."""

    id: str | None = None
    name: str | None = None
    index: int | None = None


class ScenarioWrite(BaseModel):
    set_path: str
    scenario: dict[str, Any]
    match: Match | None = None


class ScenarioTarget(BaseModel):
    set_path: str
    match: Match


@router.get("/scenario-sets")
def get_scenario_sets(request: Request) -> dict[str, Any]:
    workspace = _workspace(request)
    return _call(
        lambda: {
            "sets": workspace.scenario_sets(),
            "evaluations_dir": str(workspace.eval_dir),
        }
    )


@router.post("/scenario-sets/scenarios")
def post_scenario(request: Request, body: ScenarioWrite) -> dict[str, Any]:
    return _call(lambda: _workspace(request).add_scenario(body.set_path, body.scenario))


@router.put("/scenario-sets/scenarios")
def put_scenario(request: Request, body: ScenarioWrite) -> dict[str, Any]:
    match = body.match or Match()
    return _call(
        lambda: _workspace(request).update_scenario(
            body.set_path,
            body.scenario,
            scenario_id=match.id,
            name=match.name,
            index=match.index,
        )
    )


@router.post("/scenario-sets/scenarios/remove")
def remove_scenario(request: Request, body: ScenarioTarget) -> dict[str, Any]:
    return _call(
        lambda: _workspace(request).remove_scenario(
            body.set_path,
            scenario_id=body.match.id,
            name=body.match.name,
            index=body.match.index,
        )
    )


@router.post("/scenario-sets/scenarios/promote")
def promote_scenario(request: Request, body: ScenarioTarget) -> dict[str, Any]:
    return _call(
        lambda: _workspace(request).promote_scenario(
            body.set_path,
            scenario_id=body.match.id,
            name=body.match.name,
            index=body.match.index,
        )
    )


@router.delete("/scenario-sets")
def delete_scenario_set(request: Request, path: str = Query(...)) -> dict[str, Any]:
    _call(lambda: _workspace(request).remove_scenario_set(path))
    return {"removed": path}


# -- judges ---------------------------------------------------------------------


class NewJudge(BaseModel):
    key: str
    model: str
    source: str = "final_assistant_text"
    failure_category: str | None = None
    label: str | None = None
    instructions: str | None = None
    template: str | None = None


class JudgeChange(BaseModel):
    model: str | None = None
    source: str | None = None
    failure_category: str | None = None
    label: str | None = None
    instructions: str | None = None
    template: str | None = None


@router.get("/judges")
def get_judges(request: Request) -> dict[str, Any]:
    return {"judges": _call(lambda: _workspace(request).judges())}


@router.post("/judges")
def post_judge(request: Request, body: NewJudge) -> dict[str, Any]:
    fields = body.model_dump()
    key = fields.pop("key")
    return _call(lambda: _workspace(request).add_judge(key, **fields))


@router.patch("/judges/{key}")
def patch_judge(request: Request, key: str, body: JudgeChange) -> dict[str, Any]:
    return _call(lambda: _workspace(request).update_judge(key, **body.model_dump()))


@router.delete("/judges/{key}")
def delete_judge(request: Request, key: str) -> dict[str, Any]:
    _call(lambda: _workspace(request).remove_judge(key))
    return {"removed": key}


# -- judge datasets -------------------------------------------------------------


class NewJudgeDataset(BaseModel):
    set_path: str
    category: str
    fractions: tuple[float, float, float] = (0.15, 0.425, 0.425)
    seed: int = 42
    negatives_ratio: float | None = None


@router.get("/judge-datasets")
def get_judge_datasets(request: Request) -> dict[str, Any]:
    return {"datasets": _call(lambda: _workspace(request).judge_datasets())}


@router.post("/judge-datasets")
def post_judge_dataset(request: Request, body: NewJudgeDataset) -> dict[str, Any]:
    return _call(
        lambda: _workspace(request).create_judge_dataset(
            body.set_path,
            body.category,
            fractions=body.fractions,
            seed=body.seed,
            negatives_ratio=body.negatives_ratio,
        )
    )


@router.get("/judge-datasets/split")
def get_judge_dataset_split(request: Request, path: str = Query(...)) -> dict[str, Any]:
    return _call(lambda: _workspace(request).judge_dataset_split(path))


@router.delete("/judge-datasets")
def delete_judge_dataset(
    request: Request, category: str = Query(...)
) -> dict[str, Any]:
    _call(lambda: _workspace(request).remove_judge_dataset(category))
    return {"removed": category}


# -- holdout --------------------------------------------------------------------


class NewHoldout(BaseModel):
    strata: list[str] = Field(default_factory=list)
    fractions: tuple[float, float, float] = (0.5, 0.25, 0.25)
    seed: int = 42
    select: str | None = None
    exclude: str | None = None


@router.get("/holdout")
def get_holdout(request: Request) -> dict[str, Any]:
    return {"holdout": _call(lambda: _workspace(request).holdout())}


@router.post("/holdout")
def post_holdout(request: Request, body: NewHoldout) -> dict[str, Any]:
    return {
        "holdout": _call(
            lambda: _workspace(request).create_holdout(
                strata=body.strata,
                fractions=body.fractions,
                seed=body.seed,
                select=body.select,
                exclude=body.exclude,
            )
        )
    }


# -- saved runs -----------------------------------------------------------------


@router.get("/runs")
def get_runs(request: Request) -> dict[str, Any]:
    return {"runs": _call(lambda: _workspace(request).runs())}


@router.get("/runs/detail")
def get_run(request: Request, path: str = Query(...)) -> dict[str, Any]:
    return _call(lambda: _workspace(request).read_run(path))


@router.get("/runs/compare")
def compare_runs(
    request: Request, a: str = Query(...), b: str = Query(...)
) -> dict[str, Any]:
    return _call(lambda: _workspace(request).compare_runs(a, b))


# -- jobs -------------------------------------------------------------------------


class RunJob(BaseModel):
    kind: Literal["run"]
    agent: str | None = None
    scenario_set: str | None = None
    select: str | None = None
    exclude: str | None = None
    split: str | None = None
    include_unmeasured: bool | None = None
    max_turns: int | None = None
    name: str | None = None


class GenerateJob(BaseModel):
    kind: Literal["generate"]
    name: str
    count: int | None = None
    seed: int | None = None
    dimension_values: dict[str, list[str]] | None = None


class AnnotateJob(BaseModel):
    kind: Literal["annotate"]
    set_path: str
    agent: str | None = None


class OptimizeJudgeJob(BaseModel):
    kind: Literal["optimize_judge"]
    judge: str
    category: str
    max_metric_calls: int = Field(default=60, ge=6)
    seed: int = 0


class OptimizeAgentJob(BaseModel):
    kind: Literal["optimize_agent"]
    agent: str
    category: str
    positives_only: bool = False
    max_metric_calls: int = Field(default=100, ge=6)
    seed: int = 0


JobRequest = RunJob | GenerateJob | AnnotateJob | OptimizeJudgeJob | OptimizeAgentJob


def _run_work(workspace: EvalWorkspace, body: RunJob):
    async def work(report: JobReporter) -> dict[str, Any]:
        def progress(event: dict[str, Any]) -> None:
            if event.get("event") == "progress":
                report.progress(int(event.get("index") or 0), event.get("total"))
                report.log(
                    f"{event.get('scenario')}: "
                    + (
                        "skipped"
                        if event.get("skipped")
                        else f"{event.get('failed', 0)} failed"
                    )
                )

        saved = await asyncio.to_thread(
            workspace.run,
            scenario_set=body.scenario_set,
            select=body.select,
            exclude=body.exclude,
            split=body.split,
            include_unmeasured=body.include_unmeasured,
            max_turns=body.max_turns,
            name=body.name,
            source="studio",
            progress_cb=progress,
        )
        return {"path": saved["path"], "overview": saved["overview"]}

    return work


def _generate_work(workspace: EvalWorkspace, body: GenerateJob):
    async def work(report: JobReporter) -> dict[str, Any]:
        state = {"done": 0, "total": body.count}

        def progress(event: dict[str, Any]) -> None:
            if event.get("event") == "start":
                state["total"] = event.get("target")
            elif event.get("event") == "progress":
                state["done"] += 1
            report.progress(state["done"], state["total"])

        generated = await workspace.generate_scenarios(
            body.name,
            count=body.count,
            seed=body.seed,
            dimension_values=body.dimension_values,
            progress_cb=progress,
        )
        return {"set_path": generated["path"], "count": len(generated["scenarios"])}

    return work


def _annotate_work(workspace: EvalWorkspace, body: AnnotateJob, project: str | None):
    async def work(report: JobReporter) -> dict[str, Any]:
        if not project:
            raise EvalWorkspaceError("Studio has no trace project to read reviews from")
        result = await asyncio.to_thread(
            workspace.annotate_from_reviews, body.set_path, project
        )
        labelled = sum(
            1 for s in result["set"]["scenarios"] if s.get("failure_categories")
        )
        report.log(f"Labelled {labelled} scenarios from the run {result['run']}")
        return {"set_path": result["set"]["path"], "run": result["run"]}

    return work


def _dataset_files(workspace: EvalWorkspace, category: str) -> dict[str, str]:
    dataset = workspace.judge_dataset(category)
    files = {split["split"]: split["path"] for split in dataset["splits"]}
    if set(files) != {"train", "dev", "test"}:
        raise EvalWorkspaceError(f"The judge dataset for {category!r} is incomplete")
    return files


def _optimize_judge_work(workspace: EvalWorkspace, body: OptimizeJudgeJob):
    async def work(report: JobReporter) -> dict[str, Any]:
        from omniagents.core.eval.optimize import optimize_judge_from_project

        files = _dataset_files(workspace, body.category)
        judge = workspace.judge(body.judge)
        report.progress(0, body.max_metric_calls)
        result = await asyncio.to_thread(
            optimize_judge_from_project,
            proj_path=str(workspace.manifest),
            agent_key=None,
            category=body.category,
            train_file=files["train"],
            dev_file=files["dev"],
            test_file=files["test"],
            judge_key=body.judge,
            runs_file=None,
            source=judge["source"],
            max_turns=1,
            max_metric_calls=body.max_metric_calls,
            reflection_model=None,
            optimize_components="instructions,template",
            optimizer_key=None,
            seed=body.seed,
            output_dir=None,
            resume=False,
            apply=True,
        )
        report.progress(body.max_metric_calls, body.max_metric_calls)
        return result

    return work


def _optimize_agent_work(workspace: EvalWorkspace, body: OptimizeAgentJob):
    async def work(report: JobReporter) -> dict[str, Any]:
        from omniagents.core.eval.optimize import optimize_agent_from_project

        files = _dataset_files(workspace, body.category)
        report.progress(0, body.max_metric_calls)

        def log(line: str) -> None:
            if line.startswith("PROGRESS:"):
                done = line.split(":")[1]
                if done.isdigit():
                    report.progress(int(done), body.max_metric_calls)
            elif not line.startswith("LOG_PATH:"):
                report.log(line)

        return await asyncio.to_thread(
            optimize_agent_from_project,
            proj_path=str(workspace.manifest),
            agent_key=body.agent,
            category=body.category,
            train_file=files["train"],
            dev_file=files["dev"],
            test_file=files["test"],
            positives_only=body.positives_only,
            max_turns=None,
            max_metric_calls=body.max_metric_calls,
            reflection_model="gpt-4.1",
            score_mode="fraction",
            optimizer_key=None,
            seed=body.seed,
            output_dir=None,
            resume=False,
            apply=True,
            log_hook=log,
        )

    return work


@router.post("/jobs")
async def start_job(request: Request, body: JobRequest) -> dict[str, Any]:
    jobs: Jobs = request.app.state.jobs
    agent = getattr(body, "agent", None)
    workspace = _workspace(request, agent if body.kind != "optimize_agent" else None)
    if isinstance(body, RunJob):
        work = _run_work(workspace, body)
    elif isinstance(body, GenerateJob):
        work = _generate_work(workspace, body)
    elif isinstance(body, AnnotateJob):
        work = _annotate_work(workspace, body, _project_name(request))
    elif isinstance(body, OptimizeJudgeJob):
        work = _optimize_judge_work(workspace, body)
    else:
        work = _optimize_agent_work(workspace, body)
    return jobs.start(body.kind, body.model_dump(), work).to_dict()


@router.get("/jobs")
def list_jobs(request: Request) -> dict[str, Any]:
    return {"jobs": [job.to_dict() for job in request.app.state.jobs.list()]}


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict[str, Any]:
    job = request.app.state.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job")
    return job.to_dict()


@router.websocket("/jobs/{job_id}/ws")
async def follow_job(websocket: WebSocket, job_id: str) -> None:
    await websocket.app.state.require_ws_auth(websocket)
    jobs: Jobs = websocket.app.state.jobs
    # Accepted before refusing: a socket closed before acceptance reaches a
    # browser as a failed handshake, not as the reason.
    await websocket.accept()
    if jobs.get(job_id) is None:
        await websocket.close(code=4404, reason="No such job")
        return
    try:
        async for snapshot in jobs.follow(job_id):
            await websocket.send_json(snapshot)
        await websocket.close()
    except WebSocketDisconnect:
        return
