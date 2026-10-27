import gc
import importlib

import pytest


@pytest.fixture(autouse=True)
def _isolated_omniagents_home(tmp_path, monkeypatch):
    """Keep traces, sessions and history out of the developer's real home."""
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "omniagents-home"))
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "history.db"))
    # Product identity comes from the project under test, never from
    # distributions installed in the dev environment.
    monkeypatch.setenv("OMNIAGENTS_PRODUCT_DISCOVERY", "0")

    import omniagents.core.session.history_db as history_db
    import omniagents.core.session.manager as manager

    importlib.reload(history_db)
    importlib.reload(manager)
    yield
    for connection, _ in history_db._STORES.values():
        connection.close()
    history_db._STORES.clear()
    from omniagents.core.conversation.store import reset_conversation_store

    reset_conversation_store()
    gc.collect()


@pytest.fixture(autouse=True)
def _isolate_product_identity():
    """``load_project()`` installs the manifest's product identity process-wide;
    restore it so one test's fixture project never leaks into the next."""
    from omniagents.core import product

    current = product._current
    explicitly_set = product._explicitly_set
    discovered = product._discovered
    yield
    product._current = current
    product._explicitly_set = explicitly_set
    product._discovered = discovered
