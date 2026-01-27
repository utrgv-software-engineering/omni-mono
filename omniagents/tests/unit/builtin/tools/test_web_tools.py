import importlib
import json
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

import pytest
import requests

web_fetch_module = importlib.import_module("omniagents.builtin.tools.web_fetch")
scholar_module = importlib.import_module("omniagents.builtin.tools.scholar_search")
web_search_module = importlib.import_module("omniagents.builtin.tools.web_search")
youtube_module = importlib.import_module("omniagents.builtin.tools.youtube_search")


def unwrap(tool):
    impl = tool.on_invoke_tool.__closure__[0].cell_contents
    async_wrapper = impl.__closure__[-1].cell_contents
    closure = getattr(async_wrapper, "__closure__", None)
    if closure:
        return closure[0].cell_contents
    return async_wrapper


class DummyResponse:
    def __init__(self, status_code=200, data=None, text="", headers=None):
        self.status_code = status_code
        self._data = data or {}
        payload = text or json.dumps(self._data)
        self.text = payload
        self.headers = headers or {}
        self._content = payload.encode("utf-8")

    def json(self):
        return self._data

    @property
    def content(self):
        return self._content


def test_web_fetch_html(monkeypatch):
    html = "<html><head><title>Title</title></head><body><p>Text</p></body></html>"
    response = DummyResponse(
        status_code=200,
        text=html,
        headers={"Content-Type": "text/html"},
    )

    monkeypatch.setattr(
        web_fetch_module.requests,
        "get",
        lambda url, headers, timeout: response,
    )

    result = unwrap(web_fetch_module.web_fetch)(object(), "https://example.com")

    assert "Text" in result.llm_output
    assert "Elapsed:" in result.llm_output
    assert result.ui_metadata["metadata"]["extracted_text"] is True
    assert result.ui_metadata["metadata"]["link_count"] == 0
    assert "elapsed_ms" in result.ui_metadata["metadata"]


def test_web_fetch_raw(monkeypatch):
    response = DummyResponse(
        status_code=200, text="body", headers={"Content-Type": "text/plain"}
    )

    monkeypatch.setattr(
        web_fetch_module.requests,
        "get",
        lambda url, headers, timeout: response,
    )

    result = unwrap(web_fetch_module.web_fetch)(
        object(), "https://example.com", extract_text=False
    )

    assert "body" in result.llm_output
    assert "Elapsed:" in result.llm_output
    assert result.ui_metadata["metadata"]["extracted_text"] is False
    assert "elapsed_ms" in result.ui_metadata["metadata"]


def test_web_fetch_http_error(monkeypatch):
    response = DummyResponse(status_code=500, text="fail")

    monkeypatch.setattr(
        web_fetch_module.requests,
        "get",
        lambda url, headers, timeout: response,
    )

    result = unwrap(web_fetch_module.web_fetch)(object(), "https://example.com/error")

    assert "Request failed with status code 500" in result.llm_output
    assert "elapsed:" in result.llm_output.lower()
    assert result.ui_metadata["display_type"] == "error"
    assert "elapsed_ms" in result.ui_metadata["metadata"]


def test_web_fetch_network_error(monkeypatch):
    def fake_get(url, headers, timeout):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(web_fetch_module.requests, "get", fake_get)

    result = unwrap(web_fetch_module.web_fetch)(object(), "https://example.com/down")

    assert "Network error occurred: connection refused" in result.llm_output
    assert result.ui_metadata["metadata"]["error_type"] == "network_error"
    assert "elapsed_ms" in result.ui_metadata["metadata"]


def test_web_fetch_timeout_error(monkeypatch):
    def fake_get(url, headers, timeout):
        raise web_fetch_module.requests.Timeout("timed out")

    monkeypatch.setattr(web_fetch_module.requests, "get", fake_get)

    result = unwrap(web_fetch_module.web_fetch)(object(), "https://slow.example.com")

    assert "Request timed out" in result.llm_output
    assert result.ui_metadata["metadata"]["error_type"] == "timeout"
    assert "elapsed_ms" in result.ui_metadata["metadata"]


def test_web_fetch_custom_timeout(monkeypatch):
    response = DummyResponse(
        status_code=200, text="ok", headers={"Content-Type": "text/plain"}
    )

    captured_timeout = None

    def fake_get(url, headers, timeout):
        nonlocal captured_timeout
        captured_timeout = timeout
        return response

    monkeypatch.setattr(web_fetch_module.requests, "get", fake_get)

    unwrap(web_fetch_module.web_fetch)(
        object(), "https://example.com", extract_text=False, timeout=30
    )

    assert captured_timeout == 30


def test_web_fetch_links_truncation_notice(monkeypatch):
    """Test that a notice is shown when links exceed the limit."""
    # Create HTML with many links
    links_html = "".join(
        [f'<a href="https://link{i}.com">Link {i}</a>' for i in range(150)]
    )
    html = f"<html><head><title>Links</title></head><body>{links_html}</body></html>"

    # We need a more complete fake soup for this test
    class FakeSoupWithLinks:
        def __init__(self, html, parser=None):
            self._html = html
            self.title = type("obj", (object,), {"string": "Links"})()

        def __call__(self, names):
            return []

        def find_all(self, name, **kwargs):
            if name == "a" and kwargs.get("href"):
                # Return 150 fake links
                class FakeLink:
                    def __init__(self, i):
                        self._href = f"https://link{i}.com"

                    def __getitem__(self, key):
                        if key == "href":
                            return self._href
                        return None

                    def get_text(self, strip=False):
                        return f"Link"

                return [FakeLink(i) for i in range(150)]
            return []

        def get_text(self, separator="\n", strip=False):
            return "Content with many links"

    original_soup = web_fetch_module.BeautifulSoup
    monkeypatch.setattr(web_fetch_module, "BeautifulSoup", FakeSoupWithLinks)

    response = DummyResponse(
        status_code=200,
        text=html,
        headers={"Content-Type": "text/html"},
    )

    monkeypatch.setattr(
        web_fetch_module.requests,
        "get",
        lambda url, headers, timeout: response,
    )

    result = unwrap(web_fetch_module.web_fetch)(object(), "https://example.com")

    # Should show truncation notice in LLM output
    assert "Showing 100 of 150 links" in result.llm_output
    assert result.ui_metadata["metadata"]["link_count"] == 100
    assert result.ui_metadata["metadata"]["total_links"] == 150
    assert result.ui_metadata["metadata"]["links_truncated"] is True


@pytest.fixture
def serp_env(monkeypatch):
    monkeypatch.setitem(os.environ, "SERPAPI_API_KEY", "key")


def test_scholar_search_success(monkeypatch, serp_env):
    payload = {
        "organic_results": [
            {
                "title": "Paper",
                "snippet": "Abstract",
                "publication_info": {},
                "inline_links": {"cited_by": {"total": 3, "link": "link"}},
            }
        ],
        "search_metadata": {"id": "id", "status": "success", "total_time_taken": 1},
    }
    response = DummyResponse(status_code=200, data=payload)

    def fake_get(url, params):
        assert params["engine"] == "google_scholar"
        assert params["num"] == 10
        return response

    monkeypatch.setattr(scholar_module.requests, "get", fake_get)

    result = unwrap(scholar_module.scholar_search)(object(), "query")

    assert "Paper" in result.llm_output
    assert result.ui_metadata["metadata"]["result_count"] == 1


def test_scholar_search_missing_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    result = unwrap(scholar_module.scholar_search)(object(), "query")

    assert result.llm_output == "SERPAPI_API_KEY not found in environment variables"
    assert result.ui_metadata["metadata"]["error_type"] == "configuration_error"


def test_scholar_search_http_error(monkeypatch, serp_env):
    response = DummyResponse(status_code=403, text="denied")

    monkeypatch.setattr(
        scholar_module.requests,
        "get",
        lambda url, params: response,
    )

    result = unwrap(scholar_module.scholar_search)(object(), "query")

    assert result.llm_output == "SerpAPI request failed with status code 403"
    assert result.ui_metadata["metadata"]["status_code"] == 403


def test_web_search_success(monkeypatch, serp_env):
    payload = {
        "organic_results": [
            {
                "title": "Result",
                "link": "https://example.com",
                "snippet": "Snippet",
                "position": 1,
                "displayed_link": "example.com",
            }
        ],
        "search_metadata": {"id": "id", "status": "success", "total_time_taken": 1},
    }
    response = DummyResponse(status_code=200, data=payload)

    def fake_get(url, params):
        assert params["gl"] == "us"
        assert params["safe"] == "active"
        assert params["num"] == 5
        return response

    monkeypatch.setattr(web_search_module.requests, "get", fake_get)

    result = unwrap(web_search_module.web_search)(object(), "q", num_results=5)

    assert "Result" in result.llm_output
    assert result.ui_metadata["metadata"]["result_count"] == 1


def test_web_search_missing_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    result = unwrap(web_search_module.web_search)(object(), "q")

    assert result.llm_output == "SERPAPI_API_KEY not found in environment variables"
    assert result.ui_metadata["metadata"]["error_type"] == "configuration_error"


def test_web_search_http_error(monkeypatch, serp_env):
    response = DummyResponse(status_code=502, text="bad gateway")

    monkeypatch.setattr(
        web_search_module.requests,
        "get",
        lambda url, params: response,
    )

    result = unwrap(web_search_module.web_search)(object(), "q")

    assert result.llm_output == "SerpAPI request failed with status code 502"
    assert result.ui_metadata["metadata"]["status_code"] == 502


def test_youtube_search_success(monkeypatch, serp_env):
    payload = {
        "video_results": [
            {
                "title": "Video",
                "link": "https://youtube.com/watch?v=abc",
                "channel": {"name": "Channel", "link": "https://channel"},
                "views": "10",
                "duration_text": "1:00",
            }
        ],
        "search_metadata": {"id": "id", "status": "success", "total_time_taken": 1},
    }
    response = DummyResponse(status_code=200, data=payload)

    def fake_get(url, params):
        assert params["engine"] == "youtube"
        assert params["num"] == 3
        return response

    monkeypatch.setattr(youtube_module.requests, "get", fake_get)

    result = unwrap(youtube_module.youtube_search)(object(), "cats", num_results=3)

    assert "Video" in result.llm_output
    assert result.ui_metadata["metadata"]["result_count"] == 1


def test_youtube_search_missing_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    result = unwrap(youtube_module.youtube_search)(object(), "cats")

    assert result.llm_output == "SERPAPI_API_KEY not found in environment variables"
    assert result.ui_metadata["metadata"]["error_type"] == "configuration_error"


def test_youtube_search_http_error(monkeypatch, serp_env):
    response = DummyResponse(status_code=503, text="service unavailable")

    monkeypatch.setattr(
        youtube_module.requests,
        "get",
        lambda url, params: response,
    )

    result = unwrap(youtube_module.youtube_search)(object(), "cats")

    assert result.llm_output == "SerpAPI request failed with status code 503"
    assert result.ui_metadata["metadata"]["status_code"] == 503
