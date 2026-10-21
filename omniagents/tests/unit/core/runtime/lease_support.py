"""Test helpers for the run lease."""


def expire_lease(store, thread_id, *, claims=False):
    """Age the session's reservation (and optionally its queue claims) past the TTL."""

    def stale(state, receipt):
        owner = state.get("runtime_owner")
        if owner is not None:
            owner["heartbeat"] = 0
        if claims:
            for entry in state.get("items", []):
                if entry.get("owner") is not None:
                    entry["claimed_at"] = 0
        return None, None

    store.mutate_queue(thread_id, stale)
