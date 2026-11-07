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
    assert change["before"]["purpose"] == ""
    assert change["after"]["purpose"] == "BI on the warehouse"
    labels = w.governance.decisions.all()[0]
    assert labels["kind"] == "labels" and labels["status"] == "applied"
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
    i.set_governance_owners(owner, [owner.id])
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
        "/workplace/api/installation/allowed-services",
        json={"services": SERVICES, "reason": "Our Azure tenant"},
    )
    w, _ = i.instances[record["id"]]
    client.put(
        prefix(record) + "/workplace/api/factory/profile",
        json={
            "classification": "published",
            "heightened_risk": False,
            "reason": "Public",
        },
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


def test_raising_is_immediate_and_lowering_waits_for_the_governance_owner(strict):
    i, client, record = strict
    base = prefix(record) + "/workplace/api/factory/profile"
    steward = identity(i, "steward")
    i.grant(record["id"], steward.id, "admin")
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(steward))
    # The most controlled labels need no one's approval.
    raised = client.put(
        base, json={"classification": "confidential", "heightened_risk": True}
    ).json()
    assert raised["profile"]["classification"] == "confidential"
    assert raised["pending"] is None
    # Classified, but no model is an allowed service yet.
    assert not raised["startable"] and "No model" in raised["blocked"]
    # Lowering waits.
    lowered = client.put(
        base,
        json={
            "classification": "controlled",
            "heightened_risk": False,
            "reason": "Internal data only",
        },
    ).json()
    assert lowered["pending"]["status"] == "pending"
    assert lowered["profile"]["classification"] == "confidential"
    w, _ = i.instances[record["id"]]
    assert w.governance.heightened_risk() is True
    decision = lowered["pending"]["id"]
    decide = f"/workplace/api/installation/governance/decisions/{decision}"
    assert (
        client.post(decide, json={"approve": True, "reasoning": "fine"}).status_code
        == 403
    )
    client.cookies.set(
        SESSION_COOKIE, i.accounts.start_session(i.accounts.by_username("owner"))
    )
    governance = client.get("/workplace/api/installation/governance").json()
    assert governance["owner"]
    assert [d["status"] for d in governance["factories"][0]["decisions"]] == [
        "applied",
        "pending",
    ]
    decided = client.post(
        decide, json={"approve": True, "reasoning": "Reviewed the data sources"}
    ).json()
    assert decided["status"] == "decided" and decided["decided_by"] == "owner"
    assert w.governance.classification() == "controlled"
    assert w.governance.heightened_risk() is False
    # The governance owner's own lowering applies at once, with reasoning.
    assert client.put(base, json={"classification": "published"}).status_code == 422
    done = client.put(
        base, json={"classification": "published", "reason": "Public data only"}
    ).json()
    assert done["pending"] is None and done["profile"]["classification"] == "published"


def test_others_allowed_services_changes_wait_for_the_governance_owner(strict):
    i, client, record = strict
    admin2 = identity(i, "admin2", True)
    client.cookies.set(SESSION_COOKIE, i.accounts.start_session(admin2))
    proposed = client.put(
        "/workplace/api/installation/allowed-services", json={"services": SERVICES}
    ).json()
    assert proposed["services"] == []
    assert proposed["pending"]["kind"] == "allowed_services"
    client.cookies.set(
        SESSION_COOKIE, i.accounts.start_session(i.accounts.by_username("owner"))
    )
    client.post(
        f"/workplace/api/installation/governance/decisions/{proposed['pending']['id']}",
        json={"approve": True, "reasoning": "Assessed"},
    )
    listed = client.get("/workplace/api/installation/allowed-services").json()
    assert len(listed["services"]) == 3


def test_models_are_the_governance_owners_to_change(strict):
    i, client, record = strict
    w, _ = i.instances[record["id"]]
    steward = identity(i, "steward")
    i.grant(record["id"], steward.id, "admin")
    with pytest.raises(HTTPException) as refused:
        w.governance.require_model_owner(
            w.accounts.by_username("steward"), "Made x the default model"
        )
    assert refused.value.status_code == 403
    w.governance.require_model_owner(
        w.accounts.by_username("owner"), "Made x the default model"
    )
    assert w.governance.decisions.all()[-1]["summary"] == "Made x the default model"


def test_material_changes_in_the_repository():
    from omniagents_workplace.governance import material_changes

    class Bare:
        files = {
            ("base", "project.yml"): b"security: {}\nagents: {a: 1}\n",
            (
                "head",
                "project.yml",
            ): b"security: {mcp_require_allowlist: true}\nagents: {a: 2}\n",
            ("base", "agents/x/agent.yml"): b"model: a\ninstructions_file: i.md\n",
            ("head", "agents/x/agent.yml"): b"model: a\ninstructions_file: j.md\n",
        }

        def diff(self, base, head):
            return (
                "--- a/project.yml\n+++ b/project.yml\n"
                "--- a/agents/x/agent.yml\n+++ b/agents/x/agent.yml\n+++ b/models.yml\n"
            )

        def blob(self, rev, path):
            found = self.files.get((rev, path))
            return ("100644", found) if found else None

    assert material_changes(Bare(), "base", "head") == [
        "models.yml",
        "project.yml: security",
    ]


def test_a_material_pull_request_waits_for_the_governance_owner(workplace_server):
    from omniagents_workplace.governance import GOVERNANCE_CHECK
    from omniagents_workplace.repository_settings import KEY

    workplace, base_url = workplace_server
    workplace.policy = Policy({"WORKPLACE_POLICY": "strict"})
    state = workplace.github
    repo = state.repos["acme/api-service"]
    settings = dict(workplace.repository_settings.settings())
    workplace.store.set_meta(
        KEY, {**settings, "factory_repository": "acme/api-service"}
    )
    state.create_branch(repo, "acme-factory", "factory/model", "main")
    state.write_files(
        repo, "acme-factory", "factory/model", [("models.yml", b"x: 1\n")], "m"
    )
    pr = state.create_pull(
        repo, "acme-factory", "Switch model", "factory/model", "main", ""
    )
    state.create_review(repo, pr, "priya", event="APPROVE")
    with pytest.raises(GitHubError, match=GOVERNANCE_CHECK):
        state.merge_pull(repo, pr, "acme-factory")
    [pending] = workplace.governance.decisions.pending()
    assert pending["kind"] == "pull_request" and "models.yml" in pending["summary"]
    workplace.governance.decisions.decide(
        pending["id"], "priya", True, "Assessed the model"
    )
    assert state.merge_pull(repo, pr, "acme-factory")


def test_cards_reviews_reports_and_evidence(strict):
    import time

    i, client, record = strict
    client.put(
        prefix(record) + "/workplace/api/factory/profile",
        json={
            "classification": "confidential",
            "heightened_risk": False,
            "purpose": "Software, analysis and BI",
            "sponsor": "Data and AI team",
            "data": "Clinical data warehouse extracts",
            "reason": "Prepares work; people decide",
        },
    )
    inventory = client.get("/workplace/api/installation/inventory").json()
    card = inventory["cards"][0]
    assert card["classification"] == "confidential"
    assert card["sponsor"] == "Data and AI team"
    assert card["labels_decision"]["reasoning"] == "Prepares work; people decide"
    assert "**Sponsor:** Data and AI team" in inventory["markdown"]
    calendar = client.get("/workplace/api/installation/reviews").json()["calendar"]
    assert {c["kind"] for c in calendar} >= {
        "restore_drill",
        "risk_review",
        "access_review",
    }
    assert all(c["overdue"] for c in calendar)  # nothing done yet
    assert not any(c["kind"] == "heightened_risk_reassessment" for c in calendar)
    done = client.post(
        "/workplace/api/installation/reviews",
        json={"kind": "risk_review", "factory": record["id"], "result": "No change"},
    )
    assert done.status_code == 200
    calendar = client.get("/workplace/api/installation/reviews").json()["calendar"]
    assert not next(c for c in calendar if c["kind"] == "risk_review")["overdue"]
    now = time.gmtime()
    quarter = f"{now.tm_year}Q{(now.tm_mon - 1) // 3 + 1}"
    report = client.get(
        f"/workplace/api/installation/quarterly-report?quarter={quarter}"
    ).text
    assert "Prepares work; people decide" in report
    assert "risk_review by owner" in report
    evidence = client.get("/workplace/api/installation/evidence").json()
    assert quarter in evidence["quarterly_reports"]
    assert evidence["inventory"][0]["factory"] == "One"
    assert any(d["kind"] == "labels" for d in evidence["decisions"])
    assert evidence["scheduled_reviews"]["completed"][0]["kind"] == "risk_review"
    assert "password" not in json.dumps(evidence).lower()


def test_a_strict_factory_without_allowed_models_says_so_and_takes_no_work(strict):
    i, client, record = strict
    base = prefix(record) + "/workplace/api/factory/profile"
    client.put(
        base,
        json={
            "classification": "published",
            "heightened_risk": False,
            "reason": "Public",
        },
    )
    view = client.get(base).json()
    assert "No model is an allowed service" in view["blocked"]
    started = client.post(f"/workplace/api/factories/{record['id']}/start")
    assert started.status_code == 422 and "No model" in started.json()["detail"]
    sent = client.post(
        prefix(record) + "/workplace/api/personal/chats/main/messages",
        json={"text": "hi", "submission_id": "00000000-0000-0000-0000-000000000002"},
    )
    assert sent.status_code == 409 and "No model" in sent.json()["detail"]
    client.put(
        "/workplace/api/installation/allowed-services",
        json={"services": SERVICES, "reason": "Our Azure tenant"},
    )
    assert client.get(base).json()["blocked"] is None


def test_standalone_owner_management_is_discoverable(tmp_path):
    from omniagents_workplace.server import create_app

    workplace = _workplace(tmp_path, "")
    account = workplace.accounts.by_username("bill")
    with TestClient(create_app(workplace, static_dir=tmp_path)) as client:
        client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(account))
        view = client.get("/workplace/api/governance").json()
        assert view["can_manage_owners"] is True
        assert view["managed_by_installation"] is False
        assert view["owners"] == []
        assert any(person["id"] == account.id for person in view["people"])
        assert (
            client.put(
                "/workplace/api/governance/owners", json={"account_ids": [account.id]}
            ).status_code
            == 200
        )
        view = client.get("/workplace/api/governance").json()
        assert view["owners"] == [account.id]
        assert view["owner"] is True
