"""Signed workplace memory events use the same automation intake as other sources."""

import hashlib
import hmac
import json
import os

from fastapi import HTTPException, Request

from .events import IncomingEvent


def mount_memory_route(app, intake):
    @app.post("/integrations/memory/events")
    async def memory_event(request: Request):
        secret = os.environ.get("MEMORY_WEBHOOK_SECRET", "")
        body = await request.body()
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not secret or not hmac.compare_digest(
            expected, request.headers.get("memory-signature", "")
        ):
            raise HTTPException(401, "Invalid memory signature")
        try:
            payload = json.loads(body)
            kind, delivery = payload["event"], payload["id"]
            if kind not in (
                "memory_created",
                "memory_updated",
                "memory_forgotten",
            ) or not isinstance(delivery, str):
                raise ValueError("Invalid event")
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, "Invalid memory event") from exc
        accepted = await intake.accept(
            IncomingEvent(
                provider="memory", event=kind, delivery_id=delivery, payload=payload
            )
        )
        return {"accepted": accepted}
