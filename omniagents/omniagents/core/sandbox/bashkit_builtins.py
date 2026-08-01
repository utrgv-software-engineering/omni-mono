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

**One filesystem.** The sandbox mounts the workspace directory into bashkit
(``FileSystem.real``), so the shell, Python and dulwich all operate on the
same bytes. These builtins therefore open files directly — there is nothing
to bridge, and no second copy to drift out of step.

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
    author: str = DEFAULT_AUTHOR,
    proxy_base: Optional[str] = None,
) -> Callable:
    """Build a ``git`` builtin backed by dulwich.

    dulwich works on ordinary paths, and so does the shell: the sandbox mounts
    the workspace directory into bashkit, so both see the same bytes. The
    callback therefore needs no bridge at all - it opens the very files the
    student is looking at.

    (It still avoids ``ctx.fs``: VFS handle operations inside a builtin panic
    on wasm, since they may run off-thread and Pyodide has no threads. Plain
    ``open()`` is both simpler and safe.)

    Args:
        root: the workspace directory, used to resolve ``ctx.cwd``.
        author: identity used until ``git config user.name``/``user.email``
            set one.
        proxy_base: same-origin git proxy (e.g. ``https://book/git``) that
            remote operations are routed through. Without it, remotes on
            other origins are unreachable from a browser - see
            :func:`resolve_remote`.
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

        os.makedirs(root, exist_ok=True)
        work = _work_dir(ctx, root)
        out, err, code = _run_git(porcelain, argv, work, root, author, proxy_base)
        return BuiltinResult(stdout=out, stderr=err, exit_code=code)

    return git


def _work_dir(ctx: Any, root: str) -> str:
    """Where the shell is standing. The same path Python sees."""
    cwd = str(getattr(ctx, "cwd", root) or root)
    return cwd if os.path.isdir(cwd) else root


def _run_git(
    porcelain: Any,
    argv: List[str],
    work: str,
    root: str,
    author: str,
    proxy_base: Optional[str] = None,
) -> tuple:
    """Dispatch one git subcommand. Returns ``(stdout, stderr, exit_code)``."""
    command, args = argv[0], argv[1:]

    def repo_root(start: str) -> Optional[str]:
        current = os.path.abspath(start)
        while True:
            if os.path.isdir(os.path.join(current, ".git")):
                return current
            parent = os.path.dirname(current)
            if parent == current or len(parent) < len(root):
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
        target = (
            os.path.join(work, args[0])
            if args and not args[0].startswith("-")
            else work
        )
        os.makedirs(target, exist_ok=True)
        if os.path.isdir(os.path.join(target, ".git")):
            return (f"Reinitialized existing Git repository in {target}/.git/\n", "", 0)
        porcelain.init(target)
        return (f"Initialized empty Git repository in {target}/.git/\n", "", 0)

    if command == "clone":
        positional = [a for a in args if not a.startswith("-")]
        if not positional:
            return ("", "usage: git clone <url> [<dir>]\n", 1)
        url = positional[0]
        name = (
            positional[1]
            if len(positional) > 1
            else (url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
        )
        target = os.path.join(work, name)
        if os.path.exists(target) and os.listdir(target):
            return ("", f"fatal: destination path '{name}' already exists\n", 128)
        os.makedirs(target, exist_ok=True)
        try:
            repo = _clone_into(porcelain, target, url, proxy_base)
        except Exception as error:
            shutil.rmtree(target, ignore_errors=True)
            return ("", f"fatal: {_remote_hint(error, url, proxy_base)}\n", 128)
        head = repo.refs.read_ref(b"HEAD") or b""
        return (f"Cloning into '{name}'...\n", "", 0)

    base = repo_root(work)
    if base is None:
        return (
            "",
            "fatal: not a git repository (or any of the parent directories): .git\n",
            128,
        )

    if command == "remote":
        repo = _open_repo(porcelain, base)
        config = repo.get_config()
        if len(args) >= 3 and args[0] == "add":
            config.set((b"remote", args[1].encode()), b"url", args[2].encode())
            config.write_to_path()
            return ("", "", 0)
        if args and args[0] == "remove":
            return ("", "", 0)
        names = []
        for section in config.sections():
            if len(section) == 2 and section[0] == b"remote":
                names.append(section[1].decode())
        if args and args[0] == "-v":
            lines = []
            for n in names:
                url = config.get((b"remote", n.encode()), b"url").decode()
                lines += [f"{n}\t{url} (fetch)", f"{n}\t{url} (push)"]
            return ("\n".join(lines) + "\n" if lines else "", "", 0)
        return ("".join(f"{n}\n" for n in names), "", 0)

    if command in ("fetch", "pull", "push"):
        repo = _open_repo(porcelain, base)
        config = repo.get_config()
        name = next((a for a in args if not a.startswith("-")), "origin")
        try:
            url = config.get((b"remote", name.encode()), b"url").decode()
        except KeyError:
            return (
                "",
                f"fatal: '{name}' does not appear to be a git repository\n",
                128,
            )
        try:
            return _remote_op(porcelain, command, repo, base, url, proxy_base)
        except Exception as error:
            return ("", f"fatal: {_remote_hint(error, url, proxy_base)}\n", 128)

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
                return (
                    config.get((section.encode(),), key.encode()).decode() + "\n",
                    "",
                    0,
                )
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
            return (
                "",
                "fatal: your current branch does not have any commits yet\n",
                128,
            )
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
            "".join(f"* {n}\n" if n == current else f"  {n}\n" for n in sorted(names)),
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
# remote git
# --------------------------------------------------------------------------


class _HttpxResponse:
    """The response shape dulwich expects, over an httpx response."""

    def __init__(self, response: Any) -> None:
        self.status = response.status_code
        self.content_type = response.headers.get("content-type")
        self.redirect_location = (
            str(response.url) if response.has_redirect_location else ""
        )

    def close(self) -> None:
        return None


def _httpx_client_class():
    """dulwich HTTP transport over httpx.

    dulwich talks to remotes through urllib3, which has no working backend in
    Pyodide; httpx does, and it is already how this platform reaches the
    model. Only ``_http_request`` has to be supplied.
    """
    import httpx
    from dulwich.client import AbstractHttpGitClient

    class HttpxGitClient(AbstractHttpGitClient):
        def __init__(self, base_url: str, **kwargs: Any) -> None:
            self._client = httpx.Client(follow_redirects=True, timeout=60.0)
            super().__init__(base_url=base_url, **kwargs)

        def _http_request(self, url, headers=None, data=None, raise_for_status=True):
            sent = dict(headers or {})
            if data is None:
                response = self._client.get(url, headers=sent)
            else:
                body = data if isinstance(data, bytes) else b"".join(data)
                response = self._client.post(url, headers=sent, content=body)
            if raise_for_status and response.status_code >= 400:
                from dulwich.errors import GitProtocolError

                raise GitProtocolError(f"HTTP {response.status_code} for {url}")
            return _HttpxResponse(response), io.BytesIO(response.content).read

    return HttpxGitClient


def resolve_remote(url: str, proxy_base: Optional[str]) -> str:
    """Rewrite an https remote to travel through the same-origin git proxy.

    A browser will not fetch ``https://github.com/...`` from a page on
    another origin - GitHub sends no CORS headers, so the request never
    leaves. Pointing the same URL at the app's own ``/git/<host>/<path>``
    makes it same-origin, and the server does the cross-origin part.
    """
    if not proxy_base or not url.startswith(("http://", "https://")):
        return url
    without_scheme = url.split("://", 1)[1]
    host, _, path = without_scheme.partition("/")
    if proxy_base.rstrip("/").endswith(f"/git/{host}"):
        return url  # already proxied
    return f"{proxy_base.rstrip('/')}/{host}/{path}"


def _client_for(url: str, proxy_base: Optional[str]):
    """Build a dulwich client for ``url``, routed through the proxy."""
    resolved = resolve_remote(url, proxy_base)
    client_class = _httpx_client_class()
    # `dumb` is only a hint: dulwich upgrades to smart HTTP when the server
    # advertises it, so a statically served repo and a real git host both work.
    return client_class(resolved, dumb=False), resolved


def _clone_into(porcelain: Any, target: str, url: str, proxy_base: Optional[str]):
    from dulwich.repo import Repo

    repo = Repo.init(target)
    repo.hooks.clear()
    client, resolved = _client_for(url, proxy_base)
    result = client.fetch(resolved, repo)
    for ref, sha in result.refs.items():
        if ref.startswith(b"refs/heads/") or ref.startswith(b"refs/tags/"):
            repo.refs[ref] = sha
    head = result.refs.get(b"HEAD")
    default = (
        head
        or result.refs.get(b"refs/heads/main")
        or result.refs.get(b"refs/heads/master")
    )
    if default:
        branch = b"main" if b"refs/heads/main" in result.refs else b"master"
        repo.refs[b"refs/heads/" + branch] = default
        repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/" + branch)
        _checkout_tree(repo, repo[default].tree)
    # One config object: get_config() hands back a fresh instance each call,
    # so setting on one and writing another silently drops the remote.
    config = repo.get_config()
    config.set((b"remote", b"origin"), b"url", url.encode())
    config.write_to_path()
    return repo


def _remote_op(
    porcelain: Any,
    command: str,
    repo: Any,
    base: str,
    url: str,
    proxy_base: Optional[str],
) -> tuple:
    client, resolved = _client_for(url, proxy_base)

    if command == "push":

        def update_refs(old_refs):
            new = dict(old_refs)
            for ref, sha in repo.refs.as_dict(b"refs/heads").items():
                new[b"refs/heads/" + ref] = sha
            return new

        client.send_pack(
            resolved, update_refs, generate_pack_data=repo.generate_pack_data
        )
        return (f"To {url}\n", "", 0)

    result = client.fetch(resolved, repo)
    for ref, sha in result.refs.items():
        if ref.startswith((b"refs/heads/", b"refs/tags/")):
            repo.refs[b"refs/remotes/origin/" + ref.split(b"/", 2)[-1]] = sha
    if command == "fetch":
        return (f"From {url}\n", "", 0)

    # pull: fast-forward the checked-out branch to what the remote has
    try:
        branch = repo.refs.follow(b"HEAD")[0][-1]
    except Exception:
        branch = b"refs/heads/master"
    remote_sha = result.refs.get(branch)
    if remote_sha is None:
        return (f"Already up to date.\n", "", 0)
    repo.refs[branch] = remote_sha
    _checkout_tree(repo, repo[remote_sha].tree)
    return (f"Updating {branch.decode()} from {url}\n", "", 0)


def _checkout_tree(repo: Any, tree_id: bytes) -> None:
    """Write a tree into the working copy.

    ``Repo.reset_index`` moved to the worktree object in dulwich 1.2; accept
    either so the builtin is not pinned to one point release.
    """
    worktree = getattr(repo, "get_worktree", None)
    if worktree is not None:
        worktree().reset_index(tree_id)
    else:
        repo.reset_index(tree_id)


def _remote_hint(error: Exception, url: str, proxy_base: Optional[str]) -> str:
    """Turn a transport failure into something a student can act on."""
    text = str(error) or type(error).__name__
    if "Failed to fetch" in text or "ConnectError" in type(error).__name__:
        if not proxy_base:
            return (
                f"cannot reach {url} from a browser: the remote sends no CORS "
                f"headers, so the request is blocked before it is sent. Route "
                f"it through a same-origin git proxy."
            )
        return f"cannot reach {url} through the proxy at {proxy_base}: {text}"
    return text


# --------------------------------------------------------------------------
# python
# --------------------------------------------------------------------------


def make_python_builtin(*, root: str = "/workspace") -> Callable:
    """A ``python3`` that is the interpreter the rest of the page uses.

    bashkit embeds Monty, a small Rust Python. It runs simple scripts, which
    is exactly what makes it dangerous here: `python3 main.py` would quietly
    execute the student's file under a *different* interpreter from the Run
    button - no httpx, no openai, no omniagents, different semantics - and
    usually appear to work.

    Since the shell and Python share one filesystem, the file the shell names
    is the file this process can open. So `python3` runs it here, in the real
    CPython, with the real packages.
    """

    def python(ctx: Any):
        import contextlib
        import io as _io
        import runpy
        import sys

        from bashkit import BuiltinResult

        argv = [str(a) for a in ctx.argv]
        cwd = str(getattr(ctx, "cwd", root) or root)

        code = None
        script = None
        args: List[str] = []
        index = 0
        while index < len(argv):
            token = argv[index]
            if token == "-c" and index + 1 < len(argv):
                code = argv[index + 1]
                args = argv[index + 2 :]
                break
            if token in ("--version", "-V"):
                return BuiltinResult(stdout=f"Python {sys.version.split()[0]}\n")
            if token.startswith("-"):
                index += 1
                continue
            script = token
            args = argv[index + 1 :]
            break

        if code is None and script is None:
            return BuiltinResult(
                stderr="python3: interactive mode is unavailable here; "
                "pass a script or -c\n",
                exit_code=2,
            )

        target = None
        if script is not None:
            target = script if script.startswith("/") else os.path.join(cwd, script)
            if not os.path.isfile(target):
                return BuiltinResult(
                    stderr=f"python3: can't open file '{script}': "
                    f"[Errno 2] No such file or directory\n",
                    exit_code=2,
                )

        out, err = _io.StringIO(), _io.StringIO()
        saved_argv, saved_cwd = sys.argv, os.getcwd()
        exit_code = 0
        try:
            sys.argv = [script or "-c", *args]
            os.chdir(cwd if os.path.isdir(cwd) else root)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                if target is not None:
                    runpy.run_path(target, run_name="__main__")
                else:
                    # ``target`` is None only when ``script`` is None, and the
                    # both-are-None case returned above — so ``-c code`` was
                    # given. Narrow it for the type checker.
                    assert code is not None
                    exec(compile(code, "<stdin>", "exec"), {"__name__": "__main__"})
        except SystemExit as stop:
            exit_code = int(stop.code or 0)
        except BaseException:
            import traceback

            traceback.print_exc(file=err)
            exit_code = 1
        finally:
            sys.argv = saved_argv
            os.chdir(saved_cwd)

        return BuiltinResult(
            stdout=out.getvalue(), stderr=err.getvalue(), exit_code=exit_code
        )

    return python


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
            # Straight to the workspace: it is one filesystem, so the file
            # exists the moment this returns. (Not via ctx.fs - VFS handle ops
            # inside a builtin panic on wasm.)
            work = _work_dir(ctx, root)
            target = output if output.startswith("/") else os.path.join(work, output)
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
