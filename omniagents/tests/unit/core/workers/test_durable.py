"""Durable worker recovery across transaction boundaries and fresh processes."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from omniagents.core.conversation.model import Thread
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.workers.durable import WorkerJournal
from omniagents.core.workers.recovery import (
    reconcile,
    capture_environment,
    restore_environment,
)
from omniagents.core.session import queue_inbox
from omniagents.core.session.manager import QueuedItem


@pytest.fixture(params=["sqlite", "postgres"])
def journal(tmp_path, request):
    if request.param == "postgres":
        url = os.environ.get("OMNIAGENTS_TEST_PG_URL")
        if not url:
            pytest.skip("OMNIAGENTS_TEST_PG_URL is not configured")
        from uuid import uuid4
        from omniagents.core.conversation.store import PgConversationStore

        store = PgConversationStore(url, tenant_id=f"worker_test_{uuid4().hex}")
    else:
        store = SqliteConversationStore(path=str(tmp_path / "domain.db"))
    store.upsert_thread(Thread(thread_id="parent", user_id="alice"))
    yield WorkerJournal(store, "parent", "alice")
    store.delete_thread("parent")
    store.close()


def admit(journal, wid="worker", operation="spawn"):
    return journal.admit(
        wid,
        operation,
        "task",
        {"kind": "project", "version": 1},
        {"kind": "none", "prepared": True},
        "runtime-one",
    )


def test_admission_deduplicates_and_rejects_changed_payload(journal):
    first = admit(journal)
    assert admit(journal, "different-name")["session_id"] == first["session_id"]
    assert len(journal.rows()) == 1
    with pytest.raises(ValueError, match="different inputs"):
        journal.admit("worker", "spawn", "other task", {}, {}, "runtime-two")
    with pytest.raises(PermissionError):
        WorkerJournal(journal.store, "parent", "bob").rows()


def test_expiry_fences_old_owner_and_resume_preserves_child(journal):
    row = admit(journal)
    journal.change("worker", 1, row["owner"], status="running", heartbeat=0)
    recovered = reconcile(journal)[0]
    assert recovered["status"] == "interrupted"
    with pytest.raises(RuntimeError, match="ownership"):
        journal.finish("worker", 1, row["owner"], "completed", "stale")
    new = journal.resume("worker", "resume-1", "continue", "runtime-two")
    assert new["session_id"] == row["session_id"]
    assert new["generation"] == 2
    assert journal.resume("worker", "resume-1", "continue", "runtime-two") == new
    with pytest.raises(ValueError, match="active execution"):
        journal.resume("worker", "resume-2", "continue", "runtime-three")


def test_completion_and_parent_delivery_are_atomic_and_deduplicated(journal):
    row = admit(journal)
    for _ in range(2):
        journal.finish("worker", 1, row["owner"], "completed", "answer")
    notices = queue_inbox.notifications(journal.store, "parent")
    assert len(notices) == 1
    assert journal.get("worker")["report_status"] == "queued"
    queue_inbox.promote_notifications(
        journal.store, "parent", lambda n: QueuedItem(content=n[0]["content"]).to_dict()
    )
    item = queue_inbox.entries(journal.store, "parent")[0]["item"]
    queue_inbox.claim(journal.store, "parent", item["id"], "parent-runtime")
    assert queue_inbox.finish(journal.store, "parent", item["id"], "parent-runtime")
    assert journal.get("worker")["report_status"] == "delivered"
    journal.finish("worker", 1, row["owner"], "completed", "answer")
    assert not queue_inbox.notifications(journal.store, "parent")
    next_run = journal.resume("worker", "resume", "new task", "runtime-two")
    journal.finish(
        "worker", next_run["generation"], "runtime-two", "completed", "next answer"
    )
    assert len(queue_inbox.notifications(journal.store, "parent")) == 1


def test_completion_transaction_rollback_does_not_lose_report(journal, monkeypatch):
    row = admit(journal)
    report = WorkerJournal._report

    def fail(row, state):
        report(row, state)
        raise RuntimeError("crash before commit")

    monkeypatch.setattr(WorkerJournal, "_report", staticmethod(fail))
    with pytest.raises(RuntimeError):
        journal.finish("worker", 1, row["owner"], "completed", "answer")
    assert journal.get("worker")["status"] == "accepted"
    assert not queue_inbox.notifications(journal.store, "parent")
    monkeypatch.setattr(WorkerJournal, "_report", staticmethod(report))
    journal.finish("worker", 1, row["owner"], "completed", "answer")
    assert len(queue_inbox.notifications(journal.store, "parent")) == 1


def test_cancel_survives_loss_of_handle(journal):
    row = admit(journal)
    journal.change("worker", 1, row["owner"], status="running", heartbeat=0)
    journal.cancel("worker")
    assert reconcile(journal)[0]["status"] == "cancelled"
    assert len(queue_inbox.notifications(journal.store, "parent")) == 1


@pytest.mark.asyncio
async def test_host_environment_recovery_reuses_identity_and_blocks_replacement(
    tmp_path,
):
    from omniagents.execution.host import AgentHostResources, register_host_environment

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "edit.txt").write_text("prior edit")
    original = AgentHostResources.create("first")
    register_host_environment(
        original,
        str(root),
        owner_user_id="alice",
        workspace_id="workspace",
        environment_id="environment",
    )
    env = await capture_environment(original, "environment", str(root), "none")
    row = {"environment": env, "user_id": "alice"}
    fresh = AgentHostResources.create("replacement")
    assert await restore_environment(fresh, row) == "environment"
    assert (root / "edit.txt").read_text() == "prior edit"
    root.rename(tmp_path / "old")
    root.mkdir()
    with pytest.raises(ValueError, match="identity changed"):
        await restore_environment(AgentHostResources.create("third"), row)


@pytest.mark.parametrize(
    "phase",
    [
        "accepted",
        "provisioning",
        "ready",
        "running",
        "completed",
        "report_admitted",
        "report_claimed",
    ],
)
def test_abrupt_process_death_preserves_identity_and_reports(tmp_path, phase):
    db = tmp_path / "crash.db"
    ready = tmp_path / "ready.json"
    script = r"""
import json,sys,time
from pathlib import Path
from omniagents.core.conversation.model import Thread
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.workers.durable import WorkerJournal
store=SqliteConversationStore(path=sys.argv[1]);store.upsert_thread(Thread(thread_id="parent",user_id="alice"))
journal=WorkerJournal(store,"parent","alice")
rows=[]
for name in ("a","b"):
 row=journal.admit(name,name,"task",{"kind":"project","version":1},{"kind":"none","prepared":sys.argv[3] not in ("accepted","provisioning")},"dead-runtime")
 if sys.argv[3] in ("completed","report_admitted","report_claimed"):journal.finish(name,1,"dead-runtime","completed","saved answer")
 else:journal.change(name,1,"dead-runtime",status=sys.argv[3],heartbeat=0)
 rows.append(row)
if sys.argv[3] in ("report_admitted", "report_claimed"):
 from omniagents.core.session import queue_inbox
 from omniagents.core.session.manager import QueuedItem
 queue_inbox.promote_notifications(store,"parent",lambda notices:QueuedItem(content="reports").to_dict())
 if sys.argv[3]=="report_claimed":
  item=queue_inbox.entries(store,"parent")[0]["item"]
  queue_inbox.claim(store,"parent",item["id"],"dead-parent")
  def expire(state,receipt):
   state["items"][0]["claimed_at"]=0
   return None,None
  store.mutate_queue("parent",expire)
Path(sys.argv[2]).write_text(json.dumps(rows))
while True:time.sleep(1)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(db), str(ready), phase],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not ready.exists() and proc.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert ready.exists(), proc.communicate(timeout=1)
        expected = json.loads(ready.read_text())
        proc.kill()
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    reader = r"""
import json,sys
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.workers.durable import WorkerJournal
from omniagents.core.workers.recovery import reconcile
from omniagents.core.session.queue_inbox import notifications,entries,claim,finish
s=SqliteConversationStore(path=sys.argv[1]);j=WorkerJournal(s,"parent","alice")
reconcile(j)
pending=entries(s,"parent")
for entry in pending:
 assert claim(s,"parent",entry["item"]["id"],"new-parent")
 assert finish(s,"parent",entry["item"]["id"],"new-parent")
print(json.dumps({"rows":j.rows(),"reports":notifications(s,"parent"),"admitted":len(pending)}))
"""
    result = json.loads(
        subprocess.check_output([sys.executable, "-c", reader, str(db)], text=True)
    )
    assert [r["session_id"] for r in result["rows"]] == [
        r["session_id"] for r in expected
    ]
    target = (
        "completed"
        if phase in {"completed", "report_admitted", "report_claimed"}
        else "blocked" if phase in {"accepted", "provisioning"} else "interrupted"
    )
    assert {r["status"] for r in result["rows"]} == {target}
    assert len(result["reports"]) == (2 if phase == "completed" else 0)
    if phase.startswith("report_"):
        assert result["admitted"] == 1
        assert {r["report_status"] for r in result["rows"]} == {"delivered"}


@pytest.mark.asyncio
async def test_preallocated_worker_is_owned_before_configuration(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from agents import Agent
    from omniagents.core.agents.specs import AgentSpec
    from omniagents.core.conversation.recorder import recorder_for_session
    from omniagents.core.session.manager import Session
    from omniagents.rpc.agents.workers import WorkerManager

    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "history.db"))
    parent = Session("ownership", "omni", "parent")
    parent.user_id = "alice"
    recorder = recorder_for_session(parent)
    recorder.ensure_thread("parent", user_id="alice")
    journal = WorkerJournal(recorder.store, "parent", "alice")

    async def stream(agent, prompt, session, channel, **kwargs):
        assert session.user_id == "alice"
        session.append_message({"role": "assistant", "content": "owned answer"})
        return {"usage": {}, "model": "test"}

    monkeypatch.setattr("omniagents.core.agents.service.stream_agent", stream)

    async def build(settings, mcp, app):
        return Agent(name="worker", model="gpt-4.1", instructions="Test worker")

    def loader(model):
        return (
            AgentSpec(
                name="worker",
                welcome_text="",
                get_agent_instructions=lambda: "",
                build_agent=build,
                project_slug="ownership",
                agent_slug="omni",
            ),
            {},
        )

    parent_service = SimpleNamespace(
        get_session=lambda sid: parent if sid == "parent" else None,
        _refresh_queue=lambda session: None,
    )
    manager = WorkerManager(spec_loader=loader)
    row = journal.admit(
        "worker",
        "spawn",
        "task",
        {"kind": "project", "version": 1},
        {"kind": "none", "prepared": True},
        manager.owner,
    )
    handle = await manager.spawn(
        worker_id="worker",
        task_description="task",
        parent_session_id="parent",
        parent_service=parent_service,
        project_slug="ownership",
        agent_slug="omni",
        journal=journal,
        record=row,
    )
    try:
        await asyncio.wait_for(handle.done_event.wait(), timeout=15)
        assert handle.status == "completed", handle.error
        assert journal.store.get_thread(row["session_id"]).user_id == "alice"
        assert journal.get("worker")["report_status"] == "queued"
    finally:
        await handle.agent.cleanup()


def test_two_recovery_owners_cannot_both_resume(journal):
    from concurrent.futures import ThreadPoolExecutor

    row = admit(journal)
    journal.change("worker", 1, row["owner"], status="running", heartbeat=0)
    reconcile(journal)

    def attempt(owner):
        try:
            return journal.resume("worker", owner, "continue", owner)["owner"]
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["owner-a", "owner-b"]))
    assert len([result for result in results if result]) == 1
    assert journal.get("worker")["generation"] == 2


def test_canonical_completion_recovers_lost_dispatch_ack(journal):
    from omniagents.core.conversation.recorder import ConversationRecorder

    row = admit(journal)
    recorder = ConversationRecorder(journal.store)
    recorder.ensure_thread(row["session_id"], user_id="alice")
    recorder.open_turn(
        row["session_id"],
        "lost-ack-run",
        prompt="task",
        prompt_role="user",
        user_id="alice",
    )
    recorder.close_turn(
        row["session_id"],
        "lost-ack-run",
        end_reason="completed",
        error=None,
        usage=None,
        model=None,
        model_ref=None,
    )
    journal.change("worker", 1, row["owner"], status="ready", heartbeat=0)
    assert reconcile(journal)[0]["status"] == "completed"
    assert len(queue_inbox.notifications(journal.store, "parent")) == 1
    reconcile(journal)
    assert len(queue_inbox.notifications(journal.store, "parent")) == 1


def test_dismissed_completion_is_not_reported_as_pending(journal):
    row = admit(journal)
    journal.finish("worker", 1, row["owner"], "completed", "answer")
    queue_inbox.promote_notifications(
        journal.store, "parent", lambda n: QueuedItem(content=n[0]["content"]).to_dict()
    )
    item = queue_inbox.entries(journal.store, "parent")[0]["item"]
    assert queue_inbox.cancel(journal.store, "parent", item["id"])["ok"]
    assert journal.get("worker")["report_status"] == "dismissed"
    journal.finish("worker", 1, row["owner"], "completed", "answer")
    assert not queue_inbox.notifications(journal.store, "parent")


def test_legacy_import_requires_owner_and_explicit_parent_link(journal, monkeypatch):
    from types import SimpleNamespace
    from omniagents.core.workers.recovery import import_legacy

    for sid, owner in [("legacy", "alice"), ("unrelated", "alice"), ("foreign", "bob")]:
        journal.store.upsert_thread(
            Thread(thread_id=sid, user_id=owner, source="worker")
        )
    monkeypatch.setattr(
        "omniagents.core.session.history_db.get_session_metadata",
        lambda sid, **kw: (
            {
                "parent_session_id": "other" if sid == "unrelated" else "parent",
                "worker_id": sid,
            },
            {},
        ),
    )
    session = SimpleNamespace(id="parent", user_id="alice")
    service = SimpleNamespace(
        _session_manager=SimpleNamespace(_project_slug="project", _agent_slug="agent")
    )
    import_legacy(service, session, journal)
    import_legacy(service, session, journal)
    assert [(r["worker_id"], r["session_id"], r["status"]) for r in journal.rows()] == [
        ("legacy", "legacy", "blocked")
    ]
    with pytest.raises(ValueError):
        journal.resume("legacy", "resume", "continue", "new")


@pytest.mark.asyncio
async def test_existing_workspace_registration_and_nonhost_never_fall_back(tmp_path):
    from omniagents.execution.host import AgentHostResources, register_host_environment
    from omniagents.execution.models import WorkspaceResource
    from omniagents.execution.errors import EnvironmentNotFoundError

    original = AgentHostResources.create("original")
    register_host_environment(
        original,
        str(tmp_path),
        owner_user_id="alice",
        workspace_id="ws",
        environment_id="env",
    )
    env = await capture_environment(original, "env", str(tmp_path), "none")
    fresh = AgentHostResources.create("fresh")
    fresh.workspaces.register(WorkspaceResource("ws", owner_user_id="alice"))
    assert (
        await restore_environment(fresh, {"environment": env, "user_id": "alice"})
        == "env"
    )
    env.update(kind="docker", environment_id="missing")
    with pytest.raises(EnvironmentNotFoundError):
        await restore_environment(fresh, {"environment": env, "user_id": "alice"})


@pytest.mark.asyncio
async def test_git_worktree_recovers_edits_without_recreating_checkout(tmp_path):
    from omniagents.execution.host import AgentHostResources, register_host_environment

    repo, root = tmp_path / "repo", tmp_path / "worktree"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], stderr=subprocess.STDOUT
        )

    git("init")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "initial",
    )
    git("worktree", "add", "-b", "worker", str(root))
    (root / "unfinished.txt").write_text("preserve this edit")
    original = AgentHostResources.create("original")
    register_host_environment(
        original,
        str(root),
        owner_user_id="alice",
        workspace_id="ws",
        environment_id="env",
    )
    env = await capture_environment(original, "env", str(root), "worktree")
    env["prepared"] = True
    fresh = AgentHostResources.create("fresh")
    assert (
        await restore_environment(fresh, {"environment": env, "user_id": "alice"})
        == "env"
    )
    assert (root / "unfinished.txt").read_text() == "preserve this edit"
    assert (
        subprocess.check_output(
            ["git", "-C", str(root), "branch", "--show-current"]
        ).strip()
        == b"worker"
    )
    root.rename(tmp_path / "moved")
    with pytest.raises(FileNotFoundError):
        await restore_environment(
            AgentHostResources.create("missing"),
            {"environment": env, "user_id": "alice"},
        )


def test_cold_swarm_and_single_restore_preserve_roles_and_mailbox(journal, monkeypatch):
    from types import SimpleNamespace
    from omniagents.builtin.tools import (
        swarm_tools as swarms,
        single_worker_tools as singles,
    )

    row = admit(journal)
    service = object()
    monkeypatch.setattr(swarms, "_journal", lambda svc, sid: journal)
    monkeypatch.setattr(swarms, "_SWARMS", {})
    monkeypatch.setattr(singles, "_WORKERS", {})
    swarm = swarms.Swarm("team", "parent", "objective", service=service)
    swarm.members["builder"] = swarms.Member(
        "builder", "role", row["worker_id"], task="assignment"
    )
    swarm.messages.append(
        {"sender": "owner", "recipient": "builder", "message": "retained guidance"}
    )
    swarms._save(swarm)
    single = singles.SingleWorker(
        "helper", "single-id", role="review", task="review changes"
    )
    singles._save("parent", single)
    swarms.restore_groups(service, "parent")
    singles._restore("parent")
    restored = swarms._SWARMS["team"]
    assert restored.members["builder"].started
    assert restored.members["builder"].role == "role"
    assert restored.messages[0]["message"] == "retained guidance"
    assert singles._WORKERS[("parent", "helper")].role == "review"
    with pytest.raises(RuntimeError):
        journal.save_group("team", {}, 0)


def test_unknown_recipe_version_is_blocked():
    from omniagents.core.workers.recipes import reconstruct

    with pytest.raises(ValueError, match="migration"):
        reconstruct({"version": 999, "kind": "project"}, None, "parent")


def test_kill_after_external_effect_never_replays_unfinished_tool(tmp_path):
    db, ready, effect = tmp_path / "effect.db", tmp_path / "ready", tmp_path / "effect"
    script = r"""
import sys,time
from pathlib import Path
from omniagents.core.conversation.store import SqliteConversationStore
from omniagents.core.conversation.recorder import ConversationRecorder
from omniagents.core.workers.durable import WorkerJournal
store=SqliteConversationStore(path=sys.argv[1]);rec=ConversationRecorder(store)
rec.ensure_thread("parent", user_id="alice")
j=WorkerJournal(store,"parent","alice")
r=j.admit("worker","spawn","task",{}, {"kind":"none","prepared":True},"old")
rec.ensure_thread(r["session_id"],user_id="alice")
rec.open_turn(r["session_id"],"run",prompt="task",prompt_role="user",user_id="alice")
j.change("worker",1,"old",status="running",run_id="run",heartbeat=0)
rec.claim_tool_execution(r["session_id"],{"run_id":"run","call_id":"effect","tool":"external_action","input":"{}"},"dispatcher")
Path(sys.argv[3]).write_text("one external effect")
Path(sys.argv[2]).touch()
while True:time.sleep(1)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(db), str(ready), str(effect)]
    )
    try:
        deadline = time.monotonic() + 15
        while (
            not ready.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert ready.exists()
    finally:
        process.kill()
        process.wait(timeout=5)
    from omniagents.core.conversation.recorder import ConversationRecorder

    store = SqliteConversationStore(path=str(db))
    try:
        journal = WorkerJournal(store, "parent", "alice")
        row = reconcile(journal)[0]
        assert row["status"] == "interrupted"
        assert effect.read_text() == "one external effect"
        recorder = ConversationRecorder(store)
        with pytest.raises(RuntimeError, match="refusing duplicate execution"):
            recorder.claim_tool_execution(
                row["session_id"],
                {
                    "run_id": "run",
                    "call_id": "effect",
                    "tool": "external_action",
                    "input": "{}",
                },
                "replacement",
            )
    finally:
        store.close()


def test_worker_cache_scopes_identical_ids_to_parent_and_namespace(monkeypatch):
    from types import SimpleNamespace
    from omniagents.rpc.agents.workers import WorkerManager
    from omniagents.core.agents.service import (
        bind_current_service,
        reset_current_service,
    )
    from omniagents.core.session.helpers import (
        set_current_session_id,
        reset_current_session_id,
    )

    manager = WorkerManager(spec_loader=lambda model: None)
    for project, parent in [
        ("project-a", "parent"),
        ("project-b", "parent"),
        ("project-a", "other"),
    ]:
        handle = SimpleNamespace(
            worker_id="legacy-id",
            project_slug=project,
            agent_slug="agent",
            parent_session_id=parent,
        )
        manager._remember(handle)
    for project, parent in [
        ("project-a", "parent"),
        ("project-b", "parent"),
        ("project-a", "other"),
    ]:
        service_token = bind_current_service(
            SimpleNamespace(
                _session_manager=SimpleNamespace(
                    _project_slug=project, _agent_slug="agent"
                )
            )
        )
        session_token = set_current_session_id(parent)
        try:
            handle = manager.workers["legacy-id"]
            assert (handle.project_slug, handle.parent_session_id) == (project, parent)
        finally:
            reset_current_session_id(session_token)
            reset_current_service(service_token)
