"""Trusted factory runtime actions, outside tool VMs.

Only admin-allowed repositories can use this runner. Their agent definitions,
Python context/measure modules and prompts are trusted control-plane code. No
workflow shell script or file changed by a tool VM is executed on the host.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .spaces import HostSpace

ACTIONS = {
    f"./.github/actions/{mode}": mode for mode in ("evaluate", "benchmark", "staging")
}


def _name(value):
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(
            "Agent, scenario, and suite names must use letters, numbers, underscores or hyphens"
        )
    return value


def _relative(value):
    path = Path(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not value.endswith((".yml", ".yaml"))
    ):
        raise ValueError(
            "Configuration must be a relative YAML path inside the factory"
        )
    return value


class AgentJobSpace(HostSpace):
    describe = "trusted host agent runtime; tools execute in QuickSand"

    def __init__(self, job_id, profile, *, environment=None):
        import yaml

        selected = Path(profile).resolve()
        if (yaml.safe_load(selected.read_text()).get("client") or {}).get(
            "type"
        ) != "quicksand":
            raise ValueError("Host agent-runtime jobs require a QuickSand tool profile")
        super().__init__(job_id, own_environment=True)
        self.profile = str(selected)
        self.environment = dict(environment) if environment is not None else None

    def base_env(self):
        return {}  # Workflow expressions and scripts never receive host credentials.

    def runtime_env(self):
        env = (
            dict(self.environment)
            if self.environment is not None
            else HostSpace.base_env(self)
        )
        for key in ("PYTHONHOME", "BASH_ENV", "ENV", "OMNIAGENTS_PROJECT_OVERLAYS"):
            env.pop(key, None)
        # Evaluations and staging provision their own service identities. Do not
        # inherit production service tokens or mount the resident Library.
        for key in list(env):
            if (
                key.startswith(
                    (
                        "GITHUB_",
                        "SLACK_",
                        "LINEAR_",
                        "CANVAS_",
                        "MEMORY_",
                        "LIBRARY_",
                        "FACTORY_",
                    )
                )
                or key == "GH_TOKEN"
            ):
                env.pop(key)
        # Development installs use PYTHONPATH; resolve it before changing cwd.
        if env.get("PYTHONPATH"):
            env["PYTHONPATH"] = os.pathsep.join(
                str(Path(p).resolve()) for p in env["PYTHONPATH"].split(os.pathsep) if p
            )
        env["OMNIAGENTS_SANDBOX_PROFILE"] = self.profile
        return env

    def popen(self, *args, **kwargs):
        raise ValueError(
            "Agent-runtime jobs only accept explicit omniagents actions; run scripts belong in a VM job"
        )

    def run(self, *args, **kwargs):
        raise ValueError("Setup actions belong in a VM job, not the host agent runtime")

    def action_command(self, action, given):
        mode = ACTIONS.get(action)
        if not mode:
            raise ValueError(
                "Agent-runtime jobs support actions/checkout@v4 and ./.github/actions/{evaluate,benchmark,staging} only"
            )
        expected = {
            "evaluate": {"agent", "scenario"},
            "benchmark": {"suite", "configs"},
            "staging": {"limit", "since"},
        }[mode]
        if set(given) - expected:
            raise ValueError("Unknown agent-runtime action input")
        values = dict(given)
        if mode == "evaluate":
            values = {k: _name(values.get(k, "")) for k in ("agent", "scenario")}
        elif mode == "benchmark":
            values = {
                "suite": _name(values.get("suite", "")),
                "configs": _relative(
                    values.get("configs", "evaluations/benchmark-configs.yml")
                ),
            }
            resolved = (Path(self.checkout) / values["configs"]).resolve()
            if not resolved.is_relative_to(Path(self.checkout).resolve()):
                raise ValueError("Configuration path escapes the factory")
        else:
            values = {
                "limit": str(int(values.get("limit", "10"))),
                "since": values.get("since", "24h"),
            }
            if int(values["limit"]) < 1 or not re.fullmatch(
                r"\d+[mhd]", values["since"]
            ):
                raise ValueError("Invalid staging limit or duration")
        return [
            sys.executable,
            "-P",
            "-m",
            __name__,
            mode,
            *[f"--{k}={v}" for k, v in values.items()],
        ]


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("evaluate", "benchmark", "staging"))
    for name in ("agent", "scenario", "suite", "configs", "limit", "since"):
        parser.add_argument(f"--{name}")
    args = parser.parse_args()
    # Each invocation gets separate sessions, traces and archives. Never use
    # GITHUB_ENV or other guest-produced files to configure this process.
    archive = os.environ.get("EVALUATIONS_ARCHIVE")
    kept = (
        Path(archive) / "runtime-jobs" / f"{time.time_ns()}-{args.mode}"
        if archive
        else Path.cwd() / ".runtime-results"
    )
    if archive and args.mode == "evaluate":
        sha = os.environ.get("GITHUB_SHA", "manual")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sha):
            raise ValueError("Invalid run SHA")
        kept = Path(archive) / "runs" / sha / _name(args.scenario)
    kept.mkdir(parents=True, exist_ok=True)
    home = Path(archive) / "omniagents" if archive else kept / "home"
    os.environ["OMNIAGENTS_HOME"] = str(home.resolve())
    command = [sys.executable, "-P", "-m", "omniagents"]
    if args.mode == "evaluate":
        command += [
            "eval",
            "suite",
            "run",
            "--agent",
            _name(args.agent),
            "--select",
            _name(args.scenario),
            "--reporter",
            "pretty",
            "--color",
            "never",
        ]
    elif args.mode == "benchmark":
        if (
            not (Path.cwd() / _relative(args.configs))
            .resolve()
            .is_relative_to(Path.cwd().resolve())
        ):
            raise ValueError("Configuration path escapes the factory")
        reports = Path(archive) / "benchmarks" if archive else kept
        reports.mkdir(parents=True, exist_ok=True)
        report = reports / f"{time.time_ns()}-{_name(args.suite)}.json"
        command += [
            "eval",
            "scenarios",
            "run",
            "--set",
            f"evaluations/benchmarks/{_name(args.suite)}.yml",
            "--matrix",
            _relative(args.configs),
            "--output",
            str(report.resolve()),
        ]
    else:
        os.environ.update(
            STAGING_REPLAY_LIMIT=args.limit, STAGING_REPLAY_SINCE=args.since
        )
        # Installed runner implementation, never bin/staging-check from the PR.
        command = ["bash", str(Path(__file__).with_name("staging_check.sh"))]
    try:
        return subprocess.call(command)
    finally:
        for source in (Path("evaluations/runs"), Path("target/eval_transcripts")):
            if source.exists():
                shutil.copytree(source, kept / source.name, dirs_exist_ok=True)
        print(f"Runtime results: {kept}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
