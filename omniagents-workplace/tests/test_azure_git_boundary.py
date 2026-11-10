import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from omniagents_workplace.server import SandboxBoundary
from omniagents_workplace.azure_devops.git import router


@pytest.mark.parametrize(
    "endpoint", ["info/refs", "git-upload-pack", "git-receive-pack"]
)
@pytest.mark.parametrize("port_boundary", [False, True])
def test_azure_git_reaches_authenticated_broker(endpoint, port_boundary):
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"], status_code=401)
    async def authenticated_broker(path: str):
        return {"detail": "Git capability required"}

    boundary = SandboxBoundary(
        app,
        addresses=[] if port_boundary else ["testserver"],
        ports=[80] if port_boundary else [],
    )
    with TestClient(boundary) as client:
        path = f"/azure-devops/git/connection/project/repository/{endpoint}"
        response = (
            client.get(path, params={"service": "git-upload-pack"})
            if endpoint == "info/refs"
            else client.post(path, content=b"0000")
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Git capability required"}


@pytest.mark.parametrize(
    "path",
    [
        "/azure-devops/mcp",
        "/workplace/api/azure-devops/connections",
        "/azure-devops/git/connection/project/repository/admin",
        "/azure-devops/git/connection/project/repository/info/refs/extra",
        "/azure-devops/git/connection/project/repository/git-upload-pack/extra",
        "/azure-devops/git/connection/project/info/refs",
        "/azure-devops/git/connection/project/repository/extra/info/refs",
        "/",
    ],
)
def test_azure_git_boundary_preserves_denials(path):
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def private_endpoint(path: str):
        return {"reachable": True}

    with TestClient(SandboxBoundary(app, addresses=["testserver"])) as client:
        response = client.get(path)
        assert response.status_code == 403
        assert response.text.startswith("Sandboxes reach")


@pytest.mark.parametrize(
    "authorization", ["", "Basic invalid", "Basic YnJva2VyOmJhZA=="]
)
def test_azure_git_challenges_git_credential_helper(authorization):
    class Connections:
        def app_for_token(self, supplied):
            raise HTTPException(401, "A scoped Workplace app bearer token is required")

    app = FastAPI()
    app.include_router(router(Connections()))
    with TestClient(app) as client:
        response = client.get(
            "/azure-devops/git/connection/project/repository/info/refs",
            params={"service": "git-upload-pack"},
            headers={"Authorization": authorization} if authorization else {},
        )
        assert response.status_code == 401
        assert (
            response.headers.get("www-authenticate") == 'Basic realm="Azure DevOps Git"'
        )
