import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from omniagents_workplace.factory_settings import FactorySettings, KEY, settings_router
from omniagents_workplace.store import Store
from test_installation import installation


@pytest.fixture
def setup(tmp_path):
    slack = SimpleNamespace(name="Factory", domain="unchanged", tz="UTC")

    def set_team(name, domain, tz):
        slack.name, slack.domain, slack.tz = name, domain, tz

    slack.set_team = set_team
    workplace = SimpleNamespace(
        store=Store(tmp_path / "workplace.db"),
        slack=slack,
        audit_log=SimpleNamespace(record=Mock()),
        rename_factory=Mock(),
    )
    settings = FactorySettings(workplace, lambda: ("http://factory", "control"))
    remote = {
        "revision": 0,
        "display_name": "Factory",
        "sources": [{"source": "teams", "identity": "immutable", "marker": "@factory"}],
    }

    def runtime(method, body=None):
        if method == "PUT":
            remote.clear()
            remote.update(copy.deepcopy(body))
        return copy.deepcopy(remote)

    settings.runtime = Mock(side_effect=runtime)
    yield settings, workplace, remote
    workplace.store.close()


def edit(settings):
    value = settings.get()
    return {
        "revision": value["revision"],
        "display_name": "Research",
        "sources": [{**value["sources"][0], "marker": "@research"}],
    }


def test_save_persists_audits_renames_and_applies(setup):
    settings, workplace, remote = setup
    result = settings.save(edit(settings), "admin")
    assert result["status"] == "applied"
    assert result["revision"] == 1
    assert remote["sources"][0]["marker"] == "@research"
    assert workplace.slack.name == "Research"
    assert workplace.slack.domain == "unchanged"
    workplace.rename_factory.assert_called_with("Research")
    assert workplace.audit_log.record.call_args.kwargs["actor"] == "admin"
    reopened = FactorySettings(workplace, lambda: (None, None))
    assert reopened.get() == result


def test_offline_save_is_pending_then_reconciles_without_ui(setup):
    settings, workplace, remote = setup
    value = edit(settings)
    runtime = settings.runtime
    settings.runtime = Mock(side_effect=HTTPException(503, "Offline"))
    result = settings.save(value, "admin")
    assert result["status"] == "pending"
    assert result["error"] == "Offline"
    assert workplace.store.meta(KEY)["desired"]["sources"][0]["marker"] == "@research"
    settings.runtime = runtime
    assert settings.reconcile()["status"] == "applied"
    assert remote["revision"] == 1


def test_runtime_state_loss_is_reconciled_from_workplace(setup):
    settings, _, remote = setup
    settings.save(edit(settings), "admin")
    remote["revision"] = 0
    remote["sources"][0]["marker"] = "@factory"
    assert settings.reconcile()["status"] == "applied"
    assert remote["sources"][0]["marker"] == "@research"


def test_concurrent_edits_are_rejected(setup):
    settings, workplace, _ = setup
    stale = edit(settings)
    settings.save(stale, "admin")
    with pytest.raises(HTTPException) as error:
        settings.save(stale, "admin")
    assert error.value.status_code == 409
    assert workplace.audit_log.record.call_count == 1


@pytest.mark.parametrize(
    "change",
    [{"sources": []}, {"display_name": "  "}, {"revision": True}, {"extra": True}],
)
def test_invalid_save_leaves_state_unchanged(setup, change):
    settings, workplace, _ = setup
    value = edit(settings)
    before = settings.get()
    with pytest.raises(HTTPException):
        settings.save({**value, **change}, "admin")
    assert settings.get() == before
    workplace.audit_log.record.assert_not_called()


def test_admin_required_for_read_and_write(setup):
    settings, _, _ = setup

    def admin(request):
        if request.headers.get("x-test-admin") != "yes":
            raise HTTPException(403)
        return SimpleNamespace(username="admin")

    app = FastAPI()
    app.include_router(settings_router(settings, admin))
    with TestClient(app) as client:
        assert client.get("/workplace/api/factory/settings").status_code == 403
        assert client.put("/workplace/api/factory/settings", json={}).status_code == 403
        headers = {"x-test-admin": "yes"}
        assert (
            client.get("/workplace/api/factory/settings", headers=headers).status_code
            == 200
        )
        assert (
            client.put(
                "/workplace/api/factory/settings", headers=headers, json=edit(settings)
            ).json()["status"]
            == "applied"
        )


def test_real_installation_rename_is_scoped_and_uses_admin_control_token(
    installation, monkeypatch
):
    from omniagents_workplace.accounts import SESSION_COOKIE

    instance, client, owner, member, first, second = installation
    workplace = instance.instances[first["id"]][0]
    config = workplace.factory_config.load()["factory"]
    remote = {"revision": 0, "display_name": "One", "sources": []}
    endpoints = []

    def runtime(settings, method, body=None):
        endpoints.append(settings.endpoint())
        if method == "PUT":
            remote.clear()
            remote.update(copy.deepcopy(body))
        return copy.deepcopy(remote)

    monkeypatch.setattr(FactorySettings, "runtime", runtime)
    path = f"/factories/{first['id']}/workplace/api/factory/settings"
    assert client.get(path).status_code == 200
    response = client.put(
        path, json={"revision": 0, "display_name": "Research", "sources": []}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert instance.records[first["id"]]["name"] == "Research"
    assert instance.records[second["id"]]["name"] == "Two"
    assert workplace.slack.name == "Research"
    assert all(token == config["admin_auth_token"] for _, token in endpoints)
    assert config["admin_auth_token"] != config["auth_token"]
    response = client.patch(
        f"/factories/{first['id']}/workplace/api/team", json={"name": "Renamed"}
    )
    assert response.status_code == 200
    assert remote["display_name"] == "Renamed"
    assert client.get(path).json()["display_name"] == "Renamed"
    assert instance.records[first["id"]]["name"] == "Renamed"
    assert instance.records[second["id"]]["name"] == "Two"
    client.cookies.set(SESSION_COOKIE, instance.accounts.start_session(member))
    assert client.get(path).status_code == 403
    assert client.put(path, json={}).status_code == 403
