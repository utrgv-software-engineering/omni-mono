#!/usr/bin/env python3
"""Release a factory runtime, retaining its mounts and previous container.

Run as the operator through a forced command with a fixed, root-owned config.
Workflow callers cannot choose images, targets, health checks, or credentials.
An operator approval names both the tested revision and immutable image digest.
"""

import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from release_evidence import evidence, verify


class Engine(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=180)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def docker(method, path, body=None):
    connection = Engine()
    try:
        connection.request(
            method,
            "/v1.47" + path,
            json.dumps(body) if body is not None else None,
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        data = response.read()
        if response.status >= 400:
            raise RuntimeError(f"Docker {method} failed with HTTP {response.status}")
        return json.loads(data) if data else None
    finally:
        connection.close()


def http(url, token, method="GET", body=None):
    request = Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def token(config, name):
    return Path(config[name]).read_text().strip()


def runtime_url(config):
    container = docker("GET", "/containers/" + config["container"] + "/json")
    address = container["NetworkSettings"]["Networks"][config["network"]]["IPAddress"]
    return "http://" + address + ":" + str(config.get("port", 8765))


def checked_policy(config):
    policy = http(config["policy_url"], token(config, "policy_token_file"))
    if (
        policy.get("enabled") is not True
        or policy.get("provider") != config["provider"]
        or policy.get("repository") != config["repository"]
    ):
        raise ValueError(
            "Self-improvement is disabled or the linked repository changed"
        )


def check(config, request):
    return verify(
        config,
        evidence(config, request["RUN_ID"], token(config, "evidence_token_file")),
        revision=request["REVISION"],
        run_id=request["RUN_ID"],
        image_digest=request["IMAGE_DIGEST"],
    )


def activate(config, request, root):
    state_path = root / "state.json"
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    if previous.get("phase") not in {
        None,
        "complete",
        "rolled_back",
        "awaiting_approval",
        "approved",
        "deferred",
    }:
        raise RuntimeError("An interrupted release requires operator recovery")
    release = check(config, request)
    if previous.get("phase") in {"complete", "rolled_back"} and all(
        previous.get(k) == release[k] for k in ("revision", "image_digest")
    ):
        print("Factory release was already resolved: " + previous["phase"])
        return
    checked_policy(config)
    approval_path = Path(config["approval_file"])
    approval = json.loads(approval_path.read_text()) if approval_path.exists() else {}
    if any(approval.get(k) != release[k] for k in ("revision", "image_digest")):
        atomic(state_path, {**release, "phase": "awaiting_approval"})
        print(
            "Release checks passed; awaiting operator approval of revision and image digest"
        )
        return
    # Remove a previous deferred state before fallible checks so the timer
    # does not repeatedly retry a failing evaluation or artifact check.
    atomic(state_path, {**release, "phase": "approved"})
    image = config["image_repository"] + "@" + release["image_digest"]
    subprocess.run(
        ["docker", "--config", config["docker_config"], "pull", image],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    metadata = docker("GET", "/images/" + image + "/json")
    labels = metadata["Config"].get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != release["revision"]:
        raise ValueError("Image revision does not match verified workflow")
    if labels.get("org.omniagents.framework") != config["framework_image"]:
        raise ValueError("Framework image differs from the operator-approved pin")
    # Checks are operator-owned commands, not commands supplied by a workflow.
    subprocess.run(config["validate_command"] + [image], check=True, timeout=180)
    current = docker("GET", "/containers/" + config["container"] + "/json")
    if current["Config"].get("Labels", {}).get("omniagents.factory") not in {
        None,
        config["factory_id"],
    }:
        raise ValueError("Container belongs to another factory")
    original_url = runtime_url(config)
    auth = token(config, "runtime_token_file")
    control = token(config, "runtime_control_token_file")
    status = http(original_url + "/integrations/architecture", auth)
    if status.get("release_protocol") != 1:
        raise RuntimeError(
            "Install the release-aware runtime before activating releases"
        )
    if status.get("busy"):
        atomic(
            state_path, {**release, "phase": "deferred", "reason": "Factory is busy"}
        )
        print("Factory is busy; release deferred without stopping it")
        return
    validation = {
        "revision": release["revision"],
        "image_digest": release["image_digest"],
        "framework_image": config["framework_image"],
        "evaluation_version": config["evaluation_version"],
    }
    validated = root / "validation.json"
    if not validated.exists() or json.loads(validated.read_text()) != validation:
        if not config.get("evaluation_command"):
            raise ValueError("A focused factory evaluation gate is required")
        subprocess.run(
            config["evaluation_command"] + [image, release["revision"]],
            check=True,
            timeout=config.get("evaluation_timeout", 1800),
        )
        atomic(validated, validation)
    # Take a consistent durable-state backup before changing any runtime.
    subprocess.run(config["backup_command"], check=True, timeout=600)
    checked_policy(config)
    check(config, request)
    try:
        old_lease = http(
            original_url + "/integrations/release-lease", control, "POST", {}
        )["lease"]
    except HTTPError as exc:
        if exc.code != 409:
            raise
        atomic(
            state_path,
            {**release, "phase": "deferred", "reason": "Factory became busy"},
        )
        print("Factory became busy; release deferred without stopping it")
        return
    prior_name = config["container"] + "-previous-" + secrets.token_hex(4)
    prior_runtime = (
        (root / "runtime.json").read_bytes()
        if (root / "runtime.json").exists()
        else None
    )
    release.update(
        previous_runtime=json.loads(prior_runtime) if prior_runtime else None,
        previous_container=prior_name,
        previous_image=current["Image"],
        image=image,
        phase="activating",
        started_at=time.time(),
    )
    atomic(state_path, release)
    pending = root / "pending"
    pending.write_text(secrets.token_urlsafe(32))
    pending.chmod(0o600)
    old_stopped = old_renamed = new_created = False
    try:
        subprocess.run(
            config["snapshot_command"] + [original_url], check=True, timeout=120
        )
        docker("POST", "/containers/" + config["container"] + "/stop?t=45")
        old_stopped = True
        docker(
            "POST", "/containers/" + config["container"] + "/rename?name=" + prior_name
        )
        old_renamed = True
        body = {
            k: current["Config"][k]
            for k in (
                "Env",
                "Labels",
                "User",
                "ExposedPorts",
                "Volumes",
                "Healthcheck",
            )
            if k in current["Config"]
        }
        body["Image"] = image
        body["WorkingDir"] = "/release"
        body["Entrypoint"] = ["/bin/sh"]
        body["Cmd"] = [
            "-c",
            'set -a; . "$1"; export FACTORY_VERSION="$2" FACTORY_PROMOTION_MODE=release '
            "FACTORY_RELEASE_PENDING=/release-control/pending; "
            "exec /factory/.venv/bin/omniagents run --project /release/project.yml "
            '--mode server --host 0.0.0.0 --port 8765 --auth-token "$FACTORY_AUTH_TOKEN"',
            "factory-release",
            config["runtime_env"],
            release["revision"],
        ]
        body["HostConfig"] = current["HostConfig"]
        # All original durable mounts are retained verbatim. Only release-control
        # is added; it contains no application/customer data.
        mounts = list(body["HostConfig"].get("Mounts") or [])
        mounts = [m for m in mounts if m["Target"] != "/release-control"]
        mounts.append(
            {"Type": "bind", "Source": str(root), "Target": "/release-control"}
        )
        body["HostConfig"]["Mounts"] = mounts
        body["Labels"] = {
            **(body.get("Labels") or {}),
            "omniagents.factory": config["factory_id"],
            "omniagents.release": release["revision"],
        }
        docker("POST", "/containers/create?name=" + config["container"], body)
        new_created = True
        docker("POST", "/containers/" + config["container"] + "/start")
        url = runtime_url(config)
        for _ in range(120):
            try:
                observed = http(url + "/integrations/architecture", auth)
                if (
                    observed.get("version") == release["revision"]
                    and observed.get("release_paused") is True
                ):
                    break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError(
                "Candidate did not pass authenticated startup verification"
            )
        subprocess.run(config["chat_check_command"] + [url], check=True, timeout=120)
        checked_policy(config)
        check(config, request)
        # Commit the exact runtime image to managed factories' restart path too.
        atomic(
            root / "runtime.json",
            {
                "image": image,
                "revision": release["revision"],
                "project": "/release/project.yml",
            },
        )
        http(
            url + "/integrations/release-lease",
            control,
            "DELETE",
            {"lease": pending.read_text().strip()},
        )
        release["phase"] = "complete"
        atomic(state_path, release)
        print("Activated factory release " + release["revision"])
    except Exception:
        try:
            if new_created:
                docker("DELETE", "/containers/" + config["container"] + "?force=true")
            if old_renamed:
                docker(
                    "POST",
                    "/containers/" + prior_name + "/rename?name=" + config["container"],
                )
            # A previously released runtime also reads this marker on startup.
            # Remove it before restarting the retained container.
            pending.unlink(missing_ok=True)
            if old_stopped:
                docker("POST", "/containers/" + config["container"] + "/start")
                for _ in range(60):
                    try:
                        if http(
                            runtime_url(config) + "/integrations/architecture", auth
                        ).get("release_protocol"):
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError("Previous runtime did not recover")
            else:
                http(
                    original_url + "/integrations/release-lease",
                    control,
                    "DELETE",
                    {"lease": old_lease},
                )
            pending.unlink(missing_ok=True)
            if prior_runtime is None:
                (root / "runtime.json").unlink(missing_ok=True)
            else:
                atomic(root / "runtime.json", json.loads(prior_runtime))
            release["phase"] = "rolled_back"
            atomic(state_path, release)
        except Exception:
            release["phase"] = "recovery_required"
            atomic(state_path, release)
        raise


def main():
    os.umask(0o077)
    config = json.loads(Path(sys.argv[1]).read_text())
    if not re.fullmatch(r"[a-f0-9]{32}", config["factory_id"]):
        raise ValueError("Invalid factory ID")
    root = Path(config["state_directory"])
    root.mkdir(parents=True, exist_ok=True)
    raw = sys.stdin.read(16385)
    if len(raw) > 16384:
        raise ValueError("Request too large")
    request = json.loads(raw)
    with (root / "release.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        atomic(root / "request.json", request)
        activate(config, request, root)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Factory release failed: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
