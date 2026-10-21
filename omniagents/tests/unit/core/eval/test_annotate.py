import json
import sqlite3

import yaml

from omniagents.core.eval import annotate


def test_annotate_scenarios_maps_failure_categories(monkeypatch, tmp_path):
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

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    agent_yaml = cfg_dir / "agent.yaml"
    agent_yaml.write_text(
        yaml.dump({"tracing": {"name": "base"}}, sort_keys=False),
        encoding="utf-8",
    )
    scenarios_path = cfg_dir / "scenarios.yml"
    scenarios = {"scenarios": [{"id": "abc", "name": "Scenario"}]}
    scenarios_path.write_text(yaml.dump(scenarios, sort_keys=False), encoding="utf-8")
    manifest_path = cfg_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"timestamp": "2025_01_02_03_04_05", "name": "set"}),
        encoding="utf-8",
    )

    annotated, original, returned = annotate.annotate_scenarios(
        agent_yaml, scenarios_path, "proj", None
    )

    assert returned == scenarios_path
    assert original == scenarios["scenarios"]
    assert annotated[0]["failure_categories"] == ["quality_review"]


def test_write_annotation_output_updates_file(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    output_path = project_root / "scenarios.yml"
    original = {"scenarios": [{"id": "one"}]}
    output_path.write_text(yaml.dump(original, sort_keys=False), encoding="utf-8")

    annotated = [{"id": "one", "failure_categories": ["q"]}]
    annotate.write_annotation_output(project_root, output_path, annotated)

    updated = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert updated["scenarios"] == annotated
    backups = list(output_path.parent.glob("scenarios.yml.bak.*"))
    assert backups
