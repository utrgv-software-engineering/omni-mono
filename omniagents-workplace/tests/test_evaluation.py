from pathlib import Path

import pytest

from omniagents_workplace.evaluation import build

# The factory template, checked out beside omni-mono.
FACTORY = Path(__file__).resolve().parents[3] / "omni-factory"
EVALUATIONS = FACTORY / "evaluations"


@pytest.fixture
def environment():
    if not EVALUATIONS.is_dir():
        pytest.skip("the omni-factory checkout is not beside omni-mono")
    return build(
        {
            "seed": "acme",
            "fixtures_dir": "fixtures/workspaces",
            "github_dir": "fixtures/github",
        },
        project_root=FACTORY,
        eval_dir=EVALUATIONS,
    )


def _repo(seed):
    [repo] = seed["github"]["repositories"]
    return repo


def test_a_scenarios_repository_is_its_fixture_with_its_issues(environment):
    seed = environment._seed({"github": "issues.yml", "fixture": "api_service"})
    repo = _repo(seed)
    assert repo["name"] == "acme/api-service"
    assert repo["from"] == str(EVALUATIONS / "fixtures/workspaces/api_service")
    assert [i["number"] for i in repo["issues"]] == [12, 14, 15]
    assert "factory:Acme" in {label["name"] for label in repo["labels"]}
    # Nothing listens for the factory's webhooks in an evaluation.
    assert [set(app) & {"github", "linear"} for app in seed["apps"]] == [set()]


def test_a_pull_requests_head_is_the_fixture_over_its_base_fixture(environment):
    repo = _repo(
        environment._seed(
            {"github": "pr_pagination.yml", "fixture": "api_service_pr_pagination"}
        )
    )
    assert repo["from"] == str(EVALUATIONS / "fixtures/workspaces/api_service")
    [pr] = repo["pull_requests"]
    assert (pr["number"], pr["head"], pr["from"]) == (
        33,
        "dana/pagination",
        str(EVALUATIONS / "fixtures/workspaces/api_service_pr_pagination"),
    )
    typo = _repo(
        environment._seed({"github": "pr_readme_typo.yml", "fixture": "api_service"})
    )
    [typo_pr] = typo["pull_requests"]
    assert typo_pr["from"] == typo_pr["base_from"] and typo_pr[
        "base_unapply"
    ].startswith("--- a/README.md")


def test_the_seeded_pull_requests_diff_is_the_fixtures(environment, tmp_path):
    from omniagents_workplace.server import Workplace

    seed = environment._seed(
        {"github": "pr_pagination.yml", "fixture": "api_service_pr_pagination"}
    )
    workplace = Workplace(
        tmp_path / "pagination",
        base_url="http://127.0.0.1:7399",
        seed=seed,
        seed_dir=tmp_path,
    )
    try:
        repo = workplace.github.repos["acme/api-service"]
        pr = repo.issues[33]
        files = {
            f.filename
            for f in repo.bare.changes(
                workplace.github.base_sha(repo, pr), workplace.github.head_sha(repo, pr)
            )
        }
        assert files == {"app/api.py", "tests/test_api.py"}
        typo = Workplace(
            tmp_path / "typo",
            base_url="http://127.0.0.1:7399",
            seed=environment._seed(
                {"github": "pr_readme_typo.yml", "fixture": "api_service"}
            ),
            seed_dir=tmp_path,
        )
        r = typo.github.repos["acme/api-service"]
        assert "newest first" in r.bare.blob("sam/readme-typo", "README.md")[1].decode()
        assert "newst first" in r.bare.blob("main", "README.md")[1].decode()
        typo.close()
    finally:
        workplace.close()


@pytest.mark.asyncio
async def test_scenario_exports_its_own_memory_and_canvas_credentials(
    environment, tmp_path, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from omniagents_workplace.server import Workplace

    workplace = Workplace(
        tmp_path / "memory-eval", base_url="http://127.0.0.1:7391", run_workflows=False
    )
    app = workplace.apps.register(None, name="Eval", username="eval-app")
    expected = workplace.apps.env(app)
    monkeypatch.setattr(environment, "_seed", lambda _: {})
    monkeypatch.setattr(
        environment,
        "_serve",
        lambda _: SimpleNamespace(workplace=workplace, stop=workplace.close),
    )

    class Environments:
        def __init__(self, *args):
            pass

        @asynccontextmanager
        async def sandbox(self, *args):
            yield SimpleNamespace(root="/workspace/eval")

    monkeypatch.setattr(
        "omniagents.integrations.environment.RunEnvironments", Environments
    )
    context = await environment.setup(
        {"memory_fixture": {"role": "implementer", "text": "Lyra uses staging."}}
    )
    try:
        fixture = context.data["memory_fixture"]
        assert fixture["text"] == "Lyra uses staging."
        assert fixture["store"] == expected["MEMORY_AGENT_STORE"] + "/implementer"
        assert fixture["id"]
        exported = environment.get_env_vars(context)
        for key in (
            "MEMORY_MCP_URL",
            "MEMORY_MCP_TOKEN",
            "MEMORY_AGENT_STORE",
            "CANVAS_MCP_URL",
            "CANVAS_MCP_TOKEN",
        ):
            assert exported[key] == expected[key]
        assert exported["CANVAS_MCP_TOKEN"]
        assert (
            workplace.canvas.member_for_token(exported["CANVAS_MCP_TOKEN"]).id == app.id
        )
    finally:
        await environment.teardown(context)


@pytest.mark.parametrize("status", ["open", "merged", "closed"])
def test_memory_promotion_pr_status(environment, tmp_path, status):
    from omniagents_workplace.server import Workplace

    seed = environment._seed(
        {"github": f"memory_pr_{status}.yml", "fixture": "api_service"}
    )
    workplace = Workplace(
        tmp_path / status,
        base_url="http://127.0.0.1:7399",
        seed=seed,
        seed_dir=tmp_path,
    )
    try:
        pr = workplace.github.repos["acme/api-service"].issues[34]
        assert pr.state == ("open" if status == "open" else "closed")
        assert bool(pr.pull.merged) == (status == "merged")
    finally:
        workplace.close()


def test_improver_fixture_is_a_factory_repository(environment, tmp_path):
    from omniagents_workplace.server import Workplace

    seed = environment._seed(
        {"github": "memory_factory.yml", "fixture": "factory_memory"}
    )
    workplace = Workplace(
        tmp_path / "factory",
        base_url="http://127.0.0.1:7399",
        seed=seed,
        seed_dir=tmp_path,
    )
    try:
        repo = workplace.github.repos["acme/factory"]
        assert not repo.issues
        assert (
            b"Implementation"
            in repo.bare.blob("main", "agents/implementer/instructions.md")[1]
        )
        assert (
            b"scenarios:"
            in repo.bare.blob("main", "evaluations/scenarios/implementer.yml")[1]
        )
    finally:
        workplace.close()
