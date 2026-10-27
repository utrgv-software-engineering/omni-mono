"""Construction smoke for the declared eval environment.

The Python environment classes (``environments/workspace_env.py``,
``environments/devbox_env.py``) are gone; ``evaluations/evaluation.yml``
declares the same environment and the framework builds it. This test
loads the real suite config through the framework loader, instantiates
the declared environment, and pins every resolved knob to the literal
values the deleted classes produced — so a drift in either the
declaration or the framework's declarative builder fails loudly.
"""

from pathlib import Path

from omniagents.core.eval.container_routing import ContainerRoutingMixin
from omniagents.core.eval.environment import EnvironmentContext
from omniagents.core.eval.environment_config import (
    ConfiguredWorkspaceEnvironment,
    build_environment,
)
from omniagents.core.eval.loader import load_eval_config
from omniagents.core.eval.local_workspace import LocalWorkspaceEnvironment
from omniagents.core.sandbox.profiles import resolve_product_profile

REPO_ROOT = Path(__file__).resolve().parent.parent


def _build_declared_environment():
    cfg = load_eval_config(str(REPO_ROOT / "omni_code" / "project.yml"))
    env_cfg = cfg.config.get("environment")
    assert env_cfg, "evaluation.yml must declare evaluation.environment"
    env = build_environment(
        env_cfg, project_root=cfg.project_root, eval_dir=cfg.eval_dir
    )
    return cfg, env


def test_declared_environment_composition():
    _, env = _build_declared_environment()
    # Same composition the deleted DevboxEvalEnvironment had: container
    # routing mixed over the workspace environment.
    assert isinstance(env, ConfiguredWorkspaceEnvironment)
    assert isinstance(env, ContainerRoutingMixin)
    assert isinstance(env, LocalWorkspaceEnvironment)


def test_declared_workspace_knobs_match_deleted_workspace_env():
    _, env = _build_declared_environment()
    # WorkspaceEvalEnvironment.__init__ constants, verbatim.
    assert env.fixtures_dir == REPO_ROOT / "evaluations" / "fixtures" / "workspaces"
    assert env.workspace_cache_dir == (
        Path.home() / ".cache" / "omni-code" / "eval-workspaces"
    )
    assert env.skills_subdir == ".omni_code/skills"
    assert env.keep_tempdir_env == "OMNI_EVAL_KEEP_TEMPDIR"
    assert env.tempdir_prefix == "omni_eval_"


def test_declared_env_vars_match_deleted_workspace_env():
    _, env = _build_declared_environment()
    ctx = EnvironmentContext(
        session_id="smoke", data={"workspace_root": "/tmp/omni_eval_smoke"}
    )
    assert env.get_env_vars(ctx) == {
        "PWD": "/tmp/omni_eval_smoke",
        "OMNI_WORKSPACE_ROOT": "/tmp/omni_eval_smoke",
        "SERPAPI_API_KEY": "",
    }


def test_declared_container_routing_matches_deleted_devbox_env():
    _, env = _build_declared_environment()
    # DevboxEvalEnvironment class attributes, verbatim.
    assert env.profile_resolver is resolve_product_profile
    assert env.default_profile_path == (
        REPO_ROOT / "evaluations" / "profiles" / "devbox.yml"
    )
    assert env.default_profile_path.is_file()
    # Container mode stays strictly per-scenario (no class-level fallbacks).
    assert env.default_profile is None
    assert env.default_image is None


def test_declares_no_mcp_fixture():
    _, env = _build_declared_environment()
    assert env.mcp_fixture is None
