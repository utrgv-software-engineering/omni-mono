"""Editing the factory: its agents' instructions, skills and automations, saved as commits.

The factory's definition is its repository here (workplace.yml's
``factory.repository``). An admin edits a file; saving it is a commit on
the repository's main branch, as that person. The factory runs from a
checkout of the repository and reads these files again on each run, so
publishing a commit is asking the factory to move its checkout to it
(``POST /integrations/definition/advance``): the next run uses it, with no
restart.

A factory can require review instead (``factory.definition.require_review``):
then saving opens a pull request, and the factory moves to its commit once
it is merged. Any move of main is offered to the factory the same way; it
refuses changes that need a release (agent settings, dependencies), which
reach it by release as before.

The factory itself says what is editable and what is wrong with an edit
(``GET /integrations/definition``, ``POST .../check``): the rules live with
the code that reads the files.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .accounts import admin
from .github.repos import NULL_SHA, git
from .github.state import GitHubError

logger = logging.getLogger(__name__)

REVIEW_KEY = "factory.definition.require_review"


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=300)
    #: The file's new content; ``None`` deletes it.
    content: Optional[str] = Field(default=None, max_length=500_000)


class SaveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: The commit of main the editor read the files from.
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    changes: List[Change] = Field(min_length=1, max_length=20)
    message: Optional[str] = Field(default=None, max_length=200)


class RestoreBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=300)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_review: bool


def describe(
    changes: List[Tuple[str, Optional[bytes]]], existed: Dict[str, bool]
) -> str:
    """A commit message for ``changes`` (path, content or ``None``):
    "Edit foreman instructions", "Add skill triage-notes"."""

    def one(path: str, content: Optional[bytes]) -> str:
        parts = PurePosixPath(path).parts
        verb = "Delete" if content is None else ("Edit" if existed[path] else "Add")
        if parts[0] == "automations" and len(parts) == 3:
            return f"{verb} automation {parts[1]}"
        if parts[-1] == "SKILL.md" and len(parts) >= 3:
            return f"{verb} skill {parts[-2]}"
        if len(parts) >= 2:
            return f"{verb} {parts[-2].replace('_', ' ')} instructions"
        return f"{verb} {path}"

    lines = [one(path, content) for path, content in changes]
    return lines[0] if len(lines) == 1 else f"{lines[0]} and {len(lines) - 1} more"


class FactoryDefinition:
    """The factory's repository here, and the running factory, for editing."""

    def __init__(
        self,
        workplace: Any,
        repository: Callable[[], Optional[str]],
        endpoint: Callable[[], Tuple[Optional[str], Optional[str]]],
    ):
        self.w = workplace
        self.repository = repository
        self.endpoint = endpoint
        #: The last time main was offered to the factory, and what it said.
        self.published: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    # -- the parts ------------------------------------------------------------

    def require_review(self) -> bool:
        return bool(self.w.store.meta(REVIEW_KEY, False))

    def repo(self) -> Any:
        """The repository the factory runs from: its own here, whichever
        provider serves the product repositories."""
        name = self.repository()
        repo = self.w.github.repos.get(name or "")
        if repo is None:
            raise HTTPException(
                409,
                "The factory has no repository here (workplace.yml's factory.repository).",
            )
        return repo

    def main(self, repo: Any) -> str:
        sha = repo.bare.ref_sha(repo.default_branch)
        if sha is None:
            raise HTTPException(409, "The factory's repository has no main branch.")
        return sha

    def factory(self, method: str, path: str, body: Any = None) -> Tuple[int, Any]:
        """One of the factory's definition routes: its status and answer."""
        url, token = self.endpoint()
        if not url:
            raise HTTPException(503, "The factory is unavailable")
        try:
            response = httpx.request(
                method,
                url.rstrip("/") + path,
                json=body,
                headers={"Authorization": f"Bearer {token}"} if token else {},
                timeout=60,
            )
        except httpx.HTTPError:
            raise HTTPException(503, "The factory is not answering") from None
        if response.status_code == 404:
            raise HTTPException(
                409,
                "The factory's server can't be edited from here yet: it needs a newer omniagents.",
            )
        if response.status_code == 401:
            raise HTTPException(503, "The factory refused the Workplace's token")
        try:
            data = response.json()
        except ValueError:
            data = {}
        return response.status_code, data

    # -- reading ----------------------------------------------------------------

    def tree(self) -> Dict[str, Any]:
        repo = self.repo()
        status, data = self.factory("GET", "/integrations/definition")
        if status >= 400:
            raise HTTPException(
                502, data.get("detail") or "The factory could not list its definition"
            )
        main = self.main(repo)
        return {
            **data,
            "repository": repo.full_name,
            "main": main,
            "live": data.get("commit"),
            "require_review": self.require_review(),
            "published": self.published,
        }

    def read(self, path: str) -> Dict[str, Any]:
        repo = self.repo()
        main = self.main(repo)
        found = repo.bare.blob(main, path)
        if found is None:
            raise HTTPException(404, "No such file")
        try:
            content = found[1].decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(422, "The file is not text") from None
        return {"path": path, "content": content, "commit": main}

    def history(self, path: str) -> Dict[str, Any]:
        repo = self.repo()
        commits = repo.bare.commits(self.main(repo), path=path, max_count=50)
        return {
            "path": path,
            "commits": [
                {
                    "sha": c.sha,
                    "message": c.subject,
                    "author": c.author_name,
                    "date": c.author_date,
                }
                for c in commits
            ],
        }

    def diff(self, path: str, commit: str) -> Dict[str, Any]:
        repo = self.repo()
        try:
            patch = git(
                "show",
                "--format=",
                "--no-color",
                "--root",
                commit,
                "--",
                path,
                cwd=repo.bare.path,
            )
        except Exception:
            raise HTTPException(404, "No such commit") from None
        return {"path": path, "commit": commit, "diff": patch}

    # -- saving ------------------------------------------------------------------

    def save(
        self,
        account: Any,
        base_commit: str,
        changes: List[Change],
        message: Optional[str],
        *,
        check_conflicts: bool = True,
    ) -> Dict[str, Any]:
        repo = self.repo()
        paths = [c.path for c in changes]
        if len(set(paths)) != len(paths):
            raise HTTPException(422, "Each file can change once in a save")
        status, data = self.factory(
            "POST",
            "/integrations/definition/check",
            {"changes": [c.model_dump() for c in changes]},
        )
        if status >= 400:
            raise HTTPException(
                502, data.get("detail") or "The factory could not check the change"
            )
        if data.get("problems"):
            raise HTTPException(422, {"problems": data["problems"]})
        review = self.require_review()
        with self._lock:
            main = self.main(repo)
            if check_conflicts and base_commit != main:
                if repo.bare.commit(base_commit) is None:
                    raise HTTPException(
                        409, "Reload: the file changed since you opened it."
                    )
                for path in paths:
                    before = repo.bare.blob(base_commit, path)
                    now = repo.bare.blob(main, path)
                    if (before and before[0]) != (now and now[0]):
                        raise HTTPException(
                            409,
                            f"Someone else changed {path} since you opened it. Reload to see their change.",
                        )
            if not review:
                status, live = self.factory("GET", "/integrations/definition")
                if status < 400 and live.get("uncommitted"):
                    raise HTTPException(
                        409,
                        {
                            "detail": "The running factory has changes that are not committed. Commit them to its repository first.",
                            "paths": live["uncommitted"],
                        },
                    )
            files = [
                (c.path, None if c.content is None else c.content.encode("utf-8"))
                for c in changes
            ]
            existed = {p: repo.bare.blob(main, p) is not None for p in paths}
            title = (message or "").strip() or describe(files, existed)
            actor = account.github_login
            try:
                if review:
                    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
                    branch = f"edit/{actor}/{slug}-{int(time.time())}"
                    self.w.github.create_branch(
                        repo, actor, branch, repo.default_branch
                    )
                    commit = self.w.github.write_files(
                        repo, actor, branch, files, title
                    )
                    pull = self.w.github.create_pull(
                        repo,
                        actor,
                        title,
                        branch,
                        repo.default_branch,
                        "Edited in the Workplace. Merging it publishes it to the factory.",
                    )
                    return {
                        "commit": commit,
                        "published": False,
                        "pull_request": f"{self.w.base_url.rstrip('/')}/{repo.full_name}/pull/{pull.number}",
                    }
                commit = self.w.github.write_files(
                    repo,
                    actor,
                    repo.default_branch,
                    files,
                    title,
                    bypass_protection=True,
                )
            except GitHubError as exc:
                raise HTTPException(exc.status, exc.message) from None
        result = self.publish(commit)
        return {
            "commit": commit,
            "published": result["ok"],
            "detail": result.get("detail"),
        }

    def restore(self, account: Any, path: str, commit: str) -> Dict[str, Any]:
        repo = self.repo()
        if repo.bare.commit(commit) is None:
            raise HTTPException(404, "No such commit")
        found = repo.bare.blob(commit, path)
        content = None if found is None else found[1].decode("utf-8", "replace")
        return self.save(
            account,
            self.main(repo),
            [Change(path=path, content=content)],
            f"Restore {PurePosixPath(path).parent.name or path} as of {commit[:7]}",
            check_conflicts=False,
        )

    # -- publishing ----------------------------------------------------------------

    def publish(self, commit: str) -> Dict[str, Any]:
        """Ask the factory to run ``commit``; what it said."""
        try:
            status, data = self.factory(
                "POST", "/integrations/definition/advance", {"commit": commit}
            )
        except HTTPException as exc:
            status, data = exc.status_code, {"detail": exc.detail}
        if status < 400:
            result = {"commit": commit, "ok": True, "at": time.time()}
        elif status == 422 and data.get("paths"):
            result = {
                "commit": commit,
                "ok": False,
                "at": time.time(),
                "detail": "Saved. The factory will use it with its next release: main also changes "
                + ", ".join(data["paths"][:5]),
            }
        else:
            result = {
                "commit": commit,
                "ok": False,
                "at": time.time(),
                "detail": f"Saved, but the factory did not move to it: {data.get('detail') or status}",
            }
        self.published = result
        return result

    def on_github(self, kind: str, data: Dict[str, Any]) -> None:
        """Main moved (a merged pull request, a push): offer it to the factory."""
        if kind != "push" or data.get("after") in (None, NULL_SHA):
            return
        repo = data.get("repo")
        if repo is None or repo.full_name != self.repository():
            return
        if data.get("ref") != f"refs/heads/{repo.default_branch}":
            return
        commit = data["after"]

        def offer():
            try:
                self.publish(commit)
            except Exception:  # never breaks the push; the editor shows the status
                logger.exception("factory definition: publishing %s failed", commit[:7])

        threading.Thread(target=offer, daemon=True).start()


def definition_router(definition: FactoryDefinition) -> APIRouter:
    router = APIRouter(prefix="/workplace/api/factory/definition")

    @router.get("")
    def tree(request: Request):
        """What can be edited, main's commit and the commit the factory runs."""
        admin(request)
        return definition.tree()

    @router.get("/file")
    def read_file(path: str, request: Request):
        admin(request)
        return definition.read(path)

    @router.put("/files")
    def save_files(body: SaveBody, request: Request):
        """Save edits: a commit on main (published), or a pull request when review is required."""
        account = admin(request)
        return definition.save(account, body.base_commit, body.changes, body.message)

    @router.get("/history")
    def history(path: str, request: Request):
        admin(request)
        return definition.history(path)

    @router.get("/diff")
    def diff(path: str, commit: str, request: Request):
        admin(request)
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise HTTPException(422, "Expected a full commit id")
        return definition.diff(path, commit)

    @router.post("/restore")
    def restore(body: RestoreBody, request: Request):
        """Put a file back as it was at a commit: a new commit, published like any save."""
        account = admin(request)
        return definition.restore(account, body.path, body.commit)

    @router.get("/settings")
    def settings(request: Request):
        admin(request)
        return {"require_review": definition.require_review()}

    @router.put("/settings")
    def save_settings(body: SettingsBody, request: Request):
        admin(request)
        definition.w.store.set_meta(REVIEW_KEY, body.require_review)
        return {"require_review": body.require_review}

    return router
