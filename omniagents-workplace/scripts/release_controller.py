#!/usr/bin/env python3
"""Restricted SSH release entrypoint. Config and this script must be root-owned.

Install with a forced SSH command selecting one fixed config file. The caller
supplies only revision, run ID, digest and a short-lived source read token.
Coolify, registry and app-host credentials stay on this controller.
"""

import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from urllib.request import Request, urlopen

from release_evidence import evidence, verify


def run(config_path):
    os.umask(0o077)
    config = json.loads(Path(config_path).read_text())
    if config.get("coolify_container"):
        inspected = json.loads(
            subprocess.check_output(["docker", "inspect", config["coolify_container"]])
        )[0]
        address = inspected["NetworkSettings"]["Networks"][config["coolify_network"]][
            "IPAddress"
        ]
        config["coolify_url"] = f"http://{address}:{config['coolify_port']}/api/v1"
    root = Path(config["state_directory"])
    root.mkdir(parents=True, exist_ok=True)
    with (root / "release.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        release(config, root)


def release(config, root):
    raw = sys.stdin.read(16385)
    if len(raw) > 16384:
        raise ValueError("Release request too large")
    request = json.loads(raw)
    token = (
        Path(config["evidence_token_file"]).read_text().strip()
        if config["provider"] == "standin"
        else request["SOURCE_TOKEN"]
    )
    checked = verify(
        config,
        evidence(config, request["RUN_ID"], token),
        revision=request["REVISION"],
        run_id=request["RUN_ID"],
        image_digest=request["IMAGE_DIGEST"],
    )
    state = root / "release-state.json"
    if state.exists() and json.loads(state.read_text())["phase"] not in {
        "complete",
        "rolled_back",
    }:
        raise RuntimeError("Previous release requires recovery")

    def api(method, path, body=None):
        req = Request(
            config["coolify_url"].rstrip("/") + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer "
                + Path(config["coolify_token_file"]).read_text().strip(),
            },
        )
        with urlopen(req, timeout=30) as response:
            return json.load(response)

    app_path = "/applications/" + config["app_uuid"]
    previous = api("GET", app_path)
    if previous["docker_registry_image_name"] != config["target_image"]:
        raise ValueError("Unexpected deployment target")
    checked.update(before=previous["docker_registry_image_tag"], started_at=time.time())

    def save(phase):
        checked["phase"] = phase
        temporary = state.with_suffix(".tmp")
        temporary.write_text(json.dumps(checked, indent=2))
        temporary.replace(state)

    def remote(phase, tag=None):
        payload = dict(
            config=config,
            phase=phase,
            revision=request["REVISION"],
            digest=request["IMAGE_DIGEST"],
            release_tag=checked["image_tag"],
            tag=tag,
            registry_password=Path(config["registry_password_file"])
            .read_text()
            .strip(),
        )
        command = [
            "ssh",
            "-T",
            "-i",
            config["app_host_key"],
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "root@" + config["app_host"],
            "python3 -c " + shlex.quote(Path(config["remote_script"]).read_text()),
        ]
        subprocess.run(
            command, input=json.dumps(payload), text=True, check=True, timeout=600
        )

    def deploy(tag):
        api("PATCH", app_path, {"docker_registry_image_tag": tag})
        result = api("POST", "/deploy", {"uuid": config["app_uuid"]})
        deployment = result["deployments"][0]["deployment_uuid"]
        for _ in range(120):
            status = api("GET", "/deployments/" + deployment)["status"]
            if status == "finished":
                return
            if status in {"failed", "cancelled", "canceled"}:
                raise RuntimeError("Coolify deployment failed")
            time.sleep(5)
        api("POST", "/deployments/" + deployment + "/cancel")
        raise TimeoutError("Coolify deployment timed out")

    save("staging")
    try:
        remote("stage")
        # Check again after staging: a superseding push must not be promoted.
        verify(
            config,
            evidence(config, request["RUN_ID"], token),
            revision=request["REVISION"],
            run_id=request["RUN_ID"],
            image_digest=request["IMAGE_DIGEST"],
        )
        save("deploying")
        deploy(checked["image_tag"])
        remote("status", checked["image_tag"])
        remote("finish")
        save("complete")
        print("Released " + request["REVISION"])
    except Exception:
        try:
            if checked["phase"] == "staging":
                remote("abort_stage")
            else:
                save("rolling_back")
                deploy(checked["before"])
                remote("status", checked["before"])
                remote("finish")
            save("rolled_back")
        except Exception:
            save("recovery_required")
        raise


if __name__ == "__main__":
    try:
        run(sys.argv[1])
    except Exception as error:
        # Requests and subprocess output may contain credentials.
        print("Release failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
