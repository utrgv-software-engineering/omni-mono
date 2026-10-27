import gc
import importlib

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "filterwarnings",
        "ignore:unclosed database:ResourceWarning",
    )


# ---------------------------------------------------------------------------
# Pytest fixture: no ambient product discovery
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_ambient_product_discovery(monkeypatch):
    """Framework tests must not inherit a product identity from
    distributions installed in the dev environment (e.g. an editable
    omni-code declaring an ``omniagents.products`` entry point).
    Discovery tests re-enable it explicitly with ``delenv``."""
    monkeypatch.setenv("OMNIAGENTS_PRODUCT_DISCOVERY", "0")


# ---------------------------------------------------------------------------
# Pytest fixture: nothing is written into the developer's real home
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolated_omniagents_home(tmp_path, monkeypatch):
    """Traces, sessions and anything else kept under ``~/.omniagents`` go to
    a per-test directory. Without this, a test that records traces (an
    evaluation run, scenario generation) left ``<project>.db`` files in the
    developer's real ``~/.omniagents/traces``, where Studio lists them as
    projects. A test that needs a particular home still sets its own."""
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path / "omniagents-home"))


# ---------------------------------------------------------------------------
# Pytest fixture: process-global product identity must not leak across tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_product_identity():
    """``load_project()`` installs a manifest's ``product:`` section as the
    process-wide default identity (``omniagents.core.product``). Without
    per-test restoration, any test that loads a project fixture carrying a
    ``product:`` section (e.g. slug ``acme``) silently rewrites how every
    later test derives slugs, dot-dirs, and config paths — skill roots
    become ``.acme/skills`` instead of ``.omniagents/skills``. Snapshot
    the identity globals before each test and restore them after, so a
    test only ever sees identity state it installed itself."""
    from omniagents.core import product

    current = product._current
    explicitly_set = product._explicitly_set
    discovered = product._discovered
    yield
    product._current = current
    product._explicitly_set = explicitly_set
    product._discovered = discovered


# ---------------------------------------------------------------------------
# Pytest fixture: temporary history DB for tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def temp_history_db(tmp_path, monkeypatch):
    db_path = tmp_path / "history.db"
    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(db_path))
    # Reload modules that depend on this env var
    import omniagents.core.session.history_db as history_db
    import omniagents.core.session.manager as manager

    importlib.reload(history_db)
    importlib.reload(manager)
    yield
    for connection, _ in history_db._STORES.values():
        connection.close()
    history_db._STORES.clear()
    # The canonical-conversation store caches a sqlite connection per
    # resolved db path; closing it here keeps the per-test temp dir free of
    # open handles (Windows) and the cache from growing across the suite.
    from omniagents.core.conversation.store import reset_conversation_store

    reset_conversation_store()
    gc.collect()
