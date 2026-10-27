"""GitHub Actions, run on this machine.

A repository's workflows (``.github/workflows/*.yml`` at the pushed commit)
run as GitHub runs them, simplified to one machine: a job's ``container``
and ``services`` are ignored, and its steps run here, in a fresh clone of
the commit. ``runs-on`` picks one of two runners: GitHub's hosted ones
(``ubuntu-latest`` and the rest) are this machine, or a locked-down
container per job; ``self-hosted`` is the workplace's self-hosted runner,
for the repositories an admin allows (as GitHub's runner groups do), with
Docker and what the admin gave it (the factory's image and its model
sign-in, for the factory's evaluations). What runs:

- Triggers: ``push``, ``pull_request`` (``opened``, ``synchronize``,
  ``reopened``) with ``branches`` / ``branches-ignore`` / ``paths`` /
  ``paths-ignore``, and ``workflow_dispatch``.
- Jobs, in ``needs`` order, each ``strategy.matrix`` combination its own job;
  ``if``, ``env``, ``defaults.run``, ``timeout-minutes``,
  ``continue-on-error``.
- ``run`` steps in ``bash`` (``-eo pipefail``), ``sh`` or ``python``, with
  ``working-directory``, ``env``, ``GITHUB_OUTPUT``, ``GITHUB_ENV`` and
  ``GITHUB_PATH``; ``${{ }}`` expressions over ``github``, ``env``,
  ``matrix``, ``steps``, ``needs``, ``inputs``, ``runner`` and ``job``.
- ``uses`` steps have local stand-ins: ``actions/checkout`` (the job's
  clone is the commit), ``actions/setup-python`` (this machine's Python, or
  ``uv``'s for a version), ``actions/setup-node`` (this machine's Node);
  others (caches, artifacts, marketplace actions) are skipped, and the log
  says so.

Each job runs in a space of its own (:mod:`.spaces`): on this machine, as
the user running the workplace with its tools (the machine's Python, Node
and whatever else is installed), or in a throwaway container per job, for a
workplace other people push to.
"""

from __future__ import annotations

import itertools
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import yaml

from .spaces import ContainerSettings, DockerSpace, HostSpace, Space

if TYPE_CHECKING:
    from .state import GitHubState, Job, Repo, WorkflowRun

__all__ = ["ActionsRunner", "Workflow", "evaluate", "workflows_at"]

WORKFLOWS_DIR = ".github/workflows"
DEFAULT_STEP_TIMEOUT = 30 * 60
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)


@dataclass
class Workflow:
    path: str
    name: str
    doc: Dict[str, Any]

    @property
    def on(self) -> Dict[str, Any]:
        # YAML 1.1 reads a bare ``on:`` key as True.
        raw = self.doc.get("on", self.doc.get(True))
        if isinstance(raw, str):
            return {raw: {}}
        if isinstance(raw, list):
            return {str(e): {} for e in raw}
        return {str(k): (v or {}) for k, v in (raw or {}).items()}


def workflows_at(repo: "Repo", sha: str) -> List[Workflow]:
    """The workflows at ``sha`` (none when it has no ``.github/workflows``)."""
    entries = repo.bare.tree(sha, WORKFLOWS_DIR) or []
    found = []
    for kind, name, *_ in sorted(entries, key=lambda e: e[1]):
        if kind != "file" or not name.endswith((".yml", ".yaml")):
            continue
        blob = repo.bare.blob(sha, f"{WORKFLOWS_DIR}/{name}")
        if blob is None:
            continue
        path = f"{WORKFLOWS_DIR}/{name}"
        try:
            doc = yaml.safe_load(blob[1].decode(errors="replace")) or {}
        except yaml.YAMLError as exc:
            doc = {"name": name, "_error": f"{path} is not valid YAML: {exc}"}
        if not isinstance(doc, dict):
            doc = {"name": name, "_error": f"{path} is not a workflow"}
        found.append(Workflow(path=path, name=str(doc.get("name") or path), doc=doc))
    return found


def _glob(pattern: str) -> re.Pattern:
    """GitHub's filter patterns: ``*`` stops at ``/``, ``**`` does not."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def _filtered(
    values: List[str],
    include: Optional[List[str]],
    exclude: Optional[List[str]],
    *,
    any_match: bool,
) -> bool:
    if include is not None:
        hits = [
            v
            for v in values
            if any(_glob(p).match(v) for p in include if not p.startswith("!"))
            and not any(_glob(p[1:]).match(v) for p in include if p.startswith("!"))
        ]
        return bool(hits) if any_match else len(hits) == len(values)
    if exclude is not None:
        kept = [v for v in values if not any(_glob(p).match(v) for p in exclude)]
        return bool(kept)
    return True


def triggered(
    workflow: Workflow,
    event: str,
    branch: str,
    paths: Optional[List[str]] = None,
    action: str = "",
) -> bool:
    """Whether ``event`` (on ``branch``, changing ``paths``) runs ``workflow``."""
    filters = workflow.on.get(event)
    if filters is None:
        return False
    if not isinstance(filters, dict):
        filters = {}
    if event == "pull_request":
        types = filters.get("types") or ["opened", "synchronize", "reopened"]
        if action and action not in types:
            return False
    as_list = lambda key: (
        [str(x) for x in filters[key]] if key in filters else None
    )  # noqa: E731
    if not _filtered(
        [branch], as_list("branches"), as_list("branches-ignore"), any_match=True
    ):
        return False
    if paths is not None and (
        as_list("paths") is not None or as_list("paths-ignore") is not None
    ):
        if not paths:
            return False
        if not _filtered(
            paths, as_list("paths"), as_list("paths-ignore"), any_match=True
        ):
            return False
    return True


def expand_jobs(
    workflow: Workflow,
) -> List[Tuple[str, Dict[str, Any], Dict[str, Any], str]]:
    """``(job id, job, matrix values, display name)``: one for each matrix combination."""
    out = []
    for job_id, job in (workflow.doc.get("jobs") or {}).items():
        job = job or {}
        matrix = ((job.get("strategy") or {}).get("matrix")) or {}
        combos: List[Dict[str, Any]] = [{}]
        if isinstance(matrix, dict) and matrix:
            axes = {
                k: v
                for k, v in matrix.items()
                if k not in ("include", "exclude") and isinstance(v, list)
            }
            if axes:
                combos = [
                    dict(zip(axes, values))
                    for values in itertools.product(*axes.values())
                ]
            elif matrix.get("include"):
                # Only includes: each is a combination of its own.
                combos = []
            for ex in matrix.get("exclude") or []:
                combos = [
                    c for c in combos if not all(c.get(k) == v for k, v in ex.items())
                ]
            for inc in matrix.get("include") or []:
                matched = [
                    c
                    for c in combos
                    if all(c.get(k) == v for k, v in inc.items() if k in c)
                ]
                if matched and any(k in c for c in combos for k in inc):
                    for c in matched:
                        c.update({k: v for k, v in inc.items() if k not in axes})
                else:
                    combos.append(dict(inc))
        for combo in combos:
            base = str(job.get("name") or job_id)
            name = base
            if combo:
                name = (
                    _substitute(base, {"matrix": combo})
                    if "${{" in base
                    else f"{base} ({', '.join(str(v) for v in combo.values())})"
                )
            out.append((str(job_id), job, combo, name))
    return out


# -- expressions -------------------------------------------------------------------

_TOKEN = re.compile(
    r"""\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<str>'(?:[^']|'')*')|(?P<op>==|!=|<=|>=|&&|\|\||[!<>()\[\],.*])|(?P<name>[A-Za-z_][A-Za-z0-9_-]*))"""
)


def _tokens(text: str) -> List[Tuple[str, str]]:
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"cannot read expression {text!r}")
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


class _Parser:
    def __init__(
        self, text: str, context: Dict[str, Any], functions: Dict[str, Callable]
    ):
        self.t = _tokens(text)
        self.i = 0
        self.context = context
        self.functions = functions

    def peek(self, value: Optional[str] = None) -> bool:
        return self.i < len(self.t) and (value is None or self.t[self.i][1] == value)

    def take(self) -> Tuple[str, str]:
        token = self.t[self.i]
        self.i += 1
        return token

    def parse(self) -> Any:
        value = self.or_()
        if self.i != len(self.t):
            raise ValueError("unexpected input in expression")
        return value

    def or_(self) -> Any:
        left = self.and_()
        while self.peek("||"):
            self.take()
            right = self.and_()
            left = left or right
        return left

    def and_(self) -> Any:
        left = self.compare()
        while self.peek("&&"):
            self.take()
            right = self.compare()
            left = left and right
        return left

    def compare(self) -> Any:
        left = self.unary()
        while self.i < len(self.t) and self.t[self.i][1] in (
            "==",
            "!=",
            "<",
            ">",
            "<=",
            ">=",
        ):
            op = self.take()[1]
            right = self.unary()
            a, b = _loose(left), _loose(right)
            left = (
                {
                    "==": a == b,
                    "!=": a != b,
                    "<": a < b,
                    ">": a > b,
                    "<=": a <= b,
                    ">=": a >= b,
                }[op]
                if op in ("==", "!=")
                or (isinstance(a, (int, float)) and isinstance(b, (int, float)))
                else False
            )
        return left

    def unary(self) -> Any:
        if self.peek("!"):
            self.take()
            return not _truthy(self.unary())
        return self.postfix()

    def postfix(self) -> Any:
        value = self.primary()
        while self.peek(".") or self.peek("["):
            if self.take()[1] == ".":
                kind, name = self.take()
                if name == "*":
                    value = list(value.values()) if isinstance(value, dict) else value
                else:
                    value = _get(value, name)
            else:
                key = self.or_()
                self.take()  # ]
                value = _get(value, key)
        return value

    def primary(self) -> Any:
        kind, value = self.take()
        if kind == "num":
            return float(value) if "." in value else int(value)
        if kind == "str":
            return value[1:-1].replace("''", "'")
        if value == "(":
            inner = self.or_()
            self.take()
            return inner
        if kind == "name":
            lowered = value.lower()
            if lowered in ("true", "false"):
                return lowered == "true"
            if lowered == "null":
                return None
            if self.peek("("):
                self.take()
                args = []
                while not self.peek(")"):
                    args.append(self.or_())
                    if self.peek(","):
                        self.take()
                self.take()
                fn = self.functions.get(lowered)
                if fn is None:
                    raise ValueError(f"unknown function {value}")
                return fn(*args)
            return self.context.get(value)
        raise ValueError(f"unexpected {value!r} in expression")


def _get(value: Any, key: Any) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        lowered = {str(k).lower(): v for k, v in value.items()}
        return lowered.get(str(key).lower())
    if isinstance(value, list) and isinstance(key, (int, float)):
        return value[int(key)] if 0 <= int(key) < len(value) else None
    return None


def _truthy(value: Any) -> bool:
    return value not in (None, False, 0, "", 0.0)


def _loose(value: Any) -> Any:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, bool):
        return int(value)
    return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value)
    return str(value)


def _functions(status: Callable[[], str]) -> Dict[str, Callable]:
    import json

    return {
        "success": lambda: status() == "success",
        "failure": lambda: status() == "failure",
        "always": lambda: True,
        "cancelled": lambda: status() == "cancelled",
        "contains": lambda hay, needle: (
            (str(needle).lower() in str(hay).lower())
            if isinstance(hay, str)
            else any(_loose(x) == _loose(needle) for x in (hay or []))
        ),
        "startswith": lambda s, p: str(s).lower().startswith(str(p).lower()),
        "endswith": lambda s, p: str(s).lower().endswith(str(p).lower()),
        "format": lambda f, *a: re.sub(
            r"\{(\d+)\}", lambda m: _text(a[int(m.group(1))]), str(f)
        ),
        "join": lambda items, sep=",": str(sep).join(_text(x) for x in (items or [])),
        "tojson": lambda v: json.dumps(v, indent=2),
        "fromjson": lambda s: json.loads(s),
        "hashfiles": lambda *patterns: "",
    }


def evaluate(
    expression: str,
    context: Dict[str, Any],
    status: Callable[[], str] = lambda: "success",
) -> Any:
    return _Parser(expression, context, _functions(status)).parse()


def _substitute(
    text: str, context: Dict[str, Any], status: Callable[[], str] = lambda: "success"
) -> str:
    return _EXPRESSION.sub(lambda m: _text(evaluate(m.group(1), context, status)), text)


def condition(expr: Any, context: Dict[str, Any], status: Callable[[], str]) -> bool:
    """A job's or step's ``if``: without a status function, ``success()`` is implied."""
    if expr is None:
        return status() == "success"
    if isinstance(expr, bool):
        return expr and status() == "success"
    text = str(expr).strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", text, re.S)
    if m:
        text = m.group(1)
    if not re.search(r"\b(success|failure|always|cancelled)\s*\(", text):
        text = f"success() && ({text})"
    return _truthy(evaluate(text, context, status))


# -- running ------------------------------------------------------------------------


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class _Cancelled(Exception):
    pass


class ActionsRunner:
    """Runs workflow runs, one thread each; jobs and steps fill the run as they go."""

    def __init__(
        self,
        state: "GitHubState",
        base_url: Callable[[], str],
        container: Optional[ContainerSettings] = None,
        self_hosted: Optional[ContainerSettings] = None,
        *,
        instance: str = "",
    ):
        """``container``: run each job in a container made so; on this machine without one.
        ``self_hosted``: the self-hosted runner's containers (with ``container``;
        without it, self-hosted jobs run on this machine too). ``instance``: the
        workplace's id, on its job containers."""
        self.state = state
        self.base_url = base_url
        self.container = container
        self.self_hosted = self_hosted
        self.instance = instance
        if container is not None:
            # A previous run of this workplace stopped mid-job: its containers stay
            # behind (their runs end as interrupted). Another workplace's are not ours.
            DockerSpace.remove_leftovers(container.docker, instance)
        self._cancel: Dict[int, threading.Event] = {}
        self._procs: Dict[int, Tuple[Space, subprocess.Popen]] = {}
        self._threads: List[threading.Thread] = []

    def _space(self, repo: "Repo", job_id: int, spec: Dict[str, Any]) -> Space:
        """Where the job runs, by its ``runs-on``; a :class:`NoRunner` saying why if nowhere."""
        if "self-hosted" in runs_on(spec):
            if not repo.self_hosted:
                return NoRunner(
                    f"{repo.full_name} may not use the self-hosted runner: an admin allows it "
                    "in the repository's settings"
                )
            if self.container is None:
                return HostSpace(job_id, own_environment=True)
            if self.self_hosted is None:
                return NoRunner(
                    "This workplace has no self-hosted runner (serve --self-hosted-image)"
                )
            return DockerSpace(job_id, self.self_hosted, self.instance)
        return (
            DockerSpace(job_id, self.container, self.instance)
            if self.container
            else HostSpace(job_id)
        )

    def start(
        self,
        repo: "Repo",
        run: "WorkflowRun",
        workflow: Workflow,
        context: Dict[str, Any],
    ) -> None:
        cancel = threading.Event()
        self._cancel[run.id] = cancel
        thread = threading.Thread(
            target=self._run,
            args=(repo, run, workflow, context, cancel),
            daemon=True,
            name=f"workflow-run-{run.id}",
        )
        self._threads.append(thread)
        thread.start()

    def cancel(self, run_id: int) -> bool:
        event = self._cancel.get(run_id)
        if event is None or event.is_set():
            return False
        event.set()
        running = self._procs.get(run_id)
        if running is not None:
            running[0].kill(running[1])
        return True

    def wait(self, run_ids: List[int], timeout: float = 30.0) -> None:
        """Until those runs have ended (cancel them first)."""
        deadline = time.time() + timeout
        for thread in list(self._threads):
            if thread.name in {f"workflow-run-{i}" for i in run_ids}:
                thread.join(max(0.0, deadline - time.time()))

    def stop(self, timeout: float = 30.0) -> None:
        """Cancel every run, and wait for them to say so."""
        for run_id in list(self._cancel):
            self.cancel(run_id)
        deadline = time.time() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.time()))
        self._threads = [t for t in self._threads if t.is_alive()]

    # -- a run ---------------------------------------------------------------------

    def _run(
        self,
        repo: "Repo",
        run: "WorkflowRun",
        workflow: Workflow,
        github: Dict[str, Any],
        cancel: threading.Event,
    ) -> None:
        state = self.state
        state.run_started(repo, run)
        results: Dict[str, Dict[str, Any]] = {}
        try:
            if workflow.doc.get("_error"):
                for job in run.jobs:
                    state.job_finished(
                        repo,
                        run,
                        job,
                        "failure",
                        f"{_stamp()} ##[error]{workflow.doc['_error']}\n",
                        [],
                    )
                state.run_finished(repo, run, "failure")
                return
            planned = expand_jobs(workflow)
            by_id: Dict[str, List[Tuple["Job", Dict[str, Any], Dict[str, Any]]]] = {}
            for (job_id, spec, matrix, _), job in zip(planned, run.jobs):
                by_id.setdefault(job_id, []).append((job, spec, matrix))
            for job_id in _order(workflow.doc.get("jobs") or {}):
                needs = _needs((workflow.doc.get("jobs") or {}).get(job_id) or {})
                for job, spec, matrix in by_id.get(job_id, []):
                    if cancel.is_set():
                        state.job_finished(repo, run, job, "cancelled", "", [])
                        continue
                    need_results = {
                        n: results.get(n, {"result": "skipped", "outputs": {}})
                        for n in needs
                    }
                    failed_need = any(
                        r["result"] != "success" for r in need_results.values()
                    )
                    context = self._context(
                        github, workflow, spec, matrix, need_results
                    )
                    status = (lambda: "failure") if failed_need else (lambda: "success")
                    try:
                        go = condition(spec.get("if"), context, status)
                    except ValueError as exc:
                        state.job_finished(
                            repo,
                            run,
                            job,
                            "failure",
                            f"{_stamp()} ##[error]The job's if: {exc}\n",
                            [],
                        )
                        results[job_id] = {"result": "failure", "outputs": {}}
                        continue
                    if not go:
                        state.job_finished(repo, run, job, "skipped", "", [])
                        results.setdefault(job_id, {"result": "skipped", "outputs": {}})
                        continue
                    conclusion, outputs = self._job(
                        repo, run, job, spec, context, cancel
                    )
                    previous = results.get(job_id, {}).get("result")
                    results[job_id] = {
                        "result": (
                            conclusion if previous in (None, "success") else previous
                        ),
                        "outputs": outputs,
                    }
            conclusions = [j.conclusion for j in run.jobs]
            final = (
                "cancelled"
                if cancel.is_set()
                else (
                    "failure"
                    if "failure" in conclusions
                    else (
                        "cancelled"
                        if "cancelled" in conclusions
                        else (
                            "skipped"
                            if conclusions and all(c == "skipped" for c in conclusions)
                            else "success"
                        )
                    )
                )
            )
            state.run_finished(repo, run, final)
        except Exception as exc:  # the runner itself failed: the run says so
            for job in run.jobs:
                if job.status != "completed":
                    state.job_finished(
                        repo,
                        run,
                        job,
                        "failure",
                        f"{_stamp()} ##[error]The runner failed: {exc}\n",
                        [],
                    )
            state.run_finished(repo, run, "failure")
        finally:
            self._cancel.pop(run.id, None)

    def _context(
        self,
        github: Dict[str, Any],
        workflow: Workflow,
        spec: Dict[str, Any],
        matrix: Dict[str, Any],
        needs: Dict[str, Any],
    ) -> Dict[str, Any]:
        base = {
            "github": github,
            "matrix": matrix,
            "needs": needs,
            "inputs": github.get("event", {}).get("inputs") or {},
            "runner": {
                "os": "Linux",
                "arch": "X64",
                "name": "workplace",
                "temp": tempfile.gettempdir(),
            },
            "secrets": {},
            "vars": {},
            "strategy": {},
            "job": {"status": "success"},
            "steps": {},
        }
        env: Dict[str, str] = {}
        for layer in (workflow.doc.get("env") or {}, spec.get("env") or {}):
            for key, value in layer.items():
                env[str(key)] = _substitute(_text(value), {**base, "env": env})
        base["env"] = env
        return base

    def _job(
        self,
        repo: "Repo",
        run: "WorkflowRun",
        job: "Job",
        spec: Dict[str, Any],
        context: Dict[str, Any],
        cancel: threading.Event,
    ) -> Tuple[str, Dict[str, str]]:
        from .state import Step

        state = self.state
        space = self._space(repo, job.id, spec)
        log: List[str] = []
        steps: List[Step] = []
        outputs: Dict[str, str] = {}

        def write(line: str) -> None:
            log.append(f"{_stamp()} {line}")
            state.job_log(repo, run, job, "\n".join(log) + "\n")

        state.job_started(repo, run, job)
        started = time.time()
        steps.append(Step(1, "Set up job", "completed", "success", started, started))
        write("##[group]Set up job")
        write(f"Runner: {space.describe}")
        write("##[endgroup]")
        if isinstance(space, NoRunner):
            write(f"##[error]{space.reason}")
            state.job_finished(repo, run, job, "failure", "\n".join(log) + "\n", steps)
            return "failure", outputs
        try:
            failed = space.prepare(repo.bare.path, run.head_sha)
            if failed is not None:
                write(f"##[error]Could not check out {run.head_sha[:7]}: {failed}")
                state.job_finished(
                    repo, run, job, "failure", "\n".join(log) + "\n", steps
                )
                return "failure", outputs
            return self._steps(
                repo,
                run,
                job,
                spec,
                context,
                cancel,
                space,
                write,
                log,
                steps,
                outputs,
                started,
            )
        finally:
            space.close()

    def _steps(
        self,
        repo: "Repo",
        run: "WorkflowRun",
        job: "Job",
        spec: Dict[str, Any],
        context: Dict[str, Any],
        cancel: threading.Event,
        space: Space,
        write: Callable[[str], None],
        log: List[str],
        steps: List[Any],
        outputs: Dict[str, str],
        started: float,
    ) -> Tuple[str, Dict[str, str]]:
        from .state import Step

        state = self.state
        status = ["success"]
        checkout = space.checkout
        files = {name: name for name in ("output", "env", "path")}
        for name in files.values():
            space.write(name, "")
        extra_path: List[str] = []
        github = context["github"]
        env_base = {
            **space.base_env(),
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "GITHUB_WORKSPACE": checkout,
            "GITHUB_SHA": github["sha"],
            "GITHUB_REF": github["ref"],
            "GITHUB_REF_NAME": github["ref_name"],
            "GITHUB_REPOSITORY": github["repository"],
            "GITHUB_REPOSITORY_OWNER": github["repository_owner"],
            "GITHUB_ACTOR": github["actor"],
            "GITHUB_EVENT_NAME": github["event_name"],
            "GITHUB_RUN_ID": str(run.id),
            "GITHUB_RUN_NUMBER": str(run.run_number),
            "GITHUB_JOB": job.name,
            "GITHUB_SERVER_URL": github["server_url"],
            "GITHUB_HEAD_REF": github.get("head_ref") or "",
            "GITHUB_BASE_REF": github.get("base_ref") or "",
            "RUNNER_OS": "Linux",
            "RUNNER_ARCH": "X64",
            "RUNNER_TEMP": space.root,
            "GITHUB_OUTPUT": space.path("output"),
            "GITHUB_ENV": space.path("env"),
            "GITHUB_PATH": space.path("path"),
            **context["env"],
        }
        defaults = ((spec.get("defaults") or {}).get("run")) or {}
        job_deadline = started + float(spec.get("timeout-minutes") or 360) * 60
        job_status = lambda: status[0]  # noqa: E731

        for index, raw in enumerate(spec.get("steps") or [], start=2):
            raw = raw or {}
            step_id = str(raw.get("id") or f"__step{index}")
            label = str(
                raw.get("name")
                or (
                    f"Run {raw['uses']}"
                    if raw.get("uses")
                    else f"Run {str(raw.get('run', '')).strip().splitlines()[0] if raw.get('run') else 'step'}"
                )
            )
            try:
                label = _substitute(label, context, job_status)
            except ValueError:
                pass
            step = Step(index, label, "in_progress", None, time.time(), None)
            steps.append(step)
            state.job_steps(repo, run, job, steps)
            if cancel.is_set():
                step.status, step.conclusion, step.completed_at = (
                    "completed",
                    "cancelled",
                    time.time(),
                )
                status[0] = "cancelled"
                continue
            try:
                go = condition(raw.get("if"), context, job_status)
            except ValueError as exc:
                write(f"##[error]{label}: if: {exc}")
                step.status, step.conclusion, step.completed_at = (
                    "completed",
                    "failure",
                    time.time(),
                )
                status[0] = "failure"
                continue
            if not go:
                step.status, step.conclusion, step.completed_at = (
                    "completed",
                    "skipped",
                    time.time(),
                )
                context["steps"][step_id] = {
                    "outputs": {},
                    "outcome": "skipped",
                    "conclusion": "skipped",
                }
                continue
            write(f"##[group]{label}")
            env = dict(env_base)
            env.update(_read_kv(space.read("env")))
            if extra_path:
                env["PATH"] = os.pathsep.join(extra_path + [env.get("PATH", "")])
            try:
                for key, value in (raw.get("env") or {}).items():
                    env[str(key)] = _substitute(
                        _text(value), {**context, "env": env}, job_status
                    )
                context["env"] = {
                    **context["env"],
                    **{k: env[k] for k in (raw.get("env") or {})},
                }
                if raw.get("uses"):
                    outcome = self._uses(
                        str(raw["uses"]),
                        raw.get("with") or {},
                        context,
                        env,
                        extra_path,
                        write,
                        job_status,
                        space,
                    )
                else:
                    outcome = self._run_step(
                        raw,
                        defaults,
                        context,
                        env,
                        space,
                        job_deadline,
                        run.id,
                        write,
                        job_status,
                        cancel,
                    )
            except ValueError as exc:
                write(f"##[error]{exc}")
                outcome = "failure"
            write("##[endgroup]")
            step_outputs = _read_kv(space.read("output"))
            space.write("output", "")
            extra_path[:0] = [
                p
                for p in space.read("path").splitlines()
                if p.strip() and p not in extra_path
            ]
            space.write("path", "")
            if outcome == "failure" and raw.get("continue-on-error"):
                write(
                    f"##[warning]{label} failed; continue-on-error lets the job go on"
                )
                conclusion = "success"
            else:
                conclusion = outcome
            context["steps"][step_id] = {
                "outputs": step_outputs,
                "outcome": outcome,
                "conclusion": conclusion,
            }
            step.status, step.conclusion, step.completed_at = (
                "completed",
                conclusion,
                time.time(),
            )
            if conclusion == "failure":
                status[0] = "failure"
            if conclusion == "cancelled":
                status[0] = "cancelled"
            state.job_steps(repo, run, job, steps)

        for key, value in (spec.get("outputs") or {}).items():
            try:
                outputs[str(key)] = _substitute(_text(value), context, job_status)
            except ValueError:
                outputs[str(key)] = ""
        done = time.time()
        steps.append(
            Step(len(steps) + 1, "Complete job", "completed", "success", done, done)
        )
        write(f"Job {'succeeded' if status[0] == 'success' else status[0]}")
        state.job_finished(repo, run, job, status[0], "\n".join(log) + "\n", steps)
        return status[0], outputs

    def _run_step(
        self,
        raw: Dict[str, Any],
        defaults: Dict[str, Any],
        context: Dict[str, Any],
        env: Dict[str, str],
        space: Space,
        job_deadline: float,
        run_id: int,
        write: Callable[[str], None],
        status: Callable[[], str],
        cancel: threading.Event,
    ) -> str:
        script = _substitute(str(raw.get("run") or ""), context, status)
        shell = str(raw.get("shell") or defaults.get("shell") or "bash")
        directory = raw.get("working-directory") or defaults.get("working-directory")
        cwd = (
            f"{space.checkout}/{_substitute(str(directory), context, status)}"
            if directory
            else space.checkout
        )
        for line in script.rstrip("\n").splitlines():
            write(line)
        name = f"step-{time.time_ns()}"
        space.write(name, script)
        path = space.path(name)
        if shell == "bash":
            argv = ["bash", "--noprofile", "--norc", "-eo", "pipefail", path]
        elif shell == "sh":
            argv = ["sh", "-e", path]
        elif shell in ("python", "python3"):
            argv = [space.which("python3", env.get("PATH")) or "python3", path]
        elif shell == "pwsh":
            write("##[error]shell: pwsh is not available on this runner")
            return "failure"
        else:
            argv = [part.replace("{0}", path) for part in shell.split()]
        timeout = min(
            float(raw.get("timeout-minutes") or DEFAULT_STEP_TIMEOUT / 60) * 60,
            max(1.0, job_deadline - time.time()),
        )
        try:
            proc = space.popen(argv, cwd, env)
        except OSError as exc:
            write(f"##[error]{exc}")
            return "failure"
        self._procs[run_id] = (space, proc)
        timed_out = threading.Event()

        def out_of_time() -> None:
            timed_out.set()
            space.kill(proc)

        timer = threading.Timer(timeout, out_of_time)
        timer.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                write(line.rstrip("\n"))
            code = proc.wait()
        finally:
            timer.cancel()
            self._procs.pop(run_id, None)
        if cancel.is_set():
            write("##[error]The run was cancelled")
            return "cancelled"
        if timed_out.is_set():
            write(f"##[error]The step ran out of time ({int(timeout)} seconds)")
            return "failure"
        if code != 0:
            write(f"##[error]Process completed with exit code {code}.")
            return "failure"
        return "success"

    def _uses(
        self,
        uses: str,
        inputs: Dict[str, Any],
        context: Dict[str, Any],
        env: Dict[str, str],
        extra_path: List[str],
        write: Callable[[str], None],
        status: Callable[[], str],
        space: Space,
    ) -> str:
        action = uses.split("@", 1)[0].lower()
        given = {
            str(k): _substitute(_text(v), context, status) for k, v in inputs.items()
        }
        if action == "actions/checkout":
            write(
                f"The job's workspace is already {context['github']['sha'][:7]} ({context['github']['repository']})"
            )
            return "success"
        if action == "actions/setup-python":
            version = given.get("python-version", "").strip()
            python = None
            if version:
                uv = space.which("uv", env.get("PATH"))
                if uv:
                    found = space.run([uv, "python", "find", version])
                    if found.returncode != 0:
                        write(f"Installing Python {version} with uv")
                        space.run([uv, "python", "install", version])
                        found = space.run([uv, "python", "find", version])
                    if found.returncode == 0:
                        python = found.stdout.strip()
            python = python or space.which("python3", env.get("PATH"))
            if not python:
                write("##[error]No Python on this runner")
                return "failure"
            # A virtual environment, as setup-python's interpreter is the job's own: pip installs go there.
            venv = f"{space.root}/python-{abs(hash(python))}"
            if not space.exists(venv):
                made = space.run([python, "-m", "venv", venv])
                if made.returncode != 0:
                    write(
                        f"##[error]Could not make a Python environment with {python}: "
                        f"{((made.stderr or made.stdout).strip().splitlines() or [''])[-1]}"
                    )
                    return "failure"
            bin_dir = f"{venv}/bin"
            if space.exists(bin_dir):
                extra_path.insert(0, bin_dir)
                env["PATH"] = os.pathsep.join([bin_dir, env.get("PATH", "")])
            version_out = space.run(
                [f"{bin_dir}/python" if space.exists(bin_dir) else python, "--version"]
            ).stdout.strip()
            write(f"Using {version_out} ({python})")
            return "success"
        if action == "actions/setup-node":
            node = space.which("node", env.get("PATH"))
            if not node:
                write("##[error]No Node.js on this runner")
                return "failure"
            out = space.run([node, "--version"]).stdout.strip()
            wanted = given.get("node-version")
            write(
                f"Using Node.js {out} ({node})"
                + (f"; the workflow asks for {wanted}" if wanted else "")
            )
            return "success"
        write(f"Skipped: {uses} does not run on this runner")
        return "success"


class NoRunner(Space):
    """No runner the job may use: it fails, saying why (GitHub would queue it)."""

    describe = "none"

    def __init__(self, reason: str):
        self.reason = reason


def runs_on(spec: Dict[str, Any]) -> List[str]:
    """The labels a job's ``runs-on`` asks for (a label, a list, or ``{labels}``)."""
    value = spec.get("runs-on")
    if isinstance(value, dict):
        value = value.get("labels")
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value] if isinstance(value, list) else []


def _needs(spec: Dict[str, Any]) -> List[str]:
    needs = spec.get("needs") or []
    return [str(needs)] if isinstance(needs, str) else [str(n) for n in needs]


def _order(jobs: Dict[str, Any]) -> List[str]:
    """Job ids, each after the jobs it needs (a cycle runs in file order)."""
    done: List[str] = []
    pending = [str(j) for j in jobs]
    while pending:
        ready = [
            j
            for j in pending
            if all(n in done or n not in jobs for n in _needs(jobs[j] or {}))
        ]
        if not ready:
            ready = pending[:1]
        for j in ready:
            done.append(j)
            pending.remove(j)
    return done


def _read_kv(text: str) -> Dict[str, str]:
    """``GITHUB_OUTPUT`` / ``GITHUB_ENV``: ``key=value`` lines, or ``key<<DELIM`` blocks."""
    out: Dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            key, delim = line.split("<<", 1)
            value = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                value.append(lines[i])
                i += 1
            out[key.strip()] = "\n".join(value)
        elif "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value
        i += 1
    return out


def job_names(workflow: Workflow) -> List[str]:
    if workflow.doc.get("_error"):
        return [workflow.name]
    return [name for *_, name in expand_jobs(workflow)] or [workflow.name]


def changed_paths(repo: "Repo", base: Optional[str], head: str) -> List[str]:
    if not base:
        commit = repo.bare.commit(head)
        if commit is None or not commit.parents:
            return (
                [path for path in repo.bare.files(head)]
                if hasattr(repo.bare, "files")
                else []
            )
        base = commit.parents[0]
    return [c.filename for c in repo.bare.changes(base, head, three_dot=False)] + [
        c.previous_filename
        for c in repo.bare.changes(base, head, three_dot=False)
        if c.previous_filename
    ]


__all__ += ["changed_paths", "condition", "expand_jobs", "job_names", "triggered"]
