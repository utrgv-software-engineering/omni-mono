"""Saved evaluation runs: where they are written and how they are read back.

Every run is saved in the evaluations directory's ``runs/``, whatever it
ran. ``runs/<run id>/`` holds ``results.json``, everything the run produced
(each run record, the summary with its interval, the statistics and
metrics with their slices, warnings, and the manifest that makes two runs
comparable), ``metadata.json``, how it was run (which scenarios, from
where, against which agent), and ``agent/``, a copy of the agent's
``agent.yml`` and instructions as they were, so an old run shows what it
tested. ``runs/index.json`` lists the runs, newest first, so a listing does
not open every result.

The CLI, the notebook and Studio save and read runs through this module
only, so a run saved by one is complete in the others.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from omniagents.core.eval.layout import EvalLayout
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


def _snapshot_agent(agent_path: Path, into: Path) -> None:
    """Copy ``agent.yml`` and the instructions file it names into ``into``."""
    into.mkdir(parents=True)
    shutil.copy2(agent_path, into / agent_path.name)
    try:
        data = yaml.safe_load(agent_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return
    named = data.get("instructions_file") if isinstance(data, dict) else None
    if isinstance(named, str) and named.strip():
        instructions = (agent_path.parent / named).resolve()
        if instructions.is_file():
            shutil.copy2(instructions, into / instructions.name)


def _read_index(layout: EvalLayout) -> dict[str, Any]:
    if not layout.runs_index.is_file():
        return {"runs": []}
    return json.loads(layout.runs_index.read_text(encoding="utf-8"))


def save_evaluation_run(
    layout: EvalLayout,
    data: dict[str, Any],
    *,
    source: str,
    label: str,
    scenario_set: Path | None = None,
    agent: str | None = None,
    agent_path: Path | None = None,
    workflow_name: str | None = None,
) -> Path:
    """Save everything ``data`` (an engine result) holds as a new run and
    index it. ``scenario_set`` is the set file it ran (``None``: the
    project's suite). Returns the run's ``results.json``."""
    run_id = generate_run_id()
    run_dir = layout.runs_dir / run_id
    run_dir.mkdir(parents=True)
    runs = list(data.get("runs") or [])
    summary = summarize_run(runs)
    agent_value = agent.strip() if isinstance(agent, str) and agent.strip() else None
    finished = _now()
    metadata = {
        "id": run_id,
        "source": source,
        "set": layout.relative(scenario_set) if scenario_set else None,
        "label": label,
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
    if agent_path is not None and Path(agent_path).is_file():
        _snapshot_agent(Path(agent_path), run_dir / "agent")

    index = _read_index(layout)
    index["runs"].insert(
        0,
        {
            "id": run_id,
            "timestamp": finished,
            "set": metadata["set"],
            "label": label,
            "source": source,
            "workflow_name": workflow_name,
            "agent": agent_value,
            "summary": summary,
            "manifest": data.get("manifest"),
        },
    )
    layout.runs_index.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return results_path


def run_path(layout: EvalLayout, run_id: str) -> Path:
    """A run's ``results.json``."""
    return layout.runs_dir / run_id / "results.json"


BASELINE_ENTRY = "entry.json"
_BASELINE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _baselines(layout: EvalLayout) -> list[dict[str, Any]]:
    if not layout.baselines_dir.is_dir():
        return []
    entries = []
    for folder in sorted(layout.baselines_dir.iterdir()):
        entry_path = folder / BASELINE_ENTRY
        if entry_path.is_file():
            entry = json.loads(entry_path.read_text(encoding="utf-8"))
            entries.append({**entry, "path": str(folder / "results.json")})
    return entries


def list_runs(layout: EvalLayout) -> list[dict[str, Any]]:
    """Every saved run and every baseline, newest first, each with its
    ``path``; a baseline carries its name as ``baseline``. A run whose folder
    was deleted is not listed."""
    runs = [
        {**entry, "baseline": None, "path": str(run_path(layout, str(entry["id"])))}
        for entry in _read_index(layout).get("runs") or []
        if entry.get("id") and run_path(layout, str(entry["id"])).is_file()
    ]
    return sorted(
        runs + _baselines(layout),
        key=lambda entry: str(entry.get("timestamp") or ""),
        reverse=True,
    )


def keep_run(layout: EvalLayout, run_id: str, name: str) -> dict[str, Any]:
    """Copy a saved run, with the agent it tested, into ``baselines/<name>/``,
    which is committed: the evidence for a number someone reports."""
    if not _BASELINE_NAME.fullmatch(name or ""):
        raise ValueError(
            "A baseline's name is letters, digits, - and _ (it names its folder)"
        )
    target = layout.baselines_dir / name
    if target.exists():
        raise ValueError(f"A baseline named {name!r} exists")
    entry = next(
        (e for e in _read_index(layout).get("runs") or [] if e.get("id") == run_id),
        None,
    )
    if entry is None:
        raise FileNotFoundError(f"No saved run {run_id!r}")
    shutil.copytree(layout.runs_dir / run_id, target)
    kept = {**entry, "baseline": name}
    (target / BASELINE_ENTRY).write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return {**kept, "path": str(target / "results.json")}


def remove_baseline(layout: EvalLayout, name: str) -> None:
    target = layout.baselines_dir / name
    if not (target / BASELINE_ENTRY).is_file():
        raise FileNotFoundError(f"No baseline {name!r}")
    shutil.rmtree(target)


#: results.json path -> (modification time, trace group ids of its records)
_GROUPS: dict[str, tuple[float, dict[str, int]]] = {}


def _groups_of(results_path: Path) -> dict[str, int]:
    stamp = results_path.stat().st_mtime
    cached = _GROUPS.get(str(results_path))
    if cached and cached[0] == stamp:
        return cached[1]
    data = json.loads(results_path.read_text(encoding="utf-8"))
    groups = {
        str(record["trace_group_id"]): index
        for index, record in enumerate(data.get("runs") or [])
        if record.get("trace_group_id")
    }
    _GROUPS[str(results_path)] = (stamp, groups)
    return groups


def find_conversation(layout: EvalLayout, group_id: str) -> dict[str, Any] | None:
    """The saved run record of the conversation traced as ``group_id`` (an
    evaluation run's conversation), with the run's index entry; ``None``
    when no saved run or baseline has it."""
    for entry in list_runs(layout):
        path = Path(entry["path"])
        if not path.is_file():
            continue
        index = _groups_of(path).get(group_id)
        if index is not None:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"entry": entry, "record": data["runs"][index]}
    return None


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
