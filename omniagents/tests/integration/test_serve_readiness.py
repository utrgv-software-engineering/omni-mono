"""``serve`` announces readiness only once it is accepting connections.

The launcher dials the port in the readiness payload the moment it reads
it. These spawn real ``serve`` processes — one after another and many at
once — and dial each port immediately on reading the payload.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PROJECT = PACKAGE_ROOT / "tests/fixtures/projects/studio_playground/project.yml"

SERVE = (
    "import sys; from omniagents.product_serve import serve_main; "
    "sys.exit(serve_main(sys.argv[1:]))"
)


def _serve_and_dial(home: Path) -> int:
    home.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(PACKAGE_ROOT), os.environ.get("PYTHONPATH", "")]
        ),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            SERVE,
            "--output",
            "json",
            "--agent-project",
            str(PROJECT),
            "--exit-on-stdin-close",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(home),
        text=True,
    )
    try:
        line = proc.stdout.readline()
        assert line, proc.stderr.read()
        port = json.loads(line)["ports"]["ui"]
        # No retry: the payload promises the server is accepting now.
        with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
            conn.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            assert conn.recv(16).startswith(b"HTTP/1.1 ")
        return port
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_serve_accepts_as_soon_as_ready_sequentially(tmp_path):
    for i in range(4):
        _serve_and_dial(tmp_path / f"seq-{i}")


def test_serve_accepts_as_soon_as_ready_concurrently(tmp_path):
    count = 8
    with ThreadPoolExecutor(max_workers=count) as pool:
        ports = list(
            pool.map(lambda i: _serve_and_dial(tmp_path / f"conc-{i}"), range(count))
        )
    assert len(set(ports)) == count
