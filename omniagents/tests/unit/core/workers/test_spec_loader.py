from types import SimpleNamespace

from omniagents.core.workers import make_project_worker_spec_loader


class _FakeSpec(SimpleNamespace):
    pass


def _patch_build(monkeypatch, calls):
    from omniagents.core.project import runtime

    def fake_build(project, agent_key=None):
        calls.append((project, agent_key))
        return _FakeSpec(
            project_slug="from-project",
            model_name=None,
            model_settings=None,
            sandbox_use_sandbox_agent=False,
            sandbox_run_as=None,
            sandbox_run_config=None,
        )

    monkeypatch.setattr(runtime, "build_spec_from_project", fake_build)


def test_loader_builds_from_project_and_stamps_model(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    project = object()
    resolved = {"model": "gpt-worker", "model_settings": {"reasoning": "low"}}

    loader = make_project_worker_spec_loader(
        project_provider=lambda: project,
        agent_key="worker",
        project_slug="my_product",
        model_resolver=lambda m: resolved,
    )
    spec, runtime_cfg = loader("requested-model")

    assert calls == [(project, "worker")]
    assert runtime_cfg is resolved
    assert spec.project_slug == "my_product"
    assert spec.model_name == "gpt-worker"
    assert spec.model_settings == {"reasoning": "low"}
    assert spec.resolve_default_model_config() is resolved


def test_model_request_forwarded_to_resolver(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)
    seen = []

    loader = make_project_worker_spec_loader(
        project_provider=lambda: object(),
        model_resolver=lambda m: seen.append(m) or {"model": "x"},
    )
    loader("fast-model")
    loader()
    assert seen == ["fast-model", None]


def test_without_resolver_or_slug_spec_left_as_built(monkeypatch):
    calls = []
    _patch_build(monkeypatch, calls)

    loader = make_project_worker_spec_loader(project_provider=lambda: object())
    spec, runtime_cfg = loader(None)

    assert runtime_cfg is None
    assert spec.project_slug == "from-project"
    assert spec.model_name is None
    assert spec.model_settings is None
    assert spec.resolve_default_model_config() is None
