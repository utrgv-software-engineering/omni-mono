import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Query

from ..accounts import admin
from .client import segment
from .connections import ConnectionBody, EVENTS


def router(connections):
    routes = APIRouter()

    @routes.get("/workplace/api/azure-devops/connections")
    async def listed(request: Request):
        admin(request)
        from .tools import WRITE_TOOLS

        return {
            "connections": [
                connections.public(value) for value in connections.records().values()
            ],
            "events": list(EVENTS),
            "write_tools": [*WRITE_TOOLS, "git_push"],
            "apps": [
                {"id": app.id, "name": app.username}
                for app in connections.w.apps.active()
            ],
        }

    @routes.post("/workplace/api/azure-devops/connections")
    async def created(body: ConnectionBody, request: Request):
        admin(request)
        return connections.save(secrets.token_hex(16), body)

    @routes.put("/workplace/api/azure-devops/connections/{identifier}")
    async def saved(identifier: str, body: ConnectionBody, request: Request):
        admin(request)
        connections.get(identifier)
        return connections.save(identifier, body)

    @routes.delete("/workplace/api/azure-devops/connections/{identifier}")
    async def deleted(identifier: str, request: Request, revision: int = Query(ge=1)):
        admin(request)
        connections.delete(identifier, revision)
        return {"ok": True}

    @routes.get("/workplace/api/azure-devops/connections/{identifier}/available")
    async def available(
        identifier: str,
        request: Request,
        project: str | None = None,
        resource: Literal["repositories", "pipelines"] = "repositories",
    ):
        admin(request)
        client = connections.client(connections.get(identifier))
        if project is None:
            return {
                "projects": await client.all("/_apis/projects", params={"$top": 100})
            }
        await client.json("GET", "/_apis/projects/" + segment(project))
        path = (
            "/_apis/git/repositories"
            if resource == "repositories"
            else "/_apis/build/definitions"
        )
        try:
            values = await client.all(
                f"/{segment(project)}{path}", params={"$top": 100}
            )
        except HTTPException as exc:
            if exc.status_code in (401, 403):
                permission = (
                    "Code (Read)" if resource == "repositories" else "Build (Read)"
                )
                raise HTTPException(
                    exc.status_code,
                    f"Azure DevOps denied access to {resource}. Check the token's {permission} permission and project access. Your saved credential was not changed.",
                ) from None
            raise
        return {resource: values}

    @routes.get("/workplace/api/azure-devops/connections/{identifier}/preview")
    async def preview(identifier: str, request: Request):
        admin(request)
        connection = connections.get(identifier)
        client = connections.client(connection)
        result = []
        for scope in connection["scopes"]:
            for repository in scope["repositories"]:
                values = await client.all(
                    f"/{segment(scope['project'])}/_apis/git/repositories/{segment(repository)}/pullrequests",
                    params={"searchCriteria.status": "active", "$top": 100},
                    offset=True,
                )
                result.append(
                    {
                        "project_id": scope["project"],
                        "repository_id": repository,
                        "pull_requests": values,
                    }
                )
        return {"resources": result, "dispatches": 0}

    @routes.get("/azure-devops/policy")
    async def policy(request: Request):
        app_id = connections.app_for_token(request.headers.get("authorization", ""))
        values = []
        for connection in connections.records().values():
            if connection["enabled"] and app_id in connection["app_ids"]:
                scopes = []
                for scope in connection["scopes"]:
                    repositories = [
                        {
                            "id": repository,
                            "clone_url": connections.w.sandbox_url.rstrip("/")
                            + f"/azure-devops/git/{connection['id']}/{scope['project']}/{repository}",
                            "canonical_url": f"https://dev.azure.com/{connection['organization']}/{scope['project']}/_git/{repository}",
                        }
                        for repository in scope["repositories"]
                    ]
                    scopes.append({**scope, "repositories": repositories})
                values.append(
                    {
                        "id": connection["id"],
                        "label": connection["label"],
                        "organization": connection["organization"],
                        "scopes": scopes,
                        "write_tools": connection["write_tools"],
                    }
                )
        return {
            "connections": values,
            "mcp_url": connections.w.sandbox_url.rstrip("/") + "/azure-devops/mcp",
        }

    return routes
