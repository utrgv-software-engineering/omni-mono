"""Administrator-owned workflow credentials, never taken from repository code."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading

from ..credentials import encrypt, decrypt

_LOCK = threading.RLock()


class WorkflowSettings:
    def __init__(self, data):
        self.data = Path(data)
        self.path = self.data / "workflow-settings.enc"
        self.key_path = self.data / "workflow-settings.key"

    def _key(self):
        if not self.key_path.exists():
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as out:
                out.write(secrets.token_hex(32))
        return self.key_path.read_text().strip()

    def all(self):
        with _LOCK:
            if not self.path.exists():
                return {}
            return json.loads(
                decrypt(self.path.read_text(), self.key_path.read_text().strip())
            )

    def get(self, repository):
        return self.all().get(repository, {})

    def configure(
        self,
        repo,
        *,
        variables=None,
        credentials=None,
        approve_workflows=None,
        revoke_workflows=None,
    ):
        with _LOCK:
            values = self.all()
            item = values.get(repo.full_name, {})
            for field, updates in [("variables", variables), ("secrets", credentials)]:
                if updates is not None:
                    target = item.setdefault(field, {})
                    for name, value in updates.items():
                        if not re.fullmatch(
                            r"[A-Z_][A-Z0-9_]*", name
                        ) or name.startswith(
                            ("GITHUB_", "RUNNER_", "ACTIONS_", "OMNI_")
                        ):
                            raise ValueError(
                                "Invalid or reserved workflow variable name"
                            )
                        if value is None:
                            target.pop(name, None)
                        elif (
                            isinstance(value, str)
                            and "\0" not in value
                            and len(value) <= 65536
                        ):
                            target[name] = value
                        else:
                            raise ValueError(
                                "Workflow values must be strings of at most 64 KiB"
                            )
            if approve_workflows is not None:
                approved = dict(item.get("trusted_workflows", {}))
                for path in approve_workflows:
                    if not re.fullmatch(r"\.github/workflows/[\w.-]+\.ya?ml", path):
                        raise ValueError(
                            "Expected a workflow path under .github/workflows"
                        )
                    blob = repo.bare.blob(repo.default_branch, path)
                    if not blob:
                        raise ValueError("Workflow is missing from the default branch")
                    approved[path] = hashlib.sha256(blob[1]).hexdigest()
                item["trusted_workflows"] = approved
                item["branch"] = repo.default_branch
            for path in revoke_workflows or []:
                item.setdefault("trusted_workflows", {}).pop(path, None)
            values[repo.full_name] = item
            self._write(values)
            return self.public(repo.full_name)

    def _write(self, values):
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(encrypt(json.dumps(values), self._key()))
        temporary.replace(self.path)

    def rotate_release_token(self, repository):
        with _LOCK:
            values = self.all()
            token = secrets.token_urlsafe(48)
            values.setdefault(repository, {})["release_token_hash"] = hashlib.sha256(
                token.encode()
            ).hexdigest()
            self._write(values)
            return token

    def permits_release_read(self, repository, token):
        expected = self.get(repository).get("release_token_hash", "")
        return bool(expected and token) and secrets.compare_digest(
            expected, hashlib.sha256(token.encode()).hexdigest()
        )

    def public(self, repository):
        item = self.get(repository)
        return {
            "variables": item.get("variables", {}),
            "secret_names": sorted(item.get("secrets", {})),
            "trusted_workflows": item.get("trusted_workflows", {}),
            "branch": item.get("branch"),
        }

    def execution(self, repo, run):
        item = self.get(repo.full_name)
        variables = item.get("variables", {})
        if (
            not item.get("secrets")
            or run.event != "push"
            or run.head_branch != item.get("branch")
        ):
            return variables, {}
        if (
            run.head_branch != repo.default_branch
            or run.head_branch not in repo.protected
        ):
            raise ValueError(
                "Credential-bearing workflows require a protected default branch"
            )
        if repo.bare.ref_sha(run.head_branch) != run.head_sha:
            raise ValueError(
                "A newer default-branch commit supersedes this credential-bearing run"
            )
        blob = repo.bare.blob(run.head_sha, run.path)
        digest = hashlib.sha256(blob[1]).hexdigest() if blob else None
        if digest != item.get("trusted_workflows", {}).get(run.path):
            raise ValueError(
                "Workflow credentials withheld: an administrator must approve this workflow version"
            )
        return variables, item["secrets"]


def dotenv(values):
    # godotenv's quoted values preserve newlines; escape interpolation explicitly.
    return "".join(
        name + "=" + json.dumps(value, ensure_ascii=False).replace("$", r"\$") + "\n"
        for name, value in values.items()
    )
