"""Opt-in factory architecture. Unconfigured projects keep normal routing."""

import os

PERSONAL_SESSION = "factory-personal"


def factory_architecture():
    mode = os.environ.get("FACTORY_ARCHITECTURE")
    if mode not in (None, "personal", "specialist"):
        raise ValueError("FACTORY_ARCHITECTURE must be personal or specialist")
    return mode


def claim_architecture(project, event_store):
    """Hold one architecture per factory process; refuse a busy mode change."""
    import fcntl
    from omniagents.core.paths import get_home_dir
    from omniagents.core.project.runtime import build_spec_from_project
    from omniagents.core.conversation.store import get_conversation_store
    from omniagents.core.conversation.model import ThreadFilter, UNSCOPED
    from omniagents.core.runtime.ownership import lease_expired

    mode = factory_architecture()
    if not mode:
        return None
    path = get_home_dir() / "integrations" / f"{project.product.slug}.architecture"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        previous = handle.read().strip() or "specialist"
        if previous != mode:
            if event_store.running():
                raise RuntimeError(
                    "Cannot switch factory architecture while automation work is active"
                )
            for agent in project.agents_map:
                spec = build_spec_from_project(project, agent)
                store = get_conversation_store(spec.project_slug, spec.agent_slug)
                after = None
                while True:
                    threads = store.list_threads(
                        ThreadFilter(user_id=UNSCOPED), limit=100, after=after
                    )
                    if not threads:
                        break
                    for thread in threads:
                        state = store.mutate_queue(
                            thread.thread_id, lambda state, receipt: (dict(state), None)
                        )
                        owner = state.get("runtime_owner")
                        pending = any(
                            e["item"].get("state", "pending") == "pending"
                            for e in state.get("items", [])
                        )
                        if pending or (owner and not lease_expired(owner)):
                            raise RuntimeError(
                                "Cannot switch factory architecture while conversation work is active or queued"
                            )
                    last = threads[-1]
                    after = (last.updated_at, last.thread_id)
        handle.seek(0)
        handle.truncate()
        handle.write(mode)
        handle.flush()
        return handle
    except BaseException:
        handle.close()
        raise
