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
) -> Path:
    """
    Save evaluation run results with full metadata.

    Args:
        scenario_path: Path to the scenario YAML file
        data: The evaluation results dict with "runs" key
        source: "cli" or "studio"
        scenario_name: Optional name for the scenario set

    Returns:
        Path to the saved results.json file
    """
    run_id = generate_run_id()
    results_dir = get_results_directory(scenario_path)
    runs = data.get("runs", [])

    # Calculate summary stats
    total = len(runs)
    passed = sum(1 for r in runs if r.get("passed", False))
    failed = total - passed

    metadata = {
        "source": source,
        "scenario_path": str(scenario_path),
        "scenario_name": scenario_name or scenario_path.stem,
        "started_at": datetime.now().isoformat(),
        "completed_at": datetime.now().isoformat(),
        "total_scenarios": total,
        "passed": passed,
        "failed": failed,
    }

    results_path = save_run_results(results_dir, run_id, runs, metadata)

    # Update index
    run_entry = {
        "id": run_id,
        "timestamp": metadata["completed_at"],
        "path": str(results_path),
        "workflow_name": "",
        "summary": {
            "total_scenarios": total,
            "passed": passed,
            "failed": failed,
        },
    }
    update_runs_index(results_dir, run_entry)

    return results_path
