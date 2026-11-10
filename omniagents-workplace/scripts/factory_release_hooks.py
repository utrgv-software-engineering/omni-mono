#!/usr/bin/env python3
"""Fixed operator-owned hooks; configuration and credentials stay outside CI."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

os.umask(0o077)
config = json.loads(Path(sys.argv[1]).read_text())
mode = sys.argv[2]
root = Path(config["operator_directory"])
factory = config["factory_id"]


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


if mode in {"snapshot", "chat"}:
    url = sys.argv[3]
    result = subprocess.check_output(
        [
            "docker",
            "exec",
            "-i",
            config["workplace_container"],
            "python",
            "-c",
            (root / "factory_release_chat.py").read_text(),
            mode,
            url,
            config["session_id"],
        ],
        input=Path(config["runtime_token_file"]).read_bytes(),
    )
    observed = json.loads(result)
    path = root / "chat-baseline.json"
    if mode == "snapshot":
        path.write_bytes(result)
    else:
        prior = json.loads(path.read_text())
        if prior != observed:
            raise RuntimeError("Authenticated chat history changed during release")
        if (root / "simulate-chat-failure").exists():
            raise RuntimeError("Operator-requested rollback proof")
        print("Authenticated chat and retained history verified")
elif mode == "backup":
    base = "/data/installation/factories/" + factory
    run(
        [
            "docker",
            "exec",
            config["backup_container"],
            "omniagents-workplace",
            "backup",
            "--data",
            base + "/workplace",
            "--to",
            "/backups/factory-" + factory,
            "--include",
            "factory=" + base,
        ]
    )
elif mode == "validate":
    image = sys.argv[3]
    code = """
from pathlib import Path
import yaml
r=Path('/release')
assert yaml.safe_load((r/'project.yml').read_text())['agents']['entrypoint']=='generalist'
for p in r.rglob('*.yml'): yaml.safe_load(p.read_text())
assert (r/'agents/generalist/skills/factory-improvement/SKILL.md').is_file()
print('Candidate configuration validated')
"""
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/factory/.venv/bin/python",
            image,
            "-c",
            code,
        ]
    )
elif mode == "eval":
    image, revision = sys.argv[3:]
    name = "factory-eval-" + factory[:12]
    output = root / "evaluations" / (revision + "-" + str(int(time.time())))
    output.mkdir(parents=True)
    (output / "tmp").mkdir()
    (output / "home").mkdir()
    profile = output / "sandbox.yml"
    # The tools execute in their own normal sandbox; the agent hosts fixtures.
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/bin/cat",
            image,
            "/release/sandbox_profile_default.yml",
        ],
        stdout=profile.open("wb"),
    )
    # YAML is parsed in the pinned runtime, so no host dependency is required.
    code = "import yaml,sys; p=yaml.safe_load(sys.stdin.read()); p.setdefault('client',{})['network_mode']=sys.argv[1]; print(yaml.safe_dump(p))"
    configured = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--entrypoint",
            "/factory/.venv/bin/python",
            image,
            "-c",
            code,
            config["network"],
        ],
        input=profile.read_bytes(),
    )
    profile.write_bytes(configured)
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        config["network"],
        "--mount",
        "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
        "--mount",
        "type=bind,src="
        + config["eval_codex_directory"]
        + ",dst=/secrets/codex,readonly",
        "--mount",
        "type=bind,src="
        + str(profile)
        + ",dst=/release/sandbox_profile_default.yml,readonly",
        "--mount",
        "type=bind,src=" + str(output) + ",dst=/eval-results",
        "--mount",
        "type=bind,src=" + str(output) + ",dst=" + str(output),
        "--mount",
        "type=bind,src="
        + str(root / "factory_release_eval.py")
        + ",dst=/operator-eval.py,readonly",
        "-e",
        "OMNIAGENTS_WORKPLACE_HOST=" + name,
        "-e",
        "OMNIAGENTS_CODEX_HOME=/secrets/codex",
        "-e",
        "OMNIAGENTS_HOME=" + str(output / "home"),
        "-e",
        "TMPDIR=" + str(output / "tmp"),
        "--workdir",
        "/release",
        "--entrypoint",
        "/factory/.venv/bin/python",
        image,
        "/operator-eval.py",
    ]
    try:
        with (output / "console.log").open("w") as log:
            run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1700)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    report = json.loads((output / "result.json").read_text())
    if report.get("passed") is not True:
        raise RuntimeError("Factory outcome evaluation failed")
    print("Focused factory outcome evaluation passed: " + str(output))
else:
    raise SystemExit("Unknown release hook")
