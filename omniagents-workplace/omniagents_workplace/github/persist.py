"""GitHub's state in the workplace's store: written as it changes, read at start.

Repositories' content is git, in the state's ``root``; this keeps the rest:
people, repositories' settings, issues and pull requests (each one document,
with its comments, reviews and threads) and workflow runs.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from ..store import Store, decode, encode
from .repos import BareRepo
from .state import GitHubState, Issue, Repo, User, Webhook, WorkflowRun

__all__ = ["GitHubPersistence"]

_REPO_EXCLUDE = ("bare", "issues", "runs")


class GitHubPersistence:
    def __init__(self, state: GitHubState, store: Store):
        self.state = state
        self.store = store

    def attach(self) -> None:
        self.state.subscribe(self._on_change)

    # -- everything at once -------------------------------------------------------

    def save_all(self) -> None:
        state = self.state
        with self.store.transaction():
            for kind in ("github.user", "github.repo", "github.issue", "github.run"):
                self.store.delete_kind(kind)
            for user in state.users.values():
                self.store.put("github.user", user.login, user)
            for repo in state.repos.values():
                self._put_repo(repo)
                for issue in repo.issues.values():
                    self._put_issue(repo, issue)
                for run in repo.runs:
                    self._put_run(repo, run)
            self._save_meta()

    def load(self, *, resume: bool = True) -> None:
        state = self.state
        with state._lock:
            state.next_id = int(self.store.meta("github.next_id", 1001))
            state.hooks = [
                Webhook(**raw) for raw in self.store.meta("github.hooks", [])
            ]
            state.users = {u.login: u for u in self.store.load("github.user", User)}
            state.repos = {}
            for _, doc in self.store.docs("github.repo"):
                owner, name = doc["full_name"].split("/", 1)
                bare = BareRepo(state.root / owner / f"{name}.git")
                bare.install_hooks()
                repo = decode(Repo, doc, self.store, bare=bare, issues={}, runs=[])
                state.repos[repo.full_name] = repo
            for key, doc in self.store.docs("github.issue"):
                full_name = key.rsplit("#", 1)[0]
                issue = decode(Issue, doc, self.store)
                state.repos[full_name].issues[issue.number] = issue
            for key, doc in self.store.docs("github.run"):
                full_name = key.rsplit(":", 1)[0]
                state.repos[full_name].runs.append(decode(WorkflowRun, doc, self.store))
            for repo in state.repos.values():
                repo.runs.sort(key=lambda r: r.id)
        # Runs a restart interrupted end cancelled: their steps stopped with the server.
        for repo in state.repos.values() if resume else ():
            for run in repo.runs:
                if run.status == "completed":
                    continue
                for job in run.jobs:
                    if job.status != "completed":
                        job.status, job.conclusion, job.completed_at = (
                            "completed",
                            "cancelled",
                            time.time(),
                        )
                        job.log += "The workplace restarted while this job ran.\n"
                run.status, run.conclusion = "completed", "cancelled"
                self._put_run(repo, run)

    # -- as it changes ----------------------------------------------------------------

    def _on_change(self, kind: str, data: Dict[str, Any]) -> None:
        if kind == "reset":
            self.save_all()
            return
        state = self.state
        if kind == "repo_deleted":
            name = data["repo"].full_name
            with self.store.transaction():
                self.store.delete("github.repo", name)
                self.store.delete_prefix("github.issue", f"{name}#")
                self.store.delete_prefix("github.run", f"{name}:")
                self._save_meta()
            return
        repo_named = data.get("repo")
        if (
            repo_named is not None
            and state.repos.get(repo_named.full_name) is not repo_named
        ):
            return  # a deleted repository's last word (a run it was cancelling)
        with self.store.transaction():
            if kind == "user":
                self.store.put("github.user", data["user"].login, data["user"])
            repo: Optional[Repo] = data.get("repo")
            issue: Optional[Issue] = data.get("issue")
            if issue is None and kind == "reaction":
                repo, issue = self._containing(data.get("target"))
            if issue is None and kind == "thread_resolved":
                repo, issue, _ = state.find_thread(data["thread"].id)
            if issue is not None and repo is None:
                repo = self._repo_of(issue)
            if repo is not None:
                self._put_repo(repo)
            if repo is not None and issue is not None:
                self._put_issue(repo, issue)
            if repo is not None and data.get("run") is not None:
                self._put_run(repo, data["run"])
            self._save_meta()

    def _containing(self, target: Any) -> Tuple[Optional[Repo], Optional[Issue]]:
        """The repository and issue a comment, review comment or issue belongs to."""
        for repo in self.state.repos.values():
            for issue in repo.issues.values():
                if issue is target or any(c is target for c in issue.comments):
                    return repo, issue
                if issue.pull and any(
                    c is target for t in issue.pull.threads for c in t.comments
                ):
                    return repo, issue
        return None, None

    def _repo_of(self, issue: Issue) -> Optional[Repo]:
        return next(
            (
                r
                for r in self.state.repos.values()
                if r.issues.get(issue.number) is issue
            ),
            None,
        )

    def _put_repo(self, repo: Repo) -> None:
        self.store.put("github.repo", repo.full_name, repo, exclude=_REPO_EXCLUDE)

    def _put_issue(self, repo: Repo, issue: Issue) -> None:
        self.store.put("github.issue", f"{repo.full_name}#{issue.number}", issue)

    def _put_run(self, repo: Repo, run: WorkflowRun) -> None:
        self.store.put("github.run", f"{repo.full_name}:{run.id}", run)

    def _save_meta(self) -> None:
        state = self.state
        self.store.set_meta("github.next_id", state.next_id)
        self.store.set_meta("github.hooks", [encode(h) for h in state.hooks])
