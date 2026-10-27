"""Git over HTTP: the repositories, cloned, fetched and pushed with plain git.

``git http-backend`` (git's own smart HTTP server) serves every repository
at ``/github/<owner>/<name>(.git)``. A factory's sandbox points
``https://github.com/`` there with ``url.<base>.insteadOf``; the clones keep
their GitHub remotes. As on GitHub, a push needs a token (basic auth, the
password git's credential helper gives), as does cloning a private
repository; the push is the token's account's. A push to a protected branch
is refused. After a push, GitHub does what it does after one: pull requests
on the branch move, workflows run and webhooks go out (:meth:`GitHubState.pushed`).
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..wire import Wire
from .repos import NULL_SHA
from .state import GitHubError, GitHubState

__all__ = ["git_router"]


def _service(path: str, query: str) -> Tuple[str, str]:
    """``("clone or fetch" | "push", git service)`` for a smart HTTP request."""
    service = path.rsplit("/", 1)[-1]
    if service == "refs" and "service=" in query:
        service = query.split("service=", 1)[1].split("&", 1)[0]
    return ("push" if service == "git-receive-pack" else "clone or fetch", service)


def _caller(state: GitHubState, authorization: str) -> Optional[str]:
    if authorization.lower().startswith("basic "):
        try:
            _, _, token = base64.b64decode(authorization[6:]).decode().partition(":")
        except Exception:
            token = ""
        user = state.user_for_token(token)
        if user:
            return user.login
    if authorization.lower().startswith("bearer "):
        user = state.user_for_token(authorization[7:].strip())
        if user:
            return user.login
    return None


def _ask_for_credentials() -> Response:
    # What GitHub answers: git asks its credential helper and tries again.
    return Response(
        "Invalid username or token. Password authentication is not supported for Git operations.\n",
        status_code=401,
        media_type="text/plain",
        headers={"WWW-Authenticate": 'Basic realm="GitHub"'},
    )


def git_router(state: GitHubState, wire: Wire) -> APIRouter:
    router = APIRouter()

    @router.api_route("/github/{owner}/{name}/{path:path}", methods=["GET", "POST"])
    async def smart_http(owner: str, name: str, path: str, request: Request):
        try:
            repo = state.repo(owner, name[:-4] if name.endswith(".git") else name)
        except GitHubError:
            return Response(
                "Repository not found.\n", status_code=404, media_type="text/plain"
            )
        body = await request.body()
        query = request.url.query
        action, service = _service(path, query)
        started = time.monotonic()
        entry = None
        before: Dict[str, str] = {}
        actor = _caller(state, request.headers.get("authorization", ""))
        if actor is None and (action == "push" or repo.private):
            return _ask_for_credentials()
        # The ref advertisement and the pack exchange are one fetch or push:
        # the wire shows the exchange that moves objects.
        if request.method == "POST":
            entry = wire.record(
                service="github",
                direction="in",
                kind="git",
                title=f"git {action}",
                actor=repo.full_name,
                status="pending",
                request={
                    "repository": repo.full_name,
                    "service": service,
                    "bytes": len(body),
                    **({"pusher": actor} if action == "push" else {}),
                },
            )
            if action == "push":
                before = repo.bare.refs()
        env = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_PROJECT_ROOT": str(state.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": f"/{repo.owner}/{repo.name}.git/{path}",
            "REQUEST_METHOD": request.method,
            "QUERY_STRING": query,
            "CONTENT_TYPE": request.headers.get("content-type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_USER": actor or "anonymous",
            "REMOTE_ADDR": request.client.host if request.client else "",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "WORKPLACE_PROTECTED_BRANCHES": " ".join(repo.protected),
        }
        for header, value in request.headers.items():
            env["HTTP_" + header.upper().replace("-", "_")] = value
        process = await asyncio.create_subprocess_exec(
            "git",
            "http-backend",
            env=env,
            # A ref advertisement (GET) has no body, and git may exit before
            # reading one: writing it would hit a closed pipe.
            stdin=asyncio.subprocess.PIPE if body else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(body or None)
        head, sep, payload = stdout.partition(b"\r\n\r\n")
        if not sep:
            head, _, payload = stdout.partition(b"\n\n")
        status = 200
        headers: Dict[str, str] = {}
        for line in head.decode("latin-1").splitlines():
            key, _, value = line.partition(":")
            if key.lower() == "status":
                status = int(value.strip().split(" ", 1)[0])
            elif key:
                headers[key.strip()] = value.strip()
        ok = process.returncode == 0 and status < 400
        # A push git refuses (a protected branch) is still a 200: its report says so.
        rejected = (
            action == "push"
            and request.method == "POST"
            and (b"ng refs/" in payload or b"pre-receive hook declined" in payload)
        )
        if action == "push" and request.method == "POST" and ok:
            after = repo.bare.refs()
            updates = {
                ref: (before.get(ref, NULL_SHA), sha)
                for ref, sha in after.items()
                if before.get(ref) != sha
            }
            updates.update(
                {
                    ref: (sha, NULL_SHA)
                    for ref, sha in before.items()
                    if ref not in after
                }
            )
            if updates:
                state.pushed(repo, updates, actor)
        if entry is not None:
            wire.update(
                entry,
                status="ok" if ok and not rejected else "error",
                duration_ms=round((time.monotonic() - started) * 1000, 1),
                response={
                    "status": status,
                    "bytes": len(payload),
                    **(
                        {"stderr": stderr.decode(errors="replace").strip()}
                        if stderr.strip()
                        else {}
                    ),
                    **(
                        {"branches": sorted(repo.bare.branches())}
                        if action == "push" and ok
                        else {}
                    ),
                    **(
                        {"rejected": "a protected branch changes through pull requests"}
                        if rejected
                        else {}
                    ),
                },
            )
        media_type = headers.pop("Content-Type", None)
        return Response(
            payload, status_code=status, headers=headers, media_type=media_type
        )

    return router
