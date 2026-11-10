"""Provider-independent release checks for an external deployment controller.

Configuration is administrator-owned. Never accept repository, API URL, branch,
required checks, or workflow approval from a workflow's deployment request.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import quote
from urllib.request import Request, urlopen


def evidence(config, run_id, token):
    if not re.fullmatch(r"\d+", str(run_id)):
        raise ValueError("Invalid workflow run ID")
    repository = config["repository"]
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("Invalid repository")

    def get(url):
        request = Request(
            url,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
            },
        )
        with urlopen(request, timeout=30) as response:
            return json.load(response)

    if config["provider"] == "standin":
        return get(
            config["api_url"].rstrip("/")
            + "/github/release-evidence/"
            + repository
            + "/"
            + str(run_id)
        )
    if config["provider"] != "github":
        raise ValueError("Unknown repository provider")
    base = "https://api.github.com/repos/" + repository + "/"
    repository_info = get(base.rstrip("/"))
    run = get(base + "actions/runs/" + str(run_id))
    branch = get(base + "branches/" + quote(config["branch"], safe=""))
    jobs = []
    page = 1
    while True:
        batch = get(
            base + f"actions/runs/{run_id}/jobs?filter=all&per_page=100&page={page}"
        )["jobs"]
        jobs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    latest = {}
    for job in jobs:
        old = latest.get(job["name"])
        if old is None or (job["run_attempt"], job["id"]) > (
            old["run_attempt"],
            old["id"],
        ):
            latest[job["name"]] = job
    content = get(base + "contents/" + config["workflow"] + "?ref=" + run["head_sha"])
    return {
        "provider": "github",
        "repository": repository,
        "run_id": str(run_id),
        "revision": run["head_sha"],
        "current_revision": branch["commit"]["sha"],
        "branch": run["head_branch"],
        "default_branch": repository_info["default_branch"],
        "protected": branch.get("protected", False),
        "event": run["event"],
        "path": run["path"],
        "status": run["status"],
        "workflow_sha256": hashlib.sha256(
            base64.b64decode(content["content"])
        ).hexdigest(),
        "jobs": list(latest.values()),
    }


def verify(config, observed, *, revision, run_id, image_digest):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid revision")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise ValueError("An immutable image digest is required")
    expected = {
        "provider": config["provider"],
        "repository": config["repository"],
        "run_id": str(run_id),
        "revision": revision,
        "current_revision": revision,
        "branch": config["branch"],
        "default_branch": config["branch"],
        "event": "push",
        "path": config["workflow"],
        "workflow_sha256": config["workflow_sha256"],
    }
    for name, value in expected.items():
        if observed.get(name) != value:
            raise ValueError("Release evidence mismatch: " + name)
    if config.get("require_branch_protection", True) and not observed.get("protected"):
        raise ValueError("The release branch must be protected")
    if not config.get("required_checks"):
        raise ValueError("Configure at least one required release check")
    for name in config["required_checks"]:
        matches = [j for j in observed["jobs"] if j["name"] == name]
        if not matches or any(
            j["status"] != "completed" or j["conclusion"] != "success" for j in matches
        ):
            raise ValueError("Required release check has not passed: " + name)
    return {
        "repository": config["repository"],
        "revision": revision,
        "run_id": str(run_id),
        "image_digest": image_digest,
        # Rebuilding the same commit can produce a different image. Keep both
        # artifacts addressable so rollback cannot resolve to the replacement.
        "image_tag": revision + "-" + image_digest.removeprefix("sha256:"),
    }
