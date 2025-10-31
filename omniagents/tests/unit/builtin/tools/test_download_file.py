import importlib
import io
import os
import re
import sys
import types

if "pygit2" not in sys.modules:
    fake_pygit2 = types.ModuleType("pygit2")

    class _StubRepo:
        def __init__(self, gitdir):
            self.workdir = gitdir

        def path_is_ignored(self, path):
            return False

        def status_file(self, rel):
            return 0

    fake_pygit2.discover_repository = lambda start_dir=None: None
    fake_pygit2.Repository = _StubRepo
    fake_pygit2.GIT_STATUS_IGNORED = 0x1000
    sys.modules["pygit2"] = fake_pygit2

if "bs4" not in sys.modules:
    fake_bs4 = types.ModuleType("bs4")

    def _extract_tag(html, tag):
        match = re.search(f"<{tag}[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    class _FakeTag:
        def __init__(self, text):
            self.string = text

    class _FakeSoup:
        def __init__(self, html, parser=None):
            self._html = html
            title = _extract_tag(html, "title")
            self.title = _FakeTag(title) if title else None

        def __call__(self, names):
            return []

        def find_all(self, name, **kwargs):
            return []

        def get_text(self, separator="\n", strip=False):
            text = re.sub(r"<[^>]+>", " ", self._html)
            if strip:
                text = text.strip()
            words = text.split()
            if separator != " ":
                return separator.join(words)
            return " ".join(words)

    fake_bs4.BeautifulSoup = _FakeSoup
    sys.modules["bs4"] = fake_bs4

import requests

download_module = importlib.import_module("omniagents.builtin.tools.download_file")


def unwrap(tool):
    impl = tool.on_invoke_tool.__closure__[0].cell_contents
    async_wrapper = impl.__closure__[-1].cell_contents
    closure = getattr(async_wrapper, "__closure__", None)
    if closure:
        return closure[0].cell_contents
    return async_wrapper


class DummyResponse:
    def __init__(self, status_code=200, headers=None, content=b"", text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content
        self.text = text

    @property
    def content(self):
        return self._content

    def iter_content(self, chunk_size=8192):
        stream = io.BytesIO(self._content)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            yield chunk


def test_download_file_success_with_output_path(monkeypatch, tmp_path):
    target = tmp_path / "out.bin"
    response = DummyResponse(
        status_code=200,
        headers={"Content-Type": "application/octet-stream"},
        content=b"payload",
    )

    monkeypatch.setattr(
        download_module.requests,
        "get",
        lambda url, headers, stream, timeout: response,
    )

    result = unwrap(download_module.download_file)(
        "https://example.com/file.bin", str(target)
    )

    assert target.exists()
    assert target.read_bytes() == b"payload"
    assert result.llm_output.startswith("Successfully downloaded file")
    assert result.ui_metadata["metadata"]["file_path"] == str(target)


def test_download_file_success_header_filename(monkeypatch, tmp_path):
    response = DummyResponse(
        status_code=200,
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": "attachment; filename=doc.pdf",
        },
        content=b"pdfdata",
    )

    monkeypatch.setattr(
        download_module.requests,
        "get",
        lambda url, headers, stream, timeout: response,
    )

    result = unwrap(download_module.download_file)("https://example.com/doc")

    meta = result.ui_metadata["metadata"]
    path = meta["file_path"]
    assert path.endswith("doc.pdf")
    assert os.path.exists(path)
    assert meta["is_temporary"] is True


def test_download_file_http_error(monkeypatch):
    response = DummyResponse(status_code=404, text="nope")

    monkeypatch.setattr(
        download_module.requests,
        "get",
        lambda url, headers, stream, timeout: response,
    )

    result = unwrap(download_module.download_file)("https://example.com/missing")

    assert result.llm_output == "Request failed with status code 404"
    assert result.ui_metadata["metadata"]["success"] is False


def test_download_file_network_error(monkeypatch):
    def fake_get(url, headers, stream, timeout):
        raise requests.RequestException("network down")

    monkeypatch.setattr(download_module.requests, "get", fake_get)

    result = unwrap(download_module.download_file)("https://example.com/network")

    assert result.llm_output == "Network error occurred: network down"
    assert result.ui_metadata["metadata"]["error_type"] == "network_error"
