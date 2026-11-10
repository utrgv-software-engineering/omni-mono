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
        summary["default"] == ref and summary["providers"][0]["key"] == "Configured"
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
    with pytest.raises(ModelsError):
        models.remove_provider("local")
    models.set_default("")
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
    def fake_get(url, headers=None, timeout=None, proxy=None):
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
        proxy = "http://factory:test-proxy-token@egress:3128"
        monkeypatch.setattr(
            workplace.egress,
            "runtime_environment",
            lambda hosts: {"HTTPS_PROXY": proxy},
        )

        def probe_get(url, **kwargs):
            assert kwargs.get("proxy") == proxy
            assert url == "https://azure.example/openai/v1/models"
            return httpx.Response(200, json={"data": [{"id": "deployment"}]})

        monkeypatch.setattr(factory_models.httpx, "get", probe_get)
        probe = client.post(
            "/workplace/api/factory/model/validate",
            json={
                "kind": "openai-compatible",
                "base_url": "https://azure.example/openai/v1",
                "api_key": "test-key",
            },
        )
        assert probe.json()["choices"][0]["id"] == "deployment"
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


@pytest.mark.parametrize("proxied", [False, True])
def test_probe_route_and_safe_network_errors(monkeypatch, proxied):
    proxy = "http://factory:private-token@egress:3128" if proxied else None

    def fake_get(url, **kwargs):
        assert kwargs["proxy"] == proxy
        assert url == "https://azure.example/openai/v1/models"
        assert kwargs["headers"] == {"Authorization": "Bearer test-key"}
        raise httpx.ConnectTimeout("private-token test-key")

    monkeypatch.setattr(factory_models.httpx, "get", fake_get)
    result = validate_provider(
        "openai-compatible", "test-key", "https://azure.example/openai/v1", proxy=proxy
    )
    assert result["code"] == "network"
    assert "private-token" not in json.dumps(result)
    assert "test-key" not in json.dumps(result)


def test_provider_edit_preserves_secrets_options_and_stable_references(tmp_path):
    models = FactoryModels(tmp_path)
    config = models.read()
    config.update(default="azure-prod/deployment", extra={"keep": True})
    config["providers"]["azure-prod"] = {
        "type": "openai-compatible",
        "api_key": "test-existing-credential",
        "base_url": "https://old.example/v1",
        "headers": {"custom": "keep"},
        "models": {
            "deployment": {
                "model": "deployment",
                "label": "Production",
                "model_settings": {"temperature": 0.2},
            }
        },
    }
    models.write(config)
    models.update_provider(
        "azure-prod", {"base_url": "https://new.example", "api_key": ""}
    )
    changed = models.read()
    assert changed["providers"]["azure-prod"]["api_key"] == "test-existing-credential"
    assert changed["providers"]["azure-prod"]["headers"] == {"custom": "keep"}
    assert changed["default"] == "azure-prod/deployment" and changed["extra"] == {
        "keep": True
    }
    assert changed["providers"]["azure-prod"]["base_url"] == "https://new.example/v1"
    models.update_provider("azure-prod", {"api_key": "test-rotated-credential"})
    assert (
        models.read()["providers"]["azure-prod"]["api_key"] == "test-rotated-credential"
    )
    assert "credential" not in json.dumps(models.summary())
    before = models.path.read_bytes()
    for patch in [
        {"name": "renamed"},
        {"type": "openai"},
        {"api_key": "sk-…1234"},
        {"base_url": "not a URL"},
    ]:
        with pytest.raises(ModelsError):
            models.update_provider("azure-prod", patch)
        assert models.path.read_bytes() == before


def test_model_crud_preserves_unspecified_settings_and_blocks_default_deletion(
    tmp_path,
):
    models = FactoryModels(tmp_path)
    models.add_provider("openai", model={"id": "first"})
    config = models.read()
    config["providers"]["openai"]["models"]["first"]["custom"] = {"keep": True}
    models.write(config)
    models.update_model("openai", "first", {"label": "New label"})
    assert models.read()["providers"]["openai"]["models"]["first"]["custom"] == {
        "keep": True
    }
    models.add_model("openai", {"id": "second", "label": "Second"})
    assert set(models.read()["providers"]["openai"]["models"]) == {"first", "second"}
    before = models.path.read_bytes()
    for operation in [
        lambda: models.add_model("openai", {"id": "first"}),
        lambda: models.update_model("openai", "first", {"id": "renamed"}),
        lambda: models.update_model("openai", "first", {"maxInput": -1}),
        lambda: models.remove_model("openai", "first"),
        lambda: models.remove_provider("openai"),
    ]:
        with pytest.raises(ModelsError):
            operation()
        assert models.path.read_bytes() == before
    models.remove_model("openai", "second")
    assert "second" not in models.read()["providers"]["openai"]["models"]
    models.set_default("")
    models.remove_provider("openai")
    assert models.read()["default"] is None


@pytest.fixture
def model_api(tmp_path, monkeypatch):
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
                    "password": "test-password",
                },
                "factory": {
                    "url": "http://factory:8765",
                    "config_dir": str(tmp_path / "cfg"),
                },
                "app": {"name": "Factory", "username": "initech-factory"},
            }
        )
    )
    workplace = Workplace(tmp_path / "data", base_url="http://testserver")
    workplace.factory_config = FactoryConfig(root / "workplace.yml", None)
    workplace.factory_config.apply(workplace)
    client = TestClient(create_app(workplace, static_dir=tmp_path))
    client.cookies.set(
        SESSION_COOKIE,
        workplace.accounts.start_session(workplace.accounts.by_username("bill")),
    )
    models = FactoryModels(tmp_path / "cfg")
    models.add_provider(
        "openai", api_key="test-original-credential", model={"id": "first"}
    )
    models.set_default("")

    async def no_agents(self, method, path, timeout=30.0):
        assert method == "GET" and path == "/integrations/definition"
        return {"agents": []}

    monkeypatch.setattr(factory_models.FactoryRpc, "http", no_agents)
    try:
        yield client, workplace, models
    finally:
        workplace.close()


def test_model_api_edits_and_crud_are_persisted_without_probing(model_api, monkeypatch):
    client, workplace, models = model_api

    def no_probe(*args, **kwargs):
        pytest.fail("Saving model settings must not probe a provider")

    monkeypatch.setattr(factory_models, "validate_provider", no_probe)
    base = "/workplace/api/factory/model/providers/openai"
    response = client.patch(
        base, json={"api_key": "", "base_url": "https://override.example/v1"}
    )
    assert response.status_code == 200
    assert "credential" not in response.text
    assert models.read()["providers"]["openai"]["api_key"] == "test-original-credential"
    assert (
        client.post(
            base + "/models",
            json={"id": "vendor/second", "label": "Second", "maxInput": 1234},
        ).status_code
        == 200
    )
    assert (
        client.patch(
            base + "/models/vendor%2Fsecond", json={"label": "Edited", "maxOutput": 99}
        ).status_code
        == 200
    )
    entry = models.read()["providers"]["openai"]["models"]["vendor/second"]
    assert (
        entry["label"] == "Edited"
        and entry["max_input_tokens"] == 1234
        and entry["max_output_tokens"] == 99
    )
    assert entry["model_settings"]["store"] is False
    before = models.path.read_bytes()
    assert client.patch(base, json={"name": "renamed"}).status_code == 422
    assert client.patch(base, json={"api_key": "Configured"}).status_code == 422
    assert (
        client.patch(base + "/models/first", json={"id": "renamed"}).status_code == 422
    )
    assert models.path.read_bytes() == before
    assert client.delete(base + "/models/vendor%2Fsecond").status_code == 200
    assert "vendor/second" not in models.read()["providers"]["openai"]["models"]
    assert client.delete(base).status_code == 200
    assert models.read()["providers"] == {} and models.read()["default"] is None


@pytest.mark.parametrize("inherited", [False, True])
def test_removal_checks_agent_references_and_fails_closed(
    model_api, monkeypatch, inherited
):
    client, workplace, models = model_api
    base = "/workplace/api/factory/model/providers/openai"

    async def used(self, method, path, timeout=30.0):
        if path == "/integrations/definition":
            return {"agents": [{"key": "analyst"}]}
        assert path == "/integrations/definition/agents/analyst"
        return {
            "model": None if inherited else "openai/first",
            "inherited": {"model": "openai/first" if inherited else None},
        }

    monkeypatch.setattr(factory_models.FactoryRpc, "http", used)
    before = models.path.read_bytes()
    for path in [base, base + "/models/first"]:
        response = client.delete(path)
        assert response.status_code == 422 and "analyst" in response.text
        assert models.path.read_bytes() == before

    async def unavailable(*args, **kwargs):
        raise factory_models.FactoryRpcError("private connection details")

    monkeypatch.setattr(factory_models.FactoryRpc, "http", unavailable)
    response = client.delete(base + "/models/first")
    assert response.status_code == 422 and "private connection" not in response.text
    assert models.path.read_bytes() == before


def test_model_api_nonowner_mutations_are_denied_before_network_or_write(
    model_api, monkeypatch
):
    client, workplace, models = model_api
    monkeypatch.setattr(workplace.governance, "gated", lambda: True)
    monkeypatch.setattr(workplace.governance, "is_owner", lambda account: False)

    async def no_network(*args, **kwargs):
        pytest.fail("Denied model mutation must not call the factory")

    monkeypatch.setattr(factory_models.FactoryRpc, "http", no_network)
    before = models.path.read_bytes()
    base = "/workplace/api/factory/model/providers/openai"
    for method, path, body in [
        ("PATCH", base, {"api_key": "test-rotation"}),
        ("POST", base + "/models", {"id": "second"}),
        ("PATCH", base + "/models/first", {"label": "Denied"}),
        ("DELETE", base + "/models/first", None),
        ("DELETE", base, None),
        ("PUT", "/workplace/api/factory/model/default", {"model": "openai/first"}),
    ]:
        response = client.request(method, path, json=body)
        assert response.status_code == 403
        assert models.path.read_bytes() == before


def test_voice_default_cannot_be_removed_and_new_model_keeps_project_fallback(tmp_path):
    models = FactoryModels(tmp_path)
    models.add_chatgpt([])
    models.add_model("codex", {"id": "manual-model"})
    assert models.read()["default"] is None
    config = models.read()
    config["voice_default"] = "codex/gpt-realtime-2.1"
    models.write(config)
    before = models.path.read_bytes()
    with pytest.raises(ModelsError):
        models.remove_model("codex", "gpt-realtime-2.1")
    with pytest.raises(ModelsError):
        models.remove_provider("codex")
    assert models.path.read_bytes() == before


def test_adding_without_make_default_preserves_project_fallback(tmp_path):
    models = FactoryModels(tmp_path)
    models.add_provider("openai", model={"id": "first"}, make_default=False)
    assert models.read()["default"] is None


def test_unqualified_default_references_are_protected(tmp_path):
    models = FactoryModels(tmp_path)
    models.add_provider("openai", model={"id": "first"})
    config = models.read()
    config["default"] = "first"
    models.write(config)
    before = models.path.read_bytes()
    with pytest.raises(ModelsError):
        models.remove_model("openai", "first")
    with pytest.raises(ModelsError):
        models.remove_provider("openai")
    assert models.path.read_bytes() == before
