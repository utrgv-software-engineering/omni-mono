"""Narrow bridge from act to trusted factory runtime actions outside tool VMs."""

import json
import threading

import yaml

from .agent_jobs import ACTIONS, AgentJobSpace
from .spaces import DockerSpace, HostSpace

CLIENT = r"""
import json, os, time, uuid
from pathlib import Path
root = Path('/runner')
while True:
    try:
        (root/'runtime.lock').mkdir()
        break
    except FileExistsError:
        time.sleep(.2)
try:
    ident = uuid.uuid4().hex
    request = {'id':ident, 'action':os.environ['RUNTIME_ACTION'],
               'inputs':{k[6:].lower():v for k,v in os.environ.items() if k.startswith('INPUT_') and v}}
    temp=root/'runtime-request.tmp'
    temp.write_text(json.dumps(request))
    temp.replace(root/'runtime-request.json')
    while True:
        try:
            result=json.loads((root/'runtime-response.json').read_text())
            if result['id']==ident:
                print(result.get('log',''),flush=True)
                raise SystemExit(result['code'])
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(.5)
finally:
    (root/'runtime.lock').rmdir()
"""


def runtime_jobs(workflow, repo):
    jobs = []
    for key, spec in (workflow.doc.get("jobs") or {}).items():
        labels = spec.get("runs-on", [])
        labels = [labels] if isinstance(labels, str) else labels
        runtime = any(s.get("uses") in ACTIONS for s in spec.get("steps", []))
        if runtime or "self-hosted" in labels or "omniagents-runtime" in labels:
            if not repo.self_hosted or "self-hosted" not in labels:
                raise ValueError(
                    "Factory runtime jobs require an admin-allowed self-hosted repository"
                )
            if any(
                s.get("run") is not None
                or s.get("uses", "").split("@")[0] != "actions/checkout"
                and s.get("uses") not in ACTIONS
                for s in spec.get("steps", [])
            ):
                raise ValueError(
                    "Factory runtime jobs accept checkout and named evaluate/benchmark/staging actions only; ordinary CI belongs in a container job"
                )
            jobs.append(key)
    return jobs


class RuntimeBridge:
    def __init__(self, runner, space, repo, run, workflow):
        self.runner, self.space, self.repo, self.run = runner, space, repo, run
        self.jobs = runtime_jobs(workflow, repo)
        self.stop = threading.Event()
        self.thread = None
        self.host = self.proc = None

    def start(self):
        if not self.jobs:
            return
        if self.runner.quicksand and not self.runner.agent_profile:
            raise ValueError("Factory runtime jobs require --workflow-agent-profile")
        self.space.write("omni-runtime-client.py", CLIENT)
        for action, mode in ACTIONS.items():
            inputs = {
                "evaluate": ["agent", "scenario"],
                "benchmark": ["suite", "configs"],
                "staging": ["limit", "since"],
            }[mode]
            doc = {
                "name": "Factory " + mode,
                "inputs": {k: {"description": k} for k in inputs},
                "runs": {
                    "using": "composite",
                    "steps": [
                        {
                            "shell": "bash",
                            "env": {
                                "RUNTIME_ACTION": action,
                                **{
                                    "INPUT_" + k.upper(): "${{ inputs." + k + " }}"
                                    for k in inputs
                                },
                            },
                            "run": "python3 /runner/omni-runtime-client.py",
                        }
                    ],
                },
            }
            self.space.run(["mkdir", "-p", self.space.checkout + "/" + action])
            self.space.write("workspace/" + action + "/action.yml", yaml.safe_dump(doc))
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        seen = set()
        while not self.stop.wait(0.5):
            try:
                text = self.space.read("runtime-request.json")
                if not text or len(text) > 16000:
                    continue
                req = json.loads(text)
                ident = req.get("id")
                if not isinstance(ident, str) or ident in seen:
                    continue
                seen.add(ident)
                code, log = self.execute(req)
                self.space.write(
                    "runtime-response.json",
                    json.dumps({"id": ident, "code": code, "log": log}),
                )
            except Exception as exc:  # noqa: BLE001 — send runtime failures back to act
                if self.stop.is_set():
                    return
                if "req" in locals():
                    self.space.write(
                        "runtime-response.json",
                        json.dumps(
                            {
                                "id": req.get("id"),
                                "code": 1,
                                "log": "Factory runtime action failed: " + str(exc),
                            }
                        ),
                    )

    def execute(self, req):
        runner = self.runner
        if self.stop.is_set() or not self.repo.self_hosted:
            raise ValueError("Factory runtime invocation cancelled or access revoked")
        if runner.agent_profile:
            self.host = AgentJobSpace(
                self.run.id, runner.agent_profile, environment=runner.agent_environment
            )
        elif runner.self_hosted:
            self.host = DockerSpace(self.run.id, runner.self_hosted, runner.instance)
        elif runner.container:
            raise ValueError("No factory self-hosted runtime is configured")
        else:
            self.host = HostSpace(self.run.id, own_environment=True)
        try:
            error = self.host.prepare(self.repo.bare.path, self.run.head_sha)
            if error:
                raise ValueError(error)
            if self.stop.is_set():
                raise ValueError("Factory runtime invocation cancelled")
            argv = AgentJobSpace.action_command(self.host, req["action"], req["inputs"])
            if isinstance(self.host, DockerSpace):
                argv[0] = "python3"
            env = (
                self.host.runtime_env()
                if isinstance(self.host, AgentJobSpace)
                else self.host.base_env()
            )
            env["GITHUB_SHA"] = self.run.head_sha
            # Bypass AgentJobSpace's blanket workflow-shell guard for this validated command only.
            self.proc = (
                HostSpace.popen(self.host, argv, self.host.checkout, env)
                if isinstance(self.host, AgentJobSpace)
                else self.host.popen(argv, self.host.checkout, env)
            )
            if self.stop.is_set():
                self.host.kill(self.proc)
            lines = []
            for line in self.proc.stdout:
                lines.append(line)
                if sum(map(len, lines)) > 512000:
                    lines = lines[-1000:]
            return self.proc.wait(), "".join(lines)
        finally:
            self.host.close()
            self.host = self.proc = None

    def close(self):
        self.stop.set()
        if self.host and self.proc:
            self.host.kill(self.proc)
        if self.thread:
            self.thread.join(10)
