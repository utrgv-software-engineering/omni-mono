from pathlib import Path
import pytest

from omniagents.core.automations import Automation
from omniagents.integrations import azure_devops as ado
from omniagents.integrations.links import PullRequestLinks, opened_pull_requests
from omniagents.integrations.store import EventStore


def payload(**changes):
    return {
        **dict(
            connection_id="connection",
            scope_epoch="epoch",
            organization="org",
            project_id="project",
            repository_id="repo",
            pull_request_id="8",
            resource_id="threads:8",
            event="pull_request_thread_changed",
        ),
        **changes,
    }


def comment(text, identity="human", number=1):
    return {"id": number, "content": text, "commentType": 1, "author": {"id": identity}}


def test_normalize_delta_and_exact_target():
    old = [{"id": 2, "comments": [comment("old")]}]
    current = [{"id": 2, "comments": [comment("old"), comment("new", number=2)]}]
    event = ado.normalize(
        payload(
            previous=old,
            resource=current,
            pull_request={
                "sourceRefName": "refs/heads/master",
                "targetRefName": "refs/heads/dev",
            },
        ),
        "delta",
    )
    assert event.payload["changed_human_comments"] == [
        {"thread_id": 2, **comment("new", number=2)}
    ]
    assert event.filters["base_branches"] == ["refs/heads/dev"]


def test_discussion_admission_requires_addressing_and_suppresses_own(tmp_path):
    store = EventStore(tmp_path / "events.db")
    automation = Automation(name="discussion", path=Path(), instruction="Discuss")
    config = ado.AzureDevOpsConfig(markers=("[factory]",), identity_ids=("bot",))

    def event(text, identity="human", delivery="one"):
        return ado.normalize(
            payload(
                previous=[], resource=[{"id": 2, "comments": [comment(text, identity)]}]
            ),
            delivery,
            config=config,
        )

    assert not ado.admit(event("unrelated"), automation, store)
    assert not ado.admit(event("[factory]", "bot"), automation, store)
    assert ado.admit(event("[factory] help", delivery="two"), automation, store)
    assert ado.admit(event("later clarification", delivery="three"), automation, store)
    store.close()


def test_review_head_and_draft_duplicate_admission(tmp_path):
    store = EventStore(tmp_path / "events.db")
    automation = Automation(
        name="review",
        path=Path(),
        instruction="Review",
        triggers=[
            {
                "provider": "azure-devops",
                "event": "pull_request_updated",
                "filter": {"review_eligible": ["true"]},
            }
        ],
    )

    def event(draft=False, head="a"):
        return ado.normalize(
            payload(
                event="pull_request_updated",
                pull_request={
                    "status": "active",
                    "isDraft": draft,
                    "lastMergeSourceCommit": {"commitId": head},
                },
            ),
            "review",
        )

    assert not ado.admit(event(draft=True), automation, store)
    assert ado.admit(event(), automation, store)
    assert not ado.admit(event(), automation, store)
    assert ado.admit(event(head="b"), automation, store)
    store.close()


def test_route_context_is_carried_without_identity_reuse(tmp_path):
    store = EventStore(tmp_path / "events.db")
    event = ado.normalize(payload(), "first")
    event_id = store.record(event)
    store.remember_thread(event.provider, event.thread_key, "generalist", "old-session")
    dispatch = store.start_dispatch(
        event_id,
        "discussion",
        "generalist",
        "old-session",
        prompt="Work on branch factory/fix; approval is pending",
    )
    store.finish_dispatch(
        dispatch, "completed", output="PR 8; branch factory/fix; approval pending"
    )
    assert store.thread_session(event.provider, event.thread_key, "dispatcher") is None
    context = store.thread_context(event.provider, event.thread_key, "dispatcher")
    assert (
        "generalist" in context
        and "factory/fix" in context
        and "approval pending" in context
    )
    store.close()


def test_azure_links_are_provider_qualified_and_abandonment_is_not_closure(tmp_path):
    store = EventStore(tmp_path / "events.db")
    links = PullRequestLinks(store, lambda *args: [], lambda *args: [])
    event = ado.normalize(payload(), "worked")
    dispatch = store.start_dispatch(
        store.record(event), "review", "code_reviewer", "session"
    )
    links.record(dispatch, "code_reviewer", "session", event)
    assert store.run_pull_requests(dispatch) == [
        {
            "repository": "azure-devops:connection:org:project:repo",
            "number": 8,
            "relation": "worked",
        }
    ]
    store.record(ado.normalize(payload(event="pull_request_abandoned"), "abandoned"))
    assert not store.conversations()[0]["closed"]
    store.record(ado.normalize(payload(event="pull_request_completed"), "completed"))
    assert store.conversations()[0]["closed"]
    store.close()


def test_azure_created_pr_output_is_tracked():
    history = [
        {
            "type": "function_call",
            "name": "azure-devops/ado_create_pull_request",
            "call_id": "one",
            "arguments": '{"connection_id":"connection","project_id":"project","repository_id":"repo"}',
        },
        {
            "type": "function_call_output",
            "call_id": "one",
            "output": {
                "pullRequestId": 8,
                "repository": {"id": "repo"},
                "url": "https://dev.azure.com/org/project/_apis/git/repositories/repo/pullRequests/8",
            },
        },
    ]
    assert opened_pull_requests(history) == [
        ("azure-devops:connection:org:project:repo", 8)
    ]


def test_comment_edits_provenance_and_status_only_changes():
    old = [{"id": 2, "status": 1, "comments": [comment("old")]}]
    status = [{"id": 2, "status": 2, "comments": [comment("old")]}]
    assert (
        ado.normalize(payload(previous=old, resource=status), "status").payload[
            "changed_human_comments"
        ]
        == []
    )
    edited = [{"id": 2, "comments": [comment("edited")]}]
    assert (
        ado.normalize(payload(previous=old, resource=edited), "edit").payload[
            "changed_human_comments"
        ][0]["content"]
        == "edited"
    )
    assert (
        ado.normalize(
            payload(previous=old, resource=edited, factory_comment_ids=[[2, 1]]), "own"
        ).payload["changed_human_comments"]
        == []
    )


def test_admission_and_dispatch_intents_are_atomic_and_survive_restart(tmp_path):
    path = tmp_path / "events.db"
    store = EventStore(path)
    config = ado.AzureDevOpsConfig(markers=("[factory]",))
    event = ado.normalize(
        payload(
            previous=[], resource=[{"id": 2, "comments": [comment("[factory] help")]}]
        ),
        "atomic",
        config=config,
    )
    event_id = store.record(event)
    automation = Automation(name="discussion", path=Path(), instruction="Discuss")
    broken = Automation(
        name="broken",
        path=Path(),
        instruction="Broken",
        triggers=[{"invalid": object()}],
    )
    with pytest.raises(TypeError):
        store.prepare_dispatches(
            event_id,
            [automation, broken],
            admission=lambda route: ado.admit(event, route, store, transaction=False),
        )
    assert not store.pending_dispatches()
    plain = ado.normalize(
        payload(previous=[], resource=[{"id": 2, "comments": [comment("unrelated")]}]),
        "plain",
    )
    assert not ado.admit(plain, automation, store)
    assert store.prepare_dispatches(
        event_id,
        [automation],
        admission=lambda route: ado.admit(event, route, store, transaction=False),
    ) == [automation]
    store.close()
    store = EventStore(path)
    assert len(store.pending_dispatches()) == 1
    assert ado.admit(plain, automation, store)
    store.close()


def test_tracked_factory_pr_ownership_admits_human_reply(tmp_path):
    store = EventStore(tmp_path / "events.db")
    event = ado.normalize(
        payload(previous=[], resource=[{"id": 2, "comments": [comment("please fix")]}]),
        "owner",
    )
    dispatch = store.start_dispatch(
        store.record(event), "implementation", "dispatcher", "session"
    )
    store.link_pull_request(
        dispatch, "azure-devops:connection:org:project:repo", 8, "opened"
    )
    assert ado.admit(
        event, Automation(name="discussion", path=Path(), instruction="Discuss"), store
    )
    store.close()


@pytest.mark.asyncio
async def test_route_change_serializes_source_work_and_transfers_completed_context(
    tmp_path, monkeypatch
):
    import asyncio
    from omniagents.integrations.intake import EventIntake, RunResult
    from omniagents.core.runtime import factory_stop
    from omniagents.integrations import self_improvement

    monkeypatch.setattr(factory_stop, "current", lambda: None)

    async def guarded(run, automation, *args):
        return await run(*args)

    monkeypatch.setattr(self_improvement, "guarded_run", guarded)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def run(agent, session, prompt, progress):
        calls.append((agent, session, prompt))
        if agent == "generalist":
            started.set()
            await release.wait()
        return RunResult(output="Work branch factory/fix; PR 8; approval pending")

    store = EventStore(tmp_path / "events.db")
    intake = EventIntake(
        project_root=tmp_path, entrypoint="generalist", store=store, run=run
    )
    event = ado.normalize(payload(), "first")
    automation = Automation(
        name="discussion", path=Path(), instruction="Discuss", conversation="thread"
    )
    first = asyncio.create_task(
        intake._dispatch(store.record(event), event, automation)
    )
    await started.wait()
    second_event = ado.normalize(payload(), "second")
    second = asyncio.create_task(
        intake._dispatch(
            store.record(second_event), second_event, automation, agent="dispatcher"
        )
    )
    await asyncio.sleep(0)
    assert len(calls) == 1
    release.set()
    await asyncio.gather(first, second)
    assert calls[1][0] == "dispatcher" and calls[1][1] != calls[0][1]
    assert "factory/fix" in calls[1][2] and "approval pending" in calls[1][2]
    store.close()
