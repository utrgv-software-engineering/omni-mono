"""Concurrent first-use must share a connection and migrate exactly once."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3
import time

import pytest

from omniagents.core.session.storage import sqlite as storage


def test_failed_initialization_rolls_back_and_is_not_cached(monkeypatch, tmp_path):
    path = tmp_path / "retry.db"
    monkeypatch.setattr(storage, "get_sessions_db_path", lambda *args: path)
    original = storage._migrate_schema

    def failed(conn):
        original(conn)
        raise OSError("migration failed")

    monkeypatch.setattr(storage, "_migrate_schema", failed)
    backend = storage.SqliteSessionStorage()
    with pytest.raises(OSError, match="migration failed"):
        backend._get_store(None, None)
    assert str(path) not in storage._STORES
    conn = sqlite3.connect(path)
    try:
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            == []
        )
    finally:
        conn.close()
    monkeypatch.setattr(storage, "_migrate_schema", original)
    assert backend._get_store(None, None) is storage._STORES[str(path)]


def test_concurrent_cache_initialization_shares_connection(monkeypatch, tmp_path):
    path = tmp_path / "history.db"
    monkeypatch.setattr(storage, "get_sessions_db_path", lambda *args: path)
    original = storage._ensure_schema

    def slow_schema(conn):
        time.sleep(0.02)
        original(conn)

    monkeypatch.setattr(storage, "_ensure_schema", slow_schema)
    barrier = Barrier(8)

    def get_store(_):
        barrier.wait(timeout=10)
        return storage.SqliteSessionStorage()._get_store(None, None)

    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = list(pool.map(get_store, range(8)))
    assert all(entry is stores[0] for entry in stores)


def test_independent_connections_serialize_schema_migration(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, archived INTEGER)"
        )
    barrier = Barrier(8)

    def migrate(_):
        conn = sqlite3.connect(path, timeout=20)
        try:
            barrier.wait(timeout=10)
            storage._ensure_schema(conn)
            return {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        columns = list(pool.map(migrate, range(8)))
    assert all(
        {"context_json", "sdk_context_json", "user_id"} <= value for value in columns
    )
