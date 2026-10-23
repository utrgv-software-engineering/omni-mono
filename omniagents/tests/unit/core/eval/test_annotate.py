from omniagents.core.eval import annotate
from omniagents.core.tracing.storage import SQLiteStorage


def test_review_labels_are_the_categories_studio_recorded_for_the_run(
    monkeypatch, tmp_path
):
    """Written the way Studio writes a review: categories on the analysis row."""
    monkeypatch.setattr(
        "omniagents.core.eval.annotate.get_traces_dir", lambda: tmp_path
    )
    storage = SQLiteStorage(str(tmp_path / "proj.db"))
    for trace, group, workflow, scenario in (
        ("t1", "g1", "run_a", "abc"),
        ("t2", "g2", "run_a", "def"),
        ("t3", "g3", "run_b", "abc"),
    ):
        storage.insert_trace(
            {
                "id": trace,
                "workflow_name": workflow,
                "group_id": group,
                "metadata": {"omniagents": {"scenario": {"id": scenario}}},
            }
        )
    storage.set_analysis_categories("proj", "g1", ["quality_review", "tone"])
    storage.set_analysis_categories("proj", "g3", ["other_run"])

    assert annotate.review_labels("proj", "run_a") == {
        "abc": ["quality_review", "tone"]
    }
    assert annotate.review_labels("proj", "run_b") == {"abc": ["other_run"]}
    assert annotate.review_labels("proj", "no_such_run") == {}
