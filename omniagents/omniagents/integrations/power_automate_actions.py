from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from uuid import UUID, uuid4

from .power_automate_policy import actor_authorized


OPERATIONS = {
    "teams.reply",
    "sharepoint.get_file",
    "sharepoint.update_item",
    "outlook.send_email",
}


def validate_access_policy(policy):
    access = policy.get("agent_access", {})
    if not isinstance(access, dict):
        raise ValueError("agent_access must be a mapping")
    for agent, rules in access.items():
        if not isinstance(agent, str) or not agent or not isinstance(rules, dict):
            raise ValueError("invalid agent_access")
        operations = rules.get("operations", [])
        if not isinstance(operations, list) or not all(
            isinstance(value, str) and value in OPERATIONS for value in operations
        ):
            raise ValueError("invalid allowed operations")
        if not isinstance(rules.get("download_attachments", False), bool):
            raise ValueError("invalid attachment permission")
        sites = rules.get("sharepoint_sites", {})
        if not isinstance(sites, dict):
            raise ValueError("invalid SharePoint policy")
        for site, settings in sites.items():
            if not isinstance(site, str) or not isinstance(settings, dict):
                raise ValueError("invalid SharePoint site")
            libraries = settings.get("libraries", [])
            lists = settings.get("lists", {})
            if not isinstance(libraries, list) or not all(
                isinstance(item, str) and item for item in libraries
            ):
                raise ValueError("invalid library allowlist")
            if not isinstance(lists, dict) or not all(
                isinstance(key, str)
                and isinstance(fields, list)
                and all(isinstance(field, str) and field for field in fields)
                for key, fields in lists.items()
            ):
                raise ValueError("invalid list field allowlist")
        for key in ("mailboxes", "recipients"):
            values = rules.get(key, [])
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value for value in values
            ):
                raise ValueError("invalid email allowlist")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def text(value, maximum=2048):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode()) > maximum
        or "\x00" in value
    ):
        raise ValueError("invalid text field")
    return value


def validate_action(operation, payload, rules, context):
    if operation not in rules.get("operations", []):
        raise ValueError("operation_not_authorized")
    if not isinstance(payload, dict) or len(canonical(payload).encode()) > 131072:
        raise ValueError("invalid_action_payload")
    if operation == "teams.reply":
        if set(payload) != {"text"}:
            raise ValueError("invalid_reply_fields")
        return {
            **{
                key: context[key]
                for key in ("tenant_id", "team_id", "channel_id", "root_message_id")
            },
            "text": text(payload["text"], 16000),
            "bridge_generated": True,
        }
    if operation in ("sharepoint.get_file", "sharepoint.update_item"):
        expected = (
            {"site_id", "library_id", "file_id"}
            if operation == "sharepoint.get_file"
            else {"site_id", "list_id", "item_id", "fields", "etag"}
        )
        if set(payload) != expected:
            raise ValueError("invalid_sharepoint_fields")
        site = text(payload["site_id"])
        settings = rules.get("sharepoint_sites", {}).get(site, {})
        if operation == "sharepoint.get_file":
            if text(payload["library_id"]) not in settings.get("libraries", []):
                raise ValueError("library_not_authorized")
            text(payload["file_id"])
        else:
            allowed = settings.get("lists", {}).get(text(payload["list_id"]), [])
            text(payload["item_id"])
            if text(payload["etag"]) == "*":
                raise ValueError("explicit_item_version_required")
            fields = payload["fields"]
            if (
                not isinstance(fields, dict)
                or not fields
                or not all(
                    key in allowed
                    and (value is None or isinstance(value, (str, int, float, bool)))
                    for key, value in fields.items()
                )
            ):
                raise ValueError("list_fields_not_authorized")
        return payload
    if operation == "outlook.send_email":
        if set(payload) != {"mailbox", "to", "subject", "body"}:
            raise ValueError("invalid_email_fields")
        if text(payload["mailbox"]).casefold() not in {
            item.casefold() for item in rules.get("mailboxes", [])
        }:
            raise ValueError("mailbox_not_authorized")
        recipients = payload["to"]
        if (
            not isinstance(recipients, list)
            or not 1 <= len(recipients) <= 50
            or not all(
                text(item).casefold()
                in {value.casefold() for value in rules.get("recipients", [])}
                for item in recipients
            )
        ):
            raise ValueError("recipient_not_authorized")
        text(payload["subject"], 998)
        text(payload["body"], 65536)
        return {**payload, "body_format": "text"}
    raise ValueError("unsupported_operation")


class PowerAutomateActions:
    def __init__(self, source, event_store):
        self.source = source
        self.event_store = event_store

    async def download(self, agent, session_id, attachment_id, workspace):
        from .power_automate_files import fetch_attachment
        from omniagents.workspace._process import await_cleanup

        source, identity = await self.authorize_attachment(
            agent, session_id, attachment_id
        )
        with tempfile.TemporaryDirectory(prefix="omni-bridge-") as staging:
            staged = await await_cleanup(
                asyncio.create_task(
                    asyncio.to_thread(
                        fetch_attachment,
                        os.environ[self.source.config.connection_env],
                        identity,
                        source,
                        staging,
                    )
                )
            )
            directory = workspace.resolve_confined(f".omni-attachment-{uuid4()}")
            await workspace.mkdir(directory, parents=False, exist_ok=False)
            target = workspace.resolve_confined(f"{directory}/{identity}")
            try:
                with staged.open("rb") as stream:
                    await workspace.write_stream(target, stream)
            except BaseException:
                await await_cleanup(
                    asyncio.create_task(workspace.remove(directory, recursive=True))
                )
                raise
            return {
                "attachment_id": identity,
                "path": target,
                "byte_size": staged.stat().st_size,
            }

    def context(self, agent, session_id):
        rows = self.event_store._db.execute(
            "SELECT DISTINCT events.payload FROM dispatches JOIN events ON events.id=dispatches.event_id WHERE dispatches.agent=? AND dispatches.session_id=? AND events.provider='teams' ORDER BY events.id",
            (agent, session_id),
        ).fetchall()
        if not rows:
            raise ValueError("no_bridge_conversation")
        payloads = [json.loads(row[0]) for row in rows]
        sources = {payload.get("bridge_source") for payload in payloads}
        if len(sources) != 1:
            raise ValueError("ambiguous_bridge_source")
        source = payloads[-1].get("bridge_source")
        policy = self.source.config.sources.get(source, {})
        rules = policy.get("agent_access", {}).get(agent)
        if rules is None:
            raise ValueError("agent_not_authorized")
        for payload in payloads:
            if not actor_authorized(policy, payload) or any(
                payload.get(key) != policy.get(key)
                for key in ("tenant_id", "team_id", "channel_id")
            ):
                raise ValueError("conversation_no_longer_authorized")
        attachments = {
            item["id"]
            for payload in payloads
            for item in payload.get("attachments", [])
        }
        return source, rules, payloads[-1], attachments

    async def request(self, agent, session_id, operation, payload, idempotency_key):
        source, rules, context, _ = self.context(agent, session_id)
        text(idempotency_key, 256)
        validated = validate_action(operation, payload, rules, context)
        body = canonical(validated)
        identity = hashlib.sha256(
            canonical([source, agent, session_id, idempotency_key]).encode()
        ).hexdigest()
        async with await self.source.connect() as connection:
            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO omni_bridge.outbound_actions (id,source,operation,payload,agent,session_id) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                    (identity, source, operation, body, agent, session_id),
                )
                cursor = await connection.execute(
                    "SELECT operation,payload,agent,session_id,state FROM omni_bridge.outbound_actions WHERE id=%s AND source=%s",
                    (identity, source),
                )
                row = await cursor.fetchone()
                if row is None or row[:4] != (operation, body, agent, session_id):
                    raise ValueError("idempotency_conflict")
        return {"action_id": identity, "state": row[4]}

    async def status(self, agent, session_id, action_id):
        source, _, _, _ = self.context(agent, session_id)
        text(action_id, 128)
        async with await self.source.connect() as connection:
            cursor = await connection.execute(
                "SELECT operation,state,result,result_attachment_id FROM omni_bridge.outbound_actions WHERE id=%s AND source=%s AND agent=%s AND session_id=%s",
                (action_id, source, agent, session_id),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ValueError("action_not_available")
        return {
            "action_id": action_id,
            "operation": row[0],
            "state": row[1],
            "result": row[2],
            "attachment_id": str(row[3]) if row[3] else None,
        }

    async def authorize_attachment(self, agent, session_id, attachment_id):
        source, rules, _, attachments = self.context(agent, session_id)
        identity = str(UUID(attachment_id))
        if not rules.get("download_attachments", False):
            raise ValueError("attachments_not_authorized")
        if identity not in attachments:
            async with await self.source.connect() as connection:
                cursor = await connection.execute(
                    "SELECT 1 FROM omni_bridge.outbound_actions WHERE source=%s AND agent=%s AND session_id=%s AND operation='sharepoint.get_file' AND state='succeeded' AND result_attachment_id=%s",
                    (source, agent, session_id, identity),
                )
                if await cursor.fetchone() is None:
                    raise ValueError("attachment_not_in_conversation")
        return source, identity
