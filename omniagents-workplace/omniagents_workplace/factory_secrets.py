"""Admin-owned tool credentials, scoped to one factory and never returned by the API."""

import json
import os
import secrets
import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, SecretStr

from .accounts import admin
from .credentials import decrypt, encrypt

KEY = "factory.secrets"
# Deliberately not arbitrary process environment overrides (PATH, LD_PRELOAD,
# runtime auth, provider routing, etc.). Add supported tool credentials here.
NAMES = ("SERPAPI_API_KEY",)


class SecretsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, SecretStr | None]


class FactorySecrets:
    def __init__(self, workplace):
        self.w = workplace
        self.key_path = workplace.data_dir / "factory-secrets.key"
        self.lock = threading.RLock()

    def values(self):
        encrypted = self.w.store.meta(KEY)
        if not encrypted:
            return {}
        return json.loads(decrypt(encrypted, self.key_path.read_text().strip()))

    def public(self):
        values = self.values()
        return {"configured": [name for name in NAMES if values.get(name)]}

    def environment(self):
        values = self.values()
        # Empty assignments clear credentials inherited from an older container
        # configuration when the generated env file is sourced on restart.
        return {name: values.get(name, "") for name in NAMES}

    def save(self, body):
        if set(body.values) - set(NAMES):
            raise HTTPException(422, "Unsupported factory secret")
        changes = {}
        for name, secret in body.values.items():
            value = secret.get_secret_value().strip() if secret is not None else ""
            if len(value) > 4096 or any(c in value for c in "\0\r\n"):
                raise HTTPException(
                    422, "Enter a single-line API key of at most 4096 characters"
                )
            if secret is not None and not value:
                raise HTTPException(422, "Enter an API key, or remove the saved key")
            changes[name] = value
        with self.lock:
            values = self.values()
            values.update(changes)
            if not self.key_path.exists():
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as out:
                    out.write(secrets.token_hex(32))
            self.w.store.set_meta(
                KEY, encrypt(json.dumps(values), self.key_path.read_text().strip())
            )
            if self.w.factory_config:
                self.w.factory_config.write_environment(self.w)
            return self.public()

    def router(self):
        router = APIRouter()

        @router.get("/workplace/api/factory/secrets")
        def get_secrets(request: Request):
            admin(request)
            return self.public()

        @router.put("/workplace/api/factory/secrets")
        def save_secrets(body: SecretsUpdate, request: Request):
            admin(request)
            return self.save(body)

        return router
