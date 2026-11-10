"""Read-only release evidence with a separate, repository-scoped credential."""

import hashlib

from fastapi import APIRouter, HTTPException, Request, Response

from .workflow_settings import WorkflowSettings


def release_evidence_router(state):
    router = APIRouter()

    @router.get("/github/release-evidence/{owner}/{name}/{run_id}")
    def evidence(
        owner: str, name: str, run_id: int, request: Request, response: Response
    ):
        settings = WorkflowSettings(state.root.parent)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        repository = owner + "/" + name
        if not settings.permits_release_read(repository, token):
            raise HTTPException(403, "Release evidence credential is invalid")
        repo = state.repos.get(repository)
        run = next((r for r in repo.runs if r.id == run_id), None) if repo else None
        if not run:
            raise HTTPException(404, "Workflow run not found")
        blob = repo.bare.blob(run.head_sha, run.path)
        response.headers["Cache-Control"] = "no-store"
        return {
            "provider": "standin",
            "repository": repository,
            "run_id": str(run.id),
            "revision": run.head_sha,
            "current_revision": repo.bare.ref_sha(repo.default_branch),
            "branch": run.head_branch,
            "default_branch": repo.default_branch,
            "protected": repo.default_branch in repo.protected,
            "event": run.event,
            "path": run.path,
            "status": run.status,
            "workflow_sha256": hashlib.sha256(blob[1]).hexdigest() if blob else None,
            "jobs": [
                {"name": j.name, "status": j.status, "conclusion": j.conclusion}
                for j in run.jobs
            ],
        }

    return router
