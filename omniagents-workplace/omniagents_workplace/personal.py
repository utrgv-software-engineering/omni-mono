"""Administrator-only agent conversations, bound to server-registered sessions.

Every chat agent has one Main Chat, shared by every admin and pinned; admins
add Side Chats beside it; automations land in their own chats (``kind:
automation``), which archive themselves once their pull request or issue
closes. Each admin's unread state is their own.

The agent keeps running on the factory. This connection belongs to the web
service, not a browser tab; refresh/disconnect never issues stop_run.
"""

from __future__ import annotations

import asyncio
import hashlib
import contextlib
import json
import re
import time
import uuid
from typing import Any

from fastapi import HTTPException
from .factory_models import FactoryRpcError


def sender_line(name, username):
    """Who wrote a message, as the agent and memory extraction read it
    (omniagents' ``core/memory/extract.py`` SPEAKER_RULE names this format)."""
    return f"[From {name} (@{username})]"


async def chat_configuration(rpc):
    config = await rpc.http("GET", "/integrations/architecture")
    if "agents" not in config:
        # Older pinned runtimes expose their actual entry agent through RPC.
        # Never guess a role name.
        info = await rpc.call("get_agent_info", {})
        agent = info.get("name")
        if not isinstance(agent, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", agent):
            raise FactoryRpcError("The factory did not identify its chat agent")
        config = {
            **config,
            "agents": [agent],
            "chat_agents": [agent],
            "chat_agent": agent,
        }
    return config


class PersonalConnection:
    def __init__(
        self,
        url: str,
        token: str | None,
        session_id: str,
        agent: str | None = None,
    ):
        self.base_url = url.rstrip("/")
        self.agent = agent
        self.url = re.sub(r"^http", "ws", url.rstrip("/")) + (
            f"/agents/{agent}/ws" if agent else "/ws"
        )
        self.token = token
        self.session_id = session_id
        self.socket = None
        self.reader = None
        self.pending: dict[int, asyncio.Future] = {}
        self.serial = 0
        self.connect_lock = asyncio.Lock()
        self.messages = None
        # The factory pushes every change of this conversation to this
        # connection; reads are served from these until a push says they
        # changed. ``None`` is unknown: read it from the factory.
        self.cached_state = None
        self.cached_workers = None
        self.cached_items = {}
        self.changes = 0
        self.fetch_lock = asyncio.Lock()

    async def connect(self):
        async with self.connect_lock:
            if self.socket is not None and self.reader and not self.reader.done():
                return
            if self.socket is not None:
                await self.close()
            import websockets

            try:
                from .factory_models import FactoryRpc

                config = await chat_configuration(FactoryRpc(self.base_url, self.token))
                self.agent = self.agent or config.get("chat_agent")
                if not self.agent or self.agent not in config.get("agents", []):
                    raise FactoryRpcError(
                        "The selected chat agent is unavailable in this factory."
                    )
                self.url = (
                    re.sub(r"^http", "ws", self.base_url) + f"/agents/{self.agent}/ws"
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
                            # Streaming deltas; the finished messages suffice.
                            "disabled_notifications": ["token"],
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
                    "The agent is unavailable. Check the factory connection and selected agent."
                ) from exc

    def _changed(self, message):
        params = message.get("params") or {}
        if params.get("session_id") not in (None, self.session_id):
            return
        self.changes += 1
        self.cached_state = None
        self.cached_items = {}
        if (
            message["method"] == "client_request"
            and params.get("function") == "ui.subagents.update"
        ):
            snapshot = (params.get("args") or {}).get("snapshot")
            if isinstance(snapshot, list):
                self.cached_workers = snapshot

    async def _read(self, socket):
        try:
            async for raw in socket:
                message = json.loads(raw)
                if "method" in message and "id" not in message:
                    self._changed(message)
                    continue
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
            # Pushes missed while disconnected: read everything again.
            self.cached_state = self.cached_workers = None
            self.cached_items = {}
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(
                        FactoryRpcError(
                            "The agent disconnected. Reconnect to check the result before retrying."
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
                "The agent did not answer. Reconnect to check the result before retrying."
            ) from exc
        finally:
            self.pending.pop(rid, None)

    async def call(self, method: str, params: dict):
        await self.connect()
        return await self._call(method, params)

    async def state(self, *, fresh=False):
        """The conversation's queue, run state and newest items. A decision
        about acting on it passes ``fresh``; a display reads what the last
        push left current."""
        async with self.fetch_lock:
            if not fresh and self.cached_state is not None:
                return self.cached_state
            seen = self.changes
            state = await self.call(
                "queue_status",
                {
                    "session_id": self.session_id,
                    "include_snapshot": True,
                    "snapshot_limit": SNAPSHOT_ITEMS,
                },
            )
            snapshot = state.get("snapshot") or {}
            if snapshot.get("stream_id"):
                # Read through here: the factory may let go of older events.
                await self.call(
                    "ack_events",
                    {
                        "session_id": self.session_id,
                        "stream_id": snapshot["stream_id"],
                        "seq": snapshot.get("last_seq") or 0,
                    },
                )
            if self.changes == seen:
                self.cached_state = state
            return state

    async def items(self, params: dict):
        """A page of ``list_items``. This conversation's pages are kept until
        a push changes it; another thread's (a worker's) are read each time."""
        if params.get("thread_id") != self.session_id:
            return await self.call("list_items", params)
        key = json.dumps(params, sort_keys=True)
        if key not in self.cached_items:
            seen = self.changes
            page = await self.call("list_items", params)
            if self.changes != seen:
                return page
            self.cached_items[key] = page
        return self.cached_items[key]

    async def workers(self):
        """This conversation's workers, as last pushed by the factory."""
        if self.cached_workers is None:
            seen = self.changes
            result = await self.call(
                "server_call",
                {
                    "function": "workers.list",
                    "args": {},
                    "session_id": self.session_id,
                },
            )
            if self.changes == seen or self.cached_workers is None:
                self.cached_workers = result.get("snapshot", [])
        return self.cached_workers

    async def snapshot(self, *, fresh=False):
        state = await self.state(fresh=fresh)
        snapshot = state.get("snapshot") or {}
        items = snapshot.get("items") or []
        return {
            "session_id": self.session_id,
            "run_id": state.get("active_run_id"),
            "running": bool(state.get("run_active") or state.get("draining")),
            "queue_depth": state.get("depth", 0),
            "items": items,
            "truncated": bool(snapshot.get("earlier_cursor")),
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

    async def send(
        self,
        text: str,
        submission_id: str,
        *,
        author=None,
        author_name=None,
        input_content=None,
    ):
        if author is not None and self.messages is not None:
            self.messages.publish(
                self.session_id, submission_id, "user", author, text, input_content
            )
        if author is not None:
            # Admins share chats: the agent (and memory extraction) must know
            # who wrote each message. The public feed keeps the plain text.
            line = sender_line(author_name or author, author)
            text = f"{line}\n\n{text}"
            if isinstance(input_content, str):
                input_content = f"{line}\n\n{input_content}"
            elif isinstance(input_content, list):
                input_content = [{"type": "input_text", "text": line}, *input_content]
        result = await self.call(
            "enqueue_message",
            {
                "session_id": self.session_id,
                "content": text,
                "role": "user",
                "source": "workplace:personal",
                "submission_id": submission_id,
                **(
                    {"input_content": input_content}
                    if input_content is not None
                    else {}
                ),
                **(
                    {
                        "variables": {
                            "workplace_sender": author,
                            "workplace_conversation": self.session_id,
                        }
                    }
                    if author
                    else {}
                ),
            },
        )

        if result.get("ok") is False:
            raise HTTPException(
                409, result.get("reason") or "The message was not queued"
            )
        return result

    async def stop(self, run_id: str):
        state = await self.state(fresh=True)
        if state.get("active_run_id") != run_id:
            raise HTTPException(
                409, "That run is no longer active. Refresh to see the current work."
            )
        return {"stopped": await self.call("stop_run", {"run_id": run_id})}

    async def decide(self, item_id: str, action: str, value: dict | None):
        state = await self.state(fresh=True)
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


#: The newest items a chat's state carries; older ones page in on demand.
SNAPSHOT_ITEMS = 200

MAIN_TITLE = "Main Chat"
#: The default agent's Main Chat keeps the original shared conversation's ID,
#: so its history carries over.
ORIGINAL_MAIN = "factory-personal"


class PersonalConversations:
    def __init__(self, store):
        self.store = store
        from .chat_messages import ChatMessages

        self.messages = ChatMessages(store)
        self.deleting: set[str] = set()
        self.connections: dict[str, tuple[tuple, PersonalConnection]] = {}
        # Chats saved before kinds existed: those automations registered, and
        # every other one a person started.
        for chat_id, chat in self.store.docs("personal.chat"):
            if "kind" not in chat:
                legacy = chat.get("automation") is True
                chat["kind"] = "automation" if legacy else "side"
                if legacy:
                    chat["automation"] = None
                self.store.put("personal.chat", chat_id, chat)

    def _deleted(self):
        return dict(self.store.docs("chat.deleted"))

    def chats(self):
        return sorted(
            (dict(chat) for _, chat in self.store.docs("personal.chat")),
            key=lambda chat: chat.get("created_at", 0),
            reverse=True,
        )

    def _get(self, session_id):
        return dict(self.store.docs("personal.chat")).get(session_id)

    def ensure_mains(self, agents, default_agent):
        """Every chat agent has a Main Chat; the default agent's is the original."""
        rows = dict(self.store.docs("personal.chat"))
        have = {c["agent"] for c in rows.values() if c.get("kind") == "main"}
        for agent in agents:
            if agent in have:
                continue
            original = rows.get(ORIGINAL_MAIN)
            adopt = (
                ORIGINAL_MAIN not in self._deleted()
                and (original is None or original.get("kind") != "main")
                and ((original or {}).get("agent") or default_agent) == agent
            )
            chat_id = ORIGINAL_MAIN if adopt else f"main-{agent}"
            chat = {
                **(rows.get(chat_id) or {"created_at": 0}),
                "id": chat_id,
                "title": MAIN_TITLE,
                "agent": agent,
                "kind": "main",
                "archived": False,
                "auto_title": False,
            }
            self.store.put("personal.chat", chat_id, chat)
            rows[chat_id] = chat

    def register_automation(self, conversation):
        """An automation conversation as a chat of its agent, archived once its
        work item closes (once: an admin may restore it)."""
        chat_id = conversation["id"]
        if chat_id in self._deleted():
            return
        chat = self._get(chat_id)
        if chat is None:
            chat = {
                "id": chat_id,
                "title": conversation.get("title") or "Automation",
                "agent": conversation.get("agent"),
                "kind": "automation",
                "archived": False,
                "auto_title": False,
                "created_at": conversation.get("created_at") or time.time(),
                "automation": conversation.get("automation"),
                "source": conversation.get("source"),
                "thread_key": conversation.get("thread_key"),
            }
        elif chat.get("kind") != "automation":
            return
        else:
            for field in ("automation", "source", "thread_key"):
                chat[field] = chat.get(field) or conversation.get(field)
        if conversation.get("closed") and not chat.get("auto_archived"):
            chat.update(archived=True, auto_archived=True)
        self.store.put("personal.chat", chat_id, chat)

    def exists(self, session_id):
        return session_id not in self.deleting and self._get(session_id) is not None

    def create(self, title="New chat", *, auto_title=True, agent=None):
        chat = {
            "id": "personal-" + uuid.uuid4().hex,
            "title": title,
            "agent": agent,
            "kind": "side",
            "archived": False,
            "created_at": time.time(),
            "auto_title": auto_title,
        }
        self.store.put("personal.chat", chat["id"], chat)
        return chat

    def update(self, session_id, changes):
        chat = self._get(session_id)
        if chat is None:
            raise HTTPException(404, "Chat not found.")
        if chat.get("kind") == "main" and ({"title", "archived"} & set(changes)):
            raise HTTPException(409, "A Main Chat stays pinned.")
        chat.update(changes)
        if "title" in changes:
            chat["auto_title"] = False
        self.store.put("personal.chat", session_id, chat)
        return chat

    def mark_seen(self, account, session_id, revision):
        if not self.exists(session_id):
            raise HTTPException(404, "Chat not found.")
        self.store.put(
            "chat.seen", f"{account.id}:{session_id}", {"revision": revision}
        )

    def _seen(self, account):
        prefix = f"{account.id}:"
        return {
            key[len(prefix) :]: doc.get("revision")
            for key, doc in self.store.docs("chat.seen")
            if key.startswith(prefix)
        }

    def _last_message(self, chat_id, names):
        last = self.messages.last(chat_id)
        if last and last["role"] == "user":
            last["author_name"] = names.get(last["author"], last["author"])
        return last

    async def summaries(self, account, url, token, names=None):
        """Every chat, with live state for the active ones, its latest public
        message and this admin's unread."""
        # Isolate failures: an unavailable runtime must not hide the saved list.
        limit = asyncio.Semaphore(8)
        seen = self._seen(account)
        names = names or {}

        async def summary(chat):
            chat = {**chat, "last_message": self._last_message(chat["id"], names)}
            if chat.get("archived"):
                return chat
            async with limit:
                try:
                    connection = await self.for_account(account, url, token, chat["id"])
                    state = await connection.snapshot()
                    if connection.agent and not chat.get("agent"):
                        self.update(chat["id"], {"agent": connection.agent})
                    current = self._get(chat["id"])
                    if current is None:
                        return None
                    if current.get("auto_title"):
                        published = self.messages.page(chat["id"], limit=100)[
                            "messages"
                        ]
                        first = next(
                            (m for m in published if m["role"] == "user"), None
                        )
                        text = first["text"] if first else ""
                        if isinstance(text, str) and text.strip():
                            title = " ".join(text.split())[:60]
                            if title:
                                current = self.update(chat["id"], {"title": title})
                    working_for = None
                    if state["running"]:
                        if current.get("kind") == "automation":
                            working_for = current.get("title")
                        else:
                            author = self.messages.last_author(chat["id"])
                            working_for = names.get(author, author)
                    return {
                        **current,
                        "last_message": chat["last_message"],
                        "running": state["running"],
                        "waiting": bool(state["attention"]),
                        "revision": state["revision"],
                        "unread": bool(state["revision"])
                        and seen.get(chat["id"]) != state["revision"],
                        "working_for": working_for,
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
        if (self._get(sid) or {}).get("kind") == "main":
            raise HTTPException(409, "A Main Chat stays pinned.")
        if sid in self.deleting:
            raise HTTPException(409, "Chat is being deleted.")
        self.deleting.add(sid)
        try:
            state = await connection.state(fresh=True)
            if state.get("run_active") or state.get("draining") or state.get("depth"):
                raise HTTPException(
                    409, "Stop this chat's work and clear its queue before deleting it."
                )
            await connection.call("delete_session", {"session_id": sid})
            self.store.put("chat.deleted", sid, {"deleted": True})
            self.store.delete("personal.chat", sid)
            self.messages.delete(sid)
            self.connections.pop(sid, None)
            await connection.close()
        finally:
            self.deleting.discard(sid)
        return {"ok": True}

    async def for_account(self, account, url, token, session_id=ORIGINAL_MAIN):

        # Shared factory credentials are administrative authority. Do not expose
        # them to ordinary members merely because they can sign into Workplace.
        if not account.admin or account.disabled:
            raise HTTPException(
                403, "Chat currently requires an administrator account."
            )
        if not url:
            raise HTTPException(
                503, "Connect a factory with the selected agent to use Chat."
            )
        if not self.exists(session_id):
            raise HTTPException(404, "Chat not found.")
        agent = self._get(session_id).get("agent")
        key = (url, token, session_id, agent)
        previous = self.connections.get(session_id)
        if previous and previous[0] != key:
            await previous[1].close()
        if not previous or previous[0] != key:
            self.connections[session_id] = (
                key,
                PersonalConnection(url, token, session_id, agent=agent),
            )
        self.connections[session_id][1].messages = self.messages
        return self.connections[session_id][1]

    async def close(self):
        await asyncio.gather(*(c.close() for _, c in self.connections.values()))
        self.connections.clear()
