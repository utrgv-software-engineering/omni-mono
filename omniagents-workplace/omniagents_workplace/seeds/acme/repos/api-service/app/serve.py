"""Run the app: python -m app.serve --port 8000."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

from app import api, web
from app.store import seeded

STORE = seeded()


class Handler(BaseHTTPRequestHandler):
    """Routes requests to app.api and app.web."""

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = dict(parse_qsl(url.query))
        if url.path == "/":
            page = web.render_task_list(STORE.all())
            self._send(200, page.encode(), "text/html; charset=utf-8")
        elif url.path == "/tasks":
            self._json(*api.list_tasks(STORE, query))
        elif url.path.startswith("/tasks/"):
            self._json(*api.get_task(STORE, url.path.split("/", 2)[2]))
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/tasks":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        self._json(*api.create_task(STORE, self.rfile.read(length)))


def main() -> None:
    """Start the server."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    port = parser.parse_args().port
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
