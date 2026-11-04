from omniagents.core.tracing.storage import SQLiteStorage


def test_deleting_a_conversation_deletes_its_traces(tmp_path, monkeypatch):
    import omniagents.core.paths as paths
    from omniagents.core.session.retention import purge_session_derivatives

    monkeypatch.setattr(paths, "get_traces_dir", lambda: tmp_path)
    storage = SQLiteStorage(str(tmp_path / "factory.db"))
    for trace, group in (
        ("t1", "session_s1"),
        ("t2", "session_s1"),
        ("t3", "session_s2"),
    ):
        storage.insert_trace(
            {"id": trace, "workflow_name": "run", "group_id": group, "metadata": {}}
        )
        storage.insert_span(
            {
                "id": f"span-{trace}",
                "trace_id": trace,
                "parent_id": None,
                "started_at": "2026-11-04T00:00:00",
                "ended_at": "2026-11-04T00:00:01",
                "span_data": {"type": "agent", "name": "x"},
                "error": None,
            }
        )
    storage.close()
    purge_session_derivatives("s1")
    storage = SQLiteStorage(str(tmp_path / "factory.db"))
    assert [t["trace_id"] for t in storage.get_traces()] == ["t3"]
    assert storage.get_spans_for_trace("t1") == []
    storage.close()
