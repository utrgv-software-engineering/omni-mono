import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from fastapi.testclient import TestClient

from omniagents_workplace.accounts import SESSION_COOKIE
from omniagents_workplace.config import FactoryConfig
from omniagents_workplace.governance import OWNERS_KEY, SERVICES_KEY
from omniagents_workplace.server import Workplace, create_app


def main():
    os.environ["WORKPLACE_POLICY"] = "strict"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / "workplace.yml"
        config.write_text(
            yaml.safe_dump(
                {
                    "organization": {
                        "name": "Image smoke",
                        "slug": "image-smoke",
                        "tz": "UTC",
                    },
                    "admin": {
                        "name": "Owner",
                        "username": "owner",
                        "email": "owner@example.test",
                        "password": "image-smoke-password",
                    },
                    "factory": {"config_dir": str(root / "config")},
                    "app": {"name": "Smoke factory", "username": "smoke-factory"},
                }
            )
        )
        workplace = Workplace(root / "data", base_url="http://testserver")
        workplace.factory_config = FactoryConfig(config, None)
        workplace.factory_config.apply(workplace)
        account = workplace.accounts.by_username("owner")
        workplace.store.set_meta(OWNERS_KEY, [account.id])
        workplace.store.set_meta(
            SERVICES_KEY,
            [
                {
                    "kind": "model",
                    "name": "Test Azure",
                    "provider": "openai-compatible",
                    "endpoint": "https://azure.example/openai/v1",
                    "note": "",
                }
            ],
        )
        client = TestClient(create_app(workplace, static_dir=root))
        client.cookies.set(SESSION_COOKIE, workplace.accounts.start_session(account))
        body = {
            "kind": "openai-compatible",
            "api_key": "dummy-not-a-real-key",
            "base_url": "https://azure.example/openai/v1",
            "model": {"id": "deployment", "maxInput": 0, "maxOutput": 0},
            "make_default": True,
        }
        response = client.post("/workplace/api/factory/model/providers", json=body)
        assert response.status_code == 200, response.text
        saved = json.loads((root / "config/models.json").read_text())
        assert saved["default"] == response.json()["ref"]
        before = (root / "config/models.json").read_bytes()
        response = client.post(
            "/workplace/api/factory/model/providers",
            json={**body, "base_url": "https://not-allowed.example/v1"},
        )
        assert response.status_code == 422, response.text
        assert (root / "config/models.json").read_bytes() == before
        workplace.store.set_meta(OWNERS_KEY, [])
        response = client.post("/workplace/api/factory/model/providers", json=body)
        assert response.status_code == 403, response.text
        assert (root / "config/models.json").read_bytes() == before
        print("STRICT_MODEL_SAVE_IMAGE_SMOKE_OK")


if __name__ == "__main__":
    main()
