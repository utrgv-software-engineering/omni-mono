"""Session-bound device login through the official GitHub CLI."""

import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import HTTPException


class GitHubDeviceLogin:
    """Called under RepositorySettings.lock. No CLI credentials persist after login."""

    def __init__(self):
        self.pending = None

    def close(self):
        pending, self.pending = self.pending, None
        if pending:
            process = pending["process"]
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            pending["directory"].cleanup()

    def expire(self):
        if self.pending and time.monotonic() >= self.pending["expires"]:
            self.close()

    def start(self, owner):
        self.expire()
        if self.pending:
            if self.pending["owner"] != owner:
                raise HTTPException(
                    409,
                    "Another administrator is signing in to GitHub. Try again later.",
                )
            return self.view()
        executable = shutil.which("gh")
        if not executable:
            raise HTTPException(
                503, "GitHub CLI is not installed on this Workplace server."
            )
        directory = tempfile.TemporaryDirectory(
            prefix="workplace-github-",
            dir="/dev/shm" if Path("/dev/shm").is_dir() else None,
        )
        root = Path(directory.name)
        # Do not inherit tokens, debug settings, credential stores, or CLI config.
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(root),
            "XDG_CONFIG_HOME": str(root),
            "GH_CONFIG_DIR": str(root / "gh"),
            "GH_BROWSER": "/bin/true",
            "BROWSER": "/bin/true",
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "NO_COLOR": "1",
            "LANG": "C.UTF-8",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent",
        }
        try:
            with (root / "output").open("wb") as output:
                process = subprocess.Popen(
                    [
                        executable,
                        "auth",
                        "login",
                        "--hostname",
                        "github.com",
                        "--git-protocol",
                        "https",
                        "--web",
                        "--scopes",
                        "repo,read:org,workflow",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=root,
                )
        except OSError:
            directory.cleanup()
            raise HTTPException(503, "Unable to start GitHub login.") from None
        self.pending = {
            "id": secrets.token_urlsafe(24),
            "owner": owner,
            "directory": directory,
            "process": process,
            "env": env,
            "executable": executable,
            "expires": time.monotonic() + 900,
        }
        return self.view()

    def check(self, owner, attempt):
        self.expire()
        if not self.pending or not secrets.compare_digest(self.pending["id"], attempt):
            raise HTTPException(
                410, "GitHub sign-in expired or was cancelled. Start again."
            )
        if self.pending["owner"] != owner:
            raise HTTPException(403, "This GitHub sign-in belongs to another session.")
        return self.pending

    def view(self):
        pending = self.pending
        with (Path(pending["directory"].name) / "output").open(
            "r", errors="replace"
        ) as output:
            match = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", output.read(16384))
        return {
            "id": pending["id"],
            "status": "pending",
            "user_code": match[0] if match else None,
            "verification_uri": "https://github.com/login/device",
        }

    def token(self):
        pending = self.pending
        try:
            result = subprocess.run(
                [pending["executable"], "auth", "token", "--hostname", "github.com"],
                env=pending["env"],
                cwd=pending["directory"].name,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(
                502, "Unable to read the GitHub login credential. Start again."
            ) from None
        token = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"[A-Za-z0-9_]{1,512}", token):
            raise HTTPException(
                502, "GitHub login did not return a usable credential. Start again."
            )
        return token
