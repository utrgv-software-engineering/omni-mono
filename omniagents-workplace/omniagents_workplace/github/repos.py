"""The repositories' git: bare repositories, and what GitHub reads and writes in them.

Each repository is a bare git repository. Its content comes from git
itself: branches are refs, a pull request's diff is ``git diff
base...head``, a file is ``git show``. Changes GitHub makes without a push
(creating a branch, committing a file, merging a pull request) are written
with git's plumbing, so a sandbox fetching afterwards sees them as it would
on GitHub.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = ["BareRepo", "GitError", "Commit", "FileChange", "NULL_SHA"]

NULL_SHA = "0" * 40


class GitError(Exception):
    """A git command failed; ``str()`` is git's message."""


@dataclass
class Commit:
    sha: str
    message: str
    author_name: str
    author_email: str
    author_date: str  # ISO 8601
    committer_name: str
    committer_email: str
    committer_date: str
    parents: List[str]

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0]


@dataclass
class FileChange:
    filename: str
    status: str  # added, removed, modified, renamed
    additions: int
    deletions: int
    patch: str
    previous_filename: Optional[str] = None

    @property
    def changes(self) -> int:
        return self.additions + self.deletions


def _env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        **(extra or {}),
    }


def git(
    *args: str,
    cwd: Optional[Path] = None,
    input: Optional[bytes] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=_env(env), input=input, capture_output=True
    )
    if proc.returncode != 0:
        raise GitError((proc.stderr or proc.stdout).decode(errors="replace").strip())
    return proc.stdout.decode(errors="replace")


def git_bytes(*args: str, cwd: Optional[Path] = None) -> bytes:
    proc = subprocess.run(["git", *args], cwd=cwd, env=_env(), capture_output=True)
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode(errors="replace").strip())
    return proc.stdout


def identity(name: str, email: str, date: Optional[str] = None) -> Dict[str, str]:
    env = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    return env


_LOG_FORMAT = "%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI%x1f%P%x1f%B%x1e"


_PRE_RECEIVE = """#!/bin/sh
status=0
while read old new ref; do
  # Protection applies to a branch that exists: the push that creates it goes through.
  [ "$old" = "0000000000000000000000000000000000000000" ] && continue
  for branch in $WORKPLACE_PROTECTED_BRANCHES; do
    if [ "$ref" = "refs/heads/$branch" ]; then
      echo "error: GH006: Protected branch update failed for $ref."
      echo "error: Changes must be made through a pull request."
      status=1
    fi
  done
done
exit $status
"""


class BareRepo:
    def __init__(self, path: Path):
        self.path = Path(path)

    # -- creating -------------------------------------------------------

    @classmethod
    def create(cls, path: Path, default_branch: str) -> "BareRepo":
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        git(
            "init", "--bare", "--quiet", f"--initial-branch={default_branch}", str(path)
        )
        repo = cls(path)
        for key, value in (
            ("http.receivepack", "true"),
            ("uploadpack.allowFilter", "true"),
            ("uploadpack.allowAnySHA1InWant", "true"),
        ):
            git("config", key, value, cwd=path)
        repo.install_hooks()
        return repo

    def install_hooks(self) -> None:
        """A push to a protected branch is refused, as GitHub refuses it
        (the branches come in ``WORKPLACE_PROTECTED_BRANCHES``)."""
        hook = self.path / "hooks" / "pre-receive"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(_PRE_RECEIVE)
        hook.chmod(0o755)

    def commit_directory(
        self,
        branch: str,
        source: Optional[Path],
        message: str,
        who: Dict[str, str],
        parent: Optional[str] = None,
        patch: Optional[str] = None,
        reverse: bool = False,
    ) -> str:
        """Commit ``source``'s files (or ``parent``'s tree), with ``patch`` applied (in
        ``reverse``, to get the content it was made from), to ``branch``."""
        with tempfile.TemporaryDirectory(prefix="workplace-seed-") as tmp:
            work = Path(tmp) / "work"
            if source is not None:
                shutil.copytree(
                    source,
                    work,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"),
                )
                git("init", "--quiet", cwd=work)
            elif parent is not None:
                git("clone", "--quiet", str(self.path), str(work))
                git("checkout", "--quiet", "--detach", parent, cwd=work)
            else:
                work.mkdir()
                git("init", "--quiet", cwd=work)
            if patch:
                git(
                    "apply",
                    "--whitespace=nowarn",
                    "--unidiff-zero",
                    *(["-R"] if reverse else []),
                    "-",
                    cwd=work,
                    input=patch.encode(),
                )
            if source is not None and parent is not None:
                # The directory is the branch's whole content, on top of ``parent``.
                git("fetch", "--quiet", str(self.path), parent, cwd=work)
                git("reset", "--soft", "FETCH_HEAD", cwd=work)
            git("add", "-A", cwd=work)
            git("commit", "--quiet", "--allow-empty", "-m", message, cwd=work, env=who)
            git(
                "push",
                "--quiet",
                "--force",
                str(self.path),
                f"HEAD:refs/heads/{branch}",
                cwd=work,
            )
        return self.ref_sha(branch)

    # -- reading --------------------------------------------------------

    def refs(self) -> Dict[str, str]:
        """``refs/heads/x`` and ``refs/tags/x`` -> sha."""
        out = git(
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/heads",
            "refs/tags",
            cwd=self.path,
        )
        refs = {}
        for line in out.splitlines():
            if line:
                name, sha = line.split(" ", 1)
                refs[name] = sha
        return refs

    def branches(self) -> Dict[str, str]:
        return {
            ref[len("refs/heads/") :]: sha
            for ref, sha in self.refs().items()
            if ref.startswith("refs/heads/")
        }

    def ref_sha(self, ref: str) -> Optional[str]:
        """The commit ``ref`` (a branch, tag, full ref or sha) names, or ``None``."""
        for candidate in (f"refs/heads/{ref}", f"refs/tags/{ref}", ref):
            try:
                return git(
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"{candidate}^{{commit}}",
                    cwd=self.path,
                ).strip()
            except GitError:
                continue
        return None

    def commits(
        self,
        rev: str,
        *,
        path: Optional[str] = None,
        max_count: Optional[int] = None,
        skip: int = 0,
        author: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Commit]:
        args = ["log", f"--format={_LOG_FORMAT}", f"--skip={skip}"]
        if max_count:
            args.append(f"--max-count={max_count}")
        if author:
            args.append(f"--author={author}")
        if since:
            args.append(f"--since={since}")
        if until:
            args.append(f"--until={until}")
        args.append(rev)
        if path:
            args += ["--", path]
        try:
            out = git(*args, cwd=self.path)
        except GitError:
            return []
        return [
            self._parse_commit(chunk) for chunk in out.split("\x1e") if chunk.strip()
        ]

    def last_commits(
        self, rev: str, path: str, names: Sequence[str]
    ) -> Dict[str, Commit]:
        """For each of a directory's entries (``names``, in ``path``), the last
        commit on ``rev`` that changed it: one walk of the directory's history,
        newest first."""
        wanted = set(names)
        found: Dict[str, Commit] = {}
        args = [
            "log",
            f"--format=%x1d{_LOG_FORMAT}",
            "--name-only",
            "--no-renames",
            rev,
        ]
        if path:
            args += ["--", path]
        try:
            out = git(*args, cwd=self.path)
        except GitError:
            return found
        prefix = f"{path}/" if path else ""
        for chunk in out.split("\x1d"):
            if not chunk.strip() or len(found) == len(wanted):
                continue
            head, _, files = chunk.partition("\x1e")
            commit = None
            for line in files.splitlines():
                if not line or not line.startswith(prefix):
                    continue
                entry = line[len(prefix) :].split("/", 1)[0]
                if entry in wanted and entry not in found:
                    commit = commit or self._parse_commit(head)
                    found[entry] = commit
        return found

    def commit(self, sha: str) -> Optional[Commit]:
        found = self.commits(sha, max_count=1)
        return found[0] if found else None

    @staticmethod
    def _parse_commit(chunk: str) -> Commit:
        fields = chunk.lstrip("\n").split("\x1f")
        sha, an, ae, ad, cn, ce, cd, parents, body = (fields + [""] * 9)[:9]
        return Commit(sha, body.rstrip("\n"), an, ae, ad, cn, ce, cd, parents.split())

    def tree(
        self, rev: str, path: str = ""
    ) -> Optional[List[Tuple[str, str, str, int]]]:
        """``(type, name, sha, size)`` of a directory's entries, or ``None`` when ``path`` is no directory."""
        spec = f"{rev}:{path}" if path else f"{rev}:"
        try:
            kind = git("cat-file", "-t", spec, cwd=self.path).strip()
        except GitError:
            return None
        if kind != "tree":
            return None
        out = git("ls-tree", "-l", spec, cwd=self.path)
        entries = []
        for line in out.splitlines():
            meta, name = line.split("\t", 1)
            mode, otype, sha, size = meta.split()
            kind = {
                "blob": "symlink" if mode == "120000" else "file",
                "tree": "dir",
                "commit": "submodule",
            }[otype]
            entries.append((kind, name, sha, int(size) if size.isdigit() else 0))
        return entries

    def blob(self, rev: str, path: str) -> Optional[Tuple[str, bytes]]:
        """``(blob sha, content)`` of a file, or ``None``."""
        try:
            sha = git(
                "rev-parse", "--verify", "--quiet", f"{rev}:{path}", cwd=self.path
            ).strip()
            if git("cat-file", "-t", sha, cwd=self.path).strip() != "blob":
                return None
            return sha, git_bytes("cat-file", "blob", sha, cwd=self.path)
        except GitError:
            return None

    def files(self, rev: str) -> List[str]:
        try:
            return git("ls-tree", "-r", "--name-only", rev, cwd=self.path).splitlines()
        except GitError:
            return []

    def ahead_behind(self, base: str, head: str) -> Tuple[int, int]:
        """How many commits ``head`` has that ``base`` lacks, and the reverse."""
        try:
            out = git(
                "rev-list", "--left-right", "--count", f"{base}...{head}", cwd=self.path
            ).split()
        except GitError:
            return 0, 0
        behind, ahead = (int(x) for x in out[:2])
        return ahead, behind

    def set_head(self, branch: str) -> None:
        """The branch a clone checks out."""
        git("symbolic-ref", "HEAD", f"refs/heads/{branch}", cwd=self.path)

    def merge_base(self, a: str, b: str) -> Optional[str]:
        try:
            return git("merge-base", a, b, cwd=self.path).strip()
        except GitError:
            return None

    def diff(self, base: str, head: str) -> str:
        """``git diff base...head``, as GitHub shows a pull request's diff."""
        mb = self.merge_base(base, head) or base
        return git(
            "diff",
            "--no-color",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "-M",
            mb,
            head,
            cwd=self.path,
        )

    def changes(
        self, base: str, head: str, *, three_dot: bool = True
    ) -> List[FileChange]:
        """The files ``head`` changes from ``base`` (from their merge base, when ``three_dot``)."""
        start = (self.merge_base(base, head) or base) if three_dot else base
        status_out = git("diff", "--name-status", "-M", start, head, cwd=self.path)
        numstat = {}
        for line in git(
            "diff", "--numstat", "-M", start, head, cwd=self.path
        ).splitlines():
            added, deleted, name = line.split("\t", 2)
            if " => " in name:
                name = _renamed_to(name)
            numstat[name] = (
                int(added) if added.isdigit() else 0,
                int(deleted) if deleted.isdigit() else 0,
            )
        changes = []
        for line in status_out.splitlines():
            parts = line.split("\t")
            code = parts[0][0]
            previous = None
            if code == "R":
                previous, name = parts[1], parts[2]
            else:
                name = parts[1]
            status = {
                "A": "added",
                "D": "removed",
                "M": "modified",
                "R": "renamed",
                "T": "changed",
            }.get(code, "modified")
            added, deleted = numstat.get(name, (0, 0))
            patch = git(
                "diff",
                "--no-color",
                "-M",
                start,
                head,
                "--",
                *(p for p in (previous, name) if p),
                cwd=self.path,
            )
            changes.append(
                FileChange(name, status, added, deleted, _hunks(patch), previous)
            )
        return changes

    def commit_paths(self, sha: str) -> Tuple[List[str], List[str], List[str]]:
        """``(added, removed, modified)`` paths of one commit, against its first parent."""
        commit = self.commit(sha)
        if commit is None:
            return [], [], []
        if commit.parents:
            out = git(
                "diff",
                "--name-status",
                "--no-renames",
                commit.parents[0],
                sha,
                cwd=self.path,
            )
        else:
            out = "".join(f"A\t{p}\n" for p in self.files(sha))
        added, removed, modified = [], [], []
        for line in out.splitlines():
            code, path = line.split("\t", 1)
            {"A": added, "D": removed}.get(code[0], modified).append(path)
        return added, removed, modified

    def mergeable(self, base: str, head: str) -> bool:
        try:
            git("merge-tree", "--write-tree", base, head, cwd=self.path)
            return True
        except GitError:
            return False

    def grep(self, rev: str, pattern: str) -> List[Tuple[str, int, str]]:
        """``(path, line number, line)`` matching ``pattern`` (fixed, case-insensitive) at ``rev``."""
        try:
            out = git("grep", "-n", "-I", "-i", "-F", "-e", pattern, rev, cwd=self.path)
        except GitError:
            return []
        hits = []
        for line in out.splitlines():
            _, path, number, text = line.split(":", 3)
            hits.append((path, int(number), text))
        return hits

    # -- writing --------------------------------------------------------

    def set_ref(self, ref: str, sha: str, old: Optional[str] = None) -> None:
        args = ["update-ref", ref, sha] + ([old] if old else [])
        git(*args, cwd=self.path)

    def delete_ref(self, ref: str) -> None:
        git("update-ref", "-d", ref, cwd=self.path)

    def write_files(
        self,
        branch: str,
        files: Sequence[Tuple[str, Optional[bytes]]],
        message: str,
        who: Dict[str, str],
    ) -> str:
        """Commit ``files`` (path, content or ``None`` to delete) on top of ``branch``; the new sha."""
        parent = self.ref_sha(branch)
        with tempfile.TemporaryDirectory(prefix="workplace-index-") as tmp:
            index = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if parent:
                git("read-tree", parent, cwd=self.path, env=index)
            for path, content in files:
                if content is None:
                    # git wants a work tree to remove from the index: an empty one.
                    git(
                        "update-index",
                        "--force-remove",
                        "--",
                        path,
                        cwd=self.path,
                        env={**index, "GIT_WORK_TREE": tmp},
                    )
                    continue
                blob = git(
                    "hash-object", "-w", "--stdin", cwd=self.path, input=content
                ).strip()
                git(
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},{path}",
                    cwd=self.path,
                    env=index,
                )
            tree = git("write-tree", cwd=self.path, env=index).strip()
        args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
        sha = git(*args, cwd=self.path, env=who).strip()
        self.set_ref(f"refs/heads/{branch}", sha, parent)
        return sha

    def merge(
        self, base: str, head: str, method: str, message: str, who: Dict[str, str]
    ) -> str:
        """Merge ``head`` into the ``base`` branch (merge, squash or rebase); the new tip."""
        base_sha = self.ref_sha(base)
        head_sha = self.ref_sha(head)
        if base_sha is None or head_sha is None:
            raise GitError("Base or head does not exist")
        tree = (
            git("merge-tree", "--write-tree", base_sha, head_sha, cwd=self.path)
            .splitlines()[0]
            .strip()
        )
        if method == "merge":
            parents = ["-p", base_sha, "-p", head_sha]
        else:  # squash and rebase both land one commit's worth of change on base here
            parents = ["-p", base_sha]
        sha = git(
            "commit-tree", tree, *parents, "-m", message, cwd=self.path, env=who
        ).strip()
        self.set_ref(f"refs/heads/{base}", sha, base_sha)
        return sha


def _renamed_to(name: str) -> str:
    # numstat writes renames as "dir/{old => new}/f" or "old => new".
    if "{" in name:
        prefix, rest = name.split("{", 1)
        inner, suffix = rest.split("}", 1)
        return prefix + inner.split(" => ", 1)[1] + suffix
    return name.split(" => ", 1)[1]


_HUNK = re.compile(r"^@@ ", re.M)


def _hunks(patch: str) -> str:
    """A file's patch as GitHub's ``patch`` field has it: from the first hunk."""
    match = _HUNK.search(patch)
    return patch[match.start() :].rstrip("\n") if match else ""
