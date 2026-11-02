#!/usr/bin/env python3
"""One existing generalist outcome scenario, run inside the candidate image.

The trusted operator invokes this script; the agent process remains outside its
Docker tool sandbox. No production Workplace or service credentials are supplied.
"""

import json
import os
from pathlib import Path
import sys
import shutil

import yaml

from omniagents.core.eval.pipeline import run_evaluation
from omniagents.core.eval.results import save_evaluation_run
from omniagents.core.eval.workspace import EvalWorkspace

root = Path("/release")
os.chdir(root)
workspace = EvalWorkspace(root, agent="generalist")
config = workspace.config()
source = root / "evaluations/scenarios/foreman.yml"
scenario = next(
    s
    for s in yaml.safe_load(source.read_text())["scenarios"]
    if s["name"] == "factory_clear_request"
)
scenario["agent"] = "generalist"
scenario["expect"]["entrypoint"] = "generalist"
scenario["measures"] = ["factory_outcome"]
scenario["event_timeout_seconds"] = 1200
scenario["_source_path"] = str(source)
try:
    result = run_evaluation(
        config.agent_path,
        tracing_cfg=config.tracing_config,
        workflow_base_name="factory-release-gate",
        include_unmeasured=False,
        eval_config=config,
        eval_cfg={**config.config, "scenarios": [scenario]},
        include_history=True,
    )
    saved = save_evaluation_run(
        workspace.layout,
        result,
        source="factory-release",
        label="factory_clear_request",
        scenario_set=source,
        agent="generalist",
        agent_path=config.agent_path,
        workflow_name=result.get("workflow_name"),
    )
    runs = result.get("runs") or []
    measures = [
        m
        for run in runs
        for m in run.get("measures", [])
        if m["name"] == "factory_outcome"
    ]
    passed = (
        len(runs) == 1
        and len(measures) == 1
        and measures[0].get("passed") is True
        and not any(run.get("error") for run in runs)
    )
    report = {
        "scenario": "factory_clear_request",
        "passed": passed,
        "results": str(saved),
        "measures": measures,
        "errors": [r.get("error") for r in runs if r.get("error")],
    }
except Exception as exc:
    report = {
        "scenario": "factory_clear_request",
        "passed": False,
        "error": type(exc).__name__ + ": " + str(exc),
    }
if (root / "evaluations/runs").exists():
    shutil.copytree(root / "evaluations/runs", "/eval-results/runs", dirs_exist_ok=True)
Path("/eval-results/result.json").write_text(json.dumps(report, indent=2, default=str))
print(
    json.dumps({"scenario": report["scenario"], "passed": report["passed"]}), flush=True
)
sys.exit(0 if report["passed"] else 1)
