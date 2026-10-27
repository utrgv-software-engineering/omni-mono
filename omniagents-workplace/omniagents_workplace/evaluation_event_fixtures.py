"""External events and independent acceptance checks for factory scenarios."""

from __future__ import annotations

import asyncio
import base64
import shlex
import uuid

from omniagents.integrations.slack import normalize_slack_event


def _workplace(environment, context):
    return environment._live[context.session_id]["server"].workplace


def _pull(workplace, repository):
    repo = workplace.github.repos[repository]
    pulls = [i for i in repo.issues.values() if i.pull]
    if len(pulls) != 1:
        raise ValueError(f"Expected one PR before the next event, found {len(pulls)}")
    return repo, pulls[0]


async def prepare_event(environment, context, step, index):
    w = _workplace(environment, context)
    app = (
        w.apps.by_username(environment.app)
        if environment.app
        else next(iter(w.apps.active()))
    )
    context.data["event_actor"] = w.slack.users[app.slack_user_id].name
    action = step.get("action")
    if action == "approve_spec":
        repo, pr = _pull(w, "acme/api-service")
        files = [
            f.filename
            for f in repo.bare.changes(
                w.github.base_sha(repo, pr), w.github.head_sha(repo, pr)
            )
        ]
        if not files or any(not f.startswith("docs/") for f in files):
            raise ValueError(
                "Human approval requires a docs-only spec PR; implementation must not precede approval"
            )
        w.github.create_review(
            repo,
            pr,
            "priya",
            event="APPROVE",
            body="Spec approved; implement the agreed pagination contract.",
        )
    elif action:
        raise ValueError(f"Unknown scenario action: {action}")

    if step["provider"] == "slack":
        thread = step.get("thread", "request")
        threads = context.data.setdefault("event_threads", {})
        parent = threads.get(thread)
        text = step["text"].replace("@factory", f"<@{app.slack_user_id}>")
        message = w.slack.post_message(
            "U0ACME0PRI", "C0ACME0ENG", text, thread_ts=parent
        )
        threads.setdefault(thread, message.ts)
        callback = {
            "type": "event_callback",
            "event_id": step.get("delivery_id", f"eval-{index}"),
            "team_id": w.slack.team_id,
            "authorizations": [{"user_id": app.slack_user_id, "is_bot": False}],
            "event": {
                "type": "message",
                "channel": message.channel,
                "channel_type": "channel",
                "user": message.user,
                "ts": message.ts,
                "thread_ts": parent or message.ts,
                "text": text,
            },
        }
        return normalize_slack_event(callback)
    raise ValueError(f"Unsupported event provider: {step['provider']}")


async def validate_event(environment, context, step):
    """Run a trusted acceptance script on the actual PR head, inside the sandbox.

    The script is kept outside the agent's checkout so changing/deleting the
    repository tests cannot turn an incorrect implementation into a pass.
    """
    w = _workplace(environment, context)
    repo, pr = _pull(w, step.get("repository", "acme/api-service"))
    script = (environment.project_root / "evaluations" / step["validate"]).resolve()
    root = (environment.project_root / "evaluations" / "checks").resolve()
    if not script.is_relative_to(root):
        raise ValueError("Acceptance scripts must be under evaluations/checks")
    encoded = base64.b64encode(script.read_bytes()).decode()
    work = f"/tmp/factory-acceptance-{uuid.uuid4().hex}"
    checkout = f"/workspace/{repo.name}"
    head = w.github.head_sha(repo, pr)
    q = shlex.quote
    command = (
        f"git -C {q(checkout)} fetch origin {q(pr.pull.head)} && "
        f"git -C {q(checkout)} worktree add --detach {q(work)} {q(head)} && "
        f"printf %s {q(encoded)} | base64 -d > {q(work + '.py')} && "
        f"cd {q(work)} && PYTHONPATH=. python3 {q(work + '.py')}"
    )
    session = environment._live[context.session_id]["sandbox"].session
    from omniagents.core.eval.container_routing import _decode

    result = await asyncio.wait_for(session.exec(command), 120)
    return {
        "exit_code": result.exit_code,
        "stdout": _decode(result.stdout),
        "stderr": _decode(result.stderr),
        "head_sha": head,
    }
