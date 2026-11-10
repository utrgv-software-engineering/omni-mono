import pytest
from pydantic import ValidationError
from omniagents_workplace.azure_devops.tools import model_for, perform

PROJECT = "11111111-1111-1111-1111-111111111111"
REPO = "22222222-2222-2222-2222-222222222222"


def arguments(**changes):
    return dict(
        connection_id="one",
        project_id=PROJECT,
        repository_id=REPO,
        operation_id="review:8:head:file:4",
        pull_request_id=8,
        text="Advisory finding",
        file_path="/src/app.py",
        line=4,
        iteration_id=2,
        change_tracking_id=7,
        **changes
    )


@pytest.mark.asyncio
async def test_inline_review_schema_and_native_anchors():
    args = (
        model_for("ado_comment_pull_request").model_validate(arguments()).model_dump()
    )
    calls = []

    class Client:
        async def json(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return {"id": 1}

    await perform(Client(), {}, {}, "ado_comment_pull_request", args)
    body = calls[0][2]["json_body"]
    assert body["threadContext"] == {
        "filePath": "/src/app.py",
        "rightFileStart": {"line": 4, "offset": 1},
        "rightFileEnd": {"line": 4, "offset": 1},
    }
    assert body["pullRequestThreadContext"] == {
        "changeTrackingId": 7,
        "iterationContext": {
            "firstComparingIteration": 0,
            "secondComparingIteration": 2,
        },
    }


def test_inline_review_rejects_incomplete_and_invalid_anchors():
    model = model_for("ado_comment_pull_request")
    with pytest.raises(ValidationError):
        model.model_validate({**arguments(), "line": 0})
    with pytest.raises(ValidationError):
        model.model_validate({**arguments(), "file_path": None})


@pytest.mark.asyncio
async def test_left_side_and_base_iteration_and_unanchored_summary():
    model = model_for("ado_comment_pull_request")
    calls = []

    class Client:
        async def json(self, method, path, **kwargs):
            calls.append(kwargs["json_body"])
            return {"id": 1}

    args = model.model_validate(
        {**arguments(), "side": "left", "end_line": 6, "comparing_iteration_id": 0}
    ).model_dump()
    await perform(Client(), {}, {}, "ado_comment_pull_request", args)
    assert calls[-1]["threadContext"]["leftFileEnd"]["line"] == 6
    assert (
        calls[-1]["pullRequestThreadContext"]["iterationContext"][
            "firstComparingIteration"
        ]
        == 0
    )
    args = model.model_validate(
        {
            key: value
            for key, value in arguments().items()
            if key not in {"file_path", "line", "iteration_id", "change_tracking_id"}
        }
    ).model_dump()
    await perform(Client(), {}, {}, "ado_comment_pull_request", args)
    assert "threadContext" not in calls[-1]


def test_polling_provenance_is_scoped_to_successful_factory_writes():
    from types import SimpleNamespace
    from omniagents_workplace.azure_devops.polling import factory_comments

    resource = {"project_id": PROJECT, "repository_id": REPO, "pull_request_id": 8}
    operations = [
        (
            "one:factory:comment",
            {
                "status": "succeeded",
                "tool": "ado_comment_pull_request",
                "resource": resource,
                "result": {"id": 3, "comments": [{"id": 1}]},
            },
        ),
        (
            "one:factory:reply",
            {
                "status": "succeeded",
                "tool": "ado_reply_pull_request_thread",
                "resource": {**resource, "thread_id": 3},
                "result": {"id": 2},
            },
        ),
        (
            "other:factory:comment",
            {
                "status": "succeeded",
                "tool": "ado_comment_pull_request",
                "resource": resource,
                "result": {"id": 4, "comments": [{"id": 1}]},
            },
        ),
        (
            "one:factory:uncertain",
            {
                "status": "uncertain",
                "tool": "ado_comment_pull_request",
                "resource": resource,
                "result": {"id": 5, "comments": [{"id": 1}]},
            },
        ),
    ]
    connections = SimpleNamespace(
        w=SimpleNamespace(store=SimpleNamespace(docs=lambda kind: operations))
    )
    assert factory_comments(connections, {"id": "one"}, PROJECT, REPO, "8") == [
        [3, 1],
        [3, 2],
    ]
    assert factory_comments(connections, {"id": "one"}, PROJECT, REPO, "9") == []


@pytest.mark.asyncio
async def test_review_changes_use_the_requested_comparing_iteration():
    args = (
        model_for("ado_get_pull_request_changes")
        .model_validate(
            {
                "connection_id": "one",
                "project_id": PROJECT,
                "repository_id": REPO,
                "pull_request_id": 8,
                "iteration_id": 3,
                "comparing_iteration_id": 2,
            }
        )
        .model_dump()
    )
    calls = []

    class Client:
        async def json(self, method, path, **kwargs):
            calls.append((path, kwargs))
            return {"changeEntries": []}

    await perform(Client(), {}, {}, "ado_get_pull_request_changes", args)
    assert calls[0][0].endswith("/iterations/3/changes")
    assert calls[0][1]["params"]["$compareTo"] == 2
