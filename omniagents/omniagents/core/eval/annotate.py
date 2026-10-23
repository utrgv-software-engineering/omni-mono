"""The failure categories reviewers gave an evaluation run's conversations.

Studio records a review per conversation (trace group) in the project's
trace database: the judgment and notes, and the failure categories seen,
as category ids on the ``analysis`` row. A run's traces carry the scenario
each conversation ran, so the labels can go back onto the scenarios.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from omniagents.core.paths import get_traces_dir
from omniagents.core.tracing.storage import SQLiteStorage


def _scenario_id(metadata: Any) -> str | None:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return None
    scenario = ((metadata or {}).get("omniagents") or {}).get("scenario") or {}
    found = scenario.get("id")
    return str(found) if found else None


def review_labels(studio_project: str, workflow_name: str) -> Dict[str, List[str]]:
    """The failure categories reviewers gave each scenario's conversation in
    the traces of the run named ``workflow_name``, by scenario id."""
    storage = SQLiteStorage(str(get_traces_dir() / f"{studio_project}.db"))
    rows = storage.conn.execute(
        "SELECT group_id, metadata FROM traces WHERE workflow_name = ?",
        (workflow_name,),
    ).fetchall()
    groups: Dict[str, str] = {}
    for row in rows:
        scenario_id = _scenario_id(row["metadata"])
        if scenario_id and row["group_id"] and scenario_id not in groups:
            groups[scenario_id] = row["group_id"]
    labels: Dict[str, List[str]] = {}
    for scenario_id, group_id in groups.items():
        categories = storage.get_analysis_categories(group_id)
        if categories:
            labels[scenario_id] = sorted(set(categories))
    return labels
