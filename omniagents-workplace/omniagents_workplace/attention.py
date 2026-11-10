import asyncio
import logging
from urllib.parse import quote

import httpx

from .teams_notifications import TeamsNotification


logger = logging.getLogger(__name__)
CURSOR_KEY = "notifications.attention_cursor"


class AttentionNotifications:
    def __init__(self, workplace):
        self.w = workplace

    async def announce(self, client):
        url, token = self.w.approvals.endpoint()
        if not url or not token or not self.w.notifier.url:
            return
        cursor = self.w.store.meta(CURSOR_KEY)
        response = await client.get(
            url + "/integrations/attention",
            params={} if cursor is None else {"after": cursor},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        feed = response.json()
        if cursor is None:
            self.w.store.set_meta(CURSOR_KEY, feed["cursor"])
        for question in feed["questions"]:
            await self.w.teams_notifications.system(
                TeamsNotification(
                    event_id=f"question:{question['kind']}:{question['id']}",
                    title="Factory needs your input",
                    text="An automation is waiting for a person's response. Open Workplace to answer.",
                    path=f"/factory/runs/{question['run_id']}?session={quote(question['session_id'], safe='')}",
                )
            )
        for event in feed["events"]:
            result = await self.w.teams_notifications.system(
                TeamsNotification(
                    event_id=f"run-failed:{event['run_id']}",
                    title="Factory automation failed",
                    text="An automation failed. Open its run in Workplace to review the details.",
                    path=f"/factory/runs/{event['run_id']}",
                )
            )
            if result["status"] == "failed":
                return
            self.w.store.set_meta(CURSOR_KEY, event["id"])
        self.w.store.set_meta(CURSOR_KEY, feed["cursor"])

    async def maintain(self, interval=15):
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            while True:
                try:
                    await self.announce(client)
                except Exception:
                    logger.warning("Factory attention notification check failed")
                await asyncio.sleep(interval)


class QuestionResponsePolicy:
    def __init__(self, workplace, account, agent, run_id):
        self.w = workplace
        self.account = account
        self.agent = agent
        self.run_id = run_id
        self.session_id = None

    async def __call__(self, message):
        from .factory_runtime import _refusal
        import json

        if not isinstance(message, dict):
            return None, None
        method = message.get("method")
        params = message.get("params") or {}
        if method == "resume_session" and isinstance(params, dict):
            self.session_id = params.get("session_id")
        if method not in {"client_response", "elicitation_response"}:
            refusal = _refusal(message)
            if refusal is not None:
                return None, json.loads(refusal) if refusal else None
            return message, None
        reply = {"jsonrpc": "2.0", "id": message.get("id")}
        if not self.run_id or not self.session_id or not isinstance(params, dict):
            return None, {
                **reply,
                "error": {
                    "code": -32602,
                    "message": "Open the pending question's run first",
                },
            }
        url, token = self.w.approvals.endpoint()
        if not url or not token:
            return None, {
                **reply,
                "error": {"code": -32000, "message": "Factory unavailable"},
            }
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            try:
                response = await client.post(
                    url + "/integrations/attention/respond",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "run_id": self.run_id,
                        "agent": self.agent,
                        "session_id": self.session_id,
                        "method": method,
                        "params": params,
                        "actor": self.account.username,
                    },
                )
                response.raise_for_status()
                result = response.json()["result"]
            except (httpx.HTTPError, KeyError, ValueError):
                return None, {
                    **reply,
                    "error": {
                        "code": -32000,
                        "message": "Question could not be answered. It may have expired or already been answered.",
                    },
                }
        self.w.audit_log.record(
            "factory.question_answered",
            actor=self.account.username,
            run=self.run_id,
            agent=self.agent,
            method=method,
        )
        return None, {**reply, "result": result}
