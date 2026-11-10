from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import sqlite3
from dataclasses import replace


def source_identity(source, policy):
    activation = policy["thread_activation"]
    values = [source, activation["factory_id"]]
    values.extend(policy[key] for key in ("tenant_id", "team_id", "channel_id"))
    values.append(activation["not_before"])
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


def pin_thread_namespaces(config, database):
    with database:
        database.execute(
            "CREATE TABLE IF NOT EXISTS bridge_thread_namespaces (identity TEXT PRIMARY KEY, marker TEXT NOT NULL)"
        )
        for source, policy in config.sources.items():
            if not policy.get("thread_activation"):
                continue
            identity = source_identity(source, policy)
            database.execute(
                "INSERT OR IGNORE INTO bridge_thread_namespaces VALUES (?, ?)",
                (identity, policy["thread_activation"]["marker"]),
            )
            marker = database.execute(
                "SELECT marker FROM bridge_thread_namespaces WHERE identity=?",
                (identity,),
            ).fetchone()[0]
            policy["thread_activation"]["identity_marker"] = marker


class FactorySettings:
    def __init__(self, path, source, name):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.database = sqlite3.connect(path)
        path.chmod(0o600)
        self.source = source
        self.lock = source.delivery_lock if source else asyncio.Lock()
        self.value = {"revision": 0, "display_name": name, "sources": self.sources()}
        with self.database:
            self.database.execute(
                "CREATE TABLE IF NOT EXISTS factory_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)"
            )
        row = self.database.execute(
            "SELECT value FROM factory_settings WHERE id=1"
        ).fetchone()
        if row:
            saved = json.loads(row[0])
            self.validate(saved)
            self.value = saved
            self.apply_markers(saved)

    def sources(self):
        if self.source is None:
            return []
        return [
            {
                "source": name,
                "identity": source_identity(name, policy),
                "marker": policy["thread_activation"]["marker"],
            }
            for name, policy in sorted(self.source.config.sources.items())
            if policy.get("thread_activation")
            and policy.get("handler", "teams") == "teams"
        ]

    def validate(self, value):
        if not isinstance(value, dict) or set(value) != {
            "revision",
            "display_name",
            "sources",
        }:
            raise ValueError("Expected revision, display_name and sources")
        if type(value["revision"]) is not int or value["revision"] < 0:
            raise ValueError("Invalid settings revision")
        name = value["display_name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 120
            or any(ord(char) < 32 for char in name)
        ):
            raise ValueError(
                "Factory name must be 1–120 characters without control characters"
            )
        if not isinstance(value["sources"], list):
            raise ValueError("Event sources must be a list")
        expected = {item["source"]: item["identity"] for item in self.sources()}
        found = {}
        for item in value["sources"]:
            if not isinstance(item, dict) or set(item) != {
                "source",
                "identity",
                "marker",
            }:
                raise ValueError("Invalid event source settings")
            source = item["source"]
            if (
                not isinstance(source, str)
                or source in found
                or expected.get(source) != item["identity"]
            ):
                raise ValueError("Event source configuration changed; reload settings")
            if (
                not isinstance(item["marker"], str)
                or re.fullmatch(r"@[A-Za-z0-9_-]{1,64}", item["marker"]) is None
            ):
                raise ValueError(
                    "Use @ followed by 1–64 letters, numbers, underscores or hyphens"
                )
            found[source] = item["identity"]
        if found != expected:
            raise ValueError("Event source configuration changed; reload settings")

    def apply_markers(self, value):
        if self.source:
            policies = copy.deepcopy(self.source.config.sources)
            for item in value["sources"]:
                policies[item["source"]]["thread_activation"]["marker"] = item["marker"]
            self.source.config = replace(self.source.config, sources=policies)

    def snapshot(self):
        return copy.deepcopy(self.value)

    async def update(self, value):
        async with self.lock:
            self.validate(value)
            if value["revision"] < self.value["revision"] or (
                value["revision"] == self.value["revision"] and value != self.value
            ):
                raise RuntimeError("Settings revision conflict")
            with self.database:
                self.database.execute(
                    "INSERT OR REPLACE INTO factory_settings VALUES (1, ?)",
                    (json.dumps(value),),
                )
            self.apply_markers(value)
            self.value = copy.deepcopy(value)
            return self.snapshot()


def mount_factory_settings(app, settings, authorized):
    from starlette.responses import JSONResponse, Response

    async def endpoint(request):
        if not authorized(request):
            return Response(status_code=403)
        if request.method == "GET":
            return JSONResponse(settings.snapshot())
        try:
            return JSONResponse(await settings.update(await request.json()))
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except RuntimeError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    app.add_route("/integrations/settings", endpoint, methods=["GET", "PUT"])
    app.router.on_shutdown.append(settings.database.close)
