"""Benchmarks: the same tasks under several configurations, cost against correctness.

``omniagents eval scenarios run --set <tasks> --matrix <configs.yml>`` runs
a scenario set (a factory's benchmark suite: tasks taken from its real runs)
once per configuration::

    configs:
      - {name: astra, model: gpt-6-astra}
      - {name: terra-high, model: gpt-5.6-terra, reasoning: high}
      - {name: luna, model: gpt-5.6-luna}
    runs: 3          # each task this many times per configuration

Each configuration's runs are made with its model and reasoning effort for
every agent (``core.agents.overrides``). What every model call uses is
recorded (``core.usage``), and priced with the project's prices
(``paths.pricing``). The result puts the configurations side by side:
correctness (the share of runs whose measures all passed), cost per task,
tokens and time, and which are on the frontier (no other is both cheaper and
more correct).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

__all__ = ["frontier", "load_matrix", "run_matrix"]


def load_matrix(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    configs = data.get("configs") if isinstance(data, dict) else None
    if not isinstance(configs, list) or not configs:
        raise ValueError(
            f"{path}: expected `configs:`, a list of {{name, model, reasoning?}}"
        )
    names = set()
    for config in configs:
        if (
            not isinstance(config, dict)
            or not config.get("name")
            or not config.get("model")
        ):
            raise ValueError(f"{path}: each configuration has a name and a model")
        if config["name"] in names:
            raise ValueError(f"{path}: two configurations are named {config['name']!r}")
        names.add(config["name"])
    runs = int(data.get("runs") or 1)
    if runs < 1:
        raise ValueError(f"{path}: runs is at least 1")
    return {"configs": configs, "runs": runs}


def frontier(rows: List[Dict[str, Any]]) -> List[str]:
    """The configurations no other beats on both cost per task and correctness."""
    priced = [r for r in rows if r.get("cost_per_task") is not None]
    out = []
    for r in priced:
        beaten = any(
            q is not r
            and q["cost_per_task"] <= r["cost_per_task"]
            and q["correctness"] >= r["correctness"]
            and (
                q["cost_per_task"] < r["cost_per_task"]
                or q["correctness"] > r["correctness"]
            )
            for q in priced
        )
        if not beaten:
            out.append(r["name"])
    return out


def _passed(run: Dict[str, Any]) -> bool:
    measures = run.get("measures") or []
    return (
        not run.get("error")
        and bool(measures)
        and all(m.get("passed") for m in measures)
    )


def run_matrix(
    run_once: Callable[[], Dict[str, Any]],
    matrix: Dict[str, Any],
    *,
    prices: Any,
    suite: str,
    on_config: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Run ``run_once`` (one evaluation of the task set, returning its results) per
    configuration, ``matrix["runs"]`` times, and compare them."""
    from omniagents.core.agents.overrides import overriding
    from omniagents.core.tracing import (
        add_permanent_processor,
        remove_permanent_processor,
    )
    from omniagents.core.usage import UsageRecorder

    rows = []
    for config in matrix["configs"]:
        records: List[Dict[str, Any]] = []
        recorder = UsageRecorder(records.append)
        add_permanent_processor(recorder)
        started = time.time()
        results: List[Dict[str, Any]] = []
        paths: List[str] = []
        try:
            with overriding(model=config["model"], reasoning=config.get("reasoning")):
                for _ in range(matrix["runs"]):
                    data = run_once()
                    paths.append(str(data.get("path") or ""))
                    results += data.get("runs") or []
        finally:
            remove_permanent_processor(recorder)
        tokens = {
            k: sum(int(r.get(k) or 0) for r in records)
            for k in (
                "input_tokens",
                "cached_tokens",
                "cache_write_tokens",
                "output_tokens",
                "reasoning_tokens",
            )
        }
        costs = [
            prices.cost(r.get("model"), r) if prices is not None else None
            for r in records
        ]
        cost = None if not records or any(c is None for c in costs) else sum(costs)
        tasks = len(results)
        passed = sum(1 for r in results if _passed(r))
        by_task: Dict[str, Dict[str, int]] = {}
        for r in results:
            slot = by_task.setdefault(str(r.get("scenario")), {"runs": 0, "passed": 0})
            slot["runs"] += 1
            slot["passed"] += int(_passed(r))
        row = {
            "name": config["name"],
            "model": config["model"],
            "reasoning": config.get("reasoning"),
            "runs": tasks,
            "passed": passed,
            "correctness": (passed / tasks) if tasks else 0.0,
            "requests": len(records),
            **tokens,
            "cost": cost,
            "cost_per_task": (cost / tasks) if cost is not None and tasks else None,
            "seconds_per_task": ((time.time() - started) / tasks) if tasks else None,
            "by_task": by_task,
            "results": paths,
        }
        rows.append(row)
        if on_config is not None:
            on_config(row)
    return {
        "suite": suite,
        "at": time.time(),
        "runs_per_task": matrix["runs"],
        "configs": rows,
        "frontier": frontier(rows),
    }


def write(result: Dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return path
