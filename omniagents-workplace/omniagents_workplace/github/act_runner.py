"""Workplace lifecycle adapter for act. No workflow/action interpreter lives here."""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict

from .act_runtime import RuntimeBridge, runtime_jobs
from .act_space import (
    ACT_INSTALL,
    JOB_DOCKERFILE,
    JOB_IMAGE,
    RUNNER_IMAGE,
    ActDockerSpace,
)
from .spaces import ContainerSettings


class ActionsRunner:
    def __init__(
        self,
        state,
        base_url,
        container=None,
        self_hosted=None,
        *,
        instance="",
        quicksand=None,
        agent_profile=None,
        agent_environment=None,
    ):
        self.state, self.base_url = state, base_url
        self.container, self.self_hosted, self.instance = (
            container,
            self_hosted,
            instance,
        )
        self.quicksand, self.agent_profile, self.agent_environment = (
            quicksand,
            agent_profile,
            agent_environment,
        )
        self._cancel, self._procs, self._threads = {}, {}, []
        if container:
            ActDockerSpace.remove_leftovers(container.docker, instance)

    def start(self, repo, run, workflow, context):
        cancel = self._cancel[run.id] = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(repo, run, workflow, context, cancel),
            daemon=True,
            name=f"workflow-run-{run.id}",
        )
        self._threads.append(thread)
        thread.start()

    def cancel(self, run_id):
        event = self._cancel.get(run_id)
        if event is None or event.is_set():
            return False
        event.set()
        active = self._procs.get(run_id)
        if active:
            active[0].kill(active[1])
        return True

    def wait(self, run_ids, timeout=30):
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            if thread.name in {f"workflow-run-{i}" for i in run_ids}:
                thread.join(max(0, deadline - time.monotonic()))

    def stop(self, timeout=30):
        ids = list(self._cancel)
        for ident in ids:
            self.cancel(ident)
        self.wait(ids, timeout)
        self._threads = [t for t in self._threads if t.is_alive()]

    #: The factory's egress proxy for jobs (Workplace sets it): its
    #: environment, and the Docker network the proxy is reachable on.
    proxy_environment = None
    proxy_network = None

    def proxy(self):
        return dict(self.proxy_environment()) if self.proxy_environment else {}

    def with_proxy(self, settings):
        """``settings`` with the factory's egress proxy, when it has one."""
        from dataclasses import replace

        proxy = self.proxy()
        if settings is None or not proxy:
            return settings
        return replace(settings, proxy=proxy, proxy_network=self.proxy_network)

    def _space(self, run_id):
        if self.quicksand:
            from dataclasses import replace

            from .quicksand_space import QuicksandSpace

            proxy = self.proxy()
            return QuicksandSpace(
                run_id,
                (
                    replace(self.quicksand, proxy=proxy.get("HTTPS_PROXY"))
                    if proxy
                    else self.quicksand
                ),
            )
        return ActDockerSpace(
            run_id,
            self.with_proxy(self.container or ContainerSettings(image=RUNNER_IMAGE)),
            self.instance,
        )

    def _run(self, repo, run, workflow, github, cancel):
        state = self.state
        state.run_started(repo, run)
        space = bridge = None
        logs = defaultdict(list)
        results = defaultdict(dict)
        steps = defaultdict(dict)
        jobs = dict(zip((workflow.doc.get("jobs") or {}), run.jobs))
        mask = []

        def append(job, line):
            for value in mask:
                line = line.replace(value, "***")
            logs[job.id].append(line)
            state.job_log(repo, run, job, "\n".join(logs[job.id]) + "\n")

        def report(entry):
            key = entry.get("jobID")
            job = jobs.get(key)
            message = entry.get("msg", "")
            if job is None:
                if entry.get("level") != "debug":
                    for item in run.jobs:
                        append(item, message)
                return
            if job.status == "queued":
                state.job_started(repo, run, job)
            matrix = json.dumps(entry.get("matrix") or {}, sort_keys=True)
            # Raw action output must never be interpreted as lifecycle metadata.
            if not entry.get("raw_output"):
                if entry.get("jobResult"):
                    results[key][matrix] = (
                        entry["jobResult"]
                        if entry["jobResult"]
                        in {"success", "failure", "skipped", "cancelled"}
                        else "failure"
                    )
                    # A later deployment job must be able to verify completed
                    # dependencies before act exits. Matrix instances are not a
                    # whole job: keep those pending until aggregation below.
                    strategy = workflow.doc["jobs"][key].get("strategy") or {}
                    if not strategy.get("matrix") and matrix == "{}":
                        state.job_finished(
                            repo,
                            run,
                            job,
                            results[key][matrix],
                            "\n".join(logs[job.id]) + "\n",
                            list(steps[key].values()),
                        )
                if entry.get("stepID") and entry.get("step"):
                    from .state import Step

                    ident = (matrix, entry.get("stage"), tuple(entry["stepID"]))
                    step = steps[key].get(ident)
                    if step is None:
                        name = entry["step"] + (f" {matrix}" if matrix != "{}" else "")
                        step = steps[key][ident] = Step(
                            len(steps[key]) + 1,
                            name,
                            "in_progress",
                            None,
                            time.time(),
                            None,
                        )
                    if entry.get("stepResult"):
                        step.status, step.conclusion, step.completed_at = (
                            "completed",
                            entry["stepResult"],
                            time.time(),
                        )
                        state.job_steps(repo, run, job, list(steps[key].values()))
            # Debug is needed for act's skipped-job events, not for the displayed log.
            if (
                entry.get("level") != "debug"
                or entry.get("stepResult")
                or entry.get("jobResult")
            ):
                append(job, message)

        try:
            if workflow.doc.get("_error"):
                raise ValueError(workflow.doc["_error"])
            if not jobs:
                raise ValueError("Workflow has no jobs")
            runtime_jobs(
                workflow, repo
            )  # enforce host-runtime authorization before any execution
            variables, credentials = {}, {}
            if hasattr(state, "root"):
                from .workflow_settings import WorkflowSettings

                variables, credentials = WorkflowSettings(state.root.parent).execution(
                    repo, run
                )
                mask = sorted(
                    {
                        part
                        for value in credentials.values()
                        for part in [value, *value.splitlines()]
                        if len(part) >= 4
                    },
                    key=len,
                    reverse=True,
                )
            space = self._space(run.id)
            for job in run.jobs:
                append(job, f"Runner: act · {space.describe}")
            error = space.prepare(repo.bare.path, run.head_sha)
            if error:
                raise RuntimeError(error)
            if cancel.is_set():
                raise RuntimeError("Run cancelled during preparation")
            installed = space.run(["sh", "-c", ACT_INSTALL])
            if installed.returncode:
                raise RuntimeError(
                    "Could not install pinned act: "
                    + (installed.stderr or installed.stdout)[-2000:]
                )
            # Invoke outside the checkout so repository .actrc cannot change daemon,
            # mounts, platform policy, or load credentials from another location.
            space.run(["git", "-C", space.checkout, "remote", "remove", "origin"])
            space.run(
                [
                    "git",
                    "-C",
                    space.checkout,
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/" + repo.full_name + ".git",
                ]
            )
            event = {
                **github.get("event", {}),
                "ref": github["ref"],
                "after": run.head_sha,
                "repository": {
                    "full_name": repo.full_name,
                    "name": repo.name,
                    "default_branch": repo.default_branch,
                    "owner": {"login": repo.owner, "name": repo.owner},
                },
                "sender": {"login": run.actor},
            }
            if event.get("pull_request"):
                for side in ("head", "base"):
                    event["pull_request"][side]["repo"] = event["repository"]
            space.write("event.json", json.dumps(event))
            from .workflow_settings import dotenv

            space.write("workflow-secrets.env", dotenv(credentials))
            space.write("workflow-variables.env", dotenv(variables))
            space.run(
                [
                    "chmod",
                    "600",
                    space.path("workflow-secrets.env"),
                    space.path("workflow-variables.env"),
                ]
            )
            bridge = RuntimeBridge(self, space, repo, run, workflow)
            bridge.start()
            image = self.container.image if self.container else RUNNER_IMAGE
            # Compatibility for installations configured with our former minimal image.
            if image.startswith("omni-workflow-runner:"):
                image = RUNNER_IMAGE
            if image == RUNNER_IMAGE:
                # Match the common hosted-runner baseline without emulating setup actions.
                # Runtime versions still come from the workflow's real setup actions.
                for job in run.jobs:
                    append(
                        job,
                        "Preparing act job image (Ubuntu, Yarn, apt package indexes)",
                    )
                space.write("job.Dockerfile", JOB_DOCKERFILE)
                space.run(["mkdir", "-p", space.path("image-context")])
                built = space.run(
                    [
                        "docker",
                        "build",
                        *(
                            arg
                            for key, value in self.proxy().items()
                            for arg in ("--build-arg", f"{key}={value}")
                        ),
                        "-t",
                        JOB_IMAGE,
                        "-f",
                        space.path("job.Dockerfile"),
                        space.path("image-context"),
                    ]
                )
                if built.returncode:
                    raise RuntimeError(
                        "Could not build the act job image: "
                        + (built.stderr or built.stdout)[-2000:]
                    )
                image = JOB_IMAGE
            argv = [
                "/runner/bin/act",
                run.event,
                "--directory",
                space.checkout,
                "--workflows",
                space.checkout + "/" + workflow.path,
                "--eventpath",
                space.path("event.json"),
                "--json",
                "--verbose",
                "--pull=false",
                "--use-gitignore=false",
                "--concurrent-jobs",
                "1",
                "--env-file",
                "/dev/null",
                "--env",
                "GITHUB_RUN_ID=" + str(run.id),
                "--env",
                "GITHUB_RUN_NUMBER=" + str(getattr(run, "run_number", run.id)),
                "--env",
                "GITHUB_RUN_ATTEMPT=" + str(getattr(run, "run_attempt", 1)),
                # Every job container reaches the internet through the proxy.
                *(
                    arg
                    for key, value in self.proxy().items()
                    for arg in ("--env", f"{key}={value}")
                ),
                "--secret-file",
                space.path("workflow-secrets.env"),
                "--var-file",
                space.path("workflow-variables.env"),
                "--input-file",
                "/dev/null",
                "--artifact-server-path",
                space.path("artifacts"),
                "--artifact-server-addr",
                "0.0.0.0",
                "--actor",
                run.actor,
                "--network",
                "host",
                "-P",
                "ubuntu-latest=" + image,
                "-P",
                "ubuntu-22.04=" + image,
                "-P",
                "ubuntu-24.04=ghcr.io/catthehacker/ubuntu:act-24.04",
                "-P",
                "ubuntu-20.04=ghcr.io/catthehacker/ubuntu:act-20.04",
                "-P",
                "windows-latest=",
                "-P",
                "macos-latest=",
            ]
            if bridge.jobs:
                argv += [
                    "-P",
                    "self-hosted=-self-hosted",
                    "-P",
                    "omniagents-runtime=-self-hosted",
                ]
            env = space.base_env()
            env.update(
                {
                    "HOME": space.root,
                    "XDG_CONFIG_HOME": space.path("config"),
                    "XDG_CACHE_HOME": space.path("cache"),
                    "CI": "true",
                }
            )
            proc = space.popen(argv, space.root, env)
            self._procs[run.id] = (space, proc)
            if cancel.is_set():
                space.kill(proc)
            # Bound a workflow even if an action or daemon hangs; act owns job/step timeouts.
            timer = threading.Timer(6 * 3600, lambda: self.cancel(run.id))
            timer.start()
            try:
                for line in proc.stdout:
                    try:
                        entry = json.loads(line)
                    except (ValueError, TypeError):
                        entry = {"msg": line.rstrip()}
                    if isinstance(entry, dict):
                        report(entry)
                code = proc.wait()
            finally:
                timer.cancel()
            for key, job in jobs.items():
                values = list(results[key].values())
                conclusion = (
                    "cancelled"
                    if cancel.is_set()
                    else (
                        "failure"
                        if not values or "failure" in values
                        else (
                            "skipped"
                            if all(v == "skipped" for v in values)
                            else "success"
                        )
                    )
                )
                if not values and not cancel.is_set():
                    append(
                        job,
                        "##[error]act did not report a result for this job; refusing to mark it successful",
                    )
                state.job_finished(
                    repo,
                    run,
                    job,
                    conclusion,
                    "\n".join(logs[job.id]) + "\n",
                    list(steps[key].values()),
                )
            if (
                code
                and not cancel.is_set()
                and all(j.conclusion in {"success", "skipped"} for j in run.jobs)
            ):
                job = run.jobs[-1]
                append(job, "##[error]act exited unsuccessfully after job reporting")
                state.job_finished(
                    repo, run, job, "failure", "\n".join(logs[job.id]) + "\n", job.steps
                )
            final = (
                "cancelled"
                if cancel.is_set()
                else (
                    "failure"
                    if code or any(j.conclusion == "failure" for j in run.jobs)
                    else "success"
                )
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 — execution errors must finish the check
            conclusion = "cancelled" if cancel.is_set() else "failure"
            for job in run.jobs:
                append(job, "##[error]" + str(exc))
                state.job_finished(
                    repo,
                    run,
                    job,
                    conclusion,
                    "\n".join(logs[job.id]) + "\n",
                    list(
                        steps.get(
                            next((k for k, v in jobs.items() if v is job), ""), {}
                        ).values()
                    ),
                )
            final = conclusion
        finally:
            try:
                try:
                    if bridge:
                        bridge.close()
                finally:
                    if space:
                        space.close()
            except (
                Exception
            ) as exc:  # noqa: BLE001 — cleanup failure is a runner failure
                final = "failure"
                for job in run.jobs:
                    append(job, "##[error]Runner cleanup failed: " + str(exc))
                    state.job_finished(
                        repo, run, job, final, "\n".join(logs[job.id]) + "\n", job.steps
                    )
            self._procs.pop(run.id, None)
            self._cancel.pop(run.id, None)
            # A rerun may reuse this run ID. Publish completion only after cleanup
            # so an old controller cannot remove the new run's cancellation state.
            state.run_finished(repo, run, final)
