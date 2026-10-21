"""Reconcile durable worker records without replaying interrupted effects."""

from __future__ import annotations

import os
import time
from .durable import ACTIVE, TERMINAL, LEASE_SECONDS


def reconcile(journal):
    from omniagents.core.runtime.ownership import lease_expired
    from omniagents.core.session import queue_inbox
    from omniagents.core.conversation.model import ItemKind

    for row in journal.rows():
        if row["status"] not in ACTIVE:
            continue
        child = journal.store.get_thread(row["session_id"])
        if child is not None and child.user_id != journal.user_id:
            raise PermissionError("Worker conversation ownership changed")
        run_id = row["run_id"]
        if child is not None and not run_id:
            receipt = journal.store.submission_receipt(
                row["session_id"],
                f"worker-start:{row['worker_id']}:{row['generation']}",
            )
            if receipt and receipt.get("status") == "completed":
                run_id = receipt["result"].get("run_id", "")
        if child is not None and not run_id:
            turns = journal.store.list_turns(
                row["session_id"], limit=1, descending=True
            )
            if turns and turns[0].created_at >= row.get(
                "execution_after", row["started_at"]
            ):
                run_id = turns[0].turn_id
        turn = journal.store.get_turn(row["session_id"], run_id) if run_id else None
        if turn is not None and turn.end_reason is not None:
            items = journal.store.list_items(
                row["session_id"],
                limit=1,
                turn_id=run_id,
                kinds=[ItemKind.AGENT_MESSAGE],
                descending=True,
            )
            result = items[0].content.get("text") if items else None
            status = (
                "cancelled"
                if turn.end_reason == "cancelled"
                else "error" if turn.error else "completed"
            )
            try:
                journal.finish(
                    row["worker_id"],
                    row["generation"],
                    row["owner"],
                    status,
                    result,
                    str(turn.error) if turn.error else None,
                )
            except RuntimeError:
                pass  # A newer execution won the compare-and-swap.
            continue
        if time.time() - row["heartbeat"] <= LEASE_SECONDS:
            continue
        if row["status"] in {
            "accepted",
            "provisioning",
            "ready",
            "resuming",
        } and not lease_expired(
            queue_inbox.runtime_owner(journal.store, journal.parent_id)
        ):
            continue
        if child is not None and not lease_expired(
            queue_inbox.runtime_owner(journal.store, row["session_id"])
        ):
            continue
        if row.get("cancel_requested"):
            journal.finish(
                row["worker_id"],
                row["generation"],
                row["owner"],
                "cancelled",
                error="Cancelled by owner",
            )
        else:
            incomplete = row["status"] in {"accepted", "provisioning"} and not row[
                "environment"
            ].get("prepared")
            journal.interrupt(
                row,
                status="blocked" if incomplete else "interrupted",
                reason=(
                    "Workspace provisioning was interrupted; inspect the recorded preparation paths"
                    if incomplete
                    else None
                ),
            )
    return journal.rows()


def host_identity(root):
    stat = os.stat(root)
    return {"path": os.path.realpath(root), "device": stat.st_dev, "inode": stat.st_ino}


async def capture_environment(resources, environment_id, root, isolation):
    value = {
        "environment_id": environment_id,
        "root": root,
        "isolation": isolation,
        "prepared": isolation == "none",
    }
    if environment_id is None:
        value["kind"] = "none"
        return value
    if resources is None:
        raise ValueError("Worker environment registry unavailable")
    lease = await resources.environments.acquire(environment_id)
    value.update(
        kind=lease.descriptor.kind.value,
        workspace_id=lease.workspace_id,
        generation=lease.generation,
    )
    if value["kind"] == "host":
        value["identity"] = host_identity(root)
    return value


async def restore_environment(resources, row):
    env = row["environment"]
    if row.get("cleaned"):
        raise ValueError("Worker workspace was explicitly cleaned up")
    if not env.get("prepared"):
        raise ValueError("Worker workspace preparation is incomplete")
    if env.get("kind") == "none":
        return None
    if resources is None:
        raise ValueError("Worker environment registry unavailable")
    if env.get("kind") == "host":
        if host_identity(env["root"]) != env.get("identity"):
            raise ValueError(
                "Worker workspace identity changed; refusing a different directory"
            )
        from omniagents.execution.host import register_host_environment
        from omniagents.execution.errors import EnvironmentNotFoundError

        try:
            lease = await resources.environments.acquire(env["environment_id"])
        except EnvironmentNotFoundError:
            register_host_environment(
                resources,
                env["root"],
                owner_user_id=row["user_id"],
                workspace_id=env["workspace_id"],
                environment_id=env["environment_id"],
            )
            lease = await resources.environments.acquire(env["environment_id"])
        if host_identity(lease.workspace.root) != env["identity"]:
            raise ValueError("Worker environment points to a different workspace")
    else:
        # Reattach only the original backend generation. Never turn a vanished
        # remote/container/WASM environment into a host directory with the same path.
        lease = await resources.environments.acquire(
            env["environment_id"], expected_generation=env["generation"]
        )
    if (
        lease.workspace_id != env["workspace_id"]
        or lease.descriptor.kind.value != env["kind"]
    ):
        raise ValueError("Worker environment identity changed")
    workspace = resources.workspaces.get(lease.workspace_id)
    if workspace.owner_user_id not in (None, row["user_id"]):
        raise PermissionError("Worker workspace belongs to a different owner")
    return lease.environment_id


def reconcile_session(service, session, store):
    """Reconnect loads state immediately; a bounded watcher waits out old leases."""
    import asyncio
    from .durable import WorkerJournal

    journal = WorkerJournal(store, session.id, session.user_id)
    import_legacy(service, session, journal)
    rows = reconcile(journal)

    from omniagents.core.session import queue_inbox

    def queued(row):
        if row.get("closed") or row.get("cleaned") or row["status"] == "blocked":
            return False
        return any(
            entry["item"].get("state", "pending") == "pending"
            for entry in queue_inbox.entries(store, row["session_id"])
        )

    def unsettled(rows):
        return any(
            row["status"] in ACTIVE
            or queued(row)
            or (
                row.get("report_status") == "queued"
                and time.time() - row["updated_at"] < 300
            )
            for row in rows
        )

    if not unsettled(rows):
        return
    task = getattr(session, "worker_recovery_task", None)
    if task is not None and not task.done():
        return

    async def watch():
        from omniagents.core.agents.service import (
            bind_current_service,
            reset_current_service,
        )
        from omniagents.core.session.helpers import (
            set_current_session_id,
            reset_current_session_id,
        )

        service_token = bind_current_service(service)
        session_token = set_current_session_id(session.id)
        try:
            while True:
                await asyncio.sleep(5)
                rows = reconcile(journal)
                from omniagents.builtin.tools.worker_tools import manager

                candidates = [
                    row for row in rows if row["status"] not in ACTIVE and queued(row)
                ]
                if candidates:
                    mgr = manager()
                    namespace = service._session_manager
                    mgr.restore(
                        journal, service, namespace._project_slug, namespace._agent_slug
                    )
                    for row in candidates:
                        try:
                            await mgr.drain_queued(row["worker_id"])
                        except Exception as exc:
                            # Retain queued messages, and expose unavailable environments.
                            journal.change(
                                row["worker_id"],
                                row["generation"],
                                row["owner"],
                                status="blocked",
                                error=str(exc),
                            )
                service._refresh_queue(session)
                from omniagents.builtin.tools.worker_tools import (
                    broadcast_workers_snapshot,
                )

                await broadcast_workers_snapshot(service, session.id)
                if not unsettled(rows):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Worker reconciliation failed; persisted work remains recoverable"
            )
        finally:
            reset_current_session_id(session_token)
            reset_current_service(service_token)

    session.worker_recovery_task = asyncio.create_task(
        watch(), name=f"worker-recovery:{session.id}"
    )


def import_legacy(service, session, journal):
    """One conservative import of proven parent/child links; never infer a workspace."""
    from omniagents.core.conversation.model import ThreadFilter
    from omniagents.core.session.history_db import get_session_metadata

    if journal.mutate(lambda data, state: data.get("legacy_imported", False)):
        return
    # Anonymous historical sessions have no reliably scoped owner index.
    if session.user_id is None:
        return
    manager = service._session_manager
    found = []
    after = None
    while True:
        threads = journal.store.list_threads(
            ThreadFilter(user_id=session.user_id, source="worker"),
            limit=100,
            after=after,
        )
        for thread in threads:
            context, _ = get_session_metadata(
                thread.thread_id,
                project_slug=manager._project_slug,
                agent_slug=manager._agent_slug,
            )
            if (
                not isinstance(context, dict)
                or context.get("parent_session_id") != session.id
            ):
                continue
            wid = context.get("worker_id")
            if not isinstance(wid, str) or not wid:
                continue
            found.append((wid, thread))
        if len(threads) < 100:
            break
        after = (threads[-1].updated_at, threads[-1].thread_id)

    def update(data, state):
        for wid, thread in found:
            if wid in data["workers"] or len(data["workers"]) >= 512:
                continue
            data["workers"][wid] = dict(
                worker_id=wid,
                operation_id=f"legacy:{thread.thread_id}",
                digest="",
                session_id=thread.thread_id,
                parent_session_id=session.id,
                user_id=session.user_id,
                task=thread.title or "Legacy worker",
                recipe={"kind": "legacy", "version": 0},
                environment={
                    "kind": "unknown",
                    "prepared": False,
                    "isolation": "unknown",
                },
                status="blocked",
                generation=1,
                owner="legacy",
                heartbeat=0,
                started_at=thread.created_at,
                updated_at=time.time(),
                run_id="",
                result=None,
                error="Legacy worker history is available, but its workspace and configuration were not recorded. Automatic reconstruction is unsafe.",
                report_status=None,
                cancel_requested=False,
            )
        data["legacy_imported"] = True

    journal.mutate(update)
