import json
from pathlib import Path
from xml.etree import ElementTree

import httpx
import pytest

from omniagents_workplace.azure_devops.client import AzureDevOpsClient
from omniagents_workplace.azure_devops.mcp import AzureDevOpsMCP
from test_azure_devops import PROJECT, REPO, setup


@pytest.mark.asyncio
@pytest.mark.parametrize("question_index", range(10))
async def test_read_only_mcp_evaluations(setup, monkeypatch, question_index):
    connections, _, workplace = setup
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/azure_devops_evaluation.json").read_text()
    )
    requests = []

    def upstream(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/pullrequests"):
            value = {"value": fixture["pull_requests"]}
        elif "/pullrequests/" in path:
            tail = path.split("/pullrequests/", 1)[1].split("/")
            if len(tail) == 1:
                value = next(
                    item
                    for item in fixture["pull_requests"]
                    if item["pullRequestId"] == int(tail[0])
                )
            elif tail[-1] == "changes":
                value = {"changeEntries": fixture["changes"]}
            else:
                value = {"value": fixture[tail[-1]]}
        elif path.endswith("/policy/evaluations"):
            value = {"value": fixture["policies"]}
        elif path.endswith("/items"):
            value = fixture["file"]
        elif path.endswith("/commits"):
            value = {"value": fixture["commits"]}
        elif path.endswith("/build/builds"):
            value = {"value": fixture["builds"]}
        elif "/build/builds/" in path:
            tail = path.split("/build/builds/", 1)[1].split("/")
            if len(tail) == 1:
                value = next(
                    item for item in fixture["builds"] if item["id"] == int(tail[0])
                )
            elif "logs" in tail:
                return httpx.Response(200, text=fixture["log"])
            elif tail[-1] == "timeline":
                value = fixture["timeline"]
            else:
                value = {"value": fixture["artifacts"]}
        elif path.endswith("/wit/wiql"):
            value = {
                "workItems": [{"id": item["id"]} for item in fixture["work_items"]]
            }
        elif path.endswith("/projects/" + PROJECT):
            value = {"name": fixture["project_name"]}
        elif "/workitems/" in path:
            tail = path.split("/workitems/", 1)[1].split("/")
            value = (
                fixture["work_items"][0]
                if len(tail) == 1
                else {"value": fixture[tail[-1]]}
            )
        else:
            raise AssertionError("Unexpected fixture route: " + path)
        return httpx.Response(200, json=value)

    client = AzureDevOpsClient(connections, "one", httpx.MockTransport(upstream))
    connections.clients["one"] = client
    endpoint = AzureDevOpsMCP(connections)
    monkeypatch.setattr(endpoint, "caller", lambda: "factory")
    tools = {tool.name: tool for tool in await endpoint.list_tools()}
    calls = []

    async def call(name, **arguments):
        assert tools[name].annotations.read_only_hint
        calls.append(name)
        if name != "ado_list_connections":
            arguments = {"connection_id": "one", "project_id": PROJECT, **arguments}
        result = await endpoint.call_tool(name, arguments)
        assert not result.is_error, result.content
        return json.loads(result.content[0].text)

    async def find_pull_request(title):
        listing = await call("ado_list_pull_requests", repository_id=REPO, limit=10)
        item = next(item for item in listing["value"] if title in item["title"])
        return await call(
            "ado_get_pull_request",
            repository_id=REPO,
            pull_request_id=item["pullRequestId"],
        )

    async def find_build():
        listing = await call("ado_list_builds", limit=10)
        item = next(
            item
            for item in listing["value"]
            if item["sourceBranch"] == "refs/heads/factory/recovery"
            and item["result"] == "failed"
        )
        return await call("ado_get_build", build_id=item["id"])

    async def find_work_item():
        listing = await call(
            "ado_list_work_items", filters={"System.State": "Active"}, limit=10
        )
        return await call(
            "ado_get_work_item", work_item_id=listing["workItems"][0]["id"]
        )

    try:
        if question_index in {0, 1, 2, 3, 9}:
            detail = await find_pull_request("Preserve intake")
            pull_request = detail["pull_request"]
            arguments = {
                "repository_id": REPO,
                "pull_request_id": pull_request["pullRequestId"],
            }
            if question_index == 0:
                thread = next(
                    thread
                    for thread in detail["threads"]
                    if thread["status"] == "active"
                )
                answer = thread["comments"][0]["author"]["displayName"]
            elif question_index == 1:
                assert detail["statuses"][0]["state"] == "failed"
                policies = await call("ado_list_pull_request_policies", **arguments)
                answer = next(
                    policy
                    for policy in policies["policies"]
                    if policy["status"] == "rejected"
                )["configuration"]["type"]["displayName"]
            elif question_index == 2:
                iteration = max(item["id"] for item in detail["iterations"])
                changes = await call(
                    "ado_get_pull_request_changes",
                    **arguments,
                    iteration_id=iteration,
                    limit=10
                )
                answer = changes["changeEntries"][0]["item"]["path"]
            elif question_index == 3:
                file = await call(
                    "ado_get_file",
                    repository_id=REPO,
                    path="/src/queue.py",
                    ref=pull_request["lastMergeSourceCommit"]["commitId"],
                )
                answer = file["content"].strip().split("=")[1]
            else:
                commits = await call(
                    "ado_list_commits",
                    repository_id=REPO,
                    ref=pull_request["sourceRefName"],
                    limit=10,
                )
                answer = next(
                    item
                    for item in commits["value"]
                    if item["commitId"]
                    == pull_request["lastMergeSourceCommit"]["commitId"]
                )["comment"]
        elif question_index in {4, 5}:
            detail = await find_build()
            build_id = detail["build"]["id"]
            if question_index == 4:
                job = next(
                    item
                    for item in detail["timeline"]["records"]
                    if item["result"] == "failed"
                )
                log = await call(
                    "ado_get_build_log", build_id=build_id, log_id=job["log"]["id"]
                )
                answer = log["text"].strip().split("AssertionError: ")[1]
            else:
                artifacts = await call("ado_list_build_artifacts", build_id=build_id)
                answer = artifacts["artifacts"][0]["name"]
        elif question_index in {6, 7}:
            detail = await find_work_item()
            if question_index == 6:
                revisions = detail["revisions"]
                assert revisions[0]["fields"]["System.State"] == "New"
                answer = str(
                    next(
                        item
                        for item in revisions
                        if item["fields"]["System.State"] == "Active"
                    )["rev"]
                )
            else:
                pull_request_id = int(
                    detail["comments"][0]["text"].split("pull request ")[1].rstrip(".")
                )
                pull_request = await call(
                    "ado_get_pull_request",
                    repository_id=REPO,
                    pull_request_id=pull_request_id,
                )
                answer = pull_request["pull_request"]["createdBy"]["displayName"]
        else:
            detail = await find_pull_request("account totals")
            answer = detail["pull_request"]["targetRefName"]
        evaluation_path = (
            Path(__file__).parents[2] / "omniagents/docs/azure-devops-evaluation.xml"
        )
        questions = ElementTree.parse(evaluation_path).getroot().findall("qa_pair")
        assert len(questions) == 10
        assert answer == questions[question_index].findtext("answer")
        assert len(calls) >= 2
        assert not list(workplace.store.docs("ado.operation"))
        assert all(
            request.method == "GET" or request.url.path.endswith("/wiql")
            for request in requests
        )
    finally:
        await client.close()
