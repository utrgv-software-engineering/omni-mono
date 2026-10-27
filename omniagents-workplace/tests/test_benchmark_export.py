"""A real run's task into a benchmark suite, as a pull request on the factory's repository."""

import time

import pytest
import yaml

from omniagents_workplace.benchmark_export import BenchmarkError, export_benchmark
from omniagents_workplace.server import Workplace, load_seed


def test_a_runs_task_becomes_a_benchmark_pull_request(tmp_path):
    w = Workplace(tmp_path / "data", base_url="http://w", seed=load_seed())
    try:
        gh = w.github
        factory = gh.create_repo("acme", "factory", "admin", auto_init=True)
        gh.write_files(
            factory,
            "admin",
            "main",
            [
                (
                    "evaluations/judges/task_compliance/judge.yml",
                    b"agents: [implementer]\n",
                ),
                (
                    "evaluations/judges/review_quality/judge.yml",
                    b"agents: [reviewer]\n",
                ),
            ],
            "Judges",
        )
        api = gh.repos["acme/api-service"]
        started = time.time()
        triaged = gh.create_issue(
            api, "acme-factory", "Cap GET /tasks with limit", "1 to 50; 400 otherwise."
        )
        before = api.bare.ref_sha("main")
        implementer_began = time.time()
        time.sleep(2)  # git reads commit times to the second
        gh.create_branch(api, "dana", "later", "main")
        gh.write_files(
            api, "dana", "later", [("later.txt", b"after the run started\n")], "Later"
        )
        gh.merge_pull(
            api,
            gh.create_pull(api, "dana", "Later", "later", "main", ""),
            "dana",
            bypass_protection=True,
        )
        case = {
            "id": 11,
            "automation": "slack-app-mentions",
            "agent": "foreman",
            "started_at": started,
            "provider": "slack",
            "event": "app_mention",
            "payload": {"event": {"text": "add limit"}},
            "version": "f44aa89",
            "pull_requests": [
                {"repository": "acme/api-service", "number": 6, "relation": "opened"}
            ],
            "sessions": [
                {
                    "agent": "foreman",
                    "session_id": "S",
                    "prompt": "Someone mentioned the factory...",
                },
                {"agent": "reviewer", "session_id": "sub_r", "prompt": "Review #6"},
                {
                    "agent": "implementer",
                    "session_id": "sub_i",
                    "started_at": implementer_began,
                    "prompt": f"Implement acme/api-service#{triaged.number}: limit (http://w/acme/api-service/issues/{triaged.number})",
                    "opened": ["acme/api-service#6"],
                },
            ],
        }
        made = export_benchmark(
            w,
            "acme/factory",
            case,
            suite="api-features",
            session_id=None,
            actor="priya",
        )
        pull = factory.issues[made["number"]]
        assert pull.user == "priya" and made["scenario"] == "run-11-implementer"
        branch = pull.pull.head
        suite = yaml.safe_load(
            factory.bare.blob(branch, "evaluations/benchmarks/api-features.yml")[1]
        )
        [scenario] = suite["scenarios"]
        # The implementer's task: the session that opened the pull request (the reviewer's is judged too).
        assert (scenario["agent"], scenario["prompt"], scenario["judges"]) == (
            "implementer",
            f"Implement acme/api-service#{triaged.number}: limit (http://w/acme/api-service/issues/{triaged.number})",
            ["task_compliance"],
        )
        assert scenario["from_run"]["commit"] == before
        fixture = "evaluations/fixtures/workspaces/bench-run-11-implementer"
        assert factory.bare.blob(branch, f"{fixture}/README.md") is not None
        assert (
            factory.bare.blob(branch, f"{fixture}/later.txt") is None
        )  # the repository as it was
        github = yaml.safe_load(
            factory.bare.blob(
                branch, "evaluations/fixtures/github/bench-run-11-implementer.yml"
            )[1]
        )
        assert github["repository"] == "acme/api-service"
        # The issue its text names, at its number, as it was: the scenario's workplace has it.
        [issue] = github["issues"]
        assert (issue["number"], issue["title"]) == (
            triaged.number,
            "Cap GET /tasks with limit",
        )
        # Main does not have it until it is merged; the same task twice is refused.
        gh.merge_pull(factory, pull, "dana", bypass_protection=True)
        with pytest.raises(BenchmarkError, match="already has"):
            export_benchmark(
                w,
                "acme/factory",
                case,
                suite="api-features",
                session_id=None,
                actor="priya",
            )
        with pytest.raises(BenchmarkError, match="lowercase"):
            export_benchmark(
                w,
                "acme/factory",
                case,
                suite="API Features",
                session_id=None,
                actor="priya",
            )
    finally:
        w.close()
