"""Stand-in workflow discovery and event filtering; execution belongs to act."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .state import Repo
WORKFLOWS_DIR = ".github/workflows"


@dataclass
class Workflow:
    path: str
    name: str
    doc: dict[str, Any]

    @property
    def on(self) -> dict[str, Any]:
        # YAML 1.1 reads a bare ``on:`` key as True.
        raw = self.doc.get("on", self.doc.get(True))
        if isinstance(raw, str):
            return {raw: {}}
        if isinstance(raw, list):
            return {str(e): {} for e in raw}
        return {str(k): (v or {}) for k, v in (raw or {}).items()}


def workflows_at(repo: Repo, sha: str) -> list[Workflow]:
    """The workflows at ``sha`` (none when it has no ``.github/workflows``)."""
    entries = repo.bare.tree(sha, WORKFLOWS_DIR) or []
    found = []
    for kind, name, *_ in sorted(entries, key=lambda e: e[1]):
        if kind != "file" or not name.endswith((".yml", ".yaml")):
            continue
        blob = repo.bare.blob(sha, f"{WORKFLOWS_DIR}/{name}")
        if blob is None:
            continue
        path = f"{WORKFLOWS_DIR}/{name}"
        try:
            doc = yaml.safe_load(blob[1].decode(errors="replace")) or {}
        except yaml.YAMLError as exc:
            doc = {"name": name, "_error": f"{path} is not valid YAML: {exc}"}
        if not isinstance(doc, dict):
            doc = {"name": name, "_error": f"{path} is not a workflow"}
        found.append(Workflow(path=path, name=str(doc.get("name") or path), doc=doc))
    return found


def _glob(pattern: str) -> re.Pattern:
    """GitHub's filter patterns: ``*`` stops at ``/``, ``**`` does not."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(f"^{out}$")


def _filtered(
    values: list[str],
    include: list[str] | None,
    exclude: list[str] | None,
    *,
    any_match: bool,
) -> bool:
    if include is not None:
        hits = [
            v
            for v in values
            if any(_glob(p).match(v) for p in include if not p.startswith("!"))
            and not any(_glob(p[1:]).match(v) for p in include if p.startswith("!"))
        ]
        return bool(hits) if any_match else len(hits) == len(values)
    if exclude is not None:
        kept = [v for v in values if not any(_glob(p).match(v) for p in exclude)]
        return bool(kept)
    return True


def triggered(
    workflow: Workflow,
    event: str,
    branch: str,
    paths: list[str] | None = None,
    action: str = "",
) -> bool:
    """Whether ``event`` (on ``branch``, changing ``paths``) runs ``workflow``."""
    filters = workflow.on.get(event)
    if filters is None:
        return False
    if not isinstance(filters, dict):
        filters = {}
    if event == "pull_request":
        types = filters.get("types") or ["opened", "synchronize", "reopened"]
        if action and action not in types:
            return False
    as_list = lambda key: [str(x) for x in filters[key]] if key in filters else None
    if not _filtered(
        [branch], as_list("branches"), as_list("branches-ignore"), any_match=True
    ):
        return False
    if paths is not None and (
        as_list("paths") is not None or as_list("paths-ignore") is not None
    ):
        if not paths:
            return False
        if not _filtered(
            paths, as_list("paths"), as_list("paths-ignore"), any_match=True
        ):
            return False
    return True


def changed_paths(repo: Repo, base: str | None, head: str) -> list[str]:
    if not base:
        commit = repo.bare.commit(head)
        if commit is None or not commit.parents:
            return (
                [path for path in repo.bare.files(head)]
                if hasattr(repo.bare, "files")
                else []
            )
        base = commit.parents[0]
    return [c.filename for c in repo.bare.changes(base, head, three_dot=False)] + [
        c.previous_filename
        for c in repo.bare.changes(base, head, three_dot=False)
        if c.previous_filename
    ]


def job_names(workflow: Workflow) -> list[str]:
    # One check per job; act owns matrix expansion and all expression evaluation.
    return [
        (
            str(spec.get("name") or key)
            if "${{" not in str(spec.get("name", ""))
            else str(key)
        )
        for key, spec in (workflow.doc.get("jobs") or {}).items()
    ] or [workflow.name]


from .act_runner import ActionsRunner  # noqa: F401
