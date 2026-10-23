"""Agent runs on separate event loops in one process each get working
connections (an evaluation server, an optimizer's batches)."""

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import omniagents  # noqa: F401 - installs the patch
from agents.models import openai_provider


class _Ok(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def test_each_event_loop_gets_working_connections():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"

    async def fetch():
        client = openai_provider.shared_http_client()
        assert client is openai_provider.shared_http_client()  # shared within a loop
        return (await client.get(url)).status_code

    try:
        # The second run used to reuse the first loop's closed connection.
        assert [asyncio.run(fetch()) for _ in range(3)] == [200, 200, 200]
    finally:
        server.shutdown()
