#!/usr/bin/env python3
"""App-host release steps for the durable counter reference application.

Run only through the root-owned controller, with its fixed configuration.
The candidate checks a snapshot of the counter before touching the live app.
"""

import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen


def command(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs).stdout


def health(port):
    with urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
        return json.load(response)


def wait(port):
    for attempt in range(30):
        try:
            return health(port)
        except Exception:
            if attempt == 29:
                raise
            time.sleep(2)


def route(config, port):
    url = "http://127.0.0.1:2019" + config["route_path"]
    with tempfile.TemporaryDirectory() as directory:
        headers = Path(directory) / "headers"
        current = json.loads(command(["curl", "-fsS", "-D", str(headers), url]))
        etag = next(
            line.split(":", 1)[1].strip()
            for line in headers.read_text().splitlines()
            if line.lower().startswith("etag:")
        )
    if not any(
        config["hostname"] in match.get("host", []) for match in current["match"]
    ):
        raise ValueError("Unexpected route host")
    candidate = copy.deepcopy(current)
    replaced = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("handler") == "reverse_proxy":
                for upstream in value["upstreams"]:
                    if upstream["dial"] not in [
                        f"127.0.0.1:{config[k]}" for k in ["port", "fallback_port"]
                    ]:
                        raise ValueError("Unexpected route upstream")
                    upstream["dial"] = f"127.0.0.1:{port}"
                    replaced.append(True)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(candidate)
    if len(replaced) != 1:
        raise ValueError("Expected exactly one app upstream")
    request = Request(
        url,
        data=json.dumps(candidate).encode(),
        method="PATCH",
        headers={"Content-Type": "application/json", "If-Match": etag},
    )
    urlopen(request, timeout=30).close()


def main():
    os.umask(0o077)
    payload = json.load(sys.stdin)
    config, phase = payload["config"], payload["phase"]
    fallback = config["container_prefix"] + "-fallback"
    candidate = config["container_prefix"] + "-candidate"
    marker = Path(config["data_directory"]).parent / "counter-before.json"
    target = config["target_image"] + ":" + payload["release_tag"]
    if phase == "stage":
        before = health(config["port"])
        marker.write_text(json.dumps(before))
        with tempfile.TemporaryDirectory() as directory:
            command(
                [
                    "docker",
                    "--config",
                    directory,
                    "login",
                    config["registry"],
                    "-u",
                    config["registry_user"],
                    "--password-stdin",
                ],
                input=payload["registry_password"].encode(),
            )
            auth = str(Path(directory) / "config.json")
            source = (
                config["registry"]
                + "/"
                + config["source_image"]
                + "@"
                + payload["digest"]
            )
            inspected = json.loads(
                command(["skopeo", "inspect", "--authfile", auth, "docker://" + source])
            )
            if (
                inspected["Labels"].get("org.opencontainers.image.revision")
                != payload["revision"]
            ):
                raise ValueError("Image revision mismatch")
            command(
                [
                    "skopeo",
                    "copy",
                    "--authfile",
                    auth,
                    "--dest-tls-verify=false",
                    "docker://" + source,
                    "docker://" + target,
                ]
            )
        command(["docker", "pull", target])
        with tempfile.TemporaryDirectory(prefix="reference-candidate-") as directory:
            snapshot = Path(directory)
            with sqlite3.connect(
                str(Path(config["data_directory"]) / "app.db")
            ) as live, sqlite3.connect(str(snapshot / "app.db")) as backup:
                live.backup(backup)
            snapshot.chmod(0o755)
            os.chown(snapshot, 1000, 1000)
            os.chown(snapshot / "app.db", 1000, 1000)
            try:
                command(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        candidate,
                        "--memory",
                        "128m",
                        "--cpus",
                        "0.5",
                        "-p",
                        f"127.0.0.1:{config['candidate_port']}:8080",
                        "--mount",
                        f"type=bind,src={snapshot},dst=/data",
                        target,
                    ]
                )
                observed = wait(config["candidate_port"])
                if (
                    observed["revision"] != payload["revision"]
                    or observed["counter"] < before["counter"]
                ):
                    raise ValueError("Candidate failed revision or persistence check")
            finally:
                subprocess.run(["docker", "rm", "-f", candidate], capture_output=True)
        current = json.loads(command(["docker", "inspect", config["app_uuid"]]))[0]
        with tempfile.NamedTemporaryFile(mode="w") as environment:
            environment.write("\n".join(current["Config"]["Env"]) + "\n")
            environment.flush()
            command(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    fallback,
                    "--restart",
                    "unless-stopped",
                    "--memory",
                    "128m",
                    "--cpus",
                    "0.5",
                    "--env-file",
                    environment.name,
                    "-p",
                    f"127.0.0.1:{config['fallback_port']}:8080",
                    "--mount",
                    f"type=bind,src={config['data_directory']},dst=/data",
                    current["Image"],
                ]
            )
        wait(config["fallback_port"])
        route(config, config["fallback_port"])
    elif phase == "status":
        current = json.loads(command(["docker", "inspect", config["app_uuid"]]))[0]
        if current["Config"]["Image"] != config["target_image"] + ":" + payload["tag"]:
            raise ValueError("Unexpected running image")
        if not current["State"]["Running"] or current["RestartCount"]:
            raise ValueError("Application restarted or stopped")
        observed = wait(config["port"])
        if observed["counter"] < json.loads(marker.read_text())["counter"]:
            raise ValueError("Persistent counter was lost")
        if (
            payload["tag"] == payload["release_tag"]
            and observed["revision"] != payload["revision"]
        ):
            raise ValueError("Running revision mismatch")
    elif phase in {"finish", "abort_stage"}:
        wait(config["port"])
        route(config, config["port"])
        subprocess.run(["docker", "rm", "-f", fallback], capture_output=True)
    else:
        raise ValueError("Unknown release phase")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("App-host release failed: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
