"""A benchmark task from a real run: a pull request adding it to the factory's suites.

A factory engineer flags a run worth measuring configurations on (a hard
one, or one of a class of work). The task is one session of it, the run's
own or an agent it called (the implementer's work, say). It is made
reproducible:

- the scenario's prompt is what that session was given;
- its fixture is the repository it worked in, as it was when that session
  began (``evaluations/fixtures/workspaces/<case>``);
- its GitHub fixture names that repository, with the issues the task needs
  as they were when the run started: the one it came from, and the ones its
  text names (``acme/api-service#5``, or their URLs), at their numbers
  (``evaluations/fixtures/github/<case>.yml``);
- the judges are the factory's that judge that agent.

It goes in ``evaluations/benchmarks/<suite>.yml``, a set ``eval scenarios run
--set ... --matrix`` runs across configurations, never as the pull requests'
checks, through a pull request on the factory's repository like any other
change to it.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import yaml

if TYPE_CHECKING:
    from .server import Workplace

__all__ = ["BenchmarkError", "export_benchmark"]

#: Files bigger than this stay out of a fixture (generated assets, binaries).
MAX_FILE = 512 * 1024
_SUITE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class BenchmarkError(Exception):
    pass


def _session(
    case: Dict[str, Any], session_id: Optional[str], judged: Dict[str, List[str]]
) -> Dict[str, Any]:
    sessions = case.get("sessions") or []
    if session_id:
        found = next((s for s in sessions if s["session_id"] == session_id), None)
        if found is None:
            raise BenchmarkError(f"run {case['id']} has no session {session_id}")
        return found
    # The session that opened the run's pull request (where its work was), if a judge
    # judges it; else the first one a judge judges, the agents it called before its own.
    for s in sorted(sessions, key=lambda s: (not s.get("opened"), s is sessions[0])):
        if judged.get(s["agent"]):
            return s
    raise BenchmarkError(
        "no judge judges any of this run's agents' work: add one to evaluations/judges"
    )


def _judges(w: "Workplace", repo: Any) -> Dict[str, List[str]]:
    """Each agent's judges, from the factory repository's main."""
    out: Dict[str, List[str]] = {}
    for path in repo.bare.files(repo.default_branch):
        if not (path.startswith("evaluations/judges/") and path.endswith("/judge.yml")):
            continue
        found = repo.bare.blob(repo.default_branch, path)
        if not found:
            continue
        cfg = yaml.safe_load(found[1]) or {}
        key = path.split("/")[2]
        for agent in cfg.get("agents") or []:
            out.setdefault(str(agent), []).append(key)
    return out


def _repository(w: "Workplace", case: Dict[str, Any]) -> Any:
    names = [
        p["repository"]
        for p in case.get("pull_requests") or []
        if p["relation"] == "opened"
    ]
    names += [p["repository"] for p in case.get("pull_requests") or []]
    payload = case.get("payload") or {}
    if isinstance(payload.get("repository"), str):
        names.append(payload["repository"])
    for name in names:
        if name in w.github.repos:
            return w.github.repos[name]
    raise BenchmarkError(
        "can't tell which repository the run worked in (it opened no pull request, "
        "and its event names none)"
    )


def _commit_at(repo: Any, at: float) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at))
    found = repo.bare.commits(repo.default_branch, until=stamp, max_count=1)
    if not found:
        raise BenchmarkError(
            f"{repo.full_name} had no commit on {repo.default_branch} when the run started"
        )
    return found[0].sha


def _fixture(repo: Any, sha: str, case_name: str) -> List[Tuple[str, Optional[bytes]]]:
    files = []
    for path in repo.bare.files(sha):
        found = repo.bare.blob(sha, path)
        if found and len(found[1]) <= MAX_FILE:
            files.append(
                (f"evaluations/fixtures/workspaces/{case_name}/{path}", found[1])
            )
    return files


def _github_fixture(
    repo: Any, case: Dict[str, Any], prompt: str, started: float
) -> Dict[str, Any]:
    fixture: Dict[str, Any] = {
        "repository": repo.full_name,
        "default_branch": repo.default_branch,
    }
    numbers: List[int] = []
    payload = case.get("payload") or {}
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else None
    if issue and case.get("provider") == "github" and issue.get("number"):
        numbers.append(int(issue["number"]))
    name = re.escape(repo.full_name)
    for match in re.finditer(rf"(?:{name}#|/{name}/issues/)(\d+)", prompt or ""):
        numbers.append(int(match.group(1)))
    issues = []
    for number in dict.fromkeys(numbers):
        original = repo.issues.get(number)
        if (
            original is None
            or original.pull is not None
            or original.created_at > started
        ):
            continue  # pull requests and later issues are not the task's
        issues.append(
            {
                "number": original.number,
                "title": original.title,
                "author": original.user,
                "body": original.body,
                "labels": list(original.labels),
                "comments": [
                    {"author": c.user, "body": c.body}
                    for c in original.comments
                    if c.created_at <= started
                ],
            }
        )
    if issues:
        fixture["issues"] = issues
    return fixture


def export_benchmark(
    w: "Workplace",
    factory_repo: str,
    case: Dict[str, Any],
    *,
    suite: str,
    session_id: Optional[str],
    actor: str,
) -> Dict[str, Any]:
    """Open a pull request on ``factory_repo`` adding ``case`` (``GET .../runs/{id}/case``)
    to the ``suite`` benchmark; the pull request's number and URL."""
    if not _SUITE.match(suite or ""):
        raise BenchmarkError(
            "a suite is named in lowercase letters, digits and dashes (api-features)"
        )
    factory = w.github.repos.get(factory_repo)
    if factory is None:
        raise BenchmarkError(f"the factory's repository {factory_repo} is not here")
    judged = _judges(w, factory)
    session = _session(case, session_id, judged)
    if not session.get("prompt"):
        raise BenchmarkError(f"the {session['agent']} session kept no input to replay")
    judges = judged.get(session["agent"]) or []
    if not judges:
        raise BenchmarkError(f"no judge judges the {session['agent']} agent's work")
    repo = _repository(w, case)
    # As it was when the session began (an agent the run called began later than the run).
    started = float(session.get("started_at") or case["started_at"])
    sha = _commit_at(repo, started)
    name = f"run-{case['id']}-{session['agent']}"
    case_name = f"bench-{name}"
    suite_path = f"evaluations/benchmarks/{suite}.yml"
    existing = factory.bare.blob(factory.default_branch, suite_path)
    data = (yaml.safe_load(existing[1]) or {}) if existing else {}
    scenarios = list(data.get("scenarios") or [])
    if any(s.get("name") == name for s in scenarios if isinstance(s, dict)):
        raise BenchmarkError(f"{suite} already has {name}")
    scenarios.append(
        {
            "name": name,
            "agent": session["agent"],
            "prompt": session["prompt"],
            "github": f"{case_name}.yml",
            "fixture": case_name,
            "judges": judges,
            "measures": judges,
            "from_run": {
                "id": case["id"],
                "automation": case["automation"],
                "repository": repo.full_name,
                "commit": sha,
                "version": case.get("version"),
            },
        }
    )
    header = (
        f"# The {suite} benchmark: tasks from the factory's real runs, run across\n"
        "# configurations with `omniagents eval scenarios run --set evaluations/benchmarks/"
        f"{suite}.yml --matrix <configs.yml>`\n# (.github/workflows/benchmarks.yml). Not a pull request check.\n"
    )
    files: List[Tuple[str, Optional[bytes]]] = [
        (
            suite_path,
            (
                header
                + yaml.safe_dump(
                    {"scenarios": scenarios},
                    sort_keys=False,
                    allow_unicode=True,
                    width=100,
                )
            ).encode(),
        ),
        (
            f"evaluations/fixtures/github/{case_name}.yml",
            yaml.safe_dump(
                _github_fixture(repo, case, session["prompt"], started),
                sort_keys=False,
                allow_unicode=True,
            ).encode(),
        ),
        *_fixture(repo, sha, case_name),
    ]
    branch = f"benchmark/{suite}/{name}"
    w.github.create_branch(factory, actor, branch, factory.default_branch)
    w.github.write_files(factory, actor, branch, files, f"Benchmark {suite}: {name}")
    body = (
        f"Adds run {case['id']}'s {session['agent']} task ({case['automation']}) to the `{suite}` "
        f"benchmark: its input, `{repo.full_name}` at `{sha[:7]}` as it was when the run started, "
        f"and the judges {', '.join(judges)}.\n\nWhat it reached outside that repository (a Slack thread, "
        "a Linear issue) is not in the fixture: the task's text carries it."
    )
    pull = w.github.create_pull(
        factory,
        actor,
        f"Benchmark {suite}: run {case['id']} ({session['agent']})",
        branch,
        factory.default_branch,
        body,
    )
    return {
        "number": pull.number,
        "url": f"{w.base_url.rstrip('/')}/{factory.full_name}/pull/{pull.number}",
        "scenario": name,
    }
