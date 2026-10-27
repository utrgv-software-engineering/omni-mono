"""Administrator-only generalist conversations, bound to server-registered sessions.

The generalist keeps running on the factory. This connection belongs to the web
service, not a browser tab; refresh/disconnect never issues stop_run.
"""

from __future__ import annotations

import asyncio
import hashlib
import contextlib
import json
import re
import os
import time
import uuid
from typing import Any

from fastapi import HTTPException
from .factory_models import FactoryRpcError


class PersonalConnection:
    def __init__(
        self,
        url: str,
        token: str | None,
        session_id: str,
        main_session_id: str | None = None,
    ):
        self.base_url = url.rstrip("/")
        self.url = re.sub(r"^http", "ws", url.rstrip("/")) + "/agents/generalist/ws"
        self.token = token
        self.session_id = session_id
        self.main_session_id = main_session_id or session_id
        self.socket = None
        self.reader = None
        self.pending: dict[int, asyncio.Future] = {}
        self.serial = 0
        self.connect_lock = asyncio.Lock()

    async def connect(self):
        async with self.connect_lock:
            if self.socket is not None and self.reader and not self.reader.done():
                return
            if self.socket is not None:
                await self.close()
            import websockets

            try:
                import httpx

                async with httpx.AsyncClient(timeout=10) as client:
                    status = await client.get(
                        self.base_url + "/integrations/architecture",
                        headers=(
                            {"Authorization": f"Bearer {self.token}"}
                            if self.token
                            else {}
                        ),
                    )
                    status.raise_for_status()
                    config = status.json()
                    if (
                        config.get("architecture") != "personal"
                        or config.get("session_id") != self.main_session_id
                    ):
                        raise FactoryRpcError(
                            "The factory is not running the Personal architecture."
                        )
                self.socket = await websockets.connect(
                    self.url,
                    additional_headers=(
                        {"Authorization": f"Bearer {self.token}"} if self.token else {}
                    ),
                    open_timeout=10,
                    max_size=16 * 1024 * 1024,
                )
                self.reader = asyncio.create_task(self._read(self.socket))
                await self._call(
                    "initialize",
                    {
                        "protocol_version": "2.0.0",
                        "identity": {"name": "workplace-personal", "version": "1"},
                        "platform": {"os": "web", "arch": "server"},
                        "capabilities": {
                            "realtime": False,
                            "mcp_apps": False,
                            "client_functions": False,
                            "approvals": True,
                            "artifacts": False,
                            "replay": False,
                            "terminal": False,
                            "experimental_operations": ["queue_status"],
                            "disabled_notifications": [],
                        },
                    },
                )
                await self.socket.send(
                    json.dumps(
                        {"jsonrpc": "2.0", "method": "initialized", "params": {}}
                    )
                )
                # Create/claim the server-selected conversation before reading
                # it. Runtime ownership checks reject reads of unknown sessions.
                await self._call(
                    "server_call",
                    {
                        "function": "session.ensure",
                        "args": {"session_id": self.session_id},
                        "session_id": self.session_id,
                    },
                )
            except Exception as exc:
                await self.close()
                raise FactoryRpcError(
                    "The generalist is unavailable. Check the factory connection and generalist agent."
                ) from exc

    async def _read(self, socket):
        try:
            async for raw in socket:
                message = json.loads(raw)
                future = self.pending.get(message.get("id"))
                if "method" not in message and future is not None and not future.done():
                    if "error" in message:
                        future.set_exception(
                            FactoryRpcError(
                                str(
                                    message["error"].get(
                                        "message", "Agent request failed"
                                    )
                                )
                            )
                        )
                    else:
                        future.set_result(message.get("result"))
                # Canonical snapshots provide authoritative state, including
                # pending controls. No browser responses to upstream requests.
        except Exception:
            pass
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(
                        FactoryRpcError(
                            "The generalist disconnected. Reconnect to check the result before retrying."
                        )
                    )

    async def _call(self, method: str, params: dict):
        self.serial += 1
        rid = self.serial
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.socket.send(
                json.dumps(
                    {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
                )
            )
            return await asyncio.wait_for(future, 30)
        except FactoryRpcError:
            raise
        except Exception as exc:
            raise FactoryRpcError(
                "The generalist did not answer. Reconnect to check the result before retrying."
            ) from exc
        finally:
            self.pending.pop(rid, None)

    async def call(self, method: str, params: dict):
        await self.connect()
        return await self._call(method, params)

    async def state(self):
        return await self.call(
            "queue_status", {"session_id": self.session_id, "include_snapshot": True}
        )

    async def snapshot(self):
        state = await self.state()
        snapshot = state.get("snapshot") or {}
        items = snapshot.get("items") or []
        return {
            "session_id": self.session_id,
            "run_id": state.get("active_run_id"),
            "running": bool(state.get("run_active") or state.get("draining")),
            "queue_depth": state.get("depth", 0),
            "items": items[-200:],
            "truncated": len(items) > 200,
            "revision": (
                hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
                if items
                else ""
            ),
            "attention": [
                i
                for i in items
                if i.get("kind") in ("approval", "elicitation")
                and i.get("status") == "started"
            ],
            "state_events": snapshot.get("state_events") or [],
        }

    async def send(self, text: str, submission_id: str):
        return await self.call(
            "enqueue_message",
            {
                "session_id": self.session_id,
                "content": text,
                "role": "user",
                "source": "workplace:personal",
                "submission_id": submission_id,
            },
        )

    async def stop(self, run_id: str):
        state = await self.state()
        if state.get("active_run_id") != run_id:
            raise HTTPException(
                409, "That run is no longer active. Refresh to see the current work."
            )
        return {"stopped": await self.call("stop_run", {"run_id": run_id})}

    async def decide(self, item_id: str, action: str, value: dict | None):
        state = await self.state()
        item = next(
            (
                i
                for i in (state.get("snapshot") or {}).get("items", [])
                if i.get("item_id") == item_id
                and i.get("status") == "started"
                and i.get("kind") in ("approval", "elicitation")
            ),
            None,
        )
        if item is None:
            raise HTTPException(
                409, "This request is no longer pending in your conversation."
            )
        content = item.get("content") or {}
        if item["kind"] == "approval":
            if action not in ("approve", "reject"):
                raise HTTPException(422, "Choose approve or reject.")
            if content.get("approval_kind") == "mcp":
                return await self.call(
                    "mcp_approval_response",
                    {"request_id": content["request_id"], "decision": action},
                )
            return await self.call(
                "tool_approval_response",
                {
                    "call_id": content["call_id"],
                    "decision": action,
                    "always_approve": False,
                },
            )
        if action not in ("accept", "decline", "cancel"):
            raise HTTPException(422, "Invalid answer action.")
        return await self.call(
            "elicitation_response",
            {
                "elicitation_id": content["elicitation_id"],
                "action": action,
                "value": value,
            },
        )

    async def close(self):
        if self.socket is not None:
            await self.socket.close()
        if self.reader is not None:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader
        self.socket = self.reader = None


class PersonalConversations:
    def __init__(self, store):
        self.store = store
        self.architecture = os.environ.get("FACTORY_ARCHITECTURE", "specialist")
        if self.architecture not in {"personal", "specialist"}:
            raise ValueError("FACTORY_ARCHITECTURE must be personal or specialist")
        self.deleting: set[str] = set()
        self.connections: dict[str, tuple[tuple, PersonalConnection]] = {}

    def chats(self):
        return [
            {"id": "factory-personal", "title": "Main Chat", "archived": False},
            *sorted(
                (d for _, d in self.store.docs("personal.chat")),
                key=lambda d: d["created_at"],
                reverse=True,
            ),
        ]

    def exists(self, session_id):
        return session_id not in self.deleting and any(
            c["id"] == session_id for c in self.chats()
        )

    def create(self, title="New chat", *, auto_title=True):
        chat = {
            "id": "personal-" + uuid.uuid4().hex,
            "title": title,
            "archived": False,
            "created_at": time.time(),
            "auto_title": auto_title,
        }
        self.store.put("personal.chat", chat["id"], chat)
        return chat

    def update(self, session_id, changes):
        if session_id == "factory-personal":
            raise HTTPException(409, "Main Chat stays pinned.")
        chat = next((c for c in self.chats() if c["id"] == session_id), None)
        if chat is None:
            raise HTTPException(404, "Chat not found.")
        chat.update(changes)
        if "title" in changes:
            chat["auto_title"] = False
        self.store.put("personal.chat", session_id, chat)
        return chat

    async def summaries(self, account, url, token):
        # Isolate failures: an unavailable runtime must not hide the saved list.
        limit = asyncio.Semaphore(8)

        async def summary(chat):
            async with limit:
                try:
                    connection = await self.for_account(account, url, token, chat["id"])
                    state = await connection.snapshot()
                    current = next(
                        (c for c in self.chats() if c["id"] == chat["id"]), None
                    )
                    if current is None:
                        return None
                    if current.get("auto_title"):
                        first = next(
                            (
                                item
                                for item in state["items"]
                                if item.get("kind") == "user_message"
                            ),
                            None,
                        )
                        text = (
                            (first.get("content") or {}).get("text", "")
                            if first
                            else ""
                        )
                        if isinstance(text, str) and text.strip():
                            text = re.sub(r"^\[[^\]\n]+\]\n", "", text).strip()
                            title = " ".join(text.split())[:60]
                            if title:
                                current = self.update(chat["id"], {"title": title})
                    return {
                        **current,
                        "running": state["running"],
                        "waiting": bool(state["attention"]),
                        "revision": state["revision"],
                    }
                except (FactoryRpcError, HTTPException):
                    return {**chat, "offline": True}

        return [
            chat
            for chat in await asyncio.gather(*(summary(c) for c in self.chats()))
            if chat is not None
        ]

    async def delete(self, connection):
        sid = connection.session_id
        if sid == "factory-personal":
            raise HTTPException(409, "Main Chat stays pinned.")
        if sid in self.deleting:
            raise HTTPException(409, "Chat is being deleted.")
        self.deleting.add(sid)
        try:
            state = await connection.state()
            if state.get("run_active") or state.get("draining") or state.get("depth"):
                raise HTTPException(
                    409, "Stop this chat's work and clear its queue before deleting it."
                )
            await connection.call("delete_session", {"session_id": sid})
            self.store.delete("personal.chat", sid)
            self.connections.pop(sid, None)
            await connection.close()
        finally:
            self.deleting.discard(sid)
        return {"ok": True}

    async def for_account(self, account, url, token, session_id="factory-personal"):

        # Shared factory credentials are administrative authority. Do not expose
        # them to ordinary members merely because they can sign into Workplace.
        if not account.admin or account.disabled:
            raise HTTPException(
                403, "Personal currently requires an administrator account."
            )
        if not url:
            raise HTTPException(
                503, "Connect a factory with the generalist agent to use Personal."
            )
        if self.architecture != "personal":
            raise HTTPException(
                409, "Personal is disabled in the specialist architecture."
            )
        # Runtime stores are already scoped by factory/product. Never merge the
        # old account conversations into this new shared history.
        if not self.exists(session_id):
            raise HTTPException(404, "Chat not found.")
        key = (url, token, session_id)
        previous = self.connections.get(session_id)
        if previous and previous[0] != key:
            await previous[1].close()
        if not previous or previous[0] != key:
            self.connections[session_id] = (
                key,
                PersonalConnection(url, token, session_id, "factory-personal"),
            )
        return self.connections[session_id][1]

    async def close(self):
        await asyncio.gather(*(c.close() for _, c in self.connections.values()))
        self.connections.clear()
