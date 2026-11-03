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
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .accounts import admin
from .github.repos import NULL_SHA, git
from .github.state import GitHubError

logger = logging.getLogger(__name__)

REVIEW_KEY = "factory.definition.require_review"
#: A repository in this Workplace, as a git URL names it (``.../github/<owner>/<name>.git``).
_REPOSITORY_URL = re.compile(
    r"/github/(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


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


class SkillBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    #: ``<skills folder>/<name>/SKILL.md``; a new one is created.
    path: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=1024)
    #: The steps: the file's text after its frontmatter.
    body: str = Field(default="", max_length=500_000)


class AgentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: Its folder's name and its ``name:``: a-z, 0-9 and _.
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    description: str = Field(min_length=1, max_length=1024)
    #: The template (a base) it builds on, if any.
    template: Optional[str] = Field(default=None, max_length=200)
    #: Whether Chat lists it.
    chat: bool = False


class AgentSettingsBody(BaseModel):
    """The settings to change; those left out stay as they are."""

    model_config = ConfigDict(extra="forbid")
    label: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1024)
    #: ``None``: the template's.
    model: Optional[str] = Field(default=None, max_length=200)
    capabilities: Optional[List[str]] = Field(default=None, max_length=100)
    #: The agents it can call.
    calls: Optional[List[str]] = Field(default=None, max_length=100)
    chat: Optional[bool] = None
    chat_default: Optional[bool] = None
    automation_default: Optional[bool] = None


class AutomationBody(BaseModel):
    """An automation as fields: what starts it, who does it, what it's told."""

    model_config = ConfigDict(extra="forbid")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    description: str = Field(min_length=1, max_length=1024)
    #: ``None``: the agent automations run by default.
    agent: Optional[str] = Field(default=None, max_length=64)
    #: Each ``{provider, event, filter?}`` or ``{provider: "schedule", schedule}``.
    triggers: List[Dict[str, Any]] = Field(min_length=1, max_length=20)
    instruction: str = Field(min_length=1, max_length=100_000)
    conversation: Literal["auto", "thread", "new"] = "auto"


class DeleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    path: str = Field(min_length=1, max_length=300)


class MoveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    #: The skill's ``SKILL.md``.
    path: str = Field(min_length=1, max_length=300)
    #: The skills folder it moves to (an agent's, a base's, the factory's).
    to: str = Field(min_length=1, max_length=300)
    message: str = Field(min_length=1, max_length=200)


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
        if parts[-1] == "agent.yml" and len(parts) >= 2:
            return f"{verb} {parts[-2].replace('_', ' ')} settings"
        if path == "project.yml":
            return "Change the agents in Chat"
        if len(parts) >= 2:
            return f"{verb} {parts[-2].replace('_', ' ')} instructions"
        return f"{verb} {path}"

    lines = [one(path, content) for path, content in changes]
    return lines[0] if len(lines) == 1 else f"{lines[0]} and {len(lines) - 1} more"


def split_skill(text: str) -> Tuple[Dict[str, Any], str]:
    """A ``SKILL.md``'s frontmatter (as a mapping) and its body."""
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.S)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), match.group(2).lstrip("\n")


def join_skill(meta: Dict[str, Any], body: str) -> str:
    """A ``SKILL.md`` from its frontmatter and body."""
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{front}---\n\n{body.strip()}\n"


def split_automation(text: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """An automation file's frontmatter and instruction, or ``None`` when it
    is more than a form can show (template logic in its frontmatter)."""
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.S)
    if not match or "{%" in match.group(1):
        return None
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    return (meta, match.group(2).strip()) if isinstance(meta, dict) else None


def join_automation(meta: Dict[str, Any], instruction: str) -> str:
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{front}---\n\n{instruction.strip()}\n"


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
        #: The repository the factory said its checkout came from.
        self._origin: Optional[str] = None
        self._lock = threading.Lock()

    # -- the parts ------------------------------------------------------------

    def require_review(self) -> bool:
        return bool(self.w.store.meta(REVIEW_KEY, False))

    def known_name(self) -> Optional[str]:
        """The factory's repository, as far as is known without asking it."""
        return self.repository() or self._origin

    def repo(self) -> Any:
        """The repository the factory runs from, here, whichever provider
        serves the product repositories: workplace.yml's ``factory.repository``,
        else the one the factory's checkout came from (a hosted factory's)."""
        name = self.known_name()
        if self.w.github.repos.get(name or "") is None:
            status, data = self.factory("GET", "/integrations/definition")
            found = _REPOSITORY_URL.search(
                (data.get("origin") or "") if status < 400 else ""
            )
            name = f"{found['owner']}/{found['name']}" if found else None
            if name in self.w.github.repos:
                self._origin = name
        repo = self.w.github.repos.get(name or "")
        if repo is None:
            raise HTTPException(
                409,
                "The factory doesn't run from a repository in this Workplace, so it can't be edited here.",
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

    def skill(self, path: str) -> Dict[str, Any]:
        """A skill as its fields: name, when to use it, its steps."""
        found = self.read(path)
        meta, body = split_skill(found["content"])
        return {
            "path": path,
            "name": PurePosixPath(path).parent.name,
            "description": str(meta.get("description") or ""),
            "body": body,
            "commit": found["commit"],
        }

    def save_skill(self, account: Any, body: SkillBody) -> Dict[str, Any]:
        """Save a skill's fields, keeping the rest of its frontmatter."""
        pure = PurePosixPath(body.path)
        if pure.name != "SKILL.md" or len(pure.parts) < 3:
            raise HTTPException(422, "A skill is <folder>/<name>/SKILL.md")
        repo = self.repo()
        found = repo.bare.blob(body.base_commit, body.path)
        meta, _ = (
            split_skill(found[1].decode("utf-8", "replace")) if found else ({}, "")
        )
        meta = {"name": pure.parent.name, **meta}
        meta["name"] = pure.parent.name
        meta["description"] = body.description.strip()
        return self.save(
            account,
            body.base_commit,
            [Change(path=body.path, content=join_skill(meta, body.body))],
            None,
        )

    def move_skill(self, account: Any, body: MoveBody) -> Dict[str, Any]:
        """Move a skill's folder, every file in it, to another skills folder."""
        pure = PurePosixPath(body.path)
        if pure.name != "SKILL.md" or len(pure.parts) < 3:
            raise HTTPException(422, "A skill is <folder>/<name>/SKILL.md")
        repo = self.repo()
        folder = pure.parent.as_posix()
        target = f"{body.to.rstrip('/')}/{pure.parent.name}"
        if target == folder:
            raise HTTPException(422, "The skill is already there")
        files = [
            f for f in repo.bare.files(body.base_commit) if f.startswith(folder + "/")
        ]
        if not files:
            raise HTTPException(404, "No such skill")
        if any(f.startswith(target + "/") for f in repo.bare.files(self.main(repo))):
            raise HTTPException(409, f"There is already a skill at {target}")
        changes: List[Change] = []
        for f in files:
            content = repo.bare.blob(body.base_commit, f)[1]
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(
                    422, f"{f} is not text; move this skill by pull request"
                ) from None
            changes += [
                Change(path=f, content=None),
                Change(path=target + f[len(folder) :], content=text),
            ]
        return self.save(account, body.base_commit, changes, body.message)

    def add_agent(self, account: Any, body: AgentBody) -> Dict[str, Any]:
        """Add an agent: the factory says which files (its definition, its
        instructions, Chat's list), and saving them publishes it."""
        repo = self.repo()
        status, data = self.factory(
            "POST", "/integrations/definition/new-agent", body.model_dump()
        )
        if status >= 400:
            raise HTTPException(status, data.get("detail") or "Unable to add the agent")
        name = body.key.replace("_", " ").title()
        saved = self.save(
            account,
            self.main(repo),
            [Change(path=c["path"], content=c["content"]) for c in data["changes"]],
            f"Add agent {name}",
        )
        return {**saved, "agent": body.key}

    def agent_settings(self, key: str) -> Dict[str, Any]:
        self.repo()
        status, data = self.factory("GET", f"/integrations/definition/agents/{key}")
        if status >= 400:
            raise HTTPException(
                status, data.get("detail") or "Unable to read the agent"
            )
        return data

    def change_agent(
        self, account: Any, key: str, body: Dict[str, Any], message: str
    ) -> Dict[str, Any]:
        """Save what the factory says ``body`` (``{settings}`` or ``{delete}``) means."""
        repo = self.repo()
        status, data = self.factory(
            "POST", f"/integrations/definition/agents/{key}/changes", body
        )
        if status >= 400:
            raise HTTPException(
                status, data.get("detail") or "Unable to change the agent"
            )
        if not data["changes"]:
            return {"commit": self.main(repo), "published": True, "detail": None}
        return self.save(
            account,
            self.main(repo),
            [Change(path=c["path"], content=c["content"]) for c in data["changes"]],
            message,
        )

    def automation(self, name: str) -> Dict[str, Any]:
        """An automation as fields, or ``fields: None`` when only its file can show it."""
        path = f"automations/{name}/automation.md"
        found = self.read(path)
        parsed = split_automation(found["content"])
        fields = None
        if parsed is not None:
            meta, instruction = parsed
            fields = {
                "description": str(meta.get("description") or ""),
                "agent": meta.get("agent"),
                "triggers": meta.get("triggers") or [],
                "instruction": instruction,
                "conversation": meta.get("conversation") or "auto",
            }
        return {"name": name, "path": path, "commit": found["commit"], "fields": fields}

    def save_automation(
        self, account: Any, name: str, body: AutomationBody
    ) -> Dict[str, Any]:
        """Create or change an automation from its fields, keeping any other
        settings its file has."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise HTTPException(422, "A name is lowercase letters, digits, - and _")
        repo = self.repo()
        path = f"automations/{name}/automation.md"
        found = repo.bare.blob(body.base_commit, path)
        parsed = (
            split_automation(found[1].decode("utf-8", "replace")) if found else ({}, "")
        )
        if parsed is None:
            raise HTTPException(
                409, "This automation's file has template logic; edit the file instead"
            )
        meta = dict(parsed[0])
        meta["description"] = body.description.strip()
        if body.agent:
            meta["agent"] = body.agent
        else:
            meta.pop("agent", None)
        meta["triggers"] = body.triggers
        if body.conversation == "auto":
            meta.pop("conversation", None)
        else:
            meta["conversation"] = body.conversation
        ordered = {
            k: meta[k]
            for k in ("description", "agent", "conversation", "triggers")
            if k in meta
        }
        ordered.update({k: v for k, v in meta.items() if k not in ordered})
        return self.save(
            account,
            body.base_commit,
            [Change(path=path, content=join_automation(ordered, body.instruction))],
            None,
        )

    def delete_automation(self, account: Any, name: str) -> Dict[str, Any]:
        repo = self.repo()
        main = self.main(repo)
        folder = f"automations/{name}/"
        files = [f for f in repo.bare.files(main) if f.startswith(folder)]
        if not files:
            raise HTTPException(404, "No such automation")
        return self.save(
            account,
            main,
            [Change(path=f, content=None) for f in files],
            f"Delete automation {name}",
        )

    def delete_skill(self, account: Any, base_commit: str, path: str) -> Dict[str, Any]:
        """Delete a skill: every file in its folder."""
        pure = PurePosixPath(path)
        if pure.name != "SKILL.md" or len(pure.parts) < 3:
            raise HTTPException(422, "A skill is <folder>/<name>/SKILL.md")
        repo = self.repo()
        folder = pure.parent.as_posix() + "/"
        files = [f for f in repo.bare.files(base_commit) if f.startswith(folder)]
        if not files:
            raise HTTPException(404, "No such skill")
        return self.save(
            account,
            base_commit,
            [Change(path=f, content=None) for f in files],
            f"Delete skill {pure.parent.name}",
        )

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
        if repo is None or data.get("ref") != f"refs/heads/{repo.default_branch}":
            return
        known = self.known_name()
        if known and repo.full_name != known:
            return
        commit = data["after"]

        def offer():
            try:
                # Not known yet: ask the factory which repository it runs from.
                if not known and self.repo().full_name != repo.full_name:
                    return
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

    @router.get("/skill")
    def read_skill(path: str, request: Request):
        """A skill's fields: name, when to use it, its steps."""
        admin(request)
        return definition.skill(path)

    @router.put("/skill")
    def save_skill(body: SkillBody, request: Request):
        account = admin(request)
        return definition.save_skill(account, body)

    @router.post("/agent")
    def add_agent(body: AgentBody, request: Request):
        """Add an agent; it joins the running factory when its commit is published."""
        account = admin(request)
        return definition.add_agent(account, body)

    @router.get("/agents/{key}")
    def agent_settings(key: str, request: Request):
        """An agent's settings as fields, what it inherits, and the choices."""
        admin(request)
        return definition.agent_settings(key)

    @router.put("/agents/{key}")
    def change_agent(key: str, body: AgentSettingsBody, request: Request):
        account = admin(request)
        name = key.replace("_", " ").title()
        return definition.change_agent(
            account,
            key,
            {"settings": body.model_dump(exclude_unset=True)},
            f"Change {name}'s settings",
        )

    @router.delete("/agents/{key}")
    def delete_agent(key: str, request: Request):
        account = admin(request)
        name = key.replace("_", " ").title()
        return definition.change_agent(
            account, key, {"delete": True}, f"Delete agent {name}"
        )

    @router.get("/automations/{name}")
    def read_automation(name: str, request: Request):
        """An automation as fields: what starts it, who does it, what it's told."""
        admin(request)
        return definition.automation(name)

    @router.put("/automations/{name}")
    def save_automation(name: str, body: AutomationBody, request: Request):
        """Create or change an automation from its fields."""
        account = admin(request)
        return definition.save_automation(account, name, body)

    @router.delete("/automations/{name}")
    def delete_automation(name: str, request: Request):
        account = admin(request)
        return definition.delete_automation(account, name)

    @router.post("/skill/delete")
    def delete_skill(body: DeleteBody, request: Request):
        account = admin(request)
        return definition.delete_skill(account, body.base_commit, body.path)

    @router.post("/skill/move")
    def move_skill(body: MoveBody, request: Request):
        """Give a skill to every agent, or to one: its folder moves."""
        account = admin(request)
        return definition.move_skill(account, body)

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
