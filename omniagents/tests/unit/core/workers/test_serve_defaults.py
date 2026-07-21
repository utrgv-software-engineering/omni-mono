"""Serve-state-derived worker defaults (`omniagents.core.workers.serve_defaults`)."""

from types import SimpleNamespace

import pytest

from omniagents.core.runtime import serve_state
from omniagents.core.workers import (
    MultiSourceWorktreeIsolation,
    WorkerSource,
    default_worker_model_resolver,
    default_worker_project_context,
    get_isolation_strategy,
    install_project_worker_defaults,
    register_isolation_strategy,
    serve_worker_sources,
    set_worker_defaults,
    worker_defaults,
    WorktreeIsolation,
)
from omniagents.core.workers import serve_defaults as serve_defaults_mod


@pytest.fixture(autouse=True)
def _restore_globals():
    """Restore serve state, worker defaults, the isolation registry entry,
    and the module's install/caching globals after each test."""
    original_defaults = worker_defaults()
    original_worktree = get_isolation_strategy("worktree")
    original_installed = serve_defaults_mod._installed
    original_fallback = serve_defaults_mod._fallback_project_context
    serve_state.reset()
    yield
    serve_state.reset()
    set_worker_defaults(original_defaults)
    register_isolation_strategy(original_worktree)
    serve_defaults_mod._installed = original_installed
    serve_defaults_mod._fallback_project_context = original_fallback


# -- serve_worker_sources ----------------------------------------------


def test_sources_mapping_kinds_and_paths():
    serve_state.set_manifest_root("/workspace")
    serve_state.set_sources(
        [
            # local-git with a host path → git worktree at that path
            {"kind": "local-git", "mountName": "api", "path": "/host/api"},
            # git-remote without a path → git worktree under the manifest root
            {
                "kind": "git-remote",
                "mountName": "web",
                "repoUrl": "https://example.com/web.git",
            },
            # plain local → copy
            {"kind": "local", "mountName": "notes", "path": "/host/notes"},
        ]
    )
    assert serve_worker_sources() == [
        WorkerSource(name="api", host_path="/host/api", git=True),
        WorkerSource(name="web", host_path="/workspace/web", git=True),
        WorkerSource(name="notes", host_path="/host/notes", git=False),
    ]


def test_sources_mapping_skips_unusable_entries():
    serve_state.set_manifest_root("/workspace")
    serve_state.set_sources(
        [
            # unknown kind → skipped
            {"kind": "s3", "mountName": "bucket", "path": "/host/bucket"},
            # no mount name → skipped
            {"kind": "local-git", "path": "/host/api"},
            # empty/junk path with a manifest root → mounted under it
            {"kind": "local", "mountName": "scratch", "path": "   "},
        ]
    )
    assert serve_worker_sources() == [
        WorkerSource(name="scratch", host_path="/workspace/scratch", git=False),
    ]


def test_sources_mapping_skips_pathless_without_manifest_root():
    serve_state.set_sources(
        [
            {"kind": "git-remote", "mountName": "web", "repoUrl": "https://x/y.git"},
            {"kind": "local-git", "mountName": "api", "path": "/host/api"},
        ]
    )
    assert serve_worker_sources() == [
        WorkerSource(name="api", host_path="/host/api", git=True),
    ]


def test_sources_mapping_empty_when_nothing_published():
    assert serve_worker_sources() == []


# -- default_worker_project_context ------------------------------------


def test_project_context_prefers_published_context(monkeypatch):
    published = object()
    serve_state.set_project_context(published)
    assert default_worker_project_context() is published


def test_project_context_falls_back_to_product_manifest_cached(monkeypatch):
    from omniagents.core.project import runtime as project_runtime

    loaded = object()
    calls = []

    monkeypatch.setattr(
        "omniagents.core.product.product_info",
        lambda: SimpleNamespace(manifest_path="/product/project.yml"),
    )

    def fake_load_project(path):
        calls.append(path)
        return loaded

    monkeypatch.setattr(project_runtime, "load_project", fake_load_project)

    assert default_worker_project_context() is loaded
    assert default_worker_project_context() is loaded  # cached
    assert calls == ["/product/project.yml"]


def test_project_context_raises_without_manifest(monkeypatch):
    monkeypatch.setattr(
        "omniagents.core.product.product_info",
        lambda: SimpleNamespace(manifest_path=None),
    )
    with pytest.raises(RuntimeError, match="manifest_path"):
        default_worker_project_context()


# -- default_worker_model_resolver -------------------------------------


def test_model_resolver_uses_installed_store(monkeypatch):
    class _FakeStore:
        def resolve_model_for_runtime(self, model=None):
            return {"model": model or "default-model", "provider": "openai"}

    monkeypatch.setattr("omniagents.core.models.model_store", lambda: _FakeStore())
    assert default_worker_model_resolver("fast")["model"] == "fast"
    assert default_worker_model_resolver()["model"] == "default-model"


def test_model_resolver_none_without_store(monkeypatch):
    monkeypatch.setattr("omniagents.core.models.model_store", lambda: None)
    assert default_worker_model_resolver("fast") is None


# -- install_project_worker_defaults -----------------------------------


def test_install_registers_worktree_strategy_and_spec_loader(monkeypatch):
    serve_defaults_mod._installed = False
    monkeypatch.setattr(
        "omniagents.core.product.product_info",
        lambda: SimpleNamespace(slug="my_product", manifest_path=None),
    )

    install_project_worker_defaults()

    strategy = get_isolation_strategy("worktree")
    assert isinstance(strategy, MultiSourceWorktreeIsolation)
    assert strategy._sources_provider is serve_worker_sources

    defaults = worker_defaults()
    assert defaults.spec_loader is not None
    assert defaults.default_isolation == "none"


def test_install_spec_loader_derives_all_inputs(monkeypatch):
    """The installed loader builds from the published project context,
    stamps the product slug, forwards the published orchestrator sandbox
    attachment, and resolves the model over the installed store."""
    serve_defaults_mod._installed = False
    monkeypatch.setattr(
        "omniagents.core.product.product_info",
        lambda: SimpleNamespace(slug="my_product", manifest_path=None),
    )

    class _FakeStore:
        def resolve_model_for_runtime(self, model=None):
            return {"model": model or "store-default", "provider": "openai"}

    monkeypatch.setattr("omniagents.core.models.model_store", lambda: _FakeStore())

    project = object()
    serve_state.set_project_context(project)
    serve_state.set_orchestrator_spec(
        SimpleNamespace(
            sandbox_use_sandbox_agent=True,
            sandbox_run_as="agent",
            sandbox_run_config={"image": "devbox"},
        )
    )

    from omniagents.core.project import runtime as project_runtime

    built = []

    def fake_build(proj, agent_key=None):
        built.append((proj, agent_key))
        return SimpleNamespace(
            project_slug="from-project",
            model_name=None,
            model_settings=None,
            sandbox_use_sandbox_agent=False,
            sandbox_run_as=None,
            sandbox_run_config=None,
        )

    monkeypatch.setattr(project_runtime, "build_spec_from_project", fake_build)

    install_project_worker_defaults()
    spec, runtime_cfg = worker_defaults().spec_loader("fast")

    assert built == [(project, "worker")]
    assert spec.project_slug == "my_product"
    assert spec.model_name == "fast"
    assert runtime_cfg == {"model": "fast", "provider": "openai"}
    assert spec.sandbox_use_sandbox_agent is True
    assert spec.sandbox_run_as == "agent"
    assert spec.sandbox_run_config == {"image": "devbox"}


def test_install_honors_product_model_resolver_hook(monkeypatch):
    serve_defaults_mod._installed = False
    monkeypatch.setattr(
        "omniagents.core.product.product_info",
        lambda: SimpleNamespace(slug="my_product", manifest_path=None),
    )
    seen = []

    install_project_worker_defaults(
        model_resolver=lambda m: seen.append(m) or {"model": "custom"}
    )

    from omniagents.core.project import runtime as project_runtime

    monkeypatch.setattr(
        project_runtime,
        "build_spec_from_project",
        lambda proj, agent_key=None: SimpleNamespace(
            project_slug=None,
            model_name=None,
            model_settings=None,
            sandbox_use_sandbox_agent=False,
            sandbox_run_as=None,
            sandbox_run_config=None,
        ),
    )
    serve_state.set_project_context(object())

    spec, runtime_cfg = worker_defaults().spec_loader("requested")
    assert seen == ["requested"]
    assert runtime_cfg == {"model": "custom"}
    assert spec.model_name == "custom"


def test_install_is_idempotent(monkeypatch):
    serve_defaults_mod._installed = False
    monkeypatch.setattr(
        "omniagents.core.product.product_info",
        lambda: SimpleNamespace(slug="my_product", manifest_path=None),
    )
    install_project_worker_defaults()
    first_loader = worker_defaults().spec_loader
    first_strategy = get_isolation_strategy("worktree")

    install_project_worker_defaults()
    assert worker_defaults().spec_loader is first_loader
    assert get_isolation_strategy("worktree") is first_strategy


def test_worktree_default_degrades_to_single_repo_without_sources(monkeypatch):
    """With no published sources the registered strategy behaves as the
    plain single-repo worktree isolation (drop-in replacement)."""
    serve_defaults_mod._installed = False
    monkeypatch.setattr(
        "omniagents.core.product.product_info",
        lambda: SimpleNamespace(slug="my_product", manifest_path=None),
    )
    install_project_worker_defaults()
    strategy = get_isolation_strategy("worktree")
    assert isinstance(strategy, WorktreeIsolation)
    assert strategy.sources() == []
