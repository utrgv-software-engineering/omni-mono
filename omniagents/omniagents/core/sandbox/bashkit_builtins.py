"""Real ``git`` and real ``curl`` for the in-browser sandbox.

bashkit's wasm build has neither. Its own ``git`` feature is a simulation —
the commit "hash" is ``timestamp ^ 0xdeadbeef``, so commits made in the same
second collide, and ``.git`` holds text files instead of an object store —
which is worse than nothing for teaching, since it models git backwards. And
its HTTP client cannot be compiled to wasm at all: ``http_client`` pulls
``reqwest`` → ``mio``, whose net feature has no wasm target.

Neither limit needs Rust to fix. bashkit runs Python callbacks as bash
builtins, and sync callbacks work in the wasm build, so the shell command
can be a thin front end over a Python library that already works in Pyodide:

* ``git``  → dulwich. Pure Python, publishes a ``py3-none-any`` wheel, and
  produces a genuine content-addressed object store: distinct SHA-1s, parent
  links, trees and blobs a student can actually inspect.
* ``curl`` → httpx, which is how the platform already reaches the model.

**How git bridges filesystems.** dulwich works against ordinary paths, so it
cannot see bashkit's VFS. Each invocation therefore mirrors the workspace
subtree out to the host/Emscripten filesystem, runs dulwich there, and
mirrors the result — ``.git`` included — back in. Deletions propagate both
ways, so ``git checkout`` dropping a file is reflected in the shell. The
copies are bounded by the workspace, which for a lesson is a handful of small
files.

Two behaviours deliberately differ from bashkit's built-in network handling:
a blocked URL is a loud error rather than a silent empty result (bashkit
exits 0 with no output, which reads as "the server returned nothing"), and
the allowlist is enforced before any request is issued.
"""

from __future__ import annotations

import io
import os
import posixpath
import shutil
from typing import Any, Callable, Dict, List, Optional, Sequence

_DIR_TYPES = {"directory", "dir"}

DEFAULT_AUTHOR = "Student <student@example.com>"


# --------------------------------------------------------------------------
# VFS <-> real filesystem mirroring
# --------------------------------------------------------------------------


def _vfs_entries(fs: Any, root: str) -> List[tuple]:
    """``(path, is_dir)`` for everything under ``root`` in the bashkit VFS."""
    found: List[tuple] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = fs.read_dir(current)
        except Exception:
            continue
        for entry in entries:
            name = entry.get("name")
            if not name:
                continue
            path = posixpath.join(current, name)
            is_dir = (entry.get("metadata") or {}).get("file_type") in _DIR_TYPES
            found.append((path, is_dir))
            if is_dir:
                pending.append(path)
    return found


def _mirror_in(fs: Any, root: str, mirror: str) -> set:
    """Copy the VFS subtree at ``root`` onto the real filesystem.

    Returns the set of VFS paths mirrored, which :func:`_mirror_out` needs as
    a baseline to tell "git deleted this" from "the shell created this after
    the mirror was taken".
    """
    if os.path.exists(mirror):
        shutil.rmtree(mirror, ignore_errors=True)
    os.makedirs(mirror, exist_ok=True)

    baseline: set = set()
    for path, is_dir in sorted(_vfs_entries(fs, root)):
        relative = posixpath.relpath(path, root)
        target = os.path.join(mirror, relative)
        baseline.add(path)
        if is_dir:
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            with open(target, "wb") as handle:
                handle.write(fs.read_file(path))
        except Exception:
            continue
    return baseline


def _mirror_out(fs: Any, root: str, mirror: str, baseline: Optional[set] = None) -> None:
    """Copy the mirror back into the VFS.

    Deletion is the subtle part. The mirror is a snapshot taken *before* the
    command ran, so anything the shell itself wrote during the command (a
    plain ``echo > file``) exists in the VFS and not in the mirror. Pruning
    everything the mirror lacks would delete exactly those files - which is
    what happened before ``baseline`` existed: ``echo hello > f.txt`` was
    erased before ``git add`` could stage it, and every commit came out with
    an empty tree.

    So only paths that *were* mirrored in and have since disappeared from the
    mirror count as real deletions (``git rm``, ``git checkout``).
    """
    known = baseline if baseline is not None else set()
    written: set = set()
    kept_dirs: set = set()
    for directory, subdirs, files in os.walk(mirror):
        # Directories are recreated even when empty: a fresh `git init` leaves
        # `.git/objects` and `.git/refs` empty, and without them dulwich does
        # not recognise the directory as a repository at all.
        for name in subdirs:
            relative = os.path.relpath(os.path.join(directory, name), mirror)
            destination = posixpath.join(root, relative.replace(os.sep, "/"))
            if not fs.exists(destination):
                fs.mkdir(destination, True)
            kept_dirs.add(destination)
        for name in files:
            source = os.path.join(directory, name)
            relative = os.path.relpath(source, mirror)
            destination = posixpath.join(root, relative.replace(os.sep, "/"))
            parent = posixpath.dirname(destination)
            if parent and not fs.exists(parent):
                fs.mkdir(parent, True)
            with open(source, "rb") as handle:
                fs.write_file(destination, handle.read())
            written.add(destination)

    for path in sorted(known - written - kept_dirs, reverse=True):
        try:
            fs.remove(path, True)
        except Exception:
            pass


# --------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------


def _parse_author(value: str) -> bytes:
    return value.encode() if isinstance(value, str) else value


def _open_repo(porcelain: Any, path: str):
    from dulwich.repo import Repo

    repo = Repo(path)
    # Every dulwich hook shells out through subprocess, and Emscripten has no
    # processes, so any commit would raise. `no_verify=True` is not enough:
    # it suppresses pre-commit and commit-msg but not post-commit.
    repo.hooks.clear()
    return repo


def make_git_builtin(
    *,
    root: str = "/workspace",
    mirror: str = "/tmp/omniagents-git/workspace",
    author: str = DEFAULT_AUTHOR,
) -> Callable:
    """Build a ``git`` builtin backed by dulwich.

    The callback never touches the bashkit VFS. It cannot: a ``ctx.fs``
    operation inside a builtin panics in the wasm build ("failed to spawn
    thread"), because those ops may run off-thread and Pyodide has no
    threads. So the builtin reads and writes only ``mirror`` on the ordinary
    filesystem, and :class:`BashkitSandboxSession` mirrors the workspace in
    before each command and back out after - where calling ``bash.fs()`` is
    safe because it happens outside the callback.

    Args:
        root: VFS path the mirror corresponds to, used to map ``ctx.cwd``.
        mirror: real-filesystem directory holding the mirrored workspace.
        author: identity used until ``git config user.name``/``user.email``
            set one.
    """

    def git(ctx: Any):
        from bashkit import BuiltinResult

        try:
            from dulwich import porcelain
        except ImportError:
            return BuiltinResult(
                stderr=(
                    "git: dulwich is not installed. It provides the object "
                    "store this builtin runs on: pip/micropip install dulwich\n"
                ),
                exit_code=127,
            )

        argv = [str(a) for a in ctx.argv]
        if not argv:
            return BuiltinResult(stderr="usage: git <command> [<args>]\n", exit_code=1)

        os.makedirs(mirror, exist_ok=True)
        work = _work_dir(ctx, root, mirror)
        out, err, code = _run_git(porcelain, argv, work, mirror, author)
        return BuiltinResult(stdout=out, stderr=err, exit_code=code)

    return git


def _work_dir(ctx: Any, root: str, mirror: str) -> str:
    """Map the shell's cwd onto the mirror."""
    cwd = str(getattr(ctx, "cwd", root) or root)
    if cwd.startswith(root):
        relative = posixpath.relpath(cwd, root).lstrip("./")
        candidate = os.path.join(mirror, relative) if relative else mirror
    else:
        candidate = mirror
    return candidate if os.path.isdir(candidate) else mirror


def _run_git(
    porcelain: Any, argv: List[str], work: str, mirror: str, author: str
) -> tuple:
    """Dispatch one git subcommand. Returns ``(stdout, stderr, exit_code)``."""
    command, args = argv[0], argv[1:]

    def repo_root(start: str) -> Optional[str]:
        current = os.path.abspath(start)
        while True:
            if os.path.isdir(os.path.join(current, ".git")):
                return current
            parent = os.path.dirname(current)
            if parent == current or len(parent) < len(mirror):
                return None
            current = parent

    def identity(path: str) -> bytes:
        try:
            repo = _open_repo(porcelain, path)
            config = repo.get_config()
            name = config.get((b"user",), b"name").decode()
            email = config.get((b"user",), b"email").decode()
            return f"{name} <{email}>".encode()
        except Exception:
            return _parse_author(author)

    if command == "init":
        target = os.path.join(work, args[0]) if args and not args[0].startswith("-") else work
        os.makedirs(target, exist_ok=True)
        if os.path.isdir(os.path.join(target, ".git")):
            return (f"Reinitialized existing Git repository in {target}/.git/\n", "", 0)
        porcelain.init(target)
        return (f"Initialized empty Git repository in {target}/.git/\n", "", 0)

    base = repo_root(work)
    if base is None:
        return ("", "fatal: not a git repository (or any of the parent directories): .git\n", 128)

    if command == "config":
        repo = _open_repo(porcelain, base)
        config = repo.get_config()
        if len(args) >= 2:
            section, _, key = args[0].rpartition(".")
            config.set(section.encode(), key.encode(), args[1].encode())
            config.write_to_path()
            return ("", "", 0)
        if len(args) == 1:
            section, _, key = args[0].rpartition(".")
            try:
                return (config.get((section.encode(),), key.encode()).decode() + "\n", "", 0)
            except KeyError:
                return ("", "", 1)
        return ("", "usage: git config <key> [<value>]\n", 1)

    if command == "add":
        if not args:
            return ("", "Nothing specified, nothing added.\n", 1)
        paths = [os.path.join(work, a) for a in args]
        expanded: List[str] = []
        for path in paths:
            if os.path.isdir(path):
                for directory, _sub, files in os.walk(path):
                    if ".git" in directory.split(os.sep):
                        continue
                    expanded += [os.path.join(directory, f) for f in files]
            else:
                expanded.append(path)
        porcelain.add(base, expanded)
        return ("", "", 0)

    if command == "commit":
        message = None
        if "-m" in args:
            index = args.index("-m")
            if index + 1 < len(args):
                message = args[index + 1]
        if not message:
            return ("", "Aborting commit due to empty commit message.\n", 1)
        repo = _open_repo(porcelain, base)
        who = identity(base)
        sha = porcelain.commit(
            repo, message=message.encode(), author=who, committer=who
        )
        branch = "master"
        try:
            ref = repo.refs.follow(b"HEAD")[0][-1]
            branch = ref.decode().rsplit("/", 1)[-1]
        except Exception:
            pass
        return (f"[{branch} {sha.decode()[:7]}] {message}\n", "", 0)

    if command == "status":
        status = porcelain.status(base)
        lines = ["On branch " + _current_branch(porcelain, base), ""]
        staged = status.staged
        any_staged = any(staged.get(k) for k in ("add", "delete", "modify"))
        if any_staged:
            lines.append("Changes to be committed:")
            for path in staged.get("add", []):
                lines.append(f"\tnew file:   {_text(path)}")
            for path in staged.get("modify", []):
                lines.append(f"\tmodified:   {_text(path)}")
            for path in staged.get("delete", []):
                lines.append(f"\tdeleted:    {_text(path)}")
            lines.append("")
        if status.unstaged:
            lines.append("Changes not staged for commit:")
            for path in status.unstaged:
                lines.append(f"\tmodified:   {_text(path)}")
            lines.append("")
        if status.untracked:
            lines.append("Untracked files:")
            for path in status.untracked:
                lines.append(f"\t{_text(path)}")
            lines.append("")
        if not any_staged and not status.unstaged and not status.untracked:
            lines.append("nothing to commit, working tree clean")
        return ("\n".join(lines) + "\n", "", 0)

    if command == "log":
        repo = _open_repo(porcelain, base)
        oneline = "--oneline" in args
        limit = None
        for flag in args:
            if flag.startswith("-n"):
                tail = flag[2:]
                if tail.isdigit():
                    limit = int(tail)
        try:
            walker = list(repo.get_walker())
        except Exception:
            return ("", "fatal: your current branch does not have any commits yet\n", 128)
        if limit:
            walker = walker[:limit]
        chunks = []
        for entry in walker:
            commit = entry.commit
            if oneline:
                chunks.append(
                    f"{commit.id.decode()[:7]} {commit.message.decode().strip()}"
                )
            else:
                import datetime

                when = datetime.datetime.utcfromtimestamp(commit.commit_time)
                chunks.append(
                    f"commit {commit.id.decode()}\n"
                    f"Author: {commit.author.decode()}\n"
                    f"Date:   {when:%a %b %d %H:%M:%S %Y} +0000\n\n"
                    f"    {commit.message.decode().strip()}\n"
                )
        return ("\n".join(chunks) + "\n", "", 0)

    if command == "diff":
        buffer = io.BytesIO()
        try:
            porcelain.diff_tree(base, outstream=buffer)
        except Exception:
            pass
        return (buffer.getvalue().decode("utf-8", "replace"), "", 0)

    if command == "branch":
        repo = _open_repo(porcelain, base)
        names = [r.decode().rsplit("/", 1)[-1] for r in repo.refs.keys(b"refs/heads")]
        if args and not args[0].startswith("-"):
            porcelain.branch_create(base, args[0])
            return ("", "", 0)
        current = _current_branch(porcelain, base)
        return (
            "".join(
                f"* {n}\n" if n == current else f"  {n}\n" for n in sorted(names)
            ),
            "",
            0,
        )

    if command == "checkout":
        if not args:
            return ("", "usage: git checkout <branch>\n", 1)
        if args[0] == "-b" and len(args) > 1:
            porcelain.branch_create(base, args[1])
            target = args[1]
        else:
            target = args[0]
        try:
            porcelain.checkout(base, target)
        except Exception as error:
            return ("", f"error: {error}\n", 1)
        return (f"Switched to branch '{target}'\n", "", 0)

    if command == "show":
        repo = _open_repo(porcelain, base)
        try:
            head = repo[repo.head()]
            return (
                f"commit {head.id.decode()}\nAuthor: {head.author.decode()}\n\n"
                f"    {head.message.decode().strip()}\n",
                "",
                0,
            )
        except Exception as error:
            return ("", f"error: {error}\n", 1)

    if command == "rm":
        if not args:
            return ("", "usage: git rm <file>\n", 1)
        porcelain.remove(base, [os.path.join(work, a) for a in args])
        return ("".join(f"rm '{a}'\n" for a in args), "", 0)

    return ("", f"git: '{command}' is not supported by this sandbox\n", 1)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _current_branch(porcelain: Any, path: str) -> str:
    try:
        repo = _open_repo(porcelain, path)
        return repo.refs.follow(b"HEAD")[0][-1].decode().rsplit("/", 1)[-1]
    except Exception:
        return "master"


# --------------------------------------------------------------------------
# curl / wget
# --------------------------------------------------------------------------


class NetworkBlocked(Exception):
    """Raised when a URL is not on the allowlist."""


def _check_allowed(url: str, allow: Sequence[str], allow_all: bool) -> None:
    if allow_all:
        return
    for pattern in allow:
        if url.startswith(pattern):
            return
    raise NetworkBlocked(
        f"{url} is not on this sandbox's network allowlist. "
        f"Allowed: {', '.join(allow) if allow else '(nothing)'}"
    )


def make_http_builtins(
    *,
    allow: Optional[Sequence[str]] = None,
    allow_all: bool = False,
    timeout: float = 30.0,
    root: str = "/workspace",
    mirror: str = "/tmp/omniagents-git/workspace",
) -> Dict[str, Callable]:
    """Build ``curl`` and ``wget`` builtins over httpx.

    httpx is what the platform already uses to reach the model from inside
    Pyodide, so it is known to work there; bashkit's own HTTP client is a
    Rust stack that cannot target wasm at all.

    Args:
        allow: URL prefixes permitted. Anything else is refused *loudly*.
        allow_all: skip the allowlist. Reasonable for a trusted lesson,
            never for untrusted input.
        timeout: per-request timeout in seconds.
    """
    allowed = list(allow or ())

    def _request(argv: List[str]) -> tuple:
        import httpx

        method = "GET"
        url = None
        headers: Dict[str, str] = {}
        data = None
        silent = False
        include_headers = False
        output: Optional[str] = None
        follow = False

        index = 0
        while index < len(argv):
            token = argv[index]
            if token in ("-X", "--request") and index + 1 < len(argv):
                method = argv[index + 1].upper()
                index += 2
            elif token in ("-H", "--header") and index + 1 < len(argv):
                name, _, value = argv[index + 1].partition(":")
                headers[name.strip()] = value.strip()
                index += 2
            elif token in ("-d", "--data") and index + 1 < len(argv):
                data = argv[index + 1]
                if method == "GET":
                    method = "POST"
                index += 2
            elif token in ("-o", "--output") and index + 1 < len(argv):
                output = argv[index + 1]
                index += 2
            elif token in ("-s", "--silent"):
                silent = True
                index += 1
            elif token in ("-i", "--include"):
                include_headers = True
                index += 1
            elif token in ("-L", "--location"):
                follow = True
                index += 1
            elif token.startswith("-"):
                index += 1  # ignore unknown flags rather than fail the lesson
            else:
                url = token
                index += 1

        if not url:
            return ("", "curl: no URL specified\n", 2)

        _check_allowed(url, allowed, allow_all)

        content = None
        if data is not None:
            content = data.encode()
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        with httpx.Client(follow_redirects=follow, timeout=timeout) as client:
            response = client.request(method, url, headers=headers, content=content)

        body = response.text
        if include_headers:
            status = f"HTTP/1.1 {response.status_code} {response.reason_phrase}\n"
            head = "".join(f"{k}: {v}\n" for k, v in response.headers.items())
            body = status + head + "\n" + body
        return (body, "", 0, output, silent)

    def curl(ctx: Any):
        from bashkit import BuiltinResult

        argv = [str(a) for a in ctx.argv]
        try:
            result = _request(argv)
        except NetworkBlocked as blocked:
            # Loud on purpose. bashkit's own default-deny exits 0 with empty
            # output, which is indistinguishable from an empty response.
            return BuiltinResult(stderr=f"curl: {blocked}\n", exit_code=7)
        except Exception as error:
            return BuiltinResult(
                stderr=f"curl: ({type(error).__name__}) {error}\n", exit_code=7
            )

        if len(result) == 3:
            body, err, code = result
            return BuiltinResult(stdout=body, stderr=err, exit_code=code)

        body, err, code, output, silent = result
        if output:
            # Written to the mirror, not through ctx.fs: VFS ops inside a
            # builtin panic on wasm. The session copies the mirror back into
            # the workspace once the command returns.
            work = _work_dir(ctx, root, mirror)
            target = (
                os.path.join(mirror, output.lstrip("/"))
                if output.startswith("/")
                else os.path.join(work, output)
            )
            os.makedirs(os.path.dirname(target) or work, exist_ok=True)
            with open(target, "wb") as handle:
                handle.write(body.encode())
            return BuiltinResult(stdout="", stderr=err, exit_code=code)
        return BuiltinResult(stdout=body, stderr=err, exit_code=code)

    def wget(ctx: Any):
        from bashkit import BuiltinResult

        argv = [str(a) for a in ctx.argv]
        urls = [a for a in argv if not a.startswith("-")]
        if not urls:
            return BuiltinResult(stderr="wget: missing URL\n", exit_code=1)
        name = urls[0].rstrip("/").rsplit("/", 1)[-1] or "index.html"
        forwarded = ["-o", name, urls[0]]
        return curl(_ArgvContext(ctx, forwarded))

    return {"curl": curl, "wget": wget}


class _ArgvContext:
    """Wrap a BuiltinContext, substituting argv (used by wget -> curl)."""

    def __init__(self, inner: Any, argv: Sequence[str]) -> None:
        self._inner = inner
        self.argv = list(argv)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
