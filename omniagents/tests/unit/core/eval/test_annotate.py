import json
import sqlite3

from omniagents.core.eval import annotate


def test_review_labels_are_the_categories_given_in_the_named_run(monkeypatch, tmp_path):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    db_path = db_dir / "proj.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE traces (
            trace_id TEXT,
            group_id TEXT,
            workflow_name TEXT,
            metadata TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE failure_categories (
            id INTEGER,
            name TEXT,
            project TEXT
        )
        """)
    conn.execute("""
        CREATE TABLE analysis_categories (
            project TEXT,
            group_id TEXT,
            category_id INTEGER
        )
        """)
    workflow_name = "base_scenarios_set_2025_01_02_03_04_05"
    metadata = json.dumps({"omniagents": {"scenario": {"id": "abc"}}})
    conn.execute(
        "INSERT INTO traces VALUES (?, ?, ?, ?)",
        ("trace-1", "group-1", workflow_name, metadata),
    )
    conn.execute(
        "INSERT INTO failure_categories VALUES (?, ?, ?)",
        (1, "Quality Review", "proj"),
    )
    conn.execute(
        "INSERT INTO analysis_categories VALUES (?, ?, ?)",
        ("proj", "group-1", 1),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("omniagents.core.eval.annotate.get_traces_dir", lambda: db_dir)

    assert annotate.review_labels("proj", workflow_name) == {"abc": ["quality_review"]}
    assert annotate.review_labels("proj", "another_run") == {}
