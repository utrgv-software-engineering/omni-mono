"""The factory's model, configured from the workplace as omni-desktop configures its own."""

import json
import stat

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from omniagents_workplace import factory_models
from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.config import FactoryConfig
from omniagents_workplace.factory_models import (
    FactoryModels,
    ModelsError,
    resolve_model_choices,
    validate_provider,
)
from omniagents_workplace.server import Workplace, create_app


def test_models_json_is_written_as_the_desktop_writes_it(tmp_path):
    models = FactoryModels(tmp_path / "acme_factory")
    ref = models.add_provider(
        "anthropic",
        api_key="sk-ant-secret-1234",
        model={
            "id": "claude-fable-5",
            "label": "Claude Fable 5",
            "maxInput": 200000,
            "maxOutput": 64000,
        },
    )
    assert ref == "anthropic/claude-fable-5"
    written = json.loads(models.path.read_text())
    assert written["version"] == 3 and written["default"] == ref
    provider = written["providers"]["anthropic"]
    assert provider["type"] == "litellm" and provider["routing_prefix"] == "anthropic"
    assert (
        provider["api_key"] == "sk-ant-secret-1234"
    )  # kept, so it lasts across restarts
    assert provider["models"]["claude-fable-5"]["model"] == "anthropic/claude-fable-5"
    assert stat.S_IMODE(models.path.stat().st_mode) == 0o600

    models.add_provider(
        "ollama",
        base_url="http://localhost:11434",
        model={"id": "qwen3"},
        make_default=False,
    )
    assert (
        json.loads(models.path.read_text())["providers"]["local"]["base_url"]
        == "http://localhost:11434/v1"
    )
    summary = models.summary()
    assert (
        summary["default"] == ref and summary["providers"][0]["key"] == "sk-…1234"
    )  # masked
    assert "secret" not in json.dumps(summary)

    made = models.add_chatgpt(
        [{"id": "gpt-6-luna", "provider": {"name": "codex", "type": "openai-oauth"}}]
    )
    assert made is None  # others are set up: the default stays the user's
    assert (
        "gpt-realtime-2.1"
        in json.loads(models.path.read_text())["providers"]["codex"]["models"]
    )
    models.set_default("local/qwen3")
    with pytest.raises(ModelsError):
        models.set_default("local/nope")
    models.remove_provider("local")
    assert json.loads(models.path.read_text())["default"] is None


def test_a_chatgpt_sign_in_alone_becomes_the_default(tmp_path):
    models = FactoryModels(tmp_path)
    made = models.add_chatgpt(
        [
            {"id": "gpt-5.5", "provider": {"name": "codex", "type": "openai-oauth"}},
            {"id": "gpt-6-luna", "provider": {"name": "codex", "type": "openai-oauth"}},
        ]
    )
    config = json.loads(models.path.read_text())
    assert (
        made == config["default"] == "codex/gpt-6-luna"
        and config["voice_default"] == "codex/gpt-realtime-2.1"
    )


def test_keys_are_checked_against_the_provider(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if "anthropic" in url:
            assert headers["x-api-key"] == "good" and headers["anthropic-version"]
            if headers["x-api-key"] != "good":
                return httpx.Response(401)
            return httpx.Response(
                200, json={"data": [{"id": "claude-fable-5"}, {"id": "claude-x"}]}
            )
        if url.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "qwen3"}]})
        return httpx.Response(401)

    monkeypatch.setattr(factory_models.httpx, "get", fake_get)
    ok = validate_provider("anthropic", "good")
    assert ok == {"ok": True, "models": ["claude-fable-5", "claude-x"]}
    assert [c["id"] for c in resolve_model_choices("anthropic", ok["models"])] == [
        "claude-fable-5"
    ]
    assert validate_provider("openai", "bad")["code"] == "unauthorized"
    assert validate_provider("ollama", base_url="http://localhost:11434")["models"] == [
        "qwen3"
    ]
    assert resolve_model_choices("ollama", ["qwen3"])[0]["recommended"]


def test_the_model_page_signs_in_through_the_factorys_runtime(tmp_path, monkeypatch):
    root = tmp_path / "factory"
    root.mkdir()
    (root / "workplace.yml").write_text(
        yaml.safe_dump(
            {
                "organization": {
                    "name": "Initech",
                    "slug": "initech",
                    "tz": "America/New_York",
                },
                "admin": {
                    "name": "Bill",
                    "username": "bill",
                    "email": "bill@initech.test",
                    "password": "tps-reports",
                },
                "factory": {
                    "url": "http://factory:8765",
                    "auth_token": "t",
                    "admin_auth_token": "admin-secret",
                    "config_dir": str(tmp_path / "cfg"),
                },
                "env_file": "factory.env",
                "app": {"name": "Initech Factory", "username": "initech-factory"},
            }
        )
    )
    calls = []

    async def fake_call(self, method, params=None, timeout=30.0):
        calls.append((self.url, self.token, method, params))
        if method == "account_status":
            return {
                "providers": [
                    {"id": "openai-chatgpt", "state": "signed_out", "identity": None}
                ]
            }
        if method == "list_models":
            return {
                "default_model": "gpt-6-astra",
                "models": [
                    {
                        "id": "gpt-6-luna",
                        "label": "GPT-6 Luna",
                        "provider": {"name": "codex", "type": "openai-oauth"},
                        "availability": {"available": True, "reasons": []},
                        "is_default": False,
                    }
                ],
            }
        if method == "account_login_start":
            return {
                "state": "pending",
                "login_id": "L1",
                "user_code": "ABCD-1234",
                "verification_url": "https://auth.openai.com/codex/device",
                "interval": 5,
            }
        if method == "account_login_complete":
            return {"state": "completed"}
        return {}

    monkeypatch.setattr(factory_models.FactoryRpc, "call", fake_call)
    workplace = Workplace(tmp_path / "data", base_url="http://127.0.0.1:7300")
    try:
        workplace.factory_config = FactoryConfig(root / "workplace.yml", None)
        workplace.factory_config.apply(workplace)
        environment = (root / "factory.env").read_text()
        assert "FACTORY_AUTH_TOKEN=t" in environment
        assert "OMNIAGENTS_AGENT_HOST_CONTROL_TOKEN=admin-secret" in environment
        client = TestClient(create_app(workplace, static_dir=tmp_path))
        client.cookies.set(
            SESSION_COOKIE,
            workplace.accounts.start_session(workplace.accounts.by_username("bill")),
        )
        assert client.get("/workplace/api/session").json()["factory"]
        got = client.get("/workplace/api/factory/model").json()
        assert (
            got["chatgpt"]["state"] == "signed_out"
            and got["runtime"]["models"][0]["ref"] == "codex/gpt-6-luna"
        )
        assert calls[0][:3] == (
            "ws://factory:8765/ws",
            "admin-secret",
            "account_status",
        )
        started = client.post("/workplace/api/factory/model/chatgpt/start").json()
        assert started["user_code"] == "ABCD-1234"
        assert calls[-1][1] == "admin-secret"
        assert calls[-1][3] == {"provider": "openai-chatgpt", "mode": "device_code"}
        done = client.post(
            "/workplace/api/factory/model/chatgpt/complete", json={"login_id": "L1"}
        ).json()
        assert done["state"] == "completed" and done["default"] == "codex/gpt-6-luna"
        assert (
            json.loads((tmp_path / "cfg" / "models.json").read_text())["default"]
            == "codex/gpt-6-luna"
        )
        # Discovered text models are selectable without copying the live catalog
        # into models.json (which only stores explicit overrides).
        configured = json.loads((tmp_path / "cfg" / "models.json").read_text())
        assert "gpt-6-luna" not in configured["providers"]["codex"]["models"]
        assert (
            client.put(
                "/workplace/api/factory/model/default",
                json={"model": "codex/gpt-6-luna"},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/workplace/api/factory/model/default",
                json={"model": "codex/not-a-model"},
            ).status_code
            == 422
        )

        workplace.add_person(username="peter", name="Peter", email="peter@initech.test")
        peter = TestClient(create_app(workplace, static_dir=tmp_path))
        peter.cookies.set(
            SESSION_COOKIE,
            workplace.accounts.start_session(workplace.accounts.by_username("peter")),
        )
        assert peter.get("/workplace/api/factory/model").status_code == 403
        before = len(calls)
        assert (
            peter.post("/workplace/api/factory/model/chatgpt/start").status_code == 403
        )
        assert len(calls) == before
    finally:
        workplace.close()


def test_an_admin_runs_a_factory_schedule_now(tmp_path, monkeypatch):
    root = tmp_path / "factory"
    root.mkdir()
    (root / "workplace.yml").write_text(
        yaml.safe_dump(
            {
                "organization": {
                    "name": "Initech",
                    "slug": "initech",
                    "tz": "America/New_York",
                },
                "admin": {
                    "name": "Bill",
                    "username": "bill",
                    "email": "bill@initech.test",
                    "password": "tps-reports",
                },
                "factory": {"url": "http://factory:8765", "auth_token": "t"},
                "app": {"name": "Initech Factory", "username": "initech-factory"},
            }
        )
    )
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.method, str(request.url), request.headers.get("authorization"))
        )
        if request.url.path == "/integrations/schedules":
            return httpx.Response(
                200,
                json={
                    "schedules": [
                        {
                            "name": "nightly-review",
                            "automation": "nightly-review",
                            "cron": "0 2 * * *",
                            "timezone": None,
                            "review_runs": 86400,
                            "next": "2026-10-25T07:00:00Z",
                        }
                    ]
                },
            )
        if request.url.path == "/integrations/schedules/nightly-review/run":
            return httpx.Response(200, json={"fired": "nightly-review"})
        return httpx.Response(404, json={"error": "no schedule 'nope'"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    workplace = Workplace(tmp_path / "data", base_url="http://127.0.0.1:7300")
    try:
        workplace.factory_config = FactoryConfig(root / "workplace.yml", None)
        workplace.factory_config.apply(workplace)
        client = TestClient(create_app(workplace, static_dir=tmp_path))
        client.cookies.set(
            SESSION_COOKIE,
            workplace.accounts.start_session(workplace.accounts.by_username("bill")),
        )
        listed = client.get("/workplace/api/factory/schedules").json()
        assert (
            listed["factory_error"] is None
            and listed["schedules"][0]["name"] == "nightly-review"
        )
        assert client.post(
            "/workplace/api/factory/schedules/nightly-review/run"
        ).json() == {"fired": "nightly-review"}
        assert seen[-1] == (
            "POST",
            "http://factory:8765/integrations/schedules/nightly-review/run",
            "Bearer t",
        )
        missing = client.post("/workplace/api/factory/schedules/nope/run")
        assert missing.status_code == 422 and "no schedule" in missing.json()["detail"]
    finally:
        workplace.close()


def test_discovered_default_survives_signing_in_again(tmp_path):
    models = FactoryModels(tmp_path)
    catalog = [
        {
            "id": "gpt-6-astra",
            "provider": {"name": "codex", "type": "openai-oauth"},
            "availability": {"available": True},
        }
    ]
    models.add_chatgpt(catalog)
    models.set_default("codex/gpt-6-astra", catalog)
    models.add_chatgpt(
        [{"id": "gpt-6-luna", "provider": {"name": "codex", "type": "openai-oauth"}}]
    )
    assert models.read()["default"] == "codex/gpt-6-astra"
    with pytest.raises(ModelsError):
        models.set_default(
            "codex/gpt-6-astra", [{**catalog[0], "availability": {"available": False}}]
        )
