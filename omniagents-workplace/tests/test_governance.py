"""Classification, allowed services, forced review and the factory repository's rules."""

import json

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.governance import PERSONAL_DATA_CHECK, personal_data_findings
from omniagents_workplace.github.state import GitHubError
from omniagents_workplace.installation import Installation
from omniagents_workplace.installation_server import create_installation_app
from omniagents_workplace.policy import Policy
from test_factory_runtime import _workplace
from test_installation import identity, installation, prefix, project  # noqa: F401

AZURE = "https://utrgv-ha.openai.azure.com/openai/v1"
SERVICES = [
    {"kind": "model", "name": "Azure OpenAI", "provider": "azure", "endpoint": AZURE},
    {"kind": "integration", "name": "Slack stand-in", "provider": "slack"},
    {"kind": "mcp", "name": "memory", "endpoint": "http://workplace/memory/mcp"},
]


def test_a_factory_is_classified_by_its_admins_and_every_change_audited(installation):
    i, client, owner, other, a, b = installation
    base = prefix(a) + "/workplace/api/factory/profile"
    assert client.get(base).json()["profile"] == {}
    assert client.put(base, json={"classification": "secret"}).status_code == 422
    saved = client.put(
        base,
        json={
            "classification": "confidential",
            "purpose": "BI on the warehouse",
            "owner": "Eric",
        },
    ).json()
    assert saved["profile"]["classification"] == "confidential"
    w, _ = i.instances[a["id"]]
    change = w.audit_log.events(event="factory.profile_changed")[0]
    assert (
        change["before"] == {} and change["after"]["purpose"] == "BI on the warehouse"
    )
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert client.get(base).status_code == 200
    assert client.put(base, json={"classification": "published"}).status_code == 403


def test_installation_keeps_every_factorys_allowed_services(installation):
    i, client, owner, other, a, b = installation
    assert (
        client.put(
            "/workplace/api/installation/allowed-services",
            json={
                "services": [
                    {
                        "kind": "model",
                        "name": "x",
                        "provider": "azure",
                        "endpoint": "ftp://x",
                    }
                ]
            },
        ).status_code
        == 422
    )
    saved = client.put(
        "/workplace/api/installation/allowed-services", json={"services": SERVICES}
    )
    assert saved.status_code == 200
    view = client.get(prefix(a) + "/workplace/api/allowed-services").json()
    assert view["managed_by_installation"] and len(view["services"]) == 3
    # A factory cannot change them itself.
    assert (
        client.put(
            prefix(a) + "/workplace/api/allowed-services", json={"services": []}
        ).status_code
        == 409
    )
    assert (
        i.audit_log.events(event="allowed_services.changed")[0]["after"][0]["endpoint"]
        == AZURE
    )
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(other))
    assert (
        client.put(
            "/workplace/api/installation/allowed-services", json={"services": []}
        ).status_code
        == 403
    )


@pytest.fixture
def strict(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKPLACE_POLICY", "strict")
    i = Installation(tmp_path / "installation", base_url="http://testserver")
    owner = identity(i, "owner", True)
    record = i.add(name="One", project=project(tmp_path, "one"), owner=owner)
    with TestClient(create_installation_app(i, static_dir=tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, i.accounts.start_session(owner))
        yield i, client, record


def test_strict_factories_start_only_once_classified(strict):
    i, client, record = strict
    refused = client.post(f"/workplace/api/factories/{record['id']}/start")
    assert refused.status_code == 422 and "Classify" in refused.json()["detail"]
    view = client.get(prefix(record) + "/workplace/api/factory/profile").json()
    assert view["strict"] and not view["startable"]


def test_the_runtime_is_given_the_allowed_services(strict, tmp_path):
    i, client, record = strict
    client.put(
        "/workplace/api/installation/allowed-services", json={"services": SERVICES}
    )
    w, _ = i.instances[record["id"]]
    client.put(
        prefix(record) + "/workplace/api/factory/profile",
        json={"classification": "published"},
    )
    env = w.governance.runtime_environment(tmp_path)
    assert json.loads(env["OMNIAGENTS_ALLOWED_MODEL_ENDPOINTS"]) == [
        {"provider": "azure", "base_url": AZURE}
    ]
    assert "OMNIAGENTS_ALLOWED_INTEGRATIONS" not in env
    overlay = yaml.safe_load(open(env["OMNIAGENTS_PROJECT_OVERLAYS"]))
    assert "mcp_require_allowlist" not in overlay["security"]
    client.put(
        prefix(record) + "/workplace/api/factory/profile",
        json={"classification": "controlled"},
    )
    env = w.governance.runtime_environment(tmp_path)
    assert json.loads(env["OMNIAGENTS_ALLOWED_INTEGRATIONS"]) == [
        {"type": "slack", "url": None}
    ]
    overlay = yaml.safe_load(open(env["OMNIAGENTS_PROJECT_OVERLAYS"]))
    assert overlay["security"]["mcp_require_allowlist"] is True
    assert overlay["security"]["mcp_allowed_servers"] == [
        "memory=http://workplace/memory/mcp"
    ]
    assert overlay["security"]["audit"]["max_detail_chars"] == 0
    # The model page refuses a provider that is not allowed.
    w.governance.require_model("azure", AZURE)
    with pytest.raises(HTTPException) as refused:
        w.governance.require_model("openai", None)
    assert refused.value.status_code == 422


def test_review_is_forced_for_restricted_factories_under_the_strict_policy(tmp_path):
    w = _workplace(tmp_path, "http://runtime.invalid")
    from omniagents_workplace.server import create_app

    client = TestClient(create_app(w, static_dir=tmp_path))
    from omniagents_workplace.accounts import SESSION_COOKIE as cookie

    client.cookies.set(cookie, w.accounts.start_session(w.accounts.by_username("bill")))
    settings = "/workplace/api/factory/definition/settings"
    assert client.put(settings, json={"require_review": False}).json() == {
        "require_review": False
    }
    w.policy = Policy({"WORKPLACE_POLICY": "strict"})
    w.store.set_meta("factory.profile", {"classification": "confidential"})
    assert client.get(settings).json() == {"require_review": True, "forced": True}
    refused = client.put(settings, json={"require_review": False})
    assert refused.status_code == 409
    w.store.set_meta("factory.profile", {"classification": "published"})
    assert client.get(settings).json()["require_review"] is False
    w.close()


def test_personal_data_findings_look_at_added_lines_only():
    diff = "\n".join(
        [
            "--- a/skills/x.md",
            "+++ b/skills/x.md",
            "-Patient MRN 1234567 was removed",
            "+Look up the patient by MRN: 12345678",
            "+Ask jane@example.test for help",
            "+SSN 123-45-6789",
        ]
    )
    assert personal_data_findings(diff) == ["mrn", "ssn"]
    assert personal_data_findings("-SSN 123-45-6789\n+nothing here") == []


def test_factory_repository_needs_a_persons_approval_and_a_clean_personal_data_check(
    workplace_server,
):
    workplace, base_url = workplace_server
    state = workplace.github
    repo = state.repos["acme/api-service"]
    settings = dict(workplace.repository_settings.settings())
    workplace.store.set_meta(
        "repository.settings", {**settings, "factory_repository": "acme/api-service"}
    )
    from omniagents_workplace.repository_settings import KEY

    workplace.store.set_meta(
        KEY, {**settings, "factory_repository": "acme/api-service"}
    )
    state.add_user("another-agent", name="Another agent", type="Bot")
    state.create_branch(repo, "acme-factory", "factory/improve", "main")
    state.write_files(
        repo,
        "acme-factory",
        "factory/improve",
        [("skills/x.md", b"Look up MRN: 12345678\n")],
        "x",
    )
    pr = state.create_pull(
        repo, "acme-factory", "Improve", "factory/improve", "main", ""
    )
    state.create_review(repo, pr, "another-agent", event="APPROVE")
    with pytest.raises(GitHubError, match="approving review"):
        state.merge_pull(repo, pr, "acme-factory")
    state.create_review(repo, pr, "priya", event="APPROVE")
    # Published: approval by a person is enough.
    workplace.store.set_meta("factory.profile", {"classification": "confidential"})
    with pytest.raises(GitHubError, match=PERSONAL_DATA_CHECK):
        state.merge_pull(repo, pr, "acme-factory")
    assert workplace.audit_log.events(event="factory.personal_data_found")[0][
        "kinds"
    ] == ["mrn"]
    state.write_files(
        repo, "acme-factory", "factory/improve", [("skills/x.md", b"Look it up\n")], "y"
    )
    state.create_review(repo, pr, "priya", event="APPROVE")
    assert state.merge_pull(repo, pr, "acme-factory")
