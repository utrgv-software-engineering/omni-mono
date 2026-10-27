import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omniagents.integrations import EventIntake, EventStore
from omniagents.integrations.intake import RunResult
from omniagents.integrations.github import (
    GITHUB_EVENTS_PATH,
    GitHubWebhookConfig,
    github_signature,
    normalize_github_event,
    verify_github_request,
)
from omniagents.integrations.matching import trigger_matches
from omniagents.integrations.server import _mount_github_route, parse_integrations

SECRET = "webhook-secret"
REPO = {
    "full_name": "acme/api-service",
    "name": "api-service",
    "owner": {"login": "acme"},
}


def _user(login):
    return {"login": login, "id": 1, "type": "User"}


def _issue(number=12, labels=("factory:Acme",), pull=False, body="", assignees=()):
    issue = {
        "number": number,
        "title": "Add pagination to GET /tasks",
        "html_url": f"https://github.com/acme/api-service/issues/{number}",
        "state": "open",
        "user": _user("priya"),
        "labels": [{"name": n} for n in labels],
        "assignees": [_user(a) for a in assignees],
        "body": body,
    }
    if pull:
        issue["pull_request"] = {
            "url": "https://api.github.com/repos/acme/api-service/pulls/12"
        }
    return issue


def _pull(number=33, labels=(), base="main", body="", merged=False):
    return {
        **_issue(number, labels, body=body),
        "html_url": f"https://github.com/acme/api-service/pull/{number}",
        "head": {"ref": "dana/pagination"},
        "base": {"ref": base},
        "draft": False,
        "merged": merged,
    }


def _events(event, payload, account="acme-factory"):
    return {
        e.event: e
        for e in normalize_github_event(
            event, "d-1", {"repository": REPO, **payload}, account
        )
    }


def test_signatures_are_githubs():
    body = b'{"zen": "Keep it logically awesome."}'
    signature = github_signature(SECRET, body)
    assert signature.startswith("sha256=") and len(signature) == 71
    assert verify_github_request(SECRET, {"x-hub-signature-256": signature}, body)
    assert not verify_github_request(
        SECRET, {"x-hub-signature-256": github_signature("other", body)}, body
    )
    assert not verify_github_request("", {"x-hub-signature-256": signature}, body)


def test_a_mention_in_a_comment_is_the_issue_or_pull_request_mentioning_the_factory():
    comment = {
        "body": "@acme-factory can you take this?",
        "user": _user("priya"),
        "html_url": "u",
        "id": 5,
    }
    events = _events(
        "issue_comment",
        {
            "action": "created",
            "issue": _issue(),
            "comment": comment,
            "sender": _user("priya"),
        },
    )
    mention = events["issue_mentioned"]
    assert mention.thread_key == "acme/api-service#12"
    assert mention.filters == {
        "repos": ["acme/api-service"],
        "labels": ["factory:Acme"],
        "mentioned": ["acme-factory"],
    }
    assert mention.payload["comment"]["body"] == "@acme-factory can you take this?"
    assert mention.payload["issue"]["number"] == 12
    on_pull = _events(
        "issue_comment",
        {
            "action": "created",
            "issue": _issue(pull=True),
            "comment": comment,
            "sender": _user("priya"),
        },
    )
    assert set(on_pull) == {"pull_request_mentioned"}
    # The factory mentioning itself is not a request.
    own = _events(
        "issue_comment",
        {
            "action": "created",
            "issue": _issue(),
            "sender": _user("acme-factory"),
            "comment": {**comment, "user": _user("acme-factory")},
        },
    )
    assert own == {}
    # Mentions in code are not mentions.
    code = _events(
        "issue_comment",
        {
            "action": "created",
            "issue": _issue(),
            "sender": _user("priya"),
            "comment": {**comment, "body": "run `@acme-factory` later"},
        },
    )
    assert code == {}


def test_assignments_labels_and_pull_requests():
    assigned = _events(
        "issues",
        {
            "action": "assigned",
            "issue": _issue(assignees=["acme-factory"]),
            "assignee": _user("acme-factory"),
            "sender": _user("priya"),
        },
    )
    assert assigned["issue_assigned"].filters["assignees"] == ["acme-factory"]
    labeled = _events(
        "issues",
        {
            "action": "labeled",
            "issue": _issue(labels=("bug", "needs-repro")),
            "label": {"name": "needs-repro"},
            "sender": _user("priya"),
        },
    )
    # A *_labeled event's labels are the one added.
    assert labeled["issue_labeled"].filters["labels"] == ["needs-repro"]
    opened = _events(
        "pull_request",
        {
            "action": "opened",
            "pull_request": _pull(body="@acme-factory please look"),
            "sender": _user("dana"),
        },
    )
    assert set(opened) == {"pull_request_opened", "pull_request_mentioned"}
    assert opened["pull_request_opened"].filters["base_branches"] == ["main"]
    assert (
        opened["pull_request_opened"].payload["pull_request"]["head"]
        == "dana/pagination"
    )
    assert len({e.delivery_id for e in opened.values()}) == 2
    merged = _events(
        "pull_request",
        {
            "action": "closed",
            "pull_request": _pull(merged=True),
            "sender": _user("priya"),
        },
    )
    closed = _events(
        "pull_request",
        {"action": "closed", "pull_request": _pull(), "sender": _user("priya")},
    )
    assert set(merged) == {"pull_request_merged"} and set(closed) == {
        "pull_request_closed"
    }
    ready = _events(
        "pull_request",
        {
            "action": "ready_for_review",
            "pull_request": _pull(),
            "sender": _user("dana"),
        },
    )
    synced = _events(
        "pull_request",
        {"action": "synchronize", "pull_request": _pull(), "sender": _user("dana")},
    )
    requested = _events(
        "pull_request",
        {
            "action": "review_requested",
            "pull_request": _pull(),
            "requested_reviewer": _user("acme-factory"),
            "sender": _user("dana"),
        },
    )
    assert set(ready) == {"pull_request_ready"} and set(synced) == {
        "pull_request_synchronized"
    }
    assert requested["pull_request_review_requested"].filters["reviewers"] == [
        "acme-factory"
    ]


def test_pushes_and_workflow_runs():
    push = _events(
        "push",
        {
            "ref": "refs/heads/main",
            "before": "a",
            "after": "b",
            "sender": _user("priya"),
            "commits": [
                {
                    "id": "b",
                    "message": "Change the API",
                    "author": {"username": "priya"},
                    "added": ["api/v2.py"],
                    "removed": [],
                    "modified": ["README.md"],
                }
            ],
        },
    )["push"]
    assert push.filters == {
        "repos": ["acme/api-service"],
        "branches": ["main"],
        "paths": ["api/v2.py", "README.md"],
    }
    assert push.thread_key is None
    trigger = {
        "provider": "github",
        "event": "push",
        "filter": {
            "repos": ["acme/api-service"],
            "branches": ["main"],
            "paths": ["api/**"],
        },
    }
    assert trigger_matches(trigger, push)
    assert not trigger_matches({**trigger, "filter": {"paths": ["docs/**"]}}, push)
    run = _events(
        "workflow_run",
        {
            "action": "completed",
            "sender": _user("priya"),
            "workflow_run": {
                "id": 7,
                "name": "tests",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "b",
                "html_url": "u",
                "pull_requests": [],
            },
        },
    )["workflow_run_completed"]
    assert run.filters == {
        "repos": ["acme/api-service"],
        "branches": ["main"],
        "conclusions": ["failure"],
    }
    assert run.payload["workflow_run"]["conclusion"] == "failure"


def test_parse_integrations_turns_on_github_webhooks_with_a_secret(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    [config] = parse_integrations(
        [
            {
                "type": "github",
                "account": "acme-factory",
                "webhook_secret": "${GITHUB_WEBHOOK_SECRET}",
            }
        ]
    )
    assert config == GitHubWebhookConfig(webhook_secret=SECRET, account="acme-factory")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET")
    assert (
        parse_integrations([{"type": "github", "repositories": ["acme/api-service"]}])
        == []
    )


class _Runs:
    def __init__(self):
        self.calls = []

    async def __call__(self, agent, session_id, prompt, progress=None):
        self.calls.append((agent, session_id, prompt))
        return RunResult()


def test_the_route_verifies_and_starts_the_automation(tmp_path):
    path = tmp_path / "automations" / "github-mention-assign" / "automation.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\ntriggers:\n  - provider: github\n    event: issue_mentioned\n    filter:\n"
        '      repos: [acme/api-service]\n      mentioned: [acme-factory]\n      labels: ["factory:Acme"]\n'
        "---\nRead the thread.\n",
        encoding="utf-8",
    )
    runs = _Runs()
    intake = EventIntake(
        project_root=tmp_path,
        entrypoint="foreman",
        store=EventStore(tmp_path / "e.db"),
        run=runs,
    )
    app = FastAPI()
    _mount_github_route(
        app, intake, GitHubWebhookConfig(webhook_secret=SECRET, account="acme-factory")
    )
    comment = {
        "body": "@acme-factory please",
        "user": _user("priya"),
        "html_url": "u",
        "id": 5,
    }
    body = json.dumps(
        {
            "action": "created",
            "issue": _issue(),
            "comment": comment,
            "repository": REPO,
            "sender": _user("priya"),
        }
    ).encode()
    headers = {
        "X-GitHub-Event": "issue_comment",
        "X-GitHub-Delivery": "abc-1",
        "X-Hub-Signature-256": github_signature(SECRET, body),
        "Content-Type": "application/json",
    }
    with TestClient(app) as client:
        assert (
            client.post(
                GITHUB_EVENTS_PATH,
                content=body,
                headers={**headers, "X-Hub-Signature-256": "sha256=0"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                GITHUB_EVENTS_PATH,
                content=b"{}",
                headers={
                    **headers,
                    "X-GitHub-Event": "ping",
                    "X-Hub-Signature-256": github_signature(SECRET, b"{}"),
                },
            ).status_code
            == 204
        )
        assert (
            client.post(GITHUB_EVENTS_PATH, content=body, headers=headers).status_code
            == 202
        )
        # A redelivery (same X-GitHub-Delivery) is recorded once.
        assert (
            client.post(GITHUB_EVENTS_PATH, content=body, headers=headers).status_code
            == 202
        )
        client.portal.call(intake.drain)
    [(agent, _, prompt)] = runs.calls
    assert agent == "foreman"
    assert "Read the thread." in prompt and "@acme-factory please" in prompt
