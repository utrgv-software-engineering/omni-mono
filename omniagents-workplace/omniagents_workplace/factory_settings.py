from __future__ import annotations

import asyncio
import copy
import re
import threading

import httpx
from fastapi import APIRouter, HTTPException, Request

KEY = "factory.operational_settings"


class FactorySettings:
    def __init__(self, workplace, endpoint):
        self.w = workplace
        self.endpoint = endpoint
        self.lock = threading.RLock()

    def runtime(self, method, body=None):
        url, token = self.endpoint()
        if not url or not token:
            raise HTTPException(
                503, "Factory settings are unavailable until the runtime is connected"
            )
        try:
            response = httpx.request(
                method,
                url.rstrip("/") + "/integrations/settings",
                headers={"Authorization": "Bearer " + token},
                json=body,
                timeout=10,
                follow_redirects=False,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(
                503,
                "Factory has not applied these settings. Check its connection and runtime version.",
            ) from None

    def initialize(self):
        saved = self.w.store.meta(KEY)
        if saved is not None:
            return saved
        current = self.runtime("GET")
        saved = {
            "desired": current,
            "applied_revision": current["revision"],
            "error": None,
        }
        if current["display_name"] != self.w.slack.name:
            current["display_name"] = self.w.slack.name
            current["revision"] += 1
        self.w.store.set_meta(KEY, saved)
        return saved

    def snapshot(self, saved):
        result = copy.deepcopy(saved["desired"])
        result["applied_revision"] = saved["applied_revision"]
        result["status"] = (
            "applied" if saved["applied_revision"] == result["revision"] else "pending"
        )
        result["error"] = saved["error"]
        return result

    def get(self):
        with self.lock:
            return self.snapshot(self.initialize())

    def validate(self, value, before):
        if not isinstance(value, dict) or set(value) != {
            "revision",
            "display_name",
            "sources",
        }:
            raise HTTPException(422, "Expected revision, display_name and sources")
        if (
            type(value["revision"]) is not int
            or value["revision"] != before["revision"]
        ):
            raise HTTPException(
                409, "Settings changed in another window. Reload before saving."
            )
        name = value["display_name"]
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 120
            or any(ord(char) < 32 for char in name)
        ):
            raise HTTPException(
                422, "Factory name must be 1–120 characters without control characters"
            )
        expected = {item["source"]: item["identity"] for item in before["sources"]}
        found = {}
        if not isinstance(value["sources"], list):
            raise HTTPException(422, "Event sources must be a list")
        for item in value["sources"]:
            if not isinstance(item, dict) or set(item) != {
                "source",
                "identity",
                "marker",
            }:
                raise HTTPException(422, "Invalid event source settings")
            source = item["source"]
            if (
                not isinstance(source, str)
                or source in found
                or expected.get(source) != item["identity"]
            ):
                raise HTTPException(
                    422, "Event source configuration changed. Reload settings."
                )
            if (
                not isinstance(item["marker"], str)
                or re.fullmatch(r"@[A-Za-z0-9_-]{1,64}", item["marker"]) is None
            ):
                raise HTTPException(
                    422,
                    "Use @ followed by 1–64 letters, numbers, underscores or hyphens",
                )
            found[source] = item["identity"]
        if found != expected:
            raise HTTPException(
                422, "Event source configuration changed. Reload settings."
            )

    def display_name(self, name):
        ws = self.w.slack
        if ws.name != name:
            ws.set_team(name, ws.domain, ws.tz)
        rename = getattr(self.w, "rename_factory", None)
        if rename:
            rename(name)

    def save(self, value, actor):
        with self.lock:
            saved = self.initialize()
            before = saved["desired"]
            self.validate(value, before)
            desired = copy.deepcopy(value)
            desired["display_name"] = desired["display_name"].strip()
            desired["revision"] += 1
            saved = {
                "desired": desired,
                "applied_revision": saved["applied_revision"],
                "error": None,
            }
            with self.w.store.transaction():
                self.w.store.set_meta(KEY, saved)
                self.w.audit_log.record(
                    "factory.settings_changed",
                    actor=actor,
                    before=before,
                    after=desired,
                )
            self.display_name(desired["display_name"])
            return self.reconcile()

    def reconcile(self):
        with self.lock:
            saved = self.w.store.meta(KEY)
            if saved is None:
                return None
            desired = saved["desired"]
            self.display_name(desired["display_name"])
            try:
                applied = self.runtime("GET")
                if applied != desired:
                    applied = self.runtime("PUT", desired)
                if applied != desired:
                    raise HTTPException(
                        503, "Factory did not acknowledge the saved settings"
                    )
                saved["applied_revision"] = desired["revision"]
                saved["error"] = None
            except HTTPException as exc:
                saved["applied_revision"] = None
                saved["error"] = str(exc.detail)
            self.w.store.set_meta(KEY, saved)
            return self.snapshot(saved)

    async def maintain(self):
        while True:
            pending = asyncio.create_task(asyncio.to_thread(self.reconcile))
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                await pending
                raise
            await asyncio.sleep(5)


def settings_router(settings, admin):
    router = APIRouter(prefix="/workplace/api/factory/settings")

    @router.get("")
    def get(request: Request):
        admin(request)
        return settings.get()

    @router.put("")
    def save(body: dict, request: Request):
        account = admin(request)
        return settings.save(body, account.username)

    return router
