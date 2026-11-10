"""The factory's intake lock, and the conversation the former personal mode shared."""

#: Where the former personal mode kept every chat and automation; its history
#: is still read for reports.
PERSONAL_SESSION = "factory-personal"


def intake_lock_path(slug):
    """Held by the factory's running server; workspace maintenance takes it too."""
    from omniagents.core.paths import get_home_dir

    return get_home_dir() / "integrations" / f"{slug}.intake.lock"


def claim_intake(project):
    """One process owns a factory's intake and its shared machine at a time."""
    import fcntl

    path = intake_lock_path(project.product.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BaseException:
        handle.close()
        raise
