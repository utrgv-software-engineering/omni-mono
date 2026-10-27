"""Deployment overrides for the existing GitHub integration, shared with templates."""

import json
import os
import re


def github_settings(entry):
    entry = dict(entry)
    if "GITHUB_REPOSITORIES" in os.environ:
        repositories = json.loads(os.environ["GITHUB_REPOSITORIES"])
        if not isinstance(repositories, list) or any(
            not isinstance(r, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r)
            or any(p in (".", "..") for p in r.split("/"))
            for r in repositories
        ):
            raise ValueError(
                "GITHUB_REPOSITORIES must be a JSON list of owner/name repositories"
            )
        entry["repositories"] = repositories
    if os.environ.get("GITHUB_ACCOUNT"):
        entry["account"] = os.environ["GITHUB_ACCOUNT"]
    if "GITHUB_GIT_URL" in os.environ:
        entry["git_url"] = os.environ["GITHUB_GIT_URL"]
    if "FACTORY_LINKED_REPOSITORY" in os.environ:
        entry["factory_repository"] = os.environ["FACTORY_LINKED_REPOSITORY"]
    return entry
