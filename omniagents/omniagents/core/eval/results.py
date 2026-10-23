"""Saved evaluation runs: where they are written and how they are read back.

A run is saved beside the scenarios it ran. ``<scenarios dir>/results/<run id>/``
holds ``results.json``, everything the run produced (each run record, the
summary with its interval, the statistics and metrics with their slices,
warnings, and the manifest that makes two runs comparable), and
``metadata.json``, how it was run (from where, against which agent, by
which tool). ``<scenarios dir>/results/runs.json`` indexes that directory's
runs, newest first, so a listing does not open every result.

The CLI, the notebook and Studio save and read runs through this module
only, so a run saved by one is complete in the others.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from omniagents.core.eval.metrics import run_passed, summarize_runs

#: Manifest fields that say which scenarios a run used and how often. Two
#: runs that differ here measured different things, so their pass rates
#: are not compared; differences elsewhere (model, instructions, tools) are
#: what a comparison is for.
SELECTION_FIELDS = ("select", "exclude", "split", "holdout", "scenarios", "repeats")


def generate_run_id() -> str:
    """A run id that sorts by time and does not collide within a second."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{secrets.token_hex(3)}"


def get_results_directory(scenario_path: Path) -> Path:
    """The results directory for runs of the scenarios in ``scenario_path``."""
    results_dir = Path(scenario_path).parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    return results_dir


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def summarize_run(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """What a listing shows of a run: counts, the pass rate with its 95%
    interval, and each measure's pass count."""
    summary = summarize_runs(runs)
    per_measure: dict[str, dict[str, int]] = {}
    for run in runs:
        for measure in run.get("measures") or []:
            entry = per_measure.setdefault(
                str(measure.get("name", "unknown")),
                {"passed": 0, "failed": 0, "total": 0},
            )
            entry["total"] += 1
            entry["passed" if measure.get("passed") else "failed"] += 1
    passed = sum(1 for run in runs if run_passed(run))
    return {
        "total_scenarios": len(runs),
        "passed": passed,
        "failed": len(runs) - passed,
        "answered": summary["answered"],
        "non_responses": len(runs) - summary["answered"],
        "repeats": max([int(run.get("repeats") or 1) for run in runs] or [1]),
        "pass_rate": summary["value"],
        "interval": summary["interval"],
        "per_measure": per_measure,
    }


def save_evaluation_run(
    scenario_path: Path,
    data: dict[str, Any],
    *,
    source: str,
    scenario_name: str | None = None,
    agent: str | None = None,
    workflow_name: str | None = None,
) -> Path:
    """Save everything ``data`` (an engine result) holds as a new run of the
    scenarios in ``scenario_path``, and index it. Returns ``results.json``."""
    results_dir = get_results_directory(scenario_path)
    run_id = generate_run_id()
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True)
    runs = list(data.get("runs") or [])
    summary = summarize_run(runs)
    agent_value = agent.strip() if isinstance(agent, str) and agent.strip() else None
    finished = _now()
    metadata = {
        "id": run_id,
        "source": source,
        "scenario_path": str(scenario_path),
        "scenario_name": scenario_name or Path(scenario_path).parent.name,
        "agent": agent_value,
        "workflow_name": workflow_name,
        "completed_at": finished,
        "summary": summary,
    }
    results_path = run_dir / "results.json"
    results_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )

    index_path = results_dir / "runs.json"
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.is_file()
        else {"schema_version": 2, "runs": []}
    )
    index["schema_version"] = 2
    index["runs"].insert(
        0,
        {
            "id": run_id,
            "timestamp": finished,
            "path": str(results_path),
            "source": source,
            "scenario_name": metadata["scenario_name"],
            "workflow_name": workflow_name,
            "agent": agent_value,
            "summary": summary,
            "manifest": data.get("manifest"),
        },
    )
    index["latest_run_id"] = run_id
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return results_path


def list_runs(roots: Iterable[Path]) -> list[dict[str, Any]]:
    """Every indexed run under ``roots``, newest first."""
    seen: set[str] = set()
    runs: list[dict[str, Any]] = []
    for root in roots:
        if not Path(root).is_dir():
            continue
        for index_path in sorted(Path(root).rglob("results/runs.json")):
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for entry in index.get("runs") or []:
                path = str(entry.get("path") or "")
                if not path or path in seen:
                    continue
                seen.add(path)
                runs.append(entry)
    return sorted(
        runs, key=lambda entry: str(entry.get("timestamp") or ""), reverse=True
    )


def read_run(results_path: Path) -> dict[str, Any]:
    """A saved run: what the engine produced (``runs``, ``summary``,
    ``stats``, ``metrics``, ``manifest``, ``warnings``) as it was saved, with
    its ``path``, ``metadata`` and an ``overview`` (:func:`summarize_run`)."""
    results_path = Path(results_path)
    data = json.loads(results_path.read_text(encoding="utf-8"))
    metadata_path = results_path.parent / "metadata.json"
    metadata = (
        json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata_path.is_file()
        else {}
    )
    runs = list(data.get("runs") or [])
    return {
        **data,
        "runs": runs,
        "path": str(results_path),
        "metadata": metadata,
        "overview": summarize_run(runs),
    }


def scenario_key(run: dict[str, Any]) -> str:
    """Which scenario a run record is a run of."""
    config = run.get("scenario_config") or {}
    return str(
        config.get("id")
        or config.get("name")
        or run.get("scenario_id")
        or run.get("scenario")
        or ""
    )


def _outcomes(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_scenario: dict[str, dict[str, Any]] = {}
    for run in runs:
        key = scenario_key(run)
        entry = by_scenario.setdefault(
            key,
            {
                "name": (run.get("scenario_config") or {}).get("name")
                or run.get("scenario"),
                "passed": 0,
                "runs": 0,
            },
        )
        entry["runs"] += 1
        entry["passed"] += 1 if run_passed(run) else 0
    return by_scenario


def compare_runs(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """How run ``b`` differs from run ``a`` (both from :func:`read_run`).

    ``differences`` lists every manifest field that changed. Pass rates are
    compared only when the runs used the same scenarios the same number of
    times; otherwise ``comparable`` is false and ``reason`` names what
    differs. ``scenarios`` lists each scenario whose outcome changed.
    """
    manifest_a = a.get("manifest") or {}
    manifest_b = b.get("manifest") or {}
    differences = {
        key: [manifest_a.get(key), manifest_b.get(key)]
        for key in sorted(set(manifest_a) | set(manifest_b))
        if key != "created_at" and manifest_a.get(key) != manifest_b.get(key)
    }
    selection = [key for key in SELECTION_FIELDS if key in differences]
    comparable = bool(manifest_a and manifest_b) and not selection
    if not (manifest_a and manifest_b):
        reason = "A run saved without a manifest cannot be compared"
    elif selection:
        reason = "The runs used different scenarios: " + ", ".join(selection)
    else:
        reason = None
    outcomes_a = _outcomes(a.get("runs") or [])
    outcomes_b = _outcomes(b.get("runs") or [])
    changed = []
    for key in sorted(set(outcomes_a) | set(outcomes_b)):
        left, right = outcomes_a.get(key), outcomes_b.get(key)
        rate_a = left["passed"] / left["runs"] if left else None
        rate_b = right["passed"] / right["runs"] if right else None
        if rate_a != rate_b:
            changed.append(
                {
                    "scenario": key,
                    "name": (right or left or {}).get("name"),
                    "a": rate_a,
                    "b": rate_b,
                }
            )
    summary_a, summary_b = a.get("overview") or {}, b.get("overview") or {}
    rate_a, rate_b = summary_a.get("pass_rate"), summary_b.get("pass_rate")
    return {
        "comparable": comparable,
        "reason": reason,
        "differences": differences,
        "pass_rate": {
            "a": rate_a,
            "a_interval": summary_a.get("interval"),
            "b": rate_b,
            "b_interval": summary_b.get("interval"),
            "change": (
                rate_b - rate_a
                if comparable and rate_a is not None and rate_b is not None
                else None
            ),
        },
        "scenarios": changed,
    }


def latest_conversations(scenarios_dir: Path) -> dict[str, dict[str, Any]]:
    """The newest saved run record, with its conversation, of each scenario
    run from ``scenarios_dir`` (keyed by :func:`scenario_key`). What a judge
    is trained and checked against."""
    found: dict[str, dict[str, Any]] = {}
    index_path = Path(scenarios_dir) / "results" / "runs.json"
    if not index_path.is_file():
        return found
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in index.get("runs") or []:
        path = Path(str(entry.get("path") or ""))
        if not path.is_file():
            continue
        for run in json.loads(path.read_text(encoding="utf-8")).get("runs") or []:
            key = scenario_key(run)
            if key and key not in found and run.get("history") is not None:
                found[key] = run
    return found


def conversations_for(
    scenarios_dir: Path, runs_file: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Run records with their conversations, keyed by :func:`scenario_key`:
    from ``runs_file`` (a saved ``results.json`` or any ``{"runs": [...]}``)
    when given, else the newest saved run of each scenario in
    ``scenarios_dir``. A judge is checked and optimized against these."""
    if runs_file is None:
        return latest_conversations(scenarios_dir)
    data = json.loads(Path(runs_file).read_text(encoding="utf-8"))
    runs = data.get("runs") if isinstance(data, dict) else data
    return {scenario_key(run): run for run in runs or [] if scenario_key(run)}
