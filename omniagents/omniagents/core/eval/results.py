"""Shared results storage for evaluation runs (CLI and Studio)."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def generate_run_id() -> str:
    """Generate timestamped run ID."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_results_directory(scenario_path: Path) -> Path:
    """Get or create results directory next to scenario file."""
    results_dir = scenario_path.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir


def save_run_results(
    results_dir: Path,
    run_id: str,
    runs: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> Path:
    """Save results to structured directory."""
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Save results
    results_path = run_dir / "results.json"
    results_path.write_text(json.dumps({"runs": runs}, indent=2), encoding="utf-8")

    # Save metadata
    meta_path = run_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return results_path


def update_runs_index(results_dir: Path, run_entry: Dict[str, Any]) -> None:
    """Update runs.json index with new run entry."""
    index_path = results_dir / "runs.json"
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index_data = {"schema_version": 1, "runs": []}

    index_data["runs"].insert(0, run_entry)  # Most recent first
    index_data["latest_run_id"] = run_entry["id"]

    index_path.write_text(json.dumps(index_data, indent=2), encoding="utf-8")


def save_evaluation_run(
    scenario_path: Path,
    data: Dict[str, Any],
    source: str = "cli",
    scenario_name: Optional[str] = None,
    agent: Optional[str] = None,
) -> Path:
    """
    Save evaluation run results with full metadata.

    Args:
        scenario_path: Path to the scenario YAML file
        data: The evaluation results dict with "runs" key
        source: "cli" or "studio"
        scenario_name: Optional name for the scenario set
        agent: Optional agent key/name this run targeted (e.g. "ai_doc" or
            "baseline"). When ``None`` or empty, the run hit the project's
            entrypoint agent. Surfaced by the Studio Results UI so users can
            tell which agent each historical run was scored against.

    Returns:
        Path to the saved results.json file
    """
    run_id = generate_run_id()
    results_dir = get_results_directory(scenario_path)
    runs = data.get("runs", [])

    # Calculate summary stats. A run "passes" iff every measure on it passed
    # (mirrors the rule used by the Studio detail endpoint). Runs with no
    # measures are treated as passes so empty scenarios don't poison the count.
    def _run_passed(r: Dict[str, Any]) -> bool:
        measures = r.get("measures", [])
        if not measures:
            return True
        return all(m.get("passed", False) for m in measures)

    total = len(runs)
    passed = sum(1 for r in runs if _run_passed(r))
    failed = total - passed

    # Per-measure aggregates: { measure_name: {passed, failed, total} }. Lets
    # the UI surface each measure's pass rate independently so users can see
    # softer measures climb separately from stricter ones.
    per_measure: Dict[str, Dict[str, int]] = {}
    for r in runs:
        for m in r.get("measures", []):
            name = m.get("name", "unknown")
            entry = per_measure.setdefault(name, {"passed": 0, "failed": 0, "total": 0})
            entry["total"] += 1
            if m.get("passed", False):
                entry["passed"] += 1
            else:
                entry["failed"] += 1

    agent_value = agent.strip() if isinstance(agent, str) and agent.strip() else None

    metadata = {
        "source": source,
        "scenario_path": str(scenario_path),
        "scenario_name": scenario_name or scenario_path.stem,
        "agent": agent_value,
        "started_at": datetime.now().isoformat(),
        "completed_at": datetime.now().isoformat(),
        "total_scenarios": total,
        "passed": passed,
        "failed": failed,
        "per_measure": per_measure,
    }

    results_path = save_run_results(results_dir, run_id, runs, metadata)

    # Update index
    run_entry = {
        "id": run_id,
        "timestamp": metadata["completed_at"],
        "path": str(results_path),
        "workflow_name": "",
        "agent": agent_value,
        "summary": {
            "total_scenarios": total,
            "passed": passed,
            "failed": failed,
            "per_measure": per_measure,
        },
    }
    update_runs_index(results_dir, run_entry)

    return results_path
