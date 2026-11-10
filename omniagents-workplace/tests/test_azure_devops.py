import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from omniagents_workplace.azure_devops.client import AzureDevOpsClient
from omniagents_workplace.azure_devops.connections import (
    AzureDevOpsConnections,
    ConnectionBody,
)
from omniagents_workplace.azure_devops.polling import (
    observe,
    source_key,
    pull_requests,
    builds,
)
from omniagents_workplace.azure_devops.tools import execute, model_for, TOOLS, perform
from omniagents_workplace.event_gateway import EventGateway
from omniagents_workplace.store import Store
from omniagents_workplace.azure_devops.git import receive_refs, router as git_router
from omniagents_workplace.azure_devops.mcp import AzureDevOpsMCP
from fastapi import FastAPI

PROJECT = "11111111-1111-1111-1111-111111111111"
REPO = "22222222-2222-2222-2222-222222222222"


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["polling", "tool"])
async def test_policy_evaluations_use_preview_version(setup, caller):
    connections, connection, workplace = setup
    policy_requests = []

    def handler(request):
        path = request.url.path
        if path.endswith("/policy/evaluations"):
            policy_requests.append(request)
            if request.url.params["api-version"] != "7.1-preview.1":
                return httpx.Response(
                    400, json={"typeKey": "VssInvalidPreviewVersionException"}
                )
        if path.endswith("/pullrequests"):
            return httpx.Response(200, json={"value": [{"pullRequestId": 8}]})
        if path.endswith("/pullrequests/8"):
            return httpx.Response(
                200, json={"pullRequestId": 8, "status": "active", "reviewers": []}
            )
        return httpx.Response(200, json={"value": []})

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(handler))
    connections.clients["one"] = client
    try:
        if caller == "polling":
            await pull_requests(connections, connection, connection["scopes"][0], REPO)
            assert rows(workplace) == []
        else:
            result = await execute(
                connections,
                "factory",
                "ado_list_pull_request_policies",
                {
                    "connection_id": "one",
                    "project_id": PROJECT,
                    "repository_id": REPO,
                    "pull_request_id": 8,
                },
            )
            assert result == {"policies": []}
        assert len(policy_requests) == 1
        assert policy_requests[0].url.params["artifactId"].endswith(f"/{PROJECT}/8")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_provider_client_uses_configured_egress(setup, monkeypatch):
    connections, _, workplace = setup
    workplace.egress = SimpleNamespace(
        runtime_environment=lambda hosts: {"HTTPS_PROXY": "http://egress:3128"}
    )
    created = []
    original = httpx.AsyncClient

    def capture(**kwargs):
        created.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", capture)
    client = AzureDevOpsClient(connections, "one")
    try:
        assert created[0].get("proxy") == "http://egress:3128"
        assert created[0]["trust_env"] is False
    finally:
        await client.close()


def packet(command):
    value = command.encode()
    return f"{len(value) + 4:04x}".encode() + value + b"0000"


def test_git_ref_boundary():
    command = "a" * 40 + " " + "b" * 40 + " refs/heads/factory/fix\x00 report-status\n"
    assert receive_refs(packet(command), ["refs/heads/factory/*"]) == [
        "refs/heads/factory/fix"
    ]
    for denied in [
        command.replace("factory/fix", "main"),
        command.replace("b" * 40, "0" * 40),
        command.replace("refs/heads/", "refs/tags/"),
    ]:
        with pytest.raises(HTTPException):
            receive_refs(packet(denied), ["refs/heads/factory/*"])
    with pytest.raises(HTTPException):
        receive_refs(b"ffffshort", ["refs/heads/*"])


@pytest.mark.asyncio
async def test_git_broker_scopes_and_credentials(setup):
    connections, connection, _ = setup
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=b"git response",
            headers={"content-type": "application/x-git-upload-pack-advertisement"},
        )

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(upstream))
    connections.clients["one"] = client
    app = FastAPI()
    app.include_router(git_router(connections))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://host"
    ) as local:
        path = (
            f"/azure-devops/git/one/{PROJECT}/{REPO}/info/refs?service=git-upload-pack"
        )
        assert (await local.get(path)).status_code == 401
        response = await local.get(path, headers={"Authorization": "Bearer app-token"})
        assert response.content == b"git response"
        assert requests[0].headers["authorization"] != "Bearer app-token"
        assert "provider-secret" not in str(response.headers)
        assert (
            await local.get(
                path.replace(REPO, PROJECT),
                headers={"Authorization": "Bearer app-token"},
            )
        ).status_code == 403
        assert (
            await local.get(
                path.replace("upload-pack", "receive-pack"),
                headers={"Authorization": "Bearer app-token"},
            )
        ).status_code == 403
    await client.close()


@pytest.mark.asyncio
async def test_mcp_authentication_and_write_tool_visibility(setup, monkeypatch):
    connections, _, _ = setup
    endpoint = AzureDevOpsMCP(connections)
    monkeypatch.setattr(endpoint, "caller", lambda: "factory")
    tools = await endpoint.list_tools()
    assert tools and all(tool.annotations.read_only_hint for tool in tools)
    assert all(tool.input_schema.get("additionalProperties") is False for tool in tools)
    result = await endpoint.call_tool(
        "ado_complete_pull_request",
        {
            "connection_id": "one",
            "project_id": PROJECT,
            "repository_id": REPO,
            "operation_id": "merge",
            "pull_request_id": 8,
            "commit_id": "a" * 40,
        },
    )
    assert result.is_error
    sent = []

    async def send(value):
        sent.append(value)

    await endpoint({"type": "http", "headers": []}, None, send)
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_merge_never_bypasses_policy_and_validates_expected_head(setup):
    connections, connection, _ = setup
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "completed"})

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(handler))
    args = {
        "project_id": PROJECT,
        "repository_id": REPO,
        "pull_request_id": 8,
        "commit_id": "a" * 40,
    }
    await perform(
        client, connection, connection["scopes"][0], "ado_complete_pull_request", args
    )
    sent = json.loads(requests[0].content)
    assert sent["completionOptions"]["bypassPolicy"] is False
    assert sent["lastMergeSourceCommit"]["commitId"] == "a" * 40
    with pytest.raises(HTTPException):
        await perform(
            client,
            connection,
            connection["scopes"][0],
            "ado_update_pull_request",
            {**args, "changes": {"completionOptions": {"bypassPolicy": True}}},
        )
    await client.close()


@pytest.mark.asyncio
async def test_cross_project_work_items_and_unselected_builds_are_denied(setup):
    connections, connection, _ = setup

    def handler(request):
        if request.url.path.endswith("/projects/" + PROJECT):
            return httpx.Response(200, json={"name": "School"})
        if "/workitems/" in request.url.path:
            return httpx.Response(
                200, json={"fields": {"System.TeamProject": "Elsewhere"}}
            )
        return httpx.Response(200, json={"definition": {"id": 999}})

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(handler))
    for name, args in [
        (
            "ado_update_work_item",
            {"work_item_id": 8, "revision": 2, "changes": {"System.Title": "change"}},
        ),
        ("ado_cancel_build", {"build_id": 8}),
    ]:
        with pytest.raises(HTTPException) as caught:
            await perform(
                client,
                connection,
                connection["scopes"][0],
                name,
                {"project_id": PROJECT, **args},
            )
        assert caught.value.status_code == 403
    await client.close()


@pytest.fixture
def setup(tmp_path):
    app = SimpleNamespace(id="factory", memory_token="app-token", username="factory")
    workplace = SimpleNamespace(
        data_dir=tmp_path,
        store=Store(tmp_path / "state.db"),
        environ={},
        apps=SimpleNamespace(active=lambda: [app]),
        factory_config=None,
    )
    workplace.event_gateway = EventGateway(workplace)
    connections = AzureDevOpsConnections(workplace)
    body = ConnectionBody(
        label="Test",
        organization="school",
        token="provider-secret",
        enabled=True,
        app_ids=[app.id],
        scopes=[
            {
                "project": PROJECT,
                "repositories": [REPO],
                "boards": True,
                "pipelines": [7],
            }
        ],
    )
    value = connections.save("one", body)
    yield connections, connections.get(value["id"]), workplace
    workplace.store.close()


def rows(workplace):
    return workplace.store._db.execute(
        "SELECT * FROM factory_events ORDER BY id"
    ).fetchall()


def test_encryption_revision_and_scope(setup):
    connections, connection, workplace = setup
    assert "provider-secret" not in json.dumps(connections.records())
    assert "sealed_token" not in connections.public(connection)
    assert connections.credential(connection) == "provider-secret"
    assert (workplace.data_dir / "azure-devops.key").stat().st_mode & 0o777 == 0o600
    assert connections.app_for_token("Bearer app-token") == "factory"
    for app, project, repo in [
        ("another", PROJECT, REPO),
        ("factory", REPO, REPO),
        ("factory", PROJECT, PROJECT),
    ]:
        with pytest.raises(HTTPException) as caught:
            connections.authorize("one", app, project, repo)
        assert caught.value.status_code == 403
    with pytest.raises(HTTPException) as caught:
        connections.save("one", ConnectionBody(label="Stale", organization="school"))
    assert caught.value.status_code == 409
    with pytest.raises(HTTPException):
        connections.authorize(
            "one", "factory", PROJECT, REPO, "ado_complete_pull_request"
        )


@pytest.mark.asyncio
async def test_admin_api_and_authenticated_mcp_transport(workplace_server):
    from conftest import signed_in

    workplace, url = workplace_server
    with httpx.Client(base_url=url) as anonymous:
        assert (
            anonymous.get("/workplace/api/azure-devops/connections").status_code == 401
        )
    with signed_in(workplace, url) as admin_client:
        created = admin_client.post(
            "/workplace/api/azure-devops/connections",
            json={
                "label": "ADO",
                "organization": "school",
                "token": "not-a-live-credential",
            },
        )
        assert created.status_code == 200
        record = created.json()
        assert record["enabled"] is False and "sealed_token" not in record
        stale = admin_client.put(
            "/workplace/api/azure-devops/connections/" + record["id"],
            json={"label": "ADO", "organization": "school", "revision": 0},
        )
        assert stale.status_code == 409
    app = workplace.apps.by_username("acme-factory")
    async with httpx.AsyncClient(base_url=url) as client:
        response = await client.post(
            "/azure-devops/mcp",
            headers={
                "Authorization": "Bearer " + app.memory_token,
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert response.status_code == 200
        assert "azure-devops" in response.text


@pytest.mark.asyncio
async def test_repository_discovery_does_not_request_pipeline_permissions(
    setup, monkeypatch
):
    from omniagents_workplace.azure_devops import api

    connections, _, _ = setup
    requested = []

    async def respond(request):
        requested.append(request.url.path)
        if "/build/" in request.url.path:
            return httpx.Response(401)
        if request.url.path.endswith("/repositories"):
            return httpx.Response(
                200, json={"value": [{"id": REPO, "name": "Application"}]}
            )
        return httpx.Response(200, json={"id": PROJECT})

    provider = AzureDevOpsClient(connections, "one", httpx.MockTransport(respond))
    monkeypatch.setattr(connections, "client", lambda value: provider)
    monkeypatch.setattr(api, "admin", lambda request: None)
    app = FastAPI()
    app.include_router(api.router(connections))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/workplace/api/azure-devops/connections/one/available?project={PROJECT}"
        )
        assert response.status_code == 200
        assert response.json()["repositories"] == [{"id": REPO, "name": "Application"}]
        assert not any("/build/" in path for path in requested)
        pipelines = await client.get(
            f"/workplace/api/azure-devops/connections/one/available?project={PROJECT}&resource=pipelines"
        )
        assert pipelines.status_code == 401
        assert "pipeline" in pipelines.json()["detail"].lower()
        assert "expired" not in pipelines.json()["detail"].lower()
    await provider.close()


def test_snapshot_baseline_transition_recurrence_and_restart(setup):
    connections, connection, workplace = setup
    context = {"project_id": PROJECT, "repository_id": REPO, "pull_request_id": "8"}

    def changed(state):
        observe(
            connections,
            connection,
            "prs",
            [("pr:8", "pull_request_updated", {"status": state}, context)],
        )

    changed("active")
    assert not rows(workplace)
    changed("abandoned")
    changed("abandoned")
    restarted = AzureDevOpsConnections(workplace)
    observe(
        restarted,
        connection,
        "prs",
        [("pr:8", "pull_request_updated", {"status": "active"}, context)],
    )
    events = [json.loads(row[4])["event"] for row in rows(workplace)]
    assert events == ["pull_request_abandoned", "pull_request_reactivated"]
    assert len({row[3] for row in rows(workplace)}) == 2


def test_observation_outbox_and_cursor_are_atomic(setup, monkeypatch):
    connections, connection, workplace = setup
    source = "boards"
    observe(connections, connection, source, [], cursor="baseline")

    def fail(*args):
        raise RuntimeError("injected queue failure")

    monkeypatch.setattr(workplace.event_gateway, "enqueue", fail)
    with pytest.raises(RuntimeError):
        observe(
            connections,
            connection,
            source,
            [("item:3", "work_item_updated", {"rev": 2}, {"project_id": PROJECT})],
            cursor="next",
        )
    assert not workplace.store.docs("ado.observation")
    assert (
        workplace.store.meta("ado.source." + source_key(connection, source))["cursor"]
        == "baseline"
    )


def test_stale_revision_cannot_enqueue(setup):
    connections, connection, workplace = setup
    updated = {
        key: value
        for key, value in connection.items()
        if key in ConnectionBody.model_fields
    }
    updated["enabled"] = False
    connections.save("one", ConnectionBody(**updated))
    with pytest.raises(HTTPException):
        observe(connections, connection, "source", [], cursor="bad")
    assert not rows(workplace)


@pytest.mark.asyncio
async def test_pagination_auth_and_no_redirect_leak(setup):
    connections, connection, _ = setup
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("redirect"):
            return httpx.Response(302, headers={"Location": "https://attacker.invalid"})
        if request.url.params.get("continuationToken"):
            return httpx.Response(200, json={"value": [2]})
        return httpx.Response(
            200, json={"value": [1]}, headers={"x-ms-continuationtoken": "next"}
        )

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(handler))
    assert await client.all("/_apis/projects") == [1, 2]
    assert seen[0].headers["authorization"].startswith("Basic ")
    with pytest.raises(HTTPException):
        await client.json("GET", "/redirect")
    assert len(seen) == 3
    assert all(request.url.host == "dev.azure.com" for request in seen)
    await client.close()


@pytest.mark.asyncio
async def test_successful_retry_after_defers_next_request(setup):
    connections, _, _ = setup
    client = AzureDevOpsClient(
        connections,
        "one",
        httpx.MockTransport(
            lambda request: httpx.Response(200, json={}, headers={"Retry-After": "30"})
        ),
    )
    await client.json("GET", "/_apis/projects")
    with pytest.raises(HTTPException) as caught:
        await client.json("GET", "/_apis/projects")
    assert caught.value.status_code == 429
    await client.close()


@pytest.mark.asyncio
async def test_old_active_pr_child_changes_detected_without_parent_update(setup):
    connections, connection, workplace = setup
    threads = []

    def handler(request):
        path = request.url.path
        if path.endswith("/pullrequests"):
            return httpx.Response(200, json={"value": [{"pullRequestId": 8}]})
        if path.endswith("/pullrequests/8"):
            return httpx.Response(
                200, json={"pullRequestId": 8, "status": "active", "reviewers": []}
            )
        return httpx.Response(
            200, json={"value": threads if path.endswith("/threads") else []}
        )

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(handler))
    connections.clients["one"] = client
    await pull_requests(connections, connection, connection["scopes"][0], REPO)
    threads.append({"id": 1, "comments": [{"content": "Review this"}]})
    await pull_requests(connections, connection, connection["scopes"][0], REPO)
    assert [json.loads(row[4])["event"] for row in rows(workplace)] == [
        "pull_request_thread_changed"
    ]
    assert json.loads(rows(workplace)[0][4])["threads"] == threads
    await client.close()


@pytest.mark.asyncio
async def test_build_watch_survives_restart(setup):
    connections, connection, workplace = setup
    status = "inProgress"
    discover = True

    def handler(request):
        path = request.url.path
        if path.endswith("/builds"):
            return httpx.Response(200, json={"value": [{"id": 9}] if discover else []})
        if path.endswith("/timeline"):
            return httpx.Response(200, json={"records": []})
        return httpx.Response(
            200, json={"id": 9, "status": status, "definition": {"id": 7}}
        )

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(handler))
    connections.clients["one"] = client
    await builds(connections, connection, connection["scopes"][0])
    discover = False
    status = "completed"
    restarted = AzureDevOpsConnections(workplace)
    restarted.clients["one"] = client
    await builds(restarted, connection, connection["scopes"][0])
    assert json.loads(rows(workplace)[0][4])["event"] == "build_completed"
    await client.close()


@pytest.mark.asyncio
async def test_write_permission_and_idempotency(setup):
    connections, connection, workplace = setup
    args = {
        "connection_id": "one",
        "project_id": PROJECT,
        "repository_id": REPO,
        "pull_request_id": 8,
        "text": "Hello",
        "operation_id": "comment-1",
    }
    with pytest.raises(HTTPException) as caught:
        await execute(connections, "factory", "ado_comment_pull_request", args)
    assert caught.value.status_code == 403
    connection["write_tools"] = ["ado_comment_pull_request"]
    workplace.store.put("ado.connection", "one", connection)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": 1})

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(handler))
    connections.clients["one"] = client
    assert await execute(connections, "factory", "ado_comment_pull_request", args) == {
        "id": 1
    }
    assert await execute(connections, "factory", "ado_comment_pull_request", args) == {
        "id": 1
    }
    assert len(calls) == 1
    with pytest.raises(HTTPException):
        await execute(
            connections,
            "factory",
            "ado_comment_pull_request",
            {**args, "text": "Different"},
        )
    await client.close()


@pytest.mark.parametrize("name", list(TOOLS))
def test_tool_schema_is_strict(name):
    assert model_for(name).model_json_schema()["additionalProperties"] is False


def test_new_closed_pull_request_uses_completion_event(setup):
    connections, connection, workplace = setup
    observe(connections, connection, "prs", [])
    observe(
        connections,
        connection,
        "prs",
        [
            (
                "pr:10",
                "pull_request_updated",
                {"status": "completed"},
                {"project_id": PROJECT, "repository_id": REPO, "pull_request_id": "10"},
            )
        ],
    )
    deliveries = rows(workplace)
    assert len(deliveries) == 1
    assert json.loads(deliveries[0][4])["event"] == "pull_request_completed"


@pytest.mark.asyncio
async def test_queued_build_without_timeline_does_not_block_polling(setup):
    connections, connection, workplace = setup

    def upstream(request):
        path = request.url.path
        if path.endswith("/timeline"):
            return httpx.Response(404)
        if path.endswith("/builds"):
            return httpx.Response(200, json={"value": [{"id": 21}]})
        return httpx.Response(
            200, json={"id": 21, "status": "notStarted", "definition": {"id": 7}}
        )

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(upstream))
    connections.clients["one"] = client
    try:
        await builds(connections, connection, connection["scopes"][0])
        state = workplace.store.meta(
            "ado.source." + source_key(connection, "builds:" + PROJECT)
        )
        assert state["initialized"] is True
        assert not rows(workplace)
    finally:
        await client.close()
