import re
import subprocess
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

import omniagents.builtin.tools.bash_tool as bash_tool


def unwrap(tool):
    impl = tool.on_invoke_tool.__closure__[0].cell_contents
    async_wrapper = impl.__closure__[-1].cell_contents
    closure = getattr(async_wrapper, "__closure__", None)
    if closure:
        return closure[0].cell_contents
    return async_wrapper


def make_ctx(tmp_path):
    return types.SimpleNamespace(context={"workspace_root": str(tmp_path)})


def test_execute_bash_success(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        assert command == "echo ok"
        assert shell is True
        assert capture_output is True
        assert text is True
        assert timeout == 30
        assert cwd == str(tmp_path)
        return types.SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "echo ok")

    assert "Command executed successfully" in result.llm_output
    assert result.ui_metadata["metadata"]["stdout"] == "ok\n"
    assert result.ui_metadata["metadata"]["success"] is True


def test_execute_bash_failure(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        return types.SimpleNamespace(stdout="out", stderr="err", returncode=2)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "bad command")

    assert result.llm_output.startswith("Command failed with exit code 2")
    assert result.ui_metadata["metadata"]["success"] is False
    assert result.ui_metadata["metadata"]["has_stderr"] is True


def test_execute_bash_timeout(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "sleep 5", timeout=1)

    assert "Command timed out" in result.llm_output
    assert result.ui_metadata["metadata"]["error_type"] == "timeout"


def test_execute_bash_unexpected_error(monkeypatch, tmp_path):
    ctx = make_ctx(tmp_path)

    def fake_run(command, shell, capture_output, text, timeout, cwd):
        raise RuntimeError("boom")

    monkeypatch.setattr(bash_tool.subprocess, "run", fake_run)

    result = unwrap(bash_tool.execute_bash)(ctx, "explode")

    assert result.llm_output == "Error executing command: boom"
    assert result.ui_metadata["metadata"]["error_type"] == "execution_error"
